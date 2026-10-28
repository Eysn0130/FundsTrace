"""Public XLS-only versioning and import lifecycle checks use synthetic data."""
from datetime import datetime, time, timedelta
from decimal import Decimal
import json

import pytest

from app import case_packages, table_import
from app.services import imports

from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_currency_region_lifecycle import receipt, repreview_unchanged, source_snapshot
from test_table_mapping_coverage import coverage
from test_table_multisheet_append import encoded
from test_table_write_integrity import persisted
from test_feedback_package import _archive, _members, _mutate
from test_table_xls_reader import formula_workbook, sheet_types_workbook
from xls_fixtures import Cell, Error, workbook


XLS_PROFILE = "funds-trace-table-v18"
FROZEN_PROFILE = "funds-trace-table-v17"
HEADERS = ["date", "payer_account", "payee_account", "amount", "currency"]


def money_facts(rows):
    return [(row["trade_time"], row["payer_account"], row["payee_account"], row["amount"], row["currency"])
            for row in rows]


def committed_xls(client, cid, data, filename="synthetic.xls"):
    view = preview(client, cid, data, filename)
    assert view["automatic"] and view["table_summary"]["profile"] == XLS_PROFILE
    view = repreview_unchanged(client, cid, view)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return view, job, source_records(client, cid, view["import_id"])


def test_xls_public_preview_automatically_pins_v18_and_commits_exact_facts(client, database, make_case):
    cid = make_case("Synthetic XLS public introduction")
    data = workbook([("Synthetic transactions", [
        HEADERS, ["2026-01-01 10:00:00", "00123456789012345678", "SYN-B", "1.23", "USD"],
    ])])
    view = preview(client, cid, data, "synthetic.xls")
    assert view["automatic"] is True and view["mode"] == "A"
    assert view["table_summary"]["profile"] == XLS_PROFILE
    assert (view["table_summary"]["accepted"], view["table_summary"]["unresolved"]) == (1, 0)
    assert view["table_summary"]["currency_totals"] == {"USD": "1.23"}
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (1, 0)
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 1, 1, 10), "00123456789012345678", "SYN-B", Decimal("1.23"), "USD"),
    ]
    record = source_records(client, cid, view["import_id"])[0]
    assert (record["source_row"], record["reason_code"], record["disposition"]) == (
        2, "transaction_valid", "imported")
    source = client.get(f"/api/v1/cases/{cid}/flows/{record['flow_id']}/sources").json()["items"][0]
    assert source["parser_version"] == XLS_PROFILE
    assert source["member_row"] == 2 and source["member_path"] == record["member_path"]


def test_csv_and_xlsx_default_stay_v17_when_appended_in_the_same_case(client, database, make_case):
    cid = make_case("Synthetic existing CSV XLSX contracts")
    frozen = None
    for index, suffix in enumerate((".csv", ".xlsx"), 1):
        data = encoded([("Synthetic existing", [
            HEADERS, [f"2026-02-0{index} 11:00:00", "SYN-A", f"SYN-{index}", "2.03", "CNY"],
        ])], suffix)
        view = preview(client, cid, data, "synthetic" + suffix)
        assert view["automatic"] and view["table_summary"]["profile"] == FROZEN_PROFILE
        job = complete(client, cid, view)
        assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (1, 0)
        if frozen is None:
            frozen = (view["import_id"], source_snapshot(database, cid, view["import_id"]), job["result"])
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 2, 1, 11), "SYN-A", "SYN-1", Decimal("2.03"), "CNY"),
        (datetime(2026, 2, 2, 11), "SYN-A", "SYN-2", Decimal("2.03"), "CNY"),
    ]
    source_id, snapshot, result = frozen
    assert source_snapshot(database, cid, source_id) == snapshot
    assert receipt(client, cid, source_id) == result


@pytest.mark.parametrize("datemode", [0, 1], ids=["1900", "1904"])
def test_xls_dates_full_datetime_and_split_clock_preserve_workbook_date_system(
        client, database, make_case, datemode):
    cid = make_case("Synthetic XLS dates")
    data = workbook([
        ("Full timestamps", [HEADERS,
            [Cell(datetime(2026, 2, 1, 9, 23, 45), "yyyy-mm-dd hh:mm:ss"), "SYN-A", "SYN-B", "1.23", "CNY"]]),
        ("Split clock", [["交易日期", "交易时分秒", *HEADERS[1:]],
            [Cell(datetime(2026, 2, 2), "yyyy-mm-dd"), Cell(time(12, 34, 56), "hh:mm:ss"),
             "SYN-A", "SYN-C", "2.03", "USD"]]),
    ], datemode=datemode)
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (2, 0)
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 2, 1, 9, 23, 45), "SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        (datetime(2026, 2, 2, 12, 34, 56), "SYN-A", "SYN-C", Decimal("2.03"), "USD"),
    ]
    assert [record["source_row"] for record in records] == [2, 2]
    assert len({record["member_path"] for record in records}) == 2


