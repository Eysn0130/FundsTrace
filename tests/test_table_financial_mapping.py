"""Independent synthetic facts for the bounded v7 financial interpretation.

The public upload/commit path remains automatic and preserves every rejected
source record; the parser examples state expected facts without its helpers.
"""
import csv
import datetime as dt
from decimal import Decimal
import io

from openpyxl import Workbook
import pytest

from app import case_packages, table_import
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def table_bytes(headers, rows):
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def parse(headers, rows, **options):
    return table_import.parse(table_bytes(headers, rows), suffix=".csv", **options)


def workbook_bytes(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets:
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def assert_unresolved(record, issue=None):
    assert record["disposition"] == "unresolved"
    assert record["details"]["financial_effect"] == "undetermined"
    assert record["details"]["reason"]
    if issue:
        assert issue in record["details"].get("validation_issues", record["reason_code"]).split(",")


@pytest.mark.parametrize("direction_header,direction,payer,payee", [
    ("收付方向", "收", "SYN-OTHER", "SYN-OWN"),
    ("借贷方向", "借", "SYN-OWN", "SYN-OTHER"),
    ("收支类型", "收入", "SYN-OTHER", "SYN-OWN"),
])
def test_v7_exact_financial_aliases_preserve_parties_amount_and_time(direction_header, direction, payer, payee):
    plan = parse(["交易日期时间", "account", "counterparty account", direction_header, "交易发生金额/分"],
                 [["2026-01-01 12:34:56.123456", "SYN-OWN", "SYN-OTHER", direction, "123"]])
    assert plan["profile"] == table_import.PROFILE
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["payer_account"], row["payee_account"], row["amount"], row["trade_time"]) == (
        payer, payee, Decimal("1.23"), dt.datetime(2026, 1, 1, 12, 34, 56, 123456))


@pytest.mark.parametrize("header,direction,payer,payee", [
    ("Credit/Debit", "credit", "SYN-OTHER", "SYN-OWN"),
    ("Debit/Credit", "debit", "SYN-OWN", "SYN-OTHER"),
])
def test_complete_slash_direction_aliases_import_with_monetary_slash_unit(client, database, make_case, header, direction, payer, payee):
    cid = make_case("Synthetic complete direction header")
    content = table_bytes(["date", "account", "counterparty account", header, "amount/分"],
                          [["2026-01-01", "SYN-OWN", "SYN-OTHER", direction, "123"]])
    job = complete(client, cid, preview(client, cid, content))
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 1
    assert database.q_raw("select payer_account,payee_account,amount from flows where case_id=?", [cid]) == [
        {"payer_account": payer, "payee_account": payee, "amount": Decimal("1.23")}]


@pytest.mark.parametrize("field,primary,other,selected,value,issue", [
    ("currency", "currency", "交易币种", "CNY", "人民币", None),
    ("currency", "currency", "交易币种", "CNY", "USD", "invalid_currency"),
    ("status", "status", "交易状态", "success", "成功", None),
    ("status", "status", "交易状态", "success", "failed", "unknown_status"),
])
def test_advanced_mapping_preserves_all_currency_and_status_candidates(client, database, make_case, field, primary, other, selected, value, issue):
    cid = make_case("Synthetic mapped financial candidates")
    content = table_bytes(["date", "payer_account", "payee_account", "amount", primary, other],
                          [["2026-01-01", "SYN-A", "SYN-B", "1.23", selected, value]])
    initial = preview(client, cid, content)
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{initial['import_id']}/preview",
                            json={"mode": initial["mode"], "mapping": {**initial["mapping"], field: primary}})
    assert rechecked.status_code == 200, rechecked.text
    view = rechecked.json()
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (
        (1, 0) if issue is None else (0, 1))
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == (1 if issue is None else 0)
    rows = database.q_raw("select amount,currency from flows where case_id=?", [cid])
    if issue is None:
        assert rows == [{"amount": Decimal("1.23"), "currency": "CNY"}]
    else:
        assert rows == []
        record = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"][0]
        assert_unresolved(record, issue)
        assert record["flow_id"] is None


