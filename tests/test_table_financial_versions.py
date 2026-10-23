"""Synthetic version pins keep one physical occurrence and its old receipt."""
import csv
from decimal import Decimal
import io
import json

from fastapi import HTTPException
import pytest

from app import case_packages, table_import
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_mapping_coverage import coverage, envelope
from test_table_mapping_versions import profile_preview


def test_ready_v6_repreview_and_commit_keep_old_money_after_v11_is_default(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic frozen financial ready session")
    payload = ("date,payer_account,payee_account,amount,金额单位\n"
               "2026-01-01,SYN-A,SYN-B,1.01,万元\n").encode()
    old = profile_preview(client, cid, payload, monkeypatch, table_import.ROLE_PROFILE)
    assert old["stats"]["ok"] == 1 and old["table_summary"]["currency_totals"] == {"CNY": "1.01"}
    assert imports.TABLE_PROFILE == table_import.PROFILE == "funds-trace-table-v17"
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
        "mode": old["mode"], "mapping": old["mapping"]})
    assert rechecked.status_code == 200, rechecked.text
    assert rechecked.json()["table_summary"]["profile"] == table_import.ROLE_PROFILE
    assert rechecked.json()["table_summary"]["currency_totals"] == {"CNY": "1.01"}
    old_job = complete(client, cid, rechecked.json())
    assert old_job["stage"] == "done" and old_job["result"]["ok"] == 1
    facts = database.q_raw("select * from flows where case_id=?", [cid])
    current = preview(client, cid, payload)
    assert current["table_summary"]["profile"] == table_import.PROFILE
    assert current["table_summary"]["unresolved"] == 1
    assert complete(client, cid, current)["stage"] == "done"
    assert database.q_raw("select * from flows where case_id=?", [cid]) == facts
    assert facts[0]["amount"] == Decimal("1.01")
    assert client.get(f"/api/v1/cases/{cid}/imports/{old['import_id']}").json()["result"] == old_job["result"]
    assert coverage(client, cid)["unresolved_records"] == 1


FACTS = {
    "trade_no": "SYN-REF", "trade_time": "2026-01-01 10:00:00",
    "payer_name": "Synthetic payer", "payer_account": "SYN-A", "payer_bank": "Synthetic payer bank",
    "payee_name": "Synthetic payee", "payee_account": "SYN-B", "payee_bank": "Synthetic payee bank",
    "amount": "1.01", "balance": "100.00", "currency": "CNY", "summary": "Synthetic memo", "channel": "Synthetic channel",
}
ALTERNATES = {field: "Synthetic different value" for field in table_import.FLOW_FIELDS}
ALTERNATES.update(trade_time="2026-01-02 10:00:00", amount="2.02", balance="200.00", currency="USD")


def fact_payload(field):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    unit_column = {"amount": "金额单位", "balance": "余额单位"}.get(field)
    writer.writerow([*FACTS, unit_column or "synthetic alternate"])
    writer.writerow([*FACTS.values(), "万元" if unit_column else ALTERNATES[field]])
    return stream.getvalue().encode()


@pytest.mark.parametrize("field", table_import.FLOW_FIELDS)
@pytest.mark.parametrize("newest_first", [False, True])
def test_precreated_v6_v7_ready_sessions_bind_all_thirteen_facts_in_both_commit_orders(
        client, database, make_case, monkeypatch, field, newest_first):
    cid = make_case("Synthetic all-fact version history")
    payload = fact_payload(field)
    old = profile_preview(client, cid, payload, monkeypatch, table_import.ROLE_PROFILE)
    if field not in ("amount", "balance"):
        remapped = client.post(f"/api/v1/cases/{cid}/imports/{old['import_id']}/preview", json={
            "mode": old["mode"], "mapping": {**old["mapping"], field: "synthetic alternate"}})
        assert remapped.status_code == 200, remapped.text
        old = remapped.json()
    current = preview(client, cid, payload)
    assert old["stats"]["ok"] == current["stats"]["ok"] == 1
    first, second = (current, old) if newest_first else (old, current)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 1
    fields = ",".join(table_import.FLOW_FIELDS)
    before = database.q_raw(f"select {fields} from flows where case_id=?", [cid])
    # Both sessions were ready before the first commit. The final write must
    # compare every fact again, including fields outside the amount tuple.
    second_job = complete(client, cid, second)
    assert second_job["stage"] == "done" and second_job["result"]["ok"] == 0
    assert second_job["result"]["table_summary"]["unresolved"] == 1
    assert database.q_raw(f"select {fields} from flows where case_id=?", [cid]) == before
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()["result"] == first_job["result"]
    source = client.get(f"/api/v1/cases/{cid}/sources/{second['import_id']}/report").json()["items"][0]
    assert source["flow_id"] is None and source["details"]["counterparty_basis"] == table_import.HISTORY_CONFLICT_BASIS


