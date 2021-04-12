from config import bot_token
from config import proxy
from yahoo_fin import stock_info as si
from datetime import datetime as dt
import telebot
from telebot import types
from telebot import apihelper

# хранит японские свечи, строящиеся в данный момент ботом
curr_candles = {}
# "архив" из уже построенных свеч
last_candles = {}
# свечи, которые пользователь добавил, но свечи в реальном времени для них еще не строятся,
# потому что с момента их добавления не было нового открытия построения свеч
# (не было момента времени, количество минут в котором кратно 5)
ticker_buffer = []
# отслеживаемые пользователем тикеры (те, для которых в данный момент в реальном времени строятся свечи)
tickers = []
# отслеживаемые пользователем сигналы
signals = []
# user_id обновится при первом полученном сообщении
user_id = None


# класс японской свечи нужен, потому что сигналы анализируют именно свечи
class Candle:
    def __init__(self, open, close, high, low):
        self.open = open
        self.close = close
        self.high = high
        self.low = low

    def get_body(self):
        return abs(self.open - self.close)

    def is_bull(self):
        return self.open < self.close

    def is_bear(self):
        return self.open > self.close

    def get_window(self):
        return self.high - self.low

    def get_upper_shadow(self):
        return self.high - max(self.close, self.open)


# класс сигнала "Молот", внутри описана лишь логика проверки, созают ли сигнализируют ли последние свечи о смене тренда
class Hammer:
    def __init__(self):
        self.name = 'Hammer'

    def check(self, candles):
        length = len(candles)
        if length < 3 or candles[length - 1].is_bear == candles[length - 3].is_bear or \
                candles[length - 2].get_upper_shadow() / candles[length - 2] > 0.5:
            return [False, 3]
        rating = 0
        conf_value = candles[length - 2].get_body / candles[length - 2].get_window
        if conf_value < 0.3:
            rating = 1
        elif conf_value < 0.2:
            rating = 2
        elif conf_value < 0.1:
            rating = 3
        else:
            return [False, 3]
        trend_number = 0
        for i in range(length - 3):
            if candles[length - 3 - i].is_bear == candles[length - 4 - i].is_bear:
                trend_number += 1
            else:
                break
        rating += trend_number / 7
        if candles[length - 1].get_body / candles[length - 3].get_body >= 1:
            rating += 1
        else:
            rating += candles[length - 1].get_body / candles[length - 3].get_body
        return [True, rating]


# к сожалению, мне не удалось найти рабочий прокси для работы бота,
# без него соединение с серверами telegram не устанавливается
# (для теста бота используйте VPN)
# apihelper.proxy = {'https': proxy}
bot = telebot.TeleBot(bot_token)
available_signals = ["hammer"]
keyboard = types.ReplyKeyboardMarkup()
key_signals = types.KeyboardButton(text='Tracking signals')
key_tickers = types.KeyboardButton(text='Tracking tickers')
keyboard.add(key_signals, key_tickers)


# обработчик сообщений пользователя
@bot.message_handler(content_types=['text'])
def reply(message):
    global user_id
    user_id = message.from_user.id
    if message.text == 'Tracking tickers':
        bot.send_message(user_id, 'Enter the ticker you want to start/stop tracking')
        if not tickers and not ticker_buffer:
            bot.send_message(user_id, "You are not tracking any tickers")
        else:
            bot.send_message(user_id, "Already tracking tickers:\n" +
                             '\n'.join(tickers).upper() + '\n'.join(ticker_buffer).upper())
        bot.register_next_step_handler(message, get_ticker)
    elif message.text == 'Tracking signals':
        bot.send_message(user_id, 'Enter the signal you want to start/stop tracking')
        if signals:
            bot.send_message(user_id, "Already tracking signals:\n" + '\n'.join(signals))
        bot.send_message(user_id, "All available signals:\n" + '\n'.join(available_signals))
        bot.register_next_step_handler(message, get_signal)
    else:
        bot.send_message(user_id, "I don't understand, choose command, please.", reply_markup=keyboard)
    # после обработки возвращаемся к мониторингу биржи
    monitoring(tickers)


# обработчик начала общения с пользователем (команды /start)
@bot.message_handler(commands=['start'])
def greeting(message):
    global user_id
    user_id = message.from_user.id
    bot.send_message(user_id, "Hi, I'll help you to track signals of trend changing.", reply_markup=keyboard)
    # после обработки возвращаемся к мониторингу биржи
    monitoring(tickers)


