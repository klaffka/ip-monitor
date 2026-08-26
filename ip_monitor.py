import asyncio
import ipaddress
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

import requests
from telegram.ext import Application, CommandHandler

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

IP_FILE = "data/ip_history.json"
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Regex für IPv4 (4 Gruppen von 1-3 Ziffern, getrennt durch Punkte)
IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def fetch_local_ip():
    """Ruft die lokale/private IP-Adresse über ipify ab."""
    try:
        response = requests.get("https://api.ipify.org?format=json", timeout=5)
        response.raise_for_status()
        return response.json().get("ip")
    except requests.RequestException:
        return None


def _parse_ip(ip_str):
    """Parsen einer IP-Adresse. Gibt IPv4Address oder IPv6Address zurück."""
    try:
        return ipaddress.ip_address(ip_str)
    except ValueError:
        return None


def detect_cgnat(public_ip, local_ip, ipv6):
    """
    Erkennt ob der Nutzer hinter Carrier-Grade NAT (CGNAT) sitzt.

    Prüft:
    1. Ob die öffentliche IPv4 im RFC-6598-CGNAT-Bereich (100.64.0.0/10) liegt.
    2. Ob sowohl öffentliche als auch lokale IPv4 in privaten RFC-1918-Bereichen liegen.
    3. Ob die öffentliche IPv6 im ULA-Bereich (fc00::/7) liegt.
    """
    reasons = []

    # Öffentliche IPv4 gegen CGNAT-/Private-Bereiche prüfen
    if public_ip and public_ip != "Nicht verfügbar":
        addr = _parse_ip(public_ip)
        if addr is not None and isinstance(addr, ipaddress.IPv4Address):
            cgnat_net = ipaddress.ip_network("100.64.0.0/10")
            if addr in cgnat_net:
                reasons.append(
                    "Die öffentliche IPv4-Adresse liegt im "
                    "CGNAT-Bereich (RFC 6598, 100.64.0.0/10)."
                )
            elif local_ip:
                local_addr = _parse_ip(local_ip)
                if local_addr is not None:
                    private_nets = [
                        ipaddress.ip_network("10.0.0.0/8"),
                        ipaddress.ip_network("172.16.0.0/12"),
                        ipaddress.ip_network("192.168.0.0/16"),
                    ]
                    if addr in private_nets and local_addr in private_nets and addr != local_addr:
                        reasons.append(
                            "Sowohl öffentliche als auch lokale IPv4 liegen in "
                            "privaten RFC-1918-Bereichen."
                        )

    # Öffentliche IPv6 auf ULA prüfen (IPv6-Äquivalent zu privaten IPv4-Adressen)
    if ipv6 and ipv6 != "Nicht verfügbar":
        addr = _parse_ip(ipv6)
        if addr is not None and isinstance(addr, ipaddress.IPv6Address):
            ula_net = ipaddress.ip_network("fc00::/7")
            if addr in ula_net:
                reasons.append(
                    "Die öffentliche IPv6-Adresse liegt im ULA-Bereich "
                    "(fc00::/7, analogen privatem IPv4)."
                )

    return bool(reasons), reasons


def get_ips():
    """Ruft IPv4, IPv6 und lokale IP ab. Gibt (ipv4, ipv6, local_ip) zurück."""
    ipv4 = None
    ipv6 = "Nicht verfügbar"
    local_ip = None

    try:
        response = requests.get("https://4.myip.is/", timeout=5)
        response.raise_for_status()
        ipv4_data = response.json()
        raw = ipv4_data.get("ip", "")
        if IPV4_RE.match(str(raw)):
            ipv4 = raw
            logging.debug(f"IPv4 Response: {ipv4_data}")
        else:
            logging.warning(f"Ungültige IPv4-Adresse vom Server: {raw!r}")
    except requests.RequestException as e:
        logging.error(f"Fehler beim Abrufen der IPv4: {e}")

    try:
        response = requests.get("https://6.myip.is/", timeout=5)
        response.raise_for_status()
        ipv6_data = response.json()
        raw = ipv6_data.get("ip", "")
        if raw and ":" in str(raw):
            ipv6 = raw
            logging.debug(f"IPv6 Response: {ipv6_data}")
        else:
            logging.info("Keine IPv6-Adresse verfügbar.")
            ipv6 = "Nicht verfügbar"
    except requests.RequestException as e:
        logging.warning(f"Fehler beim Abrufen der IPv6: {e}")
        ipv6 = "Nicht verfügbar"

    local_ip = fetch_local_ip()

    if ipv4:
        local_str = local_ip or "N/A"
        logging.info(f"Aktuelle IPs: IPv4={ipv4}, IPv6={ipv6}, Local={local_str}")

    return ipv4, ipv6, local_ip


