"""Independent public-parser facts for the bounded v11 unit declaration grammar."""
import csv
import datetime as dt
from decimal import Decimal, localcontext
import io

from openpyxl import Workbook
import pytest

from app import table_import as tables


OWN_HEADERS = ["date", "account", "counterparty account"]
OWN_ROW = ["2026-01-02 10:20:30", "SYN-OWN", "SYN-OTHER"]


def parse(headers, rows, **options):
    output = io.StringIO(newline="")
    csv.writer(output).writerows([headers, *rows])
    options.setdefault("profile", tables.UNIT_DECLARATION_PROFILE)
    return tables.parse(output.getvalue().encode(), suffix=".csv", **options)


def workbook_plan(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets:
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    return tables.parse(output.getvalue(), suffix=".xlsx", profile=tables.UNIT_DECLARATION_PROFILE)


def assert_unresolved(plan, issue, index=0):
    record = plan["records"][index]
    assert record["disposition"] == "unresolved"
    assert record["details"]["financial_effect"] == "undetermined"
    assert issue in record["details"].get("validation_issues", record["reason_code"]).split(",")


@pytest.mark.parametrize("header,value,expected", [
    ("amount(unit:分)", "1234", "12.34"),
    ("amount [UNIT： cent]", "1234", "12.34"),
    ("amount （ Unit  :  CNY ）", "12.34", "12.34"),
    ("amount / unit: 万元", "0.001234", "12.34"),
    ("交易金额[unit: thousand yuan]", "0.01234", "12.34"),
    ("交易金额(unit:角)", "123.4", "12.34"),
])
def test_known_unit_declarations_normalize_only_marker_and_declared_unit(header, value, expected):
    result = parse([*OWN_HEADERS, "direction", header], [[*OWN_ROW, "out", value]])
    assert result["profile"] == tables.UNIT_DECLARATION_PROFILE == "funds-trace-table-v11"
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0]["amount"] == Decimal(expected)
    assert (result["rows"][0]["payer_account"], result["rows"][0]["payee_account"]) == (
        "SYN-OWN", "SYN-OTHER")


@pytest.mark.parametrize("profile", [f"funds-trace-table-v{version}" for version in range(5, 11)])
def test_historical_profiles_keep_english_marker_rejection_and_mixed_bracket_acceptance(profile):
    english = parse(["date", "payer account", "payee account", "amount(unit:cent)"],
                    [["2026-01-02", "SYN-A", "SYN-B", "1234"]], profile=profile)
    assert english["profile"] == profile
    assert not english["rows"]
    assert_unresolved(english, "unknown_unit")
    mixed = parse(["date", "payer account", "payee account", "amount(cent]"],
                  [["2026-01-02", "SYN-A", "SYN-B", "1234"]], profile=profile)
    assert mixed["stats"]["accepted"] == 1
    assert mixed["rows"][0]["amount"] == Decimal("12.34")


@pytest.mark.parametrize("headers,values,amount,balance,payer", [
    (["amount(unit:cent)", "direction"], ["1234", "out"], "12.34", None, "SYN-OWN"),
    (["income [unit : cent]", "expense"], ["1234", "0"], "12.34", None, "SYN-OTHER"),
    (["income", "expense/unit:万元"], ["0", "0.001234"], "12.34", None, "SYN-OWN"),
    (["amount", "direction", "balance(unit:CNY)"], ["12.34", "out", "987.65"], "12.34", "987.65", "SYN-OWN"),
    (["amount(unit:cent)", "交易前账面余额[unit:cent]", "交易后账面余额(unit:元)"],
     ["123", "10000", "98.77"], "1.23", "98.77", "SYN-OWN"),
])
def test_each_existing_money_role_keeps_its_scope_and_same_entry_balance_authority(
        headers, values, amount, balance, payer):
    result = parse([*OWN_HEADERS, *headers], [[*OWN_ROW, *values]])
    assert result["stats"]["accepted"] == 1
    row = result["rows"][0]
    assert (row["amount"], row["balance"], row["payer_account"]) == (
        Decimal(amount), None if balance is None else Decimal(balance), payer)
    if any("交易前账面余额" in header for header in headers):
        assert result["records"][0]["details"]["balance_basis"] == "same_entry_exact"
        assert result["records"][0]["details"]["direction_basis"] == "same_entry_book_balances"


