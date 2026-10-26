"""Synthetic regressions for exact currency titles and overlapping region syntax."""
from decimal import Decimal
import io

import pytest
from openpyxl import Workbook

from app import table_import
from test_table_region_headers import payload


OLD_PROFILE = "funds-trace-table-v16"
NEW_PROFILE = "funds-trace-table-v17"
HEADERS = ["date", "payer_account", "payee_account", "amount", "extra1", "extra2"]
FIRST = ["2026-01-01", "SYN-A", "SYN-B", "1.23", "", ""]
OVERLAP = ["2026-01-02", "payee_account", "payer_account", "100", "date", "amount"]
SECOND = ["2026-01-04", "SYN-D", "SYN-C", "99.00", "2026-01-03", "2.34"]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_exact_currency_code_title_matches_existing_currency_title(suffix):
    def parse(title):
        return table_import.parse(payload([
            ["date", "payer_account", "payee_account", "amount", title],
            ["2026-01-01", "SYN-A", "SYN-B", "1.23", "USD"],
        ], suffix), suffix=suffix)
    alias, canonical = parse("币种代码"), parse("币种")
    assert alias["stats"]["currency_totals"] == canonical["stats"]["currency_totals"] == {"USD": "1.23"}
    assert alias["rows"][0]["currency"] == canonical["rows"][0]["currency"] == "USD"
    assert "currency_basis" not in alias["records"][0]["details"]
    assert any(item["field"] == "currency" and item["column_index"] == 5
               for item in alias["mapping_evidence"])


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_dated_numeric_header_overlap_does_not_fabricate_certain_flows(suffix):
    standalone = table_import.parse(payload([OVERLAP, SECOND], suffix), suffix=suffix)
    assert [(row["trade_time"].isoformat(), row["payer_account"], row["payee_account"], row["amount"])
            for row in standalone["rows"]] == [
        ("2026-01-03T00:00:00", "SYN-C", "SYN-D", Decimal("2.34"))]
    plan = table_import.parse(payload([HEADERS, FIRST, OVERLAP, SECOND], suffix), suffix=suffix)
    # Both a legal text-account transaction and a complete new schema fit the
    # boundary. Concatenation adds an ambiguity, not permission to prefer either.
    assert [(row["payer_account"], row["payee_account"], row["amount"])
            for row in plan["rows"]] == [("SYN-A", "SYN-B", Decimal("1.23"))]
    assert [record["source_row"] for record in plan["records"]] == [2, 3, 4]
    assert [record["reason_code"] for record in plan["records"]] == [
        "transaction_valid", "ambiguous_region_header", "ambiguous_region_header"]
    assert all(record["details"]["financial_effect"] == "undetermined"
               for record in plan["records"][1:])
    assert plan["documents"][0]["header_rows"] == [1]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("code,other,expected,reason", [
    ("USD", "USD", "USD", "transaction_valid"),
    ("CNY", "RMB", "CNY", "transaction_valid"),
    ("RMB", "CNY", "CNY", "transaction_valid"),
    ("美元", "USD", "USD", "transaction_valid"),
    ("", "USD", "USD", "transaction_valid"),
    ("USD", "", "USD", "transaction_valid"),
    ("", "", "CNY", "transaction_valid"),
    ("USD", "CNY", None, "invalid_currency"),
    ("UNKNOWN", "USD", None, "invalid_currency"),
    ("02", "", None, "invalid_currency"),
])
def test_currency_code_validates_every_nonempty_candidate(suffix, code, other, expected, reason):
    plan = table_import.parse(payload([
        ["date", "payer_account", "payee_account", "amount", "币种代码", "币种"],
        ["2026-01-01", "000SYN-A", "000SYN-B", "1.23", code, other],
    ], suffix), suffix=suffix)
    assert plan["profile"] == NEW_PROFILE
    assert plan["records"][0]["reason_code"] == reason
    assert [(row["currency"], row["payer_account"], row["payee_account"], row["amount"])
            for row in plan["rows"]] == (
        [(expected, "000SYN-A", "000SYN-B", Decimal("1.23"))] if expected else [])
    assert {(item["column_index"], item["candidate_count"]) for item in plan["mapping_evidence"]
            if item["field"] == "currency"} == {(5, 2), (6, 2)}


