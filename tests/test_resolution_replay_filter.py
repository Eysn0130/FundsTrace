"""Public replay cost and malformed-source rollback remain separate contracts."""
import csv
import io
import json
from types import SimpleNamespace

import duckdb
import pytest

from app import evidence_resolution as e
from test_evidence_replay import list_load
from test_folder_evidence import accepted, account, complete, table, transaction


def ordinary(client, cid, name, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["交易流水号", "交易时间", "付款账号", "收款账号", "交易金额", "币种", "摘要"])
    writer.writerows(rows)
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": (name, stream.getvalue().encode("utf-8-sig"))})
    assert response.status_code == 200, response.text
    view = response.json(); job = complete(client, cid, view)
    assert job["stage"] == "done", job
    return view, job["result"]


def test_public_later_import_does_not_decode_wide_untyped_history(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic irrelevant wide audit replay")
    marker = "SYN-WIDE-UNTYPED resolution_evidence "
    view, first = ordinary(client, cid, "wide.csv", [
        [f"SYN-FILTER-{index}", f"2026-01-01 10:00:0{index}", "000SYN-A", "000SYN-B", "1.01", "CNY", marker + "x" * 10000]
        for index in range(3)])
    # A retained audit extension is not a financial field or typed declaration.
    # Populate this historical fixture before exercising the next public import.
    for row in database.q_raw("select record_no,details_json from import_records where case_id=? and source_id=?", [cid, view["import_id"]]):
        notes = json.loads(row["details_json"]); notes["unused_audit_note"] = marker + "x" * 10000
        database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
                   [json.dumps(notes, ensure_ascii=False), cid, view["import_id"], row["record_no"]])
    previous_flows = database.q_raw("select * from flows where case_id=? order by id", [cid])
    previous_notes = database.q_raw("select * from import_records where case_id=? and source_id=? order by record_no", [cid, view["import_id"]])
    assert len(previous_notes) == 3 and all(marker in row["details_json"] for row in previous_notes)
    assert all(e.DETAIL_FIELD not in json.loads(row["details_json"]) for row in previous_notes)
    decoded_history = []
    def loads(value, *args, **kwargs):
        if isinstance(value, str) and marker in value: decoded_history.append(len(value))
        return json.loads(value, *args, **kwargs)
    # Replace this module's facade, never the shared stdlib decoder used by HTTP.
    monkeypatch.setattr(e, "json", SimpleNamespace(loads=loads, dumps=json.dumps, JSONEncoder=json.JSONEncoder))
    ordinary(client, cid, "later.csv", [["SYN-LATER", "2026-01-02 10:00:00", "000SYN-A", "000SYN-C", "2.02", "CNY", "Synthetic later record"]])
    assert decoded_history == [], "The replay decoded historical audit rows that contain no typed declaration"
    assert database.q_raw("select * from flows where case_id=? and batch_id=? order by id", [cid, first["batch_id"]]) == previous_flows
    assert database.q_raw("select * from import_records where case_id=? and source_id=? order by record_no", [cid, view["import_id"]]) == previous_notes
    page = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    assert page["total"] == 4 and sorted(row["amount"] for row in page["items"]) == ["1.01", "1.01", "1.01", "2.02"]
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["funds"]["totals"] == [
        {"currency": "CNY", "amount": None, "lower": "0.00", "upper": "5.05"}]


@pytest.mark.parametrize("variant", ["escaped_key", "null_then_typed", "typed_then_null", "nested_key", "key_text"])
def test_replay_filter_keeps_python_key_and_duplicate_semantics(client, database, make_case, variant):
    cid = make_case("Synthetic replay key semantics")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    saved = database.q_raw("select source_id,record_no,details_json from import_records where case_id=? and disposition='imported'", [cid])[0]
    raw = saved["details_json"]
    if variant == "escaped_key": raw = raw.replace('"resolution_evidence":', '"resolution_\\u0065vidence":')
    elif variant == "null_then_typed": raw = '{"resolution_evidence":null,' + raw[1:]
    elif variant == "typed_then_null": raw = raw[:-1] + ',"resolution_evidence":null}'
    elif variant == "nested_key": raw = '{"nested":{"resolution_evidence":"not a top-level declaration"}}'
    elif variant == "key_text": raw = json.dumps({"unused": '{"resolution_evidence":"quoted note"}'})
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?", [raw, cid, saved["source_id"], saved["record_no"]])
    assert list(e._load(cid)) == list_load(database, cid)
    assert database.q_raw("select details_json from import_records where case_id=? and source_id=? and record_no=?", [cid, saved["source_id"], saved["record_no"]])[0]["details_json"] == raw


def snapshot(database, cid):
    return {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid])
        for name in ["flows", "batches", "import_records", "flow_sources", "import_sources", "import_source_operations",
                     "evidence_sources", "resolution_state", "resolution_revisions", "resolution_entities", "resolved_endpoints", "resolution_subject_aliases"]}


@pytest.mark.parametrize("raw", ['{"unused":"value",}', '{"unused":["value",]}', '{"unused":Inf}',
                                  '{"unused":nan}', '[]', 'null', '{"resolution_evidence":"invalid typed payload"}'])
def test_public_delete_replay_rejects_corruption_and_rolls_back(client, database, make_case, raw):
    cid = make_case("Synthetic corrupt replay rollback")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    saved = database.q_raw("select source_id,record_no from import_records where case_id=? and disposition='imported'", [cid])[0]
    _, removable = ordinary(client, cid, "separate.csv", [["SYN-SEPARATE", "2026-01-02 10:00:00", "SYN-C", "SYN-D", "2.02", "CNY", "Synthetic separate batch"]])
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?", [raw, cid, saved["source_id"], saved["record_no"]])
    before = snapshot(database, cid)
    with pytest.raises((AttributeError, TypeError, ValueError, duckdb.Error)): list(e._load(cid))
    try:
        response = client.delete(f"/api/batches/{removable['batch_id']}", params={"case_id": cid})
    except (AttributeError, TypeError, ValueError, duckdb.Error): pass
    else: assert response.status_code >= 400, response.text
    assert snapshot(database, cid) == before


def test_public_clean_case_replay_is_not_blocked_by_another_cases_bad_json(client, database, make_case):
    bad, clean = make_case("Synthetic corrupt other case"), make_case("Synthetic clean replay case")
    for cid in (bad, clean): accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    database.x("update import_records set details_json='{' where case_id=? and disposition='imported'", [bad])
    accepted(client, clean, [("account.csv", table("account", [account(number="SYN-C", card="SYN-CARD-C")]))])
    page = client.get(f"/api/v1/cases/{clean}/flows", params={"granularity": "observations"}).json()
    assert page["total"] == 1 and page["items"][0]["amount"] == "10.25"
    assert database.q_raw("select details_json from import_records where case_id=? and disposition='imported'", [bad])[0]["details_json"] == "{"
    with pytest.raises((AttributeError, TypeError, ValueError, duckdb.Error)): list(e._load(bad))
