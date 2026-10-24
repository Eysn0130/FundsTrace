"""Public main integration preserves original money and mirror provenance."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from test_cases import insert_flow


def seed_tracked_source(client, database, make_case, flow_row, owners, *, seconds=None):
    cid = make_case("Synthetic main mirror integration")
    source_id = f"{cid:032x}"
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,
        mapping_json,sheet,parser_version,row_kind) values (?,?, 'synthetic-main.csv',?,1,'A','{}','',
        'funds-trace-csv-v1','csv_record')""", [source_id, cid, "a" * 64])
    ids = []
    seconds = list(range(len(owners))) if seconds is None else seconds
    assert len(seconds) == len(owners)
    for index, owner in enumerate(owners):
        flow_id = insert_flow(database, cid, flow_row,
                              trade_no=f"SYN-MAIN-{index}",
                              trade_time=datetime(2026, 1, 1, 10) + timedelta(seconds=seconds[index]),
                              payer_account="SYN-A", payee_account="SYN-B", amount=Decimal("100.00"),
                              currency="CNY", self_account=owner)
        database.x("insert into flow_sources(case_id,source_id,source_row,flow_id,disposition) values (?,?,?,?,'imported')",
                   [cid, source_id, index + 1, flow_id])
        ids.append(flow_id)
    tracked = client.post("/api/track", json={"case_id": cid, "flow_ids": list(reversed(ids))})
    assert tracked.status_code == 200, tracked.text
    return cid, source_id, ids


def source_snapshot(database, cid):
    return {table: database.q_raw(f"select * from {table} where case_id=? order by all", [cid])
            for table in ("flows", "flow_sources", "tracked_flows", "import_sources")}


