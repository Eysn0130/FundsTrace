"""Synthetic region boundaries keep their own columns and frozen history."""
import csv
import io
import json
from decimal import Decimal
import zipfile

from openpyxl import Workbook
import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage


HEADERS = ["date", "payer_account", "payee_account", "amount", "unused"]
FIRST = ["2026-01-01", "SYN-A", "SYN-B", "1.23", "original"]
LATER_HEADERS = ["date", "payee_account", "payer_account", "123", "amount"]
SECOND = ["2026-01-02", "SYN-C", "SYN-D", "9.87", "2.34"]
NEW_PROFILE = "funds-trace-table-v16"
OLD_PROFILE = "funds-trace-table-v14"


def payload(rows, suffix):
    output = io.BytesIO()
    if suffix == ".xlsx":
        workbook = Workbook()
        workbook.active.title = "Synthetic regions"
        for row in rows:
            workbook.active.append(row)
        workbook.save(output)
        workbook.close()
        return output.getvalue()
    text = io.StringIO(newline="")
    csv.writer(text).writerows(rows)
    return text.getvalue().encode()


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("old_field", ["amount", "income", "expense", "balance"])
def test_numeric_column_in_later_header_rebinds_party_and_money_columns(suffix, old_field):
    headers = [*HEADERS[:3], old_field, "amount" if old_field == "balance" else "unused"]
    first = [*FIRST[:3], "100.00", "1.23"] if old_field == "balance" else FIRST
    plan = table_import.parse(payload([headers, first, LATER_HEADERS, SECOND], suffix), suffix=suffix)
    assert plan["profile"] == NEW_PROFILE
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in plan["rows"]] == [
        ("SYN-A", "SYN-B", Decimal("1.23")),
        ("SYN-D", "SYN-C", Decimal("2.34")),
    ]
    assert [record["source_row"] for record in plan["records"]] == [2, 3, 4]
    assert [record["reason_code"] for record in plan["records"]] == [
        "transaction_valid", "repeated_header", "transaction_valid"]
    assert plan["documents"][0]["header_rows"] == [1, 3]
    assert {entry["header_row"] for entry in plan["mapping_evidence"]} == {1, 3}
    assert {(entry["field"], entry["column_index"]) for entry in plan["mapping_evidence"]
            if entry["header_row"] == 3} == {
        ("trade_time", 1), ("payee_account", 2), ("payer_account", 3), ("amount", 5)}
    assert plan["rows"][-1]["balance"] is None


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_rebound_region_validates_all_amount_candidates_through_the_last_row(suffix):
    rows = [[*HEADERS, "unused2"], [*FIRST, "original"], [*LATER_HEADERS, "发生金额"]]
    rows.extend([[f"2026-01-02 10:{index // 60:02d}:{index % 60:02d}",
                  "SYN-C", "SYN-D", "9.87", "2.34", "2.34"] for index in range(127)])
    rows.append(["2026-01-03", "SYN-LAST-C", "SYN-LAST-D", "55.55", "3.45", "6.78"])
    plan = table_import.parse(payload(rows, suffix), suffix=suffix)
    assert (plan["stats"]["total"], plan["stats"]["accepted"], plan["stats"]["unresolved"],
            plan["stats"]["non_transaction"]) == (130, 128, 1, 1)
    assert plan["stats"]["currency_totals"] == {"CNY": "298.41"}
    assert plan["records"][-1]["source_row"] == 131
    assert plan["records"][-1]["reason_code"] == "conflicting_amount"
    assert plan["records"][-1]["details"]["financial_effect"] == "undetermined"
    assert "conflicting_amount" in plan["records"][-1]["details"]["validation_issues"].split(",")
    assert [(entry["column_index"], entry["candidate_count"]) for entry in plan["mapping_evidence"]
            if entry["header_row"] == 3 and entry["field"] == "amount"] == [(5, 2), (6, 2)]
    assert all(row["payer_account"] == "SYN-D" and row["payee_account"] == "SYN-C"
               and row["amount"] == Decimal("2.34") for row in plan["rows"][1:])


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_automatic_region_import_persists_exact_facts_source_positions_and_format4(
        client, database, make_case, suffix):
    cid = make_case("Synthetic numeric region lifecycle")
    data = payload([[*HEADERS, "unused2"], [*FIRST, "original"],
                    [*LATER_HEADERS, "发生金额"], [*SECOND, "2.34"],
                    ["2026-01-03", "SYN-E", "SYN-F", "55.55", "3.45", "6.78"]], suffix)
    filename = "synthetic-regions" + suffix
    view = preview(client, cid, data, filename)
    summary = view["table_summary"]
    assert view["automatic"] and summary["profile"] == NEW_PROFILE
    assert (summary["record_total"], summary["accepted"], summary["unresolved"],
            summary["non_transaction"]) == (4, 2, 1, 1)
    assert summary["currency_totals"] == {"CNY": "3.57"}
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["source_row"],
             row["source_record_no"]) for row in view["preview"]] == [
        ("SYN-A", "SYN-B", "1.23", 2, 1), ("SYN-D", "SYN-C", "2.34", 4, 3)]
    assert {(entry["field"], entry["column_index"]) for entry in summary["mapping_evidence"]
            if entry["header_row"] == "3"} == {
        ("trade_time", "1"), ("payee_account", "2"), ("payer_account", "3"),
        ("amount", "5"), ("amount", "6")}
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["done"], job["total"], job["result"]["ok"]) == (4, 4, 2)
    before = facts(database, cid)
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in before] == [
        ("SYN-A", "SYN-B", Decimal("1.23")), ("SYN-D", "SYN-C", Decimal("2.34"))]
    records = source_records(client, cid, view["import_id"])
    assert [record["source_row"] for record in records] == [2, 3, 4, 5]
    assert [record["disposition"] for record in records] == [
        "imported", "non_transaction", "imported", "unresolved"]
    assert records[1]["reason_code"] == "repeated_header"
    assert records[-1]["reason_code"] == "conflicting_amount" and records[-1]["flow_id"] is None
    for record in (records[0], records[2]):
        response = client.get(f"/api/v1/cases/{cid}/flows/{record['flow_id']}/sources")
        assert response.status_code == 200
        source = response.json()["items"][0]
        assert (source["source_row"], source["member_row"], source["member_path"],
                source["row_kind"], source["parser_version"]) == (
                    record["record_no"], record["source_row"], record["member_path"], "table_record", NEW_PROFILE)
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == data
    repeated = complete(client, cid, preview(client, cid, data, "repeat" + suffix))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == before and coverage(client, cid)["unresolved_records"] == 1
    assert source_records(client, cid, view["import_id"]) == records
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == job["result"]
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 4
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == before and coverage(client, restored) == coverage(client, cid)
    restored_source = database.q_raw(
        "select source_id,parser_version,mapping_json from import_sources where case_id=? and filename=?",
        [restored, filename])[0]
    assert restored_source["parser_version"] == NEW_PROFILE
    assert json.loads(restored_source["mapping_json"]) == view["mapping"]
    restored_records = source_records(client, restored, restored_source["source_id"])
    assert [{key: value for key, value in record.items() if key != "flow_id"}
            for record in restored_records] == [
        {key: value for key, value in record.items() if key != "flow_id"} for record in records]
    restored_repeat = complete(client, restored, preview(client, restored, data, filename))
    assert (restored_repeat["result"]["ok"], restored_repeat["result"]["rows_dup"]) == (0, 2)
    assert facts(database, restored) == before


