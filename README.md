# Telegram Deposit Bot

A production-ready Telegram bot that lets registered users deposit money into a service by scanning a UPI QR code. After payment, users submit their UPI Transaction ID (UTR/Ref number). The backend automatically verifies this against bank credit SMS messages forwarded from an Android phone via webhook, then credits the user's balance in PostgreSQL.

## Architecture

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
PostgreSQL — received_transactions table
        ▲
        │  lookup by txn_id
Telegram Bot (python-telegram-bot v20, async)
        ▲
        │
Telegram User (submits /start, views profile, deposits, submits UTR)
```

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended package manager) — or pip
- Docker & Docker Compose (for production deployment)
- An Android phone with an SMS Forwarder app
- A Telegram bot token (see below)
- A UPI QR code image for your receiving account

### Install uv

💻 **Terminal**

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via Homebrew
brew install uv

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify:

```bash
uv --version
```

## Getting Your Telegram Credentials

### Bot Token — via BotFather

1. Open Telegram and search for [@BotFather](https://t.me/BotFather) (official, blue checkmark).
2. Send `/newbot`.
3. Enter a display name for your bot, e.g. `My Deposit Bot`.
4. Enter a username (must end in `bot`), e.g. `mydeposit_bot`.
5. BotFather replies with your token:

   ```text
   Done! Use this token to access the HTTP API:
   7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```

6. Copy the token and paste it as `TELEGRAM_BOT_TOKEN` in your `.env`.

> Keep this token secret — anyone with it can control your bot.

### Admin Telegram ID — via userinfobot

Your `ADMIN_TELEGRAM_ID` is your personal numeric Telegram user ID (not your username). The watchdog uses it to send alerts to you directly.

1. Open Telegram and search for [@userinfobot](https://t.me/userinfobot).
2. Send `/start`.
3. The bot replies with your info:

   ```text
   Id: 987654321
   First: Rishabh
   Username: @yourhandle
   ```

4. Copy the `Id` value and paste it as `ADMIN_TELEGRAM_ID` in your `.env`.

Alternatively, forward any message to [@JsonDumpBot](https://t.me/JsonDumpBot) — it shows the full JSON including `from.id`.

## Android SMS Forwarder Setup

The Android phone must forward incoming bank SMS messages to your webhook in real time.

**Recommended app:** [SMS to Webhook](https://play.google.com/store/apps/details?id=tech.appsverse.smstowebhook) or [SMS Forwarder](https://play.google.com/store/apps/details?id=com.kmz.smsforwarder)

**Configuration steps:**

1. Install the app on the Android phone that receives bank SMS alerts.
2. Add a forwarding rule with these settings:
   - **Sender filter:** `HDFCBK`, `ICICIB`, `SBIINB`, `AXISBK`, `KOTAKB`
     (Add all banks you expect SMS from — one per rule or comma-separated depending on the app.)
   - **Webhook URL:** `https://yourdomain.com/webhook/sms`
   - **HTTP method:** `POST`
   - **Custom header:** `X-SMS-Secret: your_secret_value`
   - **Body format (JSON):**

     ```json
     {"body": "$sms_body", "from": "$sms_from", "timestamp": "$timestamp"}
     ```

3. Configure the heartbeat (keep-alive):
   - **Heartbeat URL:** `https://yourdomain.com/webhook/heartbeat`
   - **Interval:** Every 5 minutes
   - **Header:** `X-SMS-Secret: your_secret_value`
4. Test by sending a mock SMS or triggering a small bank transaction.

## Local Development Setup

> **Legend used throughout this guide**
>
> - `💻 Terminal` — run on your local machine (host)
> - `🐳 Docker` — run inside a Docker container via `docker compose exec`
>
> **All commands must be run from the project root directory.**
> If you get `no configuration file provided: not found`, you are in the wrong directory.

There are two ways to run locally — pick one:

| Mode | When to use |
| ---- | ----------- |
| **A — Full Docker** | Simplest. Everything runs in containers. No Python install needed. |
| **B — Hybrid (uv + Docker)** | Best for development. Bot runs on your machine (fast restarts), Postgres/Redis in Docker. |

---

### 1. Clone the repo

💻 **Terminal**

```bash
git clone https://github.com/youruser/telegram-deposit-bot.git
cd telegram-deposit-bot
```

---

### 2. Configure environment

💻 **Terminal**

```bash
cp .env.example .env
```

Open `.env` and fill in every value:

