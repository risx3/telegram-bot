"""
Transaction verifier service.

Looks up a submitted UTR/UPI transaction ID in the ``received_transactions``
table, polls if necessary, validates the amount, guards against double-credit,
and credits the user's balance.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional

from bot.db.queries import credit_transaction_and_balance, get_transaction_by_txn_id

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30   # seconds between retries
_MAX_ATTEMPTS = 6     # 6 × 30s = 3 minutes total


class VerificationResult:
    """Encapsulates the outcome of a verification attempt."""

    def __init__(
        self,
        success: bool,
        amount: Optional[Decimal] = None,
        new_balance: Optional[Decimal] = None,
        already_credited: bool = False,
        not_found: bool = False,
        error_message: Optional[str] = None,
    ) -> None:
        self.success = success
        self.amount = amount
        self.new_balance = new_balance
        self.already_credited = already_credited
        self.not_found = not_found
        self.error_message = error_message


async def verify_transaction(
    txn_id: str,
    user_id: int,
    progress_callback=None,
) -> VerificationResult:
    """
    Verify a submitted transaction ID and credit the user if valid.

    Args:
        txn_id:            The UTR / UPI reference number submitted by the user.
        user_id:           The DB ``users.id`` of the submitting user.
        progress_callback: An async callable ``(attempt: int, max_attempts: int) -> None``
                           called after each failed lookup to send interim messages.

    Returns:
        A :class:`VerificationResult` describing the outcome.
    """
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        txn = await get_transaction_by_txn_id(txn_id)

        if txn is not None:
            # Found — check for double-credit.
            # Guard on both the credited flag AND matched_user_id so a transaction
            # that is already associated with any user cannot be reused.
            if txn["credited"] or txn["matched_user_id"] is not None:
                logger.warning("Double-credit attempt: txn_id=%s user_id=%s", txn_id, user_id)
                return VerificationResult(success=False, already_credited=True)

            amount: Decimal = txn["amount"]
            if amount <= 0:
                logger.error("Transaction %s has non-positive amount %s", txn_id, amount)
                return VerificationResult(
                    success=False,
                    error_message="Transaction amount is invalid. Please contact support.",
                )

            # Atomically mark credited AND credit balance inside a single DB transaction.
            new_balance = await credit_transaction_and_balance(txn_id, user_id, amount)

            logger.info(
                "Transaction credited: txn_id=%s user_id=%s amount=%s new_balance=%s",
                txn_id, user_id, amount, new_balance,
            )
            return VerificationResult(success=True, amount=amount, new_balance=new_balance)

        # Not found yet
        if attempt < _MAX_ATTEMPTS:
            logger.debug(
                "txn_id=%s not found (attempt %d/%d), waiting %ds",
                txn_id, attempt, _MAX_ATTEMPTS, _POLL_INTERVAL,
            )
            if progress_callback:
                await progress_callback(attempt, _MAX_ATTEMPTS)
            await asyncio.sleep(_POLL_INTERVAL)

    # Exhausted all retries — caller is responsible for logging manual review
    # since only the caller knows the user's telegram_id.
    logger.warning("Transaction not found after %d attempts: txn_id=%s", _MAX_ATTEMPTS, txn_id)
    return VerificationResult(success=False, not_found=True)
