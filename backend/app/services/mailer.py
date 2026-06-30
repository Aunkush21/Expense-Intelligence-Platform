"""Email delivery with a no-credentials fallback.

If SMTP is configured (smtp_host/user/password), digests are sent for real. If
not — the default for local dev and this demo — the rendered email is written to
the digest_outbox/ directory and logged, so the whole pipeline is observable
without any mail account.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger("digest.mailer")
settings = get_settings()


class MailerError(Exception):
    """Raised when a real email send fails (bad credentials, host unreachable…)."""


def _safe_slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)[:40]


def send_email(subject: str, body: str, to: str | None = None) -> str:
    """Send (or file) an email. Returns a human-readable delivery descriptor.

    Raises MailerError if SMTP is configured but the send fails, so callers can
    surface a clear message rather than a generic 500.
    """
    recipient = to or settings.digest_to or settings.digest_from

    if settings.smtp_configured:
        return _send_smtp(subject, body, recipient)
    return _write_to_outbox(subject, body, recipient)


def _send_smtp(subject: str, body: str, recipient: str) -> str:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.digest_from
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailerError(
            "SMTP authentication failed. For Gmail, use a 16-character App "
            "Password (not your normal password) and enable 2-Step Verification."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailerError(f"Could not send email: {exc}") from exc

    logger.info("Digest emailed to %s", recipient)
    return f"emailed to {recipient}"


def _write_to_outbox(subject: str, body: str, recipient: str) -> str:
    outbox = Path(settings.digest_outbox)
    outbox.mkdir(parents=True, exist_ok=True)

    # Deterministic-ish filename without wall-clock (timestamps aren't available
    # in all contexts); a short subject slug keeps files readable and unique.
    existing = len(list(outbox.glob("*.txt")))
    path = outbox / f"{existing:04d}_{_safe_slug(subject)}.txt"
    path.write_text(
        f"To: {recipient}\nSubject: {subject}\n\n{body}\n", encoding="utf-8"
    )

    logger.info("SMTP not configured - digest written to %s", path)
    return f"written to {path}"
