<p align="center">
  <img src="assets/logo.svg" width="160" alt="ip-watcher Logo">
</p>

# IP Watcher Bot

Ein Docker-fähiges Python-Tool zur Überwachung deiner öffentlichen IPv4- und IPv6-Adresse mit Benachrichtigungen über Telegram sowie automatisiertem Versioning und CI/CD.

## 🔧 Features

- Überwachung von IPv4 & IPv6
- Carrier-Grade NAT (CGNAT) / ULA-Erkennung
- Provider- und Standort-Lookup (ipinfo.io)
- Fallback-IP-Provider bei Ausfall des primären Diensts
- Telegram-Benachrichtigung bei IP-Änderung (beim Start und bei manueller Prüfung)
- Lokale Speicherung der Historie mit Zeitstempel (begrenzt, default 100 Einträge)
- Telegram-Bot mit den Befehlen `/start`, `/ip`, `/check`, `/history`, `/stats` und `/status`
- Nur autorisierte Chats dürfen Befehle senden (Whitelist)
- Optionaler Webhook-Modus als Alternative zu Polling
- CI/CD: Black, Flake8, Bandit, Trivy sowie Auto-Release (release-please) und Docker-Build bei Push auf `main`

## 🚀 Nutzung

### Docker starten

```bash
docker run -d \
  -e TELEGRAM_TOKEN=<your_token> \
  -e TELEGRAM_CHAT_ID=<your_chat_id> \
  -v $(pwd)/data:/app/data \
  your-dockerhub-user/ip-watcher:latest
```

### Docker Compose starten

```bash
docker compose up -d --build
```

Die Historie wird im Named Volume `ip-data` unter `/app/data` gespeichert.

### Manuell starten (für Entwicklung)

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=<your_token>
export TELEGRAM_CHAT_ID=<your_chat_id>
python ip_monitor.py
```

## 🤖 Bot-Befehle

| Befehl | Beschreibung |
| --- | --- |
| `/start` | Zeigt die verfügbaren Befehle |
| `/ip` | Zeigt die letzte bekannte IP |
| `/check` | Prüft die aktuellen IPs sofort und meldet Änderungen |
| `/history` | Zeigt die letzten 5 IP-Änderungen |
| `/stats` | Zeigt Statistiken (Änderungen, IPv6-, CGNAT-Quote) |
| `/status` | Zeigt Uptime, letzten Check und Betriebsmodus |

Der Bot prüft die IP einmal beim Start und anschließend nur noch bei `/check` – es gibt kein periodisches Prüfen.

Nur Chats, die in `TELEGRAM_CHAT_ID` aufgeführt sind, dürfen Befehle senden.

## ⚙️ Konfiguration (Umgebungsvariablen)

| Variable | Pflicht | Beschreibung |
| --- | --- | --- |
| `TELEGRAM_TOKEN` | ja | Bot-API-Token |
| `TELEGRAM_CHAT_ID` | ja | Chat-ID(s) für Nachrichten & Befehle, mehrere durch Komma getrennt (z. B. `123456789,-1001234567890`) |
| `TELEGRAM_WEBHOOK_URL` | nein | Vollständige öffentliche Webhook-URL (HTTPS), aktiviert den Webhook-Modus |
| `TELEGRAM_CERT_FILE` | bei Webhook | Pfad zum SSL-Zertifikat (PEM) |
| `TELEGRAM_KEY_FILE` | bei Webhook | Pfad zum SSL-Privatschlüssel (PEM) |
| `TELEGRAM_WEBHOOK_SECRET` | nein | Secret-Token, den Telegram im Header `X-Telegram-Bot-Api-Secret-Token` senden muss |
| `TELEGRAM_WEBHOOK_PORT` | nein | Interner Port für Webhooks (Default `8443`, erlaubt: `80`, `443`, `88`, `8443`) |
| `TELEGRAM_WEBHOOK_LISTEN` | nein | Adresse, auf der der Webhook lauscht (Default `0.0.0.0`) |
| `HISTORY_LIMIT` | nein | Maximale Anzahl gespeicherter Historie-Einträge (Default `100`) |

### Webhook-Modus

Standardmäßig läuft der Bot im Polling-Modus. Für einen dauerhaft erreichbaren Bot ohne Polling kann der Webhook-Modus aktiviert werden:

1. Der Bot braucht eine **öffentliche HTTPS-URL** mit einem **gültigen Zertifikat** (z. B. Let's Encrypt).
2. Setze `TELEGRAM_WEBHOOK_URL` (z. B. `https://ip-monitor.example.com/telegram`), `TELEGRAM_CERT_FILE` und `TELEGRAM_KEY_FILE`.
3. Das Zertifikat und der Schlüssel müssen im Container unter den angegebenen Pfaden liegen (z. B. als Volume mounten).
4. Der interne Port ist `8443` (im Compose-File bereits freigegeben).

Der Webhook-Modus setzt den Webhook bei Telegram und empfängt Updates direkt, ohne zu pollen. Für den Betrieb hinter einer eigenen TLS-Terminierung (z. B. Reverse-Proxy) kann `TELEGRAM_CERT_FILE`/`TELEGRAM_KEY_FILE` weggelassen und die TLS-Endung extern gemacht werden – der Bot lauscht dann auf `http://0.0.0.0:8443`.

## 📲 Telegram-Bot erstellen

1. **Starte den BotFather in Telegram**  
   Suche nach `@BotFather` und starte den Chat.

2. **Erstelle einen neuen Bot**  
   Sende den Befehl:  
   ```
   /newbot
   ```
   Gib einen Namen und Benutzernamen für deinen Bot an.  
   👉 Danach erhältst du einen **API-Token** (wird in `TELEGRAM_TOKEN` verwendet).

3. **Starte deinen Bot**  
   Suche deinen Bot in Telegram, schreibe ihm `/start`, um ihn zu aktivieren.

4. **Ermittle deine Chat-ID**
   - Schreibe deinem Bot z. B. `/ip`
   - Besuche diese URL im Browser (ersetze `<TOKEN>`):
     ```
     https://api.telegram.org/bot<TOKEN>/getUpdates
     ```
   - In der Antwort findest du `chat.id` – das ist dein `TELEGRAM_CHAT_ID`

## 📦 Projektstruktur

```bash
.
├── ip_monitor.py              # Hauptlogik für IP-Überwachung und Telegram-Bot
├── assets/
│   └── logo.svg               # Logo
├── Dockerfile                 # Docker-Image-Konfiguration
├── docker-compose.yaml        # Compose-Setup mit Named Volume für die Historie
├── pyproject.toml             # Formatierungs-Konfiguration (Black)
├── requirements.txt           # Python-Abhängigkeiten
├── AGENTS.md                  # Hinweise für KI-Coding-Assistenten
├── .github/
│   └── workflows/
│       ├── ci.yaml            # Code-Checks (Black, Flake8, Bandit, Trivy)
│       └── release.yaml       # Auto-Release & Docker-Build bei Push auf main
└── data/
    └── ip_history.json        # Historie der IP-Adressen (wird bei Bedarf erstellt)
```

## 📄 Lizenz

Dieses Projekt steht unter der **GNU General Public License v3.0**.