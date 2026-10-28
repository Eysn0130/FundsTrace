"""Public canvas investigation contracts over isolated, synthetic transactions."""
from decimal import Decimal

import pytest

from app import canvas_investigation
from funds_assertions import unresolved_funds


def seed(database, case_id, records, canvas_id=0):
    ids = []
    for index, overrides in enumerate(records):
        row = {"trade_no": f"SYN-{index}", "trade_time": "2026-01-01 10:00:00", "payer_account": "SYN-A",
               "payee_account": "SYN-B", "payer_name": "Synthetic A", "payee_name": "Synthetic B",
               "amount": Decimal("0.10"), "currency": "CNY", "self_account": "SYN-A"}
        row.update(overrides)
        cols, placeholders = ",".join(row), ",".join("?" for _ in row)
        flow_id = database.q(f"insert into flows(case_id,{cols}) values (?,{placeholders}) returning id",
                             [case_id, *row.values()])[0]["id"]
        database.x("insert into tracked_flows(case_id,flow_id,canvas_id) values (?,?,?)", [case_id, flow_id, canvas_id])
        ids.append(flow_id)
    return ids


def request(client, case_id, *, endpoint="time", **kwargs):
    response = client.post(f"/api/canvas/investigation/{endpoint}", json={"case_id": case_id, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def source(database, case_id, flow_id, *, cancelled=False):
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,
                    parser_version,row_kind) values ('syn-source',?,'synthetic.zip',?,1,'standard','{}','',
                    'funds-trace-bank-feedback-v4','archive_record')""", [case_id, "a" * 64])
    database.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,'syn-source',1,?,'imported')",
               [case_id, flow_id])
    database.x("""insert into import_records(case_id,source_id,record_no,member_path,source_row,record_key,disposition,
                    reason_code,details_json) values (?,'syn-source',1,'synthetic.csv',2,?,'imported','','{}')""",
               [case_id, "b" * 64])
    if cancelled:
        database.x("""insert into import_records(case_id,source_id,record_no,member_path,source_row,record_key,disposition,
                    reason_code,details_json) values (?,'syn-source',2,'synthetic.csv',3,?,'cancelled','matched_cancellation',
                    '{"cancellation_role":"original","cancellation_policy":"paired-cancellation-v1"}')""",
                   [case_id, "b" * 64])


def test_time_projection_reaggregates_rows_not_edge_ranges_and_preserves_money(client, database, make_case):
    case_id = make_case()
    ids = seed(database, case_id, [
        {"amount": Decimal("9999999999999999.99")},
        {"trade_time": "2026-01-02 10:00:00", "amount": Decimal("0.01")},
        {"trade_time": "2026-01-03 10:00:00", "amount": Decimal("0.20"), "currency": "USD"},
        {"trade_time": None, "amount": Decimal("0.30")},
    ])
    full = request(client, case_id)
    assert full["edges"][0]["funds"] == unresolved_funds(4, ("CNY", "10000000000000000.30"), ("USD", "0.20"))
    assert full["edges"][0]["total"] is None
    result = request(client, case_id, start="2026-01-02T10:00:00", end="2026-01-02T10:00:00", include_undated=False)
    assert result["edges"][0]["total"] is None
    assert result["edges"][0]["funds"] == unresolved_funds(1, ("CNY", "0.01"))
    assert result["edges"][0]["ids"] == [str(ids[1])]
    assert result["summary"]["selected_count"] == 1
    assert result["summary"]["excluded_undated_count"] == 1
    assert result["summary"]["time_min"] == "2026-01-01 10:00:00"
    assert result["timeline"] == [{"time": "2026-01-02 10:00:00", "cnt": None, "total": None,
        "funds": unresolved_funds(1, ("CNY", "0.01")), "amount_totals": [{"currency": "CNY", "amount": None}]}]
    assert database.q("select count(*) n from tracked_flows where case_id=?", [case_id])[0]["n"] == 4


def test_unproven_mirrors_match_graph_bounds_and_keep_original_source_links(client, database, make_case):
    case_id = make_case()
    ids = seed(database, case_id, [
        {}, {"self_account": "SYN-B", "trade_time": "2026-01-01 10:00:01"},
        {}, {"self_account": "SYN-B", "trade_time": "2026-01-01 10:00:02"},
        {"trade_time": None}, {"trade_time": None, "self_account": "SYN-B"},
    ])
    source(database, case_id, ids[0])
    before = database.q_raw("select * from flows order by id")
    result = request(client, case_id)
    legacy = client.get("/api/graph", params={"case_id": case_id}).json()
    assert result["edges"][0]["cnt"] is legacy["edges"][0]["cnt"] is None
    assert result["edges"][0]["funds"] == legacy["edges"][0]["funds"] == unresolved_funds(6, ("CNY", "0.60"))
    assert result["edges"][0]["amount_totals"] == legacy["edges"][0]["amount_totals"]
    assert sorted(result["edges"][0]["ids"]) == sorted(map(str, ids))
    canonical = result["flows"][0]
    assert canonical["mirror_ids"] == []
    assert len(result["flows"]) == 6
    assert canonical["provenance"][0]["sources"][0]["source_row"] == "1"
    assert canonical["provenance"][0]["sources"][0]["member_row"] == "2"
    assert canonical["provenance"][0]["sources"][0]["filename"] == "synthetic.zip"
    # Without confirmed event evidence, each observation retains its own date.
    later = request(client, case_id, start="2026-01-01T10:00:01", include_undated=False)
    assert [row["id"] for row in later["flows"]] == [str(ids[1]), str(ids[3])]
    assert later["funds"] == unresolved_funds(2, ("CNY", "0.20"))
    assert database.q_raw("select * from flows order by id") == before


def test_cancellation_and_non_graphable_rows_do_not_enter_investigation(client, database, make_case):
    case_id = make_case()
    ids = seed(database, case_id, [{}, {"payer_account": ""}, {"amount": None}])
    source(database, case_id, ids[0], cancelled=True)
    result = request(client, case_id)
    assert len(result["edges"]) == len(result["flows"]) == 1
    assert result["flows"][0]["id"] == str(ids[2]) and result["flows"][0]["amount"] is None
    assert result["funds"] == unresolved_funds(1, ("CNY", None))
    assert result["summary"]["tracked_effective_count"] == 2
    assert result["summary"]["excluded_non_graphable_count"] == 1


@pytest.mark.parametrize("endpoint", ["time", "paths"])
def test_binding_visible_scope_and_invalid_inputs(client, database, make_case, endpoint):
    case_id, other_case = make_case(), make_case("Other synthetic case")
    canvas_id = database.q("insert into canvases(case_id,name,snapshot) values (?,'other','{}') returning id", [other_case])[0]["id"]
    extra = {"start_account": "SYN-A", "end_account": "SYN-B"} if endpoint == "paths" else {}
    url = f"/api/canvas/investigation/{endpoint}"
    assert client.post(url, json={"case_id": case_id, "canvas_id": canvas_id, **extra}).status_code == 404
    assert client.post(url, json={"case_id": 99999, **extra}).status_code == 404
    assert client.post(url, json={"case_id": case_id, "canvas_id": -1, **extra}).status_code == 422
    assert client.post(url, json={"case_id": case_id, "scope": "visible", **extra}).status_code == 422
    assert client.post(url, json={"case_id": case_id, "start": "2026-01-01T00:00:00Z", **extra}).status_code == 422
    assert client.post(url, json={"case_id": case_id, "start": 0, **extra}).status_code == 422
    assert client.post(url, json={"case_id": case_id, "start": "2026-02-01", "end": "2026-01-01", **extra}).status_code == 422


def test_visible_scope_uses_explicit_directed_edges_and_canvas_membership(client, database, make_case):
    case_id = make_case()
    canvas_id = database.q("insert into canvases(case_id,name,snapshot) values (?,'synthetic','{}') returning id", [case_id])[0]["id"]
    seed(database, case_id, [{}, {"payee_account": "SYN-C"}, {"payer_account": "SYN-B", "payee_account": "SYN-C"}])
    seed(database, case_id, [{"payee_account": "SYN-X"}], canvas_id)
    result = request(client, case_id, scope="visible", visible_accounts=["SYN-A", "SYN-B", "SYN-C"],
                     visible_edges=[{"source": "SYN-A", "target": "SYN-B"}])
    assert [(edge["source"], edge["target"]) for edge in result["edges"]] == [("SYN-A", "SYN-B")]
    separate = request(client, case_id, canvas_id=canvas_id)
    assert [edge["target"] for edge in separate["edges"]] == ["SYN-X"]
    empty = request(client, case_id, scope="visible", visible_accounts=[], visible_edges=[])
    assert empty["flows"] == []


def test_paths_distinguish_topology_strict_time_and_currency(client, database, make_case):
    case_id = make_case()
    ids = seed(database, case_id, [
        {}, {"payer_account": "SYN-B", "payee_account": "SYN-C"},
        {"payer_account": "SYN-B", "payee_account": "SYN-C", "trade_time": "2026-01-01 10:00:01", "amount": Decimal("0.03")},
        {"payer_account": "SYN-C", "payee_account": "SYN-D", "currency": "USD", "trade_time": "2026-01-01 10:00:02"},
        {"payer_account": "SYN-C", "payee_account": "SYN-E", "trade_time": None},
    ])
    body = {"endpoint": "paths", "start_account": "SYN-A", "end_account": "SYN-C"}
    topology = request(client, case_id, **body)
    assert topology["complete"] is True
    assert topology["paths"][0]["accounts"] == ["SYN-A", "SYN-B", "SYN-C"]
    assert topology["paths"][0]["steps"][1]["count"] is None
    assert topology["paths"][0]["steps"][1]["amount"] is None
    assert topology["paths"][0]["steps"][1]["funds"] == unresolved_funds(2, ("CNY", "0.13"))
    chronological = request(client, case_id, mode="chronological", **body)
    assert len(chronological["paths"]) == 1
    assert chronological["paths"][0]["steps"][1]["flow_ids"] == [str(ids[2])]
    assert "relationship_amounts_are_not_principal" in chronological["semantics"]
    assert "separate_recorded_book_first_exit_attribution" in chronological["semantics"]
    assert request(client, case_id, endpoint="paths", start_account="SYN-A", end_account="SYN-D")["paths"] == []
    assert request(client, case_id, endpoint="paths", mode="chronological", start_account="SYN-A", end_account="SYN-E")["paths"] == []


def test_disconnected_hop_bounds_and_truncation_are_explicit(client, database, make_case, monkeypatch):
    case_id = make_case()
    seed(database, case_id, [{"payee_account": middle} for middle in ["SYN-B", "SYN-C"]] +
         [{"payer_account": middle, "payee_account": "SYN-D"} for middle in ["SYN-B", "SYN-C"]])
    args = {"endpoint": "paths", "start_account": "SYN-A", "end_account": "SYN-D"}
    bounded = request(client, case_id, max_hops=1, **args)
    assert bounded["paths"] == [] and bounded["complete"] is True
    result = request(client, case_id, max_paths=1, **args)
    assert len(result["paths"]) == 1
    assert result["complete"] is False and result["truncation_reason"] == "result_limit"
    disconnected = request(client, case_id, endpoint="paths", start_account="SYN-X", end_account="SYN-D")
    assert disconnected["complete"] is True and disconnected["paths"] == []
    monkeypatch.setattr(canvas_investigation, "MAX_SEARCH_STEPS", 1)
    limited = request(client, case_id, **args)
    assert limited["complete"] is False and limited["truncation_reason"] == "search_step_limit"


def test_resource_limits_fail_closed_without_partial_amounts(client, database, make_case, monkeypatch):
    case_id = make_case()
    seed(database, case_id, [{}, {}])
    monkeypatch.setattr(canvas_investigation, "MAX_FLOWS", 1)
    response = client.post("/api/canvas/investigation/time", json={"case_id": case_id})
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "canvas_investigation_limit"


def test_chronology_excludes_unknown_currency_and_nonpositive_movements(client, database, make_case):
    case_id = make_case()
    seed(database, case_id, [
        {"currency": ""}, {"payer_account": "SYN-B", "payee_account": "SYN-C", "currency": "", "trade_time": "2026-01-01 10:00:01"},
        {"payee_account": "SYN-D", "amount": Decimal("0.00")},
        {"payer_account": "SYN-D", "payee_account": "SYN-C", "trade_time": "2026-01-01 10:00:01"},
    ])
    result = request(client, case_id, endpoint="paths", mode="chronological", start_account="SYN-A", end_account="SYN-C")
    assert result["paths"] == []
    assert result["summary"]["excluded_unknown_currency_for_chronology_count"] == 2
    assert result["summary"]["excluded_nonpositive_for_chronology_count"] == 1


def test_export_includes_time_window_metadata_and_display_decorators(client):
    response = client.post("/api/export/graph.html", json={"nodes": [], "edges": [], "view": {
        "scope": "visible", "mode": "filtered", "total_nodes": 1, "total_edges": 1,
        "visible_nodes": 0, "visible_edges": 0, "label_mode": "all",
        "time_window": {"start": "2026-01-01 10:00:00", "end": "2026-01-02 10:00:00", "include_undated": False, "preview": True},
    }})
    assert response.status_code == 200, response.text
    assert "按窗口内实际流水重算金额" in response.text
    assert "排除无时间流水" in response.text
    assert "时间回放预览帧" in response.text
    assert "presentation.decorateNode(item,m.displayStyle" in response.text
    assert "presentation.decorateLabel(item,m.labelStyle)" in response.text


def test_case_package_round_trip_preserves_display_choices_and_full_tracked_facts(client, database, make_case):
    case_id = make_case()
    ids = seed(database, case_id, [
        {"trade_time": "2026-01-01 10:00:00.123456", "amount": Decimal("0.10")},
        {"trade_time": "2026-01-02 10:00:00", "amount": Decimal("0.20")},
    ])
    presentation = {
        "version": 1,
        "timeWindow": {"start": "2026-01-01 10:00:00.123456", "end": "2026-01-01 10:00:00.123456", "includeUndated": False},
        "groups": [{"id": "synthetic-group", "name": "待核实关系", "color": "#7f8b99", "members": ["SYN-A", "SYN-B"], "notes": "人工展示分组"}],
        "pinnedAccounts": ["SYN-A"], "nodeStyles": {"SYN-A": {"shape": "diamond", "icon": "person"}},
        "nodeLabels": {"SYN-A": {"underline": True}},
    }
    snapshot = {
        "nodes": [{"id": "SYN-A", "x": 12.5, "y": 42}, {"id": "SYN-B", "x": 300, "y": 42}],
        "edges": [{"source": "SYN-A", "target": "SYN-B", "data": {
            "ids": list(map(str, ids)), "cnt": 2, "amount_totals": [{"currency": "CNY", "amount": "0.30"}],
        }}],
        "presentation": presentation,
    }
    saved = client.post("/api/canvases/save", json={"case_id": case_id, "name": "Synthetic investigation", "snapshot": snapshot})
    assert saved.status_code == 200, saved.text
    canvas_id = saved.json()["canvas_id"]
    tracked = client.post("/api/track", json={"case_id": case_id, "canvas_id": canvas_id, "flow_ids": ids})
    assert tracked.status_code == 200, tracked.text
    exported = client.get(f"/api/cases/{case_id}/export")
    assert exported.status_code == 200, exported.text
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content, "application/octet-stream")})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    restored_canvas = client.get("/api/canvases", params={"case_id": restored_case}).json()[0]["id"]
    loaded = client.get(f"/api/canvases/{restored_canvas}", params={"case_id": restored_case}).json()["snapshot"]
    assert loaded["presentation"] == presentation
    assert loaded["nodes"] == snapshot["nodes"]
    imported_ids = loaded["edges"][0]["data"]["ids"]
    assert all(isinstance(flow_id, str) for flow_id in imported_ids)
    assert set(imported_ids).isdisjoint(map(str, ids))
    full = request(client, restored_case, canvas_id=restored_canvas)
    assert full["summary"]["selected_count"] == 2
    assert full["edges"][0]["funds"] == unresolved_funds(2, ("CNY", "0.30"))
    assert full["edges"][0]["total"] is None
    window = loaded["presentation"]["timeWindow"]
    projected = request(client, restored_case, canvas_id=restored_canvas, start=window["start"], end=window["end"],
                        include_undated=window["includeUndated"])
    assert projected["edges"][0]["total"] is None
    assert projected["edges"][0]["funds"] == unresolved_funds(1, ("CNY", "0.10"))
    assert projected["flows"][0]["trade_time"] == "2026-01-01 10:00:00.123456"
    assert projected["flows"][0]["id"] == imported_ids[0]