def load_history():
    if not os.path.exists(IP_FILE):
        return []
    try:
        with open(IP_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logging.error(f"Konnte IP-Historie nicht laden ({e}). Leere Liste wird verwendet.")
        return []


def save_history(data):
    os.makedirs(os.path.dirname(IP_FILE), exist_ok=True)
    dir_name = os.path.dirname(IP_FILE)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".json.tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, IP_FILE)
        logging.info("IP-Historie gespeichert.")
    except OSError as e:
        logging.error(f"Konnte IP-Historie nicht speichern ({e})")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def build_ip_message(ipv4, ipv6, cgnat_detected, cgnat_reasons, changed_at):
    """Erzeugt den Nachrichtentext für IP-Benachrichtigungen."""
    status = "geändert" if changed_at else "erfasst"
    ipv6_display = "❌ N/A" if ipv6 == "Nicht verfügbar" else ipv6
    timestamp_str = changed_at.strftime("%d.%m.%Y %H:%M:%S")

    text = f"🌐 IP-Adresse {status}:\n\n"
    text += f"🌐 IPv4: `{ipv4}`\n"
    text += f"🌍 IPv6: `{ipv6_display}`\n"
    if cgnat_detected:
        text += "\n⚠️ *Carrier-Grade NAT (CGNAT) erkannt*\n"
        for reason in cgnat_reasons:
            text += f"   • {reason}\n"
    text += f"\n📅 {timestamp_str}"
    return text


def check_and_record(ipv4, ipv6, local_ip):
    """
    Prüft ob die IP sich geändert hat, speichert bei Änderung und gibt True zurück.
    Enthält auch CGNAT-Erkennung.
    """
    history = load_history()

    changed = False
    if history:
        last_entry = history[-1]
        if last_entry["ipv4"] != ipv4 or last_entry.get("ipv6") != ipv6:
            changed = True
    else:
        changed = True

    if changed:
        cgnat_detected, cgnat_reasons = detect_cgnat(ipv4, local_ip, ipv6)
        timestamp = datetime.now(timezone.utc).isoformat()
        history.append(
            {
                "timestamp": timestamp,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "cgnat": cgnat_detected,
                "cgnat_reasons": cgnat_reasons,
            }
        )
        save_history(history)

    return changed


async def check_initial_ip(application):
    """Initialer IP-Check beim Start (wird als post_init ausgeführt)."""
    try:
        ipv4, ipv6, local_ip = await asyncio.to_thread(get_ips)

        if not ipv4:
            logging.error("Konnte IPv4 beim Initialcheck nicht abrufen.")
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text="❌ Konnte IPv4 beim Start nicht abrufen.",
                parse_mode="Markdown",
            )
            return

        changed = check_and_record(ipv4, ipv6, local_ip)

        if changed:
            cgnat_detected, cgnat_reasons = detect_cgnat(ipv4, local_ip, ipv6)
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text=build_ip_message(ipv4, ipv6, cgnat_detected, cgnat_reasons, changed_at=True),
                parse_mode="Markdown",
            )
        else:
            logging.info("Keine Änderung der IP-Adresse festgestellt.")
    except Exception as e:
        logging.error(f"Fehler im Initial-Check: {e}")
        try:
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text=f"❌ Fehler beim IP-Check: {e}",
                parse_mode="Markdown",
            )
        except Exception:
            pass


