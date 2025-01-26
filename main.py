import os
import time
import logging
import threading
from typing import Optional, Dict, List
from dataclasses import dataclass
import requests
import telebot
from dotenv import load_dotenv
from telebot import types
from telebot.apihelper import ApiException
from requests.exceptions import RequestException

load_dotenv()


# -------------------- پیکربندی --------------------
@dataclass
class Config:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN")
    admin_chat_id: str = os.getenv("ADMIN_TELEGRAM_CHAT_ID")
    history_file: str = os.getenv("HISTORY_FILE", "data/history.json")
    update_interval: int = int(os.getenv("UPDATE_INTERVAL", 3600))
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    max_retries: int = 5
    retry_delay: int = 5  # تاخیر مجدد در صورت خطا
    system_prompt_path: str = os.getenv("SYSTEM_PROMPT_PATH", "prompt/system.txt")
    payment_provider_token: str = ""  # توکن ارائه‌دهنده پرداخت
    prices: List[Dict] = None  # قیمت‌ها برای محصولات

    def __post_init__(self):
        if self.prices is None:
            self.prices = [
                {"label": "اشتراک ماهیانه", "amount": 50000},  # به تومان
                {"label": "اشتراک سالیانه", "amount": 500000},  # به تومان
            ]

    def validate(self):
        if not all(
            [
                self.telegram_bot_token,
                self.admin_chat_id,
                os.path.exists(self.system_prompt_path),
            ]
        ):
            raise ValueError("پیکربندی یا فایل‌های مورد نیاز ناقص هستند")


