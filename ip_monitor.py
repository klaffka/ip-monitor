import asyncio
import contextlib
import functools
import ipaddress
import json
import logging
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlparse

import requests
from telegram import BotCommand
from telegram.ext import Application, CommandHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class HistoryError(RuntimeError):
    """Die Historie konnte nicht sicher gelesen oder geschrieben werden."""


class CheckError(RuntimeError):
    """Eine IP-Pruefung konnte nicht abgeschlossen werden."""


def _int_env(name, default, minimum=None):
    """Liest eine ganze Zahl aus einer Umgebungsvariable."""
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            logging.error("%s ist ungültig (erwartet ganze Zahl): %r", name, raw)
            sys.exit(1)
    if minimum is not None and value < minimum:
        logging.error("%s muss mindestens %s sein.", name, minimum)
        sys.exit(1)
    return value


IP_FILE = "data/ip_history.json"
HEARTBEAT_FILE = "data/heartbeat"
TOKEN = os.getenv("TELEGRAM_TOKEN")
IPINFO_TOKEN = os.getenv("IPINFO_TOKEN")
WEBHOOK_URL = os.getenv("TELEGRAM_WEBHOOK_URL")
WEBHOOK_LISTEN = os.getenv("TELEGRAM_WEBHOOK_LISTEN", "0.0.0.0")  # nosec B104
WEBHOOK_PORT = _int_env("TELEGRAM_WEBHOOK_PORT", 8443, minimum=1)
SUPPORTED_WEBHOOK_PORTS = (80, 443, 88, 8443)
HISTORY_LIMIT = _int_env("HISTORY_LIMIT", 100, minimum=1)
HEARTBEAT_INTERVAL = 60

IPV4_PROVIDERS = [
    ("myip.is", "https://4.myip.is/"),
    ("ipify", "https://api.ipify.org?format=json"),
]
IPV6_PROVIDERS = [
    ("myip.is", "https://6.myip.is/"),
    ("ipify", "https://api6.ipify.org?format=json"),
]

STARTED_AT = datetime.now(timezone.utc)
LAST_ATTEMPT_AT = None
LAST_SUCCESS_AT = None
LAST_ERROR = None
CHAT_IDS = []
CHECK_LOCK = asyncio.Lock()


def _parse_chat_ids(raw):
    """Parst TELEGRAM_CHAT_ID (kommagetrennt) zu einer Liste von Chat-IDs."""
    if not raw:
        return []
    return [int(part) for part in (item.strip() for item in raw.split(",")) if part]


