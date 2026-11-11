"""Unit suffixes retain their public facts across old and current readers."""
from decimal import Decimal

import pytest

from app import table_import as tables
from test_header_width_prefilter import csv_bytes
from test_strict_csv_width import parse


@pytest.mark.parametrize("profile", [tables.FINANCIAL_PROFILE, tables.PROFILE, tables.SOURCE_OBSERVATION_ARCHIVE_PROFILE])
@pytest.mark.parametrize("header,value", [
    ("amount_cents", "123"),
    ("amount thousand yuan", "0.00123"),
    ("amount ten thousand yuan", "0.000123"),
    ("交易金额人民币元", "1.23"),
])
def test_suffix_units_keep_longest_match_and_exact_money(profile, header, value):
    raw=csv_bytes([["date","payer_account","payee_account",header],
                   ["2026-01-01 10:00:00.123456","000SYN-A","000SYN-B",value]])
    plan=parse(raw,profile)
    assert len(plan["rows"])==1
    row=plan["rows"][0]
    assert (row["payer_account"],row["payee_account"],row["amount"],row["currency"]) == (
        "000SYN-A","000SYN-B",Decimal("1.23"),"CNY")
    assert row["trade_time"].microsecond==123456
    assert plan["records"][0]["source_row"]==2


@pytest.mark.parametrize("profile", [tables.PROFILE,tables.SOURCE_OBSERVATION_ARCHIVE_PROFILE])
def test_long_nonheader_preamble_retains_late_header_and_source_position(profile):
    preamble=[["合成说明 "*50+"ordinary memo yuan"] for _ in range(1200)]
    raw=csv_bytes([*preamble,["date","payer_account","payee_account","amount_cents"],
                   ["2026-01-01 10:00:00.123456","000SYN-A","000SYN-B","123"]])
    plan=parse(raw,profile)
    assert len(plan["rows"])==1 and plan["rows"][0]["amount"]==Decimal("1.23")
    assert plan["documents"][0]["header_row"]==1201
    accepted=[record for record in plan["records"] if record["disposition"]=="accepted"]
    assert len(accepted)==1 and accepted[0]["source_row"]==1202