def test_currency_code_keeps_formula_and_explicit_money_currency_checks():
    rows = [["date", "payer_account", "payee_account", "amount(人民币元)", "币种代码"],
            ["2026-01-01", "SYN-A", "SYN-B", "1.23", '="USD"'],
            ["2026-01-02", "SYN-C", "SYN-D", "2.34", "USD"],
            ["2026-01-03", "SYN-E", "SYN-F", "3.45", "CNY"]]
    plan = table_import.parse(payload(rows, ".xlsx"), suffix=".xlsx")
    assert "formula_value" in plan["records"][0]["details"]["validation_issues"].split(",")
    assert plan["records"][1]["reason_code"] == "currency_unit_conflict"
    assert [(row["currency"], row["amount"]) for row in plan["rows"]] == [("CNY", Decimal("3.45"))]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_old_profiles_keep_ignored_title_and_overlapping_region_facts(suffix):
    historical_profiles = {f"funds-trace-table-v{version}" for version in range(5, 17)}
    assert historical_profiles <= table_import.SUPPORTED_PROFILES
    for profile in sorted(historical_profiles):
        content = [["date", "payer_account", "payee_account", "amount", "币种代码"],
                   ["2026-01-01", "SYN-A", "SYN-B", "1.23", "USD"]]
        plan = table_import.parse(payload(content, suffix), suffix=suffix, profile=profile)
        assert plan["stats"]["currency_totals"] == {"CNY": "1.23"}
    plan = table_import.parse(payload([HEADERS, FIRST, OVERLAP, SECOND], suffix),
                              suffix=suffix, profile=OLD_PROFILE)
    assert [row["amount"] for row in plan["rows"]] == [Decimal("1.23"), Decimal("100"), Decimal("99")]
    assert [record["reason_code"] for record in plan["records"]] == ["transaction_valid"] * 3


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_evidence_profile_keeps_v17_currency_title_and_ambiguous_region_protection(suffix):
    profile = table_import.EVIDENCE_PROFILE
    content = [["date", "payer_account", "payee_account", "amount", "币种代码"],
               ["2026-01-01", "SYN-A", "SYN-B", "1.23", "USD"]]
    plan = table_import.parse(payload(content, suffix), suffix=suffix, profile=profile)
    assert plan["stats"]["currency_totals"] == {"USD": "1.23"}
    plan = table_import.parse(payload([HEADERS, FIRST, OVERLAP, SECOND], suffix), suffix=suffix, profile=profile)
    assert [record["reason_code"] for record in plan["records"]] == [
        "transaction_valid", "ambiguous_region_header", "ambiguous_region_header"]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("extra_title", ["extra1", "123", "2026-01-06"])
