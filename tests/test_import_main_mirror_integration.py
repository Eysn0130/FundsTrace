"""Public main integration preserves original money and mirror provenance."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from test_cases import insert_flow
from app.canvas_investigation import mirror_groups
from funds_assertions import unresolved_funds


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
        assert flow["granularity"] == "unresolved_observation"
        assert flow["observation_count"] == 1
        assert flow["original_ids"] == original_ids
        assert flow["mirror_ids"] == original_ids[1:]
        assert [entry["flow_id"] for entry in flow["provenance"]] == original_ids
        for entry in flow["provenance"]:
            assert len(entry["sources"]) == 1
            assert entry["sources"][0]["source_id"] == source_id
            assert entry["sources"][0]["source_row"] == str(ids.index(int(entry["flow_id"])) + 1)


def assert_public_observations(value, endpoint, source_id, ids, indices):
    selected_ids = [ids[index] for index in indices]
    expected = unresolved_funds(len(indices), ("CNY", f"{len(indices) * 100}.00"))
    if endpoint == "paths":
        assert len(value["paths"]) == 1
        path = value["paths"][0]
        assert (path["accounts"], path["hops"], path["currency"]) == (["SYN-A", "SYN-B"], 1, "CNY")
        step = path["steps"][0]
        assert (step["count"], step["amount"]) == (None, None)
        assert step["funds"] == expected
        assert step["flow_ids"] == [str(flow_id) for flow_id in selected_ids]
    else:
        assert len(value["edges"]) == 1
        edge = value["edges"][0]
        assert (edge["source"], edge["target"], edge["cnt"], edge["total"]) == ("SYN-A", "SYN-B", None, None)
        assert edge["funds"] == value["funds"] == expected
        assert edge["amount_totals"] == [{"currency": "CNY", "amount": None}]
        assert sorted(map(int, edge["ids"])) == selected_ids
        if endpoint == "graph":
            assert sorted(value["tracked_ids"]) == selected_ids
    if endpoint != "graph":
        assert value["summary"]["selected_count"] == len(indices)
        assert value["summary"]["funds"] == expected
        assert_flow_evidence(value["flows"], source_id, ids, [[index] for index in indices])


@pytest.mark.parametrize("endpoint", ["graph", "time", "paths"])
@pytest.mark.parametrize("owners,count,groups", [
    (["", None], 2, [[0], [1]]),
    (["SYN-C", "SYN-D"], 2, [[0], [1]]),
    (["SYN-A", "SYN-B"], 1, [[0, 1]]),
], ids=["undeclared-owners", "third-party-owners", "established-a-b-mirror-candidates"])
def test_graph_time_and_paths_keep_mirror_candidates_unresolved_without_rewriting_sources(
        client, database, make_case, flow_row, endpoint, owners, count, groups):
    cid, source_id, ids = seed_tracked_source(client, database, make_case, flow_row, owners)
    before = source_snapshot(database, cid)
    value = request_entry(client, cid, endpoint)
    assert source_snapshot(database, cid) == before
    # Keep the original heuristic's contract independent from evidence-backed funds.
    assert mirror_groups(before["flows"]) == {ids[group[0]]: [ids[index] for index in group[1:]]
                                             for group in groups if len(group) > 1}
    assert len(groups) == count
    assert_public_observations(value, endpoint, source_id, ids, list(range(len(ids))))


@pytest.mark.parametrize("endpoint", ["graph", "time", "paths"])
def test_unproven_rows_cannot_consume_the_established_fifo_in_any_public_entry(
        client, database, make_case, flow_row, endpoint):
    owners = ["SYN-A", "", "SYN-C", "SYN-A", None, "SYN-B", "SYN-B"]
    cid, source_id, ids = seed_tracked_source(
        client, database, make_case, flow_row, owners, seconds=[0, 1, 2, 110, 119, 120, 220])
    before = source_snapshot(database, cid)
    value = request_entry(client, cid, endpoint)
    assert source_snapshot(database, cid) == before
    # FIFO still proposes 0/120 and 110/220 without consuming unknown owners.
    # No supporting event evidence makes those proposed pairs confirmed events.
    assert mirror_groups(before["flows"]) == {ids[0]: [ids[5]], ids[3]: [ids[6]]}
    assert_public_observations(value, endpoint, source_id, ids, list(range(7)))


@pytest.mark.parametrize("endpoint", ["time", "paths"])
@pytest.mark.parametrize("include_unknown", [False, True], ids=["mirror-alone-in-window", "unknown-and-mirror-in-window"])
def test_window_keeps_each_unconfirmed_observation_at_its_original_time(
        client, database, make_case, flow_row, endpoint, include_unknown):
    owners = ["SYN-A", None, "SYN-B"] if include_unknown else ["SYN-A", "SYN-B"]
    seconds = [0, 119, 120] if include_unknown else [0, 120]
    cid, source_id, ids = seed_tracked_source(
        client, database, make_case, flow_row, owners, seconds=seconds)
    before = source_snapshot(database, cid)
    mirror_index = len(ids) - 1
    assert mirror_groups(before["flows"]) == {ids[0]: [ids[mirror_index]]}
    full = request_entry(client, cid, endpoint)
    assert_public_observations(full, endpoint, source_id, ids, list(range(len(ids))))

    early = request_entry(client, cid, endpoint, start="2026-01-01T10:00:00",
                          end="2026-01-01T10:00:00", include_undated=False)
    assert_public_observations(early, endpoint, source_id, ids, [0])
    # An unconfirmed opposite-side observation cannot be suppressed or have its
    # provenance moved into the early window solely by the legacy FIFO rule.
    late = request_entry(client, cid, endpoint,
                         start="2026-01-01T10:01:59" if include_unknown else "2026-01-01T10:02:00",
                         end="2026-01-01T10:02:00", include_undated=False)
    assert_public_observations(late, endpoint, source_id, ids, [1, 2] if include_unknown else [1])
    if endpoint == "time":
        assert len(late["timeline"]) == (2 if include_unknown else 1)
        assert all(point["funds"] == unresolved_funds(1, ("CNY", "100.00")) for point in late["timeline"])

    graph = request_entry(client, cid, "graph")
    assert_public_observations(graph, "graph", source_id, ids, list(range(len(ids))))
    assert source_snapshot(database, cid) == before
