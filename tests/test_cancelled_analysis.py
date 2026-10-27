"""Paired cancellation leaves synthetic provenance but no effective money."""
import io
import json

import openpyxl

from test_cases import insert_flow
from test_folder_evidence import assert_unresolved_funds


def source(database, case_id, source_id, version):
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,
                   mapping_json,sheet,parser_version,row_kind)
                   values (?,?,'synthetic.zip',?,100,'B','{}','',?,'archive_record')""",
               [source_id, case_id, str(version) * 64, f"funds-trace-bank-feedback-v{version}"])
    database.x("""insert into import_members(case_id,source_id,member_path,role,sha256,size_bytes,encoding,row_count)
                   values (?,?,'synthetic/transactions.csv','transaction',?,100,'utf-8-sig',2)""",
               [case_id, source_id, "a" * 64])


def record(database, case_id, source_id, number, key, disposition, details=None):
    database.x("""insert into import_records(case_id,source_id,record_no,member_path,source_row,
                   record_key,disposition,reason_code,details_json)
                   values (?,?,?,'synthetic/transactions.csv',?,?,?,?,?)""",
               [case_id, source_id, number, number + 1, key, disposition,
                "matched_cancellation" if disposition == "cancelled" else "synthetic",
                json.dumps(details or {})])


def seed(database, make_case, flow_row):
    case_id = make_case("Synthetic paired cancellation readers")
    anchor = insert_flow(database, case_id, flow_row, trade_no="ANCHOR", amount="10.00",
                         payer_account="A", payee_account="B", self_account="A")
    cancelled = insert_flow(database, case_id, flow_row, trade_no="CANCELLED", amount="900.00",
                            trade_time="2026-01-01 10:01:00", payer_account="B",
                            payee_account="CANCELLED-ONLY", self_account="B")
    kept = insert_flow(database, case_id, flow_row, trade_no="KEPT", amount="10.00",
                       trade_time="2026-01-01 10:02:00", payer_account="B", payee_account="C",
                       self_account="C")
    refund = insert_flow(database, case_id, flow_row, trade_no="REFUND", amount="3.00",
                         trade_time="2026-01-01 10:03:00", payer_account="C", payee_account="B",
                         self_account="C", summary="普通退款")
    source(database, case_id, "synthetic-v2", 2)
    record(database, case_id, "synthetic-v2", 1, "original-key", "imported")
    database.x("""insert into flow_sources(case_id,source_id,source_row,flow_id,disposition)
                   values (?,'synthetic-v2',1,?,'imported')""", [case_id, cancelled])
    source(database, case_id, "synthetic-v3", 3)
    metadata = {"cancellation_policy": "paired-cancellation-v1", "cancellation_role": "original"}
    record(database, case_id, "synthetic-v3", 1, "original-key", "cancelled", metadata)
    record(database, case_id, "synthetic-v3", 2, "reversal-key", "cancelled",
           {**metadata, "cancellation_role": "reversal"})
    ids = [anchor, cancelled, kept, refund]
    # This membership predates v3. Keep the full set for stable undo CAS.
    for flow_id in ids:
        database.x("insert into tracked_flows(case_id,flow_id) values (?,?)", [case_id, flow_id])
    return case_id, anchor, cancelled, kept, refund


def test_public_effective_readers_share_one_exclusion_and_keep_original_source(client, database, make_case, flow_row):
    cid, anchor, cancelled, kept, refund = seed(database, make_case, flow_row)
    active = {anchor, kept, refund}
    legacy = client.get("/api/stats", params={"case_id": cid}).json()
    versioned = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert (legacy["flows"], legacy["accounts"]) == (None, 3)
    assert (versioned["flows"], versioned["accounts"], versioned["amount_total"]) == (None, 3, None)
    assert legacy["funds"] == versioned["funds"]
    assert_unresolved_funds(legacy["funds"], 3, [("CNY", "23.00")])
    assert legacy["import_coverage"] == versioned["import_coverage"]
    assert legacy["import_coverage"]["cancelled_records"] == 2
    assert legacy["import_coverage"]["excluded_flows"] == 1
    assert {r["id"] for r in client.get("/api/flows", params={"case_id": cid}).json()["rows"]} == active
    assert {int(r["id"]) for r in client.get(f"/api/v1/cases/{cid}/flows").json()["items"]} == active
    assert sorted(r["amount"] for r in client.get(f"/api/v1/cases/{cid}/flows").json()["items"]) == ["10.00", "10.00", "3.00"]

    profile = client.get("/api/accounts/B/profile", params={"case_id": cid}).json()
    assert (profile["cnt"], profile["out_amt"], profile["in_amt"]) == (None, None, None)
    assert_unresolved_funds(profile["funds"], 3, [("CNY", "23.00")])
    assert_unresolved_funds(profile["out_funds"], 1, [("CNY", "10.00")])
    assert_unresolved_funds(profile["in_funds"], 2, [("CNY", "13.00")])
    assert {r["acct"] for r in profile["top_counterparties"]} == {"A", "C"}
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert set(graph["tracked_ids"]) == active | {cancelled}
    assert {r["account"] for r in graph["nodes"]} == {"A", "B", "C"}
    assert {fid for edge in graph["edges"] for fid in edge["ids"]} == active
    assert all(edge["cnt"] is edge["total"] is None for edge in graph["edges"])
    assert {(r["source"], r["target"], r["funds"]["totals"][0]["upper"]) for r in graph["edges"]} == {
        ("A", "B", "10.00"), ("B", "C", "10.00"), ("C", "B", "3.00")}
    for edge in graph["edges"]:
        assert_unresolved_funds(edge["funds"], 1, [("CNY", "3.00" if edge["source"] == "C" else "10.00")])
    assert database.q("select id from flows where case_id=? and id=?", [cid, cancelled]) == [{"id": cancelled}]
    provenance = client.get(f"/api/v1/cases/{cid}/flows/{cancelled}/sources")
    assert provenance.status_code == 200, provenance.text
    assert provenance.json()["items"][0]["source_id"] == "synthetic-v2"


def test_analysis_context_bulk_and_workbook_exclude_cancellation_but_keep_refund(client, database, make_case, flow_row):
    cid, anchor, cancelled, kept, refund = seed(database, make_case, flow_row)
    context = client.get(f"/api/context/{anchor}", params={"case_id": cid}).json()
    assert context["total"] == 3
    assert {r["id"] for r in context["rows"]} == {anchor, kept, refund}
    assert "CANCELLED-ONLY" not in json.dumps(context["suggestions"])
    assert client.get(f"/api/context/{cancelled}", params={"case_id": cid}).status_code == 404
    paths = client.post("/api/analysis/paths", json={"case_id": cid, "src": "A", "dst": "C"})
    assert paths.status_code == 200, paths.text
    path = paths.json()["paths"][0]
    assert path["total"] is None
    assert_unresolved_funds(path["funds"], 2, [("CNY", "20.00")])
    assert [row["observation_amount"] for row in path["path"]] == ["10.00", "10.00"]
    excluded_path = client.post("/api/analysis/paths", json={"case_id": cid, "src": "A", "dst": "CANCELLED-ONLY"})
    assert excluded_path.json()["paths"] == []
    analysis = client.post("/api/analysis/run", json={"case_id": cid, "params": {"algorithms": ["cycles"]}}).json()
    assert analysis["source_snapshot"]["flows"] is None
    assert_unresolved_funds(analysis["source_snapshot"]["funds"], 3, [("CNY", "23.00")])
    assert "CANCELLED-ONLY" not in json.dumps(analysis)

    export = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert export.status_code == 200, export.text
    workbook = openpyxl.load_workbook(io.BytesIO(export.content))
    values = list(workbook["追踪流水"].values)
    exported = [dict(zip(values[0], row)) for row in values[1:]]
    assert {int(r["流水ID"]) for r in exported} == {anchor, kept, refund}
    assert {r["交易金额"] for r in exported} == {"10.00", "3.00"}
    # B's original ledger remains source evidence even though its only own
    # row was cancelled. Its presence is not an effective monetary fact.
    pending = list(workbook["待调取账号"].values)
    assert len(pending) == 2 and "无待调取账号" in pending[1][1]
    notes = dict(list(workbook["统计口径"].values)[1:])
    assert notes["已匹配冲正撤销事件数"] == 2
    assert notes["保留原始流水中退出分析的笔数"] == 1
    assert "普通退款仍保留" in notes["流水口径"]

    database.x("insert into main_accounts(case_id,account,name) values (?,'B','Synthetic B')", [cid])
    canvas = client.post("/api/canvases/save", json={"case_id": cid, "name": "New effective canvas", "snapshot": {}}).json()["canvas_id"]
    bulk = client.post("/api/track/bulk", json={"case_id": cid, "canvas_id": canvas,
                                              "flow_ids": [anchor], "direction": "target", "x": 5})
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["tracked_ids"] == [anchor]  # Counter-ledger row is not an own-ledger continuation.
    report = client.post("/api/analysis/report", json={"case_id": cid, "params": {"algorithms": ["cycles"]}})
    assert report.status_code == 200
    assert "已匹配的冲正／撤销双方退出有效统计" in report.text
    assert "普通退款仍保留" in report.text and "未作冲正抵销" not in report.text


def test_old_membership_is_cas_stable_but_excluded_ids_cannot_be_added_back(client, database, make_case, flow_row):
    cid, anchor, cancelled, kept, refund = seed(database, make_case, flow_row)
    initial = [anchor, cancelled, kept, refund]
    restore = client.put("/api/track/state", json={"case_id": cid, "expected_ids": initial,
                                                 "desired_ids": initial})
    assert restore.status_code == 200, restore.text
    for route, payload in [("/api/track", {}), ("/api/track/bulk", {"direction": "target"})]:
        response = client.post(route, json={"case_id": cid, "flow_ids": [cancelled], **payload})
        assert response.status_code == 404, response.text
    removed = client.request("DELETE", "/api/track", json={"case_id": cid, "flow_ids": [cancelled]})
    assert removed.status_code == 200, removed.text
    remaining = [anchor, kept, refund]
    failed = client.put("/api/track/state", json={"case_id": cid, "expected_ids": remaining,
                                                "desired_ids": initial})
    assert failed.status_code == 404, failed.text
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert set(graph["tracked_ids"]) == set(remaining)
    assert "CANCELLED-ONLY" not in json.dumps(graph)

    old_snapshot = {"nodes": [{"id": "B", "x": 10, "y": 20}, {"id": "CANCELLED-ONLY", "x": 50, "y": 20}],
                    "edges": [{"source": "B", "target": "CANCELLED-ONLY", "label": "900.00"}]}
    cloned = client.post("/api/canvases/clone", json={"case_id": cid, "name": "Old geometry",
                                                   "from_canvas": 0, "snapshot": old_snapshot})
    assert cloned.status_code == 200, cloned.text
    canvas = cloned.json()["canvas_id"]
    assert client.get(f"/api/canvases/{canvas}", params={"case_id": cid}).json()["snapshot"] == old_snapshot
    restored_graph = client.get("/api/graph", params={"case_id": cid, "canvas_id": canvas}).json()
    assert {fid for edge in restored_graph["edges"] for fid in edge["ids"]} == set(remaining)
    assert "CANCELLED-ONLY" not in json.dumps(restored_graph)


def test_excluded_original_cannot_consume_an_effective_ledger_mirror(client, database, make_case, flow_row):
    cid, _anchor, cancelled, _kept, _refund = seed(database, make_case, flow_row)
    real = insert_flow(database, cid, flow_row, trade_no="REAL-AFTER-CANCELLATION", amount="900.00",
                       trade_time="2026-01-01 10:01:30", payer_account="B",
                       payee_account="CANCELLED-ONLY", self_account="CANCELLED-ONLY")
    response = client.post("/api/track", json={"case_id": cid, "flow_ids": [real]})
    assert response.status_code == 200, response.text
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    edge = next(edge for edge in graph["edges"] if edge["target"] == "CANCELLED-ONLY")
    assert (edge["cnt"], edge["total"], edge["ids"]) == (None, None, [real])
    assert_unresolved_funds(edge["funds"], 1, [("CNY", "900.00")])
    assert cancelled in graph["tracked_ids"]
