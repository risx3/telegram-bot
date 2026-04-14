"""
View Profile handler.

Fetches the user record from the DB and displays name, phone, balance,
and account status.  Falls back gracefully if the user is not found.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from bot.handlers.common import BACK_KEYBOARD, MAIN_MENU_KEYBOARD, require_registered_user

logger = logging.getLogger(__name__)


async def handle_view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Display the user's profile information.

    Triggered when the user taps the "View Profile" menu button.
    """
    user = await require_registered_user(update, context)
    if user is None:
        return

    name = user["name"] or "N/A"
    phone = user["phone"]
    balance = user["balance"]
    created_at = user["created_at"].strftime("%d %b %Y") if user["created_at"] else "N/A"

    text = (
        "Your Profile\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"Name:      {name}\n"
        f"Phone:     {phone}\n"
        f"Balance:   ₹{balance:,.2f}\n"
        f"Member since: {created_at}\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    await update.message.reply_text(text, reply_markup=BACK_KEYBOARD)
    logger.debug("Profile shown for telegram_id=%s", update.effective_user.id)


async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to the main menu."""
    await update.message.reply_text(
        "What would you like to do?",
        reply_markup=MAIN_MENU_KEYBOARD,
    )


def build_profile_handlers() -> list:
    """Return message handlers for the profile feature."""
    return [
        MessageHandler(filters.Regex(r"^View Profile$"), handle_view_profile),
        MessageHandler(filters.Regex(r"^Back to Menu$"), handle_back_to_menu),
    ]