@pytest.mark.parametrize("header", ["amount/分", "amount(单位:角)", "amount[cent]", "amount万元"])
def test_existing_non_base_units_keep_their_original_factors(header):
    values = {"amount/分": "1234", "amount(单位:角)": "123.4", "amount[cent]": "1234", "amount万元": "0.001234"}
    result = parse([*OWN_HEADERS, "direction", header], [[*OWN_ROW, "in", values[header]]])
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0]["amount"] == Decimal("12.34")


@pytest.mark.parametrize("header", ["amount(unit:CNY)", "balance[UNIT:人民币元]"])
def test_cny_annotation_cannot_be_overridden_by_source_usd_currency(header):
    headers = [*OWN_HEADERS, "direction", "currency", header]
    values = [*OWN_ROW, "out", "USD", "12.34"]
    if header.startswith("balance"):
        headers.append("amount")
        values.append("1.23")
    result = parse(headers, [values])
    assert not result["rows"]
    assert_unresolved(result, "currency_unit_conflict")


@pytest.mark.parametrize("annotation", [
    "unit:厘", "unit:-cent", "unit:cen_ts", "unit:unit:cent", "单位:unit:cent", "unit:cent estimated",
])
def test_marker_does_not_delete_unknown_units_signs_or_extra_declarations(annotation):
    result = parse([*OWN_HEADERS, "direction", f"amount({annotation})"], [[*OWN_ROW, "out", "1234"]])
    assert not result["rows"]
    assert_unresolved(result, "unknown_unit")


@pytest.mark.parametrize("header", [
    "amount(cent]", "amount[单位:cent)", "amount(unit:cent]", "amount[UNIT：cent)",
])
def test_mixed_brackets_remain_invalid_candidates_even_beside_a_correct_amount(header):
    result = parse([*OWN_HEADERS, "direction", "amount", header], [[*OWN_ROW, "out", "12.34", "1234"]])
    assert not result["rows"]
    assert_unresolved(result, "unknown_unit")


@pytest.mark.parametrize("header", [
    "amount/unit:cent/元", "amount//cent", "amount/", "amount/unit:cent estimated/元",
])
def test_malformed_slash_unit_remains_a_validator_beside_a_correct_amount(header):
    result = parse([*OWN_HEADERS, "direction", "amount", header], [[*OWN_ROW, "out", "12.34", "1234"]])
    assert not result["rows"]
    assert_unresolved(result, "unknown_unit")
    invalid = [item for item in result["mapping_evidence"] if item["column"] == header]
    assert len(invalid) == 1
    assert (invalid[0]["field"], invalid[0]["unit_factor"]) == ("amount", "")


def test_v10_keeps_ignoring_the_historical_malformed_slash_column():
    result = parse([*OWN_HEADERS, "direction", "amount", "amount/unit:cent/元"],
                   [[*OWN_ROW, "out", "12.34", "1234"]], profile="funds-trace-table-v10")
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0]["amount"] == Decimal("12.34")
    assert not any(item["column"] == "amount/unit:cent/元" for item in result["mapping_evidence"])


@pytest.mark.parametrize("header", [
    "amount(unit:cent", "amount[unit:cent", "amount(unit:cent)备注", "amount[unit:cent] estimated",
])
def test_incomplete_or_trailing_annotations_do_not_become_valid_amount_headers(header):
    result = parse([*OWN_HEADERS, "direction", header], [[*OWN_ROW, "out", "1234"]])
    assert not result["rows"]
    assert result["stats"]["accepted"] == 0


@pytest.mark.parametrize("header,direction,payer", [
    ("credit/debit", "cr", "SYN-OTHER"),
    ("debit/credit", "dr", "SYN-OWN"),
    ("dr/cr", "cr", "SYN-OTHER"),
    ("cr/dr", "dr", "SYN-OWN"),
    ("credit/debit indicator", "cr", "SYN-OTHER"),
    ("debit/credit indicator", "dr", "SYN-OWN"),
])
def test_complete_slash_direction_headers_precede_monetary_unit_grammar(header, direction, payer):
    result = parse([*OWN_HEADERS, header, "amount / UNIT: cent"], [[*OWN_ROW, direction, "1234"]])
    assert result["stats"]["accepted"] == 1
    assert result["rows"][0]["amount"] == Decimal("12.34")
    assert result["rows"][0]["payer_account"] == payer
    evidence = [item for item in result["mapping_evidence"] if item["column"] == header]
    assert len(evidence) == 1
    assert evidence[0]["field"] == "direction"


