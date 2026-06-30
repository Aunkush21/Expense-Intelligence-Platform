"""Recurring-subscription detection.

A subscription is a merchant we charge-bill on a regular cadence for a roughly
stable amount (Netflix every ~30 days for $15.99). We detect them purely from
transaction history:

  1. group an account's spend by merchant,
  2. for merchants seen at least twice, look at the gaps between charges,
  3. if those gaps match a known cadence (weekly / monthly / ...) and the amounts
     are stable, record it as a subscription with the next expected charge date.

This is a derived view, so each run fully recomputes and replaces the account's
subscriptions table (idempotent — safe to call after every upload).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from dataclasses import dataclass
from statistics import median

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import Subscription, Transaction

# (label, period in days, tolerance in days) — ordered narrow to wide.
CADENCES: list[tuple[str, int, int]] = [
    ("weekly", 7, 2),
    ("biweekly", 14, 3),
    ("monthly", 30, 5),
    ("quarterly", 91, 10),
    ("yearly", 365, 20),
]

MIN_OCCURRENCES = 2
# Allowed spread of charge amounts, as a fraction of the average, to still count
# as "stable". True subscriptions bill a near-identical amount each cycle, so this
# is deliberately tight — it's the main signal separating a Netflix charge from a
# merchant (groceries, gas) that merely happens to recur on a similar interval.
AMOUNT_TOLERANCE = 0.05


@dataclass
class DetectedSubscription:
    merchant: str
    cadence: str
    average_amount: float
    occurrences: int
    last_seen: date
    next_expected: date


def _classify_cadence(gap_days: float) -> tuple[str, int] | None:
    for label, period, tol in CADENCES:
        if abs(gap_days - period) <= tol:
            return label, period
    return None


def _is_regular(gaps: list[int], period: int, tol: int) -> bool:
    """Every interval must sit within tolerance of the cadence period."""
    return all(abs(g - period) <= tol for g in gaps)


def _amounts_stable(amounts: list[float]) -> bool:
    avg = sum(amounts) / len(amounts)
    if avg == 0:
        return False
    return (max(amounts) - min(amounts)) <= AMOUNT_TOLERANCE * avg


def detect(transactions: list[Transaction]) -> list[DetectedSubscription]:
    """Pure detection over a list of transactions (no DB access)."""
    by_merchant: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.amount < 0:  # spend only
            by_merchant[t.merchant].append(t)

    found: list[DetectedSubscription] = []
    for merchant, txns in by_merchant.items():
        if len(txns) < MIN_OCCURRENCES:
            continue
        txns.sort(key=lambda t: t.txn_date)
        dates = [t.txn_date for t in txns]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        if not gaps:
            continue

        cadence = _classify_cadence(median(gaps))
        if cadence is None:
            continue
        label, period = cadence
        tol = next(t for lbl, p, t in CADENCES if lbl == label)
        if not _is_regular(gaps, period, tol):
            continue

        amounts = [abs(t.amount) for t in txns]
        if not _amounts_stable(amounts):
            continue

        found.append(
            DetectedSubscription(
                merchant=merchant,
                cadence=label,
                average_amount=round(sum(amounts) / len(amounts), 2),
                occurrences=len(txns),
                last_seen=dates[-1],
                next_expected=dates[-1] + timedelta(days=period),
            )
        )
    return found


def detect_and_store(db: Session, account_id: int) -> list[Subscription]:
    """Recompute subscriptions for an account, replacing any prior rows.

    Also flags the underlying transactions with `is_recurring` so the dashboard
    and (later) the anomaly/digest jobs can tell one-offs from recurring charges.
    """
    transactions = list(
        db.execute(
            select(Transaction).where(Transaction.account_id == account_id)
        ).scalars()
    )
    detected = detect(transactions)

    # Replace the derived rows for this account.
    db.execute(delete(Subscription).where(Subscription.account_id == account_id))
    rows = [
        Subscription(
            account_id=account_id,
            merchant=d.merchant,
            cadence=d.cadence,
            average_amount=d.average_amount,
            occurrences=d.occurrences,
            last_seen=d.last_seen,
            next_expected=d.next_expected,
        )
        for d in detected
    ]
    db.add_all(rows)

    # Reset then set the recurring flag in two bulk statements.
    recurring_merchants = {d.merchant for d in detected}
    db.execute(
        update(Transaction)
        .where(Transaction.account_id == account_id)
        .values(is_recurring=False)
    )
    if recurring_merchants:
        db.execute(
            update(Transaction)
            .where(
                Transaction.account_id == account_id,
                Transaction.merchant.in_(recurring_merchants),
            )
            .values(is_recurring=True)
        )

    db.commit()
    return rows
