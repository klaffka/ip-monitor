import asyncio
import functools
import ipaddress
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from telegram.ext import Application, CommandHandler

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _int_env(name, default):
    """Liest eine ganze Zahl aus einer Env-Variable. Beendet sich bei ungültigem Wert."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logging.error(f"{name} ist ungültig (erwartet ganze Zahl): {raw!r}")
        sys.exit(1)


IP_FILE = "data/ip_history.json"
HEARTBEAT_FILE = "data/heartbeat"
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL")
# Webhook-Modus muss für Telegram erreichbar sein, daher 0.0.0.0 als Default.
WEBHOOK_LISTEN = os.getenv("TELEGRAM_WEBHOOK_LISTEN", "0.0.0.0")  # nosec B104
WEBHOOK_PORT = _int_env("TELEGRAM_WEBHOOK_PORT", 8443)
SUPPORTED_WEBHOOK_PORTS = (80, 443, 88, 8443)
HISTORY_LIMIT = _int_env("HISTORY_LIMIT", 100)
HEARTBEAT_INTERVAL = 60

# Regex für IPv4 (4 Gruppen von 1-3 Ziffern, getrennt durch Punkte)
IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

IPV4_PROVIDERS = [
    "https://4.myip.is/",
    "https://api.ipify.org?format=json",
]
IPV6_PROVIDERS = [
    "https://6.myip.is/",
    "https://api6.ipify.org?format=json",
]

STARTED_AT = datetime.now(timezone.utc)
LAST_CHECK_AT = None


def _parse_chat_ids(raw):
    """Pars TELEGRAM_CHAT_ID (kommagetrennt) zu einer Liste von Chat-IDs."""
    if not raw:
        return []
    return [int(part) for part in (p.strip() for p in raw.split(",")) if part]


def _parse_ts(value):
    """Parsen eines ISO-8601-Zeitstempels. Gibt None zurück, wenn ungültig."""
    try:
        return datetime.fromisoformat(value).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _norm_ipv6(value):
    """Normiert IPv6-Darstellungen: None und 'Nicht verfügbar' -> None."""
    return None if value in (None, "Nicht verfügbar") else value


def _parse_ip(ip_str):
    """Parsen einer IP-Adresse. Gibt IPv4Address oder IPv6Address zurück."""
    try:
        return ipaddress.ip_address(ip_str)
    except ValueError:
        return None


def fetch_local_ip():
    """Ruft die lokale/private IP-Adresse über ipify ab."""
    try:
        response = requests.get("https://api.ipify.org?format=json", timeout=5)
        response.raise_for_status()
        return response.json().get("ip")
    except (requests.RequestException, ValueError):
        return None


def _fetch_ip_from(url, is_ipv6):
    """Ruft eine öffentliche IP von einem einzelnen Provider ab."""
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    raw = str(response.json().get("ip", "")).strip()
    if is_ipv6:
        return raw if ":" in raw else None
    return raw if IPV4_RE.match(raw) else None


def fetch_public_ip(is_ipv6):
    """Ruft die öffentliche IP ab und probiert bei Fehler Fallback-Provider."""
    providers = IPV6_PROVIDERS if is_ipv6 else IPV4_PROVIDERS
    for url in providers:
        try:
            ip = _fetch_ip_from(url, is_ipv6)
            if ip:
                if url != providers[0]:
                    logging.info(f"Fallback-Provider genutzt: {url}")
                return ip
            logging.warning(f"Ungültige IP-Antwort von {url}")
        except (requests.RequestException, ValueError) as e:
            logging.warning(f"IP-Provider {url} nicht erreichbar: {e}")
    return None


def fetch_ip_info(ip):
    """Ruft Provider/Standort einer IPv4-Adresse ab (ipinfo.io). Gibt (isp, location)."""
    try:
        response = requests.get(f"https://ipinfo.io/{ip}/json", timeout=5)
        response.raise_for_status()
        data = response.json()
        return data.get("org") or None, data.get("location") or None
    except (requests.RequestException, ValueError) as e:
        logging.debug(f"IP-Info nicht verfügbar: {e}")
        return None, None


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
                    public_is_private = any(addr in net for net in private_nets)
                    local_is_private = any(local_addr in net for net in private_nets)
                    if public_is_private and local_is_private and addr != local_addr:
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
    global LAST_CHECK_AT
    LAST_CHECK_AT = datetime.now(timezone.utc)

    ipv4 = fetch_public_ip(False)
    ipv6 = fetch_public_ip(True)
    local_ip = fetch_local_ip()

    if ipv4:
        local_str = local_ip or "N/A"
        logging.info(f"Aktuelle IPs: IPv4={ipv4}, IPv6={ipv6 or 'N/A'}, Local={local_str}")
    else:
        logging.error("Keine öffentliche IPv4-Adresse über keinen Provider abrufbar.")

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


def write_heartbeat():
    """Schreibt einen Liveness-Heartbeat (für den Docker-Healthcheck)."""
    os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
    with open(HEARTBEAT_FILE, "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())


async def _heartbeat_loop(application):
    """Schreibt zyklisch einen Heartbeat, damit der Healthcheck Liveness prüfen kann."""
    while True:
        try:
            write_heartbeat()
        except OSError as e:
            logging.error(f"Heartbeat nicht schreibbar: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


def build_ip_message(
    ipv4,
    ipv6,
    at,
    *,
    changed,
    cgnat_detected=False,
    cgnat_reasons=(),
    isp=None,
    location=None,
):
    """Erzeugt den Nachrichtentext für IP-Benachrichtigungen."""
    status = "geändert" if changed else "erfasst"
    ipv6_display = "❌ N/A" if not ipv6 else ipv6
    timestamp_str = at.strftime("%d.%m.%Y %H:%M:%S")

    text = f"🌐 IP-Adresse {status}:\n\n"
    text += f"🌐 IPv4: `{ipv4}`\n"
    text += f"🌍 IPv6: `{ipv6_display}`\n"
    if isp:
        text += f"📡 {isp}\n"
    if location:
        text += f"📍 {location}\n"
    if cgnat_detected:
        text += "\n⚠️ *Carrier-Grade NAT (CGNAT) erkannt*\n"
        for reason in cgnat_reasons:
            text += f"   • {reason}\n"
    text += f"\n📅 {timestamp_str}"
    return text


def entry_message(entry, changed):
    """Erzeugt den Nachrichtentext aus einem History-Eintrag."""
    at = _parse_ts(entry.get("timestamp")) or datetime.now(timezone.utc)
    return build_ip_message(
        entry.get("ipv4", "?"),
        _norm_ipv6(entry.get("ipv6")),
        at,
        changed=changed,
        cgnat_detected=entry.get("cgnat", False),
        cgnat_reasons=entry.get("cgnat_reasons", []),
        isp=entry.get("isp"),
        location=entry.get("location"),
    )


def check_and_record(ipv4, ipv6, local_ip):
    """
    Prüft ob die IP sich geändert hat, speichert bei Änderung und gibt
    (changed, entry) zurück. entry ist None, wenn sich nichts geändert hat.
    """
    history = load_history()

    changed = False
    if history:
        last = history[-1]
        changed = last.get("ipv4") != ipv4 or _norm_ipv6(last.get("ipv6")) != _norm_ipv6(ipv6)
    else:
        changed = True

    if not changed:
        return False, None

    cgnat_detected, cgnat_reasons = detect_cgnat(ipv4, local_ip, ipv6)
    isp, location = fetch_ip_info(ipv4)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ipv4": ipv4,
        "ipv6": ipv6,
        "cgnat": cgnat_detected,
        "cgnat_reasons": cgnat_reasons,
        "isp": isp,
        "location": location,
    }
    history.append(entry)
    if len(history) > HISTORY_LIMIT:
        del history[: len(history) - HISTORY_LIMIT]
    save_history(history)
    return True, entry


async def send_to_chats(application, text):
    """Sendet eine Markdown-Nachricht an alle autorisierten Chats."""
    for chat_id in CHAT_IDS:
        try:
            await application.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Telegram-Nachricht an Chat {chat_id} fehlgeschlagen: {e}")


async def check_initial_ip(application):
    """Initialer IP-Check beim Start (wird als post_init ausgeführt)."""
    try:
        ipv4, ipv6, local_ip = await asyncio.to_thread(get_ips)

        if not ipv4:
            logging.error("Konnte IPv4 beim Initialcheck nicht abrufen.")
            await send_to_chats(
                application,
                "❌ Konnte IPv4 beim Start nicht abrufen.",
            )
            return

        changed, entry = await asyncio.to_thread(check_and_record, ipv4, ipv6, local_ip)

        if changed and entry:
            await send_to_chats(application, entry_message(entry, changed=True))
        else:
            logging.info("Keine Änderung der IP-Adresse festgestellt.")
    except Exception as e:
        logging.error(f"Fehler im Initial-Check: {e}")
        try:
            await send_to_chats(application, f"❌ Fehler beim IP-Check: {e}")
        except Exception:
            # Bewusst verschluckt: Der Fehler ist bereits geloggt, ein weiterer
            # Fehlschlag bei der Benachrichtigung darf den Start nicht beenden.
            pass  # nosec B110


async def post_init(application):
    """Startet Heartbeat und führt den initialen IP-Check durch."""
    application.bot_data["heartbeat_task"] = asyncio.create_task(_heartbeat_loop(application))
    write_heartbeat()
    await check_initial_ip(application)


async def post_shutdown(application):
    """Beendet den Heartbeat und loggt den Shutdown."""
    task = application.bot_data.get("heartbeat_task")
    if task:
        task.cancel()
    logging.info("Bot heruntergefahren.")


def authorized(func):
    """Erlaubt den Command nur für autorisierte Chats (TELEGRAM_CHAT_ID)."""

    @functools.wraps(func)
    async def wrapper(update, context):
        chat = update.effective_chat
        if chat is None or chat.id not in CHAT_IDS:
            logging.warning("Command '%s' von nicht autorisiertem Chat ignoriert.", func.__name__)
            return
        await func(update, context)

    return wrapper


@authorized
async def handle_start(update, context):
    await update.message.reply_text(
        "👋 Hallo! Ich überwache deine öffentliche IP-Adresse.\n\n"
        "Befehle:\n"
        "/ip - Letzte bekannte IP\n"
        "/check - Jetzt prüfen\n"
        "/history - Letzte 5 Änderungen\n"
        "/stats - Statistik\n"
        "/status - Bot-Status"
    )


@authorized
async def handle_ip(update, context):
    history = load_history()
    if not history:
        await update.message.reply_text("Keine IP-Daten gefunden.")
        return

    await update.message.reply_text(
        text=entry_message(history[-1], changed=False),
        parse_mode="Markdown",
    )


@authorized
async def handle_check(update, context):
    """Führt eine manuelle IP-Prüfung durch."""
    await update.message.reply_text("🔍 Prüfe aktuelle IP-Adressen...")

    ipv4, ipv6, local_ip = await asyncio.to_thread(get_ips)
    if not ipv4:
        await update.message.reply_text("❌ Fehler beim Abrufen der IP-Adressen.")
        return

    changed, entry = await asyncio.to_thread(check_and_record, ipv4, ipv6, local_ip)

    if changed and entry:
        text = entry_message(entry, changed=True)
    else:
        text = build_ip_message(ipv4, _norm_ipv6(ipv6), datetime.now(timezone.utc), changed=False)
    await update.message.reply_text(text, parse_mode="Markdown")


@authorized
async def handle_history(update, context):
    """Zeigt die letzten 5 IP-Änderungen."""
    history = load_history()
    if not history:
        await update.message.reply_text("Keine IP-Historie gefunden.")
        return

    recent = history[-5:]
    message = "**IP-Historie** (letzte 5):\n\n"

    for i, entry in enumerate(reversed(recent), 1):
        ipv6 = _norm_ipv6(entry.get("ipv6"))
        ipv6_display = "❌ N/A" if not ipv6 else ipv6

        changed_at = _parse_ts(entry.get("timestamp")) or datetime.now(timezone.utc)
        timestamp_str = changed_at.strftime("%d.%m.%Y %H:%M")

        cgnat_badge = " ⚠️" if entry.get("cgnat") else ""
        message += f"**{i}.** `{timestamp_str}`{cgnat_badge}\n"
        message += f"   🌐 `{entry.get('ipv4', 'N/A')}`\n"
        message += f"   🌍 `{ipv6_display}`\n"
        if entry.get("isp"):
            message += f"   📡 {entry.get('isp')}\n"
        message += "\n"

    await update.message.reply_text(message, parse_mode="Markdown")


def format_duration(td):
    """Formatiert eine Zeitspanne als z. B. '2d 1h', '3m 10s' oder '59s'."""
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


@authorized
async def handle_status(update, context):
    """Zeigt Uptime, letzten Check und Betriebsmodus."""
    history = load_history()
    now = datetime.now(timezone.utc)
    mode = f"Webhook ({WEBHOOK_URL})" if WEBHOOK_URL else "Polling"
    last_check_str = LAST_CHECK_AT.strftime("%d.%m.%Y %H:%M:%S UTC") if LAST_CHECK_AT else "—"

    lines = [
        "🤖 *Bot-Status*",
        f"⏱️ Uptime: {format_duration(now - STARTED_AT)}",
        f"🔍 Letzter Check: {last_check_str}",
        f"🔗 Modus: {mode}",
        f"📊 Historie: {len(history)} Einträge (Limit: {HISTORY_LIMIT})",
    ]
    if history:
        last = history[-1]
        lines.append(f"🌐 Aktuelles IPv4: `{last.get('ipv4')}`")
        if last.get("cgnat"):
            lines.append("⚠️ CGNAT erkannt")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def handle_stats(update, context):
    """Zeigt Statistiken zur IP-Historie."""
    history = load_history()
    if not history:
        await update.message.reply_text("Keine IP-Historie vorhanden.")
        return

    total = len(history)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent_count = 0
    ipv6_count = 0
    cgnat_count = 0
    for entry in history:
        ts = _parse_ts(entry.get("timestamp"))
        if ts is not None and ts >= cutoff:
            recent_count += 1
        if _norm_ipv6(entry.get("ipv6")):
            ipv6_count += 1
        if entry.get("cgnat"):
            cgnat_count += 1

    message = (
        "📊 *IP-Statistik*\n\n"
        f"🗂️ Einträge gesamt: {total}\n"
        f"🔁 Änderungen (letzte 30 Tage): {recent_count}\n"
        f"🌍 IPv6 verfügbar: {ipv6_count}/{total} ({round(100 * ipv6_count / total)}%)\n"
        f"⚠️ CGNAT erkannt: {cgnat_count}/{total} ({round(100 * cgnat_count / total)}%)\n"
    )
    await update.message.reply_text(message, parse_mode="Markdown")


async def error_handler(update, context):
    logging.error(f"Exception while handling an update: {context.error}", exc_info=context.error)


def run_bot():
    app = (
        Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    )
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("ip", handle_ip))
    app.add_handler(CommandHandler("check", handle_check))
    app.add_handler(CommandHandler("history", handle_history))
    app.add_handler(CommandHandler("stats", handle_stats))
    app.add_handler(CommandHandler("status", handle_status))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        cert = os.getenv("TELEGRAM_CERT_FILE")
        key = os.getenv("TELEGRAM_KEY_FILE")
        if not cert or not key:
            logging.error("Webhook-Modus benötigt TELEGRAM_CERT_FILE und TELEGRAM_KEY_FILE.")
            sys.exit(1)
        if WEBHOOK_PORT not in SUPPORTED_WEBHOOK_PORTS:
            logging.error(
                f"TELEGRAM_WEBHOOK_PORT muss einer dieser Ports sein: {SUPPORTED_WEBHOOK_PORTS}"
            )
            sys.exit(1)
        webhook_path = urlparse(WEBHOOK_URL).path.strip("/") or "telegram"
        logging.info(f"Starte im Webhook-Modus: {WEBHOOK_URL}")
        app.run_webhook(
            listen=WEBHOOK_LISTEN,
            port=WEBHOOK_PORT,
            url_path=webhook_path,
            cert=cert,
            key=key,
            webhook_url=WEBHOOK_URL,
            drop_pending_updates=True,
            secret_token=os.getenv("TELEGRAM_WEBHOOK_SECRET"),
        )
    else:
        logging.info("Starte im Polling-Modus.")
        app.run_polling()


if __name__ == "__main__":
    try:
        CHAT_IDS = _parse_chat_ids(os.getenv("TELEGRAM_CHAT_ID"))
    except ValueError:
        logging.error(
            "TELEGRAM_CHAT_ID ist ungültig (erwartet: ganze Zahlen, getrennt durch Komma)."
        )
        sys.exit(1)

    if not TOKEN or not CHAT_IDS:
        logging.error("TELEGRAM_TOKEN und TELEGRAM_CHAT_ID müssen gesetzt sein!")
        sys.exit(1)

    logging.info("Bot gestartet. Befehle: /start /ip /check /history /stats /status")
    run_bot()