@pytest.mark.parametrize("number_format", ["hh:mm:ss", "mm", "mmmm"])
def test_xls_full_trade_time_retains_encoded_date_and_clock_when_display_hides_them(
        client, database, make_case, number_format):
    cid = make_case("Synthetic XLS datetime display semantics")
    moment = datetime(2026, 2, 1, 9, 15, 30)
    data = workbook([("Full encoded datetime", [HEADERS,
        [Cell(moment, number_format), "SYN-A", "SYN-B", "1.23", "USD"]])])
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 0)
    assert records[0]["reason_code"] == "transaction_valid"
    assert money_facts(facts(database, cid)) == [
        (moment, "SYN-A", "SYN-B", Decimal("1.23"), "USD")]


@pytest.mark.parametrize("sheet_type", ["chart", "macro", "dialog"])
def test_xls_closed_nontransaction_substreams_do_not_hide_normal_or_hidden_worksheets(
        client, database, make_case, sheet_type):
    cid = make_case("Synthetic XLS sheet dispatch")
    raw = workbook([
        ("Declared nontransaction", [HEADERS, ["2026-02-03", "SYN-NO", "SYN-NO2", "999.00", "CNY"]]),
        ("Hidden normal", [HEADERS, ["2026-02-04", "SYN-A", "SYN-B", "1.23", "CNY"]]),
        ("Visible normal", [HEADERS, ["2026-02-05", "SYN-C", "SYN-D", "2.03", "USD"]]),
    ])
    # This fixture changes BIFF substream declarations to exercise dispatch and
    # closure; it makes no claim that the synthetic chart/macro renders in Office.
    data = sheet_types_workbook(raw, types={0: sheet_type}, hidden=(1,))
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["record_total"]) == (2, 2)
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 2, 4), "SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        (datetime(2026, 2, 5), "SYN-C", "SYN-D", Decimal("2.03"), "USD"),
    ]
    assert len({record["member_path"] for record in records}) == 2
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data


def test_xls_closed_nested_chart_does_not_end_its_parent_worksheet(client, database, make_case):
    cid = make_case("Synthetic XLS nested chart depth")
    raw = workbook([("Synthetic parent", [HEADERS,
        ["2026-02-06", "SYN-A", "SYN-B", "1.23", "CNY"]])])
    _, job, _ = committed_xls(client, cid, sheet_types_workbook(raw, nested_chart="closed"))
    assert job["result"]["ok"] == 1
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 2, 6), "SYN-A", "SYN-B", Decimal("1.23"), "CNY")]


@pytest.mark.parametrize("kind", ["no-supported-worksheet", "unclosed-nested-chart"])
def test_xls_unsupported_or_unclosed_sheet_structure_rejects_entire_file_before_writes(
        client, database, make_case, kind):
    cid = make_case("Synthetic XLS rejected sheet structure")
    raw = workbook([("Synthetic declared", [HEADERS,
        ["2026-02-07", "SYN-A", "SYN-B", "1.23", "CNY"]])])
    data = (sheet_types_workbook(raw, types={0: "chart"}) if kind == "no-supported-worksheet"
            else sheet_types_workbook(raw, nested_chart="unclosed"))
    before = persisted(database)
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.xls", data)})
    assert response.status_code == 400, response.text
    assert persisted(database) == before
    assert database.q_raw("select * from import_sessions where case_id=?", [cid]) == []
    assert not list((database.DATA_DIR / "uploads").glob("*"))


@pytest.mark.parametrize("opcode", [0x0206, 0x0406], ids=["old-0206", "old-0406"])
def test_biff8_with_old_formula_opcode_is_rejected_before_any_public_import_writes(
        client, database, make_case, opcode):
    import xlwt

    cid = make_case("Synthetic BIFF8 unsupported old formula record")
    raw = workbook([
        ("Independent valid", [HEADERS, ["2026-02-08", "SYN-A", "SYN-B", "1.23", "CNY"]]),
        ("Unsupported formula opcode", [HEADERS,
            ["2026-02-09", "SYN-C", "SYN-D", xlwt.Formula("1+2"), "USD"]]),
    ])
    data = formula_workbook(raw, cached=3.0, opcode=opcode)
    before = persisted(database)
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.xls", data)})
    assert response.status_code == 400, response.text
    assert persisted(database) == before
    assert database.q_raw("select * from import_sessions where case_id=?", [cid]) == []
    assert not list((database.DATA_DIR / "uploads").glob("*"))


def test_xls_1900_leap_error_overflow_and_time_only_cannot_become_other_dates(client, database, make_case):
    cid = make_case("Synthetic XLS invalid dates")
    values = [Cell(59, "yyyy-mm-dd"), Cell(60, "yyyy-mm-dd"), Cell(61, "yyyy-mm-dd"),
              Cell(4_000_000, "yyyy-mm-dd"), Cell(time(12), "hh:mm:ss")]
    data = workbook([("Date boundaries", [HEADERS, *[
        [value, "SYN-A", f"SYN-{index}", "1.00", "CNY"] for index, value in enumerate(values)]])])
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (2, 3)
    assert money_facts(facts(database, cid)) == [
        (datetime(1900, 2, 28), "SYN-A", "SYN-0", Decimal("1.00"), "CNY"),
        (datetime(1900, 3, 1), "SYN-A", "SYN-2", Decimal("1.00"), "CNY"),
    ]
    assert [record["disposition"] for record in records] == [
        "imported", "unresolved", "imported", "unresolved", "unresolved"]
    assert all(records[index]["flow_id"] is None for index in (1, 3, 4))
    assert [records[index]["reason_code"] for index in (1, 3, 4)] == [
        "invalid_cell_type", "invalid_cell_type", "invalid_time"]


