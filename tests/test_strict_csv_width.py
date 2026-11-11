"""Strict CSV width checks leave region interpretation and replay unchanged."""
from decimal import Decimal
import io

import pytest

from app import table_archive, table_import as tables
from test_folder_evidence import archive
from test_header_width_prefilter import csv_bytes


HEAD = ["date", "payer_account", "payee_account", "amount"]
ROW = ["2026-01-01 10:00:00.123456", "000SYN-A", "000SYN-B", "9007199254740991.13"]
REGIONS = [HEAD, ROW, ["date", "payee_account", "payer_account", "amount"],
           ["2026-01-02", "000SYN-C", "000SYN-D", "2.03"],
           [*HEAD, "currency"], ["2026-01-03", "000SYN-E", "000SYN-F", "3.07", "USD"]]
PROFILES = [tables.SOURCE_CONTRACT_PROFILE, tables.PROFILE,
            tables.SOURCE_CONTRACT_ARCHIVE_PROFILE, tables.SOURCE_OBSERVATION_ARCHIVE_PROFILE]


def parse(raw, profile):
    if profile in table_archive.SUPPORTED_PROFILES:
        return table_archive.parse(io.BytesIO(archive([("synthetic.csv", raw)])), profile=profile)
    return tables.parse(raw, suffix=".csv", profile=profile)


def test_width_validation_only_reconsiders_a_changed_width(monkeypatch):
    original, regional = tables._locate_header, []
    def observed(rows, profile):
        if len(rows) == 1:
            regional.append(rows[0])
        return original(rows, profile)
    monkeypatch.setattr(tables, "_locate_header", observed)
    rows, delimiter = tables._strict_source_csv(csv_bytes(REGIONS).decode("utf-8-sig"), tables.PROFILE)
    assert rows == REGIONS and delimiter == ","
    # Includes the other delimiter candidates, whose rows each have one cell.
    # Only the real width transition requires regional header inference.
    assert regional == [REGIONS[4]]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("delimiter", [",", "\t", ";", "|"])
def test_equal_width_reorder_and_new_width_keep_exact_region_facts(profile, delimiter):
    plan = parse(csv_bytes(REGIONS, delimiter), profile)
    assert plan["profile"] == profile
    assert plan["documents"][0]["header_rows"] == [1, 3, 5]
    assert [(r["payer_account"], r["payee_account"], r["amount"], r["currency"], r["balance"])
            for r in plan["rows"]] == [
        ("000SYN-A", "000SYN-B", Decimal("9007199254740991.13"), "CNY", None),
        ("000SYN-D", "000SYN-C", Decimal("2.03"), "CNY", None),
        ("000SYN-E", "000SYN-F", Decimal("3.07"), "USD", None)]
    assert plan["rows"][0]["trade_time"].microsecond == 123456
    assert [r["source_row"] for r in plan["records"]] == [2, 3, 4, 5, 6]
    assert [r["reason_code"] for r in plan["records"]] == [
        "transaction_valid", "repeated_header", "transaction_valid", "repeated_header", "transaction_valid"]


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("tail", [REGIONS[-1][:-1], [*REGIONS[-1], "extra"]])
def test_nonheader_width_damage_is_still_rejected(profile, tail):
    with pytest.raises(tables.TableImportError, match="CSV已识别表头与数据列数不一致"):
        parse(csv_bytes([*REGIONS, tail]), profile)


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("raw,reason", [
    (b'date,amount,tail;date;amount\n2026-01-01,1.23,x;2026-01-02;2.34\n', "CSV分隔符存在歧义"),
    (b'date,payer_account,payee_account,amount\n"unterminated', "CSV引号或记录结构损坏")])
def test_delimiter_ambiguity_and_damaged_quotes_still_reject(profile, raw, reason):
    with pytest.raises(tables.TableImportError, match=reason):
        parse(raw, profile)
