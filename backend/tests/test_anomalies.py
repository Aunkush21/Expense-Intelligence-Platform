"""Tests for anomaly detection (spend spikes + new merchants)."""
from datetime import date, timedelta

from app.models import Transaction
from app.services.anomalies import detect

START = date(2026, 1, 1)


def _txn(tid: int, merchant: str, day_offset: int, amount: float, *, category="Shopping", recurring=False):
    t = Transaction(
        merchant=merchant,
        txn_date=START + timedelta(days=day_offset),
        amount=amount,
        category=category,
        is_recurring=recurring,
    )
    t.id = tid
    return t


def test_flags_spend_spike_within_category():
    txns = [
        _txn(1, "AMAZON", 1, -40.0),
        _txn(2, "AMAZON", 5, -45.0),
        _txn(3, "TARGET", 9, -50.0),
        _txn(4, "TARGET", 13, -55.0),
        _txn(5, "BEST BUY", 17, -1200.0),  # the spike
    ]
    hits = detect(txns)
    spikes = [h for h in hits if h.reason_code == "spend_spike"]
    assert len(spikes) == 1
    assert spikes[0].transaction_id == 5


def test_flags_new_merchant_in_recent_window():
    # 80-day span of small, established spend...
    txns = [_txn(i, "CORNER STORE", i * 8, -20.0, category="Groceries") for i in range(10)]
    # ...then a big first-time charge near the end.
    txns.append(_txn(99, "DELTA AIR LINES", 75, -450.0, category="Travel"))
    hits = detect(txns)
    new = [h for h in hits if h.reason_code == "new_merchant"]
    assert len(new) == 1
    assert new[0].transaction_id == 99


def test_recurring_merchant_is_not_a_new_merchant():
    txns = [_txn(i, "CORNER STORE", i * 8, -20.0, category="Groceries") for i in range(10)]
    txns.append(
        _txn(99, "NETFLIX", 75, -450.0, category="Subscriptions", recurring=True)
    )
    new = [h for h in detect(txns) if h.reason_code == "new_merchant"]
    assert new == []


def test_uniform_spend_has_no_anomalies():
    txns = [_txn(i, f"SHOP {i % 3}", i * 5, -30.0) for i in range(12)]
    assert detect(txns) == []
