"""Synthetic public expectations for deterministic header/role resolution.

Examples describe actual source facts independently of the candidate generator.
No financial rule is relaxed merely because a second identity layout is present.
"""
import csv
import datetime as dt
from decimal import Decimal
import io

from openpyxl import Workbook
import pytest

from app import table_import as tables
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def encoded_table(headers, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def parse(headers, rows, **options):
    return tables.parse(encoded_table(headers, rows), suffix=".csv", **options)


def assert_partition(plan, total):
    assert len(plan["records"]) == total
    assert sum(plan["stats"][kind] for kind in (
        "accepted", "unresolved", "non_monetary", "non_transaction", "auxiliary")) == total
    assert sum(member["row_count"] for member in plan["members"]) == total
    assert [record["record_no"] for record in plan["records"]] == list(range(1, total + 1))
    assert len(plan["rows"]) == plan["stats"]["accepted"]


def assert_issue(record, code):
    assert record["disposition"] == "unresolved"
    assert code in record["details"].get("validation_issues", record["reason_code"]).split(",")
    assert record["details"]["financial_effect"] == "undetermined"
    assert record["details"]["reason"]


B_HEADERS = ["交易时间", "本方账户号码", "本方账户名称", "对手方账户号码",
             "对手方账户名称", "对手方开户银行名称", "收支标志", "交易金额"]
B_ROW = ["2026-01-01 10:11:12.123456", "00000123456789012345", "Synthetic own",
         "00000987654321098765", "Synthetic counter", "Synthetic counter bank", "支出", "1.23"]


@pytest.mark.parametrize("headers", [
    B_HEADERS,
    ["transaction datetime", "own account number", "own account name", "counterparty account number",
     "counterparty account name", "counterparty bank name", "direction", "transaction amount"],
])
def test_composed_role_identity_headers_preserve_accounts_and_exact_facts(headers):
    plan = parse(headers, [B_ROW])
    assert_partition(plan, 1)
    assert plan["stats"]["unresolved"] == 0
    row = plan["rows"][0]
    assert (row["payer_account"], row["payer_name"], row["payee_account"], row["payee_name"], row["payee_bank"]) == (
        "00000123456789012345", "Synthetic own", "00000987654321098765", "Synthetic counter", "Synthetic counter bank")
    assert row["amount"] == Decimal("1.23")
    assert row["trade_time"] == dt.datetime(2026, 1, 1, 10, 11, 12, 123456)


@pytest.mark.parametrize("headers", [
    ["交易时间", "付款方账户号码", "付款方账户名称", "付款方开户银行名称",
     "收款方账户号码", "收款方账户名称", "收款方开户银行名称", "交易金额"],
    ["transaction date", "payer account number", "payer account name", "payer bank name",
     "payee account number", "payee account name", "payee bank name", "transaction amount"],
])
def test_explicit_payer_payee_role_identity_combinations(headers):
    plan = parse(headers, [["2026-01-01", "00001", "Synthetic payer", "Synthetic payer bank",
                            "00002", "Synthetic payee", "Synthetic payee bank", "2.34"]])
    assert_partition(plan, 1)
    row = plan["rows"][0]
    assert tuple(row[key] for key in ("payer_account", "payer_name", "payer_bank", "payee_account", "payee_name", "payee_bank")) == (
        "00001", "Synthetic payer", "Synthetic payer bank", "00002", "Synthetic payee", "Synthetic payee bank")


@pytest.mark.parametrize("payer_header", ["原付款方账户号码", "付款方账户号码说明"])
def test_role_grammar_is_anchored_and_does_not_swallow_other_header_meanings(payer_header):
    plan = parse(["date", payer_header, "收款方账户号码", "amount"], [["2026-01-01", "00001", "00002", "1.23"]])
    assert not plan["rows"]
    assert all(record["disposition"] == "unresolved" for record in plan["records"])


DUAL_HEADERS = ["date", "payer_account", "payer name", "payer bank", "payee_account", "payee name", "payee bank",
                "account", "account name", "counterparty account", "counterparty name", "counterparty bank", "direction", "amount"]
DUAL_ROW = ["2026-01-01", "00001", "Synthetic payer", "Synthetic payer bank",
            "00002", "Synthetic payee", "Synthetic payee bank", "00001", "Synthetic payer",
            "00002", "Synthetic payee", "Synthetic payee bank", "out", "1.23"]


def changed_row(**values):
    row = list(DUAL_ROW)
    for header, value in values.items():
        row[DUAL_HEADERS.index(header)] = value
    return row


def test_same_table_dual_layout_corroborates_each_role():
    plan = parse(DUAL_HEADERS, [DUAL_ROW])
    assert_partition(plan, 1)
    row = plan["rows"][0]
    assert (row["payer_account"], row["payee_account"], row["self_account"]) == ("00001", "00002", "00001")
    assert row["amount"] == Decimal("1.23")


@pytest.mark.parametrize("changes", [
    {"counterparty account": "00003"},
    {"counterparty name": "Synthetic different payee"},
    {"counterparty bank": "Synthetic different bank"},
])
def test_other_layout_cannot_be_ignored_when_nonempty_identity_conflicts(changes):
    plan = parse(DUAL_HEADERS, [changed_row(**changes)])
    assert not plan["rows"]
    assert_issue(plan["records"][0], "conflicting_field")
    assert_partition(plan, 1)


def test_explicit_direction_allows_missing_identity_completion_but_preserves_existing_party():
    plan = parse(DUAL_HEADERS, [changed_row(**{"payer_account": "", "payer name": ""})])
    row = plan["rows"][0]
    assert (row["payer_account"], row["payer_name"], row["payee_account"], row["payee_name"]) == (
        "00001", "Synthetic payer", "00002", "Synthetic payee")


@pytest.mark.parametrize("extra_header,extra_value", [
    ("payee_account", "00003"), ("payee name", "Synthetic different payee"),
])
def test_even_partial_explicit_party_columns_cannot_be_overwritten_by_own_counter_layout(extra_header, extra_value):
    headers = ["date", "account", "counterparty account", "counterparty name", "direction", "amount", extra_header]
    plan = parse(headers, [["2026-01-01", "00001", "00002", "Synthetic payee", "out", "1.23", extra_value]])
    assert not plan["rows"]
    assert_issue(plan["records"][0], "conflicting_field")


def test_new_identity_headers_do_not_supply_missing_financial_direction():
    row = list(B_ROW)
    row[-2] = ""
    plan = parse(B_HEADERS, [row])
    assert not plan["rows"]
    assert_issue(plan["records"][0], "missing_direction")


def test_candidate_identity_conflict_after_first_hundred_rows_is_still_validated():
    rows = [list(DUAL_ROW) for _ in range(127)]
    rows.append(changed_row(**{"counterparty account": "00003"}))
    plan = parse(DUAL_HEADERS, rows)
    assert_partition(plan, 128)
    assert len(plan["rows"]) == 127
    assert_issue(plan["records"][-1], "conflicting_field")
    assert plan["records"][-1]["source_row"] == 129
    assert plan["stats"]["currency_totals"] == {"CNY": "156.21"}


@pytest.mark.parametrize("extra_header,extra_value,changes,issue", [
    (None, None, {"amount": "-1.23"}, "negative_amount"),
    ("交易是否成功", "02", {}, "unknown_status"),
    (None, None, {"amount": "not-money"}, "invalid_amount"),
    ("交易金额(分)", "124", {}, "conflicting_amount"),
])
def test_candidate_layout_selection_cannot_wash_out_financial_conflicts(extra_header, extra_value, changes, issue):
    headers, row = list(DUAL_HEADERS), changed_row(**changes)
    if extra_header:
        headers.append(extra_header)
        row.append(extra_value)
    plan = parse(headers, [row])
    assert_partition(plan, 1)
    assert not plan["rows"]
    assert_issue(plan["records"][0], issue)


def test_column_permutation_and_csv_xlsx_serialization_preserve_canonical_facts():
    order = [7, 4, 0, 5, 2, 6, 1, 3]
    headers = [B_HEADERS[index] for index in order]
    values = [B_ROW[index] for index in order]
    workbook = Workbook()
    workbook.active.append(headers)
    workbook.active.append(values)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    plans = [parse(B_HEADERS, [B_ROW]), parse(headers, [values]), tables.parse(buffer.getvalue(), suffix=".xlsx")]
    expected = ("00000123456789012345", "00000987654321098765", "Synthetic counter bank", Decimal("1.23"), "CNY")
    for plan in plans:
        assert_partition(plan, 1)
        row = plan["rows"][0]
        assert tuple(row[key] for key in ("payer_account", "payee_account", "payee_bank", "amount", "currency")) == expected


def test_multiple_identity_candidates_must_agree_instead_of_first_column_winning():
    headers = [*B_HEADERS, "对方账号"]
    plan = parse(headers, [[*B_ROW, B_ROW[3]], [*B_ROW, "00003"]])
    assert_partition(plan, 2)
    assert len(plan["rows"]) == 1
    assert "counter_account" not in plan["mapping"]
    assert_issue(plan["records"][1], "conflicting_field")


@pytest.mark.parametrize("profile", ["funds-trace-table-v6", "funds-trace-table-v7"])
def test_explicit_v5_replay_keeps_old_headers_and_old_dual_role_behavior(profile):
    old = parse(B_HEADERS, [B_ROW], profile="funds-trace-table-v5")
    assert old["profile"] == "funds-trace-table-v5"
    assert not old["rows"]
    historical = parse(DUAL_HEADERS, [changed_row(**{"counterparty account": "00003"})], profile="funds-trace-table-v5")
    assert historical["rows"][0]["payee_account"] == "00002"
    current = parse(DUAL_HEADERS, [changed_row(**{"counterparty account": "00003"})], profile=profile)
    assert current["profile"] == profile
    assert not current["rows"]
    assert_issue(current["records"][0], "conflicting_field")


def test_public_import_automates_new_headers_and_retains_unresolved_sources_without_user_gate(client, database, make_case):
    cid = make_case("Synthetic deterministic mapping")
    headers = [*B_HEADERS, "对方账号", "交易是否成功"]
    valid = [*B_ROW, B_ROW[3], "01"]
    negative = list(valid)
    negative[7] = "-1.23"
    conflicting = list(valid)
    conflicting[8] = "00003"
    unknown_status = list(valid)
    unknown_status[-1] = "02"
    content = encoded_table(headers, [valid, negative, conflicting, unknown_status])
    view = preview(client, cid, content)
    assert view["automatic"] is True
    assert view["table_summary"]["profile"] == tables.PROFILE
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (1, 3)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 1
    rows = database.q_raw("select payer_account,payee_account,amount,currency from flows where case_id=?", [cid])
    assert rows == [{"payer_account": B_ROW[1], "payee_account": B_ROW[3], "amount": Decimal("1.23"), "currency": "CNY"}]
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()
    assert [record["source_row"] for record in report["items"]] == [2, 3, 4, 5]
    assert [record["disposition"] for record in report["items"]] == ["imported", "unresolved", "unresolved", "unresolved"]
    for record, issue in zip(report["items"][1:], ("negative_amount", "conflicting_field", "unknown_status")):
        assert_issue(record, issue)
        assert record["flow_id"] is None
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
    coverage = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert coverage["unresolved_records"] == 3
    assert coverage["financial_interpretation_complete"] is False
    repeated = complete(client, cid, preview(client, cid, content, "same-source.csv"))
    assert repeated["stage"] == "done"
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 1)
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"] == coverage
