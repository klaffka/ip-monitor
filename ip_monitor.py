import requests
import json
import os
import logging
from datetime import datetime
from telegram import Bot
from telegram.ext import Application, CommandHandler

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

IP_FILE = "data/ip_history.json"
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def get_ips():
    try:
        ipv4 = requests.get("https://api.ipify.org", timeout=5).text
        ipv6 = requests.get("https://api64.ipify.org", timeout=5).text
        logging.info(f"Aktuelle IPs: IPv4={ipv4}, IPv6={ipv6}")
        return ipv4, ipv6
    except Exception as e:
        logging.error(f"Fehler beim Abrufen der IPs: {e}")
        return None, None


def load_history():
    if os.path.exists(IP_FILE):
        with open(IP_FILE) as f:
            return json.load(f)
    return []


def save_history(data):
    os.makedirs(os.path.dirname(IP_FILE), exist_ok=True)
    with open(IP_FILE, "w") as f:
        json.dump(data, f, indent=2)
    logging.info("IP-Historie gespeichert.")


def notify(ipv4, ipv6):
    bot = Bot(TOKEN)
    message = f"🌐 IP-Adresse geändert:\n" f"IPv4: {ipv4}\n" f"IPv6: {ipv6}"
    bot.send_message(chat_id=CHAT_ID, text=message)
    logging.info("Benachrichtigung über Telegram gesendet.")


def check_and_update():
    ipv4, ipv6 = get_ips()
    if not ipv4 and not ipv6:
        return

    history = load_history()
    if history and history[-1]["ipv4"] == ipv4 and history[-1]["ipv6"] == ipv6:
        logging.info("Keine Änderung der IP-Adresse festgestellt.")
        return

    timestamp = datetime.now().isoformat()
    history.append({"timestamp": timestamp, "ipv4": ipv4, "ipv6": ipv6})
    save_history(history)
    notify(ipv4, ipv6)


async def handle_ip(update, context):
    history = load_history()
    if not history:
        await update.message.reply_text("Keine IP-Daten gefunden.")
    else:
        latest = history[-1]
        await update.message.reply_text(
            f"📍 Letzte bekannte IP:\n"
            f"IPv4: {latest['ipv4']}\n"
            f"IPv6: {latest['ipv6']}\n"
            f"📅 {latest['timestamp']}"
        )


if __name__ == "__main__":
    logging.info("Starte IP-Überwachung und Telegram-Bot.")
    check_and_update()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("ip", handle_ip))
    app.run_polling()
