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
        # IPv4 von 4.myip.is abrufen
        ipv4 = None
        try:
            response = requests.get("https://4.myip.is/", timeout=5)
            ipv4_data = response.json()
            ipv4 = ipv4_data.get("ip")
            logging.debug(f"IPv4 Response: {ipv4_data}")
        except Exception as e:
            logging.error(f"Fehler beim Abrufen der IPv4: {e}")

        # IPv6 von 6.myip.is abrufen
        ipv6 = None
        try:
            response = requests.get("https://6.myip.is/", timeout=5)
            ipv6_data = response.json()
            ipv6 = ipv6_data.get("ip")
            logging.debug(f"IPv6 Response: {ipv6_data}")

            # Prüfen ob es wirklich IPv6 ist (enthält ':')
            if ipv6 and ":" not in ipv6:
                logging.warning(f"Erwartete IPv6, bekam aber: {ipv6}")
                ipv6 = None

        except Exception as e:
            logging.warning(f"Fehler beim Abrufen der IPv6: {e}")

        if not ipv6:
            logging.info("Keine IPv6-Adresse verfügbar.")
            ipv6 = "Nicht verfügbar"

        if not ipv4:
            logging.error("Konnte IPv4 nicht abrufen!")
            return None, None

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
    try:
        bot = Bot(TOKEN)
        message = "🌐 *IP-Adresse geändert:*\n\n"
        message += f"🌐 IPv4: `{ipv4}`\n"

        if ipv6 and ipv6 != "Nicht verfügbar":
            message += f"🌍 IPv6: `{ipv6}`\n"
        else:
            message += "🌍 IPv6: ❌ Nicht verfügbar\n"

        message += f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"

        bot.send_message(chat_id=CHAT_ID, text=message, parse_mode="Markdown")
        logging.info("Benachrichtigung über Telegram gesendet.")
    except Exception as e:
        logging.error(f"Fehler beim Senden der Telegram-Nachricht: {e}")


def check_and_update():
    ipv4, ipv6 = get_ips()
    if not ipv4:
        logging.error("Konnte IPv4 nicht abrufen.")
        return

    history = load_history()

    # Prüfen ob sich etwas geändert hat
    if history:
        last_entry = history[-1]
        if last_entry["ipv4"] == ipv4 and last_entry.get("ipv6") == ipv6:
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
        ipv6_text = latest.get("ipv6", "Nicht verfügbar")
        if ipv6_text == "Nicht verfügbar":
            ipv6_display = "❌ Nicht verfügbar"
        else:
            ipv6_display = ipv6_text

        timestamp = datetime.fromisoformat(latest["timestamp"]).strftime("%d.%m.%Y %H:%M:%S")
        await update.message.reply_text(
            f"📍 Letzte bekannte IP:\n"
            f"🌐 IPv4: `{latest['ipv4']}`\n"
            f"🌍 IPv6: `{ipv6_display}`\n"
            f"📅 {timestamp}",
            parse_mode="Markdown",
        )


async def handle_check(update, context):
    """Führt eine manuelle IP-Prüfung durch"""
    await update.message.reply_text("🔍 Prüfe aktuelle IP-Adressen...")

    ipv4, ipv6 = get_ips()
    if not ipv4:
        await update.message.reply_text("❌ Fehler beim Abrufen der IP-Adressen.")
        return

    history = load_history()
    changed = False

    if history:
        last_entry = history[-1]
        if last_entry["ipv4"] != ipv4 or last_entry.get("ipv6") != ipv6:
            changed = True
    else:
        changed = True

    if changed:
        timestamp = datetime.now().isoformat()
        history.append({"timestamp": timestamp, "ipv4": ipv4, "ipv6": ipv6})
        save_history(history)

        ipv6_display = ipv6 if ipv6 != "Nicht verfügbar" else "❌ Nicht verfügbar"
        status_text = "geändert" if len(history) > 1 else "erfasst"
        await update.message.reply_text(
            f"✅ IP-Adresse {status_text}:\n"
            f"🌐 IPv4: `{ipv4}`\n"
            f"🌍 IPv6: `{ipv6_display}`\n"
            f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
            parse_mode="Markdown",
        )
    else:
        ipv6_display = ipv6 if ipv6 != "Nicht verfügbar" else "❌ Nicht verfügbar"
        await update.message.reply_text(
            f"✅ Keine Änderung festgestellt:\n"
            f"🌐 IPv4: `{ipv4}`\n"
            f"🌍 IPv6: `{ipv6_display}`",
            parse_mode="Markdown",
        )


async def handle_history(update, context):
    """Zeigt die letzten 5 IP-Änderungen"""
    history = load_history()
    if not history:
        await update.message.reply_text("Keine IP-Historie gefunden.")
        return

    # Letzte 5 Einträge
    recent = history[-5:]
    message = "📊 *IP-Historie* (letzte 5):\n\n"

    for i, entry in enumerate(reversed(recent), 1):
        ipv6_display = entry.get("ipv6", "Nicht verfügbar")
        if ipv6_display == "Nicht verfügbar":
            ipv6_display = "❌ N/A"

        timestamp = datetime.fromisoformat(entry["timestamp"]).strftime("%d.%m. %H:%M")
        message += f"*{i}.* `{timestamp}`\n"
        message += f"   🌐 `{entry['ipv4']}`\n"
        message += f"   🌍 `{ipv6_display}`\n\n"

    await update.message.reply_text(message, parse_mode="Markdown")


if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        logging.error("TELEGRAM_TOKEN und TELEGRAM_CHAT_ID müssen gesetzt sein!")
        exit(1)

    logging.info("Starte IP-Überwachung und Telegram-Bot.")
    check_and_update()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("ip", handle_ip))
    app.add_handler(CommandHandler("history", handle_history))
    app.add_handler(CommandHandler("check", handle_check))

    logging.info("Bot gestartet. Befehle:")
    logging.info("  /ip - Zeigt letzte bekannte IP")
    logging.info("  /history - Zeigt IP-Historie")
    logging.info("  /check - Prüft aktuelle IP manuell")
    app.run_polling()
