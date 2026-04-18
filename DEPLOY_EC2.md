# EC2 Deployment Guide — Telegram Deposit Bot

Step-by-step instructions for deploying to an AWS EC2 instance (Amazon Linux 2023 or Ubuntu 22.04).

---

## Prerequisites

- EC2 instance running (Amazon Linux 2023 or Ubuntu 22.04)
- SSH access to the instance
- Your `.env` values ready (bot token, DB password, webhook secret, admin Telegram ID)
- Inbound security group rules open:
  - Port **22** — SSH
  - Port **8000** — webhook (or 443/80 if behind a reverse proxy)

---

## Step 1 — Connect to your EC2 instance

```bash
ssh -i your-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
# Ubuntu:
ssh -i your-key.pem ubuntu@YOUR_EC2_PUBLIC_IP
```

---

## Step 2 — Install Docker and Docker Compose

**Amazon Linux 2023:**

```bash
sudo yum update -y
sudo yum install -y docker git
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ec2-user
newgrp docker

# Docker Compose plugin
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version   # should print v2.x
```

**Ubuntu 22.04:**

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ubuntu
newgrp docker
docker compose version
```

---

## Step 3 — Clone the repository

```bash
cd ~
git clone https://github.com/YOUR_USERNAME/telegram-bot.git
cd telegram-bot
```

> If the repo is private, use a personal access token:
> `git clone https://YOUR_TOKEN@github.com/YOUR_USERNAME/telegram-bot.git`

**Do NOT copy `docker-compose.override.yml` to EC2.** That file is for local development only. The EC2 server should only have `docker-compose.yml`.

---

## Step 4 — Create the `.env` file

```bash
cp .env.example .env
nano .env
```

After saving, load the vars into your current shell session (needed for the psql commands in later steps):

```bash
set -a && source .env && set +a
```

> You need to re-run this any time you open a new SSH session before running psql commands.

Fill in every value:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here

# PostgreSQL — read by both the bot and the postgres container
POSTGRES_DB=depositbot
POSTGRES_USER=botuser
POSTGRES_PASSWORD=StrongPassword123
DATABASE_URL=postgresql://botuser:StrongPassword123@postgres:5432/depositbot

REDIS_URL=redis://redis:6379/0
SMS_WEBHOOK_SECRET=generate_a_random_secret_here
ADMIN_TELEGRAM_ID=your_telegram_id_here
PORT=8000
LOG_LEVEL=INFO
QR_CODE_PATH=assets/qr_code.png
```

Generate strong random secrets:

```bash
python3 -c "import secrets; print(secrets.token_hex(16))"
```

> `POSTGRES_PASSWORD` in `.env` is read by both the bot (`DATABASE_URL`) and the postgres container. No need to edit `docker-compose.yml` separately.

---

## Step 5 — Add your UPI QR code

Run this on your **local machine**, not on EC2:

```bash
scp -i your-key.pem /path/to/your_qr_code.png \
  ec2-user@YOUR_EC2_PUBLIC_IP:~/telegram-bot/assets/qr_code.png
```

---

## Step 6 — Build and start the stack

```bash
cd ~/telegram-bot
docker compose up -d --build
```

Watch startup logs:

```bash
docker compose logs -f bot
```

Expected output:

```text
Connecting to PostgreSQL...
Webhook Redis client initialised.
Telegram bot started (polling).
Watchdog scheduler started.
```

The bot waits for postgres and redis health checks to pass before starting.

---

## Step 7 — Seed the database with test users

Replace the phone numbers below with real numbers you'll use for testing (in E.164 format: `+91XXXXXXXXXX`).

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "
INSERT INTO users (phone, name) VALUES
  ('+917387243265', 'Ronnie H'),
  ('+919876543210', 'Test User 2'),
  ('+919823456789', 'Test User 3')
ON CONFLICT (phone) DO NOTHING;
"
```

Verify:

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
  "SELECT id, name, phone, balance FROM users;"
```

Verify:

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c "SELECT id, name, phone, balance FROM users;"
```

