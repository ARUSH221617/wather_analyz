import os
import time
import json
import logging
import threading
from typing import Optional, Dict, List
from dataclasses import dataclass
import requests
import telebot
import google.generativeai as genai
from dotenv import load_dotenv
from telebot import types
from telebot.apihelper import ApiException
from requests.exceptions import RequestException

load_dotenv()


# -------------------- پیکربندی --------------------
@dataclass
class Config:
    genai_api_key: str = os.getenv("GOOGLE_GEMINI_API_KEY")
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
                {
                    "label": "اشتراک ماهیانه",
                    "amount": 50000,  # به تومان
                },
                {
                    "label": "اشتراک سالیانه",
                    "amount": 500000,  # به تومان
                },
            ]

    def validate(self):
        if not all(
            [
                self.genai_api_key,
                self.telegram_bot_token,
                self.admin_chat_id,
                os.path.exists(self.system_prompt_path),
            ]
        ):
            raise ValueError("پیکربندی یا فایل‌های مورد نیاز ناقص هستند")


# -------------------- سرویس‌ها --------------------
class OpenMeteoClient:
    BASE_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

    @staticmethod
    def get_air_quality(
        latitude: float = 30.43, longitude: float = 48.19
    ) -> Optional[Dict]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "pm10,pm2_5,carbon_monoxide,sulphur_dioxide,ozone,nitrogen_dioxide,dust",
            "timezone": "auto",
        }

        try:
            response = requests.get(OpenMeteoClient.BASE_URL, params=params, timeout=15)
            response.raise_for_status()
            return response.json()
        except RequestException as e:
            logging.error(f"خطای API OpenMeteo: {e}")
            return None


