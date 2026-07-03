"""Anomaly detection over an account's transactions.

Two reason codes, both designed for precision over recall (a noisy alert feed is
worse than none):

  spend_spike  - a charge far above the normal range for its own category, found
                 with the IQR (Tukey) rule, which is robust to the very outliers
                 we're hunting. Needs a few samples in the category to have a
                 baseline.
  new_merchant - a notable first-time charge appearing only in the most recent
                 window of the statement, so it reads as "something new started"
                 rather than just the oldest transaction for every merchant.

Like subscriptions, this is a derived view: each run recomputes and replaces the
account's anomalies.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Anomaly, Transaction
from app.services.formatting import format_inr

# A category needs at least this many charges before an outlier is meaningful.
MIN_CATEGORY_SAMPLES = 4
IQR_MULTIPLIER = 1.5
# A statistical outlier in a cheap category (e.g. a $30 lunch among $6 coffees) is
# real but not worth alerting on. Require spikes to also clear an absolute floor so
# alerts stay material.
SPIKE_MIN_AMOUNT = 50.0
# Window (days, counting back from the latest transaction) that counts as "recent"
# for new-merchant detection, plus the minimum history span required to bother.
RECENT_WINDOW_DAYS = 30
MIN_SPAN_DAYS = 45
# A new merchant is only flagged if its charge is in the top decile of all spend.
NEW_MERCHANT_PERCENTILE = 0.90


@dataclass
class AnomalyHit:
    transaction_id: int
    reason_code: str
    detail: str


def _percentile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolation percentile over a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _money(value: float) -> str:
    return format_inr(value)


def _detect_spikes(by_category: dict[str, list[Transaction]]) -> list[AnomalyHit]:
    hits: list[AnomalyHit] = []
    for category, txns in by_category.items():
        if len(txns) < MIN_CATEGORY_SAMPLES:
            continue
        amounts = sorted(abs(t.amount) for t in txns)
        q1 = _percentile(amounts, 0.25)
        q3 = _percentile(amounts, 0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue  # no spread -> no meaningful outlier
        fence = q3 + IQR_MULTIPLIER * iqr
        typical = _percentile(amounts, 0.5)
        for t in txns:
            if abs(t.amount) > fence and abs(t.amount) >= SPIKE_MIN_AMOUNT:
                hits.append(
                    AnomalyHit(
                        transaction_id=t.id,
                        reason_code="spend_spike",
                        detail=(
                            f"{_money(abs(t.amount))} is well above the typical "
                            f"{category} charge (~{_money(typical)})"
                        ),
                    )
                )
    return hits


def _detect_new_merchants(spend: list[Transaction]) -> list[AnomalyHit]:
    dates = [t.txn_date for t in spend]
    span = (max(dates) - min(dates)).days
    if span < MIN_SPAN_DAYS:
        return []  # not enough history to call anything "new"

    window_start = max(dates) - timedelta(days=RECENT_WINDOW_DAYS)
    threshold = _percentile(
        sorted(abs(t.amount) for t in spend), NEW_MERCHANT_PERCENTILE
    )

    first_txn: dict[str, Transaction] = {}
    for t in sorted(spend, key=lambda x: x.txn_date):
        first_txn.setdefault(t.merchant, t)

    hits: list[AnomalyHit] = []
    for merchant, t in first_txn.items():
        if t.is_recurring:
            continue  # established subscription, not a surprise
        if t.txn_date >= window_start and abs(t.amount) >= threshold:
            hits.append(
                AnomalyHit(
                    transaction_id=t.id,
                    reason_code="new_merchant",
                    detail=(
                        f"First-time charge from {merchant} "
                        f"({_money(abs(t.amount))})"
                    ),
                )
            )
    return hits


def detect(transactions: list[Transaction]) -> list[AnomalyHit]:
    """Pure detection over transactions (no DB access)."""
    spend = [t for t in transactions if t.amount < 0]
    if not spend:
        return []

    by_category: dict[str, list[Transaction]] = defaultdict(list)
    for t in spend:
        by_category[t.category].append(t)

    return _detect_spikes(by_category) + _detect_new_merchants(spend)


def detect_and_store(db: Session, account_id: int) -> list[Anomaly]:
    """Recompute anomalies for an account, replacing any prior rows."""
    transactions = list(
        db.execute(
            select(Transaction).where(Transaction.account_id == account_id)
        ).scalars()
    )
    hits = detect(transactions)

    db.execute(
        delete(Anomaly).where(
            Anomaly.transaction_id.in_(
                select(Transaction.id).where(Transaction.account_id == account_id)
            )
        )
    )
    rows = [
        Anomaly(
            transaction_id=h.transaction_id,
            reason_code=h.reason_code,
            detail=h.detail,
        )
        for h in hits
    ]
    db.add_all(rows)
    db.commit()
    return rows
