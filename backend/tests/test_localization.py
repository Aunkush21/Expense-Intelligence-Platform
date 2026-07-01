"""Tests for the India-first localization: INR formatting, day-first dates,
UPI merchant cleanup, and Indian merchant categorization."""
import pandas as pd

from app.services.categorization import categorize_by_rules
from app.services.etl import _infer_dayfirst, clean_merchant_name
from app.services.formatting import format_inr


def test_inr_indian_grouping():
    assert format_inr(0) == "₹0.00"
    assert format_inr(649) == "₹649.00"
    assert format_inr(12345.5) == "₹12,345.50"
    assert format_inr(1234567.89) == "₹12,34,567.89"  # lakh grouping
    assert format_inr(-89999) == "-₹89,999.00"


def test_dayfirst_detection():
    # Days exceed 12 -> day-first (Indian DD/MM).
    assert _infer_dayfirst(pd.Series(["15/04/26", "28/06/26", "01/05/26"])) is True
    # Second field exceeds 12 -> month-first (US MM/DD).
    assert _infer_dayfirst(pd.Series(["04/15/2026", "06/28/2026"])) is False
    # Ambiguous -> default day-first.
    assert _infer_dayfirst(pd.Series(["01/02/2026", "03/04/2026"])) is True


def test_upi_merchant_cleanup():
    assert clean_merchant_name("UPI/SWIGGY/swiggy@ybl/Payment") == "SWIGGY"
    assert clean_merchant_name("POS 4521 AMAZON IN") == "AMAZON"
    assert clean_merchant_name("UPI/NETFLIX/netflix@axisb/Payment") == "NETFLIX"
    # No bank rail -> left untouched.
    assert clean_merchant_name("WHOLE FOODS MARKET #123") == "WHOLE FOODS MARKET #123"


def test_indian_categorization():
    assert categorize_by_rules("SWIGGY") == "Dining"
    assert categorize_by_rules("BLINKIT") == "Groceries"
    assert categorize_by_rules("JIO") == "Utilities"
    assert categorize_by_rules("FLIPKART") == "Shopping"
    assert categorize_by_rules("CULTFIT") == "Health"
    # UPI narration with no recognizable merchant still reads as a transfer.
    assert categorize_by_rules("RAHUL", "UPI/RAHUL/rahul@oksbi/Payment") == "Transfers"
