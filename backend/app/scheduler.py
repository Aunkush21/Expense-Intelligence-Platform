"""Background scheduler for the weekly email digest.

This is the project's second data consumer. It runs on its own cadence, opens its
own database session, and builds the digest straight from the tables — it never
calls the analytics API. The only thing it shares with the rest of the app is the
database, which is the intended architecture.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Account
from app.services.digest import build_digest, render_text
from app.services.mailer import send_email

logger = logging.getLogger("digest.scheduler")
settings = get_settings()


def run_digest_for_account(account_id: int) -> str:
    """Build, render, and deliver the digest for one account. Returns delivery info."""
    with SessionLocal() as db:
        digest = build_digest(db, account_id)
        if digest is None:
            return "account not found"
        subject, body = render_text(digest)
    return send_email(subject, body)


def run_all_digests() -> None:
    """The scheduled job: deliver a digest for every account."""
    with SessionLocal() as db:
        account_ids = list(db.execute(select(Account.id)).scalars())
    logger.info("Running weekly digest for %d account(s)", len(account_ids))
    for account_id in account_ids:
        try:
            run_digest_for_account(account_id)
        except Exception:  # noqa: BLE001 - one failure shouldn't stop the rest
            logger.exception("Digest failed for account %s", account_id)


def create_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(daemon=True)
    if settings.digest_interval_minutes > 0:
        trigger = IntervalTrigger(minutes=settings.digest_interval_minutes)
        logger.info("Digest scheduled every %d min", settings.digest_interval_minutes)
    else:
        trigger = CronTrigger(day_of_week="mon", hour=8, minute=0)
        logger.info("Digest scheduled weekly (Mon 08:00)")
    scheduler.add_job(run_all_digests, trigger, id="weekly_digest", replace_existing=True)
    return scheduler