def test_last_of_106_records_still_validates_all_amount_candidates_after_conversion():
    headers = [*OWN_HEADERS, "direction", "amount", "交易金额(unit:cent)"]
    result = parse(headers, [[*OWN_ROW, "out", "12.34", "1234"] for _ in range(105)]
                   + [[*OWN_ROW, "out", "12.34", "1235"]])
    assert result["stats"]["accepted"] == 105
    assert result["stats"]["unresolved"] == 1
    assert result["records"][-1]["source_row"] == 107
    assert_unresolved(result, "conflicting_amount", -1)


@pytest.mark.parametrize("mapping", [None, {"amount": "amount"}, {"amount": "交易金额(unit:cent)"}])
def test_mapping_never_suppresses_a_conflicting_declared_amount_candidate(mapping):
    result = parse([*OWN_HEADERS, "direction", "amount", "交易金额(unit:cent)"],
                   [[*OWN_ROW, "out", "12.34", "1235"]], mapping=mapping)
    assert not result["rows"]
    assert_unresolved(result, "conflicting_amount")


@pytest.mark.parametrize("unit,issue", [("元", "conflicting_unit"), ("-cent", "unknown_unit")])
def test_explicit_row_unit_remains_an_independent_validator(unit, issue):
    result = parse([*OWN_HEADERS, "direction", "amount(unit:cent)", "amount unit"],
                   [[*OWN_ROW, "out", "1234", unit]])
    assert not result["rows"]
    assert_unresolved(result, issue)


def test_xlsx_second_sheet_and_reordered_header_regions_keep_their_own_units_and_fields():
    first = [*OWN_HEADERS, "direction", "amount(unit:cent)"]
    reordered = ["amount[UNIT:万元]", "counterparty account", "date", "direction", "account"]
    second = ["交易日期(YYYY-MM-DD)", "account", "counterparty account", "direction", "amount/unit:CNY"]
    result = workbook_plan([
        ("Synthetic First", [first, [*OWN_ROW, "out", "1234"], reordered,
                             ["0.001234", "SYN-SECOND-OTHER", "2026-01-03", "in", "SYN-SECOND-OWN"]]),
        ("Synthetic Second", [second, ["2026-01-04", "SYN-THIRD-OWN", "SYN-THIRD-OTHER", "out", "12.34"]]),
    ])
    assert result["stats"]["accepted"] == 3
    assert result["stats"]["reason_counts"] == {"transaction_valid": 3, "repeated_header": 1}
    assert [doc["header_rows"] for doc in result["documents"]] == [[1, 3], [1]]
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["trade_time"]) for row in result["rows"]] == [
        ("SYN-OWN", "SYN-OTHER", Decimal("12.34"), dt.datetime(2026, 1, 2, 10, 20, 30)),
        ("SYN-SECOND-OTHER", "SYN-SECOND-OWN", Decimal("12.34"), dt.datetime(2026, 1, 3)),
        ("SYN-THIRD-OWN", "SYN-THIRD-OTHER", Decimal("12.34"), dt.datetime(2026, 1, 4)),
    ]


@pytest.mark.parametrize("header,value,issue", [
    ("amount(unit:cent)", "0.1", "amount_precision"),
    ("amount(unit:万元)", "1000000000000", "amount_overflow"),
    ("amount(unit:cent)", "1e-1001", "amount_precision"),
])
def test_unit_marker_does_not_relax_decimal_precision_or_storage_range(header, value, issue):
    with localcontext() as context:
        context.prec = 6
        result = parse([*OWN_HEADERS, "direction", header], [[*OWN_ROW, "out", value]])
    assert not result["rows"]
    assert_unresolved(result, issue)


@pytest.mark.parametrize("value,status,issue", [
    ("-1234", "success", "negative_amount"),
    ("1234", "02", "unknown_status"),
])
def test_new_unit_syntax_does_not_confer_negative_sign_or_unknown_status_authority(value, status, issue):
    result = parse([*OWN_HEADERS, "direction", "amount(unit:cent)", "status"],
                   [[*OWN_ROW, "out", value, status]])
    assert not result["rows"]
    assert_unresolved(result, issue)
