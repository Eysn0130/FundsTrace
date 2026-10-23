"""Synthetic public regressions for the bounded v9 direction vocabulary.

Complete field names declare the existing closed direction semantics. They do
not authorize ordinary signed amounts or unknown status codes, and every
financial candidate in every physical record remains a validator.
"""
import csv
import datetime as dt
from decimal import Decimal
import io

from openpyxl import Workbook
import pytest

from app import table_import


NEW_HEADERS = (
    "debit/credit indicator", "credit/debit indicator", "CR/DR",
    "借贷标识", "收支标识", "收付标识",
)
BASE_HEADERS = ["date", "account", "counterparty account"]
BASE_ROW = ["2026-01-01 12:34:56.123456", "SYN-OWN", "SYN-OTHER"]


def parse(headers, rows, *, suffix=".csv", **options):
    if suffix == ".xlsx":
        workbook = Workbook()
        workbook.active.append(headers)
        for row in rows:
            workbook.active.append(row)
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        source = output.getvalue()
    else:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        source = output.getvalue().encode("utf-8")
    options.setdefault("profile", "funds-trace-table-v9")
    return table_import.parse(source, suffix=suffix, **options)


def assert_partition(plan, total):
    assert len(plan["records"]) == total
    assert sum(plan["stats"][kind] for kind in (
        "accepted", "unresolved", "non_monetary", "non_transaction", "auxiliary")) == total
    assert len(plan["rows"]) == plan["stats"]["accepted"]
    assert sum(member["row_count"] for member in plan["members"]) == total
    assert len({record["record_key"] for record in plan["records"]}) == total
    assert [record["record_no"] for record in plan["records"]] == list(range(1, total + 1))


def assert_unresolved(record, issue):
    assert record["disposition"] == "unresolved"
    assert record["details"]["financial_effect"] == "undetermined"
    assert record["details"]["reason"]
    assert "资金统计" in record["details"]["reason"]
    assert issue in record["details"].get("validation_issues", record["reason_code"]).split(",")


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("header", NEW_HEADERS)
@pytest.mark.parametrize("direction,payer,payee", [
    ("credit", "SYN-OTHER", "SYN-OWN"),
    ("debit", "SYN-OWN", "SYN-OTHER"),
])
def test_v9_complete_direction_headers_preserve_exact_facts_with_reordered_cent_amount(
        suffix, header, direction, payer, payee):
    plan = parse([header, "amount/分", "counterparty account", "date", "account"],
                 [[direction, "123", "SYN-OTHER", BASE_ROW[0], "SYN-OWN"]], suffix=suffix)
    assert plan["profile"] == "funds-trace-table-v9"
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["payer_account"], row["payee_account"], row["amount"], row["trade_time"]) == (
        payer, payee, Decimal("1.23"), dt.datetime(2026, 1, 1, 12, 34, 56, 123456))
    record = plan["records"][0]
    assert record["source_row"] == 2
    assert record["details"]["candidate_scope"] == "all_region_candidates"
    assert record["details"]["direction_basis"] == "explicit_direction"
    assert len(record["details"]["contract_sha256"]) == 64
    evidence = [item for item in plan["mapping_evidence"] if item["field"] == "direction"]
    assert len(evidence) == 1
    assert evidence[0]["column"] == header


@pytest.mark.parametrize("header,issue", [
    ("debit/credit indicator", "unknown_unit"),
    ("credit/debit indicator", "unknown_unit"),
    ("CR/DR", "missing_direction"),
    ("借贷标识", "missing_direction"),
    ("收支标识", "missing_direction"),
    ("收付标识", "missing_direction"),
])
def test_frozen_v8_preserves_original_direction_header_failure(header, issue):
    # The same public input reproduces the original defect under the still
    # supported historical parser, without removing working implementation.
    plan = parse([*BASE_HEADERS, "amount", header], [[*BASE_ROW, "1.23", "credit"]],
                 profile="funds-trace-table-v8")
    assert plan["profile"] == "funds-trace-table-v8"
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], issue)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("new_header", NEW_HEADERS)
@pytest.mark.parametrize("mapping", [None, {"direction": "direction"}])
def test_new_direction_candidate_cannot_be_suppressed_by_existing_candidate_or_mapping(
        suffix, new_header, mapping):
    plan = parse([*BASE_HEADERS, "amount", "direction", new_header],
                 [[*BASE_ROW, "1.23", "out", "credit"]], suffix=suffix, mapping=mapping)
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "conflicting_direction")
    assert len([item for item in plan["mapping_evidence"] if item["field"] == "direction"]) == 2