| Variable | Where to get it | Example |
| -------- | --------------- | ------- |
| `TELEGRAM_BOT_TOKEN` | BotFather (see above) | `7123456789:AAF...` |
| `ADMIN_TELEGRAM_ID` | @userinfobot (see above) | `987654321` |
| `POSTGRES_DB` | Choose a name | `depositbot` |
| `POSTGRES_USER` | Choose a username | `botuser` |
| `POSTGRES_PASSWORD` | Generate a strong password | `openssl rand -hex 16` |
| `DATABASE_URL` | See Mode A/B note below | — |
| `REDIS_URL` | See Mode A/B note below | — |
| `SMS_WEBHOOK_SECRET` | Generate randomly | `openssl rand -hex 16` |

**Mode A (Full Docker)** — bot connects to containers by service name:

```env
DATABASE_URL=postgresql://botuser:YOUR_PASSWORD@postgres:5432/depositbot
REDIS_URL=redis://redis:6379/0
```

**Mode B (Hybrid / uv)** — bot runs on host, connects to Docker on localhost:

```env
DATABASE_URL=postgresql://botuser:YOUR_PASSWORD@localhost:5432/depositbot
REDIS_URL=redis://localhost:6379/0
```

---

### 3. Place your UPI QR code

💻 **Terminal**

```bash
cp /path/to/your/qr.png assets/qr_code.png
```

---

### 4A — Full Docker: start everything

💻 **Terminal**

```bash
docker compose up -d --build
```

All three services (bot, postgres, redis) start together. The bot waits for postgres and redis to be healthy before starting.

Watch startup logs:

```bash
docker compose logs -f bot
```

Skip to **Step 5**.

---

### 4B — Hybrid: start only Postgres and Redis

💻 **Terminal**

```bash
docker compose up -d postgres redis
```

`docker-compose.override.yml` (included in the repo) automatically exposes Postgres on `localhost:5432` and Redis on `localhost:6379` when running locally, so the bot can reach them from outside Docker.

Verify both are healthy:

```bash
docker compose ps
```

---

### 5. Seed the database with test users

Wait until Postgres is healthy (Step 4A/4B), then add users.
The phone numbers here must match the Telegram accounts you'll use for testing.

🐳 **Docker**

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "
INSERT INTO users (phone, name) VALUES
  ('+919876543210', 'Rahul Sharma'),
  ('+919823456789', 'Priya Patel'),
  ('+918765432109', 'Amit Verma'),
  ('+917654321098', 'Sneha Nair'),
  ('+916543210987', 'Vikram Singh')
ON CONFLICT (phone) DO NOTHING;
"
```

Verify:

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
  "SELECT id, name, phone, balance FROM users;"
```

> If the shell doesn't expand `$POSTGRES_USER`/`$POSTGRES_DB`, replace them with the literal values from your `.env`.

---

### 6. Run database migrations (if needed)

Migrations auto-apply on first Postgres start. To apply manually (e.g. after wiping the volume):

🐳 **Docker**

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -f /docker-entrypoint-initdb.d/001_init.sql
```

Confirm tables:

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "\dt"
```

---

### 7. Run the bot (Mode B — Hybrid only)

*Skip this step if you used Mode A (Full Docker) — the bot is already running.*

💻 **Terminal** — using uv (recommended)

```bash
# Install all dependencies (creates .venv automatically)
uv sync

# Run the bot
uv run python -m bot.main
```

Or with pip:

```bash
pip install -r requirements.txt
python -m bot.main
```

The bot starts polling Telegram and FastAPI listens on port `8000`.

---

### 8. Expose the webhook for local testing (ngrok)

The Android SMS forwarder needs a public HTTPS URL to POST to. Use ngrok to tunnel `localhost:8000`.

💻 **Terminal** — open a new terminal tab

```bash
# Install ngrok (one-time)
brew install ngrok/ngrok/ngrok   # macOS
# or download from https://ngrok.com/download

ngrok http 8000
```

ngrok prints a public URL, e.g.:

```text
Forwarding  https://abc123.ngrok-free.app -> http://localhost:8000
```

Use `https://abc123.ngrok-free.app/webhook/sms` as the forwarder webhook URL.

---

## Production Deployment (Docker)

> See [DEPLOY_EC2.md](DEPLOY_EC2.md) for the full EC2 step-by-step guide.

All services (bot, Postgres, Redis) run as Docker containers. No Python install needed on the server.  
**Do NOT copy `docker-compose.override.yml` to the server** — it is for local dev only.

### 1. Build and start everything

💻 **Terminal** — on your server

```bash
docker compose up -d --build
```

Check that all three containers are healthy:

```bash
docker compose ps
```

Watch live logs from the bot:

