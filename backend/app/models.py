"""ORM models for the expense intelligence data store.

Mirrors the proposal's core data model:
  accounts, transactions, categories, subscriptions, anomalies
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Account(Base):
    """One row per bank or credit account being tracked."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    institution: Mapped[str | None] = mapped_column(String(120))
    account_type: Mapped[str] = mapped_column(String(40), default="checking")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Category(Base):
    """System defaults plus user overrides. Corrections feed future categorization."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    is_user_defined: Mapped[bool] = mapped_column(Boolean, default=False)


class Transaction(Base):
    """Raw + normalized statement line. The center of the data model."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)

    txn_date: Mapped[date] = mapped_column(Date, nullable=False)
    merchant: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(400))
    # Negative = money out (spend), positive = money in (income/refund).
    amount: Mapped[float] = mapped_column(Float, nullable=False)

    category: Mapped[str] = mapped_column(String(80), default="Uncategorized")
    # How the category was assigned: "rule", "model", "user", or "default".
    category_source: Mapped[str] = mapped_column(String(20), default="default")
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)

    # Deterministic hash of the raw line, used to dedupe re-uploaded statements.
    dedupe_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship(back_populates="transactions")
    anomalies: Mapped[list[Anomaly]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )

    # Indexes follow the real access patterns: every read is scoped to an account,
    # so single-column indexes would be redundant. The unique constraint also backs
    # the dedup lookup (filter by account_id, then hash).
    __table_args__ = (
        UniqueConstraint("account_id", "dedupe_hash", name="uq_txn_account_hash"),
        Index("ix_txn_account_date", "account_id", "txn_date"),
        Index("ix_txn_account_category", "account_id", "category"),
    )


class Subscription(Base):
    """Derived table of merchants detected to recur on a regular cadence."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    merchant: Mapped[str] = mapped_column(String(200), nullable=False)
    # e.g. "monthly", "weekly", "yearly"
    cadence: Mapped[str] = mapped_column(String(20), nullable=False)
    average_amount: Mapped[float] = mapped_column(Float, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[date] = mapped_column(Date)
    next_expected: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("account_id", "merchant", name="uq_sub_account_merchant"),
    )


class Anomaly(Base):
    """A flagged transaction with a machine-readable reason code."""

    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id"), index=True
    )
    # e.g. "spend_spike", "new_merchant", "duplicate_charge"
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(400))
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    transaction: Mapped[Transaction] = relationship(back_populates="anomalies")
