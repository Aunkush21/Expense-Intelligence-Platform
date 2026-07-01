"""ETL: parse and normalize an uploaded statement file into clean rows.

Column layout is discovered per-file by `schema_inference` rather than assumed,
so arbitrary bank/credit exports map onto our canonical fields:

    txn_date, merchant, description, amount

Amount convention after normalization: negative = money out (spend),
positive = money in (income/refund). Three sign sources are handled, in order:
  1. an explicit direction column (e.g. ANZ's `movement`: debit/credit),
  2. an already-signed amount column,
  3. separate debit/credit columns.
"""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app.services.schema_inference import (
    CREDIT_TOKENS,
    InferredSchema,
    clean_number,
    infer_schema,
)


class StatementParseError(Exception):
    """Raised when a file can't be parsed into the canonical schema."""


@dataclass
class NormalizedRow:
    txn_date: date
    merchant: str
    description: str | None
    amount: float
    dedupe_hash: str


@dataclass
class ParseResult:
    rows: list[NormalizedRow]
    schema: InferredSchema
    rows_in_file: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


def _make_hash(account_id: int, txn_date: date, merchant: str, amount: float) -> str:
    key = f"{account_id}|{txn_date.isoformat()}|{merchant.strip().lower()}|{amount:.2f}"
    return hashlib.sha256(key.encode()).hexdigest()


_DMY_RE = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})[/-]\d{2,4}")


def _infer_dayfirst(series: pd.Series) -> bool:
    """Detect whether dd/mm or mm/dd by scanning which position exceeds 12.

    Falls back to day-first (the Indian/most-of-world convention) when ambiguous.
    ISO dates (YYYY-MM-DD) don't match this and parse correctly regardless.
    """
    first_max = second_max = 0
    for value in series.dropna().astype(str).head(300):
        match = _DMY_RE.match(value)
        if not match:
            continue
        first_max = max(first_max, int(match.group(1)))
        second_max = max(second_max, int(match.group(2)))
    if first_max == 0 and second_max == 0:
        return False  # no dd/mm-style dates (e.g. ISO YYYY-MM-DD) — parse natively
    if second_max > 12 and first_max <= 12:
        return False  # mm/dd/yyyy
    return True  # dd/mm/yyyy, or ambiguous -> India default (day-first)


# Bank-rail prefixes/tokens that aren't the actual merchant name.
_TXN_NOISE = {
    "UPI", "POS", "NEFT", "IMPS", "RTGS", "ACH", "ATM", "DR", "CR", "REF", "TXN",
    "PAYMENT", "PAY", "PMT", "PYMT", "NA", "TO", "FROM", "VPA", "ECOM", "INB", "BIL",
}
_MERCHANT_SPLIT = re.compile(r"[/\-\s|:*#]+")


def clean_merchant_name(raw: str) -> str:
    """Extract a clean merchant from UPI/POS/NEFT narrations.

    `UPI/SWIGGY/abc@ybl/...` -> `SWIGGY`. Plain merchant names (no bank rail) are
    returned unchanged so we don't mangle normal statements.
    """
    text = raw.strip()
    tokens = [t for t in _MERCHANT_SPLIT.split(text) if t]
    upper = {t.upper() for t in tokens}
    if not (upper & {"UPI", "POS", "NEFT", "IMPS", "RTGS", "ACH"}):
        return text[:200]
    for token in tokens:
        if len(token) >= 3 and token.isalpha() and token.upper() not in _TXN_NOISE:
            return token[:200]
    return text[:200]