async def handle_ip(update, context):
    history = load_history()
    if not history:
        await update.message.reply_text("Keine IP-Daten gefunden.")
        return

    latest = history[-1]
    ipv4 = latest.get("ipv4", "?")
    ipv6 = latest.get("ipv6", "Nicht verfügbar")
    cgnat = latest.get("cgnat", False)
    cgnat_reasons = latest.get("cgnat_reasons", [])
    try:
        changed_at = datetime.fromisoformat(latest["timestamp"]).astimezone(timezone.utc)
    except (ValueError, KeyError):
        changed_at = datetime.now(timezone.utc)

    await update.message.reply_text(
        text=build_ip_message(ipv4, ipv6, cgnat, cgnat_reasons, changed_at),
        parse_mode="Markdown",
    )


async def handle_check(update, context):
    """Führt eine manuelle IP-Prüfung durch."""
    await update.message.reply_text("🔍 Prüfe aktuelle IP-Adressen...")

    ipv4, ipv6, local_ip = await asyncio.to_thread(get_ips)
    if not ipv4:
        await update.message.reply_text("❌ Fehler beim Abrufen der IP-Adressen.")
        return

    changed = check_and_record(ipv4, ipv6, local_ip)

    if changed:
        cgnat_detected, cgnat_reasons = detect_cgnat(ipv4, local_ip, ipv6)
        await update.message.reply_text(
            text=build_ip_message(ipv4, ipv6, cgnat_detected, cgnat_reasons, changed_at=True),
            parse_mode="Markdown",
        )
    else:
        cgnat_detected, cgnat_reasons = detect_cgnat(ipv4, local_ip, ipv6)
        await update.message.reply_text(
            text=build_ip_message(ipv4, ipv6, cgnat_detected, cgnat_reasons, changed_at=False),
            parse_mode="Markdown",
        )


async def handle_history(update, context):
    """Zeigt die letzten 5 IP-Änderungen."""
    history = load_history()
    if not history:
        await update.message.reply_text("Keine IP-Historie gefunden.")
        return

    recent = history[-5:]
    message = "**IP-Historie** (letzte 5):\n\n"

    for i, entry in enumerate(reversed(recent), 1):
        ipv6_display = (
            "❌ N/A"
            if entry.get("ipv6", "Nicht verfügbar") == "Nicht verfügbar"
            else entry.get("ipv6", "N/A")
        )

        try:
            changed_at = datetime.fromisoformat(entry["timestamp"]).astimezone(timezone.utc)
        except (ValueError, KeyError):
            changed_at = datetime.now(timezone.utc)

        timestamp_str = changed_at.strftime("%d.%m.%Y %H:%M")

        cgnat_badge = ""
        if entry.get("cgnat"):
            cgnat_badge = " ⚠️"

        message += f"**{i}.** `{timestamp_str}`{cgnat_badge}\n"
        message += f"   🌐 `{entry.get('ipv4', 'N/A')}`\n"
        message += f"   🌍 `{ipv6_display}`\n\n"

    await update.message.reply_text(message, parse_mode="Markdown")


async def error_handler(update, context):
    logging.error(f"Exception while handling an update: {context.error}", exc_info=context.error)


if __name__ == "__main__":
    if not TOKEN or not CHAT_ID:
        logging.error("TELEGRAM_TOKEN und TELEGRAM_CHAT_ID müssen gesetzt sein!")
        sys.exit(1)

    app = Application.builder().token(TOKEN).build()
    app.post_init = check_initial_ip
    app.add_handler(CommandHandler("ip", handle_ip))
    app.add_handler(CommandHandler("history", handle_history))
    app.add_handler(CommandHandler("check", handle_check))
    app.add_error_handler(error_handler)

    logging.info("Bot gestartet. Befehle:")
    logging.info("  /ip - Zeigt letzte bekannte IP")
    logging.info("  /history - Zeigt IP-Historie")
    logging.info("  /check - Prüft aktuelle IP manuell")
    app.run_polling()