@pytest.mark.parametrize("first", ["archive", "v7"])
def test_v7_unit_conflict_survives_old_archive_assessment_and_format4_restore(client, database, make_case, first):
    cid = make_case("Synthetic financial gap envelope precedence")
    payload = ("date,payer_account,payee_account,amount,金额单位\n"
               "2026-01-01,SYN-A,SYN-B,1.01,未定义单位\n").encode()
    archive = envelope(payload)
    for kind in (first, "v7" if first == "archive" else "archive"):
        view = preview(client, cid, archive if kind == "archive" else payload,
                       "synthetic.zip" if kind == "archive" else "synthetic.csv")
        job = complete(client, cid, view)
        assert job["stage"] == "done", job
        if kind == "archive":
            assert job["result"]["ok"] == 1
            archived_source, archived_receipt = view["import_id"], job["result"]
        else:
            assert job["result"]["table_summary"]["unresolved"] == 1
            item = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"][0]
            assert item["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
            assert "unknown_unit" in item["details"]["validation_issues"].split(",")
    assert database.q_raw("select amount from flows where case_id=?", [cid]) == [{"amount": Decimal("1.01")}]
    assert coverage(client, cid)["unresolved_records"] == 1
    assert not coverage(client, cid)["financial_interpretation_complete"]
    assert complete(client, cid, preview(client, cid, archive, "repeat.zip"))["stage"] == "done"
    assert coverage(client, cid)["unresolved_records"] == 1
    assert client.get(f"/api/v1/cases/{cid}/imports/{archived_source}").json()["result"] == archived_receipt
    package = case_packages.export_package(cid)[0]
    restored = case_packages.import_package(package)["case_id"]
    assert coverage(client, restored) == coverage(client, cid)
    assert database.q_raw("select amount from flows where case_id=?", [restored]) == [{"amount": Decimal("1.01")}]
    for table in case_packages.JOURNAL_PKG_COLS:
        columns = [column.strip() for column in case_packages.JOURNAL_PKG_COLS[table].split(",")
                   if column.strip() != "source_id"]
        query = f"select {','.join(columns)} from {table} where case_id=? order by {','.join(columns)}"
        assert database.q_raw(query, [restored]) == database.q_raw(query, [cid])


def test_explicit_unit_repreview_keeps_financial_gap_visible_over_old_archive(client, database, make_case):
    cid = make_case("Synthetic explicitly mapped unit coverage")
    payload = ("date,payer_account,payee_account,amount,synthetic unit\n"
               "2026-01-01,SYN-A,SYN-B,1.01,未定义单位\n").encode()
    archive = envelope(payload)
    assert complete(client, cid, preview(client, cid, archive, "synthetic.zip"))["result"]["ok"] == 1
    view = preview(client, cid, payload)
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": {**view["mapping"], "amount_unit": "synthetic unit"}})
    assert rechecked.status_code == 200, rechecked.text
    assert rechecked.json()["table_summary"]["unresolved"] == 1
    assert complete(client, cid, rechecked.json())["stage"] == "done"
    item = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"][0]
    assert item["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
    assert "unknown_unit" in item["details"]["validation_issues"].split(",")
    assert coverage(client, cid)["unresolved_records"] == 1
    assert complete(client, cid, preview(client, cid, archive, "repeat.zip"))["stage"] == "done"
    assert coverage(client, cid)["unresolved_records"] == 1
    assert database.q_raw("select amount from flows where case_id=?", [cid]) == [{"amount": Decimal("1.01")}]


@pytest.mark.parametrize("field,value,issue", [
    ("currency", "USD", "invalid_currency"),
    ("status", "failed", "unknown_status"),
    ("trade_time", "2026-01-02 10:00:00", "conflicting_time"),
    ("direction", "in", "conflicting_direction"),
])
def test_custom_financial_candidate_mapping_survives_repreview_and_commit_with_archive_gap(
        client, database, make_case, field, value, issue):
    cid = make_case("Synthetic pinned custom financial candidate")
    payload = ("date,payer_account,payee_account,amount,currency,status,direction,synthetic field\n"
               f"2026-01-01 10:00:00,SYN-A,SYN-B,1.01,CNY,success,out,{value}\n").encode()
    archive = envelope(payload)
    archived = preview(client, cid, archive, "synthetic.zip")
    archived_job = complete(client, cid, archived)
    assert archived_job["stage"] == "done" and archived_job["result"]["ok"] == 1
    facts = database.q_raw("select * from flows where case_id=?", [cid])
    view = preview(client, cid, payload)
    assert view["stats"]["ok"] == 1
    rechecked = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        "mode": view["mode"], "mapping": {**view["mapping"], field: "synthetic field"}})
    assert rechecked.status_code == 200, rechecked.text
    changed = rechecked.json()
    assert changed["table_summary"]["unresolved"] == 1
    job = complete(client, cid, changed)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    assert job["result"]["table_summary"]["unresolved"] == 1
    assert changed["mapping"][field] == "synthetic field"
    item = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"][0]
    assert item["disposition"] == "unresolved" and item["flow_id"] is None
    assert issue in item["details"]["validation_issues"].split(",")
    assert item["details"]["validation_basis"] == table_import.FINANCIAL_CONFLICT_BASIS
    assert coverage(client, cid)["unresolved_records"] == 1
    assert complete(client, cid, preview(client, cid, archive, "repeat.zip"))["stage"] == "done"
    assert coverage(client, cid)["unresolved_records"] == 1
    assert database.q_raw("select * from flows where case_id=?", [cid]) == facts
    assert client.get(f"/api/v1/cases/{cid}/imports/{archived['import_id']}").json()["result"] == archived_job["result"]


