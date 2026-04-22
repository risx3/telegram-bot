"""
Deposit / payment confirmation flow.

States:
  DEPOSIT_AWAIT_TXN_ID  — collect UTR / transaction ID, then verify

Phone is collected at /start and read from context.user_data["phone"].
"""

import logging
import os
import re
from pathlib import Path

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.handlers.common import MAIN_MENU_KEYBOARD
from bot.handlers.states import DEPOSIT_AWAIT_TXN_ID
from bot.services.verifier import verify_transaction

logger = logging.getLogger(__name__)

_CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Cancel")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

_TXN_ID_RE = re.compile(r"^[A-Z0-9]{10,22}$", re.IGNORECASE)
_QR_PATH = Path(os.environ.get("QR_CODE_PATH", "assets/qr_code.png"))


# ---------------------------------------------------------------------------
# Entry — show QR and ask for UTR
# ---------------------------------------------------------------------------

async def handle_deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Show QR code and ask for the transaction ID."""
    # Guard: phone must have been collected at /start
    if not context.user_data.get("phone"):
        await update.message.reply_text(
            "Please send /start first to register your phone number.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return ConversationHandler.END

    caption = (
        "Scan this QR code to pay.\n\n"
        "After payment, reply with your UPI Transaction ID "
        "(12-digit UTR or alphanumeric Ref number from your payment app)."
    )

    if _QR_PATH.exists():
        with _QR_PATH.open("rb") as qr_file:
            await update.message.reply_photo(
                photo=qr_file,
                caption=caption,
                reply_markup=_CANCEL_KEYBOARD,
            )
    else:
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
    """Validate UTR, run verification, respond with result."""
    raw_input = (update.message.text or "").strip()
    txn_id = raw_input.upper()

    if not _TXN_ID_RE.match(txn_id):
        await update.message.reply_text(
            "That doesn't look like a valid transaction ID.\n"
            "Expected: 10–22 alphanumeric characters (e.g. 123456789012 or HDFCXXXXXXXX).",
            reply_markup=_CANCEL_KEYBOARD,
        )
        return DEPOSIT_AWAIT_TXN_ID

    phone: str = context.user_data.get("phone", "")

    await update.message.reply_text("Checking your payment...")

    async def _progress(attempt: int, max_attempts: int) -> None:
        try:
            await update.message.reply_text(
                f"Still checking ({attempt}/{max_attempts})... the SMS may be delayed."
            )
        except Exception:
            pass

    result = await verify_transaction(txn_id, phone, progress_callback=_progress)

    if result.success:
        await update.message.reply_text(
            f"Payment of ₹{result.amount:,.2f} confirmed!\n"
            f"Recorded against {phone}.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        logger.info(
            "Payment confirmed: txn_id=%s phone=%s amount=%s",
            txn_id, phone, result.amount,
        )
        return ConversationHandler.END

    if result.already_confirmed:
        await update.message.reply_text(
            "This transaction has already been confirmed.\n"
            "If you believe this is an error, please contact support.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return ConversationHandler.END

    if result.not_found:
        await update.message.reply_text(
            f"We couldn't find a payment with transaction ID {txn_id}.\n"
            "Please check the ID and try again, or contact support.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        logger.warning("Transaction not found: txn_id=%s phone=%s", txn_id, phone)
        return ConversationHandler.END

    await update.message.reply_text(
        result.error_message or "An error occurred. Please contact support.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel mid-flow."""
    await update.message.reply_text(
        "Cancelled. You can start again anytime.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_deposit_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^Confirm Payment$"), handle_deposit_menu),
        ],
        states={
            DEPOSIT_AWAIT_TXN_ID: [
                MessageHandler(filters.Regex(r"^Cancel$"), handle_cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_txn_id),
            ],
        },
        fallbacks=[
            CommandHandler("start", handle_cancel),
            MessageHandler(filters.Regex(r"^Cancel$"), handle_cancel),
            MessageHandler(filters.Regex(r"^Exit$"), handle_cancel),
        ],
        conversation_timeout=1800,
        allow_reentry=True,
    )