@pytest.mark.parametrize("number_format", ["yyyy-mm-dd", "yyyy-mm-dd hh:mm:ss"])
@pytest.mark.parametrize("serial", [0, 0.5, 60, 60.5])
def test_xls_1900_zero_day_and_nonexistent_leap_day_are_not_synthetic_dates(
        client, database, make_case, number_format, serial):
    cid = make_case("Synthetic XLS excluded calendar serial")
    data = workbook([("Calendar boundaries", [HEADERS,
        [Cell(serial, number_format), "SYN-A", "SYN-B", "1.23", "CNY"],
        ["2026-02-01", "SYN-C", "SYN-D", "2.03", "USD"]])])
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 1)
    assert records[0]["reason_code"] == "invalid_cell_type" and records[0]["flow_id"] is None
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 2, 1), "SYN-C", "SYN-D", Decimal("2.03"), "USD")]


def test_xls_1904_zero_day_is_real_but_split_time_never_discards_a_day_or_elapsed_hours(
        client, database, make_case):
    cid = make_case("Synthetic XLS 1904 split clock boundaries")
    headers = ["交易日期", "交易时分秒", *HEADERS[1:]]
    clock_values = [Cell(0.5, "hh:mm:ss"), Cell(1.5, "hh:mm:ss"),
                    Cell(-0.5, "hh:mm:ss"), Cell(0.5, "[h]:mm:ss")]
    data = workbook([("Split times", [headers, *[
        [Cell(0, "yyyy-mm-dd"), clock, "SYN-A", f"SYN-{index}", "1.23", "CNY"]
        for index, clock in enumerate(clock_values)]])], datemode=1)
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 3)
    assert money_facts(facts(database, cid)) == [
        (datetime(1904, 1, 1, 12), "SYN-A", "SYN-0", Decimal("1.23"), "CNY")]
    # Serial 1.5 encodes 1904-01-02 noon even when the display shows only
    # the clock. It is a valid datetime, but cannot be shortened to the
    # requested standalone time_part; negative/elapsed values are invalid types.
    assert [record["reason_code"] for record in records[1:]] == [
        "invalid_time", "invalid_cell_type", "invalid_cell_type"]
    assert all(record["flow_id"] is None for record in records[1:])


def test_xls_identifiers_preserve_long_text_cards_leading_zero_formats_and_safe_integers(
        client, database, make_case):
    cid = make_case("Synthetic XLS exact identifiers")
    headers = ["date", "本方账号", "本方卡号", "对方账号", "direction", "amount"]
    data = workbook([("Exact identities", [headers,
        ["2026-03-01", "00012345678901234567", "00098765432109876543", "SYN-B", "out", "1.23"],
        ["2026-03-02", Cell(12345, "00000000"), "", "SYN-C", "out", "2.03"],
        ["2026-03-03", 12345, "", "SYN-D", "out", "3.45"],
        ["2026-03-04", "=SYN-ACCOUNT", "", "date", "out", "4.56"],
        ["2026-03-05", "", "00011112222333344445", "SYN-E", "out", "5.67"],
        ["2026-03-06", 1234567890123456, "", "SYN-NO", "out", "6.78"],
        ["2026-03-07", Cell(12345, "0.00E+00"), "", "SYN-NO", "out", "7.89"],
    ])])
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (5, 2)
    assert [row["payer_account"] for row in facts(database, cid)] == [
        "00012345678901234567", "00012345", "12345", "=SYN-ACCOUNT", "00011112222333344445"]
    assert [record["reason_code"] for record in records[-2:]] == [
        "unsafe_account_number", "unsupported_identity_format"]
    assert all(record["flow_id"] is None for record in records[-2:])


@pytest.mark.parametrize("field", ["date", "account", "counterparty account", "direction", "amount", "balance", "currency"])
@pytest.mark.parametrize("bad", [True, Error()], ids=["boolean", "excel-error"])
def test_xls_critical_boolean_or_error_is_journaled_without_poisoning_valid_neighbors(
        client, database, make_case, field, bad):
    cid = make_case("Synthetic XLS typed critical field")
    headers = ["date", "account", "counterparty account", "direction", "amount", "balance", "currency", "unused"]
    valid = ["2026-04-01", "SYN-A", "SYN-B", "out", "1.23", "100.00", "CNY", Error()]
    invalid = ["2026-04-02", "SYN-A", "SYN-C", "out", "2.03", "97.97", "CNY", True]
    invalid[headers.index(field)] = bad
    view, job, records = committed_xls(client, cid, workbook([("Typed critical", [headers, valid, invalid])]))
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 1)
    assert [record["reason_code"] for record in records] == ["transaction_valid", "invalid_cell_type"]
    assert records[1]["flow_id"] is None and records[1]["details"]["financial_effect"] == "undetermined"
    assert "invalid_cell_type" in records[1]["details"]["validation_issues"].split(",")
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 4, 1), "SYN-A", "SYN-B", Decimal("1.23"), "CNY")]
    assert coverage(client, cid)["financial_interpretation_complete"] is False


