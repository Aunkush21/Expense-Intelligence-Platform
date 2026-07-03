"""Weekly digest builder.

Composes the "what happened this week" summary the scheduler emails out. It reads
the database directly (transactions, subscriptions, anomalies) and never calls the
analytics API — the two downstream consumers share only the data store, which is
the deliberate decoupling described in the project proposal.

Produces three things: structured data, a plain-text body (fallback + preview),
and a polished inline-CSS HTML email with a category pie chart and personalized,
data-driven saving tips.

The reporting window is anchored to the account's most recent transaction rather
than wall-clock now, so a digest is meaningful for whatever statement period has
been loaded (a real always-on deployment with live data would use "now").
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, Anomaly, Subscription, Transaction
from app.services.formatting import format_inr

WINDOW_DAYS = 7

# Normalize a subscription's charge to a monthly figure.
_MONTHLY_FACTOR = {
    "weekly": 52 / 12,
    "biweekly": 26 / 12,
    "monthly": 1.0,
    "quarterly": 1 / 3,
    "yearly": 1 / 12,
}

# Category-specific, non-preachy saving nudges.
_ADVICE = {
    "Dining": "Cooking a couple more meals at home each week adds up fast.",
    "Shopping": "Try a 24-hour wait before non-essential buys — many lose their pull.",
    "Transport": "Pooling rides or a monthly pass can trim recurring travel costs.",
    "Groceries": "A weekly list and fewer quick-commerce orders keep this in check.",
    "Entertainment": "Rotate one subscription at a time instead of paying for all.",
    "Subscriptions": "Audit these — cancel anything you haven't opened this month.",
    "Travel": "Booking a few weeks ahead usually beats last-minute fares.",
    "Utilities": "Annual recharges and autopay often shave a bit off monthly bills.",
}


@dataclass
class CategoryLine:
    category: str
    total: float


@dataclass
class Digest:
    account_name: str
    period_start: date
    period_end: date
    has_activity: bool = False
    total_spend: float = 0.0
    total_income: float = 0.0
    net: float = 0.0
    prev_spend: float = 0.0
    delta_pct: float | None = None
    savings_rate: float | None = None
    transaction_count: int = 0
    top_categories: list[CategoryLine] = field(default_factory=list)
    all_categories: list[CategoryLine] = field(default_factory=list)
    biggest_increase: tuple[str, float] | None = None
    subscription_monthly: float = 0.0
    tips: list[str] = field(default_factory=list)
    upcoming: list[Subscription] = field(default_factory=list)
    anomalies: list[tuple[Anomaly, Transaction]] = field(default_factory=list)


def _spend_between(db: Session, account_id: int, start: date, end: date) -> float:
    total = db.execute(
        select(func.coalesce(func.sum(-Transaction.amount), 0.0)).where(
            Transaction.account_id == account_id,
            Transaction.amount < 0,
            Transaction.txn_date >= start,
            Transaction.txn_date <= end,
        )
    ).scalar_one()
    return round(total, 2)


def _category_spend(
    db: Session, account_id: int, start: date, end: date
) -> list[tuple[str, float]]:
    return [
        (cat, round(total, 2))
        for cat, total in db.execute(
            select(Transaction.category, func.sum(-Transaction.amount))
            .where(
                Transaction.account_id == account_id,
                Transaction.amount < 0,
                Transaction.txn_date >= start,
                Transaction.txn_date <= end,
            )
            .group_by(Transaction.category)
            .order_by(func.sum(-Transaction.amount).desc())
        ).all()
    ]


def build_digest(db: Session, account_id: int) -> Digest | None:
    account = db.get(Account, account_id)
    if account is None:
        return None

    latest = db.execute(
        select(func.max(Transaction.txn_date)).where(
            Transaction.account_id == account_id
        )
    ).scalar_one_or_none()

    if latest is None:
        today = date.today()
        return Digest(
            account_name=account.name,
            period_start=today - timedelta(days=WINDOW_DAYS - 1),
            period_end=today,
        )

    period_end = latest
    period_start = period_end - timedelta(days=WINDOW_DAYS - 1)
    prev_start = period_start - timedelta(days=WINDOW_DAYS)
    prev_end = period_start - timedelta(days=1)

    total_spend = _spend_between(db, account_id, period_start, period_end)
    prev_spend = _spend_between(db, account_id, prev_start, prev_end)
    income = db.execute(
        select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
            Transaction.account_id == account_id,
            Transaction.amount > 0,
            Transaction.txn_date >= period_start,
            Transaction.txn_date <= period_end,
        )
    ).scalar_one()
    income = round(income, 2)
    delta_pct = (
        round((total_spend - prev_spend) / prev_spend * 100, 1)
        if prev_spend > 0
        else None
    )
    savings_rate = (
        round((income - total_spend) / income * 100, 1) if income > 0 else None
    )

    count = db.execute(
        select(func.count(Transaction.id)).where(
            Transaction.account_id == account_id,
            Transaction.txn_date >= period_start,
            Transaction.txn_date <= period_end,
        )
    ).scalar_one()

    current_cats = _category_spend(db, account_id, period_start, period_end)
    prev_cats = dict(_category_spend(db, account_id, prev_start, prev_end))

    # Category with the biggest jump vs the prior week.
    biggest_increase = None
    for cat, total in current_cats:
        delta = total - prev_cats.get(cat, 0.0)
        if delta > 0 and (biggest_increase is None or delta > biggest_increase[1]):
            biggest_increase = (cat, round(delta, 2))

    subscriptions = list(
        db.execute(
            select(Subscription).where(Subscription.account_id == account_id)
        ).scalars()
    )
    subscription_monthly = round(
        sum(
            s.average_amount * _MONTHLY_FACTOR.get(s.cadence, 1.0)
            for s in subscriptions
        ),
        2,
    )

    upcoming = [
        s
        for s in sorted(subscriptions, key=lambda x: x.next_expected or period_end)
        if s.next_expected
        and period_end <= s.next_expected <= period_end + timedelta(days=WINDOW_DAYS)
    ]

    anomalies = db.execute(
        select(Anomaly, Transaction)
        .join(Transaction, Anomaly.transaction_id == Transaction.id)
        .where(
            Transaction.account_id == account_id,
            Transaction.txn_date >= period_start,
            Transaction.txn_date <= period_end,
        )
        .order_by(Transaction.txn_date.desc())
    ).all()

    all_categories = [CategoryLine(c, t) for c, t in current_cats]
    digest = Digest(
        account_name=account.name,
        period_start=period_start,
        period_end=period_end,
        has_activity=count > 0,
        total_spend=total_spend,
        total_income=income,
        net=round(income - total_spend, 2),
        prev_spend=prev_spend,
        delta_pct=delta_pct,
        savings_rate=savings_rate,
        transaction_count=count,
        top_categories=all_categories[:3],
        all_categories=all_categories,
        biggest_increase=biggest_increase,
        subscription_monthly=subscription_monthly,
        upcoming=upcoming,
        anomalies=[(a, t) for a, t in anomalies],
    )
    digest.tips = _build_tips(digest, len(subscriptions))
    return digest


def _build_tips(d: Digest, subscription_count: int) -> list[str]:
    tips: list[str] = []

    if d.biggest_increase and d.biggest_increase[1] >= 200:
        cat, delta = d.biggest_increase
        advice = _ADVICE.get(cat, "Keep an eye on this one next week.")
        tips.append(
            f"You spent {format_inr(delta)} more on {cat} than the week before. {advice}"
        )

    if subscription_count and d.subscription_monthly > 0:
        tips.append(
            f"You have {subscription_count} recurring "
            f"subscription{'s' if subscription_count != 1 else ''} "
            f"costing about {format_inr(d.subscription_monthly)}/month. "
            "Cancelling one you rarely use is the easiest saving there is."
        )

    if d.top_categories and not d.biggest_increase:
        top = d.top_categories[0]
        advice = _ADVICE.get(top.category, "")
        tips.append(
            f"Your biggest spend was {top.category} at {format_inr(top.total)}. {advice}".strip()
        )

    if d.savings_rate is not None:
        if d.savings_rate < 20:
            tips.append(
                f"You saved {d.savings_rate}% of your income this period. A common "
                "goal is to keep at least 20% aside — automating a transfer on "
                "payday makes it effortless."
            )
        else:
            tips.append(
                f"Nice — you saved {d.savings_rate}% of your income this period. "
                "Keep it up."
            )

    return tips[:4]


# ─────────────────────────── Pie chart ───────────────────────────


def generate_pie_png(categories: list[CategoryLine]) -> bytes | None:
    """Render a donut of spend-by-category as a transparent PNG (or None)."""
    spend = sorted(
        (c for c in categories if c.total > 0), key=lambda c: c.total, reverse=True
    )
    if not spend:
        return None

    top = spend[:6]
    other = round(sum(c.total for c in spend[6:]), 2)
    labels = [c.category for c in top]
    values = [c.total for c in top]
    if other > 0:
        labels.append("Other")
        values.append(other)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = [
        "#4338ca",
        "#0d9488",
        "#7c3aed",
        "#b45309",
        "#be123c",
        "#047857",
        "#94a3b8",
    ]
    fig, ax = plt.subplots(figsize=(4.4, 3.4), dpi=120)
    wedges, *_ = ax.pie(
        values,
        colors=colors[: len(values)],
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2},
    )
    ax.legend(
        wedges,
        [f"{label}   {format_inr(value)}" for label, value in zip(labels, values, strict=False)],
        loc="center left",
        bbox_to_anchor=(0.98, 0.5),
        frameon=False,
        fontsize=9.5,
    )
    ax.set(aspect="equal")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", transparent=True)
    plt.close(fig)
    return buf.getvalue()


# ─────────────────────────── Renderers ───────────────────────────


def _money(v: float) -> str:
    return format_inr(v)


def render_text(digest: Digest) -> tuple[str, str]:
    """Render a digest into (subject, plaintext body)."""
    subject = (
        f"Your weekly spending digest "
        f"({digest.period_start:%b %d} - {digest.period_end:%b %d})"
    )
    if not digest.has_activity:
        body = (
            f"Hi,\n\nNo transactions were recorded for {digest.account_name} "
            f"between {digest.period_start} and {digest.period_end}.\n\n"
            "- Expense Intelligence"
        )
        return subject, body

    lines = [
        f"Weekly digest for {digest.account_name}",
        f"{digest.period_start} -> {digest.period_end}",
        "",
        f"You spent {_money(digest.total_spend)} across "
        f"{digest.transaction_count} transactions.",
    ]
    if digest.delta_pct is not None:
        direction = "up" if digest.delta_pct >= 0 else "down"
        lines.append(
            f"That's {direction} {abs(digest.delta_pct)}% vs the prior week "
            f"({_money(digest.prev_spend)})."
        )
    if digest.top_categories:
        lines += ["", "Where your money went:"]
        lines += [f"  - {c.category}: {_money(c.total)}" for c in digest.top_categories]
    if digest.tips:
        lines += ["", "Tips:"]
        lines += [f"  - {t}" for t in digest.tips]
    if digest.upcoming:
        lines += ["", "Upcoming subscription charges:"]
        lines += [
            f"  - {s.merchant}: {_money(s.average_amount)} on {s.next_expected}"
            for s in digest.upcoming
        ]
    if digest.anomalies:
        lines += ["", f"Flagged ({len(digest.anomalies)}):"]
        lines += [f"  - {a.detail}" for a, _ in digest.anomalies]
    lines += ["", "- Expense Intelligence"]
    return subject, "\n".join(lines)


def render_html(digest: Digest, chart_src: str | None) -> str:
    """Render the premium HTML email. `chart_src` is a cid: or data: image URL."""
    ink, brand, dim, income_c, spend_c = (
        "#1e1b4b",
        "#4338ca",
        "#717185",
        "#047857",
        "#be123c",
    )
    border = "#e8e6e1"
    period = f"{digest.period_start:%d %b} – {digest.period_end:%d %b %Y}"

    if not digest.has_activity:
        inner = (
            f"<p style='color:{dim};font-size:15px;line-height:1.6'>No transactions "
            f"were recorded for <b>{digest.account_name}</b> between "
            f"{digest.period_start} and {digest.period_end}.</p>"
        )
        return _html_shell(digest.account_name, period, inner)

    overspent = digest.net < 0
    net_color = spend_c if overspent else income_c
    net_label = "You overspent" if overspent else "You saved"

    delta_line = ""
    if digest.delta_pct is not None:
        up = digest.delta_pct >= 0
        delta_line = (
            f"<span style='color:{spend_c if up else income_c};font-weight:600'>"
            f"{'▲' if up else '▼'} {abs(digest.delta_pct)}%</span> "
            f"<span style='color:{dim}'>vs last week ({_money(digest.prev_spend)})</span>"
        )

    # Hero
    hero = f"""
      <div style="padding:4px 0 18px">
        <div style="font-size:13px;color:{dim};margin-bottom:6px">{net_label}</div>
        <div style="font-family:'Spectral',Georgia,serif;font-size:40px;font-weight:600;
             color:{net_color};line-height:1">{_money(abs(digest.net))}</div>
        <div style="margin-top:10px;font-size:14px;color:{ink}">
          Spent <b>{_money(digest.total_spend)}</b> · Income <b>{_money(digest.total_income)}</b>
          &nbsp;&nbsp;{delta_line}
        </div>
      </div>"""

    # Chart
    chart = ""
    if chart_src:
        chart = f"""
      <div style="margin:8px 0 20px">
        <div style="{_H3}">Where your money went</div>
        <img src="{chart_src}" alt="Spending by category" style="max-width:100%;height:auto"/>
      </div>"""

    # Tips
    tips = ""
    if digest.tips:
        items = "".join(
            f"""<div style="display:flex;gap:10px;padding:10px 0;border-top:1px solid {border}">
                  <span style="color:{brand};font-weight:700">•</span>
                  <span style="font-size:14px;line-height:1.55;color:{ink}">{t}</span>
                </div>"""
            for t in digest.tips
        )
        tips = f"""
      <div style="margin:6px 0 20px">
        <div style="{_H3}">Ways to save next week</div>
        {items}
      </div>"""

    # Upcoming + alerts
    extras = ""
    if digest.upcoming:
        rows = "".join(
            f"<tr><td style='{_TD}'>{s.merchant}</td>"
            f"<td style='{_TD};text-align:right;color:{ink}'>{_money(s.average_amount)}</td>"
            f"<td style='{_TD};text-align:right;color:{dim}'>{s.next_expected:%d %b}</td></tr>"
            for s in digest.upcoming
        )
        extras += f"""
      <div style="margin:6px 0 18px">
        <div style="{_H3}">Upcoming subscription charges</div>
        <table style="width:100%;border-collapse:collapse;font-size:14px">{rows}</table>
      </div>"""
    if digest.anomalies:
        items = "".join(
            f"""<div style="padding:9px 12px;background:#fdeef1;border-radius:9px;margin-bottom:6px;
                 font-size:13.5px;color:{ink}">{a.detail}</div>"""
            for a, _ in digest.anomalies
        )
        extras += f"""
      <div style="margin:6px 0 8px">
        <div style="{_H3}">Worth a look</div>
        {items}
      </div>"""

    return _html_shell(digest.account_name, period, hero + chart + tips + extras)


