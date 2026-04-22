"""
Transaction verifier service.

Looks up a submitted UTR in the transactions table, polls if not yet arrived,
guards against double-confirm, and records the user's phone number.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional

from bot.db.queries import confirm_transaction, get_transaction_by_txn_id

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30   # seconds between retries
_MAX_ATTEMPTS = 6     # 6 × 30s = 3 minutes total


class VerificationResult:
    def __init__(
        self,
        success: bool,
        amount: Optional[Decimal] = None,
        already_confirmed: bool = False,
        not_found: bool = False,
        error_message: Optional[str] = None,
    ) -> None:
        self.success = success
        self.amount = amount
        self.already_confirmed = already_confirmed
        self.not_found = not_found
        self.error_message = error_message


async def verify_transaction(
    txn_id: str,
    phone: str,
    progress_callback=None,
) -> VerificationResult:
    """
    Verify a submitted transaction ID and link it to the user's phone number.

    Args:
        txn_id:            UTR / UPI reference number submitted by the user.
        phone:             Normalised phone number of the user.
        progress_callback: Async callable (attempt, max_attempts) for interim messages.

    Returns:
        A VerificationResult describing the outcome.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        txn = await get_transaction_by_txn_id(txn_id)

        if txn is not None:
            if txn["confirmed"]:
                logger.warning("Double-confirm attempt: txn_id=%s phone=%s", txn_id, phone)
                return VerificationResult(success=False, already_confirmed=True)

            amount: Decimal = txn["amount"]
            if amount <= 0:
                logger.error("Transaction %s has non-positive amount %s", txn_id, amount)
                return VerificationResult(
                    success=False,
                    error_message="Transaction amount is invalid. Please contact support.",
                )

            await confirm_transaction(txn_id, phone)
            logger.info(
                "Transaction confirmed: txn_id=%s phone=%s amount=%s",
                txn_id, phone, amount,
            )
            return VerificationResult(success=True, amount=amount)

        if attempt < _MAX_ATTEMPTS:
            logger.debug(
                "txn_id=%s not found (attempt %d/%d), waiting %ds",
                txn_id, attempt, _MAX_ATTEMPTS, _POLL_INTERVAL,
            )
            if progress_callback:
                await progress_callback(attempt, _MAX_ATTEMPTS)
            await asyncio.sleep(_POLL_INTERVAL)

    logger.warning("Transaction not found after %d attempts: txn_id=%s", _MAX_ATTEMPTS, txn_id)
    return VerificationResult(success=False, not_found=True)
