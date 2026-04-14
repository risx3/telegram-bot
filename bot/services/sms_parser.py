"""
SMS parser for Indian bank credit notifications.

Supports: HDFC, ICICI, SBI, Axis, Kotak, plus a generic fallback.
Each parser returns a dict with keys: txn_id (str), amount (float), bank (str)
or None if the SMS does not match.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_amount(raw: str) -> float:
    """Strip commas and convert to float."""
    return float(raw.replace(",", "").strip())


def _clean_txn_id(raw: str) -> str:
    """Uppercase and strip whitespace from a transaction ID."""
    return raw.strip().upper()


# ---------------------------------------------------------------------------
# Bank-specific patterns
# ---------------------------------------------------------------------------

# HDFC: "Rs.500.00 credited to your a/c XX1234 on 14-04-26. Info: UPI-name. Ref No 123456789012. -HDFC Bank"
_HDFC_PATTERN = re.compile(
    r"Rs\.?([\d,]+\.?\d*)\s+credited.*?Ref\s*No\s*([A-Z0-9]{10,22})",
    re.IGNORECASE | re.DOTALL,
)

# ICICI: "Rs.500.00 credited to Acct XX1234 on 14-Apr-26. UPI Ref:123456789012. -ICICI Bank"
_ICICI_PATTERN = re.compile(
    r"Rs\.?([\d,]+\.?\d*)\s+credited.*?UPI\s*Ref:?\s*([A-Z0-9]{10,22})",
    re.IGNORECASE | re.DOTALL,
)

# SBI: "Your a/c XXXX1234 credited by Rs.500.00 on 14Apr26 by UPI. UTR No 123456789012."
_SBI_PATTERN = re.compile(
    r"credited\s+by\s+Rs\.?([\d,]+\.?\d*).*?UTR\s*No\s*([A-Z0-9]{10,22})",
    re.IGNORECASE | re.DOTALL,
)

# Axis: "INR 500.00 credited to your a/c linked to VPA xxx@axis on 14-04-2026. UPI Ref No 123456789012 -Axis Bank"
_AXIS_PATTERN = re.compile(
    r"INR\s+([\d,]+\.?\d*)\s+credited.*?UPI\s*Ref\s*No\s*([A-Z0-9]{10,22})",
    re.IGNORECASE | re.DOTALL,
)

# Kotak: "Rs.500.00 credited to your Kotak Bank A/c XXXXXXXX1234 by UPI ref 123456789012 on 14-Apr-2026."
_KOTAK_PATTERN = re.compile(
    r"Rs\.?([\d,]+\.?\d*)\s+credited.*?UPI\s*ref\s*([A-Z0-9]{10,22})",
    re.IGNORECASE | re.DOTALL,
)

# Generic fallback: any Rs amount + any 12-digit numeric ref
_GENERIC_AMOUNT = re.compile(r"Rs\.?\s*([\d,]+\.?\d*)", re.IGNORECASE)
_GENERIC_TXN = re.compile(r"\b(\d{12})\b")

_BANK_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("HDFC", _HDFC_PATTERN),
    ("ICICI", _ICICI_PATTERN),
    ("SBI", _SBI_PATTERN),
    ("Axis", _AXIS_PATTERN),
    ("Kotak", _KOTAK_PATTERN),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse(sms_body: str) -> Optional[dict]:
    """
    Parse a bank credit SMS and extract transaction details.

    Args:
        sms_body: Raw SMS text received from the Android forwarder.

    Returns:
        A dict with keys ``txn_id`` (str), ``amount`` (float), ``bank`` (str),
        or ``None`` if the SMS cannot be parsed.
    """
    if not sms_body:
        return None

    for bank_name, pattern in _BANK_PATTERNS:
        match = pattern.search(sms_body)
        if match:
            try:
                amount = _clean_amount(match.group(1))
                txn_id = _clean_txn_id(match.group(2))
                logger.debug("Parsed %s SMS: txn_id=%s amount=%.2f", bank_name, txn_id, amount)
                return {"txn_id": txn_id, "amount": amount, "bank": bank_name}
            except (IndexError, ValueError) as exc:
                logger.warning("Pattern matched for %s but extraction failed: %s", bank_name, exc)
                continue

    # Generic fallback
    amount_match = _GENERIC_AMOUNT.search(sms_body)
    txn_match = _GENERIC_TXN.search(sms_body)
    if amount_match and txn_match:
        try:
            amount = _clean_amount(amount_match.group(1))
            txn_id = _clean_txn_id(txn_match.group(1))
            logger.debug("Parsed generic SMS: txn_id=%s amount=%.2f", txn_id, amount)
            return {"txn_id": txn_id, "amount": amount, "bank": "Unknown"}
        except ValueError as exc:
            logger.warning("Generic fallback extraction failed: %s", exc)

    logger.warning("Could not parse SMS (first 80 chars): %.80s", sms_body)
    return None
