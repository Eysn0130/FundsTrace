"""Synthetic public regressions for versioned source contracts and their journal."""
import csv
import io
import json
from decimal import Decimal
import zipfile

from fastapi import HTTPException
import pytest

from app import case_packages, table_contracts, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_mapping_coverage import coverage, envelope
from test_table_mapping_versions import profile_preview


HEADERS = ("date", "account", "counterparty account", "amount", "currency",
           "交易前账面余额", "交易后账面余额")
FACT_COLUMNS = ",".join((*table_import.FLOW_FIELDS, "self_account", "self_name"))
PERSISTENT_TABLES = ("flows", "batches", "import_sources", "flow_sources",
                     "import_members", "import_records", "main_accounts")


def content(rows=None, *, headers=HEADERS):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows if rows is not None else [
        ["2026-02-01 10:00:00", "SYN-OWN", "SYN-OTHER", "12.34", "CNY", "100", "87.66"],
        ["2026-02-01 11:00:00", "SYN-OWN", "SYN-USD", "5.00", "USD", "-10.00", "-5.00"],
    ])
    return stream.getvalue().encode()


def source_records(client, cid, source_id):
    response = client.get(f"/api/v1/cases/{cid}/sources/{source_id}/report")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def facts(database, cid):
    return database.q_raw(f"select {FACT_COLUMNS} from flows where case_id=? order by trade_time", [cid])


def snapshot(database):
    return {table: database.q_raw(f"select * from {table} order by all")
            for table in PERSISTENT_TABLES}


def test_ready_v7_session_keeps_its_financial_interpretation_after_v11_is_default(
        client, database, make_case, monkeypatch):
    cid = make_case("Synthetic frozen v7 contract session")
    payload = ("date,account,counterparty account,signed amount/分,direction,currency\n"
               "2026-02-01 10:00:00,SYN-OWN,SYN-OTHER,-123,out,CNY\n").encode()
    old = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v7")
    assert old["table_summary"]["accepted"] == 1
    assert imports.TABLE_PROFILE == table_import.PROFILE == "funds-trace-table-v16"
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert rechecked.status_code == 200, rechecked.text
    frozen = rechecked.json()
    assert frozen["table_summary"]["profile"] == "funds-trace-table-v7"
    assert frozen["table_summary"]["currency_totals"] == {"CNY": "1.23"}
    job = complete(client, cid, frozen)
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    before = facts(database, cid)
    assert (before[0]["payer_account"], before[0]["payee_account"], before[0]["amount"]) == (
        "SYN-OWN", "SYN-OTHER", Decimal("1.23"))
    record = source_records(client, cid, old["import_id"])[0]
    assert not table_contracts.DETAILS_FIELDS.intersection(record["details"])
    assert complete(client, cid, preview(client, cid, payload))["stage"] == "done"
    assert facts(database, cid) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == job["result"]
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == before


def test_transaction_balance_pair_recovers_actual_direction_and_round_trips_without_a_second_ledger(
        client, database, make_case):
    cid = make_case("Synthetic transaction balance pair lifecycle")
    payload = content()
    view = preview(client, cid, payload)
    assert view["automatic"] and view["table_summary"]["accepted"] == 2
    job = complete(client, cid, view)
    assert job["stage"] == "done" and (job["result"]["ok"], job["result"]["rows_dup"]) == (2, 0)
    before = facts(database, cid)
    assert [(row["payer_account"], row["payee_account"], row["amount"], row["balance"], row["currency"])
            for row in before] == [
        ("SYN-OWN", "SYN-OTHER", Decimal("12.34"), Decimal("87.66"), "CNY"),
        ("SYN-USD", "SYN-OWN", Decimal("5.00"), Decimal("-5.00"), "USD"),
    ]
    records = source_records(client, cid, view["import_id"])
    assert all(record["disposition"] == "imported" and record["flow_id"] for record in records)
    assert all(table_contracts.DETAILS_FIELDS.intersection(record["details"]) for record in records)
    assert all(table_contracts.validate_details(record["details"], record["disposition"]) for record in records)
    repeated = complete(client, cid, preview(client, cid, payload, "synthetic-repeat.csv"))
    assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert facts(database, cid) == before
    assert coverage(client, cid)["unresolved_records"] == 0
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 4
        assert not any(name.endswith("synthetic.csv") for name in archive.namelist())
    restored = case_packages.import_package(package)["case_id"]
    assert facts(database, restored) == before
    assert coverage(client, restored) == coverage(client, cid)
    restored_source = database.q_raw("select source_id from import_sources where case_id=? and filename='synthetic.csv'",
                                    [restored])[0]["source_id"]
    restored_records = source_records(client, restored, restored_source)
    assert [{key: value for key, value in record.items() if key != "flow_id"} for record in restored_records] == [
        {key: value for key, value in record.items() if key != "flow_id"} for record in records]
    assert all(record["flow_id"] for record in restored_records)


