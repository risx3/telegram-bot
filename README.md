# Telegram Deposit Bot

A Telegram bot that lets users confirm UPI payments by submitting their transaction ID and phone number. The backend automatically matches the submission against bank credit SMS messages forwarded via webhook, then records the confirmed transaction in PostgreSQL for the portal to display.

## How it works

```text
Android phone (SMS Forwarder app)
        │  HTTP POST (raw SMS body)
        ▼
/webhook/sms  ← FastAPI endpoint
        │
        ▼
Redis Queue  (buffer SMS events)
        │
        ▼
SMS Worker  (parse txn ID + amount from bank SMS)
        │
        ▼
PostgreSQL — transactions table  (phone = NULL until user confirms)
        ▲
        │  lookup by txn_id → update phone + confirmed = TRUE
Telegram Bot
        ▲
        │
User (submits UTR + phone number)
```

## Bot flow

1. User sends `/start` → bot requests phone number via contact share button
2. User taps **Share my phone number** → phone stored for the session
3. User taps **Confirm Payment** → bot sends the UPI QR code
4. User pays via any UPI app, then submits their **Transaction ID (UTR)**
5. Bot looks up the UTR in `transactions` (populated by the SMS worker)
6. If found → records phone, marks `confirmed = TRUE` → replies with confirmation
7. Portal reads the `transactions` table

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) — or pip
- Docker & Docker Compose
- An Android phone with an SMS Forwarder app
- A Telegram bot token
- A UPI QR code image for your receiving account

### Install uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Homebrew
brew install uv
```

## Getting Your Telegram Credentials

### Bot Token — via BotFather

1. Open Telegram → search [@BotFather](https://t.me/BotFather)
2. Send `/newbot` → follow the prompts
3. Copy the token into `TELEGRAM_BOT_TOKEN` in your `.env`

### Admin Telegram ID — via userinfobot

1. Open Telegram → search [@userinfobot](https://t.me/userinfobot)
2. Send `/start` → copy the `Id` value into `ADMIN_TELEGRAM_ID` in your `.env`

## Android SMS Forwarder Setup

Install **SMS Forwarder** from the Play Store on the phone that receives bank SMS alerts.

**Forwarding rule:**

| Field | Value |
| ----- | ----- |
| URL | `https://yourdomain.com/webhook/sms` |
| Method | POST |
| Header | `X-SMS-Secret: your_secret_value` |
| Body template | `{"body": "%body%", "sender": "%from%"}` |
| Sender filter | `HDFCBK`, `ICICIB`, `SBIINB`, `AXISBK`, `KOTAKB` |

**Heartbeat rule** (keeps watchdog silent):

| Field | Value |
| ----- | ----- |
| URL | `https://yourdomain.com/webhook/heartbeat` |
| Interval | Every 5 minutes |
| Header | same `X-SMS-Secret` |

## Local Development

There are two modes — pick one:

| Mode | When to use |
| ---- | ----------- |
| **A — Full Docker** | Simplest. Everything in containers. |
| **B — Hybrid (uv + Docker)** | Bot runs on host (fast restarts), Postgres/Redis in Docker. |

### 1. Clone and configure

```bash
git clone https://github.com/youruser/telegram-bot.git
cd telegram-bot
cp .env.example .env
```

Fill in `.env`:

| Variable | Notes |
| -------- | ----- |
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `ADMIN_TELEGRAM_ID` | From @userinfobot |
| `POSTGRES_DB` | e.g. `depositbot` |
| `POSTGRES_USER` | e.g. `botuser` |
| `POSTGRES_PASSWORD` | `openssl rand -hex 16` |
| `DATABASE_URL` | See Mode A/B below |
| `REDIS_URL` | See Mode A/B below |
| `SMS_WEBHOOK_SECRET` | `openssl rand -hex 16` |

**Mode A** — bot connects to containers by service name:

```env
DATABASE_URL=postgresql://botuser:YOUR_PASSWORD@postgres:5432/depositbot
REDIS_URL=redis://redis:6379/0
```

**Mode B** — bot runs on host, connects to Docker on localhost:

```env
DATABASE_URL=postgresql://botuser:YOUR_PASSWORD@localhost:5432/depositbot
REDIS_URL=redis://localhost:6379/0
```

### 2. Place your UPI QR code

```bash
cp /path/to/your/qr.png assets/qr_code.png
```

### 3A — Full Docker

```bash
docker compose up -d --build
docker compose logs -f bot
```

### 3B — Hybrid

```bash
# Start only Postgres and Redis
docker compose up -d postgres redis

# Install dependencies and run bot on host
uv sync
uv run python -m bot.main
```

`docker-compose.override.yml` exposes Postgres on `localhost:5432` and Redis on `localhost:6379` automatically when running locally.

### 4. Expose webhook for local testing (ngrok)

```bash
brew install ngrok/ngrok/ngrok
ngrok http 8000
```

Use the printed URL (e.g. `https://abc123.ngrok-free.app/webhook/sms`) in the SMS Forwarder app.

## Production Deployment (EC2)

See [DEPLOY_EC2.md](DEPLOY_EC2.md) for the full step-by-step guide.

Do **not** copy `docker-compose.override.yml` to the server — it is for local dev only.

## Database

Single table — `transactions`:

