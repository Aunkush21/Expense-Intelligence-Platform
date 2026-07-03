"""Currency formatting helpers (Indian Rupee, with lakh/crore digit grouping)."""

from __future__ import annotations

import re

_INDIAN_GROUP = re.compile(r"(\d)(?=(\d\d)+\d$)")


def format_inr(amount: float) -> str:
    """Format a number as INR with Indian digit grouping (e.g. ₹12,34,567.50)."""
    negative = amount < 0
    whole, frac = f"{abs(amount):.2f}".split(".")
    grouped = _INDIAN_GROUP.sub(r"\1,", whole)
    return f"{'-' if negative else ''}₹{grouped}.{frac}"