@pytest.mark.parametrize("range_kind", [None, "shared", "array"], ids=["ordinary", "shared", "array"])
@pytest.mark.parametrize("cache_kind,cached", [
    ("number", 3.0), ("string", "3.00"), ("boolean", True), ("error", 7), ("empty", None),
], ids=["number", "string", "boolean", "error", "empty"])
def test_xls_cached_critical_formulas_are_unresolved_for_every_cache_and_range_kind(
        client, database, make_case, range_kind, cache_kind, cached):
    import xlwt

    cid = make_case("Synthetic XLS cached formula lifecycle")
    raw = workbook([
        ("Critical formulas", [HEADERS,
            ["2026-04-03", "SYN-A", "SYN-B", xlwt.Formula("1+2"), "CNY"],
            ["2026-04-04", "SYN-A", "SYN-C", xlwt.Formula("1+2"), "CNY"]]),
        ("Independent valid", [HEADERS, ["2026-04-05", "SYN-P", "SYN-Q", "2.03", "USD"]]),
    ])
    data = formula_workbook(raw, cache_kind=cache_kind, cached=cached, range_kind=range_kind)
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 2)
    assert [record["reason_code"] for record in records] == ["formula_value", "formula_value", "transaction_valid"]
    assert all(record["flow_id"] is None and record["details"]["financial_effect"] == "undetermined"
               for record in records[:2])
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 4, 5), "SYN-P", "SYN-Q", Decimal("2.03"), "USD")]
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data


@pytest.mark.parametrize("range_kind", [None, "shared", "array"], ids=["ordinary", "shared", "array"])
def test_xls_unmapped_auxiliary_formulas_and_equals_text_do_not_reject_valid_transactions(
        client, database, make_case, range_kind):
    import xlwt

    cid = make_case("Synthetic XLS auxiliary formula lifecycle")
    raw = workbook([("Auxiliary formulas", [[*HEADERS, "unused formula"],
        ["2026-04-06", "SYN-A", "SYN-B", "1.23", "CNY", xlwt.Formula("1+2")],
        ["2026-04-07", "SYN-A", "SYN-C", "2.03", "USD", xlwt.Formula("1+2")],
        ["2026-04-08", "=SYN-LITERAL", "SYN-D", "3.45", "CNY", "=ordinary text"],
    ])])
    data = formula_workbook(raw, cached=3.0, range_kind=range_kind)
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (3, 0)
    assert [record["reason_code"] for record in records] == ["transaction_valid"] * 3
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 4, 6), "SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        (datetime(2026, 4, 7), "SYN-A", "SYN-C", Decimal("2.03"), "USD"),
        (datetime(2026, 4, 8), "=SYN-LITERAL", "SYN-D", Decimal("3.45"), "CNY"),
    ]


def test_xls_sparse_shared_reference_preserves_literal_fields_and_noncorner_formula_anchor(
        client, database, make_case):
    import xlwt

    cid = make_case("Synthetic XLS sparse shared reference")
    raw = workbook([("Sparse shared formulas", [HEADERS,
        ["2026-04-11", "SYN-A", "SYN-B", xlwt.Formula("1+2"), "CNY"],
        ["2026-04-12", "SYN-A", "SYN-C", "1.23", "CNY"],
        ["2026-04-13", "SYN-A", "SYN-D", xlwt.Formula("1+2"), "CNY"],
    ])])
    # The shared ref includes literal dates, identities, currency and the
    # middle row's amount. Only real Formula/ptgExp cells are formulas. Its
    # first actual formula (row 1, col 3) is not the ref's top-left (row 1, col 0).
    data = formula_workbook(raw, cached=3.0, range_kind="shared", shared_range=(1, 3, 0, 4))
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 2)
    assert [record["source_row"] for record in records] == [2, 3, 4]
    assert [record["reason_code"] for record in records] == ["formula_value", "transaction_valid", "formula_value"]
    assert records[0]["flow_id"] is None and records[2]["flow_id"] is None
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 4, 12), "SYN-A", "SYN-C", Decimal("1.23"), "CNY")]