def _parse_ts(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _norm_ipv6(value):
    return None if value in (None, "Nicht verfügbar") else value


def _parse_ip(value):
    try:
        return ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return None


def _fetch_ip_from(url, is_ipv6):
    """Liefert eine validierte Adresse oder einen Grund fuer die Ablehnung."""
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    data = response.json()
    raw = str(data.get("ip", "")).strip() if isinstance(data, dict) else ""
    address = _parse_ip(raw)
    expected = ipaddress.IPv6Address if is_ipv6 else ipaddress.IPv4Address
    if address is None:
        return None, "ungültiges Format"
    if not isinstance(address, expected):
        return None, "falsche Adressfamilie"
    return address, None


def fetch_public_ip(is_ipv6):
    """Fragt Provider ab, bevorzugt globale IPs und liefert (IP, Warnungen)."""
    providers = IPV6_PROVIDERS if is_ipv6 else IPV4_PROVIDERS
    family = "IPv6" if is_ipv6 else "IPv4"
    fallback = None
    fallback_provider = None
    for index, (provider, url) in enumerate(providers):
        started = time.monotonic()
        try:
            address, invalid_reason = _fetch_ip_from(url, is_ipv6)
            duration_ms = round((time.monotonic() - started) * 1000)
            if invalid_reason:
                logging.warning(
                    "%s lieferte für %s %s (%d ms).",
                    provider,
                    family,
                    invalid_reason,
                    duration_ms,
                )
                continue
            if address.is_global:
                if index:
                    logging.info(
                        "Fallback-Provider %s für %s genutzt (%d ms).",
                        provider,
                        family,
                        duration_ms,
                    )
                else:
                    logging.info("Provider %s lieferte %s (%d ms).", provider, family, duration_ms)
                return str(address), []
            logging.warning(
                "%s lieferte eine nicht globale %s-Adresse (%d ms).",
                provider,
                family,
                duration_ms,
            )
            if fallback is None:
                fallback = str(address)
                fallback_provider = provider
        except (requests.RequestException, ValueError, TypeError) as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            logging.warning(
                "IP-Provider %s für %s nicht nutzbar (%d ms): %s",
                provider,
                family,
                duration_ms,
                exc,
            )
    if fallback:
        reason = f"{family}-Antwort von {fallback_provider} ist nicht global routbar."
        return fallback, [reason]
    logging.warning("Keine %s-Adresse über die konfigurierten Provider verfügbar.", family)
    return None, []


def fetch_ip_info(ip):
    """Ruft optionale Provider- und grobe Standortdaten von ipinfo.io ab."""
    try:
        params = {"token": IPINFO_TOKEN} if IPINFO_TOKEN else None
        response = requests.get(f"https://ipinfo.io/{ip}/json", params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Antwort ist kein Objekt")
        as_data = data.get("as")
        isp = data.get("org")
        if not isp and isinstance(as_data, dict):
            isp = as_data.get("name")
        parts = [data.get(key) for key in ("city", "region", "country")]
        location = ", ".join(str(part) for part in parts if part)
        return str(isp) if isp else None, location or None
    except (requests.RequestException, ValueError, TypeError) as exc:
        logging.debug("IP-Info für %s nicht verfügbar: %s", ip, exc)
        return None, None


def get_ips():
    """Ermittelt IPv4 und IPv6 parallel; der Heartbeat ruft diese Funktion nie auf."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        ipv4_future = pool.submit(fetch_public_ip, False)
        ipv6_future = pool.submit(fetch_public_ip, True)
        ipv4, ipv4_warnings = ipv4_future.result()
        ipv6, ipv6_warnings = ipv6_future.result()
    if ipv4:
        logging.info("Aktuelle IPs: IPv4=%s, IPv6=%s", ipv4, ipv6 or "N/A")
    else:
        logging.error("Keine öffentliche IPv4-Adresse abrufbar.")
    return ipv4, ipv6, ipv4_warnings + ipv6_warnings


def _validate_history_entry(raw, index):
    if not isinstance(raw, dict):
        raise HistoryError(f"Eintrag {index} ist kein JSON-Objekt.")
    timestamp = raw.get("timestamp")
    if _parse_ts(timestamp) is None:
        raise HistoryError(f"Eintrag {index} hat keinen gültigen Zeitstempel.")
    ipv4 = _parse_ip(raw.get("ipv4"))
    if not isinstance(ipv4, ipaddress.IPv4Address):
        raise HistoryError(f"Eintrag {index} hat keine gültige IPv4-Adresse.")
    ipv6_raw = _norm_ipv6(raw.get("ipv6"))
    ipv6 = _parse_ip(ipv6_raw) if ipv6_raw else None
    if ipv6_raw and not isinstance(ipv6, ipaddress.IPv6Address):
        raise HistoryError(f"Eintrag {index} hat keine gültige IPv6-Adresse.")
    reasons = raw.get("warning_reasons")
    if reasons is None:
        reasons = raw.get("cgnat_reasons", []) if raw.get("cgnat") else []
        if raw.get("cgnat") and not reasons:
            reasons = ["Ältere CGNAT-Markierung aus der Historie."]
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        raise HistoryError(f"Eintrag {index} hat ungültige Warnungsgründe.")
    for field in ("isp", "location"):
        if raw.get(field) is not None and not isinstance(raw[field], str):
            raise HistoryError(f"Eintrag {index} hat ein ungültiges Feld {field}.")
    return {
        "timestamp": timestamp,
        "ipv4": str(ipv4),
        "ipv6": str(ipv6) if ipv6 else None,
        "warning_reasons": reasons,
        "isp": raw.get("isp"),
        "location": raw.get("location"),
    }


def load_history():
    if not os.path.exists(IP_FILE):
        return []
    try:
        with open(IP_FILE, encoding="utf-8") as file_handle:
            raw = json.load(file_handle)
    except (json.JSONDecodeError, OSError) as exc:
        logging.exception("Konnte IP-Historie nicht laden.")
        raise HistoryError("Die IP-Historie konnte nicht sicher gelesen werden.") from exc
    if not isinstance(raw, list):
        raise HistoryError("Die IP-Historie muss eine JSON-Liste sein.")
    return [_validate_history_entry(entry, index) for index, entry in enumerate(raw)]


def save_history(data):
    directory = os.path.dirname(IP_FILE) or "."
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".json.tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as file_handle:
            json.dump(data, file_handle, ensure_ascii=False, indent=2)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        os.replace(tmp_path, IP_FILE)
        logging.info("IP-Historie gespeichert.")
    except (OSError, TypeError) as exc:
        logging.exception("Konnte IP-Historie nicht speichern.")
        if tmp_path:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
        raise HistoryError("Die IP-Historie konnte nicht sicher gespeichert werden.") from exc


def write_heartbeat():
    os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as file_handle:
        file_handle.write(datetime.now(timezone.utc).isoformat())


async def _heartbeat_loop(application):
    del application
    while True:
        try:
            write_heartbeat()
        except OSError:
            logging.exception("Heartbeat nicht schreibbar.")
        await asyncio.sleep(HEARTBEAT_INTERVAL)


def build_ip_message(ipv4, ipv6, at, *, changed, warning_reasons=(), isp=None, location=None):
    status = "geändert" if changed else "erfasst"
    ipv6_display = "❌ N/A" if not ipv6 else escape(str(ipv6))
    text = f"🌐 IP-Adresse {status}:\n\n"
    text += f"🌐 IPv4: <code>{escape(str(ipv4))}</code>\n"
    text += f"🌍 IPv6: <code>{ipv6_display}</code>\n"
    if isp:
        text += f"📡 {escape(str(isp))}\n"
    if location:
        text += f"📍 {escape(str(location))}\n"
    if warning_reasons:
        text += "\n⚠️ <b>IP-Plausibilitätswarnung</b>\n"
        for reason in warning_reasons:
            text += f"   • {escape(str(reason))}\n"
    text += f"\n📅 {at.strftime('%d.%m.%Y %H:%M:%S')}"
    return text


def entry_message(entry, changed):
    at = _parse_ts(entry.get("timestamp")) or datetime.now(timezone.utc)
    return build_ip_message(
        entry.get("ipv4", "?"),
        _norm_ipv6(entry.get("ipv6")),
        at,
        changed=changed,
        warning_reasons=entry.get("warning_reasons", ()),
        isp=entry.get("isp"),
        location=entry.get("location"),
    )


def check_and_record(ipv4, ipv6, warning_reasons):
    history = load_history()
    changed = not history or history[-1]["ipv4"] != ipv4 or history[-1]["ipv6"] != ipv6
    if not changed:
        return False, None
    isp, location = fetch_ip_info(ipv4)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ipv4": ipv4,
        "ipv6": ipv6,
        "warning_reasons": list(warning_reasons),
        "isp": isp,
        "location": location,
    }
    history.append(entry)
    save_history(history[-HISTORY_LIMIT:])
    return True, entry


async def perform_check():
    """Serialisiert Abfrage und Speichern und aktualisiert den Betriebsstatus."""
    global LAST_ATTEMPT_AT, LAST_SUCCESS_AT, LAST_ERROR
    async with CHECK_LOCK:
        LAST_ATTEMPT_AT = datetime.now(timezone.utc)
        try:
            ipv4, ipv6, warnings = await asyncio.to_thread(get_ips)
            if not ipv4:
                raise CheckError("Keine öffentliche IPv4-Adresse verfügbar.")
            changed, entry = await asyncio.to_thread(check_and_record, ipv4, ipv6, warnings)
        except Exception as exc:
            LAST_ERROR = (
                "Die IP-Historie konnte nicht verarbeitet werden."
                if isinstance(exc, HistoryError)
                else "Die IP-Prüfung ist fehlgeschlagen."
            )
            raise
        LAST_SUCCESS_AT = datetime.now(timezone.utc)
        LAST_ERROR = None
        return ipv4, ipv6, warnings, changed, entry


async def send_to_chats(application, text):
    for chat_id in CHAT_IDS:
        try:
            await application.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception:
            logging.exception("Telegram-Nachricht an Chat %s fehlgeschlagen.", chat_id)


async def check_initial_ip(application):
    try:
        _ipv4, _ipv6, _warnings, changed, entry = await perform_check()
        if changed and entry:
            await send_to_chats(application, entry_message(entry, changed=True))
        else:
            logging.info("Keine Änderung der IP-Adresse festgestellt.")
    except Exception:
        logging.exception("Fehler im Initial-Check.")
        await send_to_chats(application, "❌ Der IP-Check beim Start ist fehlgeschlagen.")


async def post_init(application):
    application.bot_data["heartbeat_task"] = asyncio.create_task(_heartbeat_loop(application))
    write_heartbeat()
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Verfügbare Befehle anzeigen"),
            BotCommand("help", "Verfügbare Befehle anzeigen"),
            BotCommand("ip", "Letzte bekannte IP anzeigen"),
            BotCommand("check", "IP-Adressen jetzt prüfen"),
            BotCommand("history", "Letzte Änderungen anzeigen"),
            BotCommand("stats", "Statistik anzeigen"),
            BotCommand("status", "Bot-Status anzeigen"),
        ]
    )
    await check_initial_ip(application)


async def post_shutdown(application):
    task = application.bot_data.get("heartbeat_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    logging.info("Bot heruntergefahren.")


def authorized(func):
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
    del context
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
    del context
    try:
        history = load_history()
    except HistoryError:
        await update.message.reply_text("❌ Die IP-Historie kann derzeit nicht gelesen werden.")
        return
    if not history:
        await update.message.reply_text("Keine IP-Daten gefunden.")
        return
    await update.message.reply_text(entry_message(history[-1], changed=False), parse_mode="HTML")


@authorized
async def handle_check(update, context):
    del context
    await update.message.reply_text("🔍 Prüfe aktuelle IP-Adressen...")
    try:
        ipv4, ipv6, warnings, changed, entry = await perform_check()
    except HistoryError:
        logging.exception("History-Fehler beim manuellen Check.")
        await update.message.reply_text(
            "❌ Die IP-Historie konnte nicht sicher gespeichert werden."
        )
        return
    except Exception:
        logging.exception("Fehler beim manuellen IP-Check.")
        await update.message.reply_text("❌ Die IP-Adressen konnten nicht abgerufen werden.")
        return
    text = (
        entry_message(entry, changed=True)
        if changed and entry
        else build_ip_message(
            ipv4,
            ipv6,
            datetime.now(timezone.utc),
            changed=False,
            warning_reasons=warnings,
        )
    )
    await update.message.reply_text(text, parse_mode="HTML")


@authorized
async def handle_history(update, context):
    del context
    try:
        history = load_history()
    except HistoryError:
        await update.message.reply_text("❌ Die IP-Historie kann derzeit nicht gelesen werden.")
        return
    if not history:
        await update.message.reply_text("Keine IP-Historie gefunden.")
        return
    message = "<b>IP-Historie</b> (letzte 5):\n\n"
    for index, entry in enumerate(reversed(history[-5:]), 1):
        ipv6 = entry.get("ipv6") or "❌ N/A"
        changed_at = _parse_ts(entry.get("timestamp")) or datetime.now(timezone.utc)
        badge = " ⚠️" if entry.get("warning_reasons") else ""
        message += (
            f"<b>{index}.</b> <code>{changed_at.strftime('%d.%m.%Y %H:%M')}</code>" f"{badge}\n"
        )
        message += f"   🌐 <code>{escape(entry.get('ipv4', 'N/A'))}</code>\n"
        message += f"   🌍 <code>{escape(ipv6)}</code>\n"
        if entry.get("isp"):
            message += f"   📡 {escape(entry['isp'])}\n"
        message += "\n"
    await update.message.reply_text(message, parse_mode="HTML")


def format_duration(td):
    total = max(0, int(td.total_seconds()))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_status_time(value):
    return value.strftime("%d.%m.%Y %H:%M:%S UTC") if value else "—"


@authorized
async def handle_status(update, context):
    del context
    try:
        history = load_history()
        history_line = f"{len(history)} Einträge (Limit: {HISTORY_LIMIT})"
    except HistoryError:
        history = []
        history_line = "nicht lesbar"
    lines = [
        "🤖 <b>Bot-Status</b>",
        f"⏱️ Uptime: {format_duration(datetime.now(timezone.utc) - STARTED_AT)}",
        f"🔍 Letzter Versuch: {_format_status_time(LAST_ATTEMPT_AT)}",
        f"✅ Letzter Erfolg: {_format_status_time(LAST_SUCCESS_AT)}",
        f"❌ Letzter Fehler: {escape(LAST_ERROR) if LAST_ERROR else '—'}",
        f"🔗 Modus: {'Webhook' if WEBHOOK_URL else 'Polling'}",
        f"📊 Historie: {history_line}",
    ]
    if history:
        lines.append(f"🌐 Aktuelles IPv4: <code>{escape(history[-1]['ipv4'])}</code>")
        if history[-1].get("warning_reasons"):
            lines.append("⚠️ IP-Plausibilitätswarnung vorhanden")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@authorized
async def handle_stats(update, context):
    del context
    try:
        history = load_history()
    except HistoryError:
        await update.message.reply_text("❌ Die IP-Historie kann derzeit nicht gelesen werden.")
        return
    if not history:
        await update.message.reply_text("Keine IP-Historie vorhanden.")
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    old = datetime.min.replace(tzinfo=timezone.utc)
    recent_count = sum((_parse_ts(item["timestamp"]) or old) >= cutoff for item in history)
    ipv6_count = sum(bool(item.get("ipv6")) for item in history)
    warning_count = sum(bool(item.get("warning_reasons")) for item in history)
    total = len(history)
    message = (
        "📊 <b>IP-Statistik</b>\n\n"
        f"🗂️ Einträge gesamt: {total}\n"
        f"🔁 Änderungen (letzte 30 Tage): {recent_count}\n"
        f"🌍 IPv6 verfügbar: {ipv6_count}/{total} ({round(100 * ipv6_count / total)}%)\n"
        f"⚠️ Plausibilitätswarnungen: {warning_count}/{total} "
        f"({round(100 * warning_count / total)}%)\n"
    )
    await update.message.reply_text(message, parse_mode="HTML")


async def error_handler(update, context):
    del update
    logging.error("Exception while handling an update", exc_info=context.error)


def validate_webhook_config(url, cert, key, port):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("TELEGRAM_WEBHOOK_URL muss eine vollständige HTTPS-URL mit Host sein.")
    if bool(cert) != bool(key):
        raise ValueError("TELEGRAM_CERT_FILE und TELEGRAM_KEY_FILE müssen gemeinsam gesetzt sein.")
    if cert and port not in SUPPORTED_WEBHOOK_PORTS:
        raise ValueError(
            "Bei direktem TLS muss TELEGRAM_WEBHOOK_PORT einer dieser Ports sein: "
            f"{SUPPORTED_WEBHOOK_PORTS}"
        )
    if not cert and not 1 <= port <= 65535:
        raise ValueError("TELEGRAM_WEBHOOK_PORT muss zwischen 1 und 65535 liegen.")
    return parsed.path.strip("/") or "telegram", bool(cert)


def run_bot():
    app = (
        Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    )
    for command, handler in (
        ("start", handle_start),
        ("help", handle_start),
        ("ip", handle_ip),
        ("check", handle_check),
        ("history", handle_history),
        ("stats", handle_stats),
        ("status", handle_status),
    ):
        app.add_handler(CommandHandler(command, handler))
    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        cert = os.getenv("TELEGRAM_CERT_FILE")
        key = os.getenv("TELEGRAM_KEY_FILE")
        try:
            webhook_path, direct_tls = validate_webhook_config(WEBHOOK_URL, cert, key, WEBHOOK_PORT)
        except ValueError as exc:
            logging.error("%s", exc)
            sys.exit(1)
        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
        if not secret:
            logging.warning("TELEGRAM_WEBHOOK_SECRET ist nicht gesetzt.")
        logging.info(
            "Starte im Webhook-Modus (%s).", "direktes TLS" if direct_tls else "Reverse-Proxy"
        )
        app.run_webhook(
            listen=WEBHOOK_LISTEN,
            port=WEBHOOK_PORT,
            url_path=webhook_path,
            cert=cert,
            key=key,
            webhook_url=WEBHOOK_URL,
            drop_pending_updates=True,
            secret_token=secret,
        )
    else:
        logging.info("Starte im Polling-Modus.")
        app.run_polling()


if __name__ == "__main__":
    try:
        CHAT_IDS = _parse_chat_ids(os.getenv("TELEGRAM_CHAT_ID"))
    except ValueError:
        logging.error("TELEGRAM_CHAT_ID ist ungültig (ganze Zahlen, getrennt durch Komma).")
        sys.exit(1)
    if not TOKEN or not CHAT_IDS:
        logging.error("TELEGRAM_TOKEN und TELEGRAM_CHAT_ID müssen gesetzt sein!")
        sys.exit(1)
    logging.info("Bot gestartet. Befehle: /start /help /ip /check /history /stats /status")
    run_bot()