@pytest.mark.parametrize("unit,amount,expected", [
    ("元", "1.23", "1.23"), ("yuan", "1.23", "1.23"),
    ("分", "123", "1.23"), ("cents", "123", "1.23"),
    ("角", "12.3", "1.23"), ("jiao", "12.3", "1.23"),
    ("千元", "0.00123", "1.23"), ("thousand yuan", "0.00123", "1.23"),
    ("万元", "0.000123", "1.23"), ("ten thousand yuan", "0.000123", "1.23"),
])
def test_independent_amount_unit_column_is_exact(unit, amount, expected):
    plan = parse(["date", "payer_account", "payee_account", "amount", "金额单位"],
                 [["2026-01-01", "SYN-A", "SYN-B", amount, unit]])
    assert plan["stats"]["accepted"] == 1
    assert plan["rows"][0]["amount"] == Decimal(expected)


@pytest.mark.parametrize("unit", ["-分", "分-", "万-元", "cen_ts"])
def test_unit_value_signs_and_internal_punctuation_cannot_create_flows(client, database, make_case, unit):
    cid = make_case("Synthetic invalid unit token")
    content = table_bytes(["date", "payer_account", "payee_account", "amount", "金额单位"],
                          [["2026-01-01", "SYN-A", "SYN-B", "123", unit]])
    view = preview(client, cid, content)
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (0, 1)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 0
    assert database.q_raw("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 0
    record = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"][0]
    assert_unresolved(record, "unknown_unit")
    assert record["flow_id"] is None


@pytest.mark.parametrize("header", ["amount/-分", "交易金额(万-元)", "amount(cen_ts)"])
def test_unit_annotations_preserve_invalid_punctuation_instead_of_rescaling(header):
    plan = parse(["date", "payer_account", "payee_account", header],
                 [["2026-01-01", "SYN-A", "SYN-B", "123"]])
    assert plan["stats"]["accepted"] == 0
    assert_unresolved(plan["records"][0], "unknown_unit")


@pytest.mark.parametrize("header", ["金额单位", "交易金额单位", "amount unit"])
def test_amount_unit_alias_does_not_scale_independent_balance_or_split_amounts(header):
    plan = parse(["date", "account", "counterparty account", "amount", header,
                  "income", "expense", "balance"],
                 [["2026-01-01", "SYN-A", "SYN-B", "123", "分", "1.23", "0", "9.87"]])
    assert plan["stats"]["accepted"] == 1
    row = plan["rows"][0]
    assert (row["amount"], row["balance"], row["payer_account"], row["payee_account"]) == (
        Decimal("1.23"), Decimal("9.87"), "SYN-B", "SYN-A")


def test_split_and_balance_units_have_separate_explicit_scopes():
    plan = parse(["date", "account", "counterparty account", "income", "收入金额单位",
                  "expense", "支出金额单位", "balance", "余额单位"], [
        ["2026-01-01", "SYN-A", "SYN-B", "123", "分", "0", "元", "0.000987", "万元"],
        ["2026-01-02", "SYN-A", "SYN-B", "0", "元", "12.3", "角", "987", "分"],
    ])
    assert plan["stats"]["accepted"] == 2
    assert [(row["amount"], row["balance"], row["payer_account"], row["payee_account"]) for row in plan["rows"]] == [
        (Decimal("1.23"), Decimal("9.87"), "SYN-B", "SYN-A"),
        (Decimal("1.23"), Decimal("9.87"), "SYN-A", "SYN-B"),
    ]


@pytest.mark.parametrize("amount_header,units,issue", [
    ("amount", ["分", "cents"], None),
    ("amount/分", ["分", "cents"], None),
    ("amount/分", ["", ""], None),
    ("amount", ["", ""], "unknown_unit"),
    ("amount", ["厘", "厘"], "unknown_unit"),
    ("amount", ["分", "元"], "conflicting_unit"),
    ("amount/元", ["分", "分"], "conflicting_unit"),
])
def test_unit_candidates_must_agree_with_each_other_and_explicit_header(amount_header, units, issue):
    plan = parse(["date", "payer_account", "payee_account", amount_header, "金额单位", "amount unit"],
                 [["2026-01-01", "SYN-A", "SYN-B", "123", *units]])
    assert bool(plan["rows"]) is (issue is None)
    if issue is None:
        assert plan["rows"][0]["amount"] == Decimal("1.23")
    else:
        assert_unresolved(plan["records"][0], issue)


def test_row_units_do_not_leak_to_later_rows_and_blank_unit_is_not_inherited():
    plan = parse(["date", "payer_account", "payee_account", "amount", "金额单位"], [
        ["2026-01-01", "SYN-A", "SYN-B", "123", "分"],
        ["2026-01-02", "SYN-A", "SYN-B", "123", "元"],
        ["2026-01-03", "SYN-A", "SYN-B", "123", ""],
    ])
    assert [row["amount"] for row in plan["rows"]] == [Decimal("1.23"), Decimal("123.00")]
    assert_unresolved(plan["records"][2], "unknown_unit")


@pytest.mark.parametrize("amount_header,unit", [("amount/人民币元", ""), ("amount", "CNY")])
def test_explicit_cny_unit_cannot_override_source_usd_currency(amount_header, unit):
    plan = parse(["date", "payer_account", "payee_account", amount_header, "金额单位", "currency"],
                 [["2026-01-01", "SYN-A", "SYN-B", "1.23", unit, "USD"]])
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "currency_unit_conflict")


