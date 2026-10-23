"""Synthetic public regressions for the bounded v8 source contracts.

The independent expectations describe account effects and exact balances.  A
balance equality is evidence only for the same transaction's declared before
and after balances; it cannot authorize a different amount model or status.
"""
import csv
import datetime as dt
from decimal import Decimal
import io
import re

from openpyxl import Workbook
import pytest

from app import table_import


def encoded_table(headers, rows, suffix=".csv"):
    output = io.BytesIO()
    if suffix == ".xlsx":
        workbook = Workbook()
        workbook.active.append(headers)
        for row in rows:
            workbook.active.append(row)
        workbook.save(output)
        workbook.close()
        return output.getvalue()
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(headers)
    writer.writerows(rows)
    return text.getvalue().encode("utf-8")


def parse(headers, rows, *, suffix=".csv", **options):
    options.setdefault("profile", table_import.CONTRACT_PROFILE)
    return table_import.parse(encoded_table(headers, rows, suffix), suffix=suffix, **options)


def assert_partition(plan, total):
    assert len(plan["records"]) == total
    assert sum(plan["stats"][kind] for kind in (
        "accepted", "unresolved", "non_monetary", "non_transaction", "auxiliary")) == total
    assert len(plan["rows"]) == plan["stats"]["accepted"]
    assert sum(member["row_count"] for member in plan["members"]) == total
    assert [record["record_no"] for record in plan["records"]] == list(range(1, total + 1))


def assert_unresolved(record, issue=None):
    assert record["disposition"] == "unresolved"
    assert record["details"]["financial_effect"] == "undetermined"
    assert record["details"]["reason"]
    assert "资金统计" in record["details"]["reason"]
    assert record["record_key"]
    assert record["source_row"] >= 2
    if issue is not None:
        assert issue in record["details"].get("validation_issues", record["reason_code"]).split(",")


OWN_HEADERS = ["date", "account", "counterparty account"]
OWN_ROW = ["2026-01-01 10:20:30", "SYN-OWN", "SYN-OTHER"]
PAIR_HEADERS = [*OWN_HEADERS, "amount", "交易前账面余额", "交易后账面余额"]
GENERIC_PAIR_HEADERS = [*OWN_HEADERS, "amount", "交易前余额", "交易后余额"]


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("amount,direction,payer,payee", [
    ("-1.23", "out", "SYN-OWN", "SYN-OTHER"),
    ("1.23", "in", "SYN-OTHER", "SYN-OWN"),
    ("-1.23", "", "SYN-OWN", "SYN-OTHER"),
    ("1.23", "", "SYN-OTHER", "SYN-OWN"),
])
def test_signed_source_contract_preserves_direction_and_exact_magnitude(suffix, amount, direction, payer, payee):
    plan = parse([*OWN_HEADERS, "signed amount", "direction"],
                 [[*OWN_ROW, amount, direction]], suffix=suffix)
    assert plan["profile"] == "funds-trace-table-v8"
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["payer_account"], row["payee_account"], row["amount"], row["trade_time"]) == (
        payer, payee, Decimal("1.23"), dt.datetime(2026, 1, 1, 10, 20, 30))
    assert plan["records"][0]["details"]["sign_convention"]
    details = plan["records"][0]["details"]
    assert details["source_contract"]
    assert re.fullmatch(r"[0-9a-f]{64}", details["contract_sha256"])
    assert details["amount_model"]
    assert details["candidate_scope"]
    assert details["direction_basis"]