class GeminiAIAnalyzer:
    def __init__(
        self,
        api_key: str,
        system_prompt_path: str = "prompt/system.txt",
    ):
        genai.configure(api_key=api_key)
        self.system_prompt_path = system_prompt_path
        self.model = genai.GenerativeModel(
            model_name="gemini-2.0-flash-exp",
            generation_config={
                "temperature": 1,
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 8192,
            },
            system_instruction=self._load_system_prompt(),
        )
        self.history = []

    def _load_system_prompt(self) -> str:
        """بارگذاری دستورالعمل سیستم از فایل خارجی با مدیریت خطا."""
        try:
            with open(self.system_prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            logging.critical(f"فایل دستورالعمل سیستم پیدا نشد: {self.system_prompt_path}")
            raise
        except IOError as e:
            logging.critical(f"خطا در خواندن دستورالعمل سیستم: {e}")
            raise

    def analyze(self, data: Dict) -> Optional[Dict]:
        try:
            chat = self.model.start_chat(history=self.history)
            response = chat.send_message(json.dumps(data))
            self.history = chat.history
            self._send_message(config.admin_chat_id, response.text)
            response_text = response.text.strip("```json").strip("```").strip()
            return json.loads(response_text)
        except (json.JSONDecodeError, ValueError) as e:
            logging.error(f"پاسخ JSON نامعتبر: {e}")
            return None
        except Exception as e:
            logging.error(f"تحلیل Gemini ناموفق بود: {e}")
            return None

    def _content_to_dict(self, content) -> Dict:
        """تبدیل اشیاء Content به دیکشنری‌های قابل سریال‌سازی"""
        return {
            "role": content.role,
            "parts": [{"text": part.text} for part in content.parts],
        }

    def _dict_to_content(self, data: Dict):
        """تبدیل دیکشنری‌ها به اشیاء Content"""
        return genai.content.Content(
            role=data["role"],
            parts=[genai.content.Part(text=part["text"]) for part in data["parts"]],
        )


# -------------------- ربات اصلی --------------------
class AirQualityBot:
    def __init__(self, config: Config):
        self.config = config
        self.bot = telebot.TeleBot(config.telegram_bot_token)
        self.meteo = OpenMeteoClient()
        self.analyzer = GeminiAIAnalyzer(
            config.genai_api_key, config.system_prompt_path
        )
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
        self.bot.message_handler(commands=["subscribe"])(self._handle_subscribe)  # افزودن دستور اشتراک

    def _retry_api_call(self, func, *args, **kwargs):
        for attempt in range(self.config.max_retries):
            try:
                return func(*args, **kwargs)
            except (RequestException, ApiException) as e:
                logging.warning(f"تلاش {attempt+1} ناموفق بود: {e}")
                time.sleep(self.config.retry_delay)
        return None

    def _send_message(self, chat_id: int, text: str, reply_markup=None):
        if len(text) > 4096:
            for chunk in [text[i : i + 4096] for i in range(0, len(text), 4096)]:
                self._retry_api_call(self.bot.send_message, chat_id, chunk, reply_markup=reply_markup)
        else:
            self._retry_api_call(self.bot.send_message, chat_id, text, reply_markup=reply_markup)

    def _send_welcome(self, message: types.Message):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        markup.row("AQI فعلی 🏭", "تنظیم بازه ⏰", "اشتراک 🚀", "راهنما ℹ️")

        self._send_message(
            message.chat.id,
            "به سوپربات کیفیت هوای خرمشهر خوش آمدید! 🌟🤖\n\n"
            "دستورات موجود:\n"
            "/aqi - دریافت وضعیت فعلی هوا\n"
            "/setinterval [دقیقه] - تنظیم بازه به روزرسانی\n"
            "/subscribe - خرید اشتراک ویژه\n"
            "/help - نمایش راهنما",
            reply_markup=markup,
        )

    def _send_current_aqi(self, message: types.Message):
        data = self.meteo.get_air_quality()
        if not data:
            logging.error("عدم توانایی در دریافت داده‌های کیفیت هوا.")
            self._send_message(message.chat.id, "خطا در دریافت داده‌ها ⚠️")
            return

        analysis = self.analyzer.analyze(data)
        if analysis:
            self._send_message(message.chat.id, json.dumps(analysis))
            # response = self._format_response(analysis)
            # self._send_message(message.chat.id, response)
            logging.info("داده‌های کیفیت هوا با موفقیت ارسال شد.")
        else:
            logging.error("عدم توانایی در تحلیل داده‌های کیفیت هوا.")
            self._send_message(message.chat.id, "خطا در تحلیل داده‌ها ⚠️")

    def _format_response(self, analysis: Dict) -> str:
        return (
            f"🌍 وضعیت کیفیت هوا در خرمشهر:\n\n"
            f"📊 شاخص AQI: {analysis['aqi']} {analysis['emoji']}\n\n"
            f"🔧 توصیه‌ها:\n- " + "\n- ".join(analysis["recommendations"]) + "\n\n"
            f"📖 معنی ایموجی‌ها:\n"
            + "\n".join([f"{k}: {v}" for k, v in analysis["emoji_explanation"].items()])
        )

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
            self._send_message(self.config.admin_chat_id, f"بازه زمانی به {minutes} دقیقه تنظیم شد.")
        except (IndexError, ValueError):
            logging.error("فرمت دستور تنظیم بازه زمانی اشتباه است.")
            self._send_message(
                message.chat.id, "فرمت دستور نادرست. مثال: /setinterval 60"
            )
            self._send_message(self.config.admin_chat_id, "فرمت دستور نادرست برای تنظیم بازه.")

    def _handle_force_update(self, message: types.Message):
        if str(message.chat.id) != self.config.admin_chat_id:
            logging.warning("تلاش غیرمجاز برای به‌روزرسانی فورس.")
            self._send_message(message.chat.id, "دسترسی غیرمجاز ⚠️")
            return

        self._send_message(message.chat.id, "شروع به‌روزرسانی فوری...")
        logging.info("به‌روزرسانی فورس توسط مدیر آغاز شد.")
        self._perform_update()
        self._send_message(message.chat.id, "به‌روزرسانی با موفقیت انجام شد ✅")
        self._send_message(self.config.admin_chat_id, "به‌روزرسانی فوری با موفقیت انجام شد.")

    def _perform_update(self):
        if data := self.meteo.get_air_quality():
            if analysis := self.analyzer.analyze(data):
                self._send_message(
                    self.config.admin_chat_id, self._format_response(analysis)
                )
                logging.info("به‌روزرسانی انجام شد و داده‌ها به مدیر ارسال شدند.")
            else:
                logging.error("خطا در تحلیل داده‌ها در هنگام به‌روزرسانی.")
                self._send_message(self.config.admin_chat_id, "خطا در تحلیل داده‌ها ⚠️")
        else:
            logging.error("خطا در دریافت داده‌ها در هنگام به‌روزرسانی.")
            self._send_message(self.config.admin_chat_id, "خطا در دریافت داده‌ها ⚠️")

    def _handle_subscribe(self, message: types.Message):
        """مدیریت فرآیند اشتراک کاربر"""
        if self._is_user_subscribed(message.chat.id):
            self._send_message(message.chat.id, "شما قبلا اشتراک فعال دارید. 🌟")
            return

        prices = []
        for price in self.config.prices:
            prices.append(telebot.types.LabeledPrice(label=price["label"], amount=price["amount"]))

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
        # اینجا باید بررسی شود که آیا کاربر اشتراک دارد یا خیر
        # برای نمونه فرض می‌کنیم که همواره اشتراک ندارد
        return False

    def _handle_payment_successful(self, message: types.Message):
        """مدیریت پس از پرداخت موفق"""
        self._send_message(message.chat.id, "پرداخت شما موفقیت‌آمیز بود! 🎉 اشتراک شما فعال شد.")
        # اینجا باید وضعیت اشتراک کاربر به‌روزرسانی شود

    def _handle_payment_error(self, message: types.Message):
        """مدیریت در صورت بروز خطا در پرداخت"""
        self._send_message(message.chat.id, "پرداخت شما ناموفق بود. لطفا مجدداً تلاش کنید. ⚠️")

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

            @self.bot.message_handler(content_types=['successful_payment'])
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
        bot = AirQualityBot(config)
        bot.run()
    except Exception as e:
        logging.critical(f"راه‌اندازی ربات ناموفق بود: {e}")
        exit(1)
