# EC2 Deployment Guide — Telegram Deposit Bot

Step-by-step instructions for deploying to an AWS EC2 instance (Amazon Linux 2 / Ubuntu).

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

---

## Step 4 — Create the `.env` file

```bash
cp .env.example .env   # if .env.example exists, otherwise:
nano .env
```

Paste and fill in your values:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
DATABASE_URL=postgresql://user:StrongPassword123@postgres:5432/depositbot
REDIS_URL=redis://redis:6379/0
SMS_WEBHOOK_SECRET=generate_a_random_secret_here
ADMIN_TELEGRAM_ID=your_telegram_id_here
PORT=8000
LOG_LEVEL=INFO
QR_CODE_PATH=assets/qr_code.png
```

> Generate a strong secret:
> ```bash
> python3 -c "import secrets; print(secrets.token_hex(16))"
> ```

**Important — update DB password in docker-compose.yml to match:**
```bash
nano docker-compose.yml
```
Change the postgres service:
```yaml
environment:
  POSTGRES_DB: depositbot
  POSTGRES_USER: user
  POSTGRES_PASSWORD: StrongPassword123   # ← match DATABASE_URL
```

---

## Step 5 — Add your UPI QR code

Copy your UPI QR code image to the assets folder:

```bash
# From your local machine — run this locally, not on EC2
scp -i your-key.pem /path/to/your_qr_code.png ec2-user@YOUR_EC2_PUBLIC_IP:~/telegram-bot/assets/qr_code.png
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
```
Connecting to PostgreSQL...
Webhook Redis client initialised.
Telegram bot started (polling).
Watchdog scheduler started.
```

---

## Step 7 — Seed the database with test users

Wait until Step 6 completes (postgres container is healthy), then run:

```bash
docker compose exec postgres psql -U user -d depositbot -c "
INSERT INTO users (phone, name) VALUES
  ('+919876543210', 'Rahul Sharma'),
  ('+919823456789', 'Priya Patel'),
  ('+918765432109', 'Amit Verma'),
  ('+917654321098', 'Sneha Nair'),
  ('+916543210987', 'Vikram Singh')
ON CONFLICT (phone) DO NOTHING;
"
```

Verify the users were inserted:
```bash
docker compose exec postgres psql -U user -d depositbot -c \
  "SELECT id, name, phone, balance FROM users;"
```

Expected:
```
 id |     name      |     phone      | balance
----+---------------+----------------+---------
  1 | Rahul Sharma  | +919876543210  |    0.00
  2 | Priya Patel   | +919823456789  |    0.00
  3 | Amit Verma    | +918765432109  |    0.00
  4 | Sneha Nair    | +917654321098  |    0.00
  5 | Vikram Singh  | +916543210987  |    0.00
```

> To test the full deposit flow, one of these phone numbers must match the phone number on the Telegram account you use for testing. When you open the bot and share your contact, it matches against these records.

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

Then verify it was parsed and inserted:
```bash
docker compose exec postgres psql -U user -d depositbot -c \
  "SELECT txn_id, amount, credited FROM received_transactions;"
```

---

## Step 9 — Configure the Android SMS Forwarder

Point the app at your EC2 public URL:

| Field | Value |
|-------|-------|
| URL | `http://YOUR_EC2_PUBLIC_IP:8000/webhook/sms` |
| Method | POST |
| Header key | `X-SMS-Secret` |
| Header value | your `SMS_WEBHOOK_SECRET` |
| Body template | `{"body": "%body%", "sender": "%from%"}` |
| Filter sender | `HDFCBK` / `ICICIB` / `SBIINB` etc. |

Heartbeat rule (keeps watchdog silent):

| Field | Value |
|-------|-------|
| URL | `http://YOUR_EC2_PUBLIC_IP:8000/webhook/heartbeat` |
| Method | POST |
| Header | same `X-SMS-Secret` |
| Body | `{}` |
| Interval | Every 5 minutes |

---

## Step 10 — Run the smoke test

```bash
# On EC2
cd ~/telegram-bot
docker compose exec bot python scripts/smoke_test.py
```

All 5 checks should pass.

---

## Step 11 — End-to-end payment test

1. Open the bot in Telegram → `/start` → share your phone number  
   (must match one of the seeded numbers, e.g. `+919876543210`)
2. Tap **Deposit** — bot sends the QR code
3. Pay ₹1 via any UPI app
4. Bank SMS arrives on Android → forwarded to EC2 webhook → parsed → inserted into DB
5. Submit the UTR in the bot → bot replies with balance confirmation

**Or test without real payment (inject SMS manually):**
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
# View live logs
docker compose logs -f bot

# Restart the bot only (after a code change)
docker compose up -d --build bot

# Stop everything
docker compose down

# Stop and wipe the database (fresh start)
docker compose down -v

# Open a psql shell
docker compose exec postgres psql -U user -d depositbot

# Check Redis queue
docker compose exec redis redis-cli llen sms_queue
docker compose exec redis redis-cli get sms_forwarder_heartbeat

# Check all container statuses
docker compose ps
```

---

## Security hardening (before going live)

- [ ] Lock port 5432 (Postgres) and 6379 (Redis) in the security group — they should NOT be public-facing
- [ ] Lock port 8000 to only the Android device IP if static, or put it behind nginx + SSL
- [ ] Use a strong random `SMS_WEBHOOK_SECRET` (32+ chars)
- [ ] Change the default Postgres password from `password` to something strong
- [ ] Set `LOG_LEVEL=WARNING` in production to reduce log volume
- [ ] Enable EC2 instance termination protection in the AWS console