@pytest.mark.parametrize("header", ["date", "amount"])
def test_xls_formula_cache_which_looks_like_a_header_cannot_create_a_schema(
        client, database, make_case, header):
    import xlwt

    cid = make_case("Synthetic XLS formula alias")
    headers = list(HEADERS)
    headers[headers.index(header)] = xlwt.Formula("1+2")
    raw = workbook([("Synthetic false header", [headers,
        ["2026-04-09", "SYN-A", "SYN-B", "1.23", "CNY"]])])
    data = formula_workbook(raw, cache_kind="string", cached=header)
    view, job, records = committed_xls(client, cid, data)
    assert view["mapping"] == {} and view["table_summary"]["documents"][0]["header_rows"] == []
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (0, 2)
    assert [record["reason_code"] for record in records] == ["unknown_table", "unknown_table"]
    assert facts(database, cid) == []


def test_xls_only_empty_cached_critical_formula_keeps_its_source_record(client, database, make_case):
    import xlwt

    cid = make_case("Synthetic XLS empty cached formula record")
    raw = workbook([("Synthetic empty cache", [HEADERS,
        [None, None, None, xlwt.Formula("1+2"), None]])])
    view, job, records = committed_xls(client, cid, formula_workbook(raw, cache_kind="empty"))
    assert (job["result"]["ok"], view["table_summary"]["record_total"], view["table_summary"]["unresolved"]) == (0, 1, 1)
    assert len(records) == 1
    assert (records[0]["source_row"], records[0]["disposition"], records[0]["reason_code"], records[0]["flow_id"]) == (
        2, "unresolved", "formula_value", None)
    assert facts(database, cid) == []


@pytest.mark.parametrize("profile", ["funds-trace-table-v5", FROZEN_PROFILE])
def test_historical_xlsx_ready_mapping_keeps_auxiliary_formula_header_label(
        client, database, make_case, monkeypatch, profile):
    cid = make_case("Synthetic XLSX historical formula column label")
    data = encoded([("Historical XLSX", [[*HEADERS, "=1+2"],
        ["2026-04-10", "SYN-A", "SYN-B", "1.23", "CNY", "Synthetic preserved summary"]])], ".xlsx")
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", profile)
        view = preview(client, cid, data, "historical.xlsx")
    assert view["table_summary"]["profile"] == profile
    assert view["columns"][-1] == "=1+2", "An XLS-only change must retain historical XLSX column labels"
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": {**view["mapping"], "summary": "=1+2"}})
    assert response.status_code == 200, response.text
    ready = repreview_unchanged(client, cid, response.json())
    assert ready["table_summary"]["profile"] == profile
    job = complete(client, cid, ready)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    row = facts(database, cid)[0]
    assert (row["payer_account"], row["payee_account"], row["amount"], row["summary"]) == (
        "SYN-A", "SYN-B", Decimal("1.23"), "Synthetic preserved summary")


def test_xls_all_sheets_reordered_regions_signed_units_and_last_conflict_are_fully_validated(
        client, database, make_case):
    cid = make_case("Synthetic XLS all sheets and rows")
    rows = [["date", "account", "counterparty account", "direction", "signed amount/分", "交易金额(元)", "币种代码"]]
    rows.extend([[(datetime(2026, 5, 1) + timedelta(minutes=index * 3)).isoformat(" "),
                  "SYN-A", f"SYN-C-{index}", "out", "-123", "-1.23", "USD"] for index in range(125)])
    rows.append(["2026-05-02", "SYN-A", "SYN-NO", "out", "-123", "-9.99", "USD"])
    region = [["date", "payer_account", "payee_account", "amount", "currency"],
              ["2026-05-03", "SYN-P", "SYN-Q", "2.03", "CNY"],
              ["交易金额(分)", "收款账号", "交易时间", "付款账号", "币种代码"],
              ["345", "SYN-S", "2026-05-04", "SYN-R", "USD"]]
    data = workbook([("Signed cents", rows), ("Changed region", region),
                     ("Unknown", [["unknown_a", "unknown_b"], ["SYN-X", "SYN-Y"]])])
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (127, 3)
    assert view["table_summary"]["currency_totals"] == {"CNY": "2.03", "USD": "157.20"}
    assert [(row["payer_account"], row["payee_account"], row["amount"])
            for row in facts(database, cid)[-2:]] == [
        ("SYN-P", "SYN-Q", Decimal("2.03")), ("SYN-R", "SYN-S", Decimal("3.45"))]
    assert records[125]["source_row"] == 127 and records[125]["reason_code"] == "conflicting_amount"
    assert records[125]["flow_id"] is None
    assert [record["reason_code"] for record in records[-2:]] == ["unknown_table", "unknown_table"]
    assert view["table_summary"]["documents"][1]["header_rows"] == [1, 3]


