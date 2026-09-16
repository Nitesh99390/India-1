# Novel Translator Telegram Bot (Master + Worker)

Telegram bot that translates `.txt` novel files to Hindi (or any language) using a
two-process architecture backed by MongoDB.

```
User ──.txt──▶ master.py ──job──▶ MongoDB (novel_db.jobs) ──▶ worker.py ──translated .txt──▶ User
```

- **master.py** – Telegram bot (polling). Handles access control, queues jobs, shows status.
- **worker.py** – Pulls jobs from MongoDB, downloads the file, translates it in
  paragraph-aware chunks with retries, sends live progress, uploads the result.

Both processes start a tiny HTTP server on `$PORT` so they can run as free Render web services.

## Features

| User | Owner |
|---|---|
| `/start`, `/help` | `/approve <id> [days]`, `/reject <id>`, `/revoke <id>` |
| `/request` – ask for access | `/users`, `/pending` |
| `/status` – last 5 jobs, queue position, % progress | `/stats` – queue & user stats |
| `/cancel` – cancel latest queued job | `/broadcast <msg>` |
| `/me` – subscription info | `/clearfailed` |

Worker extras: encoding auto-detect (utf-8/utf-16/gbk/…), exponential-backoff retries,
progress bar edits in Telegram, failure notification to user, stale-job recovery if a
worker crashes mid-job, per-user concurrent job limit.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in BOT_TOKEN, MONGO_URI, OWNER_ID
export $(cat .env | xargs)
python master.py   # terminal 1
python worker.py   # terminal 2
```

### Render

`render.yaml` defines both services. Set `BOT_TOKEN`, `MONGO_URI`, `OWNER_ID` in the dashboard.

## Environment variables

See `.env.example`. Only `BOT_TOKEN` and `MONGO_URI` are required.

## MongoDB collections

- `novel_db.users` – `{_id: chat_id, status: pending|approved|rejected|revoked|expired, expires_at, name}`
- `novel_db.jobs` – `{chat_id, file_path, file_name, status: queued|running|done|failed|cancelled, progress, timestamp, started_at, finished_at, error}`
