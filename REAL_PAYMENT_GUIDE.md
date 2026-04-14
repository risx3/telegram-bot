# Real Payment End-to-End Guide

This guide walks you through a full real payment test — from UPI transfer to balance credit in the bot.

---

## How the payment flow works

```
User pays QR → Bank sends SMS to Android phone
                        ↓
             SMS Forwarder app POSTs to /webhook/sms
                        ↓
             Redis queue (sms_queue)
                        ↓
             SMS Worker parses: amount + UTR extracted
                        ↓
             PostgreSQL: received_transactions row inserted
                        ↓
             User submits UTR in bot
                        ↓
             Verifier finds row → credits balance
```

---

## Prerequisites checklist

- [ ] Bot is running (`uv run python -m bot.main`)
- [ ] `.env` has all required vars (run smoke test to confirm)
- [ ] `assets/qr_code.png` — your real UPI QR code image is placed there
- [ ] Android phone has an SMS forwarder app installed
- [ ] The webhook URL is reachable from the internet (use ngrok if local)

---

## Step 1 — Place your UPI QR code

Put your UPI QR code image (the one linked to your bank account) at:

```
assets/qr_code.png
```

This is the image the bot sends to users when they tap **Deposit**.  
It must be a real, scannable UPI QR — generate one from your bank app or Google Pay / PhonePe.

---

## Step 2 — Expose your webhook to the internet

The Android SMS forwarder app needs a **public HTTPS URL** to POST to.  
If you're running locally, use [ngrok](https://ngrok.com):

```bash
# Install ngrok (one-time)
brew install ngrok/ngrok/ngrok

# Expose port 8000
ngrok http 8000
```

ngrok prints something like:
```
Forwarding  https://abc123.ngrok-free.app -> http://localhost:8000
```

Your webhook URL will be: `https://abc123.ngrok-free.app/webhook/sms`

> On a VPS/server: use your domain directly, e.g. `https://yourdomain.com/webhook/sms`

---

## Step 3 — Set up the Android SMS forwarder

### Option A — SMS Forwarder (recommended, free)

1. Install **SMS Forwarder** from the Play Store (search: "SMS to URL Webhook")
   - Recommended app: **"SMS to Webhook"** by Bogdan Iusco, or any app that can POST JSON to a URL

2. Configure a forwarding rule:
   - **URL**: `https://YOUR_NGROK_OR_DOMAIN/webhook/sms`
   - **Method**: POST
   - **Header**: `X-SMS-Secret: YOUR_SMS_WEBHOOK_SECRET` (must match `.env`)
   - **Body template** (JSON):
     ```json
     {"sender": "%from%", "body": "%body%"}
     ```
   - **Filter**: only forward SMS from bank senders (e.g. `HDFCBK`, `ICICIB`, `SBIINB`, etc.)

3. Test the rule by sending a ping — the app usually has a "Test" button.

### Option B — MacroDroid / Automate / Tasker

If you already use automation apps, create a task:
- Trigger: SMS received from `[BANK_SENDER]`
- Action: HTTP POST to your webhook URL with JSON body `{"body": "%sms_body%"}`
- Add header: `X-SMS-Secret: YOUR_SECRET`

### Option C — Manual test via curl (no Android needed)

Skip the Android phone entirely and simulate an SMS manually:

```bash
# Replace values with your actual secret and a real UTR
curl -X POST https://YOUR_NGROK/webhook/sms \
  -H "Content-Type: application/json" \
  -H "X-SMS-Secret: YOUR_SMS_WEBHOOK_SECRET" \
  -d '{
    "sender": "HDFCBK",
    "body": "Rs.500.00 credited to your a/c XX1234 on 14-04-26. Info: UPI-Test. Ref No 123456789012. -HDFC Bank"
  }'
```

The bot logs will show: `SMS queued` → `Transaction inserted: txn_id=123456789012 amount=500.00`

---

## Step 4 — Do a real UPI payment

1. Open Telegram and start the bot
2. Tap **Deposit** — the bot sends your QR code image
3. Open any UPI app (GPay, PhonePe, Paytm, etc.)
4. Scan the QR or enter the UPI ID
5. Pay any amount (e.g. ₹1 for a test)
6. Wait for the bank SMS to arrive on your Android phone (~5–30 seconds)

---

## Step 5 — Submit the UTR in the bot

After paying:

