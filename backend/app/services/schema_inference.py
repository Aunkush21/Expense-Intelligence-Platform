"""Schema inference: map an arbitrary CSV onto our canonical statement schema.

Bank/credit exports never agree on layout, so instead of a fixed alias table we
*infer* each column's role from two signals:

  1. the header name (keyword scoring), and
  2. the actual cell contents (a sampled content profile).

Canonical roles we try to fill:
    date        - the transaction date
    merchant    - human-readable merchant / description text
    amount      - the transaction value
    direction   - optional debit/credit indicator used to sign an unsigned amount
    debit       - optional separate "money out" column
    credit      - optional separate "money in" column

A statement is usable only if we can fill `date`, `merchant`, and at least one of
{`amount`, `debit`/`credit`}. Anything else (e.g. an ML feature dataset) is
rejected with an explanation rather than silently producing junk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

SAMPLE_SIZE = 400

# Header keyword hints per role (substring match on the lower-cased header).
HEADER_HINTS: dict[str, list[str]] = {
    "date": [
        "date",
        "posted",
        "trans date",
        "txn date",
        "tran date",
        "booking",
        "value date",
        "value dt",
        "transaction date",
    ],
    "merchant": [
        "merchant",
        "description",
        "payee",
        "narrative",
        "narration",
        "details",
        "memo",
        "particulars",
        "remarks",
        "txn_description",
        "name",
        "reference",
        "transaction details",
        "transaction remarks",
    ],
    "amount": ["amount", "amt", "value", "transaction amount"],
    "direction": [
        "movement",
        "type",
        "transaction type",
        "txn type",
        "dr/cr",
        "drcr",
        "debit/credit",
        "indicator",
        "cr/dr",
        "direction",
    ],
    # Indian bank exports commonly use "Withdrawal Amt." / "Deposit Amt." columns.
    "debit": ["debit", "withdrawal", "money out", "paid out", "outflow"],
    "credit": ["credit", "deposit", "money in", "paid in", "inflow"],
}

# Header words that mean a column is NOT the single signed amount (it's a balance
# or one side of a split debit/credit layout).
_NOT_AMOUNT = ("withdraw", "deposit", "debit", "credit", "balance")

DIRECTION_TOKENS = {
    "debit",
    "dr",
    "d",
    "withdrawal",
    "wd",
    "out",
    "credit",
    "cr",
    "c",
    "deposit",
    "in",
}
CREDIT_TOKENS = {"credit", "cr", "c", "deposit", "in", "inflow"}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}", re.I)
_DATEISH_RE = re.compile(r"[/\-]")  # a real date string carries separators


@dataclass
class ColumnProfile:
    name: str
    numeric_rate: float
    has_decimals: bool
    has_negatives: bool
    date_rate: float
    text_rate: float
    avg_len: float
    id_like_rate: float
    direction_rate: float


@dataclass
class InferredSchema:
    mapping: dict[str, str] = field(default_factory=dict)  # role -> column name
    notes: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        has_amount = "amount" in self.mapping
        has_split = "debit" in self.mapping or "credit" in self.mapping
        return (
            "date" in self.mapping
            and "merchant" in self.mapping
            and (has_amount or has_split)
        )

    def missing(self) -> list[str]:
        out: list[str] = []
        if "date" not in self.mapping:
            out.append("a date column")
        if "merchant" not in self.mapping:
            out.append("a merchant/description column")
        if "amount" not in self.mapping and not (
            "debit" in self.mapping or "credit" in self.mapping
        ):
            out.append("an amount (or debit/credit) column")
        return out


def clean_number(s: str) -> float | None:
    s = s.strip()
    if not s or s.lower() in {"nan", "none", "-", "null"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "")
    s = s.replace(",", "").replace(" ", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _profile_column(series: pd.Series) -> ColumnProfile:
    name = str(series.name)
    sample = series.dropna().astype(str).str.strip()
    sample = sample[sample != ""]
    if len(sample) > SAMPLE_SIZE:
        sample = sample.sample(SAMPLE_SIZE, random_state=0)
    n = max(len(sample), 1)

    nums = [clean_number(v) for v in sample]
    parsed_nums = [x for x in nums if x is not None]
    numeric_rate = len(parsed_nums) / n
    has_decimals = any(abs(x - round(x)) > 1e-9 for x in parsed_nums)
    has_negatives = any(x < 0 for x in parsed_nums)

    # Date-likeness: only count values that actually look like dates (have
    # separators) AND parse — this rejects integer "seconds" columns.
    dateish = sample[sample.str.contains(_DATEISH_RE)]
    if len(dateish):
        parsed = pd.to_datetime(dateish, errors="coerce", dayfirst=False)
        date_rate = parsed.notna().mean() * (len(dateish) / n)
    else:
        date_rate = 0.0

    non_numeric = sample[[x is None for x in nums]]
    text_rate = len(non_numeric) / n
    avg_len = float(non_numeric.str.len().mean()) if len(non_numeric) else 0.0
    id_like_rate = non_numeric.str.match(_UUID_RE).mean() if len(non_numeric) else 0.0

    lowered = sample.str.lower()
    direction_rate = lowered.isin(DIRECTION_TOKENS).mean()

    return ColumnProfile(
        name=name,
        numeric_rate=numeric_rate,
        has_decimals=has_decimals,
        has_negatives=has_negatives,
        date_rate=float(date_rate),
        text_rate=text_rate,
        avg_len=avg_len,
        id_like_rate=float(id_like_rate),
        direction_rate=float(direction_rate),
    )


def _header_score(col: str, role: str) -> float:
    low = col.strip().lower()
    best = 0.0
    for hint in HEADER_HINTS[role]:
        if low == hint:
            best = max(best, 1.0)
        elif hint in low:
            best = max(best, 0.6)
    return best


def _score(role: str, col: str, p: ColumnProfile) -> float:
    h = _header_score(col, role)
    if role == "date":
        return h * 1.5 + p.date_rate * 2.0
    if role == "amount":
        # The single signed-amount column must look like an amount by name and
        # must not be a balance or one side of a debit/credit split.
        if h == 0 or any(w in col.lower() for w in _NOT_AMOUNT):
            return 0.0
        # Reward decimals (money), penalize id/text and date columns.
        content = p.numeric_rate * (1.3 if p.has_decimals else 0.6)
        return h * 2.0 + content - p.date_rate
    if role == "direction":
        return h * 1.0 + p.direction_rate * 2.5
    if role in ("debit", "credit"):
        return h * 1.6 + p.numeric_rate * 0.5
    if role == "merchant":
        # Favor real text; punish numbers, ids, and ultra-short codes.
        content = p.text_rate * 1.2
        if p.avg_len < 3:
            content -= 0.5
        return h * 1.5 + content - p.id_like_rate * 2.0 - p.numeric_rate
    return 0.0


def infer_schema(df: pd.DataFrame) -> InferredSchema:
    """Infer the role of each column and return the chosen mapping + notes."""
    profiles = {col: _profile_column(df[col]) for col in df.columns}
    schema = InferredSchema(columns=[str(c) for c in df.columns])

    # Thresholds keep weak/ambiguous columns from being force-assigned.
    thresholds = {
        "date": 0.8,
        "amount": 0.8,
        "merchant": 0.6,
        "direction": 1.2,
        "debit": 1.0,
        "credit": 1.0,
    }

    taken: set[str] = set()
    # Resolve in priority order so strong roles claim their column first. Split
    # debit/credit columns are claimed before the unified amount, so an Indian
    # "Withdrawal Amt"/"Deposit Amt" layout isn't mistaken for one amount column.
    for role in ["date", "direction", "debit", "credit", "amount", "merchant"]:
        best_col, best_score = None, thresholds[role]
        for col in df.columns:
            if col in taken:
                continue
            s = _score(role, col, profiles[col])
            if s > best_score:
                best_col, best_score = col, s
        if best_col is not None:
            schema.mapping[role] = best_col
            taken.add(best_col)

    _annotate(schema, profiles)
    return schema


def _annotate(schema: InferredSchema, profiles: dict[str, ColumnProfile]) -> None:
    m = schema.mapping
    if "date" in m:
        schema.notes.append(f"Date column -> '{m['date']}'")
    if "merchant" in m:
        schema.notes.append(f"Merchant/description -> '{m['merchant']}'")
    if "amount" in m:
        amt = profiles[m["amount"]]
        if "direction" in m:
            schema.notes.append(
                f"Amount -> '{m['amount']}' (unsigned); sign taken from "
                f"direction column '{m['direction']}' (debit=spend, credit=income)"
            )
        elif amt.has_negatives:
            schema.notes.append(
                f"Amount -> '{m['amount']}' (already signed: negative = spend)"
            )
        else:
            schema.notes.append(
                f"Amount -> '{m['amount']}' - no sign or direction found; "
                "treating every row as spend"
            )
    elif "debit" in m or "credit" in m:
        schema.notes.append(
            "Using split "
            f"{'debit=' + repr(m.get('debit')) if 'debit' in m else ''} "
            f"{'credit=' + repr(m.get('credit')) if 'credit' in m else ''} columns"
        )