@pytest.mark.parametrize("headers,invalid,reason", [
    (["date", "account", "counterparty account", "signed amount", "direction"],
     ["2026-05-06", "SYN-A", "SYN-NO", "-1.23", "in"], "conflicting_direction"),
    (["date", "account", "counterparty account", "amount", "direction"],
     ["2026-05-06", "SYN-A", "SYN-NO", "-1.23", "out"], "negative_amount"),
    (["date", "account", "counterparty account", "amount", "direction"],
     ["2026-05-06", "SYN-A", "SYN-NO", "1.234", "out"], "amount_precision"),
    (["date", "account", "counterparty account", "amount", "direction"],
     ["2026-05-06", "SYN-A", "SYN-NO", "10000000000000000", "out"], "amount_overflow"),
    (["date", "payer_account", "payee_account", "amount", "币种代码", "currency"],
     ["2026-05-06", "SYN-A", "SYN-NO", "1.23", "USD", "CNY"], "invalid_currency"),
    (["date", "交易时间", "payer_account", "payee_account", "amount"],
     ["2026-05-06", "2026-05-07", "SYN-A", "SYN-NO", "1.23"], "conflicting_time"),
], ids=["signed-direction", "unsigned-negative", "precision", "overflow", "currency", "time"])
def test_xls_inherits_exact_financial_rules_without_weakening_conflicts(
        client, database, make_case, headers, invalid, reason):
    cid = make_case("Synthetic XLS inherited financial conflict")
    data = workbook([("Explicit conflict", [headers, invalid]),
                     ("Independent valid", [HEADERS, ["2026-05-08", "SYN-P", "SYN-Q", "2.03", "USD"]])])
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (1, 1)
    assert records[0]["reason_code"] == reason and records[0]["flow_id"] is None
    assert money_facts(facts(database, cid)) == [
        (datetime(2026, 5, 8), "SYN-P", "SYN-Q", Decimal("2.03"), "USD")]


def test_mixed_csv_xlsx_xls_append_and_replay_preserve_first_facts_sources_and_receipts(
        client, database, make_case):
    cid = make_case("Synthetic mixed format append")
    matrices = [
        [HEADERS, ["2026-06-01", "SYN-A", "SYN-B", "1.23", "CNY"]],
        [HEADERS, ["2026-06-01", "SYN-A", "SYN-B", "1.23", "CNY"],
         ["2026-06-02", "SYN-C", "SYN-A", "2.03", "USD"]],
        [HEADERS, ["2026-06-02", "SYN-C", "SYN-A", "2.03", "USD"],
         ["2026-06-03", "SYN-A", "SYN-D", "3.45", "CNY"]],
    ]
    sources = []
    for matrix, suffix in zip(matrices, (".csv", ".xlsx", ".xls")):
        data = workbook([("Synthetic append", matrix)]) if suffix == ".xls" else encoded([("Synthetic append", matrix)], suffix)
        view = preview(client, cid, data, "synthetic" + suffix)
        assert view["table_summary"]["profile"] == (XLS_PROFILE if suffix == ".xls" else FROZEN_PROFILE)
        job = complete(client, cid, repreview_unchanged(client, cid, view))
        assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (
            1, 0 if suffix == ".csv" else 1)
        sources.append((data, suffix, view["import_id"], source_snapshot(database, cid, view["import_id"]), job["result"]))
    before = facts(database, cid)
    assert money_facts(before) == [
        (datetime(2026, 6, 1), "SYN-A", "SYN-B", Decimal("1.23"), "CNY"),
        (datetime(2026, 6, 2), "SYN-C", "SYN-A", Decimal("2.03"), "USD"),
        (datetime(2026, 6, 3), "SYN-A", "SYN-D", Decimal("3.45"), "CNY"),
    ]
    for data, suffix, source_id, snapshot, result in sources:
        repeated = complete(client, cid, preview(client, cid, data, "repeated" + suffix))
        assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (
            0, 1 if suffix == ".csv" else 2)
        assert source_snapshot(database, cid, source_id) == snapshot
        assert receipt(client, cid, source_id) == result
    assert facts(database, cid) == before
    other = make_case("Synthetic XLS isolated append")
    data = sources[-1][0]
    isolated = complete(client, other, preview(client, other, data, "isolated.xls"))
    assert (isolated["result"]["ok"], isolated["result"]["rows_dup"]) == (2, 0)
    assert client.get(f"/api/v1/cases/{other}/sources/{sources[-1][2]}/report").status_code == 404
    assert facts(database, cid) == before


@pytest.mark.parametrize("profile", [f"funds-trace-table-v{version}" for version in range(5, 18)])
def test_every_historical_table_profile_still_rejects_xls(profile):
    data = workbook([("Synthetic old profile", [HEADERS, ["2026-01-01", "SYN-A", "SYN-B", "1.23", "CNY"]])])
    with pytest.raises(table_import.TableImportError, match="XLS|xls"):
        table_import.parse(data, suffix=".xls", profile=profile)


@pytest.mark.parametrize("suffix", [".csv", ".tsv", ".txt", ".xlsx", ".zip"])
def test_xls_profile_never_takes_over_other_formats(suffix):
    data = encoded([("Synthetic unsupported version", [HEADERS])], ".xlsx" if suffix == ".xlsx" else ".csv")
    with pytest.raises(table_import.TableImportError):
        table_import.parse(data, suffix=suffix, profile=XLS_PROFILE)


