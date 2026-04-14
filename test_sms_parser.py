"""
Tests for bot/services/sms_parser.py

Run with:  pytest test_sms_parser.py -v
"""

import pytest
from bot.services.sms_parser import parse


# ---------------------------------------------------------------------------
# Fixtures — sample SMS strings
# ---------------------------------------------------------------------------

HDFC_CREDIT = (
    "Rs.500.00 credited to your a/c XX1234 on 14-04-26 by UPI. "
    "Info: JOHN-UPI. Ref No 123456789012. -HDFC Bank"
)

HDFC_CREDIT_COMMA_AMOUNT = (
    "Rs.1,500.00 credited to your a/c XX5678 on 14-04-26 by UPI. "
    "Info: JANE-UPI. Ref No 987654321098. -HDFC Bank"
)

ICICI_CREDIT = (
    "Rs.250.50 credited to Acct XX9876 on 14-Apr-26. "
    "UPI Ref:ICICI123456789. -ICICI Bank"
)

SBI_CREDIT = (
    "Your a/c XXXX1234 credited by Rs.750.00 on 14Apr26 by UPI. "
    "UTR No 112233445566."
)

AXIS_CREDIT = (
    "INR 1,000.00 credited to your a/c linked to VPA pay@axisbank on 14-04-2026. "
    "UPI Ref No AXIS9876543210 -Axis Bank"
)

KOTAK_CREDIT = (
    "Rs.300.00 credited to your Kotak Bank A/c XXXXXXXX5678 by UPI ref 246813579024 "
    "on 14-Apr-2026. -Kotak Bank"
)

GENERIC_CREDIT = (
    "Your account has been credited with Rs.100 vide Ref 135792468012."
)

MALFORMED_SMS = "OTP for your transaction is 456789. Do not share with anyone."

DEBIT_SMS = (
    "Rs.200.00 debited from your a/c XX1234. UPI Ref 111222333444. -HDFC Bank"
)


# ---------------------------------------------------------------------------
# HDFC tests
# ---------------------------------------------------------------------------

class TestHDFCParser:
    def test_basic_credit(self):
        result = parse(HDFC_CREDIT)
        assert result is not None
        assert result["bank"] == "HDFC"
        assert result["txn_id"] == "123456789012"
        assert result["amount"] == pytest.approx(500.00)

    def test_amount_with_comma(self):
        result = parse(HDFC_CREDIT_COMMA_AMOUNT)
        assert result is not None
        assert result["amount"] == pytest.approx(1500.00)
        assert result["txn_id"] == "987654321098"


# ---------------------------------------------------------------------------
# ICICI tests
# ---------------------------------------------------------------------------

class TestICICIParser:
    def test_basic_credit(self):
        result = parse(ICICI_CREDIT)
        assert result is not None
        assert result["bank"] == "ICICI"
        assert result["txn_id"] == "ICICI123456789"
        assert result["amount"] == pytest.approx(250.50)


# ---------------------------------------------------------------------------
# SBI tests
# ---------------------------------------------------------------------------

class TestSBIParser:
    def test_utr_format(self):
        result = parse(SBI_CREDIT)
        assert result is not None
        assert result["bank"] == "SBI"
        assert result["txn_id"] == "112233445566"
        assert result["amount"] == pytest.approx(750.00)


# ---------------------------------------------------------------------------
# Axis tests
# ---------------------------------------------------------------------------

class TestAxisParser:
    def test_basic_credit(self):
        result = parse(AXIS_CREDIT)
        assert result is not None
        assert result["bank"] == "Axis"
        assert result["txn_id"] == "AXIS9876543210"
        assert result["amount"] == pytest.approx(1000.00)

    def test_amount_with_comma(self):
        result = parse(AXIS_CREDIT)
        assert result is not None
        assert result["amount"] == pytest.approx(1000.00)


# ---------------------------------------------------------------------------
# Kotak tests
# ---------------------------------------------------------------------------

class TestKotakParser:
    def test_basic_credit(self):
        result = parse(KOTAK_CREDIT)
        assert result is not None
        assert result["bank"] == "Kotak"
        assert result["txn_id"] == "246813579024"
        assert result["amount"] == pytest.approx(300.00)


# ---------------------------------------------------------------------------
# Generic / fallback tests
# ---------------------------------------------------------------------------

class TestGenericParser:
    def test_generic_fallback(self):
        result = parse(GENERIC_CREDIT)
        assert result is not None
        assert result["txn_id"] == "135792468012"
        assert result["amount"] == pytest.approx(100.00)


# ---------------------------------------------------------------------------
# Negative / edge case tests
# ---------------------------------------------------------------------------

class TestNegativeCases:
    def test_malformed_sms_returns_none(self):
        result = parse(MALFORMED_SMS)
        assert result is None

    def test_empty_string_returns_none(self):
        result = parse("")
        assert result is None

    def test_none_returns_none(self):
        result = parse(None)
        assert result is None

    def test_debit_sms_not_credited_pattern(self):
        # A debit SMS should not match any credit pattern and return None
        # (debit messages don't contain "credited to" or "credited by")
        debit_only = "Rs.200.00 debited from your a/c XX1234. Balance: Rs.3800.00."
        result = parse(debit_only)
        assert result is None


# ---------------------------------------------------------------------------
# Amount normalisation
# ---------------------------------------------------------------------------

class TestAmountNormalisation:
    @pytest.mark.parametrize("sms,expected_amount", [
        (
            "Rs.1,500.00 credited to your a/c XX1234 on 14-04-26. Ref No 111222333444. -HDFC Bank",
            1500.00,
        ),
        (
            "Rs.10,000.50 credited to your a/c XX1234 on 14-04-26. Ref No 222333444555. -HDFC Bank",
            10000.50,
        ),
        (
            "Rs.50 credited to your a/c XX1234 on 14-04-26. Ref No 333444555666. -HDFC Bank",
            50.0,
        ),
    ])
    def test_amounts(self, sms: str, expected_amount: float):
        result = parse(sms)
        assert result is not None
        assert result["amount"] == pytest.approx(expected_amount)


# ---------------------------------------------------------------------------
# Transaction ID normalisation (uppercase)
# ---------------------------------------------------------------------------

class TestTxnIdNormalisation:
    def test_txn_id_is_uppercased(self):
        sms = (
            "Rs.100.00 credited to Acct XX9999 on 14-Apr-26. "
            "UPI Ref:icici123abc456. -ICICI Bank"
        )
        result = parse(sms)
        assert result is not None
        assert result["txn_id"] == "ICICI123ABC456"