@pytest.mark.parametrize("header", NEW_HEADERS)
@pytest.mark.parametrize("value", ["01", "02", "1", "2", "+in", "借贷"])
def test_header_alias_does_not_expand_closed_direction_values(header, value):
    plan = parse([*BASE_HEADERS, "amount", header], [[*BASE_ROW, "1.23", value]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "invalid_direction")


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("header", ["借贷标识", "credit/debit indicator"])
@pytest.mark.parametrize("amount,direction,payer,payee", [
    ("-1.23", "debit", "SYN-OWN", "SYN-OTHER"),
    ("1.23", "credit", "SYN-OTHER", "SYN-OWN"),
])
def test_new_alias_corroborates_explicit_signed_contract_without_losing_sign_evidence(
        suffix, header, amount, direction, payer, payee):
    plan = parse([*BASE_HEADERS, "signed amount", header],
                 [[*BASE_ROW, amount, direction]], suffix=suffix)
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    assert (plan["rows"][0]["payer_account"], plan["rows"][0]["payee_account"],
            plan["rows"][0]["amount"]) == (payer, payee, Decimal("1.23"))
    assert plan["records"][0]["details"]["amount_model"] == "account_signed"
    assert plan["records"][0]["details"]["sign_convention"]


@pytest.mark.parametrize("money_headers,money_values,direction,issue", [
    (["signed amount"], ["-1.23"], "credit", "conflicting_direction"),
    (["signed amount"], ["1.23"], "debit", "conflicting_direction"),
    (["income", "expense"], ["1.23", "0"], "debit", "conflicting_direction"),
    (["income", "expense"], ["0", "1.23"], "credit", "conflicting_direction"),
    (["income", "expense"], ["1.23", "1.23"], "credit", "conflicting_income_expense"),
    (["amount", "income", "expense"], ["1.24", "1.23", "0"], "credit", "conflicting_amount"),
])
def test_new_alias_cannot_override_signed_or_split_amount_conflicts(money_headers, money_values, direction, issue):
    plan = parse([*BASE_HEADERS, *money_headers, "debit/credit indicator"],
                 [[*BASE_ROW, *money_values, direction]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], issue)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("headers,values,issue", [
    (["amount", "借贷标识"], ["-1.23", "debit"], "negative_amount"),
    (["amount", "借贷标识", "交易是否成功"], ["1.23", "credit", "02"], "unknown_status"),
    (["amount", "借贷标识"], ["", "credit"], "missing_amount"),
    (["amount", "借贷标识"], ["1.234", "credit"], "amount_precision"),
    (["amount/DR", "借贷标识"], ["123", "credit"], "unknown_unit"),
])
def test_new_direction_headers_preserve_financial_validation(suffix, headers, values, issue):
    plan = parse([*BASE_HEADERS, *headers], [[*BASE_ROW, *values]], suffix=suffix)
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], issue)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_new_direction_header_does_not_repair_missing_time(suffix):
    plan = parse([*BASE_HEADERS, "amount", "借贷标识"],
                 [["", *BASE_ROW[1:], "1.23", "credit"]], suffix=suffix)
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "missing_time")


@pytest.mark.parametrize("formula_field", ["date", "amount", "credit/debit indicator"])
def test_excel_formula_is_not_a_financial_value_under_new_direction_alias(formula_field):
    headers = [*BASE_HEADERS, "amount", "credit/debit indicator"]
    values = [*BASE_ROW, "1.23", "credit"]
    values[headers.index(formula_field)] = "=1+1"
    plan = parse(headers, [values], suffix=".xlsx")
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "formula_value")


