"""Orchestrates ingestion: normalize -> categorize -> persist.

Keeps the rule/ML categorization wiring in one place so both the ingestion
endpoint and any future batch reprocessing share identical behavior.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services.categorization import (
    MLCategorizer,
    categorize_by_rules,
    normalize_text,
)
from app.services.etl import NormalizedRow


def build_categorizer(db: Session) -> MLCategorizer:
    """Train the ML fallback on existing labelled history (rule + user labels).

    User corrections weigh in naturally: they are stored as transactions with
    category_source='user' and become training examples for everything after.
    """
    labelled = db.execute(
        select(
            Transaction.merchant, Transaction.description, Transaction.category
        ).where(
            Transaction.category != "Uncategorized",
            Transaction.category_source.in_(("rule", "user", "model")),
        )
    ).all()

    texts = [normalize_text(f"{m} {d or ''}") for m, d, _ in labelled]
    labels = [c for _, _, c in labelled]

    categorizer = MLCategorizer()
    categorizer.train(texts, labels)
    return categorizer


def assign_category(row: NormalizedRow, categorizer: MLCategorizer) -> tuple[str, str]:
    """Return (category, source) for a row: rules first, then ML, else default."""
    rule_hit = categorize_by_rules(row.merchant, row.description)
    if rule_hit:
        return rule_hit, "rule"

    if categorizer.is_ready:
        predicted = categorizer.predict(row.merchant, row.description)
        if predicted and predicted != "Uncategorized":
            return predicted, "model"

    return "Uncategorized", "default"


def ingest_rows(
    db: Session, account_id: int, rows: list[NormalizedRow]
) -> dict[str, int]:
    """Categorize and persist normalized rows, skipping duplicates.

    Returns counts: inserted, duplicates_skipped, failed.
    """
    categorizer = build_categorizer(db)

    # Pre-load existing hashes for this account to skip re-uploaded lines cheaply.
    existing = set(
        db.execute(
            select(Transaction.dedupe_hash).where(Transaction.account_id == account_id)
        ).scalars()
    )

    inserted = duplicates = failed = 0
    seen_in_batch: set[str] = set()

    for row in rows:
        if row.dedupe_hash in existing or row.dedupe_hash in seen_in_batch:
            duplicates += 1
            continue
        try:
            category, source = assign_category(row, categorizer)
            db.add(
                Transaction(
                    account_id=account_id,
                    txn_date=row.txn_date,
                    merchant=row.merchant,
                    description=row.description,
                    amount=row.amount,
                    category=category,
                    category_source=source,
                    dedupe_hash=row.dedupe_hash,
                )
            )
            seen_in_batch.add(row.dedupe_hash)
            inserted += 1
        except Exception:  # noqa: BLE001 - one bad row shouldn't abort the batch
            failed += 1

    db.commit()
    return {"inserted": inserted, "duplicates_skipped": duplicates, "failed": failed}
