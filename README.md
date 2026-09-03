<p align="center">
  <img src="assets/logo.svg" width="160" alt="ip-watcher Logo">
</p>

# IP Watcher Bot

Ein Docker-fähiger Telegram-Bot, der die öffentliche IPv4- und IPv6-Adresse beim
Start sowie auf ausdrücklichen `/check`-Befehl prüft. Es gibt bewusst keine
periodische IP-Prüfung und keine `CHECK_INTERVAL`-Variable.

## Features

- Öffentliche IPv4- und IPv6-Ermittlung mit Fallback-Providern
- Strikte Adressvalidierung und Plausibilitätswarnung bei nicht global routbaren Antworten
- Optionaler Provider- und grober Standort-Lookup über ipinfo.io
- Telegram-Benachrichtigung bei Änderungen und eine begrenzte lokale JSON-Historie
- Befehle `/start`, `/help`, `/ip`, `/check`, `/history`, `/stats` und `/status`
- Chat-Whitelist, Polling sowie direkte und Reverse-Proxy-Webhooks
- Atomische History-Writes, serialisierte Checks und Docker-Liveness-Heartbeat
- CI mit Formatierung, Lint, Unit-Tests, Security-Scans und geprüftem Docker-Image

Eine Plausibilitätswarnung bedeutet nicht automatisch CGNAT. Sie zeigt an, dass ein
IP-Provider eine syntaktisch gültige, aber nicht global routbare Adresse geliefert
hat. Eine lokale Router-Adresse lässt sich über einen öffentlichen IP-Dienst nicht
zuverlässig bestimmen.

## Schnellstart

Kopiere zuerst die Vorlage und trage echte Zugangsdaten nur lokal ein:

```bash
cp .env.example .env
docker compose up -d --build
```

Compose speichert Historie und Heartbeat im Named Volume `ip-data` unter `/app/data`.
Das Root-Dateisystem des Containers ist schreibgeschützt; der Prozess läuft als
Benutzer und Gruppe `1000:1000` ohne Linux-Capabilities.

Alternativ direkt mit Docker:

```bash
docker run -d \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --user 1000:1000 \
  -e TELEGRAM_TOKEN=<your_token> \
  -e TELEGRAM_CHAT_ID=<your_chat_id> \
  -v "$(pwd)/data:/app/data" \
  your-dockerhub-user/ip-watcher:latest
```

Für die lokale Entwicklung:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_TOKEN=<your_token>
export TELEGRAM_CHAT_ID=<your_chat_id>
python ip_monitor.py
```

## Bot-Befehle

| Befehl | Beschreibung |
| --- | --- |
| `/start`, `/help` | Zeigt die verfügbaren Befehle |
| `/ip` | Zeigt die letzte bekannte IP |
| `/check` | Prüft IPv4 und IPv6 sofort und meldet Änderungen |
| `/history` | Zeigt die letzten fünf IP-Änderungen |
| `/stats` | Zeigt Änderungen, IPv6-Quote und Plausibilitätswarnungen |
| `/status` | Zeigt Uptime, letzten Versuch, letzten Erfolg und Fehlerstatus |

Nur Chats aus `TELEGRAM_CHAT_ID` erhalten Antworten. Mehrere IDs werden durch Kommas
getrennt.

## Konfiguration

| Variable | Pflicht | Beschreibung |
| --- | --- | --- |
| `TELEGRAM_TOKEN` | ja | Bot-API-Token |
| `TELEGRAM_CHAT_ID` | ja | Eine oder mehrere erlaubte Chat-IDs, kommasepariert |
| `IPINFO_TOKEN` | nein | Token für zuverlässigere ipinfo.io-Abfragen |
| `HISTORY_LIMIT` | nein | Anzahl gespeicherter Änderungen, mindestens `1`, Standard `100` |
| `TELEGRAM_WEBHOOK_URL` | nein | Öffentliche HTTPS-URL; aktiviert Webhook statt Polling |
| `TELEGRAM_WEBHOOK_SECRET` | nein | Empfohlenes Telegram-Webhook-Secret |
| `TELEGRAM_WEBHOOK_PORT` | nein | Lokaler Port, Standard `8443` |
| `TELEGRAM_WEBHOOK_LISTEN` | nein | Listen-Adresse, Standard `0.0.0.0` |
| `TELEGRAM_CERT_FILE` | nein | PEM-Zertifikat für direkte TLS-Terminierung |
| `TELEGRAM_KEY_FILE` | nein | Passender PEM-Schlüssel für direkte TLS-Terminierung |

Die Anwendung liest Umgebungsvariablen direkt über `os.getenv`; sie lädt selbst keine
`.env`-Datei. Docker Compose übernimmt dies automatisch.

### Webhooks

`TELEGRAM_WEBHOOK_URL` muss eine vollständige öffentliche HTTPS-URL mit Host sein.
Zertifikat und Schlüssel werden entweder gemeinsam oder gar nicht gesetzt:

- Hinter einem TLS-terminierenden Reverse Proxy bleiben `TELEGRAM_CERT_FILE` und
  `TELEGRAM_KEY_FILE` leer. Der Bot lauscht lokal per HTTP auf jedem gültigen Port
  zwischen `1` und `65535`.
- Bei direkter TLS-Terminierung werden beide Dateien gesetzt. Dann akzeptiert der Bot
  nur die von Telegram unterstützten Ports `80`, `88`, `443` und `8443`.

Ein `TELEGRAM_WEBHOOK_SECRET` ist aus Kompatibilitätsgründen optional, wird aber
dringend empfohlen. Die vollständige Webhook-URL erscheint nicht in `/status`.

## Daten und Datenschutz

`data/ip_history.json` enthält Zeitstempel, öffentliche IPs sowie optional ISP und
groben Ort. Koordinaten von ipinfo.io werden weder angezeigt noch gespeichert. Die
Daten bleiben lokal im konfigurierten Volume; die Abfragen gehen jedoch an die
IP-Provider und optional an ipinfo.io. Beschädigte oder schemawidrige Historien werden
nicht überschrieben. Alte Einträge mit `cgnat`/`cgnat_reasons` werden beim Lesen in
das Feld `warning_reasons` migriert.

## Entwicklung und Prüfungen

```bash
black --check .
flake8 ip_monitor.py --max-line-length 100
bandit -r ip_monitor.py
python -m unittest -v
docker build -t ip-monitor:test .
```

Trivy läuft blockierend in CI für Repository und Image. GitHub Actions sind auf
immutable Commit-SHAs gepinnt und werden über Dependabot gepflegt. Ein Push auf
`main` kann erst nach erfolgreichen Prüfungen und erfolgreichem Image-Scan durch
release-please veröffentlicht werden; Conventional Commits steuern die Versionierung.

## Projektstruktur

```text
ip_monitor.py          Anwendung
tests/                 isolierte Standardbibliothek-Tests
Dockerfile             Container-Image
docker-compose.yaml    gehärteter Compose-Betrieb
.env.example           getrackte Konfigurationsvorlage
.github/workflows/     CI und automatisierte Releases
```

## Lizenz

GNU General Public License v3.0