# обработчик изменения отслеживаемых тикеров,
# вызвается после того, как пользователь нажмет на кнопку [Tracking tickers]
def get_ticker(message):
    ticker = message.text.lower()
    global user_id
    user_id = message.from_user.id
    if ticker in tickers:
        tickers.remove(ticker)
        del curr_candles[ticker]
        if ticker in last_candles:
            del last_candles[ticker]
        bot.send_message(user_id, "You have deleted ticker '" + ticker.upper() + "'", reply_markup=keyboard)
    elif ticker in ticker_buffer:
        ticker_buffer.remove(ticker)
        bot.send_message(user_id, "You have deleted ticker '" + ticker.upper() + "'", reply_markup=keyboard)
    else:
        try:
            si.get_live_price(ticker)
            ticker_buffer.append(ticker)
            bot.send_message(user_id, "You have added ticker '" + ticker.upper() + "'", reply_markup=keyboard)
        except AssertionError:
            bot.send_message(user_id, "No such ticker", reply_markup=keyboard)
    # после обработки возвращаемся к мониторингу биржи
    monitoring(tickers)


# обработчик изменения отслеживаемых сигналов,
# вызвается после того, как пользователь нажмет на кнопку [Tracking signals]
def get_signal(message):
    signal = message.text.lower()
    global user_id
    user_id = message.from_user.id
    if signal in signals:
        signals.remove(signal)
        bot.send_message(user_id, "You have deleted signal '" + signal + "'", reply_markup=keyboard)
    elif signal not in available_signals:
        bot.send_message(user_id, "No such signal", reply_markup=keyboard)
    else:
        signals.append(signal)
        bot.send_message(user_id, "You have added signal '" + signal + "'", reply_markup=keyboard)
    # после обработки возвращаемся к мониторингу биржи
    monitoring(tickers)


# очистка отслеживаемых в данный момент свеч
def reinit_curr_candles(curr_candles):
    curr_candles.clear()
    for tick in tickers:
        price = si.get_live_price(tick)
        curr_candles.update({tick: Candle(price, price, price, price)})


# добавление свеч, которые только что закрылись в "архив"
def add_new_candles():
    for tick in curr_candles:
        if tick in last_candles:
            # бот хранит только последние 10 свеч для каждого тикера
            if len(last_candles[tick]) == 10:
                list.pop(0)
            last_candles[tick].append(curr_candles[tick])
        else:
            last_candles.update({tick: [curr_candles[tick]]})


# обновление свеч, которые строятся в данный момент
# для каждого тикера запрашиватеся его цена, в зависимости от полученного значения меняется соотвествующая свеча
def update_curr_candles():
    for tick in tickers:
        price = si.get_live_price(tick)
        if price > curr_candles[tick].high:
            curr_candles[tick].high = price
        elif price < curr_candles[tick].low:
            curr_candles[tick].low = price
        curr_candles[tick].close = price


# декоратор, позволяющий хранить статические переменные
def static_vars(**kwargs):
    def wrapper(function):
        for k in kwargs:
            setattr(function, k, kwargs[k])
        return function

    return wrapper


# статическая переменная last_close_time нужна, чтобы обработка закрытия свечи (происходит, когда количество минут
# на часах кратно 5) не повторялась несколько раз в одну минуту
@static_vars(last_close_time=None)
def monitoring(tickers):
    while True:
        lifetime = dt.now().minute
        # если количество минут на часах кратно 5, нужно закрыть отслеживаемые свечи,
        # добавить их в архив, проверить, не сработал ли какой-нибудь сигнал на каком-нибудь тикере.
        # Если сработал, присылаем сообщение пользователю.
        # (если время не кратно 5, просто обновляем строящиеся свечи)
        if (lifetime % 5) == 0 and lifetime != monitoring.last_message_time:
            monitoring.last_message_time = lifetime
            if tickers:
                tickers += ticker_buffer
            else:
                tickers = ticker_buffer
            ticker_buffer.clear()
            add_new_candles()
            reinit_curr_candles(curr_candles)
            for signal in signals:
                for ticker in last_candles:
                    sgnl, rating = signal.check(last_candles[ticker])
                    if sgnl and user_id is not None:
                        bot.send_message(user_id, "{0} signal!\n"
                                                  "ticker: {1}\n"
                                                  "rating: {2}".format(signal.name, ticker, rating))
        else:
            update_curr_candles()


bot.polling(none_stop=True)