# -------------------- سرویس‌ها --------------------
class OpenMeteoClient:
    BASE_URL_AIR_QUALITY = "https://air-quality-api.open-meteo.com/v1/air-quality"
    BASE_URL_WEATHER = "https://api.open-meteo.com/v1/forecast"

    @staticmethod
    def get_air_quality(
        latitude: float = 30.43, longitude: float = 48.19, record_count: int = 1
    ) -> Optional[str]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "pm10,pm2_5,carbon_monoxide,sulphur_dioxide,ozone,nitrogen_dioxide,dust,european_aqi,us_aqi",
            "timezone": "auto",
        }

        try:
            response = requests.get(
                OpenMeteoClient.BASE_URL_AIR_QUALITY, params=params, timeout=15
            )
            response.raise_for_status()
            data = response.json()

            # دریافت AQI فعلی
            aqi = data["hourly"]["european_aqi"][-1]

            # تعیین سطح کیفیت هوا و انتخاب ایموجی مرتبط
            if aqi <= 50:
                quality = "خوب 😃"
            elif aqi <= 100:
                quality = "متوسط 🙂"
            elif aqi <= 150:
                quality = "ناسالم برای گروه‌های حساس 😕"
            elif aqi <= 200:
                quality = "ناسالم 😷"
            elif aqi <= 300:
                quality = "بسیار ناسالم 🤒"
            else:
                quality = "خطرناک ☠️"

            # ساخت پیام متنی زیبا با ایموجی‌ها
            message = f"🌍 **وضعیت کیفیت هوا در موقعیت ({latitude}, {longitude}):**\n\n"
            message += f"🔢 **شاخص کیفیت هوا (AQI): {aqi}**\n"
            message += f"💡 **وضعیت: {quality}**\n\n"
            message += "📊 **جزئیات آلاینده‌ها:**\n"
            message += f"• 🌬️ **PM2.5:** {data['hourly']['pm2_5'][-1]} µg/m³\n"
            message += f"• 🌬️ **PM10:** {data['hourly']['pm10'][-1]} µg/m³\n"
            message += (
                f"• 🔥 **مونوکسید کربن:** {data['hourly']['carbon_monoxide'][-1]} ppm\n"
            )
            message += f"• 🧪 **دی‌اکسید نیتروژن:** {data['hourly']['nitrogen_dioxide'][-1]} ppb\n"
            message += (
                f"• 🧪 **دی‌اکسید گوگرد:** {data['hourly']['sulphur_dioxide'][-1]} ppb\n"
            )
            message += f"• ☁️ **اُزن:** {data['hourly']['ozone'][-1]} ppb\n"
            message += f"• 🌫️ **گرد و غبار:** {data['hourly']['dust'][-1]} µg/m³\n"

            return message
        except RequestException as e:
            logging.error(f"خطای API OpenMeteo: {e}")
            return None

    @staticmethod
    def get_weather_data(
        latitude: float = 30.43, longitude: float = 48.19
    ) -> Optional[str]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m,windspeed_10m,precipitation,relativehumidity_2m,pressure_msl",
            "current_weather": "true",
            "temperature_unit": "celsius",
            "windspeed_unit": "kmh",
            "precipitation_unit": "mm",
            "timezone": "auto",
            "forecast_days": 1,  # اضافه کردن پیش‌بینی یک روزه برای کامل‌تر شدن درخواست
        }

        try:
            response = requests.get(
                OpenMeteoClient.BASE_URL_WEATHER, params=params, timeout=15
            )
            response.raise_for_status()
            data = response.json()

            # دریافت دمای فعلی
            temperature = data["hourly"]["temperature_2m"][-1]
            current_weather = data.get("current_weather", {})
            windspeed = current_weather.get("windspeed", "N/A")
            weathercode = current_weather.get("weathercode", "N/A")
            precipitation = data["hourly"]["precipitation"][-1]
            humidity = data["hourly"]["relativehumidity_2m"][-1]
            pressure = data["hourly"]["pressure_msl"][-1]

            weather_conditions = {
                0: "صاف",
                1: "غالباً صاف",
                2: "نیمه ابری",
                3: "ابری",
                45: "مه",
                48: "مه یخ‌بندان",
                51: "نم نم باران خفیف",
                53: "نم نم باران متوسط",
                55: "نم نم باران شدید",
                56: "باران ریزه یخ خفیف",
                57: "باران ریزه یخ شدید",
                61: "باران خفیف",
                63: "باران متوسط",
                65: "باران شدید",
                66: "باران یخ‌بندان خفیف",
                67: "باران یخ‌بندان شدید",
                71: "برف خفیف",
                73: "برف متوسط",
                75: "برف شدید",
                77: "دانه‌های برف",
                80: "رگبار باران خفیف",
                81: "رگبار باران متوسط",
                82: "رگبار باران شدید",
                85: "رگبار برف خفیف",
                86: "رگبار برف شدید",
                95: "رعد و برق خفیف تا متوسط",
                96: "رعد و برق با تگرگ خفیف",
                99: "رعد و برق با تگرگ شدید",
            }
            weather_description = weather_conditions.get(weathercode, "نامشخص")

            # ساخت پیام متنی زیبا با دما و اطلاعات هواشناسی
            message = f"🌡️ **وضعیت هوا در موقعیت ({latitude}, {longitude}):**\n\n"
            message += f"🌡️ **دمای فعلی: {temperature}°C**\n"
            message += f"💨 **سرعت باد: {windspeed} km/h**\n"
            message += f"💧 **بارندگی: {precipitation} mm**\n"
            message += f" رطوبت: **{humidity}%**\n"
            message += f" فشار: **{pressure} hPa**\n"
            message += f"  وضعیت: **{weather_description}**\n"

            return message
        except RequestException as e:
            logging.error(f"خطای API OpenMeteo: {e}")
            return None


