"""All database query functions."""

import logging
from decimal import Decimal
from typing import Optional
import asyncpg

from .connection import get_pool

logger = logging.getLogger(__name__)


async def insert_transaction(
    txn_id: str,
    amount: Decimal,
    bank: str,
    sms_raw: str,
) -> bool:
    """Insert a parsed SMS transaction. Returns True if inserted, False if duplicate."""
    pool = get_pool()
    try:
        result = await pool.execute(
            """
            INSERT INTO transactions (txn_id, amount, bank, sms_raw)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (txn_id) DO NOTHING
            """,
            txn_id, amount, bank, sms_raw,
        )
        return result.endswith("1")
    except Exception:
        logger.exception("Error inserting transaction txn_id=%s", txn_id)
        raise


async def get_transaction_by_txn_id(txn_id: str) -> Optional[asyncpg.Record]:
    """Fetch a transaction row by txn_id."""
    pool = get_pool()
    try:
        return await pool.fetchrow(
            "SELECT * FROM transactions WHERE txn_id = $1", txn_id
        )
    except Exception:
        logger.exception("Error fetching transaction txn_id=%s", txn_id)
        raise


async def confirm_transaction(txn_id: str, phone: str) -> None:
    """Mark a transaction as confirmed and record the user's phone number."""
    pool = get_pool()
    try:
        await pool.execute(
            """
            UPDATE transactions
            SET phone = $1, confirmed = TRUE, updated_at = NOW()
            WHERE txn_id = $2
            """,
            phone, txn_id,
        )
    except Exception:
        logger.exception("Error confirming transaction txn_id=%s", txn_id)
        raise


async def get_daily_stats() -> dict:
    """Return last 24h stats for the admin watchdog summary."""
    pool = get_pool()
    sms_received = await pool.fetchval(
        "SELECT COUNT(*) FROM transactions WHERE received_at >= NOW() - INTERVAL '1 day'"
    )
    confirmed = await pool.fetchval(
        """
        SELECT COUNT(*) FROM transactions
        WHERE confirmed = TRUE AND updated_at >= NOW() - INTERVAL '1 day'
        """
    )
    return {
        "sms_received": sms_received,
        "confirmed": confirmed,
    }
