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


def send_email(
    subject: str,
    body: str,
    to: str | None = None,
    html: str | None = None,
    inline_images: dict[str, bytes] | None = None,
) -> str:
    """Send (or file) an email. Returns a human-readable delivery descriptor.

    `html` adds a rich alternative; `inline_images` maps a cid (referenced in the
    HTML as `cid:<key>`) to PNG bytes. Raises MailerError if SMTP is configured but
    the send fails, so callers can surface a clear message rather than a 500.
    """
    recipient = to or settings.digest_to or settings.digest_from

    if settings.smtp_configured:
        return _send_smtp(subject, body, recipient, html, inline_images)
    return _write_to_outbox(subject, body, recipient, html)


def _send_smtp(
    subject: str,
    body: str,
    recipient: str,
    html: str | None,
    inline_images: dict[str, bytes] | None,
) -> str:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.digest_from
    msg["To"] = recipient
    msg.set_content(body)

    if html:
        msg.add_alternative(html, subtype="html")
        # Attach inline images to the HTML alternative (the last payload part).
        html_part = msg.get_payload()[-1]
        for cid, data in (inline_images or {}).items():
            html_part.add_related(data, maintype="image", subtype="png", cid=f"<{cid}>")

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


def _write_to_outbox(subject: str, body: str, recipient: str, html: str | None) -> str:
    outbox = Path(settings.digest_outbox)
    outbox.mkdir(parents=True, exist_ok=True)

    # Prefer writing the rich HTML (open it in a browser) with the plaintext as a
    # readable fallback; the chart is embedded as a data URI by the caller.
    ext = "html" if html else "txt"
    existing = len(list(outbox.glob(f"*.{ext}")))
    path = outbox / f"{existing:04d}_{_safe_slug(subject)}.{ext}"
    content = html if html else f"To: {recipient}\nSubject: {subject}\n\n{body}\n"
    path.write_text(content, encoding="utf-8")

    logger.info("SMTP not configured - digest written to %s", path)
    return f"written to {path}"
