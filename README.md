# IP Watcher Bot

Ein Docker-fähiges Python-Tool zur Überwachung deiner öffentlichen IPv4- und IPv6-Adresse mit Benachrichtigungen über Telegram sowie automatisiertem Versioning und CI/CD.

## 🔧 Features

- Überwachung von IPv4 & IPv6
- Telegram-Benachrichtigung bei IP-Änderung
- Lokale Speicherung mit Zeitstempel
- Telegram-Bot mit `/ip`-Abfrage
- Docker-Container ready
- Automatisierte Releases via GitHub Actions
- Docker-Image Push nach Docker Hub (inkl. Version + latest)
- CI-Checks (Black, Flake8, Bandit, Trivy)
- Branch Protection Rules ready

## 🚀 Nutzung

### Docker starten

```bash
docker run -d   -e TELEGRAM_TOKEN=<your_token>   -e TELEGRAM_CHAT_ID=<your_chat_id>   -v $(pwd)/data:/app/data   your-dockerhub-user/ip-watcher:latest
```

### Manuell starten (für Entwicklung)

```bash
pip install -r requirements.txt
python ip_monitor.py
```

## 📦 Projektstruktur

```bash
.
├── ip_monitor.py
├── Dockerfile
├── pyproject.toml
├── .github/workflows/
│   ├── ci.yml
│   └── release.yml
```

## ✅ GitHub Actions

- `ci.yml`: Lint, Format, Bandit, Trivy (Filesystem)
- `release.yml`: Automatisierte Versionierung, Changelog, Docker-Build & Trivy (Image)

## 🛡 Schutzregeln (empfohlen)

- Require PR reviews for `main`
- Require status checks: `validate (ubuntu-latest)`

---

MIT License