def test_amount_unit_without_total_amount_never_guesses_split_scope():
    plan = parse(["date", "account", "income", "expense", "金额单位"],
                 [["2026-01-01", "SYN-A", "123", "0", "分"]])
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "unknown_unit")


@pytest.mark.parametrize("clock,other,expected", [
    ("12:34", "12:34:00", dt.time(12, 34)),
    ("123456", "12:34:56", dt.time(12, 34, 56)),
    ("12:34:56.123456", "12:34:56.123456", dt.time(12, 34, 56, 123456)),
])
def test_clock_candidates_compare_parsed_values_with_date(clock, other, expected):
    plan = parse(["date", "交易时刻", "time of day", "payer_account", "payee_account", "amount"],
                 [["2026-01-01", clock, other, "SYN-A", "SYN-B", "1.23"]])
    assert plan["stats"]["accepted"] == 1
    assert plan["rows"][0]["trade_time"] == dt.datetime.combine(dt.date(2026, 1, 1), expected)


@pytest.mark.parametrize("date,clock,other", [
    ("2026-01-01", "12345", ""),
    ("2026-01-01", "246000", ""),
    ("2026-01-01", "12:60:00", ""),
    ("2026-01-01", "12:34:56", "broken"),
    ("", "12:34:56", ""),
    ("2026-01-01 00:00:00", "12:00", ""),
])
def test_bad_clock_candidates_or_missing_date_cannot_be_ignored(date, clock, other):
    plan = parse(["date", "交易时刻", "time of day", "payer_account", "payee_account", "amount"],
                 [[date, clock, other, "SYN-A", "SYN-B", "1.23"]])
    assert not plan["rows"]
    assert_unresolved(plan["records"][0])


def test_date_only_corroboration_keeps_existing_calendar_contract():
    plan = parse(["date", "timestamp", "payer_account", "payee_account", "amount"],
                 [["2026-01-01", "2026-01-01 12:34:56.123456", "SYN-A", "SYN-B", "1.23"]])
    assert plan["rows"][0]["trade_time"] == dt.datetime(2026, 1, 1, 12, 34, 56, 123456)