def test_ready_historical_profiles_keep_their_facts_sources_and_receipts_after_xls_append(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic historical profiles unchanged")
    old_data = encoded([("Synthetic historical", [HEADERS,
        ["2026-07-01", "SYN-A", "SYN-B", "1.23", "CNY"]])], ".csv")
    historical = []
    for version in range(5, 18):
        profile = f"funds-trace-table-v{version}"
        with monkeypatch.context() as old_default:
            old_default.setattr(imports, "TABLE_PROFILE", profile)
            view = preview(client, cid, old_data, f"synthetic-v{version}.csv")
        assert view["table_summary"]["profile"] == profile
        job = complete(client, cid, repreview_unchanged(client, cid, view))
        assert job["stage"] == "done" and job["result"]["ok"] == (1 if version == 5 else 0)
        historical.append((view["import_id"], source_snapshot(database, cid, view["import_id"]), job["result"]))
    old_facts = database.q_raw("select * from flows where case_id=? order by id", [cid])
    data = workbook([("Synthetic current", [HEADERS,
        ["2026-07-02", "SYN-C", "SYN-D", "2.03", "USD"]])])
    _, job, _ = committed_xls(client, cid, data)
    assert job["result"]["ok"] == 1
    after = database.q_raw("select * from flows where case_id=? order by id", [cid])
    assert after[:1] == old_facts and len(after) == 2
    for source_id, snapshot, result in historical:
        assert source_snapshot(database, cid, source_id) == snapshot
        assert receipt(client, cid, source_id) == result


@pytest.mark.parametrize("malformation", ["html", "csv-renamed", "truncated"])
def test_invalid_xls_is_rejected_atomically_without_orphans_or_changed_existing_case(
        client, database, make_case, malformation):
    cid = make_case("Synthetic XLS invalid upload")
    old = preview(client, cid, b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.23\n")
    assert complete(client, cid, old)["stage"] == "done"
    before = persisted(database)
    sessions = database.q_raw("select * from import_sessions order by import_id")
    files = set((database.DATA_DIR / "uploads").iterdir())
    valid = workbook([("Synthetic truncated", [HEADERS, ["2026-01-02", "SYN-C", "SYN-D", "2.03", "CNY"]])])
    raw = {"html": b"<html><table><tr><td>date</td></tr></table></html>",
           "csv-renamed": b"date,payer_account,payee_account,amount\n2026-01-02,SYN-C,SYN-D,2.03\n",
           "truncated": valid[:256]}[malformation]
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("bad.xls", raw)})
    assert response.status_code == 400
    assert persisted(database) == before
    assert database.q_raw("select * from import_sessions order by import_id") == sessions
    assert set((database.DATA_DIR / "uploads").iterdir()) == files


def test_xls_journal_failure_rolls_back_every_new_fact_and_is_retryable(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic XLS transactional journal")
    data = workbook([("Synthetic rollback", [HEADERS, ["2026-01-01", "SYN-A", "SYN-B", "1.23", "CNY"]])])
    view = preview(client, cid, data, "synthetic.xls")
    before = persisted(database)
    original = database.insert_dataframe

    def fail_after_records(table, frame, columns):
        result = original(table, frame, columns)
        if table == "import_records":
            raise RuntimeError("Synthetic XLS late rollback")
        return result

    with monkeypatch.context() as failure:
        failure.setattr(database, "insert_dataframe", fail_after_records)
        job = complete(client, cid, view)
    assert job["stage"] == "error" and job["result"] is None
    assert persisted(database) == before
    assert complete(client, cid, view)["result"]["ok"] == 1


def test_xls_v18_format4_public_export_restores_to_clean_database_and_replay_adds_zero(
        client, database, make_case, monkeypatch, tmp_path):
    cid = make_case("Synthetic XLS clean database restore")
    data = workbook([("Synthetic portable", [HEADERS,
        ["2026-08-01", "00012345678901234567", "SYN-B", "1.23", "USD"],
        ["2026-08-02", "SYN-C", "SYN-D", "2.03", "CNY"],
        [True, "SYN-C", "SYN-NO", "3.45", "CNY"]])])
    view, job, records = committed_xls(client, cid, data)
    assert (job["result"]["ok"], view["table_summary"]["unresolved"]) == (2, 1)
    expected, original_coverage = facts(database, cid), coverage(client, cid)
    response = client.get(f"/api/cases/{cid}/export")
    assert response.status_code == 200
    package = response.content
    members = _members(package)
    assert json.loads(members["meta.json"])["format"] == 6
    assert json.loads(members["meta.json"])["source_files"] == "not_included"
    assert not any(name.endswith(".xls") for name in members)
    with database._lock:
        database._con.close()
        database._con = None
    with monkeypatch.context() as clean:
        directory = tmp_path / "restored-data"
        directory.mkdir()
        clean.setattr(database, "DATA_DIR", directory)
        clean.setattr(database, "DB_PATH", directory / "fund.duckdb")
        try:
            assert database.q_raw("select * from flows") == []
            response = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package)})
            assert response.status_code == 200, response.text
            restored = response.json()["case_id"]
            assert facts(database, restored) == expected
            assert coverage(client, restored) == original_coverage
            assert database.q_raw("select * from import_sessions") == []
            source = database.q_raw("select source_id,parser_version from import_sources where case_id=?", [restored])[0]
            assert source["parser_version"] == XLS_PROFILE
            restored_records = source_records(client, restored, source["source_id"])
            without_ids = lambda items: [{key: value for key, value in record.items() if key != "flow_id"} for record in items]
            assert without_ids(restored_records) == without_ids(records)
            repeated = complete(client, restored, preview(client, restored, data, "repeated.xls"))
            assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
            assert facts(database, restored) == expected
            assert coverage(client, restored) == original_coverage
        finally:
            with database._lock:
                if database._con is not None:
                    database._con.close()
                    database._con = None


