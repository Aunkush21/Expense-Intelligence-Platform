"""Automation API: preview and trigger the weekly digest on demand.

These endpoints let a user see/send the digest without waiting for the scheduled
run. They build the digest the same way the scheduler does (directly from the DB),
so the preview always matches what would be emailed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Account, User
from app.schemas import DigestPreview, DigestSendResult, SchedulerStatus
from app.scheduler import run_digest_for_account
from app.security import get_current_user, get_owned_account
from app.services.digest import build_digest, render_text
from app.services.mailer import MailerError

router = APIRouter(prefix="/api", tags=["automation"])


@router.get("/automation/status", response_model=SchedulerStatus)
def scheduler_status(
    request: Request, current_user: User = Depends(get_current_user)
) -> SchedulerStatus:
    """Report the digest scheduler's cadence and next scheduled run."""
    scheduler = getattr(request.app.state, "scheduler", None)
    job = scheduler.get_job("weekly_digest") if scheduler else None
    next_run = (
        job.next_run_time.isoformat()
        if job and job.next_run_time is not None
        else None
    )
    return SchedulerStatus(
        running=bool(scheduler and scheduler.running),
        cadence=request.app.state.digest_cadence,
        next_run=next_run,
        delivery_mode=request.app.state.digest_delivery_mode,
    )


@router.get("/accounts/{account_id}/digest/preview", response_model=DigestPreview)
def preview_digest(
    account: Account = Depends(get_owned_account), db: Session = Depends(get_db)
) -> DigestPreview:
    digest = build_digest(db, account.id)
    subject, body = render_text(digest)  # type: ignore[arg-type]
    return DigestPreview(account_id=account.id, subject=subject, body=body)


@router.post("/accounts/{account_id}/digest/send", response_model=DigestSendResult)
def send_digest(
    account: Account = Depends(get_owned_account), db: Session = Depends(get_db)
) -> DigestSendResult:
    digest = build_digest(db, account.id)
    subject, _ = render_text(digest)  # type: ignore[arg-type]
    try:
        delivery = run_digest_for_account(account.id)
    except MailerError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return DigestSendResult(account_id=account.id, subject=subject, delivery=delivery)
