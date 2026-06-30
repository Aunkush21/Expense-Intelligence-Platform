"""Ingestion API: the single entry point for statement uploads.

Per the architecture, everything funnels through here into PostgreSQL; the
analytics API and scheduler are downstream consumers that never call back in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, Anomaly, Subscription, Transaction, User
from app.schemas import (
    AccountCreate,
    AccountOut,
    AccountUpdate,
    AnomalyOut,
    CategoryCorrection,
    ClearResult,
    IngestionResult,
    PreviewRow,
    SchemaPreview,
    SubscriptionOut,
    TransactionOut,
)
from app.security import get_current_user, get_owned_account
from app.services import anomalies as anomaly_service
from app.services import subscriptions as subscription_service
from app.services.etl import (
    StatementParseError,
    infer_only,
    parse_statement,
)
from app.services.pipeline import ingest_rows

router = APIRouter(prefix="/api", tags=["ingestion"])


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Account:
    account = Account(user_id=current_user.id, **payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Account]:
    return list(
        db.execute(
            select(Account)
            .where(Account.user_id == current_user.id)
            .order_by(Account.id)
        ).scalars()
    )


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    payload: AccountUpdate,
    account: Account = Depends(get_owned_account),
    db: Session = Depends(get_db),
) -> Account:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    db.commit()
    db.refresh(account)
    return account


@router.post("/accounts/{account_id}/statements", response_model=IngestionResult)
async def upload_statement(
    file: UploadFile = File(...),
    account: Account = Depends(get_owned_account),
    db: Session = Depends(get_db),
) -> IngestionResult:
    account_id = account.id

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
    # Refresh the derived views now that new rows have landed. Subscriptions run
    # first so the recurring flag is set before anomaly detection reads it.
    subscription_service.detect_and_store(db, account_id)
    anomaly_service.detect_and_store(db, account_id)
    return IngestionResult(
        account_id=account_id,
        rows_received=len(parsed.rows),
        message=f"Processed {file.filename}",
        schema_mapping=parsed.schema.mapping,
        schema_notes=parsed.schema.notes,
        warnings=parsed.warnings,
        **counts,
    )


@router.get(
    "/accounts/{account_id}/subscriptions", response_model=list[SubscriptionOut]
)
def list_subscriptions(
    account: Account = Depends(get_owned_account), db: Session = Depends(get_db)
) -> list[Subscription]:
    return list(
        db.execute(
            select(Subscription)
            .where(Subscription.account_id == account.id)
            .order_by(Subscription.next_expected)
        ).scalars()
    )


@router.post(
    "/accounts/{account_id}/subscriptions/detect",
    response_model=list[SubscriptionOut],
)
def redetect_subscriptions(
    account: Account = Depends(get_owned_account), db: Session = Depends(get_db)
) -> list[Subscription]:
    """Re-run detection on demand (e.g. after the user corrects categories)."""
    return subscription_service.detect_and_store(db, account.id)


@router.get("/accounts/{account_id}/anomalies", response_model=list[AnomalyOut])
def list_anomalies(
    account: Account = Depends(get_owned_account), db: Session = Depends(get_db)
) -> list[AnomalyOut]:
    rows = db.execute(
        select(Anomaly, Transaction)
        .join(Transaction, Anomaly.transaction_id == Transaction.id)
        .where(Transaction.account_id == account.id)
        .order_by(Transaction.txn_date.desc())
    ).all()
    return [
        AnomalyOut(
            id=a.id,
            transaction_id=a.transaction_id,
            reason_code=a.reason_code,
            detail=a.detail,
            detected_at=a.detected_at,
            txn_date=t.txn_date,
            merchant=t.merchant,
            amount=t.amount,
            category=t.category,
        )
        for a, t in rows
    ]


@router.post("/statements/preview", response_model=SchemaPreview)
async def preview_statement(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> SchemaPreview:
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
    limit: int = 100,
    offset: int = 0,
    category: str | None = None,
    account: Account = Depends(get_owned_account),
    db: Session = Depends(get_db),
) -> list[Transaction]:
    stmt = select(Transaction).where(Transaction.account_id == account.id)
    if category:
        stmt = stmt.where(Transaction.category == category)
    stmt = stmt.order_by(Transaction.txn_date.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())


@router.delete("/accounts/{account_id}/transactions", response_model=ClearResult)
def clear_transactions(
    account: Account = Depends(get_owned_account), db: Session = Depends(get_db)
) -> ClearResult:
    """Wipe all transactions (and derived rows) for an account, keeping the account.

    Lets the user reset the dashboard and import a fresh statement. Deletes in
    dependency order via bulk statements (the ORM relationship cascade only fires
    on per-object deletes, which we avoid here for speed).
    """
    account_id = account.id
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Transaction:
    """Human-in-the-loop correction. Marks source='user' so it trains the model."""
    txn = db.get(Transaction, transaction_id)
    # Resolve ownership via the transaction's account; 404 hides others' ids.
    if txn is None or txn.account.user_id != current_user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transaction not found")
    txn.category = payload.category
    txn.category_source = "user"
    db.commit()
    db.refresh(txn)
    return txn
