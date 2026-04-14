"""All database query functions for the deposit bot."""

import logging
from decimal import Decimal
from typing import Optional
import asyncpg

from .connection import get_pool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def get_user_by_phone(phone: str) -> Optional[asyncpg.Record]:
    """Fetch a user record by normalised phone number."""
    pool = get_pool()
    try:
        return await pool.fetchrow(
            "SELECT * FROM users WHERE phone = $1", phone
        )
    except Exception:
        logger.exception("Error fetching user by phone: %s", phone)
        raise


async def get_user_by_telegram_id(telegram_id: int) -> Optional[asyncpg.Record]:
    """Fetch a user record by Telegram user ID."""
    pool = get_pool()
    try:
        return await pool.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )
    except Exception:
        logger.exception("Error fetching user by telegram_id: %s", telegram_id)
        raise


async def link_telegram_id(phone: str, telegram_id: int) -> None:
    """Associate a Telegram ID with an existing user account."""
    pool = get_pool()
    try:
        await pool.execute(
            "UPDATE users SET telegram_id = $1 WHERE phone = $2",
            telegram_id, phone,
        )
    except Exception:
        logger.exception("Error linking telegram_id %s to phone %s", telegram_id, phone)
        raise


async def get_user_balance(user_id: int) -> Decimal:
    """Return the current balance for a user."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT balance FROM users WHERE id = $1", user_id)
    if row is None:
        raise ValueError(f"User {user_id} not found")
    return row["balance"]


async def credit_user_balance(user_id: int, amount: Decimal) -> Decimal:
    """Add amount to user balance and return the new balance."""
    pool = get_pool()
    try:
        row = await pool.fetchrow(
            """
            UPDATE users
            SET balance = balance + $1
            WHERE id = $2
            RETURNING balance
            """,
            amount, user_id,
        )
        if row is None:
            raise ValueError(f"User {user_id} not found")
        return row["balance"]
    except Exception:
        logger.exception("Error crediting balance for user_id=%s, amount=%s", user_id, amount)
        raise


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

async def insert_received_transaction(
    txn_id: str,
    amount: Decimal,
    sms_raw: str,
) -> bool:
    """
    Insert a parsed SMS transaction.  Returns True if inserted, False if
    duplicate (ON CONFLICT DO NOTHING).
    """
    pool = get_pool()
    try:
        result = await pool.execute(
            """
            INSERT INTO received_transactions (txn_id, amount, sms_raw)
            VALUES ($1, $2, $3)
            ON CONFLICT (txn_id) DO NOTHING
            """,
            txn_id, amount, sms_raw,
        )
        # result is e.g. "INSERT 0 1" or "INSERT 0 0"
        return result.endswith("1")
    except Exception:
        logger.exception("Error inserting received_transaction txn_id=%s", txn_id)
        raise


async def get_transaction_by_txn_id(txn_id: str) -> Optional[asyncpg.Record]:
    """Fetch a received_transaction row by txn_id."""
    pool = get_pool()
    try:
        return await pool.fetchrow(
            "SELECT * FROM received_transactions WHERE txn_id = $1", txn_id
        )
    except Exception:
        logger.exception("Error fetching transaction txn_id=%s", txn_id)
        raise


async def mark_transaction_credited(txn_id: str, user_id: int) -> None:
    """Mark a transaction as credited and associate it with a user."""
    pool = get_pool()
    try:
        await pool.execute(
            """
            UPDATE received_transactions
            SET credited = TRUE, matched_user_id = $1
            WHERE txn_id = $2
            """,
            user_id, txn_id,
        )
    except Exception:
        logger.exception("Error marking txn_id=%s as credited", txn_id)
        raise


# ---------------------------------------------------------------------------
# Deposit sessions
# ---------------------------------------------------------------------------

async def create_deposit_session(user_id: int, session_ref: str) -> asyncpg.Record:
    """Create a new deposit session and return the record."""
    pool = get_pool()
    try:
        return await pool.fetchrow(
            """
            INSERT INTO deposit_sessions (user_id, session_ref)
            VALUES ($1, $2)
            RETURNING *
            """,
            user_id, session_ref,
        )
    except Exception:
        logger.exception("Error creating deposit session for user_id=%s", user_id)
        raise


async def expire_old_sessions() -> int:
    """Mark pending sessions past their expiry as expired. Returns count updated."""
    pool = get_pool()
    result = await pool.execute(
        """
        UPDATE deposit_sessions
        SET status = 'expired'
        WHERE status = 'pending' AND expires_at < NOW()
        """
    )
    count = int(result.split()[-1])
    return count


async def complete_deposit_session(session_ref: str) -> None:
    """Mark a deposit session as completed."""
    pool = get_pool()
    await pool.execute(
        "UPDATE deposit_sessions SET status = 'completed' WHERE session_ref = $1",
        session_ref,
    )


# ---------------------------------------------------------------------------
# Manual review
# ---------------------------------------------------------------------------

async def insert_manual_review(telegram_id: int, txn_id: str) -> None:
    """Log an unmatched transaction for manual review."""
    pool = get_pool()
    try:
        await pool.execute(
            """
            INSERT INTO manual_review (telegram_id, txn_id)
            VALUES ($1, $2)
            """,
            telegram_id, txn_id,
        )
    except Exception:
        logger.exception(
            "Error inserting manual review for telegram_id=%s, txn_id=%s",
            telegram_id, txn_id,
        )
        raise


# ---------------------------------------------------------------------------
# Watchdog / stats
# ---------------------------------------------------------------------------

async def get_daily_stats() -> dict:
    """Return yesterday's stats for the admin watchdog summary."""
    pool = get_pool()
    yesterday_sms = await pool.fetchval(
        """
        SELECT COUNT(*) FROM received_transactions
        WHERE received_at >= NOW() - INTERVAL '1 day'
        """
    )
    yesterday_credited = await pool.fetchval(
        """
        SELECT COUNT(*) FROM received_transactions
        WHERE credited = TRUE AND received_at >= NOW() - INTERVAL '1 day'
        """
    )
    pending_manual = await pool.fetchval(
        "SELECT COUNT(*) FROM manual_review WHERE resolved = FALSE"
    )
    return {
        "sms_received": yesterday_sms,
        "credited": yesterday_credited,
        "manual_review": pending_manual,
    }
