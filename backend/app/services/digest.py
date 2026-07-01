"""Weekly digest builder.

Composes the "what happened this week" summary the scheduler emails out. It reads
the database directly (transactions, subscriptions, anomalies) and never calls the
analytics API — the two downstream consumers share only the data store, which is
the deliberate decoupling described in the project proposal.

The reporting window is anchored to the account's most recent transaction rather
than wall-clock now, so a digest is meaningful for whatever statement period has
been loaded (a real always-on deployment with live data would use "now").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, Anomaly, Subscription, Transaction
from app.services.formatting import format_inr

WINDOW_DAYS = 7


@dataclass
class CategoryLine:
    category: str
    total: float


@dataclass
class Digest:
    account_name: str
    period_start: date
    period_end: date
    has_activity: bool = False
    total_spend: float = 0.0
    prev_spend: float = 0.0
    delta_pct: float | None = None
    transaction_count: int = 0
    top_categories: list[CategoryLine] = field(default_factory=list)
    upcoming: list[Subscription] = field(default_factory=list)
    anomalies: list[tuple[Anomaly, Transaction]] = field(default_factory=list)


def _spend_between(db: Session, account_id: int, start: date, end: date) -> float:
    total = db.execute(
        select(func.coalesce(func.sum(-Transaction.amount), 0.0)).where(
            Transaction.account_id == account_id,
            Transaction.amount < 0,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
        )
    ).scalar_one()
    return round(total, 2)


def build_digest(db: Session, account_id: int) -> Digest | None:
    account = db.get(Account, account_id)
    if account is None:
        return None

    latest = db.execute(
        select(func.max(Transaction.txn_date)).where(
            Transaction.account_id == account_id
        )
    ).scalar_one_or_none()

    if latest is None:
        # No data yet — return an empty, clearly-marked digest.
        today = date.today()
        return Digest(
            account_name=account.name,
            period_start=today - timedelta(days=WINDOW_DAYS - 1),
            period_end=today,
        )

    period_end = latest
    period_start = period_end - timedelta(days=WINDOW_DAYS - 1)
    prev_start = period_start - timedelta(days=WINDOW_DAYS)
    prev_end = period_start - timedelta(days=1)

    total_spend = _spend_between(db, account_id, period_start, period_end)
    prev_spend = _spend_between(db, account_id, prev_start, prev_end)
    delta_pct = (
        round((total_spend - prev_spend) / prev_spend * 100, 1)
        if prev_spend > 0
        else None
    )

    count = db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.account_id == account_id,
            Transaction.txn_date >= period_start,
            Transaction.txn_date <= period_end,
        )
    ).scalar_one()

    top = db.execute(
        select(Transaction.category, func.sum(-Transaction.amount))
        .where(
            Transaction.account_id == account_id,
            Transaction.amount < 0,
            Transaction.txn_date >= period_start,
            Transaction.txn_date <= period_end,
        )
        .group_by(Transaction.category)
        .order_by(func.sum(-Transaction.amount).desc())
        .limit(3)
    ).all()

    # Subscriptions due in the week following the reporting period.
    upcoming = list(
        db.execute(
            select(Subscription)
            .where(
                Subscription.account_id == account_id,
                Subscription.next_expected >= period_end,
                Subscription.next_expected <= period_end + timedelta(days=WINDOW_DAYS),
            )
            .order_by(Subscription.next_expected)
        ).scalars()
    )

    anomalies = db.execute(
        select(Anomaly, Transaction)
        .join(Transaction, Anomaly.transaction_id == Transaction.id)
        .where(
            Transaction.account_id == account_id,
            Transaction.txn_date >= period_start,
            Transaction.txn_date <= period_end,
        )
        .order_by(Transaction.txn_date.desc())
    ).all()

    return Digest(
        account_name=account.name,
        period_start=period_start,
        period_end=period_end,
        has_activity=count > 0,
        total_spend=total_spend,
        prev_spend=prev_spend,
        delta_pct=delta_pct,
        transaction_count=count,
        top_categories=[CategoryLine(c, round(t, 2)) for c, t in top],
        upcoming=upcoming,
        anomalies=[(a, t) for a, t in anomalies],
    )


def _money(v: float) -> str:
    return format_inr(v)


def render_text(digest: Digest) -> tuple[str, str]:
    """Render a digest into (subject, plaintext body)."""
    subject = (
        f"Your weekly spending digest "
        f"({digest.period_start:%b %d} - {digest.period_end:%b %d})"
    )

    if not digest.has_activity:
        body = (
            f"Hi,\n\nNo transactions were recorded for {digest.account_name} "
            f"between {digest.period_start} and {digest.period_end}.\n\n"
            "- Expense Intelligence"
        )
        return subject, body

    lines = [
        f"Weekly digest for {digest.account_name}",
        f"{digest.period_start} -> {digest.period_end}",
        "",
        f"You spent {_money(digest.total_spend)} across "
        f"{digest.transaction_count} transactions.",
    ]

    if digest.delta_pct is not None:
        direction = "up" if digest.delta_pct >= 0 else "down"
        lines.append(
            f"That's {direction} {abs(digest.delta_pct)}% vs the prior week "
            f"({_money(digest.prev_spend)})."
        )

    if digest.top_categories:
        lines += ["", "Top categories:"]
        lines += [
            f"  - {c.category}: {_money(c.total)}" for c in digest.top_categories
        ]

    if digest.upcoming:
        lines += ["", "Upcoming subscription charges:"]
        lines += [
            f"  - {s.merchant}: {_money(s.average_amount)} on {s.next_expected}"
            for s in digest.upcoming
        ]

    if digest.anomalies:
        lines += ["", f"Flagged ({len(digest.anomalies)}):"]
        lines += [f"  - {a.detail}" for a, _ in digest.anomalies]

    lines += ["", "- Expense Intelligence"]
    return subject, "\n".join(lines)
