"""Analytics API: read-only aggregations powering the dashboard.

Aggregations run in the database (SUM/COUNT/GROUP BY), not by hydrating rows into
Python — so an account with tens of thousands of transactions costs one grouped
scan per endpoint instead of loading every ORM object. The expressions use
portable SQLAlchemy constructs (`func`, `case`), so the same code runs on SQLite
(dev) and PostgreSQL (prod).

Convention: amount < 0 is spend, amount > 0 is income. Spend totals are reported
as positive numbers for display.
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Transaction
from app.schemas import CategorySpend, MonthlyTrend, SummaryStats

router = APIRouter(prefix="/api", tags=["analytics"])

# Spend stored as a negative amount; expose it as a positive number.
_SPEND = -Transaction.amount
_SPEND_ONLY = case((Transaction.amount < 0, _SPEND), else_=0.0)
_INCOME_ONLY = case((Transaction.amount > 0, Transaction.amount), else_=0.0)


def _require_account(db: Session, account_id: int) -> None:
    if db.get(Account, account_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")


def _for_account(stmt: Select, account_id: int) -> Select:
    return stmt.where(Transaction.account_id == account_id)


@router.get("/accounts/{account_id}/analytics/summary", response_model=SummaryStats)
def summary(account_id: int, db: Session = Depends(get_db)) -> SummaryStats:
    _require_account(db, account_id)

    spend, income, count, start, end = db.execute(
        _for_account(
            select(
                func.coalesce(func.sum(_SPEND_ONLY), 0.0),
                func.coalesce(func.sum(_INCOME_ONLY), 0.0),
                func.count(Transaction.id),
                func.min(Transaction.txn_date),
                func.max(Transaction.txn_date),
            ),
            account_id,
        )
    ).one()

    top_category = db.execute(
        _for_account(
            select(Transaction.category)
            .where(Transaction.amount < 0)
            .group_by(Transaction.category)
            .order_by(func.sum(_SPEND).desc()),
            account_id,
        ).limit(1)
    ).scalar_one_or_none()

    return SummaryStats(
        total_spend=round(spend, 2),
        total_income=round(income, 2),
        net=round(income - spend, 2),
        transaction_count=count,
        top_category=top_category,
        start_date=start,
        end_date=end,
    )


@router.get(
    "/accounts/{account_id}/analytics/by-category",
    response_model=list[CategorySpend],
)
def by_category(account_id: int, db: Session = Depends(get_db)) -> list[CategorySpend]:
    _require_account(db, account_id)

    rows = db.execute(
        _for_account(
            select(
                Transaction.category,
                func.sum(_SPEND),
                func.count(Transaction.id),
            )
            .where(Transaction.amount < 0)  # spend only
            .group_by(Transaction.category)
            .order_by(func.sum(_SPEND).desc()),
            account_id,
        )
    ).all()

    return [
        CategorySpend(category=cat, total=round(total, 2), transaction_count=n)
        for cat, total, n in rows
    ]


@router.get(
    "/accounts/{account_id}/analytics/trends",
    response_model=list[MonthlyTrend],
)
def monthly_trends(account_id: int, db: Session = Depends(get_db)) -> list[MonthlyTrend]:
    _require_account(db, account_id)

    # Month bucketing is the one DB-specific operation (strftime vs to_char), so
    # we fetch just the two needed columns — not whole ORM rows — and bucket in
    # Python. This stays portable while still avoiding object hydration.
    rows = db.execute(
        _for_account(
            select(Transaction.txn_date, Transaction.amount), account_id
        )
    ).all()

    spend: dict[str, float] = defaultdict(float)
    income: dict[str, float] = defaultdict(float)
    for txn_date, amount in rows:
        month = txn_date.strftime("%Y-%m")
        if amount < 0:
            spend[month] -= amount
        else:
            income[month] += amount

    return [
        MonthlyTrend(
            month=m,
            total_spend=round(spend.get(m, 0.0), 2),
            total_income=round(income.get(m, 0.0), 2),
        )
        for m in sorted(spend.keys() | income.keys())
    ]
