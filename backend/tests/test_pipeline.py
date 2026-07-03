"""Smoke tests for the ETL + categorization pipeline."""

from datetime import date

import pytest

from app.services.categorization import categorize_by_rules
from app.services.etl import (
    StatementParseError,
    parse_statement,
    parse_statement_csv,
)

CSV = b"""Date,Description,Amount
2026-04-02,WHOLE FOODS MARKET #123,-86.42
2026-04-03,NETFLIX.COM,-15.99
2026-04-01,PAYROLL DIRECT DEP,3200.00
"""


def test_rule_categorization():
    assert categorize_by_rules("WHOLE FOODS MARKET #123") == "Groceries"
    assert categorize_by_rules("NETFLIX.COM") == "Subscriptions"
    assert categorize_by_rules("PAYROLL DIRECT DEP") == "Income"
    assert categorize_by_rules("ZZZ UNKNOWN MERCHANT") is None


def test_parse_normalizes_rows():
    rows = parse_statement_csv(CSV, account_id=1)
    assert len(rows) == 3
    wf = next(r for r in rows if "WHOLE FOODS" in r.merchant)
    assert wf.txn_date == date(2026, 4, 2)
    assert wf.amount == -86.42
    assert wf.dedupe_hash  # hash populated


def test_parse_dedupe_hash_is_stable():
    a = parse_statement_csv(CSV, account_id=1)
    b = parse_statement_csv(CSV, account_id=1)
    assert {r.dedupe_hash for r in a} == {r.dedupe_hash for r in b}


# A different layout: unsigned amount + a separate debit/credit direction column
# (the shape of the ANZ Kaggle export). Headers are intentionally non-standard.
ANZ_LIKE = b"""txn_description,balance,date,amount,movement
POS,35.39,8/1/2018,16.25,debit
PAY/SALARY,2100.0,8/2/2018,2100.00,credit
SALES-POS,21.20,8/3/2018,14.19,debit
"""


def test_infers_direction_column_and_signs_amount():
    result = parse_statement(ANZ_LIKE, account_id=1)
    m = result.schema.mapping
    assert m["date"] == "date"
    assert m["amount"] == "amount"
    assert m["direction"] == "movement"
    assert m["merchant"] == "txn_description"

    by_merchant = {r.merchant: r.amount for r in result.rows}
    assert by_merchant["POS"] == -16.25  # debit -> spend
    assert by_merchant["PAY/SALARY"] == 2100.00  # credit -> income


# A feature/ML dataset with no dates or merchant names must be rejected clearly.
FRAUD_LIKE = b"""Time,V1,V2,Amount,Class
0,-1.359,-0.072,149.62,0
0,1.191,0.266,2.69,0
"""


def test_rejects_non_statement_file():
    with pytest.raises(StatementParseError) as exc:
        parse_statement(FRAUD_LIKE, account_id=1)
    assert "merchant" in str(exc.value).lower()
