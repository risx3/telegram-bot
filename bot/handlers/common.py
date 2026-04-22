"""Shared utilities used across bot handlers."""

import logging
import re

from telegram import ReplyKeyboardMarkup, KeyboardButton

logger = logging.getLogger(__name__)

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Confirm Payment")],
     [KeyboardButton("Exit")]],
    resize_keyboard=True,
    one_time_keyboard=False,
)


def normalise_phone(raw: str) -> str:
    """Normalise a phone number to E.164 format with +91 prefix."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if len(digits) == 13 and digits.startswith("091"):
        return f"+{digits[1:]}"
    return f"+{digits}" if not raw.startswith("+") else raw
