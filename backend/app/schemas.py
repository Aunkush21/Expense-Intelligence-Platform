"""Pydantic request/response models for the API layer."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class AccountCreate(BaseModel):
    name: str
    institution: str | None = None
    account_type: str = "checking"


class AccountUpdate(BaseModel):
    name: str | None = None
    institution: str | None = None
    account_type: str | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    institution: str | None
    account_type: str
    created_at: datetime


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    txn_date: date
    merchant: str
    description: str | None
    amount: float
    category: str
    category_source: str
    is_recurring: bool


class CategoryCorrection(BaseModel):
    """Human-in-the-loop correction; feeds back into future categorization."""

    category: str = Field(min_length=1, max_length=80)


class IngestionResult(BaseModel):
    account_id: int
    rows_received: int
    inserted: int
    duplicates_skipped: int
    failed: int
    message: str
    # How the uploaded file's columns were interpreted, plus any caveats.
    schema_mapping: dict[str, str] = {}
    schema_notes: list[str] = []
    warnings: list[str] = []


class ClearResult(BaseModel):
    account_id: int
    deleted: int


class PreviewRow(BaseModel):
    txn_date: date
    merchant: str
    amount: float


class SchemaPreview(BaseModel):
    """Result of inferring a statement's layout without importing it."""

    usable: bool
    detected_columns: list[str]
    mapping: dict[str, str]
    notes: list[str]
    warnings: list[str] = []
    missing: list[str] = []
    sample_rows: list[PreviewRow] = []


# ---- Analytics response shapes ----


class CategorySpend(BaseModel):
    category: str
    total: float
    transaction_count: int


class MonthlyTrend(BaseModel):
    month: str  # "YYYY-MM"
    total_spend: float
    total_income: float


class SummaryStats(BaseModel):
    total_spend: float
    total_income: float
    net: float
    transaction_count: int
    top_category: str | None
    start_date: date | None
    end_date: date | None


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    merchant: str
    cadence: str
    average_amount: float
    occurrences: int
    last_seen: date
    next_expected: date | None


class AnomalyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: int
    reason_code: str
    detail: str | None
    detected_at: datetime
