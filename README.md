<p align="center">
  <img src="assets/logo.svg" width="160" alt="ip-watcher Logo">
</p>

# IP Watcher Bot

Ein Docker-fähiges Python-Tool zur Überwachung deiner öffentlichen IPv4- und IPv6-Adresse mit Benachrichtigungen über Telegram sowie automatisiertem Versioning und CI/CD.

## 🔧 Features

- Überwachung von IPv4 & IPv6
- Telegram-Benachrichtigung bei IP-Änderung (beim Start und bei manueller Prüfung)
- Lokale Speicherung der Historie mit Zeitstempel
- Telegram-Bot mit den Befehlen `/ip`, `/history` und `/check`
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
| `/ip` | Zeigt die letzte bekannte IP |
| `/history` | Zeigt die letzten 5 IP-Änderungen |
| `/check` | Prüft die aktuellen IPs sofort und meldet Änderungen |

Der Bot prüft die IP einmal beim Start und anschließend nur noch bei `/check` – es gibt kein periodisches Prüfen.

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