@pytest.mark.parametrize("legacy", ["v7", "archive"])
@pytest.mark.parametrize("contract_first", [False, True])
def test_balance_conflict_remains_visible_after_older_interpretation_and_package(
        client, database, make_case, monkeypatch, legacy, contract_first):
    cid = make_case("Synthetic stronger source contract gap")
    payload = content([["2026-02-01 10:00:00", "SYN-OWN", "SYN-OTHER", "10", "CNY", "100", "95", "out"]],
                      headers=(*HEADERS, "direction"))
    archive = envelope(payload)
    for kind in (("contract", legacy) if contract_first else (legacy, "contract")):
        if kind == "archive":
            view = preview(client, cid, archive, "synthetic.zip")
        elif kind == "v7":
            view = profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v7")
        else:
            view = preview(client, cid, payload)
        job = complete(client, cid, view)
        assert job["stage"] == "done", job
        if kind == "contract":
            assert job["result"]["table_summary"]["unresolved"] == 1
            record = source_records(client, cid, view["import_id"])[0]
            assert record["flow_id"] is None and record["disposition"] == "unresolved"
            assert record["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
        else:
            old_id, old_receipt = view["import_id"], job["result"]
            assert job["result"]["ok"] == 1
    expected = facts(database, cid)
    assert len(expected) == 1 and expected[0]["amount"] == Decimal("10.00")
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    repeated = (preview(client, cid, archive, "synthetic-repeat.zip") if legacy == "archive"
                else profile_preview(client, cid, payload, monkeypatch, "funds-trace-table-v7"))
    assert complete(client, cid, repeated)["stage"] == "done"
    assert facts(database, cid) == expected
    assert coverage(client, cid)["unresolved_records"] == 1
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_id}").json()["result"] == old_receipt
    restored = case_packages.import_package(case_packages.export_package(cid)[0])["case_id"]
    assert facts(database, restored) == expected
    assert coverage(client, restored) == coverage(client, cid)


@pytest.mark.parametrize("profile", ["funds-trace-table-v5", "funds-trace-table-v6", "funds-trace-table-v7"])
def test_package_rejects_source_contract_metadata_on_frozen_profiles(
        client, database, make_case, monkeypatch, profile):
    donor = make_case("Synthetic contract metadata donor")
    view = preview(client, donor, content())
    assert complete(client, donor, view)["stage"] == "done"
    fields = source_records(client, donor, view["import_id"])[0]["details"]
    metadata = {key: fields[key] for key in table_contracts.DETAILS_FIELDS if key in fields}
    assert metadata
    cid = make_case("Synthetic frozen profile metadata rejection")
    payload = b"date,payer_account,payee_account,amount\n2026-02-01,SYN-A,SYN-B,1.01\n"
    old = profile_preview(client, cid, payload, monkeypatch, profile)
    assert complete(client, cid, old)["stage"] == "done"
    value = database.q_raw("select details_json from import_records where case_id=?", [cid])[0]["details_json"]
    details = {**json.loads(value), **metadata}
    database.x("update import_records set details_json=? where case_id=?", [json.dumps(details), cid])
    with pytest.raises(HTTPException, match="语义元数据无效"):
        case_packages.import_package(case_packages.export_package(cid)[0])


@pytest.mark.parametrize("fault", ["hash", "amount_model", "missing_hash"])
def test_package_validates_contract_fingerprint_syntax_and_closed_semantic_values(
        client, database, make_case, fault):
    cid = make_case("Synthetic contract metadata integrity")
    view = preview(client, cid, content())
    assert complete(client, cid, view)["stage"] == "done"
    record = source_records(client, cid, view["import_id"])[0]
    details = dict(record["details"])
    if fault == "missing_hash":
        del details["contract_sha256"]
    elif fault == "hash":
        details["contract_sha256"] = "g" * 64
    else:
        details["amount_model"] = "Synthetic unsupported model"
    database.x("update import_records set details_json=? where case_id=? and record_no=1",
               [json.dumps(details), cid])
    with pytest.raises(HTTPException, match="来源契约元数据无效"):
        case_packages.import_package(case_packages.export_package(cid)[0])


