"""
/start handler — collect phone number via contact share, then show main menu.

Flow:
  1. User sends /start
  2. Bot requests phone number via contact button
  3. User taps button → bot normalises and stores phone in user_data
  4. Main menu shown — phone is available for the deposit flow
"""

import logging

from telegram import Contact, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.handlers.common import MAIN_MENU_KEYBOARD, normalise_phone
from bot.handlers.states import AWAITING_CONTACT

logger = logging.getLogger(__name__)

_SHARE_CONTACT_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Share my phone number", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Handle /start — request the user's phone number."""
    await update.message.reply_text(
        "Welcome! Please share your phone number to continue.",
        reply_markup=_SHARE_CONTACT_KEYBOARD,
    )
    return AWAITING_CONTACT


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the shared phone number and show the main menu."""
    contact: Contact = update.message.contact
    raw_phone = contact.phone_number or ""
    phone = normalise_phone(raw_phone)

    logger.info("Contact received: raw=%s normalised=%s", raw_phone, phone)
    context.user_data["phone"] = phone

    await update.message.reply_text(
        f"Thanks! Your number {phone} has been saved.\nWhat would you like to do?",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END


async def cmd_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Exit — clear keyboard."""
    await update.message.reply_text(
        "Goodbye! Send /start anytime to return.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def build_start_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            AWAITING_CONTACT: [
                MessageHandler(filters.CONTACT, handle_contact),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(r"^Exit$"), cmd_exit),
        ],
        allow_reentry=True,
    )