def _read_csv(file_bytes: bytes) -> pd.DataFrame:
    try:
        return pd.read_csv(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001 - surface any pandas failure cleanly
        raise StatementParseError(f"Could not read CSV: {exc}") from exc


def infer_only(file_bytes: bytes) -> tuple[InferredSchema, pd.DataFrame]:
    """Run schema inference without building rows (used by the preview endpoint)."""
    df = _read_csv(file_bytes)
    if df.empty:
        raise StatementParseError("The file has no rows.")
    return infer_schema(df), df


def parse_statement(file_bytes: bytes, account_id: int) -> ParseResult:
    """Infer the layout, then normalize every row into the canonical schema."""
    schema, df = infer_only(file_bytes)

    if not schema.is_usable:
        missing = ", ".join(schema.missing())
        raise StatementParseError(
            f"Couldn't read this as a statement — missing {missing}. "
            f"Detected columns: {', '.join(schema.columns)}. "
            "This often means the file isn't a transaction export "
            "(e.g. an ML feature dataset with no dates or merchant names)."
        )

    m = schema.mapping

    # Normalize each column once, vectorized — parsing dates and cleaning amounts
    # per-row (iterrows + per-value to_datetime) is the slow path on large files.
    # Day-first is auto-detected so Indian DD/MM/YYYY parses correctly.
    dates = pd.to_datetime(
        df[m["date"]], errors="coerce", dayfirst=_infer_dayfirst(df[m["date"]])
    )
    merchants = df[m["merchant"]].astype(str).str.strip()
    amounts, no_sign_info = _resolve_amounts(df, m)

    rows: list[NormalizedRow] = []
    skipped = 0
    for txn_dt, raw_merchant, amount in zip(dates, merchants, amounts):
        if (
            pd.isna(txn_dt)
            or pd.isna(amount)
            or amount == 0
            or not raw_merchant
            or raw_merchant.lower() == "nan"
        ):
            skipped += 1
            continue

        txn_date = txn_dt.date()
        # Pull a clean merchant out of UPI/POS/NEFT narrations; keep the raw text
        # as the description so categorization rules can still see "upi"/"neft".
        merchant = clean_merchant_name(raw_merchant)
        description = raw_merchant[:400] if merchant != raw_merchant[:200] else None
        amount = round(float(amount), 2)
        rows.append(
            NormalizedRow(
                txn_date=txn_date,
                merchant=merchant,
                description=description,
                amount=amount,
                dedupe_hash=_make_hash(account_id, txn_date, merchant, amount),
            )
        )

    warnings: list[str] = []
    if no_sign_info:
        warnings.append(
            "No sign or debit/credit indicator found - every row was treated as "
            "spend. If this file contains income too, it won't be separated."
        )

    return ParseResult(
        rows=rows,
        schema=schema,
        rows_in_file=len(df),
        skipped=skipped,
        warnings=warnings,
    )


def _clean_series(df: pd.DataFrame, col: str) -> pd.Series:
    """Vectorized currency cleaning of a column into floats (NaN where invalid)."""
    return pd.to_numeric(df[col].map(lambda v: clean_number(str(v))), errors="coerce")


def _resolve_amounts(df: pd.DataFrame, mapping: dict[str, str]) -> tuple[pd.Series, bool]:
    """Resolve a signed amount for the whole frame at once.

    Returns the amount Series plus a flag indicating no sign information was found
    (so every row was assumed to be spend). Sign convention: negative = spend.
    """
    amount_col = mapping.get("amount")
    if amount_col is not None:
        base = _clean_series(df, amount_col)
        if "direction" in mapping:
            tokens = df[mapping["direction"]].astype(str).str.strip().str.lower()
            magnitude = base.abs()
            # credit -> income (+), anything else -> spend (-)
            return magnitude.where(tokens.isin(CREDIT_TOKENS), -magnitude), False
        if (base.dropna() < 0).any():
            return base, False  # already signed
        return -base.abs(), True  # no sign info: assume spend

    # Separate debit/credit columns; debit (money out) wins when both are present.
    amounts = pd.Series(np.nan, index=df.index)
    if "credit" in mapping:
        credit = _clean_series(df, mapping["credit"])
        amounts = amounts.mask(credit.fillna(0) != 0, credit.abs())
    if "debit" in mapping:
        debit = _clean_series(df, mapping["debit"])
        amounts = amounts.mask(debit.fillna(0) != 0, -debit.abs())
    return amounts, False


# Backwards-compatible helper used by tests and any caller that only wants rows.
def parse_statement_csv(file_bytes: bytes, account_id: int) -> list[NormalizedRow]:
    return parse_statement(file_bytes, account_id).rows