1. Open your UPI app → payment history → find the transaction
2. Copy the **Transaction ID / UTR / Ref No** — it looks like `123456789012` (12 digits) or `HDFC12345678` (alphanumeric)
3. Paste it into the bot when it asks: *"Reply with your UPI Transaction ID"*

The bot will respond:
- **Success**: `Payment of ₹500.00 confirmed! Your new balance is ₹500.00.`
- **Still checking**: `Still checking (2/6)... the SMS may be delayed.` (retries every 30s, up to 3 minutes)
- **Not found**: Goes to manual review — check your webhook and SMS forwarder logs

---

## Debugging — where to look when something goes wrong

### SMS not reaching the webhook

```bash
# Watch bot logs in real time
uv run python -m bot.main 2>&1 | grep -i sms

# Or check ngrok dashboard
open http://localhost:4040
```

Check ngrok's request inspector at `http://localhost:4040` — you can see every HTTP request that hit your webhook.

### SMS reached webhook but UTR not found

The SMS was queued but the parser may not have recognised the format.

```bash
# Check parse error log
cat logs/sms_parse_errors.log
```

If your bank's SMS format isn't supported, add a pattern to [bot/services/sms_parser.py](bot/services/sms_parser.py).

### Manually inject a known-good SMS to test parsing

```bash
# Test the parser directly (no running bot needed)
python3 - <<'EOF'
from bot.services.sms_parser import parse
sms = "Rs.500.00 credited to your a/c XX1234 on 14-04-26. Info: UPI-Test. Ref No 123456789012. -HDFC Bank"
print(parse(sms))
EOF
```

Expected output:
```python
{'txn_id': '123456789012', 'amount': 500.0, 'bank': 'HDFC'}
```

### Manually insert a transaction into PostgreSQL (skip SMS entirely)

Use this to test the verifier + credit logic without any SMS:

```bash
docker-compose exec postgres psql -U user -d depositbot -c \
  "INSERT INTO received_transactions (txn_id, amount, raw_sms)
   VALUES ('123456789012', 500.00, 'manual test')
   ON CONFLICT DO NOTHING;"
```

Then submit `123456789012` in the bot. It should credit ₹500.00 immediately.

### Check database state

```bash
# See all received transactions
docker-compose exec postgres psql -U user -d depositbot -c \
  "SELECT txn_id, amount, credited, created_at FROM received_transactions ORDER BY created_at DESC LIMIT 10;"

# Check user balances
docker-compose exec postgres psql -U user -d depositbot -c \
  "SELECT name, phone, balance FROM users;"

# See manual review queue
docker-compose exec postgres psql -U user -d depositbot -c \
  "SELECT * FROM manual_review ORDER BY created_at DESC;"
```

---

## Full end-to-end test (no Android phone required)

Use this to verify everything works before setting up the Android forwarder:

```bash
# 1. Start the bot
uv run python -m bot.main &

# 2. Inject a fake SMS via curl
curl -X POST http://localhost:8000/webhook/sms \
  -H "Content-Type: application/json" \
  -H "X-SMS-Secret: YOUR_SMS_WEBHOOK_SECRET" \
  -d '{"sender":"HDFCBK","body":"Rs.100.00 credited to your a/c XX1234 on 14-04-26. Info: UPI-Test. Ref No 112233445566. -HDFC Bank"}'

# 3. In Telegram: tap Deposit → submit UTR: 112233445566
# 4. Bot should respond: Payment of ₹100.00 confirmed!
```

---

## SMS format reference (what the parser recognises)

| Bank   | Example SMS |
|--------|-------------|
| HDFC   | `Rs.500.00 credited to your a/c XX1234 on 14-04-26. Info: UPI-name. Ref No 123456789012. -HDFC Bank` |
| ICICI  | `Rs.500.00 credited to Acct XX1234 on 14-Apr-26. UPI Ref:123456789012. -ICICI Bank` |
| SBI    | `Your a/c XXXX1234 credited by Rs.500.00 on 14Apr26 by UPI. UTR No 123456789012.` |
| Axis   | `INR 500.00 credited to your a/c linked to VPA xxx@axis on 14-04-2026. UPI Ref No 123456789012 -Axis Bank` |
| Kotak  | `Rs.500.00 credited to your Kotak Bank A/c XXXXXXXX1234 by UPI ref 123456789012 on 14-Apr-2026.` |
| Other  | Any SMS with `Rs.AMOUNT` + a standalone 12-digit number |

If your bank isn't listed, paste a real SMS (with account number redacted) and a new pattern can be added.
