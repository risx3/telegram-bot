"""
/start handler — phone authentication via Telegram contact sharing.

Flow:
  1. User sends /start
  2. Bot asks user to share their phone number via a contact button
  3. User taps the button → bot receives a Contact message
  4. Bot normalises the number, looks it up in the DB
  5a. Found → links telegram_id, shows main menu
  5b. Not found → informs user, ends conversation
"""

import logging

from telegram import (
    Contact,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    filters,
    MessageHandler,
    ContextTypes,
)

from bot.db import queries
from bot.handlers.common import MAIN_MENU_KEYBOARD, normalise_phone
from bot.handlers.states import AWAITING_CONTACT

logger = logging.getLogger(__name__)

# Keyboard with a single "Share phone" button
_SHARE_CONTACT_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Share my phone number", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Handle /start — request the user's phone number."""
    await update.message.reply_text(
        "Welcome! To use this service, please share your registered phone number.",
        reply_markup=_SHARE_CONTACT_KEYBOARD,
    )
    return AWAITING_CONTACT


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the shared Contact message and authenticate the user."""
    contact: Contact = update.message.contact
    raw_phone = contact.phone_number or ""
    phone = normalise_phone(raw_phone)

    logger.info("Contact received: raw=%s normalised=%s", raw_phone, phone)

    user = await queries.get_user_by_phone(phone)

    if user is None:
        await update.message.reply_text(
            "You are not registered with this service. Please contact support.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    telegram_id = update.effective_user.id
    # Link (or refresh) telegram_id — idempotent on re-starts
    await queries.link_telegram_id(phone, telegram_id)

    await update.message.reply_text(
        f"Welcome back, {user['name'] or 'there'}! What would you like to do?",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END


async def cmd_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the Exit menu button — clear keyboard and end."""
    await update.message.reply_text(
        "Goodbye! Send /start anytime to return.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def build_start_handler() -> ConversationHandler:
    """Return the ConversationHandler for the /start flow."""
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