| Column | Type | Notes |
| ------ | ---- | ----- |
| `txn_id` | VARCHAR(64) | Unique — from SMS parse |
| `phone` | VARCHAR(20) | NULL until user confirms |
| `amount` | DECIMAL(10,2) | From SMS parse |
| `bank` | VARCHAR(50) | Bank name from SMS |
| `sms_raw` | TEXT | Raw SMS body |
| `confirmed` | BOOLEAN | TRUE after user submits UTR + phone |
| `received_at` | TIMESTAMP | When SMS arrived |
| `updated_at` | TIMESTAMP | When user confirmed |

No pre-seeding required — rows are inserted automatically as SMS payments arrive.

### Useful queries

```sql
-- Recent transactions
SELECT txn_id, phone, amount, bank, confirmed, received_at
FROM transactions ORDER BY received_at DESC LIMIT 20;

-- Unconfirmed (waiting for user to submit UTR)
SELECT txn_id, amount, bank, received_at
FROM transactions WHERE confirmed = FALSE ORDER BY received_at DESC;

-- Confirmed today
SELECT txn_id, phone, amount FROM transactions
WHERE confirmed = TRUE AND updated_at >= NOW() - INTERVAL '1 day';
```

## Testing the SMS Flow

### Inject a fake SMS (no Android needed)

```bash
curl -X POST http://localhost:8000/webhook/sms \
  -H "Content-Type: application/json" \
  -H "X-SMS-Secret: your_secret_value" \
  -d '{"sender":"HDFCBK","body":"Rs.500.00 credited to your a/c XX1234 on 14-04-26. Info: UPI-Test. Ref No 123456789012. -HDFC Bank"}'
```

Expected: `{"status":"queued"}`

Then in Telegram: `/start` → share phone → tap **Confirm Payment** → submit `123456789012` → bot confirms.

### Run the smoke test

```bash
uv run python scripts/smoke_test.py
```

Checks: env vars, Telegram token, FastAPI `/health`, PostgreSQL table, Redis.

### Run unit tests

```bash
uv run pytest test_sms_parser.py -v
```

## Adding New Bank SMS Patterns

Open [bot/services/sms_parser.py](bot/services/sms_parser.py) and add a regex:

```python
# Example: Yes Bank
_YES_BANK_PATTERN = re.compile(
    r"INR\s+([\d,]+\.?\d*)\s+credited.*?Ref\s*No\s+([A-Z0-9]{10,22})",
    re.IGNORECASE | re.DOTALL,
)

_BANK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ...
    ("Yes Bank", _YES_BANK_PATTERN),
]
```

Add a test case to `test_sms_parser.py` and run `pytest`.

## Monitoring

### Watchdog alerts (every 5 minutes)

Sends a Telegram message to `ADMIN_TELEGRAM_ID` if the SMS forwarder heartbeat hasn't been received in 10+ minutes. Alerts once per outage with a 1-hour cooldown.

### Daily summary (09:00 server time)

```text
Daily Summary
━━━━━━━━━━━━━━━━━━
SMSes received (last 24h): 47
Transactions confirmed:    43
━━━━━━━━━━━━━━━━━━
```

### Log files

| File | Contents |
| ---- | -------- |
| `logs/sms_parse_errors.log` | Raw SMS bodies that could not be parsed |
| `docker compose logs bot` | All application-level logs |

## Security Notes

- **Never commit `.env`** — contains bot token, DB credentials, and webhook secret. Already in `.gitignore`.
- **Use a strong `SMS_WEBHOOK_SECRET`** (32+ chars) — the webhook returns HTTP 503 if this is unset.
- **Postgres and Redis** are not exposed publicly in `docker-compose.yml` — internal Docker network only.
- **Allowlist bank senders** on the Android forwarder app to prevent fake SMS injection.
- **HTTPS only in production** — the webhook transmits raw SMS data; always use TLS.

## Project Structure

```text
telegram-bot/
├── bot/
│   ├── main.py              # Entry point — bot + FastAPI + SMS worker on one event loop
│   ├── handlers/
│   │   ├── states.py        # FSM state constants
│   │   ├── common.py        # Shared utilities, keyboard layouts
│   │   ├── start.py         # /start handler
│   │   └── deposit.py       # Confirm Payment flow (UTR → phone → verify)
│   ├── services/
│   │   ├── sms_parser.py    # Bank SMS regex parser (HDFC, ICICI, SBI, Axis, Kotak)
│   │   ├── sms_worker.py    # Redis BLPOP consumer → DB insert
│   │   ├── verifier.py      # Txn lookup + phone confirmation logic
│   │   └── watchdog.py      # APScheduler heartbeat check + daily summary
│   ├── webhook/
│   │   └── sms_receiver.py  # FastAPI: POST /webhook/sms, POST /webhook/heartbeat
│   └── db/
│       ├── connection.py    # asyncpg pool lifecycle
│       └── queries.py       # All DB queries
├── scripts/
│   └── smoke_test.py        # Pre-flight health check
├── assets/
│   └── qr_code.png          # Place your UPI QR here (gitignored)
├── migrations/
│   └── 001_init.sql         # Initial schema (transactions table)
├── test_sms_parser.py       # pytest tests for SMS parser
├── .env.example
├── docker-compose.yml
├── docker-compose.override.yml  # Local dev only (gitignored)
└── Dockerfile
```
