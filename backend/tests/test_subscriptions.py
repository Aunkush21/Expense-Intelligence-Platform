"""Tests for recurring-subscription detection."""

from datetime import date, timedelta

from app.models import Transaction
from app.services.subscriptions import detect


def _txn(merchant: str, day: date, amount: float) -> Transaction:
    # Unpersisted ORM instances are fine — detect() only reads attributes.
    return Transaction(merchant=merchant, txn_date=day, amount=amount)


def _series(merchant: str, start: date, gap: int, amount: float, n: int):
    return [_txn(merchant, start + timedelta(days=gap * i), amount) for i in range(n)]


def test_detects_monthly_subscription():
    txns = _series("NETFLIX.COM", date(2026, 1, 1), 30, -15.99, 3)
    subs = detect(txns)
    assert len(subs) == 1
    assert subs[0].merchant == "NETFLIX.COM"
    assert subs[0].cadence == "monthly"
    assert subs[0].average_amount == 15.99
    assert subs[0].next_expected == date(2026, 3, 2) + timedelta(days=30)


def test_detects_after_two_charges():
    txns = _series("SPOTIFY USA", date(2026, 1, 7), 30, -10.99, 2)
    subs = detect(txns)
    assert len(subs) == 1
    assert subs[0].cadence == "monthly"


def test_ignores_irregular_cadence():
    txns = [
        _txn("CORNER STORE", date(2026, 1, 1), -12.0),
        _txn("CORNER STORE", date(2026, 1, 11), -12.0),  # 10-day gap
        _txn("CORNER STORE", date(2026, 2, 25), -12.0),  # 45-day gap
    ]
    assert detect(txns) == []


def test_ignores_variable_amounts():
    # Regular monthly cadence but wildly different amounts -> not a subscription.
    txns = [
        _txn("AMAZON", date(2026, 1, 1), -10.0),
        _txn("AMAZON", date(2026, 1, 31), -50.0),
        _txn("AMAZON", date(2026, 3, 2), -90.0),
    ]
    assert detect(txns) == []


def test_ignores_single_charge():
    assert detect([_txn("ONE OFF", date(2026, 1, 1), -99.0)]) == []