@pytest.mark.parametrize("last_column,last_value,issue", [
    ("交易发生金额", "124", "conflicting_amount"),
    ("收付方向", "in", "conflicting_direction"),
    ("交易时刻", "12:34:57", "conflicting_time"),
    ("交易金额单位", "元", "conflicting_unit"),
])
def test_all_128_rows_validate_every_financial_candidate(last_column, last_value, issue):
    headers = ["date", "account", "counterparty account", "amount", "交易发生金额", "direction",
               "收付方向", "time of day", "交易时刻", "金额单位", "交易金额单位"]
    valid = ["2026-01-01", "SYN-A", "SYN-B", "123", "123", "out", "支出", "12:34:56", "123456", "分", "cents"]
    rows = [list(valid) for _ in range(128)]
    rows[-1][headers.index(last_column)] = last_value
    plan = parse(headers, rows)
    assert (plan["stats"]["accepted"], plan["stats"]["unresolved"]) == (127, 1)
    assert len(plan["records"]) == 128
    assert plan["records"][-1]["source_row"] == 129
    assert_unresolved(plan["records"][-1], issue)
    assert plan["stats"]["currency_totals"] == {"CNY": "156.21"}


@pytest.mark.parametrize("profile", ["funds-trace-table-v5", "funds-trace-table-v6"])
def test_frozen_profiles_keep_pre_v7_unit_and_clock_interpretations(profile):
    unit = parse(["date", "payer_account", "payee_account", "amount", "金额单位"],
                 [["2026-01-01", "SYN-A", "SYN-B", "123", "分"]], profile=profile)
    assert unit["profile"] == profile
    assert unit["rows"][0]["amount"] == Decimal("123.00")
    clock = parse(["date", "交易时刻", "time of day", "payer_account", "payee_account", "amount"],
                  [["2026-01-01", "12:34", "12:34:00", "SYN-A", "SYN-B", "1.23"]], profile=profile)
    assert not clock["rows"]
    assert_unresolved(clock["records"][0], "conflicting_time")


def test_csv_reordered_headers_and_xlsx_sheets_recompute_financial_scopes():
    headers = ["交易日期时间", "account", "counterparty account", "收支类型", "交易发生金额", "金额单位"]
    first = ["2026-01-01 12:34:56", "SYN-A", "SYN-B", "支出", "123", "分"]
    order = [5, 4, 2, 0, 3, 1]
    reordered = [headers[index] for index in order]
    second = ["2026-01-02 12:34:56", "SYN-C", "SYN-D", "收入", "0.000234", "万元"]
    values = [second[index] for index in order]
    csv_plan = parse(headers, [first, reordered, values])
    xlsx_plan = table_import.parse(workbook_bytes([
        ("Synthetic first", [headers, first]), ("Synthetic second", [reordered, values]),
    ]), suffix=".xlsx")
    for plan in (csv_plan, xlsx_plan):
        assert (plan["stats"]["accepted"], plan["stats"]["unresolved"]) == (2, 0)
        assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
            ("SYN-A", "SYN-B", Decimal("1.23")), ("SYN-D", "SYN-C", Decimal("2.34"))]
        assert plan["stats"]["currency_totals"] == {"CNY": "3.57"}


def test_xlsx_formula_in_financial_unit_column_remains_unresolved():
    content = workbook_bytes([("Synthetic units", [
        ["date", "payer_account", "payee_account", "amount", "金额单位"],
        ["2026-01-01", "SYN-A", "SYN-B", "123", '=\"分\"'],
    ])])
    plan = table_import.parse(content, suffix=".xlsx")
    assert not plan["rows"]
    assert_unresolved(plan["records"][0], "formula_value")