def test_package_rejects_removing_every_contract_field_from_default_accepted_record(
        client, database, make_case):
    cid = make_case("Synthetic stripped current contract metadata")
    view = preview(client, cid, content())
    assert complete(client, cid, view)["stage"] == "done"
    record = source_records(client, cid, view["import_id"])[0]
    assert record["disposition"] == "imported"
    assert table_contracts.DETAILS_FIELDS.intersection(record["details"])
    stripped = {key: value for key, value in record["details"].items()
                if key not in table_contracts.DETAILS_FIELDS}
    database.x("update import_records set details_json=? where case_id=? and record_no=1",
               [json.dumps(stripped), cid])
    with pytest.raises(HTTPException, match="来源契约元数据无效"):
        case_packages.import_package(case_packages.export_package(cid)[0])


@pytest.mark.parametrize("profile", ["funds-trace-table-v5", "funds-trace-table-v6", "funds-trace-table-v7"])
@pytest.mark.parametrize("issue", sorted(table_contracts.VALIDATION_ISSUES))
def test_frozen_profile_cannot_claim_a_new_balance_contract_failure(
        client, database, make_case, monkeypatch, profile, issue):
    cid = make_case("Synthetic frozen balance reason rejection")
    payload = b"date,payer_account,payee_account,amount\n2026-02-01,SYN-A,SYN-B,not-money\n"
    view = profile_preview(client, cid, payload, monkeypatch, profile)
    assert complete(client, cid, view)["stage"] == "done"
    record = source_records(client, cid, view["import_id"])[0]
    assert record["disposition"] == "unresolved"
    details = {**record["details"], "validation_issues": issue, "reason": table_import.REASONS[issue]}
    database.x("update import_records set reason_code=?,details_json=? where case_id=?",
               [issue, json.dumps(details), cid])
    with pytest.raises(HTTPException, match="余额契约不属于该解析版本"):
        case_packages.import_package(case_packages.export_package(cid)[0])


def test_unknown_and_blank_records_without_contract_metadata_round_trip_in_format4(
        client, database, make_case):
    cid = make_case("Synthetic unparsed record contract exemptions")
    view = preview(client, cid, b"unknown_a,unknown_b\nSYN-A,SYN-B\n,\n")
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    records = source_records(client, cid, view["import_id"])
    assert [record["reason_code"] for record in records] == ["unknown_table", "unknown_table", "blank_record"]
    assert not any(table_contracts.DETAILS_FIELDS.intersection(record["details"]) for record in records)
    package = case_packages.export_package(cid)[0]
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("meta.json"))["format"] == 4
    restored = case_packages.import_package(package)["case_id"]
    restored_source = database.q_raw("select source_id from import_sources where case_id=?", [restored])[0]["source_id"]
    assert source_records(client, restored, restored_source) == records
    assert coverage(client, restored) == coverage(client, cid)
    assert facts(database, restored) == []


@pytest.mark.parametrize("fault", ["changed_direction", "journal_failure"])
def test_inferred_direction_write_failure_rolls_back_all_facts_and_can_retry(
        client, database, make_case, monkeypatch, fault):
    cid = make_case("Synthetic inferred direction write rollback")
    old = preview(client, cid, b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.01\n")
    old_job = complete(client, cid, old)
    assert old_job["stage"] == "done"
    before = snapshot(database)
    view = preview(client, cid, content())
    write = database.insert_dataframe

    def corrupt(table, frame, columns):
        if fault == "journal_failure" and table == "import_records":
            raise RuntimeError("Synthetic journal write failure")
        if fault == "changed_direction" and table == "flows":
            frame = frame.copy(deep=True)
            frame.loc[frame.index[0], "payer_account"] = "SYN-OTHER"
            frame.loc[frame.index[0], "payee_account"] = "SYN-OWN"
        return write(table, frame, columns)

    with monkeypatch.context() as context:
        context.setattr(database, "insert_dataframe", corrupt)
        failed = complete(client, cid, view)
    assert failed["stage"] == "error" and failed["result"] is None
    if fault == "changed_direction":
        assert failed["error_code"] == "import_integrity_failed"
    assert snapshot(database) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == old_job["result"]
    retry = complete(client, cid, view)
    assert retry["stage"] == "done" and retry["result"]["ok"] == 2