@pytest.mark.parametrize("amount,direction", [("-1.23", "in"), ("1.23", "out")])
def test_signed_source_contract_rejects_flag_conflict_even_when_balance_supports_flag(amount, direction):
    before, after = ("100.00", "101.23") if direction == "in" else ("100.00", "98.77")
    plan = parse([*OWN_HEADERS, "signed amount", "direction", "交易前账面余额", "交易后账面余额"],
                 [[*OWN_ROW, amount, direction, before, after]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "conflicting_direction")


@pytest.mark.parametrize("direction", ["out", ""])
def test_ordinary_negative_amount_does_not_gain_sign_authority_from_flag_or_balances(direction):
    plan = parse([*PAIR_HEADERS, "direction"], [[*OWN_ROW, "-1.23", "100.00", "98.77", direction]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "negative_amount")


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("before_header,after_header", [
    ("交易前账面余额", "交易后账面余额"),
    ("本笔交易前账面余额", "本笔交易后账面余额"),
])
@pytest.mark.parametrize("before,after,payer,payee", [
    ("100.00", "98.77", "SYN-OWN", "SYN-OTHER"),
    ("100.00", "101.23", "SYN-OTHER", "SYN-OWN"),
    ("-100.00", "-101.23", "SYN-OWN", "SYN-OTHER"),
    ("-100.00", "-98.77", "SYN-OTHER", "SYN-OWN"),
])
def test_declared_same_transaction_before_after_balances_supply_direction(
        suffix, before_header, after_header, before, after, payer, payee):
    plan = parse([*OWN_HEADERS, "amount", before_header, after_header],
                 [[*OWN_ROW, "1.23", before, after]], suffix=suffix)
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["payer_account"], row["payee_account"], row["amount"], row["balance"]) == (
        payer, payee, Decimal("1.23"), Decimal(after))
    assert plan["records"][0]["details"]["balance_basis"]
    assert plan["records"][0]["details"]["direction_basis"]


def test_declared_pair_respects_independent_money_units_without_rounding():
    plan = parse([*OWN_HEADERS, "amount/分", "交易前账面余额/元", "交易后账面余额/元"],
                 [[*OWN_ROW, "123", "-100.00", "-101.23"]])
    assert_partition(plan, 1)
    assert plan["rows"][0]["amount"] == Decimal("1.23")
    assert plan["rows"][0]["balance"] == Decimal("-101.23")
    assert plan["rows"][0]["payer_account"] == "SYN-OWN"


def test_explicit_before_balance_unit_is_scoped_to_before_balance():
    plan = parse([*OWN_HEADERS, "amount/分", "交易前账面余额", "交易后账面余额/元", "交易前账面余额单位"],
                 [[*OWN_ROW, "123", "10000", "98.77", "分"]])
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["amount"], row["balance"], row["payer_account"]) == (
        Decimal("1.23"), Decimal("98.77"), "SYN-OWN")