@pytest.mark.parametrize("mutation", ["csv-filename", "csv-encoding"])
def test_xls_format4_rejects_mislabelled_v18_sources(
        client, database, make_case, tmp_path, mutation):
    cid = make_case("Synthetic XLS package closed metadata")
    data = workbook([("Synthetic closed metadata", [HEADERS,
        ["2026-09-01", "SYN-A", "SYN-B", "1.23", "CNY"],
        [True, "SYN-C", "SYN-D", "2.03", "CNY"]])])
    _, _, records = committed_xls(client, cid, data)
    assert records[1]["reason_code"] == "invalid_cell_type"
    members = _members(case_packages.export_package(cid)[0])
    if mutation == "csv-filename":
        _mutate(members, "import_sources", "update entries set filename='synthetic.csv',sheet=''", tmp_path)
    else:
        _mutate(members, "import_members", "update entries set encoding='utf-8-sig'", tmp_path)
    before = persisted(database)
    cases = database.q_raw("select * from cases order by id")
    response = client.post("/api/cases/import", files={"file": ("tampered.fundcase", _archive(members))})
    assert response.status_code == 400, response.text
    assert persisted(database) == before
    assert database.q_raw("select * from cases order by id") == cases


@pytest.mark.parametrize("profile", [f"funds-trace-table-v{version}" for version in range(5, 18)])
def test_invalid_cell_type_reason_does_not_expand_acceptance_of_any_historical_package(
        client, database, make_case, tmp_path, monkeypatch, profile):
    cid = make_case("Synthetic historical closed reason set")
    data = (b"date,payer_account,payee_account,amount\n"
            b"2026-09-01,SYN-A,SYN-B,1.23\n,SYN-C,SYN-D,2.03\n")
    with monkeypatch.context() as historical:
        historical.setattr(imports, "TABLE_PROFILE", profile)
        view = preview(client, cid, data)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    assert source_records(client, cid, view["import_id"])[1]["disposition"] == "unresolved"
    # The original package is valid under its own historical metadata. Only the
    # new XLS reason is injected; no unrelated source-contract downgrade masks it.
    package = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == facts(database, cid)
    members = _members(package)
    _mutate(members, "import_records", """update entries set reason_code='invalid_cell_type',
        details_json=json_merge_patch(details_json,'{"validation_issues":"invalid_cell_type"}')
        where disposition='unresolved'""", tmp_path)
    before = persisted(database)
    cases = database.q_raw("select * from cases order by id")
    response = client.post("/api/cases/import", files={"file": ("tampered.fundcase", _archive(members))})
    assert response.status_code == 400, response.text
    assert persisted(database) == before
    assert database.q_raw("select * from cases order by id") == cases


@pytest.mark.parametrize("format_version", [1, 2, 3, 4])
def test_new_xls_support_keeps_each_existing_package_format_readable(
        client, database, make_case, tmp_path, format_version):
    if format_version in (1, 2):
        from test_import_provenance import commit_source, csv_source, read_rows

        cid = make_case("Synthetic old lineage package")
        commit_source(cid, read_rows(csv_source(tmp_path)))
    elif format_version == 3:
        from test_feedback_import_api import archive, transaction

        cid = make_case("Synthetic existing feedback package")
        data = archive([transaction()])
        assert complete(client, cid, preview(client, cid, data, "synthetic.zip"))["stage"] == "done"
    else:
        cid = make_case("Synthetic existing v17 table package")
        assert complete(client, cid, preview(client, cid,
            b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.23\n"))["stage"] == "done"
    expected = facts(database, cid)
    members = _members(case_packages.export_package(cid)[0])
    if format_version in (3, 4):
        meta = json.loads(members["meta.json"])
        assert meta["format"] == 6
        members.pop("import_source_operations.parquet")
        meta["format"] = format_version
        members["meta.json"] = json.dumps(meta).encode()
    if format_version == 1:
        meta = json.loads(members["meta.json"])
        assert meta["format"] == 2
        meta["format"] = 1
        meta.pop("source_files")
        members["meta.json"] = json.dumps(meta).encode()
        for table in case_packages.PROVENANCE_PKG_COLS:
            members.pop(f"{table}.parquet")
    assert json.loads(members["meta.json"])["format"] == format_version
    response = client.post("/api/cases/import", files={"file": ("historical.fundcase", _archive(members))})
    assert response.status_code == 200, response.text
    assert facts(database, response.json()["case_id"]) == expected