def test_signed_amount_keeps_its_explicit_b_mode_convention_with_units():
    plan = parse(["date", "account", "counterparty account", "signed amount/分"], [
        ["2026-01-01", "SYN-A", "SYN-B", "-123"],
        ["2026-01-02", "SYN-A", "SYN-B", "234"],
    ])
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", Decimal("1.23")), ("SYN-B", "SYN-A", Decimal("2.34"))]
    bilateral = parse(["date", "payer_account", "payee_account", "signed amount/分"],
                      [["2026-01-01", "SYN-A", "SYN-B", "-123"]])
    assert not bilateral["rows"]
    assert_unresolved(bilateral["records"][0], "negative_amount")


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_public_automatic_financial_import_retains_gaps_reimports_and_package(client, database, make_case, format):
    cid = make_case("Synthetic v7 source contract")
    headers = ["交易日期时间", "account", "counterparty account", "收付方向", "交易发生金额",
               "金额单位", "currency", "memo"]
    rows = [
        ["2026-01-01 12:34:56.123456", "SYN-A", "SYN-B", "支出", "123", "分", "CNY", "payment"],
        ["2026-01-02 12:34:56", "SYN-A", "SYN-B", "收入", "0.000123", "万元", "USD", "退款"],
        ["2026-01-03", "SYN-A", "SYN-B", "收入", "-123", "分", "CNY", "退款"],
        ["2026-01-04", "SYN-A", "SYN-B", "支出", "", "分", "CNY", "missing amount"],
        ["", "SYN-A", "SYN-B", "支出", "123", "分", "CNY", "missing date"],
    ]
    if format == "csv":
        content = table_bytes(headers, rows)
        source_rows = [2, 3, 4, 5, 6]
    else:
        order = [5, 4, 2, 0, 3, 1, 7, 6]
        content = workbook_bytes([
            ("Synthetic valid", [headers, *rows[:2]]),
            ("Synthetic gaps", [[headers[index] for index in order],
                                *[[row[index] for index in order] for row in rows[2:]]]),
        ])
        source_rows = [2, 3, 2, 3, 4]
    view = preview(client, cid, content, f"synthetic.{format}")
    assert view["automatic"] is True
    assert view["table_summary"]["profile"] == table_import.PROFILE
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (2, 3)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 2
    expected = [
        {"payer_account": "SYN-A", "payee_account": "SYN-B", "amount": Decimal("1.23"), "currency": "CNY"},
        {"payer_account": "SYN-B", "payee_account": "SYN-A", "amount": Decimal("1.23"), "currency": "USD"},
    ]
    query = "select payer_account,payee_account,amount,currency from flows where case_id=? order by id"
    assert database.q_raw(query, [cid]) == expected
    source = view["import_id"]
    report = client.get(f"/api/v1/cases/{cid}/sources/{source}/report").json()
    assert [record["source_row"] for record in report["items"]] == source_rows
    assert [record["disposition"] for record in report["items"]] == ["imported", "imported", "unresolved", "unresolved", "unresolved"]
    for record, issue in zip(report["items"][2:], ("negative_amount", "missing_amount", "missing_time")):
        assert_unresolved(record, issue)
        assert record["flow_id"] is None
    assert client.get(f"/api/v1/cases/{cid}/sources/{source}/file").content == content
    before = client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]
    assert before["unresolved_records"] == 3
    assert before["financial_interpretation_complete"] is False
    repeated = complete(client, cid, preview(client, cid, content, f"renamed.{format}"))
    assert repeated["stage"] == "done"
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert database.q_raw(query, [cid]) == expected
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert database.q_raw(query, [restored]) == expected
    assert client.get(f"/api/v1/cases/{restored}/stats").json()["import_coverage"] == before
    restored_source = database.q_raw("""select distinct source_id from import_records
        where case_id=? and disposition='imported'""", [restored])[0]["source_id"]
    restored_response = client.get(f"/api/v1/cases/{restored}/sources/{restored_source}/report")
    assert restored_response.status_code == 200, restored_response.text
    restored_report = restored_response.json()
    assert [(item["source_row"], item["disposition"], item["reason_code"]) for item in restored_report["items"]] == [
        (item["source_row"], item["disposition"], item["reason_code"]) for item in report["items"]]
    assert restored_report["sha256"] == report["sha256"]
    assert restored_report["source_available"] is False
    assert client.get(f"/api/v1/cases/{restored}/sources/{restored_source}/file").status_code == 404