@pytest.mark.parametrize("unit_column,unit_value", [
    ("交易前账面余额单位", "unknown"),
    ("交易前账面余额单位", "分"),
])
def test_bad_before_balance_unit_cannot_be_hidden_by_an_explicit_direction(unit_column, unit_value):
    plan = parse([*OWN_HEADERS, "amount", "交易前账面余额/元", "交易后账面余额/元", "direction", unit_column],
                 [[*OWN_ROW, "1.23", "100.00", "98.77", "out", unit_value]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "unknown_unit" if unit_value == "unknown" else "conflicting_unit")


@pytest.mark.parametrize("before,after,expected_balance", [
    ("", "98.77", Decimal("98.77")),
    ("100.00", "", None),
    ("", "", None),
])
def test_incomplete_balance_pair_does_not_break_an_otherwise_known_direction(before, after, expected_balance):
    plan = parse([*PAIR_HEADERS, "direction"], [[*OWN_ROW, "1.23", before, after, "out"]])
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    assert plan["rows"][0]["payer_account"] == "SYN-OWN"
    assert plan["rows"][0]["balance"] == expected_balance


@pytest.mark.parametrize("before,after", [("", "98.77"), ("100.00", ""), ("", "")])
def test_incomplete_pair_cannot_supply_missing_direction(before, after):
    plan = parse(PAIR_HEADERS, [[*OWN_ROW, "1.23", before, after]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "missing_direction")


def test_same_row_pair_does_not_need_or_invent_a_cross_row_chronology():
    plan = parse([*PAIR_HEADERS, "currency"], [
        ["2026-01-02 10:00:00", "SYN-OWN", "SYN-OTHER", "1.23", "100.00", "98.77", "CNY"],
        ["2026-01-01 10:00:00", "SYN-OWN", "SYN-OTHER", "2.34", "-100.00", "-97.66", "USD"],
    ])
    assert_partition(plan, 2)
    assert plan["stats"]["accepted"] == 2
    assert plan["stats"]["currency_totals"] == {"CNY": "1.23", "USD": "2.34"}
    assert [(row["payer_account"], row["payee_account"], row["balance"]) for row in plan["rows"]] == [
        ("SYN-OWN", "SYN-OTHER", Decimal("98.77")),
        ("SYN-OTHER", "SYN-OWN", Decimal("-97.66")),
    ]


@pytest.mark.parametrize("extra_headers,extra_values", [
    (["direction"], ["in"]),
    (["signed amount"], ["1.23"]),
    (["income", "expense"], ["1.23", "0"]),
])
def test_declared_balance_effect_must_agree_with_every_explicit_effect(extra_headers, extra_values):
    plan = parse([*PAIR_HEADERS, *extra_headers],
                 [[*OWN_ROW, "1.23", "100.00", "98.77", *extra_values]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "balance_direction_conflict")


@pytest.mark.parametrize("after", ["98.78", "98.75", "98.77", "100.00"])
def test_balance_effect_comparison_is_exact_and_never_uses_one_percent_or_one_cent_tolerance(after):
    plan = parse([*PAIR_HEADERS, "direction"], [[*OWN_ROW, "1.24", "100.00", after, "out"]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "balance_amount_conflict")


@pytest.mark.parametrize("balance_header", ["余额", "running balance", "交易后余额"])
def test_running_balances_without_declared_same_row_before_balance_do_not_infer_direction(balance_header):
    plan = parse([*OWN_HEADERS, "amount", balance_header, "direction"], [
        ["2026-01-01 10:00:00", "SYN-OWN", "SYN-OTHER", "1.23", "100.00", "out"],
        ["2026-01-01 10:01:00", "SYN-OWN", "SYN-OTHER", "1.23", "98.77", ""],
        ["2026-01-01 10:02:00", "SYN-OWN", "SYN-OTHER", "1.23", "100.00", "in"],
    ])
    assert_partition(plan, 3)
    assert plan["stats"]["accepted"] == 2
    assert_unresolved(plan["records"][1], "missing_direction")


def test_available_balance_words_do_not_declare_booked_transaction_balance_authority():
    plan = parse([*OWN_HEADERS, "amount", "交易前可用余额", "交易后可用余额"],
                 [[*OWN_ROW, "1.23", "100.00", "98.77"]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "missing_direction")


@pytest.mark.parametrize("before_header,after_header", [
    ("交易前余额", "交易后余额"),
    ("本笔交易前余额", "本笔交易后余额"),
    ("交易前账面余额", "交易后余额"),
    ("交易前余额", "交易后账面余额"),
])
def test_pair_without_both_explicit_booked_balance_declarations_cannot_supply_direction(before_header, after_header):
    plan = parse([*OWN_HEADERS, "amount", before_header, after_header],
                 [[*OWN_ROW, "1.23", "100.00", "98.77"]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "missing_direction")


@pytest.mark.parametrize("headers,values,issue", [
    (PAIR_HEADERS, [*OWN_ROW, "", "100.00", "98.77"], "missing_amount"),
    (PAIR_HEADERS, ["", *OWN_ROW[1:], "1.23", "100.00", "98.77"], "missing_time"),
    ([*PAIR_HEADERS, "交易是否成功"], [*OWN_ROW, "1.23", "100.00", "98.77", "02"], "unknown_status"),
    ([*PAIR_HEADERS, "金额单位"], [*OWN_ROW, "1.23", "100.00", "98.77", "unknown"], "unknown_unit"),
    ([*PAIR_HEADERS, "currency", "交易币种"],
     [*OWN_ROW, "1.23", "100.00", "98.77", "CNY", "USD"], "invalid_currency"),
    ([*OWN_HEADERS, "amount/人民币元", "交易前账面余额", "交易后账面余额", "currency"],
     [*OWN_ROW, "1.23", "100.00", "98.77", "USD"], "currency_unit_conflict"),
])
def test_balance_recovery_cannot_bypass_other_required_financial_validation(headers, values, issue):
    plan = parse(headers, [values])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], issue)


@pytest.mark.parametrize("column", ["amount", "交易前账面余额", "交易后账面余额", "date"])
def test_excel_formulas_cannot_be_used_as_balance_recovery_evidence(column):
    values = [*OWN_ROW, "1.23", "100.00", "98.77"]
    values[PAIR_HEADERS.index(column)] = "=1+1"
    plan = parse(PAIR_HEADERS, [values], suffix=".xlsx")
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "formula_value")


@pytest.mark.parametrize("extra_header,extra_value", [
    ("本笔交易前账面余额", "100.01"),
    ("本笔交易后账面余额", "98.76"),
])
def test_every_nonempty_same_transaction_balance_candidate_must_agree(extra_header, extra_value):
    plan = parse([*PAIR_HEADERS, extra_header], [[*OWN_ROW, "1.23", "100.00", "98.77", extra_value]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0])


def test_late_balance_candidate_conflict_is_not_hidden_by_preview_sampling():
    valid = [*OWN_ROW, "1.23", "100.00", "98.77", "100.00", "98.77"]
    rows = [list(valid) for _ in range(127)]
    rows.append([*valid[:-1], "98.76"])
    plan = parse([*PAIR_HEADERS, "本笔交易前账面余额", "本笔交易后账面余额"], rows)
    assert_partition(plan, 128)
    assert plan["stats"]["accepted"] == 127
    assert plan["stats"]["currency_totals"] == {"CNY": "156.21"}
    assert_unresolved(plan["records"][-1])
    assert plan["records"][-1]["source_row"] == 129


def test_signed_column_on_another_row_does_not_reinterpret_ordinary_negative_amounts():
    plan = parse([*OWN_HEADERS, "amount", "signed amount", "direction", "交易前账面余额", "交易后账面余额"], [
        [*OWN_ROW, "", "-1.23", "out", "100.00", "98.77"],
        ["2026-01-01 10:21:30", *OWN_ROW[1:], "-1.23", "", "out", "98.77", "97.54"],
    ])
    assert_partition(plan, 2)
    assert plan["stats"]["accepted"] == 1
    assert plan["rows"][0]["amount"] == Decimal("1.23")
    assert_unresolved(plan["records"][1], "negative_amount")


def test_mixed_party_and_own_account_layout_cannot_ignore_booked_balance_conflicts():
    plan = parse(["date", "payer account", "payee account", "account", "counterparty account",
                  "amount", "direction", "交易前账面余额", "交易后账面余额"],
                 [["2026-01-01", "SYN-A", "SYN-B", "SYN-A", "SYN-B", "10", "out", "100", "105"]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "balance_amount_conflict")
    assert_unresolved(plan["records"][0], "balance_direction_conflict")


def test_mixed_explicit_parties_supply_own_effect_for_booked_balance_validation_without_flag():
    plan = parse(["date", "payer account", "payee account", "account", "counterparty account",
                  "amount", "交易前账面余额", "交易后账面余额"],
                 [["2026-01-01", "SYN-A", "SYN-B", "SYN-A", "SYN-B", "10", "100", "110"]])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "balance_direction_conflict")


def test_pure_explicit_parties_do_not_assign_unspecified_balances_to_either_party():
    plan = parse(["date", "payer account", "payee account", "amount", "交易前账面余额", "交易后账面余额"],
                 [["2026-01-01", "SYN-A", "SYN-B", "10", "100", "105"]])
    assert_partition(plan, 1)
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["payer_account"], row["payee_account"], row["amount"], row["balance"]) == (
        "SYN-A", "SYN-B", Decimal("10.00"), Decimal("105.00"))
    assert plan["records"][0]["details"]["balance_basis"] == "not_account_ledger_layout"


@pytest.mark.parametrize("profile,expected_balance", [
    ("funds-trace-table-v5", Decimal("101.23")),
    ("funds-trace-table-v6", Decimal("101.23")),
    ("funds-trace-table-v7", Decimal("101.23")),
])
def test_historical_profiles_keep_prior_output_without_new_balance_effect_validation(profile, expected_balance):
    plan = parse([*GENERIC_PAIR_HEADERS, "direction"],
                 [[*OWN_ROW, "1.23", "100.00", "101.23", "out"]], profile=profile)
    assert_partition(plan, 1)
    assert plan["profile"] == profile
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["payer_account"], row["payee_account"], row["amount"], row["balance"]) == (
        "SYN-OWN", "SYN-OTHER", Decimal("1.23"), expected_balance)
    assert not {"source_contract", "contract_sha256", "amount_model", "candidate_scope",
                "direction_basis", "balance_basis"}.intersection(plan["records"][0]["details"])


@pytest.mark.parametrize("profile", ["funds-trace-table-v5", "funds-trace-table-v6", "funds-trace-table-v7"])
def test_historical_profiles_never_gain_new_same_transaction_balance_direction_recovery(profile):
    plan = parse(GENERIC_PAIR_HEADERS, [[*OWN_ROW, "1.23", "100.00", "98.77"]], profile=profile)
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "missing_direction")
