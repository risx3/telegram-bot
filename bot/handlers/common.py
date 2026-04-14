"""Shared utilities used across bot handlers."""

import logging
import re
from typing import Optional

import asyncpg
from telegram import ReplyKeyboardMarkup, KeyboardButton

from bot.db import queries

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Menu keyboard
# ---------------------------------------------------------------------------

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("View Profile"), KeyboardButton("Deposit")],
     [KeyboardButton("Exit")]],
    resize_keyboard=True,
    one_time_keyboard=False,
)

BACK_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Back to Menu")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


# ---------------------------------------------------------------------------
# Phone normalisation
# ---------------------------------------------------------------------------

def normalise_phone(raw: str) -> str:
    """
    Normalise a phone number to E.164 format with +91 prefix.

    Strips spaces, dashes, parentheses.  Adds +91 if no country code present.
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if len(digits) == 13 and digits.startswith("091"):
        return f"+{digits[1:]}"
    # Return with + prefix if already looks like full number
    return f"+{digits}" if not raw.startswith("+") else raw


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def get_user_by_telegram_id(telegram_id: int) -> Optional[asyncpg.Record]:
    """Convenience wrapper used by multiple handlers."""
    try:
        return await queries.get_user_by_telegram_id(telegram_id)
    except Exception:
        logger.exception("Failed to fetch user for telegram_id=%s", telegram_id)
        return None


async def require_registered_user(update, context) -> Optional[asyncpg.Record]:
    """
    Fetch the user for the current update.  If not found, send an error
    message and return None.  Handlers should return immediately on None.
    """
    telegram_id = update.effective_user.id
    user = await get_user_by_telegram_id(telegram_id)
    if user is None:
        await update.effective_message.reply_text(
            "You are not registered with this service. Please contact support."
        )
    return user