```bash
docker compose logs -f bot
```

---

### 2. Apply migrations (first deploy only)

Migrations auto-run on first `docker compose up`. To apply manually:

🐳 **Docker**

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -f /docker-entrypoint-initdb.d/001_init.sql
```

---

### 3. Pre-load registered users

🐳 **Docker**

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "
INSERT INTO users (phone, name) VALUES
  ('+919876543210', 'Rahul Sharma'),
  ('+919823456789', 'Priya Patel')
ON CONFLICT (phone) DO NOTHING;
"
```

---

### 4. Nginx reverse proxy configuration

💻 **Terminal** — on your server, edit `/etc/nginx/sites-available/depositbot`

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location /webhook/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }
}
```

Enable the site:

💻 **Terminal**

```bash
ln -s /etc/nginx/sites-available/depositbot /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

---

### 5. SSL certificate (Let's Encrypt)

💻 **Terminal**

```bash
certbot --nginx -d yourdomain.com
```

## Adding Your UPI QR Code

1. Log in to your bank's net banking / UPI app (PhonePe, GPay, Paytm, etc.).
2. Generate a "Receive Money" QR code tied to your account.
3. Save it as `assets/qr_code.png`.
4. The bot sends this image to users during the deposit flow.

You can also generate a static UPI QR at [upiqr.in](https://upiqr.in) or via your payment gateway.

## Database

### Users table

Pre-populate users before deployment — users must be registered (phone number added to the `users` table) before they can use the bot.

```sql
INSERT INTO users (phone, name) VALUES ('+919876543210', 'Rahul Sharma');
```

### Checking balances

```sql
SELECT phone, name, balance FROM users ORDER BY balance DESC;
```

### Viewing recent transactions

```sql
SELECT txn_id, amount, credited, received_at FROM received_transactions ORDER BY received_at DESC LIMIT 20;
```

## Adding New Bank SMS Patterns

Open [bot/services/sms_parser.py](bot/services/sms_parser.py) and add a new regex pattern:

```python
# Example: Yes Bank
# "INR 500.00 credited to your a/c. Ref No YBL123456789012."
_YES_BANK_PATTERN = re.compile(
    r"INR\s+([\d,]+\.?\d*)\s+credited.*?Ref\s*No\s+([A-Z0-9]{10,22})",
    re.IGNORECASE | re.DOTALL,
)

# Add to the _BANK_PATTERNS list:
_BANK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ...
    ("Yes Bank", _YES_BANK_PATTERN),
]
```

Then add a test case to `test_sms_parser.py` with a real sample SMS and run `pytest`.

## Smoke Test — Is Everything Running?

Run this before going live (or after any restart) to verify all components are healthy.

💻 **Terminal** — from the project root

```bash
uv run python scripts/smoke_test.py
```

What it checks:

| # | Check | What it verifies |
| - | ----- | ---------------- |
| 1 | Env vars | All required `.env` values are set |
| 2 | Telegram token | Calls `getMe` — confirms token is valid and shows bot username |
| 3 | FastAPI `/health` | Confirms the webhook server is reachable on `localhost:8000` |
| 4 | PostgreSQL | Connects to DB, confirms all 4 tables exist |
| 4b | Registered users | Lists names and phone numbers of all registered users |
| 5 | Redis | Pings Redis, checks SMS queue backlog and forwarder heartbeat |

Example output when everything is healthy:

```text
==================================================
  Telegram Deposit Bot — Smoke Test
==================================================

[1] Environment variables
  ✔  TELEGRAM_BOT_TOKEN is set
  ✔  DATABASE_URL is set
  ✔  REDIS_URL is set
  ✔  SMS_WEBHOOK_SECRET is set
  ✔  ADMIN_TELEGRAM_ID is set (987654321)

[2] Telegram bot token
  ✔  Bot is valid: @mydeposit_bot (id=7123456789)

[3] FastAPI webhook server
  ✔  Health endpoint responded: http://localhost:8000/health

[4] PostgreSQL
  ✔  Connected to PostgreSQL
  ✔  Table 'users' exists
  ✔  Table 'received_transactions' exists
  ✔  Table 'deposit_sessions' exists
  ✔  Table 'manual_review' exists

[4b] Registered users
  ✔  1 user(s) found:
       • Rahul Sharma  +919876543210

[5] Redis
  ✔  Connected to Redis
  ✔  sms_queue is empty (no backlog)
  !  No SMS forwarder heartbeat — Android app not yet connected

==================================================
All checks passed. The bot stack looks healthy.
==================================================
```

Exit code is `0` on success, `1` if any check fails — safe to use in CI or deploy scripts.

## Testing the SMS Flow

### Manually POST a fake SMS to the webhook

```bash
curl -X POST http://localhost:8000/webhook/sms \
  -H "Content-Type: application/json" \
  -H "X-SMS-Secret: your_secret_value" \
  -d '{
    "body": "Rs.500.00 credited to your a/c XX1234 on 14-04-26 by UPI. Info: TEST-PAY. Ref No 123456789012. -HDFC Bank",
    "from": "HDFCBK",
    "timestamp": "2026-04-14T10:00:00Z"
  }'
```

### Expected response

```json
{"status": "queued"}
```

The SMS worker will process it within seconds and insert it into `received_transactions`.

### Verify the transaction was stored

```sql
SELECT * FROM received_transactions WHERE txn_id = '123456789012';
```

### Submit it via the bot

1. Open Telegram → your bot → tap **Deposit**
2. Submit transaction ID: `123456789012`
3. The bot should confirm: "Payment of ₹500.00 confirmed!"

### Run unit tests

💻 **Terminal**

```bash
# With uv
uv run pytest test_sms_parser.py -v

# With pip / system Python
pytest test_sms_parser.py -v
```

## Monitoring

### Watchdog alerts

The watchdog runs every 5 minutes and sends a Telegram message to `ADMIN_TELEGRAM_ID` if:

- The SMS forwarder heartbeat has not been received for 10+ minutes.

Example alert:

```text
⚠️ SMS Forwarder is not responding.
Last seen: 2026-04-14 10:30:00 UTC
Please check the Android phone.
```

### Daily summary (09:00 local time)

```text
📊 Daily Summary
━━━━━━━━━━━━━━━━━━
SMSes received (last 24h): 47
Transactions credited:     43
Manual reviews pending:    2
━━━━━━━━━━━━━━━━━━
```

### Log files

| File                        | Contents                                |
| --------------------------- | --------------------------------------- |
| `logs/sms_parse_errors.log` | Raw SMS bodies that could not be parsed |
| stdout / `docker logs bot`  | All application-level logs              |

### Manual review queue

Transactions that could not be matched after 3 minutes of polling are logged to:

- The `manual_review` PostgreSQL table
- The application log at WARN level

Query pending reviews:

```sql
SELECT * FROM manual_review WHERE resolved = FALSE ORDER BY submitted_at DESC;
```

Mark resolved after manual credit:

```sql
UPDATE manual_review SET resolved = TRUE, notes = 'Manually credited on 2026-04-14' WHERE id = 1;
```

## Security Notes

- **Never commit `.env`** — it contains your bot token, DB credentials, and webhook secret. `.env` is already listed in `.gitignore`.
- **Rotate `SMS_WEBHOOK_SECRET` periodically** — update it in `.env` and in the Android forwarder app simultaneously.
- **The `credited = TRUE` flag** in `received_transactions` is the primary safeguard against double-crediting. It is checked before every credit operation.
- **HTTPS only in production** — the webhook endpoint transmits raw SMS data; always use TLS.
- **Allowlist bank senders** on the Android forwarder app to prevent fake SMS injection.
- **DB access** — restrict PostgreSQL to `localhost` or the Docker internal network. Never expose port 5432 to the internet.

## Project Structure

```text
telegram-deposit-bot/
├── bot/
│   ├── main.py              # Entry point (asyncio.gather of bot + FastAPI + worker)
│   ├── handlers/
│   │   ├── states.py        # FSM state constants
│   │   ├── common.py        # Shared utilities, keyboard layouts
│   │   ├── start.py         # /start + phone auth ConversationHandler
│   │   ├── profile.py       # View profile handler
│   │   └── deposit.py       # Deposit flow ConversationHandler
│   ├── services/
│   │   ├── sms_parser.py    # Bank SMS regex parser (HDFC, ICICI, SBI, Axis, Kotak)
│   │   ├── sms_worker.py    # Redis BLPOP consumer → DB insert
│   │   ├── verifier.py      # Txn lookup + balance credit logic
│   │   └── watchdog.py      # APScheduler heartbeat + daily summary jobs
│   ├── webhook/
│   │   └── sms_receiver.py  # FastAPI: POST /webhook/sms, POST /webhook/heartbeat
│   └── db/
│       ├── connection.py    # asyncpg pool lifecycle
│       └── queries.py       # All DB queries
├── assets/
│   └── qr_code.png          # Place your UPI QR here
├── migrations/
│   └── 001_init.sql         # Initial schema
├── test_sms_parser.py       # pytest tests for SMS parser
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```