> The phone number you use in Telegram must match one of the seeded numbers. When you send `/start` and share your contact, the bot matches it against this table.

---

## Step 8 — Verify the webhook is reachable

From your **local machine**:

```bash
curl http://YOUR_EC2_PUBLIC_IP:8000/health
```

Expected: `{"status":"ok"}`

Test the SMS endpoint:

```bash
curl -X POST http://YOUR_EC2_PUBLIC_IP:8000/webhook/sms \
  -H "Content-Type: application/json" \
  -H "X-SMS-Secret: YOUR_SMS_WEBHOOK_SECRET" \
  -d '{"sender":"HDFCBK","body":"Rs.100.00 credited to your a/c XX1234 on 14-04-26. Info: UPI-Test. Ref No 112233445566. -HDFC Bank"}'
```

Expected: `{"status":"queued"}`

Verify it was parsed and inserted:

```bash
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c \
  "SELECT txn_id, amount, credited FROM received_transactions;"
```

---

## Step 9 — Configure the Android SMS Forwarder

Point the app at your EC2 public URL:

| Field | Value |
| ----- | ----- |
| URL | `http://YOUR_EC2_PUBLIC_IP:8000/webhook/sms` |
| Method | POST |
| Header key | `X-SMS-Secret` |
| Header value | your `SMS_WEBHOOK_SECRET` |
| Body template | `{"body": "%body%", "sender": "%from%"}` |
| Filter sender | `HDFCBK` / `ICICIB` / `SBIINB` etc. |

Heartbeat rule (keeps watchdog silent):

| Field | Value |
| ----- | ----- |
| URL | `http://YOUR_EC2_PUBLIC_IP:8000/webhook/heartbeat` |
| Method | POST |
| Header | same `X-SMS-Secret` |
| Body | `{}` |
| Interval | Every 5 minutes |

---

## Step 10 — Run the smoke test

```bash
docker compose exec bot uv run python scripts/smoke_test.py
```

All 5 checks should pass.

---

## Step 11 — End-to-end payment test

1. Open the bot in Telegram → `/start` → share your phone number (must match a seeded number)
2. Tap **Deposit** — bot sends the QR code
3. Pay ₹1 via any UPI app
4. Bank SMS arrives on Android → forwarded to EC2 webhook → parsed → inserted into DB
5. Submit the UTR in the bot → bot replies with balance confirmation

**Or test without a real payment (inject SMS manually):**

```bash
curl -X POST http://YOUR_EC2_PUBLIC_IP:8000/webhook/sms \
  -H "Content-Type: application/json" \
  -H "X-SMS-Secret: YOUR_SMS_WEBHOOK_SECRET" \
  -d '{"sender":"HDFCBK","body":"Rs.500.00 credited to your a/c XX1234 on 17-04-26. Info: UPI-Test. Ref No 998877665544. -HDFC Bank"}'
```

Then submit UTR `998877665544` in the bot.

---

## Useful commands

```bash
# Load .env vars into shell first (required for psql commands)
set -a && source .env && set +a

# View live logs
docker compose logs -f bot

# Restart the bot only (after a code change)
docker compose up -d --build bot

# Stop everything
docker compose down

# Stop and wipe the database (fresh start)
docker compose down -v

# Open a psql shell
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB

# Check Redis queue length and forwarder heartbeat
docker compose exec redis redis-cli llen sms_queue
docker compose exec redis redis-cli get sms_forwarder_heartbeat

# Check all container statuses
docker compose ps
```

---

## Security hardening (before going live)

- [x] Postgres and Redis ports are NOT exposed publicly (handled in `docker-compose.yml`)
- [ ] Lock port 8000 to only the Android device IP, or put it behind nginx + SSL
- [ ] Use a strong random `SMS_WEBHOOK_SECRET` (32+ chars)
- [ ] Use a strong `POSTGRES_PASSWORD` (not the example value)
- [ ] Set `LOG_LEVEL=WARNING` in production to reduce log volume
- [ ] Enable EC2 instance termination protection in the AWS console
