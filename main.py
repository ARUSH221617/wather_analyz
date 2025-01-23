import time
import json
import requests
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types import content
import telebot
import logging
from requests.exceptions import ConnectionError, RequestException
from telebot.apihelper import ApiException
import os
import threading
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file


class Config:
    """Configuration class to manage environment variables and settings."""

    def __init__(self):
        """Initializes the configuration by loading environment variables."""
        self.genai_api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("ADMIN_TELEGRAM_CHAT_ID")
        self.history_file = os.getenv("HISTORY_FILE", "data/history.json")
        self.update_interval = int(os.getenv("UPDATE_INTERVAL", 3600))
        self.log_level = os.getenv("LOG_LEVEL", "ERROR").upper()
        self.retry_max_attempts = 5
        self.retry_delay_seconds = 10


class AirQualityBot:
    """Main bot class to handle air quality updates and Telegram interactions."""

    def __init__(self, config):
        """Initializes the bot with configuration and sets up necessary components."""
        self.config = config
        self.logger = self._setup_logger()
        self.bot = telebot.TeleBot(self.config.telegram_bot_token)
        self._check_essential_config()
        genai.configure(api_key=self.config.genai_api_key)

    def _setup_logger(self):
        """Sets up and configures the logger for the bot."""
        logging.basicConfig(
            level=self.config.log_level,
            format="%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
            handlers=[
                logging.FileHandler("bot.log", encoding="utf-8"),
                logging.StreamHandler(),
            ],
        )
        return logging.getLogger(__name__)

    def _check_essential_config(self):
        """Checks if essential configuration variables are set and exits if not."""
        if not self.config.genai_api_key:
            self.logger.critical("GENAI_API_KEY environment variable not set. Exiting.")
            exit(1)
        if not self.config.telegram_bot_token:
            self.logger.critical(
                "TELEGRAM_BOT_TOKEN environment variable not set. Exiting."
            )
            exit(1)
        if not self.config.chat_id:
            self.logger.critical(
                "ADMIN_TELEGRAM_CHAT_ID environment variable not set. Exiting."
            )
            exit(1)

    def load_history(self, filename=None):
        """Loads chat history from a JSON file.

        Args:
            filename (str, optional): The path to the history file. Defaults to None, which uses the configured history file.

        Returns:
            list: The loaded chat history as a list of messages, or an empty list if loading fails or the file doesn't exist.
        """
        filename = filename or self.config.history_file
        try:
            with open(filename, "r", encoding="utf-8") as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    self.logger.error(
                        f"Error decoding JSON from {filename}. Returning empty history."
                    )
                    return []
        except FileNotFoundError:
            self.logger.info(
                f"History file {filename} not found. Starting with empty history."
            )
            return []
        except Exception as e:
            self.logger.error(f"Error loading history from {filename}: {e}")
            return []

    def save_history(self, history, filename=None):
        """Saves chat history to a JSON file.

        Args:
            history (list): The chat history to save.
            filename (str, optional): The path to save the history file. Defaults to None, which uses the configured history file.
        """
        filename = filename or self.config.history_file
        try:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=4)
                self.logger.debug(f"Chat history saved to {filename}")
        except OSError as e:
            self.logger.error(
                f"OSError saving history to {filename}: {e}. Check file permissions and directory existence."
            )
        except Exception as e:
            self.logger.error(f"Error saving history to {filename}: {e}")

    def get_air_quality_data(self):
        """Fetches air quality data from the Open-Meteo API.

        Returns:
            dict: Air quality data in JSON format if successful, None otherwise.
        """
        url = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=30.43&longitude=48.19&hourly=pm10,pm2_5,carbon_monoxide,sulphur_dioxide,ozone,nitrogen_dioxide,dust&timezone=auto"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.logger.info(
                "Air quality data fetched successfully from Open-Meteo API."
            )
            self.logger.debug(f"Air quality data: {data}")
            return data
        except RequestException as e:
            self.logger.error(
                f"Error fetching air quality data from Open-Meteo API: {e}"
            )
            return None

    def analyze_air_quality(self, data):
        """Analyzes air quality data using the Gemini AI model.

        Args:
            data (dict): Air quality data fetched from Open-Meteo API.

        Returns:
            str: Analysis of air quality data in JSON format if successful, None otherwise.
        """
        try:
            generation_config = {
                "temperature": 1,
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 8192,
            }

            model = genai.GenerativeModel(
                model_name="gemini-2.0-flash-exp",
                generation_config=generation_config,
                system_instruction="""
                    شما یک متخصص تحلیل داده‌های کیفیت هوا هستید. وظیفه شما تحلیل داده‌های کیفیت هوا برای شهر خرمشهر در استان خوزستان است.

                    تحلیل خود را به صورت JSON  ارائه دهید. ساختار JSON  باید به شکل زیر باشد:

                    ```json
                    {
                      "city": "نام شهر",
                      "aqi": "مقدار AQI",
                      "emoji": "ایموجی وضعیت آلودگی",
                      "recommendations": ["توصیه 1", "توصیه 2", ...],
                      "emoji_explanation": {
                        "🟢": "توضیح هوای پاک",
                        "🟡": "توضیح هوای سالم",
                        "🟠": "توضیح هوای ناسالم برای گروه‌های حساس",
                        "🟣": "توضیح هوای ناسالم",
                        "🔴": "توضیح هوای بسیار ناسالم"
                      }
                    }
                    ```

                    **توضیحات:**

                    *   **city:** همیشه "خرمشهر" باشد.
                    *   **aqi:** مقدار شاخص کیفیت هوا (AQI) را گزارش کنید.
                    *   **emoji:** از ایموجی‌های زیر برای نمایش وضعیت آلودگی هوای خرمشهر استفاده کنید:
                        * 🟢: هوای پاک
                        * 🟡: هوای سالم
                        * 🟠: ناسالم برای گروه‌های حساس
                        * 🟣: ناسالم
                        * 🔴: بسیار ناسالم
                    *   **recommendations:** بر اساس مقدار AQI، توصیه‌های زیر را به صورت یک لیست ارائه کنید:
                        * AQI > 140: "احتمال تعطیلی مدارس"
                        * AQI > 180: "تعطیلی مدارس و دانشگاه‌ها"
                        * AQI > 200: "تعطیلی"
                        اگر AQI کمتر از 140 بود، لیست توصیه‌ها را خالی بگذارید.
                    *   **emoji_explanation:**  توضیح مختصری برای هر ایموجی ارائه دهید.
                """,
            )

            chat_session = model.start_chat(history=self.load_history())

            response = chat_session.send_message(content=json.dumps(data))
            analysis_text = response.text
            if analysis_text:
                self.logger.info("Air quality data analyzed successfully by Gemini AI.")
                self.logger.debug(f"Analysis result (text): {analysis_text}")
                self.save_history(chat_session.history)
                return analysis_text
            else:
                self.logger.warning("Gemini AI analysis returned empty response.")
                return None

        except Exception as e:
            self.logger.error(f"Error during data analysis with Gemini AI: {e}")
            return None

    def _send_with_retry(
        self, func, *args, max_retries=None, delay=None, func_name=None
    ):
        """Sends a message using the given function with retry logic.

        Args:
            func (callable): The function to call (e.g., bot.send_message).
            *args: Arguments to pass to the function.
            max_retries (int, optional): Maximum number of retries. Defaults to configured retry_max_attempts.
            delay (int, optional): Delay in seconds between retries. Defaults to configured retry_delay_seconds.
            func_name (str, optional): Name of the function for logging. Defaults to func.__name__.

        Returns:
            bool: True if the function call was successful (or eventually successful after retries), False otherwise.
        """
        max_retries = max_retries or self.config.retry_max_attempts
        delay = delay or self.config.retry_delay_seconds
        func_name = func_name or func.__name__

        for attempt in range(max_retries):
            try:
                return func(*args)
            except (ConnectionError, RequestException) as e:
                self.logger.error(
                    f"Network error during Telegram API call '{func_name}' (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    self.logger.info(
                        f"Retrying Telegram API call '{func_name}' in {delay} seconds..."
                    )
                    time.sleep(delay)
                else:
                    self.logger.error(
                        f"Max retries reached for Telegram API call '{func_name}' due to network issues."
                    )
                    return False
            except ApiException as e:
                self.logger.error(f"Telegram API error during '{func_name}': {e}")
                return False
            except Exception as e:
                self.logger.error(
                    f"Unexpected error during Telegram API call '{func_name}': {e}"
                )
                return False
        return False

    def send_update_to_telegram(self, message, parse_mode="Markdown"):
        """Sends a message to the Telegram chat, splitting it if necessary if message is too long.

        Args:
            message (str): The message to send to Telegram.
            parse_mode (str, optional): Parse mode for Telegram message. Defaults to "Markdown".
        """
        if not self._send_with_retry(self.bot.get_me, func_name="get_me"):
            self.logger.warning("Failed to ping Telegram. Update not sent.")
            return

        def _send_message_part(part):
            if self._send_with_retry(
                lambda *args: self.bot.send_message(*args, parse_mode=parse_mode),
                self.config.chat_id,
                part,
                func_name="send_message",
            ):
                self.logger.info("Part of the update sent to Telegram.")
                return True
            else:
                self.logger.error("Failed to send part of the update to Telegram.")
                return False

        if len(message) > 4096:
            parts = [message[i : i + 4096] for i in range(0, len(message), 4096)]
            all_parts_sent = True
            for part in parts:
                if not _send_message_part(part):
                    all_parts_sent = False
            if all_parts_sent:
                self.logger.info("Full update sent to Telegram in parts.")
            else:
                self.logger.error("Failed to send full update to Telegram in parts.")

        else:
            if _send_message_part(message):
                self.logger.info("Update sent to Telegram.")
            else:
                self.logger.error("Failed to send update to Telegram.")

    def periodic_update(self):
        """Periodically fetches air quality data, analyzes it, and sends updates based on configured interval."""
        while True:
            start_time = time.time()
            self.logger.info("Starting periodic air quality update...")
            self._perform_air_quality_update()

            end_time = time.time()
            elapsed_time = end_time - start_time
            wait_time = max(0, self.config.update_interval - elapsed_time)
            self.logger.info(
                f"Periodic update completed in {elapsed_time:.2f} seconds. Waiting for {wait_time:.2f} seconds until next update."
            )
            time.sleep(wait_time)

    def _perform_air_quality_update(self):
        """Fetches, analyzes, and sends air quality update. Separated for use in both periodic updates and admin command."""
        data = self.get_air_quality_data()
        if data:
            analysis = self.analyze_air_quality(data)
            if analysis:
                self.send_update_to_telegram(analysis)
            else:
                self.send_update_to_telegram("Failed to analyze air quality data.")
        else:
            self.send_update_to_telegram("Failed to retrieve air quality data.")

    def send_welcome(self, message):
        """Handles the /start and /help commands, sending a welcome message with command descriptions."""
        chat_id = message.chat.id
        self.logger.info(f"User {chat_id} started bot or requested help.")

        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        item_aqi = telebot.types.KeyboardButton("/aqi")
        item_setinterval = telebot.types.KeyboardButton("/setinterval")
        item_help = telebot.types.KeyboardButton("/help")
        markup.add(item_aqi, item_setinterval, item_help)

        self.bot.reply_to(
            message,
            """\
به ربات اطلاع‌رسانی کیفیت هوا خوش آمدید!

من به شما کمک می‌کنم تا از وضعیت کیفیت هوای شهر خرمشهر مطلع شوید.

برای دسترسی سریع‌تر به دستورات، از دکمه‌های زیر استفاده کنید:

**دستورات:**
/aqi - دریافت وضعیت فعلی کیفیت هوا
/setinterval <minutes> - تنظیم فاصله زمانی به‌روزرسانی (به دقیقه)
/help - نمایش این راهنما
""",
            reply_markup=markup,
            parse_mode="Markdown",
        )

    def send_current_aqi(self, message):
        """Handles the /aqi command to send current air quality information to the user."""
        self.logger.info(f"User {message.chat.id} requested current AQI.")
        data = self.get_air_quality_data()
        if data:
            analysis = self.analyze_air_quality(data)
            if analysis:
                self.send_update_to_telegram(analysis)
            else:
                self.bot.reply_to(
                    message, "خطا در تحلیل داده‌ها.", parse_mode="Markdown"
                )
                self.logger.warning(
                    f"Failed to analyze air quality data for user {message.chat.id}."
                )
        else:
            self.bot.reply_to(
                message,
                "دریافت داده‌های کیفیت هوا با خطا مواجه شد.",
                parse_mode="Markdown",
            )
            self.logger.error(
                f"Failed to retrieve air quality data for user {message.chat.id}."
            )

    def set_update_interval(self, message):
        """Handles the /setinterval command to set the update interval in minutes."""
        try:
            parts = message.text.split()
            if len(parts) != 2:
                raise ValueError("Invalid number of arguments")
            minutes = int(parts[1])
            if minutes <= 0:
                self.bot.reply_to(
                    message,
                    "لطفاً یک عدد مثبت برای فاصله زمانی وارد کنید.",
                    parse_mode="Markdown",
                )
                self.logger.warning(
                    f"User {message.chat.id} tried to set invalid update interval: {minutes} minutes."
                )
            else:
                self.config.update_interval = minutes * 60
                self.bot.reply_to(
                    message,
                    f"فاصله زمانی به‌روزرسانی به {minutes} دقیقه تغییر یافت.",
                    parse_mode="Markdown",
                )
                self.logger.info(
                    f"User {message.chat.id} set update interval to {minutes} minutes."
                )
        except (ValueError, IndexError):
            self.bot.reply_to(
                message,
                "فرمت دستور اشتباه است. لطفاً از /setinterval <minutes> استفاده کنید.",
                parse_mode="Markdown",
            )
            self.logger.warning(
                f"User {message.chat.id} used incorrect /setinterval command format: {message.text}"
            )

    def force_air_quality_update(self, message):
        """Forces an immediate air quality update, only accessible to the admin user."""
        if str(message.chat.id) == self.config.chat_id:
            self.logger.info(f"Admin user {message.chat.id} requested forced update.")
            self.bot.reply_to(
                message, "در حال به‌روزرسانی فوری کیفیت هوا...", parse_mode="Markdown"
            )
            self._perform_air_quality_update()
            self.bot.reply_to(
                message, "به‌روزرسانی فوری کیفیت هوا انجام شد.", parse_mode="Markdown"
            )
        else:
            self.logger.warning(
                f"Unauthorized user {message.chat.id} tried to force update."
            )
            self.bot.reply_to(
                message,
                "شما مجوز دسترسی به این دستور را ندارید.",
                parse_mode="Markdown",
            )  # "You are not authorized to use this command."

    def run(self):
        """Main function to start the bot, set up command handlers, and begin periodic updates."""
        print("Starting air quality bot...")
        self.logger.info("Starting air quality bot...")
        threading.Thread(target=self.periodic_update, daemon=True).start()
        self.logger.info("Periodic update thread started.")

        # Command handlers setup within the class
        self.bot.message_handler(commands=["start", "help"])(self.send_welcome)
        self.bot.message_handler(commands=["aqi"])(self.send_current_aqi)
        self.bot.message_handler(commands=["setinterval"])(self.set_update_interval)
        self.bot.message_handler(commands=["forceupdate"])(
            self.force_air_quality_update
        )  # Admin command

        try:
            self.bot.infinity_polling()
            self.logger.info("Bot polling started.")
        except Exception as e:
            self.logger.critical(f"Bot polling failed: {e}")


def main():
    """Main entry point of the application. Creates and runs the AirQualityBot instance."""
    config = Config()
    bot_instance = AirQualityBot(config)
    bot_instance.run()


if __name__ == "__main__":
    main()
