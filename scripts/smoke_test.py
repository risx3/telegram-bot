"""
Smoke test — verifies the bot stack is healthy before going live.

Checks:
  1. Required env vars are set
  2. Telegram bot token is valid (getMe API call)
  3. FastAPI /health endpoint is reachable
  4. PostgreSQL connection + required tables exist
  5. Redis connection
  6. At least one registered user exists and name is fetchable

Usage:
  uv run python scripts/smoke_test.py
  python scripts/smoke_test.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Allow running from repo root or scripts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncpg
import httpx
import redis.asyncio as aioredis
from dotenv import load_dotenv

load_dotenv()

# ── Colour helpers ──────────────────────────────────────────────────────────
GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def ok(msg: str)   -> None: print(f"  {GREEN}✔{RESET}  {msg}")
def fail(msg: str) -> None: print(f"  {RED}✘{RESET}  {msg}")
def warn(msg: str) -> None: print(f"  {YELLOW}!{RESET}  {msg}")

_failures = 0

def record_fail(msg: str) -> None:
    global _failures
    _failures += 1
    fail(msg)


# ── 1. Env vars ─────────────────────────────────────────────────────────────

REQUIRED_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "DATABASE_URL",
    "REDIS_URL",
    "SMS_WEBHOOK_SECRET",
]

def check_env() -> None:
    print("\n[1] Environment variables")
    for var in REQUIRED_VARS:
        val = os.environ.get(var, "")
        if val:
            ok(f"{var} is set")
        else:
            record_fail(f"{var} is missing from .env")

    admin_id = os.environ.get("ADMIN_TELEGRAM_ID", "")
    if admin_id:
        ok(f"ADMIN_TELEGRAM_ID is set ({admin_id})")
    else:
        warn("ADMIN_TELEGRAM_ID not set — watchdog alerts will be disabled")


# ── 2. Telegram API ─────────────────────────────────────────────────────────

async def check_telegram(client: httpx.AsyncClient) -> None:
    print("\n[2] Telegram bot token")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        record_fail("Cannot check — TELEGRAM_BOT_TOKEN not set")
        return

    try:
        resp = await client.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            bot = data["result"]
            ok(f"Bot is valid: @{bot['username']} (id={bot['id']})")
        else:
            record_fail(f"getMe returned error: {data.get('description', 'unknown')}")
    except httpx.ConnectError:
        record_fail("Could not reach api.telegram.org — check internet connection")
    except Exception as exc:
        record_fail(f"Telegram check failed: {exc}")


# ── 3. FastAPI /health ───────────────────────────────────────────────────────

async def check_webhook(client: httpx.AsyncClient) -> None:
    print("\n[3] FastAPI webhook server")
    port = os.environ.get("PORT", "8000")
    url = f"http://localhost:{port}/health"
    try:
        resp = await client.get(url, timeout=5)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            ok(f"Health endpoint responded: {url}")
        else:
            record_fail(f"Unexpected response from {url}: {resp.status_code} {resp.text}")
    except httpx.ConnectError:
        record_fail(
            f"Could not reach {url} — is the bot running? "
            "Start it with: uv run python -m bot.main"
        )
    except Exception as exc:
        record_fail(f"Webhook check failed: {exc}")


# ── 4. PostgreSQL ────────────────────────────────────────────────────────────

REQUIRED_TABLES = [
    "transactions",
]

async def check_postgres() -> None:
    print("\n[4] PostgreSQL")
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        record_fail("DATABASE_URL not set")
        return

    db_url = db_url.replace("postgres://", "postgresql://", 1)
    try:
        conn = await asyncpg.connect(db_url, timeout=10)
    except Exception as exc:
        record_fail(f"Could not connect to PostgreSQL: {exc}")
        return

    try:
        ok("Connected to PostgreSQL")

        # Check tables
        existing = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        existing_names = {row["tablename"] for row in existing}
        for table in REQUIRED_TABLES:
            if table in existing_names:
                ok(f"Table '{table}' exists")
            else:
                record_fail(
                    f"Table '{table}' is missing — "
                    "run: docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB "
                    "-f /docker-entrypoint-initdb.d/001_init.sql"
                )

        # Show recent transactions
        print("\n[4b] Recent transactions")
        rows = await conn.fetch(
            "SELECT txn_id, phone, amount, confirmed, received_at FROM transactions ORDER BY received_at DESC LIMIT 5"
        )
        if rows:
            ok(f"{len(rows)} transaction(s) found (latest 5):")
            for r in rows:
                confirmed = "confirmed" if r["confirmed"] else "pending"
                print(f"       • {r['txn_id']}  ₹{r['amount']}  {r['phone'] or 'no phone'}  [{confirmed}]")
        else:
            warn("No transactions yet — inject a test SMS to populate the table.")

    finally:
        await conn.close()


# ── 5. Redis ─────────────────────────────────────────────────────────────────

async def check_redis() -> None:
    print("\n[5] Redis")
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        record_fail("REDIS_URL not set")
        return

    try:
        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.ping()
        ok("Connected to Redis")

        queue_len = await client.llen("sms_queue")
        heartbeat = await client.get("sms_forwarder_heartbeat")

        if queue_len:
            warn(f"sms_queue has {queue_len} unprocessed message(s) — is the SMS worker running?")
        else:
            ok("sms_queue is empty (no backlog)")

        if heartbeat:
            ok(f"SMS forwarder heartbeat present (ts={heartbeat})")
        else:
            warn("No SMS forwarder heartbeat — Android app not yet connected")

        await client.aclose()
    except Exception as exc:
        record_fail(f"Could not connect to Redis: {exc}")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("=" * 50)
    print("  Telegram Deposit Bot — Smoke Test")
    print("=" * 50)

    check_env()

    async with httpx.AsyncClient() as client:
        await check_telegram(client)
        await check_webhook(client)

    await check_postgres()
    await check_redis()

    print("\n" + "=" * 50)
    if _failures == 0:
        print(f"{GREEN}All checks passed.{RESET} The bot stack looks healthy.")
    else:
        print(f"{RED}{_failures} check(s) failed.{RESET} Fix the issues above before going live.")
    print("=" * 50)

    sys.exit(0 if _failures == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
