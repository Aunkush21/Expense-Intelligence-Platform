"""Ingestion API: the single entry point for statement uploads.

Per the architecture, everything funnels through here into PostgreSQL; the
analytics API and scheduler are downstream consumers that never call back in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Anomaly, Subscription, Transaction
from app.schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    CategoryCorrection,
    ClearResult,
    IngestionResult,
    PreviewRow,
    SchemaPreview,
    TransactionOut,
)
from app.services.etl import (
    StatementParseError,
    infer_only,
    parse_statement,
)
from app.services.pipeline import ingest_rows

router = APIRouter(prefix="/api", tags=["ingestion"])


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> Account:
    account = Account(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[Account]:
    return list(db.execute(select(Account).order_by(Account.id)).scalars())


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)
) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    return account


@router.post("/accounts/{account_id}/statements", response_model=IngestionResult)
async def upload_statement(
    account_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> IngestionResult:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Only .csv statement exports are supported in this version.",
        )

    raw = await file.read()
    try:
        parsed = parse_statement(raw, account_id)
    except StatementParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    counts = ingest_rows(db, account_id, parsed.rows)
    return IngestionResult(
        account_id=account_id,
        rows_received=len(parsed.rows),
        message=f"Processed {file.filename}",
        schema_mapping=parsed.schema.mapping,
        schema_notes=parsed.schema.notes,
        warnings=parsed.warnings,
        **counts,
    )


@router.post("/statements/preview", response_model=SchemaPreview)
async def preview_statement(file: UploadFile = File(...)) -> SchemaPreview:
    """Infer how a CSV's columns map to the expense schema, without importing.

    Lets the UI show the user exactly how the file was interpreted (and explain
    why a non-statement file can't be used) before committing any data.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .csv files are supported."
        )

    raw = await file.read()
    try:
        # account_id=0 is fine here — preview rows are never persisted.
        parsed = parse_statement(raw, account_id=0)
    except StatementParseError:
        # Still return the (unusable) inference so the UI can explain the gap.
        try:
            schema, _ = infer_only(raw)
        except StatementParseError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)
            ) from exc
        return SchemaPreview(
            usable=False,
            detected_columns=schema.columns,
            mapping=schema.mapping,
            notes=schema.notes,
            missing=schema.missing(),
        )

    return SchemaPreview(
        usable=True,
        detected_columns=parsed.schema.columns,
        mapping=parsed.schema.mapping,
        notes=parsed.schema.notes,
        warnings=parsed.warnings,
        sample_rows=[
            PreviewRow(txn_date=r.txn_date, merchant=r.merchant, amount=r.amount)
            for r in parsed.rows[:8]
        ],
    )


@router.get("/accounts/{account_id}/transactions", response_model=list[TransactionOut])
def list_transactions(
    account_id: int,
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
    db: Session = Depends(get_db),
) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.account_id == account_id)
    if category:
        stmt = stmt.where(Transaction.category == category)
    stmt = stmt.order_by(Transaction.txn_date.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())


@router.delete("/accounts/{account_id}/transactions", response_model=ClearResult)
def clear_transactions(account_id: int, db: Session = Depends(get_db)) -> ClearResult:
    """Wipe all transactions (and derived rows) for an account, keeping the account.

    Lets the user reset the dashboard and import a fresh statement. Deletes in
    dependency order via bulk statements (the ORM relationship cascade only fires
    on per-object deletes, which we avoid here for speed).
    """
    if db.get(Account, account_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")

    txn_ids = select(Transaction.id).where(Transaction.account_id == account_id)
    db.execute(delete(Anomaly).where(Anomaly.transaction_id.in_(txn_ids)))
    db.execute(delete(Subscription).where(Subscription.account_id == account_id))
    result = db.execute(
        delete(Transaction).where(Transaction.account_id == account_id)
    )
    db.commit()
    return ClearResult(account_id=account_id, deleted=result.rowcount or 0)


@router.patch("/transactions/{transaction_id}/category", response_model=TransactionOut)
def correct_category(
    transaction_id: int,
    payload: CategoryCorrection,
    db: Session = Depends(get_db),
) -> Transaction:
    """Human-in-the-loop correction. Marks source='user' so it trains the model."""
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    txn.category = payload.category
    txn.category_source = "user"
    db.commit()
    db.refresh(txn)
    return txn
