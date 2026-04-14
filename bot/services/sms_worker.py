"""
Async SMS worker.

Continuously pops SMS messages from the Redis ``sms_queue`` list, parses
them with ``sms_parser``, and inserts valid transactions into PostgreSQL.
Unparse-able messages are written to a local error log file.
"""

import asyncio
import logging
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import redis.asyncio as aioredis

from bot.db.queries import insert_received_transaction
from bot.services import sms_parser

logger = logging.getLogger(__name__)

SMS_QUEUE_KEY = "sms_queue"
_BLPOP_TIMEOUT = 5  # seconds — allows the loop to check for shutdown signals


def _write_parse_error(sms_body: str) -> None:
    """Append a failed parse to the error log file."""
    log_dir = Path(os.environ.get("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    error_log = log_dir / "sms_parse_errors.log"
    ts = datetime.utcnow().isoformat()
    with error_log.open("a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] PARSE_ERROR: {sms_body!r}\n")


async def run_worker(redis_url: str, stop_event: asyncio.Event) -> None:
    """
    Main SMS worker coroutine.

    Connects to Redis and loops until ``stop_event`` is set.  On each iteration
    it blocks for up to ``_BLPOP_TIMEOUT`` seconds waiting for a new SMS.

    Args:
        redis_url:  Redis DSN, e.g. ``redis://localhost:6379/0``.
        stop_event: Set this event to signal the worker to shut down cleanly.
    """
    redis_client: aioredis.Redis | None = None
    retry_delay = 1.0

    while not stop_event.is_set():
        try:
            if redis_client is None:
                redis_client = aioredis.from_url(redis_url, decode_responses=True)
                logger.info("SMS worker connected to Redis.")
                retry_delay = 1.0  # reset backoff on successful connect

            result = await redis_client.blpop(SMS_QUEUE_KEY, timeout=_BLPOP_TIMEOUT)
            if result is None:
                # Timeout — loop back and check stop_event
                continue

            _key, sms_body = result
            logger.debug("SMS worker received message (first 80 chars): %.80s", sms_body)

            parsed = sms_parser.parse(sms_body)
            if parsed is None:
                logger.warning("SMS could not be parsed, logging to error file.")
                _write_parse_error(sms_body)
                continue

            txn_id = parsed["txn_id"]
            amount = Decimal(str(parsed["amount"]))
            inserted = await insert_received_transaction(txn_id, amount, sms_body)
            if inserted:
                logger.info(
                    "Transaction inserted: txn_id=%s amount=%.2f bank=%s",
                    txn_id, amount, parsed["bank"],
                )
            else:
                logger.info("Duplicate transaction ignored: txn_id=%s", txn_id)

        except (aioredis.ConnectionError, aioredis.TimeoutError) as exc:
            logger.error("Redis connection error in SMS worker: %s — retrying in %.1fs", exc, retry_delay)
            redis_client = None
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60.0)  # exponential backoff, cap at 60s

        except asyncio.CancelledError:
            break

        except Exception:
            logger.exception("Unexpected error in SMS worker.")
            await asyncio.sleep(2)

    if redis_client:
        await redis_client.aclose()

    logger.info("SMS worker stopped.")
