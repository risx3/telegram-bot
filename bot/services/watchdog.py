"""
Watchdog service — monitors SMS forwarder health and sends daily summaries.

Uses APScheduler to run two async jobs:
  - Every 5 minutes: check the Redis heartbeat key.
  - Every day at 09:00 (server local time): send a stats summary to the admin.
"""

import logging
import os
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from bot.db.queries import get_daily_stats
from bot.webhook.sms_receiver import HEARTBEAT_KEY

logger = logging.getLogger(__name__)

_HEARTBEAT_MAX_AGE_SECONDS = 600  # 10 minutes
_ALERT_ACTIVE_KEY = "watchdog:alert_active"
_ALERT_COOLDOWN_SECONDS = 3600  # re-alert at most once per hour if still down


async def _check_heartbeat(bot: Bot, redis_url: str, admin_id: int) -> None:
    """
    Check Redis for the SMS forwarder heartbeat.
    Alert the admin if missing or stale, but only once per outage:
    - Sends an alert when the forwarder first goes down.
    - Stays silent until the forwarder recovers, then resets.
    - If it stays down for over an hour, sends one reminder and resets the cooldown.
    """
    redis_client = None
    try:
        redis_client = aioredis.from_url(redis_url, decode_responses=True)
        raw = await redis_client.get(HEARTBEAT_KEY)

        now = int(time.time())

        # --- Forwarder is healthy ---
        if raw is not None:
            last_seen_ts = int(raw)
            age = now - last_seen_ts
            if age <= _HEARTBEAT_MAX_AGE_SECONDS:
                was_alerting = await redis_client.get(_ALERT_ACTIVE_KEY)
                if was_alerting:
                    # Forwarder recovered — clear alert state and notify admin
                    await redis_client.delete(_ALERT_ACTIVE_KEY)
                    logger.info("Watchdog: forwarder recovered.")
                    await bot.send_message(
                        chat_id=admin_id,
                        text="SMS Forwarder is back online.",
                    )
                else:
                    logger.debug("Watchdog: heartbeat OK (age=%ds).", age)
                return

        # --- Forwarder is down or stale ---
        alert_active = await redis_client.get(_ALERT_ACTIVE_KEY)
        if alert_active:
            # Already alerted — stay silent until cooldown expires or forwarder recovers
            logger.debug("Watchdog: forwarder still down, alert already sent (cooldown active).")
            return

        # First alert (or cooldown expired) — send message and set cooldown
        if raw is None:
            logger.warning("Watchdog: heartbeat key missing in Redis.")
            message = (
                "Send /start anytime to return."
            )
        else:
            last_seen_ts = int(raw)
            last_seen_str = datetime.fromtimestamp(last_seen_ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
            logger.warning("Watchdog: heartbeat is stale (%ds old).", now - last_seen_ts)
            message = (
                "Send /start anytime to return."
            )

        await bot.send_message(chat_id=admin_id, text=message)
        # Set cooldown — expires after 1 hour so a reminder fires if still down
        await redis_client.set(_ALERT_ACTIVE_KEY, "1", ex=_ALERT_COOLDOWN_SECONDS)

    except Exception:
        logger.exception("Watchdog: error checking heartbeat.")
    finally:
        if redis_client:
            await redis_client.aclose()


async def _daily_summary(bot: Bot, admin_id: int) -> None:
    """Send a daily stats summary to the admin."""
    try:
        stats = await get_daily_stats()
        text = (
            "Daily Summary\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"SMSes received (last 24h): {stats['sms_received']}\n"
            f"Transactions credited:     {stats['credited']}\n"
            f"Manual reviews pending:    {stats['manual_review']}\n"
            "━━━━━━━━━━━━━━━━━━"
        )
        await bot.send_message(chat_id=admin_id, text=text)
        logger.info("Daily summary sent to admin.")
    except Exception:
        logger.exception("Watchdog: error sending daily summary.")


def build_scheduler(bot: Bot, redis_url: str, admin_id: int) -> AsyncIOScheduler:
    """
    Create and return a configured APScheduler instance.

    Args:
        bot:       The Telegram Bot instance used to send alerts.
        redis_url: Redis DSN for heartbeat checks.
        admin_id:  Telegram user ID of the admin to notify.
    """
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        _check_heartbeat,
        trigger="interval",
        minutes=5,
        id="heartbeat_check",
        kwargs={"bot": bot, "redis_url": redis_url, "admin_id": admin_id},
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _daily_summary,
        trigger="cron",
        hour=9,
        minute=0,
        id="daily_summary",
        kwargs={"bot": bot, "admin_id": admin_id},
        max_instances=1,
        coalesce=True,
    )

    return scheduler
