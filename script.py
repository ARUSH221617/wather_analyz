import time
import json
import requests
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types import content
import telebot
import logging
from requests.exceptions import ConnectionError
from telebot.apihelper import ApiException
import os

genai.configure(api_key="AIzaSyAnGBWeiaRtdH8PvFbBK-XDK2C5ondAbwM")

TELEGRAM_BOT_TOKEN = "7582788883:AAGHzrsvkSZWDV-5Lhx25lVYr47PDU---80"
CHAT_ID = "5482937915"
HISTORY_FILE = "data/history.json"

# bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

logging.basicConfig(
    level=logging.ERROR, format="%(asctime)s - %(levelname)s - %(message)s"
)


def load_history(filename=HISTORY_FILE):
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_history(history, filename=HISTORY_FILE):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=4)


def get_water_quality_data():
    """دریافت داده‌های کیفیت آب و سطح آلودگی خوزستان و خرمشهر از سایت سازمان محیط زیست"""
    try:
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9,fa-IR;q=0.8,fa;q=0.7,en-AS;q=0.6,ml;q=0.5",
            "cookie": "ASP.NET_SessionId=trt5liayginmcbyenxjnylri; _ga=GA1.2.1789628695.1737318204; _gid=GA1.2.675195048.1737318204; _ga_4E2W81YF2D=GS1.2.1737318206.1.1.1737318574.0.0.0",
            "dnt": "1",
            "priority": "u=1, i",
            "referer": "https://aqms.doe.ir/",
            "sec-ch-ua": '"Chromium";v="129", "Not=A?Brand";v="8"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        }
        response = requests.get(
            "https://aqms.doe.ir/Home/LoadAQIMap?id=2",
            headers=headers,
            proxies=None,
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            print(data)
            return data
        else:
            print("خطا در دریافت داده‌های کیفیت آب از سازمان محیط زیست")
            return None
    except requests.exceptions.RequestException as e:
        print(f"خطا در ارتباط با سرور سازمان محیط زیست: {e}")
        return None


def analyze_water_quality(data):
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
                اطلاعات کیفیت هوا و آلودگی در شهر های استان خوزستان را از کاربر دریافت کنید و سپس داده‌ها را تحلیل کنید. ابتدا نام شهر و شماره AQI را فهرست کنید، مثلاً "خرمشهر:‌ 145". سپس وضعیت آلودگی را با استفاده از ایموجی‌ها مشخص کنید: 🔴،🟣،🟠،🟡،🟢. اگر AQI بیشتر از 140 بود، بنویسید "احتمال تعطیلی مدارس"، برای AQI بالای 180 بنویسید "تعطیلی مدارس و دانشگاه‌ها" و برای AQI بیش از 200 بنویسید "تعطیلی". در پایان، توضیحی دربارهٔ معانی ایموجی‌ها اضافه کنید.
            """,
        )

        chat_session = model.start_chat(history=load_history())

        response = chat_session.send_message(content=json.dumps(data))
        analysis = response.text

        # Ensure the history is a list before saving
        # history = chat_session.history
        # if not isinstance(history, list):
        #     history = [history]

        # save_history(history)

        return analysis
    except Exception as e:
        print(f"خطا در تحلیل داده‌ها: {e}")
        return None


def send_update_to_telegram(message):
    max_retries = 5
    delay = 10  # ثانیه

    bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

    # Ping Telegram API with retries
    for attempt in range(max_retries):
        try:
            bot.get_me()
            print("پینگ به تلگرام موفقیت آمیز بود.")
            break  # Exit the loop if successful
        except requests.exceptions.RequestException as e:
            logging.error(f"خطا در پینگ به تلگرام (RequestException): {e}")
            if attempt < max_retries - 1:
                print(f"تلاش مجدد برای پینگ در {delay} ثانیه...")
                time.sleep(delay)
            else:
                print("تعداد تلاش‌ها برای پینگ به پایان رسید.")
                return  # Exit the function if all retries failed
        except Exception as e:
            logging.error(f"خطا در پینگ به تلگرام: {e}")
            return  # Exit the function if a non-request exception occurs

    if len(message) > 4096:
        parts = [message[i : i + 4096] for i in range(0, len(message), 4096)]
        for part in parts:
            for attempt in range(max_retries):
                try:
                    bot.send_message(CHAT_ID, part)
                    print("بخشی از بروزرسانی به تلگرام ارسال شد.")
                    break  # Exit the inner loop if successful
                except (ConnectionError, requests.exceptions.RequestException) as e:
                    logging.error(
                        f"خطا در ارسال به تلگرام (ConnectionError/RequestException): {e}"
                    )
                    if attempt < max_retries - 1:
                        print(f"تلاش مجدد در ارسال پیام به تلگرام در {delay} ثانیه...")
                        time.sleep(delay)
                    else:
                        print("تعداد تلاش‌ها به پایان رسید. پیام ارسال نشد.")
                        break  # Exit the inner loop if all retries failed
                except ApiException as e:
                    logging.error(f"خطا در ارسال به تلگرام (ApiException): {e}")
                    break  # Exit the inner loop if ApiException occurs
                except Exception as e:
                    logging.error(f"خطا در ارسال به تلگرام: {e}")
                    break  # Exit the inner loop if a non-request exception occurs
    else:
        for attempt in range(max_retries):
            try:
                bot.send_message(CHAT_ID, message)
                print("بروزرسانی به تلگرام ارسال شد.")
                break  # Exit the inner loop if successful
            except (ConnectionError, requests.exceptions.RequestException) as e:
                logging.error(
                    f"خطا در ارسال به تلگرام (ConnectionError/RequestException): {e}"
                )
                if attempt < max_retries - 1:
                    print(f"تلاش مجدد در ارسال پیام به تلگرام در {delay} ثانیه...")
                    time.sleep(delay)
                else:
                    print("تعداد تلاش‌ها به پایان رسید. پیام ارسال نشد.")
                    break  # Exit the inner loop if all retries failed
            except ApiException as e:
                logging.error(f"خطا در ارسال به تلگرام (ApiException): {e}")
                break  # Exit the inner loop if ApiException occurs
            except Exception as e:
                logging.error(f"خطا در ارسال به تلگرام: {e}")
                break  # Exit the inner loop if a non-request exception occurs


def main():
    while True:
        data = get_water_quality_data()
        if data:
            analysis = analyze_water_quality(data)
            if analysis:
                send_update_to_telegram(analysis)
        else:
            send_update_to_telegram("داده‌ای برای تحلیل وجود ندارد.")

        time.sleep(3600)


if __name__ == "__main__":
    main()
