"""Canvas commands preserve source transactions and independent tracking scopes."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_cases import exported, import_package, insert_flow


def create_canvas(client, case_id, name, snapshot=None):
    response = client.post("/api/canvases/save", json={
        "case_id": case_id, "name": name,
        "snapshot": snapshot if snapshot is not None else {"nodes": [], "edges": []},
    })
    assert response.status_code == 200, response.text
    return response.json()["canvas_id"]


def track(database, case_id, canvas_id, *flow_ids):
    for flow_id in flow_ids:
        database.x("insert into tracked_flows(case_id,flow_id,canvas_id) values (?,?,?)",
                   [case_id, flow_id, canvas_id])


def tracking(database, case_id, canvas_id):
    return [row["flow_id"] for row in database.q(
        "select flow_id from tracked_flows where case_id=? and canvas_id=? order by flow_id",
        [case_id, canvas_id])]


@pytest.mark.parametrize("canvas_id", [0, -1])
@pytest.mark.parametrize("command", ["rename", "delete"])
def test_default_or_invalid_canvas_cannot_be_renamed_or_deleted(
        client, database, make_case, flow_row, canvas_id, command):
    case_id = make_case()
    flow_id = insert_flow(database, case_id, flow_row)
    named = create_canvas(client, case_id, "Synthetic saved canvas")
    track(database, case_id, 0, flow_id)
    track(database, case_id, named, flow_id)

    response = (client.post("/api/canvases/rename", json={
        "case_id": case_id, "canvas_id": canvas_id, "name": "Rejected name",
    }) if command == "rename" else
        client.delete(f"/api/canvases/{canvas_id}?case_id={case_id}"))

    assert response.status_code == 400, response.text
    assert tracking(database, case_id, 0) == [flow_id]
    assert tracking(database, case_id, named) == [flow_id]
    assert client.get(f"/api/canvases/{named}?case_id={case_id}").json()["name"] == "Synthetic saved canvas"
    assert database.q("select id from flows where case_id=?", [case_id]) == [{"id": flow_id}]


def test_rename_changes_only_the_canvas_name(client, database, make_case, flow_row):
    case_id = make_case()
    flow_id = insert_flow(database, case_id, flow_row)
    snapshot = {"nodes": [{"id": "SYN-A", "x": 17, "y": 41}], "edges": [],
                "presentation": {"nodeStyles": {"SYN-A": {"fill": "#367cff"}}}}
    canvas_id = create_canvas(client, case_id, "Before", snapshot)
    other_id = create_canvas(client, case_id, "Other")
    track(database, case_id, canvas_id, flow_id)
    track(database, case_id, other_id, flow_id)

    response = client.post("/api/canvases/rename", json={
        "case_id": case_id, "canvas_id": canvas_id, "name": "  After  ",
    })

    assert response.status_code == 200, response.text
    assert client.get(f"/api/canvases/{canvas_id}?case_id={case_id}").json() == {
        "id": canvas_id, "name": "After", "snapshot": snapshot,
    }
    assert tracking(database, case_id, canvas_id) == [flow_id]
    assert tracking(database, case_id, other_id) == [flow_id]
    assert client.get(f"/api/canvases/{other_id}?case_id={case_id}").json()["name"] == "Other"


@pytest.mark.parametrize("default_source", [False, True])
def test_clone_copies_all_tracking_and_full_snapshot_without_changing_source(
        client, database, make_case, flow_row, default_source):
    case_id = make_case()
    flow_ids = [insert_flow(database, case_id, flow_row, trade_no=f"SYN-{index}")
                for index in range(3)]
    saved_snapshot = {"nodes": [{"id": "SYN-A", "x": 1, "y": 2}], "edges": []}
    source_id = 0 if default_source else create_canvas(client, case_id, "Source", saved_snapshot)
    other_id = create_canvas(client, case_id, "Other", saved_snapshot)
    track(database, case_id, source_id, *flow_ids[:2])
    track(database, case_id, other_id, flow_ids[2])
    full_snapshot = {
        "nodes": [{"id": account, "x": index * 30, "y": index * 50}
                  for index, account in enumerate(["SYN-A", "SYN-B", "SYN-C"])],
        "edges": [{"source": "SYN-A", "target": "SYN-B", "label": "123.45"},
                  {"source": "SYN-B", "target": "SYN-C", "label": "678.90"}],
        "presentation": {"nodeStyles": {"SYN-C": {"fill": "#367cff"}}},
        "display": {"edgeType": "polyline", "showIds": True},
        "view": {"scope": "all", "mode": "neighborhood", "visible_nodes": 2, "total_nodes": 3},
    }

    response = client.post("/api/canvases/clone", json={
        "case_id": case_id, "from_canvas": source_id, "name": "  Copy  ", "snapshot": full_snapshot,
    })

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["copied"] == 2
    assert result["name"] == "Copy"
    assert result["canvas_id"] not in [source_id, other_id]
    assert tracking(database, case_id, result["canvas_id"]) == flow_ids[:2]
    assert tracking(database, case_id, source_id) == flow_ids[:2]
    assert tracking(database, case_id, other_id) == flow_ids[2:]
    assert client.get(f"/api/canvases/{result['canvas_id']}?case_id={case_id}").json()["snapshot"] == full_snapshot
    if source_id:
        assert client.get(f"/api/canvases/{source_id}?case_id={case_id}").json() == {
            "id": source_id, "name": "Source", "snapshot": saved_snapshot,
        }
    assert [row["id"] for row in database.q("select id from flows where case_id=? order by id", [case_id])] == flow_ids


def test_new_canvas_starts_with_an_empty_tracking_set(client, database, make_case, flow_row):
    case_id = make_case()
    flow_id = insert_flow(database, case_id, flow_row)
    source_id = create_canvas(client, case_id, "Source")
    track(database, case_id, source_id, flow_id)
    track(database, case_id, 0, flow_id)

    new_id = create_canvas(client, case_id, "Blank")

    assert tracking(database, case_id, new_id) == []
    assert client.get(f"/api/canvases/{new_id}?case_id={case_id}").json()["snapshot"] == {"nodes": [], "edges": []}
    assert tracking(database, case_id, source_id) == [flow_id]
    assert tracking(database, case_id, 0) == [flow_id]


def test_delete_removes_only_target_canvas_and_its_tracking(client, database, make_case, flow_row):
    case_id = make_case()
    flow_ids = [insert_flow(database, case_id, flow_row, trade_no=f"SYN-{index}")
                for index in range(2)]
    target_id = create_canvas(client, case_id, "Delete target")
    other_snapshot = {"nodes": [{"id": "SYN-B", "x": 23, "y": 57}], "edges": []}
    other_id = create_canvas(client, case_id, "Retained", other_snapshot)
    track(database, case_id, target_id, *flow_ids)
    track(database, case_id, other_id, flow_ids[0])
    track(database, case_id, 0, flow_ids[1])

    response = client.delete(f"/api/canvases/{target_id}?case_id={case_id}")

    assert response.status_code == 200, response.text
    assert client.get(f"/api/canvases/{target_id}?case_id={case_id}").status_code == 404
    assert tracking(database, case_id, target_id) == []
    assert tracking(database, case_id, other_id) == flow_ids[:1]
    assert tracking(database, case_id, 0) == flow_ids[1:]
    assert client.get(f"/api/canvases/{other_id}?case_id={case_id}").json() == {
        "id": other_id, "name": "Retained", "snapshot": other_snapshot,
    }
    assert [row["id"] for row in database.q("select id from flows where case_id=? order by id", [case_id])] == flow_ids


@pytest.mark.parametrize("command", ["rename", "delete", "clone", "save", "load"])
def test_canvas_commands_reject_a_canvas_from_another_case(
        client, database, make_case, flow_row, command):
    first, second = make_case("First"), make_case("Second")
    flow_id = insert_flow(database, second, flow_row)
    snapshot = {"nodes": [{"id": "SYN-A", "x": 19, "y": 31}], "edges": []}
    canvas_id = create_canvas(client, second, "Second canvas", snapshot)
    track(database, second, canvas_id, flow_id)
    body = {"case_id": first, "canvas_id": canvas_id, "name": "Rejected", "snapshot": {}}

    if command == "delete":
        response = client.delete(f"/api/canvases/{canvas_id}?case_id={first}")
    elif command == "load":
        response = client.get(f"/api/canvases/{canvas_id}?case_id={first}")
    else:
        if command == "clone":
            body["from_canvas"] = body.pop("canvas_id")
        response = client.post(f"/api/canvases/{command}", json=body)

    assert response.status_code == 404, response.text
    assert client.get(f"/api/canvases?case_id={first}").json() == []
    assert client.get(f"/api/canvases/{canvas_id}?case_id={second}").json() == {
        "id": canvas_id, "name": "Second canvas", "snapshot": snapshot,
    }
    assert tracking(database, second, canvas_id) == [flow_id]


def cycle_canvas(client, case_id, flow_ids, name="Synthetic loop"):
    return client.post("/api/canvases/from-flows", json={
        "case_id": case_id, "flow_ids": flow_ids, "name": name,
        "snapshot": {"nodes": [], "edges": [], "display": {"layout": "auto"}},
    })


def test_cycle_canvas_is_atomic_and_retries_reuse_exact_membership(
        client, database, make_case, flow_row):
    case = make_case()
    ids = [insert_flow(database, case, flow_row, trade_no=f"SYN-{i}") for i in range(3)]
    ordinary = create_canvas(client, case, "Synthetic loop")
    track(database, case, ordinary, ids[2])
    first = cycle_canvas(client, case, ids[:2])
    assert first.status_code == 200, first.text
    canvas = first.json()["canvas_id"]
    assert canvas != ordinary
    assert first.json()["added"] == 2
    assert tracking(database, case, canvas) == ids[:2]
    assert tracking(database, case, ordinary) == ids[2:]
    assert tracking(database, case, 0) == []
    retry = cycle_canvas(client, case, [ids[1], ids[0], ids[0]])
    assert retry.status_code == 200, retry.text
    assert retry.json()["canvas_id"] == canvas and retry.json()["added"] == 0
    # Identical display names do not identify different sets of evidence.
    second = cycle_canvas(client, case, ids[1:]).json()["canvas_id"]
    assert second not in (canvas, ordinary)
    assert tracking(database, case, second) == ids[1:]
    # A user-modified canvas is left alone on retry, even with the origin marker.
    track(database, case, canvas, ids[2])
    replacement = cycle_canvas(client, case, ids[:2]).json()["canvas_id"]
    assert replacement not in (canvas, ordinary, second)
    assert tracking(database, case, canvas) == ids


def test_cycle_canvas_rejects_foreign_or_missing_flows_without_leaving_a_canvas(
        client, database, make_case, flow_row):
    first, second = make_case("First"), make_case("Second")
    own = insert_flow(database, first, flow_row)
    foreign = insert_flow(database, second, flow_row)
    for ids in ([own, foreign], [own, 2147483647], []):
        response = cycle_canvas(client, first, ids)
        assert response.status_code in (400, 404), response.text
    assert client.get(f"/api/canvases?case_id={first}").json() == []
    assert tracking(database, first, 0) == []


def test_cycle_canvas_rolls_back_when_tracking_write_fails(
        client, database, make_case, flow_row, monkeypatch):
    case = make_case()
    flow = insert_flow(database, case, flow_row)
    write = database.x

    def fail_tracking(sql, params=None):
        if sql.startswith("insert into tracked_flows"):
            raise RuntimeError("synthetic tracking failure")
        return write(sql, params)

    monkeypatch.setattr(database, "x", fail_tracking)
    with pytest.raises(RuntimeError, match="synthetic tracking failure"):
        cycle_canvas(client, case, [flow])
    assert client.get(f"/api/canvases?case_id={case}").json() == []
    assert tracking(database, case, 0) == []


def test_concurrent_cycle_clicks_create_one_complete_canvas(client, database, make_case, flow_row):
    case = make_case()
    flow = insert_flow(database, case, flow_row)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: cycle_canvas(client, case, [flow]), range(8)))
    assert all(response.status_code == 200 for response in responses)
    ids = {response.json()["canvas_id"] for response in responses}
    assert len(ids) == 1
    assert tracking(database, case, ids.pop()) == [flow]
    assert len(client.get(f"/api/canvases?case_id={case}").json()) == 1


def test_cycle_canvas_package_round_trip_reuses_remapped_flow_set(client, database, make_case, flow_row):
    case = make_case()
    ids = [insert_flow(database, case, flow_row, trade_no=f"SYN-{i}") for i in range(2)]
    original = cycle_canvas(client, case, ids).json()["canvas_id"]
    response = import_package(client, exported(client, case))
    assert response.status_code == 200, response.text
    imported_case = response.json()["case_id"]
    imported_canvas = client.get(f"/api/canvases?case_id={imported_case}").json()[0]["id"]
    imported_ids = tracking(database, imported_case, imported_canvas)
    assert set(imported_ids).isdisjoint(ids)
    retry = cycle_canvas(client, imported_case, imported_ids)
    assert retry.status_code == 200, retry.text
    assert retry.json()["canvas_id"] == imported_canvas
    assert tracking(database, case, original) == ids
