"""
Deposit flow handler.

States:
  DEPOSIT_SHOW_QR       — send QR code photo with instructions
  DEPOSIT_AWAIT_TXN_ID  — accept UTR/transaction ID from user, verify, credit

The ConversationHandler times out after 30 minutes of inactivity.
"""

import logging
import os
import random
import re
import string
from decimal import Decimal
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.db import queries
from bot.handlers.common import MAIN_MENU_KEYBOARD, require_registered_user
from bot.handlers.states import DEPOSIT_AWAIT_TXN_ID, DEPOSIT_SHOW_QR
from bot.services.verifier import verify_transaction

logger = logging.getLogger(__name__)

_CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Cancel Deposit")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

# UTR / UPI ref pattern: 10–22 alphanumeric characters (case-insensitive)
_TXN_ID_RE = re.compile(r"^[A-Z0-9]{10,22}$", re.IGNORECASE)

_QR_PATH = Path(os.environ.get("QR_CODE_PATH", "assets/qr_code.png"))


def _generate_session_ref(user_id: int) -> str:
    """Generate a unique deposit session reference, e.g. DEP-USER42-8X3K."""
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"DEP-USER{user_id}-{suffix}"


# ---------------------------------------------------------------------------
# State: DEPOSIT_SHOW_QR
# ---------------------------------------------------------------------------

async def handle_deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Entry point — user taps the Deposit button.
    Create a deposit session and send the QR code.
    """
    user = await require_registered_user(update, context)
    if user is None:
        return ConversationHandler.END

    session_ref = _generate_session_ref(user["id"])
    await queries.create_deposit_session(user["id"], session_ref)
    context.user_data["deposit_session_ref"] = session_ref
    context.user_data["deposit_user_id"] = user["id"]

    caption = (
        f"Scan this QR code to pay.\n\n"
        f"Your session reference: {session_ref}\n"
        "(Add this as the UPI remarks/note if your app supports it.)\n\n"
        "After payment, reply with your UPI Transaction ID "
        "(12-digit UTR or alphanumeric Ref number from your payment app).\n\n"
        "Type 'Cancel Deposit' at any time to abort."
    )

    if _QR_PATH.exists():
        with _QR_PATH.open("rb") as qr_file:
            await update.message.reply_photo(
                photo=qr_file,
                caption=caption,
                reply_markup=_CANCEL_KEYBOARD,
            )
    else:
        # QR image not placed yet — send text-only fallback
        logger.warning("QR code image not found at %s", _QR_PATH)
        await update.message.reply_text(
            "[QR code image not configured — place your UPI QR at assets/qr_code.png]\n\n"
            + caption,
            reply_markup=_CANCEL_KEYBOARD,
        )

    return DEPOSIT_AWAIT_TXN_ID


# ---------------------------------------------------------------------------
# State: DEPOSIT_AWAIT_TXN_ID
# ---------------------------------------------------------------------------

async def handle_txn_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Validate the transaction ID and attempt verification.
    """
    raw_input = (update.message.text or "").strip()
    txn_id = raw_input.upper()

    if not _TXN_ID_RE.match(txn_id):
        await update.message.reply_text(
            "That doesn't look like a valid transaction ID.\n"
            "Please check your payment app and try again.\n"
            "(Expected: 10–22 alphanumeric characters, e.g. 123456789012 or HDFCXXXXXXXX)",
            reply_markup=_CANCEL_KEYBOARD,
        )
        return DEPOSIT_AWAIT_TXN_ID

    user_id: int = context.user_data.get("deposit_user_id")
    session_ref: str = context.user_data.get("deposit_session_ref", "")
    telegram_id = update.effective_user.id

    await update.message.reply_text("Checking your payment... ⏳")

    async def _progress(attempt: int, max_attempts: int) -> None:
        try:
            await update.message.reply_text(
                f"Still checking ({attempt}/{max_attempts})... the SMS may be delayed."
            )
        except Exception:
            pass

    result = await verify_transaction(txn_id, user_id, progress_callback=_progress)

    if result.success:
        amount: Decimal = result.amount
        new_balance: Decimal = result.new_balance
        await queries.complete_deposit_session(session_ref)
        await update.message.reply_text(
            f"Payment of ₹{amount:,.2f} confirmed!\n"
            f"Your new balance is ₹{new_balance:,.2f}.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        logger.info(
            "Deposit credited: telegram_id=%s txn_id=%s amount=%s",
            telegram_id, txn_id, amount,
        )
        return ConversationHandler.END

    if result.already_credited:
        await update.message.reply_text(
            "This transaction has already been used.\n"
            "If you believe this is an error, please contact support.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return ConversationHandler.END

    if result.not_found:
        # Log manual review with the actual telegram_id
        await queries.insert_manual_review(telegram_id, txn_id)
        await update.message.reply_text(
            "We couldn't verify your payment automatically.\n"
            f"Your transaction ID ({txn_id}) has been saved for manual review.\n"
            "Please contact support with this ID.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        logger.warning(
            "Manual review logged: telegram_id=%s txn_id=%s", telegram_id, txn_id
        )
        return ConversationHandler.END

    # Generic error
    await update.message.reply_text(
        result.error_message or "An error occurred. Please contact support.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END


async def handle_cancel_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the deposit mid-flow."""
    await update.message.reply_text(
        "Deposit cancelled. You can start a new deposit anytime.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    context.user_data.pop("deposit_session_ref", None)
    context.user_data.pop("deposit_user_id", None)
    return ConversationHandler.END


async def handle_deposit_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Called when the conversation times out due to inactivity."""
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "Your deposit session expired due to inactivity. "
            "Please start again from the main menu.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_deposit_handler() -> ConversationHandler:
    """Return the ConversationHandler for the deposit flow."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^Deposit$"), handle_deposit_menu),
        ],
        states={
            DEPOSIT_AWAIT_TXN_ID: [
                MessageHandler(
                    filters.Regex(r"^Cancel Deposit$"), handle_cancel_deposit
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_txn_id),
            ],
        },
        fallbacks=[
            CommandHandler("start", handle_cancel_deposit),
            MessageHandler(filters.Regex(r"^Cancel Deposit$"), handle_cancel_deposit),
            MessageHandler(filters.Regex(r"^Exit$"), handle_cancel_deposit),
        ],
        conversation_timeout=1800,  # 30 minutes
        allow_reentry=True,
    )