@pytest.mark.parametrize("profile,kind,marker", [
    ("funds-trace-table-v5", "unresolved", table_import.FINANCIAL_CONFLICT_BASIS),
    ("funds-trace-table-v6", "unresolved", table_import.FINANCIAL_CONFLICT_BASIS),
    ("funds-trace-table-v7", "accepted", table_import.FINANCIAL_CONFLICT_BASIS),
    ("funds-trace-table-v7", "nonfinancial", table_import.FINANCIAL_CONFLICT_BASIS),
    ("funds-trace-table-v7", "unresolved", "Synthetic unknown validation basis"),
])
def test_package_rejects_financial_marker_outside_its_profile_disposition_or_issue(
        client, database, make_case, monkeypatch, profile, kind, marker):
    cid = make_case("Synthetic invalid financial marker")
    value = "not-money" if kind == "unresolved" else "1.01"
    payer = "" if kind == "nonfinancial" else "SYN-A"
    payload = f"date,payer_account,payee_account,amount\n2026-01-01,{payer},SYN-B,{value}\n".encode()
    view = profile_preview(client, cid, payload, monkeypatch, profile)
    assert complete(client, cid, view)["stage"] == "done"
    record = database.q_raw("select details_json from import_records where case_id=?", [cid])[0]
    details = json.loads(record["details_json"])
    details["validation_basis"] = marker
    database.x("update import_records set details_json=? where case_id=?", [json.dumps(details), cid])
    with pytest.raises(HTTPException, match="资金校验冲突标记无效"):
        case_packages.import_package(case_packages.export_package(cid)[0])