# -------------------- ربات اصلی --------------------
class WeatherBot:
    def __init__(self, config: Config):
        self.config = config
        self.bot = telebot.TeleBot(config.telegram_bot_token)
        self.meteo = OpenMeteoClient()
        self._setup_logging()
        self._register_handlers()

    def _setup_logging(self):
        logging.basicConfig(
            level=self.config.log_level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.FileHandler("bot.log", encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )

    def _register_handlers(self):
        self.bot.message_handler(commands=["start", "help"])(self._send_welcome)
        self.bot.message_handler(commands=["aqi"])(self._send_current_aqi)
        self.bot.message_handler(commands=["setinterval"])(self._handle_set_interval)
        self.bot.message_handler(commands=["forceupdate"])(self._handle_force_update)
        self.bot.message_handler(commands=["subscribe"])(self._handle_subscribe)
        self.bot.message_handler(commands=["weather"])(self._send_current_weather)
        self.bot.callback_query_handler(func=lambda call: True)(
            self._handle_callback_query
        )

    def _handle_callback_query(self, call: types.CallbackQuery):
        if call.data == "aqi":
            self._send_current_aqi(call.message)
        elif call.data == "weather":
            self._send_current_weather(call.message)
        elif call.data == "setinterval":
            self._send_message(
                call.message.chat.id, "لطفاً بازه زمانی را به دقیقه وارد کنید:"
            )
        elif call.data == "subscribe":
            self._send_message(
                call.message.chat.id, "برای اشتراک ویژه، لطفاً به وبسایت ما مراجعه کنید."
            )
        elif call.data == "help":
            self._send_welcome(call.message)
        elif call.data == "admin" and call.message.chat.id == self.config.admin_chat_id:
            self._send_message(call.message.chat.id, "به پنل مدیریت خوش آمدید.")
        else:
            self._send_message(call.message.chat.id, "دستور نامعتبر است.")

    def _retry_api_call(self, func, *args, **kwargs):
        for attempt in range(self.config.max_retries):
            try:
                return func(*args, **kwargs)
            except (RequestException, ApiException) as e:
                logging.warning(f"تلاش {attempt+1} ناموفق بود: {e}")
                time.sleep(self.config.retry_delay)
        return None

    def _send_message(self, chat_id: int, text: str, reply_markup=None):
        for chunk in (text[i : i + 4096] for i in range(0, len(text), 4096)):
            self._retry_api_call(
                self.bot.send_message,
                chat_id,
                chunk,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )

    def _send_welcome(self, message: types.Message):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("AQI فعلی 🏭", callback_data="aqi"))
        markup.add(types.InlineKeyboardButton("وضعیت هوا 🌤", callback_data="weather"))
        markup.add(
            types.InlineKeyboardButton("تنظیم بازه ⏰", callback_data="setinterval")
        )
        markup.add(types.InlineKeyboardButton("اشتراک 🚀", callback_data="subscribe"))
        markup.add(types.InlineKeyboardButton("راهنما ℹ️", callback_data="help"))
        if message.chat.id == self.config.admin_chat_id:
            markup.add(types.InlineKeyboardButton("Admin Panel", callback_data="admin"))

        self._send_message(
            message.chat.id,
            "به سوپربات کیفیت هوای خرمشهر خوش آمدید! 🌟🤖\n\n"
            "با استفاده از این بات می‌توانید به‌روزترین اطلاعات کیفیت هوا را دریافت کنید، بازه زمانی به‌روزرسانی داده‌ها را تنظیم کنید و از اشتراک‌های ویژه بهره‌مند شوید.\n\n"
            "پارامترهای کیفیت هوا:\n"
            "- PM10: ذرات معلق با قطر کمتر از ۱۰ میکرومتر\n"
            "- PM2.5: ذرات معلق با قطر کمتر از ۲.۵ میکرومتر\n"
            "- مونوکسید کربن: گاز سمی بی‌رنگ و بی‌بو\n"
            "- دی‌اکسید نیتروژن: گاز سمی و آلاینده هوا\n"
            "- دی‌اکسید گوگرد: گاز سمی و آلاینده هوا\n"
            "- اُزن: گاز آلاینده و مضر در سطح زمین\n"
            "- گرد و غبار: ذرات معلق در هوا\n\n"
            "لطفاً از دکمه‌های زیر برای دسترسی به امکانات مختلف استفاده کنید.",
            reply_markup=markup,
        )

    def _send_current_aqi(self, message: types.Message):
        try:
            record_count = (
                int(message.text.split()[1]) if len(message.text.split()) > 1 else 1
            )
        except ValueError:
            record_count = 1

        data = self.meteo.get_air_quality(record_count=record_count)
        if not data:
            logging.error("عدم توانایی در دریافت داده‌های کیفیت هوا.")
            self._send_message(message.chat.id, "خطا در دریافت داده‌ها ⚠️")
            return

        self._send_message(message.chat.id, data)
        logging.info("داده‌های کیفیت هوا با موفقیت ارسال شد.")

    def _send_current_weather(self, message: types.Message):
        data = self.meteo.get_weather_data()
        if not data:
            logging.error("عدم توانایی در دریافت داده‌های وضعیت هوا.")
            self._send_message(message.chat.id, "خطا در دریافت داده‌ها ⚠️")
            return

        self._send_message(message.chat.id, data)
        logging.info("داده‌های وضعیت هوا با موفقیت ارسال شد.")

    def _handle_set_interval(self, message: types.Message):
        try:
            minutes = int(message.text.split()[1])
            if minutes <= 0:
                raise ValueError
            self.config.update_interval = minutes * 60
            self._send_message(
                message.chat.id, f"بازه زمانی به {minutes} دقیقه تنظیم شد ⏰"
            )
            logging.info(f"بازه زمانی به {minutes} دقیقه تنظیم شد.")
            self._send_message(
                self.config.admin_chat_id, f"بازه زمانی به {minutes} دقیقه تنظیم شد."
            )
        except (IndexError, ValueError):
            logging.error("فرمت دستور تنظیم بازه زمانی اشتباه است.")
            self._send_message(
                message.chat.id, "فرمت دستور نادرست. مثال: /setinterval 60"
            )
            self._send_message(
                self.config.admin_chat_id, "فرمت دستور نادرست برای تنظیم بازه."
            )

    def _handle_force_update(self, message: types.Message):
        if str(message.chat.id) != self.config.admin_chat_id:
            logging.warning("تلاش غیرمجاز برای به‌روزرسانی فورس.")
            self._send_message(message.chat.id, "دسترسی غیرمجاز ⚠️")
            return

        self._send_message(message.chat.id, "شروع به‌روزرسانی فوری...")
        logging.info("به‌روزرسانی فورس توسط مدیر آغاز شد.")
        self._perform_update()
        self._send_message(message.chat.id, "به‌روزرسانی با موفقیت انجام شد ✅")
        self._send_message(
            self.config.admin_chat_id, "به‌روزرسانی فوری با موفقیت انجام شد."
        )

    def _perform_update(self):
        data = self.meteo.get_air_quality()
        if data:
            self._send_message(self.config.admin_chat_id, data)
            logging.info("به‌روزرسانی انجام شد و داده‌ها به مدیر ارسال شدند.")
        else:
            logging.error("خطا در دریافت داده‌ها در هنگام به‌روزرسانی.")
            self._send_message(self.config.admin_chat_id, "خطا در دریافت داده‌ها ⚠️")

    def _handle_subscribe(self, message: types.Message):
        """مدیریت فرآیند اشتراک کاربر"""
        if self._is_user_subscribed(message.chat.id):
            self._send_message(message.chat.id, "شما قبلا اشتراک فعال دارید. 🌟")
            return

        prices = [
            telebot.types.LabeledPrice(label=price["label"], amount=price["amount"])
            for price in self.config.prices
        ]

        invoice = telebot.types.Invoice(
            title="اشتراک سوپربات کیفیت هوا",
            description="دریافت اطلاعات پیشرفته و ویژه از کیفیت هوا در خرمشهر.",
            payload="subscription_payload",
            provider_token=self.config.payment_provider_token,
            currency="irt",
            prices=prices,
        )

        self.bot.send_invoice(
            message.chat.id,
            invoice.title,
            invoice.description,
            invoice.payload,
            invoice.provider_token,
            currency=invoice.currency,
            prices=invoice.prices,
            start_parameter="subscription",
        )

    def _is_user_subscribed(self, user_id: int) -> bool:
        """بررسی وضعیت اشتراک کاربر"""
        return False

    def _handle_payment_successful(self, message: types.Message):
        """مدیریت پس از پرداخت موفق"""
        self._send_message(
            message.chat.id, "پرداخت شما موفقیت‌آمیز بود! 🎉 اشتراک شما فعال شد."
        )

    def _handle_payment_error(self, message: types.Message):
        """مدیریت در صورت بروز خطا در پرداخت"""
        self._send_message(
            message.chat.id, "پرداخت شما ناموفق بود. لطفا مجدداً تلاش کنید. ⚠️"
        )

    def start_periodic_updates(self):
        def update_loop():
            while True:
                self._perform_update()
                time.sleep(self.config.update_interval)

        threading.Thread(target=update_loop, daemon=True).start()

    def run(self):
        self.start_periodic_updates()
        logging.info("شروع به پایش ربات...")
        try:

            @self.bot.pre_checkout_query_handler(func=lambda query: True)
            def checkout(pre_checkout_query):
                self.bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

            @self.bot.message_handler(content_types=["successful_payment"])
            def handle_successful_payment(message: types.Message):
                self._handle_payment_successful(message)

            self.bot.infinity_polling()
        except ApiException as e:
            logging.error(f"خطای Telegram API: {e}")
            if "Conflict" in str(e):
                logging.info("تلاش برای رفع تعارض با راه‌اندازی مجدد پایش...")
                time.sleep(5)
                self.run()


# -------------------- اصلی --------------------
if __name__ == "__main__":
    try:
        config = Config()
        config.validate()
        bot = WeatherBot(config)
        bot.run()
    except Exception as e:
        logging.critical(f"راه‌اندازی ربات ناموفق بود: {e}")
        exit(1)
