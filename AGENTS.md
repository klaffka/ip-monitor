# AGENTS.md

## Layout

Single-file app: `ip_monitor.py` is the entire codebase (Telegram bot + public IP monitor). No package structure, no tests, no codegen.

## Commands

- Dev run: `pip install -r requirements.txt && python ip_monitor.py`
  - Requires `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` env vars; exits 1 without them.
  - `TELEGRAM_CHAT_ID` may hold several IDs comma-separated (whitelist of allowed chats).
  - Optional: `TELEGRAM_WEBHOOK_URL` (+ `TELEGRAM_CERT_FILE`/`TELEGRAM_KEY_FILE`) switches from polling to webhook mode.
  - The code uses plain `os.getenv` (no dotenv) — export vars manually or pass them inline.
- Pre-push checks (mirror of `.github/workflows/ci.yaml`, run in this order):
  1. `black --check .`
  2. `flake8 ip_monitor.py --max-line-length 100`
  3. `bandit -r ip_monitor.py`
  - Black config lives in `pyproject.toml` (line-length 100, py311). Trivy runs in CI only (needs Docker); skip locally.

## Gotchas

- There is **no periodic IP re-checking** and no `CHECK_INTERVAL` setting — the script checks the IP once at startup, and only via `/check` afterwards. Don't "fix" this or reintroduce the setting.
- The `_heartbeat_loop` writes a liveness file (`data/heartbeat`) on a timer. That is **not** an IP check — it exists only for the Docker healthcheck. Do not remove it and do not turn it into a periodic IP check.
- History is stored in `data/ip_history.json` relative to CWD (created on first save), capped by `HISTORY_LIMIT` (default 100). In Docker the WORKDIR is `/app`, so the volume must cover `/app/data`. The Dockerfile pre-creates `/app/data` owned by UID 1000 so the non-root `USER` can write to it.
- User-facing strings (logs, Telegram messages, README) are in German on purpose; keep code identifiers and commits in English.
- `.env` in the repo root contains placeholders only and is gitignored; never commit real tokens.

## Releases

Push to `main` triggers release-please (simple type) + Docker image build/push automatically. Use Conventional Commits (`feat:`, `fix:`, `chore:`, …) so versioning works; do not create releases or tags manually.