@pytest.mark.parametrize("newest_first", [False, True])
def test_v14_ready_session_stays_frozen_and_cannot_rewrite_or_duplicate_different_facts(
        client, database, make_case, monkeypatch, newest_first):
    cid = make_case("Synthetic frozen numeric region history")
    data = payload([HEADERS, FIRST, LATER_HEADERS, SECOND], ".csv")
    with monkeypatch.context() as context:
        context.setattr(imports, "TABLE_PROFILE", OLD_PROFILE)
        old = preview(client, cid, data)
    assert table_import.PROFILE == imports.TABLE_PROFILE == NEW_PROFILE
    assert old["table_summary"]["profile"] == OLD_PROFILE
    assert old["table_summary"]["currency_totals"] == {"CNY": "11.10"}
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in old["preview"]] == [
        ("SYN-A", "SYN-B", "1.23"), ("SYN-C", "SYN-D", "9.87")]
    response = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert response.status_code == 200
    old = response.json()
    assert old["table_summary"]["profile"] == OLD_PROFILE
    assert old["table_summary"]["currency_totals"] == {"CNY": "11.10"}
    current = preview(client, cid, data)
    assert current["table_summary"]["profile"] == NEW_PROFILE
    assert current["table_summary"]["currency_totals"] == {"CNY": "3.57"}
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 2
    before = facts(database, cid)
    assert (before[1]["payer_account"], before[1]["payee_account"], before[1]["amount"]) == (
        ("SYN-D", "SYN-C", Decimal("2.34")) if newest_first else
        ("SYN-C", "SYN-D", Decimal("9.87")))
    original_report = source_records(client, cid, first["import_id"])
    original_tables = {table: database.q_raw(
        f"select * from {table} where case_id=? and source_id=? order by all", [cid, first["import_id"]])
        for table in ("import_sources", "import_members", "import_records", "flow_sources")}
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done" and (second_job["result"]["ok"], second_job["result"]["rows_dup"]) == (0, 1)
    assert facts(database, cid) == before
    for table, values in original_tables.items():
        assert database.q_raw(f"select * from {table} where case_id=? and source_id=? order by all",
                              [cid, first["import_id"]]) == values
    assert source_records(client, cid, first["import_id"]) == original_report
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    conflicting = source_records(client, cid, second["import_id"])[-1]
    assert conflicting["source_row"] == 4 and conflicting["disposition"] == "unresolved"
    assert conflicting["flow_id"] is None
    assert conflicting["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS
    assert coverage(client, cid)["unresolved_records"] == 1
    repeated = complete(client, cid, preview(client, cid, data))
    assert (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2 if newest_first else 1)
    assert facts(database, cid) == before
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before and coverage(client, restored) == coverage(client, cid)