@pytest.mark.parametrize("lookalike,financial_issue", [
    ("借贷标识备注", None), ("收支标识说明", None), ("收付标识备注", None),
    ("原交易方向", None), ("信用额度", None), ("credit limit", None),
    # Preserve the existing strict monetary-prefix slash rule: these are not
    # direction declarations, nor may their unknown unit be ignored.
    ("debit/credit indicator remarks", "unknown_unit"),
    ("credit/debit indicator code", "unknown_unit"),
    ("CR/DR备注", None), ("debit/credit indicator/分", None),
    ("debit/credit indicator(元)", None), ("credit/debit indicator(万元)", None),
    ("CR/DR(元)", None), ("借贷标识(元)", None),
    ("收支标识(元)", None), ("收付标识(万元)", None),
])
@pytest.mark.parametrize("explicit", [False, True])
def test_direction_header_match_requires_complete_field_name(lookalike, financial_issue, explicit):
    headers = [*BASE_HEADERS, "amount", lookalike]
    values = [*BASE_ROW, "1.23", "not a direction"]
    if explicit:
        headers.append("direction")
        values.append("out")
    plan = parse(headers, [values])
    assert_partition(plan, 1)
    evidence = [item for item in plan["mapping_evidence"] if item["field"] == "direction"]
    assert [item["column"] for item in evidence] == (["direction"] if explicit else [])
    if explicit and not financial_issue:
        assert plan["stats"]["accepted"] == 1
        assert (plan["rows"][0]["payer_account"], plan["rows"][0]["payee_account"]) == (
            "SYN-OWN", "SYN-OTHER")
    else:
        assert not plan["rows"]
        assert_unresolved(plan["records"][0], financial_issue or "missing_direction")


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_last_record_is_validated_after_many_consistent_direction_candidates(suffix):
    headers = [*BASE_HEADERS, "amount/分", "direction", "credit/debit indicator"]
    valid = [*BASE_ROW, "123", "in", "credit"]
    rows = [list(valid) for _ in range(127)]
    rows.append([*BASE_ROW, "123", "in", "debit"])
    plan = parse(headers, rows, suffix=suffix)
    assert_partition(plan, 128)
    assert (plan["stats"]["accepted"], plan["stats"]["unresolved"]) == (127, 1)
    assert plan["stats"]["currency_totals"] == {"CNY": "156.21"}
    assert_unresolved(plan["records"][-1], "conflicting_direction")
    assert plan["records"][-1]["source_row"] == 129


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_later_reordered_region_uses_its_own_full_direction_and_unit_candidates(suffix):
    first_headers = [*BASE_HEADERS, "amount/分", "借贷标识", "memo"]
    second_headers = ["CR/DR", "date", "account", "amount/元", "counterparty account", "direction"]
    plan = parse(first_headers, [
        [*BASE_ROW, "123", "credit", "synthetic"],
        second_headers,
        ["debit", BASE_ROW[0], "SYN-OWN", "2.34", "SYN-OTHER", "out"],
        ["credit", BASE_ROW[0], "SYN-OWN", "2.34", "SYN-OTHER", "out"],
    ], suffix=suffix)
    assert_partition(plan, 4)
    assert (plan["stats"]["accepted"], plan["stats"]["unresolved"], plan["stats"]["non_transaction"]) == (2, 1, 1)
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
        ("SYN-OTHER", "SYN-OWN", Decimal("1.23")),
        ("SYN-OWN", "SYN-OTHER", Decimal("2.34")),
    ]
    assert plan["records"][1]["reason_code"] == "repeated_header"
    assert_unresolved(plan["records"][-1], "conflicting_direction")
    assert plan["documents"][0]["header_rows"] == [1, 3]


@pytest.mark.parametrize("profile", ["funds-trace-table-v8", "funds-trace-table-v9"])
@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_v8_booked_pair_recovery_is_preserved_alongside_new_v9_direction_headers(profile, suffix):
    plan = parse([*BASE_HEADERS, "amount", "交易前账面余额", "交易后账面余额"],
                 [[*BASE_ROW, "1.23", "-100.00", "-101.23"]], profile=profile, suffix=suffix)
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    assert (plan["rows"][0]["payer_account"], plan["rows"][0]["payee_account"],
            plan["rows"][0]["amount"], plan["rows"][0]["balance"]) == (
        "SYN-OWN", "SYN-OTHER", Decimal("1.23"), Decimal("-101.23"))
    assert plan["records"][0]["details"]["direction_basis"] == "same_entry_book_balances"