# Shared inline style fragments (email clients need inline CSS).
_H3 = "font-size:12px;text-transform:uppercase;letter-spacing:0.08em;color:#717185;font-weight:700;margin-bottom:10px"
_TD = "padding:8px 4px;border-bottom:1px solid #e8e6e1;color:#2c2b3a"


def _html_shell(account_name: str, period: str, inner: str) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;background:#f2f1ec;font-family:'Plus Jakarta Sans',Segoe UI,Arial,sans-serif">
  <div style="max-width:600px;margin:0 auto;padding:24px 16px">
    <div style="background:#ffffff;border:1px solid #e8e6e1;border-radius:18px;overflow:hidden;
         box-shadow:0 12px 28px -18px rgba(30,27,75,0.25)">
      <div style="background:linear-gradient(120deg,#4338ca,#312e81);padding:20px 24px;color:#fff">
        <div style="font-family:'Spectral',Georgia,serif;font-size:19px;font-weight:600">Expense Intelligence</div>
        <div style="font-size:13px;opacity:0.85;margin-top:2px">{account_name} · {period}</div>
      </div>
      <div style="padding:22px 24px">
        {inner}
      </div>
      <div style="padding:14px 24px;border-top:1px solid #e8e6e1;color:#9a99a8;font-size:12px">
        You're receiving this weekly summary from Expense Intelligence.
      </div>
    </div>
  </div>
</body></html>"""
