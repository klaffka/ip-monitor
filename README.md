# IP Watcher Bot

Ein Docker-fähiges Python-Tool zur Überwachung deiner öffentlichen IPv4- und IPv6-Adresse mit Benachrichtigungen über Telegram sowie automatisiertem Versioning und CI/CD.

## 🔧 Features

- Überwachung von IPv4 & IPv6
- Telegram-Benachrichtigung bei IP-Änderung
- Lokale Speicherung mit Zeitstempel
- Telegram-Bot mit `/ip`-Abfrage

## 🚀 Nutzung

### Docker starten

```bash
docker run -d \
  -e TELEGRAM_TOKEN=<your_token> \
  -e TELEGRAM_CHAT_ID=<your_chat_id> \
  -v $(pwd)/data:/app/data \
  your-dockerhub-user/ip-watcher:latest
```

### Manuell starten (für Entwicklung)

```bash
pip install -r requirements.txt
python ip_monitor.py
```

## 🤖 Telegram-Bot erstellen

1. **Starte den BotFather in Telegram**  
   Suche nach `@BotFather` und starte den Chat.

2. **Erstelle einen neuen Bot**  
   Sende den Befehl:  
   ```
   /newbot
   ```
   Gib einen Namen und Benutzernamen für deinen Bot ein.  
   👉 Danach erhältst du einen **API-Token** (wird in `TELEGRAM_TOKEN` verwendet).

3. **Starte deinen Bot**  
   Suche deinen Bot in Telegram, schreibe ihm `/start`, um ihn zu aktivieren.

4. **Ermittle deine Chat-ID**

   - Schreibe deinem Bot z. B. `/ip`
   - Besuche diese URL im Browser (ersetze `<TOKEN>`):  
     ```
     https://api.telegram.org/bot<TOKEN>/getUpdates
     ```
   - In der Antwort findest du `chat.id` – das ist dein `TELEGRAM_CHAT_ID`

## 📦 Projektstruktur

```bash
.
├── ip_monitor.py              # Hauptlogik für IP-Überwachung und Telegram-Bot
├── Dockerfile                 # Docker-Image-Konfiguration
├── pyproject.toml             # Formatierungs- und Linter-Konfiguration
├── .github/
│   └── workflows/
│       ├── ci.yml             # Code-Checks (Black, Flake8, Bandit, Trivy)
│       └── release.yml        # Auto-Release & Docker-Build bei Push auf main
└── data/
    └── ip_history.json        # Historie der IP-Adressen
```

## 📄 Lizenz

Dieses Projekt steht unter der **GNU General Public License v3.0**.