def test_ambiguous_region_recovers_at_clear_header_without_affecting_other_regions(suffix, extra_title):
    clear = ["date", "payee_account", "payer_account", "amount", extra_title, "extra2"]
    next_row = ["2026-01-05", "SYN-E", "SYN-F", "3.45", "", ""]
    data = [HEADERS, FIRST, OVERLAP, SECOND, clear, next_row]
    plan = table_import.parse(payload(data, suffix), suffix=suffix)
    isolated = table_import.parse(payload([clear, next_row], suffix), suffix=suffix)
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", Decimal("1.23")), ("SYN-F", "SYN-E", Decimal("3.45"))]
    assert {key: plan["rows"][-1][key] for key in table_import.FLOW_FIELDS} == {
        key: isolated["rows"][0][key] for key in table_import.FLOW_FIELDS}
    assert plan["documents"][0]["header_rows"] == [1, 5]
    assert {item["header_row"] for item in plan["mapping_evidence"]} == {1, 5}
    assert [record["reason_code"] for record in plan["records"]] == [
        "transaction_valid", "ambiguous_region_header", "ambiguous_region_header",
        "repeated_header", "transaction_valid"]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_recovery_uses_competing_layouts_not_numeric_and_date_extra_titles(suffix):
    clear = ["date", "payee_account", "payer_account", "amount", "100", "2026-01-06"]
    after = ["2026-01-05", "SYN-E", "SYN-F", "3.45", "", ""]
    standalone = table_import.parse(payload([clear, after], suffix), suffix=suffix)
    plan = table_import.parse(payload([HEADERS, FIRST, OVERLAP, SECOND, clear, after], suffix), suffix=suffix)
    assert [(row["payer_account"], row["payee_account"], row["amount"])
            for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", Decimal("1.23")), ("SYN-F", "SYN-E", Decimal("3.45"))]
    assert {key: plan["rows"][-1][key] for key in table_import.FLOW_FIELDS} == {
        key: standalone["rows"][0][key] for key in table_import.FLOW_FIELDS}
    assert plan["documents"][0]["header_rows"] == [1, 5]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_recovery_cannot_ignore_a_still_viable_competing_layout(suffix):
    # Swapping these extra titles puts an actual date/amount at the alternative
    # layout's time@4 / amount@5, so this candidate is not a proven boundary.
    possible = ["date", "payee_account", "payer_account", "amount", "2026-01-06", "100"]
    after = ["2026-01-05", "SYN-E", "SYN-F", "3.45", "", ""]
    plan = table_import.parse(payload([HEADERS, FIRST, OVERLAP, SECOND, possible, after], suffix), suffix=suffix)
    assert [row["amount"] for row in plan["rows"]] == [Decimal("1.23")]
    assert plan["documents"][0]["header_rows"] == [1]
    assert [record["reason_code"] for record in plan["records"]][1:] == ["ambiguous_region_header"] * 4


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_legal_text_accounts_and_card_fallback_are_not_header_blacklisted(suffix):
    data = [["date", "payer_account", "payee_account", "amount", "extra1", "extra2"],
            ["2026-01-01", "payer_account", "payee_account", "1.23", "date", "not-a-money-title"],
            ["2026-01-02", "date", "amount", "2.34", "", ""]]
    plan = table_import.parse(payload(data, suffix), suffix=suffix)
    assert plan["stats"]["accepted"] == 2 and plan["stats"]["unresolved"] == 0
    data = [["交易日期", "本方卡号", "对方账号", "借贷标志", "交易金额", "币种代码"],
            ["2026-01-03", "000SYN-CARD", "000SYN-COUNTER", "贷", "3.45", "USD"]]
    plan = table_import.parse(payload(data, suffix), suffix=suffix)
    assert [(row["payer_account"], row["payee_account"], row["currency"], row["amount"])
            for row in plan["rows"]] == [("000SYN-COUNTER", "000SYN-CARD", "USD", Decimal("3.45"))]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_query_words_in_legal_accounts_do_not_supply_candidate_identity_roles(suffix):
    headers = ["date", "payer_account", "payee_account", "amount", "extra1", "extra2", "extra3", "extra4"]
    data = [headers,
            ["2026-01-01", "查询帐号", "查询卡号", "1.23", "date", "payer_account", "payee_account", "amount"]]
    plan = table_import.parse(payload(data, suffix), suffix=suffix)
    assert plan["documents"][0]["header_rows"] == [1]
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
        ("查询帐号", "查询卡号", Decimal("1.23"))]


def test_overlapping_region_does_not_leak_into_another_worksheet():
    book = Workbook()
    book.active.title = "Synthetic overlap"
    for row in [HEADERS, FIRST, OVERLAP, SECOND]:
        book.active.append(row)
    sheet = book.create_sheet("Synthetic clear")
    sheet.append(["date", "payer_account", "payee_account", "amount", "币种代码"])
    sheet.append(["2026-01-05", "SYN-E", "SYN-F", "4.56", "USD"])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    plan = table_import.parse(stream.getvalue(), suffix=".xlsx")
    assert plan["stats"]["currency_totals"] == {"CNY": "1.23", "USD": "4.56"}
    assert plan["stats"]["unresolved"] == 2
    assert [(row["payer_account"], row["payee_account"], row["currency"], row["amount"])
            for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", "CNY", Decimal("1.23")), ("SYN-E", "SYN-F", "USD", Decimal("4.56"))]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_own_account_region_checks_counterparty_name_role_as_well(suffix):
    headers = ["date", "self_account", "counter_name", "amount", "direction", "extra1", "extra2", "extra3", "extra4"]
    first = ["2026-01-01", "SYN-OLD", "Synthetic counterparty", "1.23", "out", "", "", "", ""]
    overlap = ["2026-01-02", "100", "counter_name", "100", "out", "date", "signed amount", "self_account", "counter_account"]
    second = ["2026-01-04", "SYN-OLD-2", "Counter two", "99", "out", "2026-01-03", "2.34", "SYN-NEW", "SYN-NEW-COUNTER"]
    isolated = table_import.parse(payload([overlap, second], suffix), suffix=suffix)
    assert [(row["payer_account"], row["payee_account"], row["amount"])
            for row in isolated["rows"]] == [("SYN-NEW-COUNTER", "SYN-NEW", Decimal("2.34"))]
    plan = table_import.parse(payload([headers, first, overlap, second], suffix), suffix=suffix)
    assert [row["amount"] for row in plan["rows"]] == [Decimal("1.23")]
    assert [record["reason_code"] for record in plan["records"]] == [
        "transaction_valid", "ambiguous_region_header", "ambiguous_region_header"]
