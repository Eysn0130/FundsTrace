"""Approved graph-owner boundary preserves synthetic imports and old packages."""
import csv
import io
import json

import pytest

from app import table_import
from funds_assertions import unresolved_funds
from test_automatic_table_import import preview
from test_canvas_management import create_canvas
from test_cases import archive_of, exported, import_package, insert_flow, members
from test_feedback_import_api import archive, complete, preview as feedback_preview, transaction
from test_table_contract_lifecycle import source_records
from test_table_mapping_versions import profile_preview


def table_bytes(rows):
    text = io.StringIO(newline="")
    writer = csv.writer(text)
    writer.writerow(["date", "payer_account", "payee_account", "amount", "currency", "self_account", "trade_no"])
    writer.writerows(rows)
    return text.getvalue().encode()


def track(client, case_id, flow_ids, canvas_id=0):
    response = client.post("/api/track", json={
        "case_id": case_id, "canvas_id": canvas_id, "flow_ids": flow_ids,
    })
    assert response.status_code == 200, response.text


def graph(client, case_id, canvas_id=0):
    response = client.get("/api/graph", params={"case_id": case_id, "canvas_id": canvas_id})
    assert response.status_code == 200, response.text
    return response.json()


def case_state(client, database, case_id):
    """Compare persisted facts/relations with remapped surrogate IDs removed."""
    facts = ",".join((*table_import.FLOW_FIELDS, "self_account", "self_name"))
    stats = client.get(f"/api/v1/cases/{case_id}/stats")
    assert stats.status_code == 200, stats.text
    state = {"stats": {key: value for key, value in stats.json().items() if key != "case_id"}}
    queries = {
        "facts": f"select {facts} from flows where case_id=? order by trade_no",
        "sources": """select filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind,imported_at
            from import_sources where case_id=? order by all""",
        "links": """select s.filename,l.source_row,f.trade_no,l.disposition
            from flow_sources l join import_sources s on s.case_id=l.case_id and s.source_id=l.source_id
            join flows f on f.case_id=l.case_id and f.id=l.flow_id where l.case_id=? order by all""",
        "members": """select s.filename,m.member_path,m.role,m.sha256,m.size_bytes,m.encoding,m.row_count
            from import_members m join import_sources s on s.case_id=m.case_id and s.source_id=m.source_id
            where m.case_id=? order by all""",
        "records": """select s.filename,r.record_no,r.member_path,r.source_row,r.record_key,
            r.disposition,r.reason_code,r.details_json
            from import_records r join import_sources s on s.case_id=r.case_id and s.source_id=r.source_id
            where r.case_id=? order by all""",
        "tracking": """select f.trade_no,coalesce(c.name,'default') as canvas,t.tracked_at
            from tracked_flows t join flows f on f.case_id=t.case_id and f.id=t.flow_id
            left join canvases c on c.case_id=t.case_id and c.id=t.canvas_id
            where t.case_id=? order by all""",
    }
    for key, query in queries.items():
        state[key] = database.q_raw(query, [case_id])
    return state


def assert_edge(view, count, amount):
    assert len(view["edges"]) == 1
    edge = view["edges"][0]
    assert (edge["source"], edge["target"], edge["cnt"], edge["total"]) == (
        "SYN-A", "SYN-B", None, None)
    assert edge["amount_totals"] == [{"currency": "CNY", "amount": None}]
    assert edge["funds"] == view["funds"] == unresolved_funds(count, ("CNY", amount))


def assert_restored_state(before, restored):
    original_revision = before["stats"]["analysis_revision"]
    restored_revision = restored["stats"]["analysis_revision"]
    assert isinstance(original_revision, str) and len(original_revision) == 32
    assert isinstance(restored_revision, str) and len(restored_revision) == 32
    assert restored_revision != original_revision
    # Only the new case's analysis generation changes. All financial facts,
    # source journals, relationships and every other statistic remain exact.
    assert {**restored, "stats": {**restored["stats"], "analysis_revision": original_revision}} == before


