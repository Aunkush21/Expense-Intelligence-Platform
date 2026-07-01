"""Tests for digest rendering (DB-free — exercises the text composition)."""
from datetime import date

from app.services.digest import CategoryLine, Digest, render_text


def test_render_active_digest():
    digest = Digest(
        account_name="Everyday Checking",
        period_start=date(2026, 6, 22),
        period_end=date(2026, 6, 28),
        has_activity=True,
        total_spend=78.60,
        prev_spend=146.79,
        delta_pct=-46.5,
        transaction_count=3,
        top_categories=[CategoryLine("Shopping", 52.30), CategoryLine("Dining", 6.50)],
    )
    subject, body = render_text(digest)

    assert "Jun 22" in subject and "Jun 28" in subject
    assert "₹78.60" in body
    assert "down 46.5%" in body
    assert "Shopping: ₹52.30" in body


def test_render_empty_digest():
    digest = Digest(
        account_name="Everyday Checking",
        period_start=date(2026, 6, 22),
        period_end=date(2026, 6, 28),
        has_activity=False,
    )
    subject, body = render_text(digest)
    assert "No transactions" in body