def request_entry(client, cid, endpoint, **extra):
    if endpoint == "graph":
        response = client.get("/api/graph", params={"case_id": cid, **extra})
    else:
        body = {"case_id": cid, **extra}
        if endpoint == "paths":
            body.update(start_account="SYN-A", end_account="SYN-B")
        response = client.post(f"/api/canvas/investigation/{endpoint}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def assert_flow_evidence(flows, source_id, ids, groups):
    expected = [[str(ids[index]) for index in group] for group in groups]
    assert [flow["id"] for flow in flows] == [group[0] for group in expected]
    for flow, original_ids in zip(flows, expected):
        assert flow["amount"] == "100.00"
        assert flow["original_ids"] == original_ids
        assert flow["mirror_ids"] == original_ids[1:]
        assert [entry["flow_id"] for entry in flow["provenance"]] == original_ids
        for entry in flow["provenance"]:
            assert len(entry["sources"]) == 1
            assert entry["sources"][0]["source_id"] == source_id
            assert entry["sources"][0]["source_row"] == str(ids.index(int(entry["flow_id"])) + 1)


@pytest.mark.parametrize("endpoint", ["graph", "time", "paths"])
@pytest.mark.parametrize("owners,count,groups", [
    (["", None], 2, [[0], [1]]),
    (["SYN-C", "SYN-D"], 2, [[0], [1]]),
    (["SYN-A", "SYN-B"], 1, [[0, 1]]),
], ids=["undeclared-owners", "third-party-owners", "established-a-b-mirrors"])
def test_graph_time_and_paths_share_owner_eligibility_without_rewriting_tracked_sources(
        client, database, make_case, flow_row, endpoint, owners, count, groups):
    cid, source_id, ids = seed_tracked_source(client, database, make_case, flow_row, owners)
    before = source_snapshot(database, cid)
    value = request_entry(client, cid, endpoint)
    assert source_snapshot(database, cid) == before
    expected_amount = "200.00" if count == 2 else "100.00"
    if endpoint == "paths":
        assert len(value["paths"]) == 1
        path = value["paths"][0]
        assert path["accounts"] == ["SYN-A", "SYN-B"]
        assert path["hops"] == 1
        assert path["currency"] == "CNY"
        step = path["steps"][0]
        assert (step["count"], step["amount"]) == (count, expected_amount)
        assert step["flow_ids"] == [str(ids[group[0]]) for group in groups]
    else:
        assert len(value["edges"]) == 1
        edge = value["edges"][0]
        assert (edge["source"], edge["target"], edge["cnt"], edge["total"]) == (
            "SYN-A", "SYN-B", count, expected_amount)
        assert edge["amount_totals"] == [{"currency": "CNY", "amount": expected_amount}]
        assert sorted(map(int, edge["ids"])) == ids
        if endpoint == "graph":
            assert sorted(value["tracked_ids"]) == ids
    if endpoint != "graph":
        assert value["summary"]["selected_count"] == count
        assert_flow_evidence(value["flows"], source_id, ids, groups)


@pytest.mark.parametrize("endpoint", ["graph", "time", "paths"])
def test_unproven_rows_cannot_consume_the_established_fifo_in_any_public_entry(
        client, database, make_case, flow_row, endpoint):
    owners = ["SYN-A", "", "SYN-C", "SYN-A", None, "SYN-B", "SYN-B"]
    cid, source_id, ids = seed_tracked_source(
        client, database, make_case, flow_row, owners, seconds=[0, 1, 2, 110, 119, 120, 220])
    before = source_snapshot(database, cid)
    value = request_entry(client, cid, endpoint)
    assert source_snapshot(database, cid) == before
    # Legal FIFO pairs are 0/120 and 110/220. Three unproven rows remain
    # independent and cannot enter, expire, or consume that pending queue.
    groups = [[0, 5], [1], [2], [3, 6], [4]]
    if endpoint == "paths":
        assert len(value["paths"]) == 1
        step = value["paths"][0]["steps"][0]
        assert (step["count"], step["amount"]) == (5, "500.00")
        assert step["flow_ids"] == [str(ids[group[0]]) for group in groups]
    else:
        assert len(value["edges"]) == 1
        edge = value["edges"][0]
        assert (edge["cnt"], edge["total"]) == (5, "500.00")
        assert edge["amount_totals"] == [{"currency": "CNY", "amount": "500.00"}]
        assert sorted(map(int, edge["ids"])) == ids
        if endpoint == "graph":
            assert sorted(value["tracked_ids"]) == ids
    if endpoint != "graph":
        assert value["summary"]["selected_count"] == 5
        assert_flow_evidence(value["flows"], source_id, ids, groups)


@pytest.mark.parametrize("endpoint", ["time", "paths"])
@pytest.mark.parametrize("include_unknown", [False, True], ids=["mirror-alone-in-window", "unknown-and-mirror-in-window"])
def test_window_uses_full_canvas_pairs_and_canonical_time_without_resurrecting_a_mirror(
        client, database, make_case, flow_row, endpoint, include_unknown):
    owners = ["SYN-A", None, "SYN-B"] if include_unknown else ["SYN-A", "SYN-B"]
    seconds = [0, 119, 120] if include_unknown else [0, 120]
    cid, source_id, ids = seed_tracked_source(
        client, database, make_case, flow_row, owners, seconds=seconds)
    before = source_snapshot(database, cid)
    mirror_index = len(ids) - 1
    full = request_entry(client, cid, endpoint)
    full_groups = [[0, mirror_index], [1]] if include_unknown else [[0, mirror_index]]
    assert full["summary"]["selected_count"] == (2 if include_unknown else 1)
    assert_flow_evidence(full["flows"], source_id, ids, full_groups)

    canonical = request_entry(client, cid, endpoint, start="2026-01-01T10:00:00",
                              end="2026-01-01T10:00:00", include_undated=False)
    assert canonical["summary"]["selected_count"] == 1
    # The mirror is outside the window but its source reference belongs to
    # the canonical payment, so neither its ID nor provenance is discarded.
    assert_flow_evidence(canonical["flows"], source_id, ids, [[0, mirror_index]])
    if endpoint == "paths":
        step = canonical["paths"][0]["steps"][0]
        assert (step["count"], step["amount"], step["flow_ids"]) == (1, "100.00", [str(ids[0])])
    else:
        assert (canonical["edges"][0]["cnt"], canonical["edges"][0]["total"]) == (1, "100.00")
        assert canonical["edges"][0]["ids"] == [str(ids[0]), str(ids[mirror_index])]

    late = request_entry(client, cid, endpoint,
                         start="2026-01-01T10:01:59" if include_unknown else "2026-01-01T10:02:00",
                         end="2026-01-01T10:02:00", include_undated=False)
    assert late["summary"]["selected_count"] == (1 if include_unknown else 0)
    assert_flow_evidence(late["flows"], source_id, ids, [[1]] if include_unknown else [])
    if include_unknown:
        if endpoint == "paths":
            step = late["paths"][0]["steps"][0]
            assert (step["count"], step["amount"], step["flow_ids"]) == (1, "100.00", [str(ids[1])])
        else:
            assert (late["edges"][0]["cnt"], late["edges"][0]["total"]) == (1, "100.00")
            assert late["edges"][0]["ids"] == [str(ids[1])]
    elif endpoint == "paths":
        assert late["paths"] == []
    else:
        assert late["edges"] == late["timeline"] == []

    graph = request_entry(client, cid, "graph")
    assert (graph["edges"][0]["cnt"], graph["edges"][0]["total"]) == (
        2 if include_unknown else 1, "200.00" if include_unknown else "100.00")
    assert sorted(graph["tracked_ids"]) == ids
    assert source_snapshot(database, cid) == before