@pytest.mark.parametrize("profile", ["funds-trace-table-v5", "funds-trace-table-v17"])
def test_unknown_owner_import_append_and_restore_preserve_original_facts_and_sources(
        client, database, make_case, monkeypatch, profile):
    case_id = make_case("Synthetic owner-boundary import lifecycle")
    other = make_case("Synthetic owner-boundary separate case")
    unknown = table_bytes([
        ["2026-01-01 10:00:00", "SYN-A", "SYN-B", "100.00", "CNY", "", "SYN-U1"],
        ["2026-01-01 10:00:01", "SYN-A", "SYN-B", "100.00", "CNY", "", "SYN-U2"],
    ])
    view = profile_preview(client, case_id, unknown, monkeypatch, profile)
    assert view["automatic"] and view["table_summary"]["profile"] == profile
    first = complete(client, case_id, view)
    assert first["stage"] == "done" and (first["result"]["ok"], first["result"]["rows_dup"]) == (2, 0)
    initial_facts = database.q_raw("select * from flows where case_id=? order by id", [case_id])
    initial_report = source_records(client, case_id, view["import_id"])
    ids = [row["id"] for row in initial_facts]
    assert [row["self_account"] for row in initial_facts] == ["", ""]
    track(client, case_id, ids)
    # Both original observations remain; neither owner nor timing certifies an event.
    assert_edge(graph(client, case_id), 2, "200.00")
    named = create_canvas(client, case_id, "Synthetic independent canvas")
    track(client, case_id, ids[:1], named)

    known = table_bytes([
        ["2026-01-01 10:10:00", "SYN-A", "SYN-B", "100.00", "CNY", "SYN-A", "SYN-K1"],
        ["2026-01-01 10:10:01", "SYN-A", "SYN-B", "100.00", "CNY", "SYN-B", "SYN-K2"],
    ])
    appended = complete(client, case_id, preview(client, case_id, known, "synthetic-confirmed.csv"))
    assert appended["stage"] == "done" and (appended["result"]["ok"], appended["result"]["rows_dup"]) == (2, 0)
    all_ids = [row["id"] for row in database.q("select id from flows where case_id=? order by id", [case_id])]
    track(client, case_id, all_ids)
    assert_edge(graph(client, case_id), 4, "400.00")
    assert_edge(graph(client, case_id, named), 1, "100.00")
    # The B ledger is not a mirror of this canvas's sole earlier unknown ledger.
    track(client, case_id, all_ids[-1:], named)
    assert_edge(graph(client, case_id, named), 2, "200.00")
    assert graph(client, other)["edges"] == []
    rejected = client.post("/api/track", json={"case_id": other, "flow_ids": all_ids})
    assert rejected.status_code == 404, rejected.text
    for content, name in [(unknown, "synthetic-unknown-repeat.csv"), (known, "synthetic-confirmed-repeat.csv")]:
        repeated = complete(client, case_id, preview(client, case_id, content, name))
        assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert database.q_raw("select * from flows where case_id=? and id in (?,?) order by id", [case_id, *ids]) == initial_facts
    assert source_records(client, case_id, view["import_id"]) == initial_report
    receipt = client.get(f"/api/v1/cases/{case_id}/imports/{view['import_id']}")
    assert receipt.status_code == 200 and receipt.json()["result"] == first["result"]
    before = case_state(client, database, case_id)
    assert before["stats"]["flows"] is None
    assert before["stats"]["amount_totals"] == [{"currency": "CNY", "amount": None}]
    assert before["stats"]["funds"] == unresolved_funds(4, ("CNY", "400.00"))
    content = exported(client, case_id)
    assert json.loads(members(content)["meta.json"])["format"] == 4
    restored = import_package(client, content)
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()["case_id"]
    assert_restored_state(before, case_state(client, database, restored_id))
    assert case_state(client, database, case_id) == before
    assert_edge(graph(client, restored_id), 4, "400.00")
    restored_named = database.q("select id from canvases where case_id=? and name=?", [
        restored_id, "Synthetic independent canvas"])[0]["id"]
    assert_edge(graph(client, restored_id, restored_named), 2, "200.00")
    for content, name in [(unknown, "restored-unknown.csv"), (known, "restored-confirmed.csv")]:
        repeated = complete(client, restored_id, preview(client, restored_id, content, name))
        assert repeated["stage"] == "done" and (repeated["result"]["ok"], repeated["result"]["rows_dup"]) == (0, 2)
    assert database.q_raw("select count(*) n from flows where case_id=?", [restored_id]) == [{"n": 4}]
    assert_edge(graph(client, restored_id), 4, "400.00")


@pytest.mark.parametrize("format_version", [1, 2, 3, 4])
def test_historical_package_recomputes_unknown_owner_graph_without_rewriting_history(
        client, database, make_case, flow_row, format_version):
    case_id = make_case(f"Synthetic historical owner package {format_version}")
    if format_version == 3:
        view = feedback_preview(client, case_id, archive([transaction(**{
            "交易对手账卡号": "SYN-Z", "交易流水号": "SYN-SOURCE", "交易时间": "2026-01-02 10:00:00",
        })]))
        assert complete(client, case_id, view)["stage"] == "done"
    elif format_version == 4:
        payload = table_bytes([["2026-01-02 10:00:00", "SYN-A", "SYN-Z", "12.34", "CNY", "SYN-A", "SYN-SOURCE"]])
        assert complete(client, case_id, preview(client, case_id, payload))["stage"] == "done"
    # These unaltered legacy facts need no inferred owner to remain visible.
    ids = [insert_flow(database, case_id, flow_row, trade_no=f"SYN-UNKNOWN-{index}",
                       trade_time=f"2026-01-01 10:00:0{index}", self_account=owner,
                       amount="100.00", balance=None)
           for index, owner in enumerate(["", None])]
    named = create_canvas(client, case_id, "Synthetic historic canvas", {
        "nodes": [{"id": "SYN-A", "x": 10, "y": 20}], "edges": [],
    })
    track(client, case_id, ids)
    track(client, case_id, ids, named)
    before = case_state(client, database, case_id)
    assert_edge(graph(client, case_id), 2, "200.00")
    content = exported(client, case_id)
    entries = members(content)
    meta = json.loads(entries["meta.json"])
    if format_version == 1:
        assert meta["format"] == 2
        meta["format"] = 1
        meta.pop("source_files")
        entries["meta.json"] = json.dumps(meta).encode()
        entries.pop("import_sources.parquet")
        entries.pop("flow_sources.parquet")
        content = archive_of(entries)
    else:
        assert meta["format"] == format_version
    restored = import_package(client, content)
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()["case_id"]
    assert_restored_state(before, case_state(client, database, restored_id))
    assert case_state(client, database, case_id) == before
    assert_edge(graph(client, restored_id), 2, "200.00")
    restored_named = database.q("select id from canvases where case_id=? and name=?", [
        restored_id, "Synthetic historic canvas"])[0]["id"]
    assert_edge(graph(client, restored_id, restored_named), 2, "200.00")
    assert client.get(f"/api/canvases/{restored_named}", params={"case_id": restored_id}).json()["snapshot"] == {
        "nodes": [{"id": "SYN-A", "x": 10, "y": 20}], "edges": [],
    }
