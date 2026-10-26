"""Atomic legacy tracked-set restoration uses only synthetic in-process state."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import main
from test_cases import insert_flow


def tracked_ids(client, case_id, canvas_id=0):
    response = client.get("/api/graph", params={"case_id": case_id, "canvas_id": canvas_id})
    assert response.status_code == 200, response.text
    return sorted(response.json()["tracked_ids"])


def restore(client, case_id, expected_ids, desired_ids, **extra):
    return client.put("/api/track/state", json={
        "case_id": case_id, "expected_ids": expected_ids, "desired_ids": desired_ids, **extra,
    })


def create_canvas(client, case_id, name="Synthetic canvas"):
    response = client.post("/api/canvases/save", json={"case_id": case_id, "name": name, "snapshot": {}})
    assert response.status_code == 200, response.text
    return response.json()["canvas_id"]


@pytest.mark.parametrize("private_canvas", [False, True])
def test_tracked_state_undo_redo_round_trip_preserves_facts(client, database, make_case, flow_row, private_canvas):
    case_id = make_case()
    ids = [insert_flow(database, case_id, flow_row, trade_no=f"SYN-{i}") for i in range(3)]
    canvas_id = create_canvas(client, case_id) if private_canvas else 0
    other_canvas = create_canvas(client, case_id, "Untouched canvas")
    assert client.post("/api/track", json={"case_id": case_id, "flow_ids": [ids[2]], "canvas_id": other_canvas}).status_code == 200
    before = database.q_raw("select * from flows where case_id=? order by id", [case_id])

    # Duplicate IDs and input ordering are set semantics in both sides of the CAS.
    for expected, desired in [([], [ids[1], ids[0], ids[1]]),
                              ([ids[1], ids[0], ids[0]], [ids[1], ids[2]]),
                              ([ids[2], ids[1]], [ids[0], ids[1]]),
                              ([ids[0], ids[1]], [ids[1], ids[2]]),
                              ([ids[2], ids[1]], [])]:
        extra = {"canvas_id": canvas_id} if private_canvas else {}
        response = restore(client, case_id, expected, desired, **extra)
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True, "tracked_ids": sorted(set(desired))}
        assert tracked_ids(client, case_id, canvas_id) == sorted(set(desired))
        assert tracked_ids(client, case_id, other_canvas) == [ids[2]]
    assert database.q_raw("select * from flows where case_id=? order by id", [case_id]) == before


@pytest.mark.parametrize("bad_reference", ["other_case", "missing"])
def test_invalid_desired_set_does_not_remove_or_partially_add(client, database, make_case, flow_row, bad_reference):
    case_id = make_case()
    first, second = [insert_flow(database, case_id, flow_row) for _ in range(2)]
    other_case = make_case("Other synthetic case")
    foreign = insert_flow(database, other_case, flow_row)
    bad_id = foreign if bad_reference == "other_case" else foreign + 1000
    assert client.post("/api/track", json={"case_id": case_id, "flow_ids": [first]}).status_code == 200
    response = restore(client, case_id, [first], [second, bad_id])
    assert response.status_code == 404, response.text
    assert tracked_ids(client, case_id) == [first]
    assert tracked_ids(client, other_case) == []


@pytest.mark.parametrize("bad_desired", [[1.5], [None], ["not-an-id"]])
def test_invalid_desired_id_type_rejects_entire_request(client, database, make_case, flow_row, bad_desired):
    case_id = make_case()
    flow_id = insert_flow(database, case_id, flow_row)
    assert client.post("/api/track", json={"case_id": case_id, "flow_ids": [flow_id]}).status_code == 200
    response = restore(client, case_id, [flow_id], bad_desired)
    assert response.status_code == 422, response.text
    assert tracked_ids(client, case_id) == [flow_id]


def test_stale_state_conflict_preserves_new_tracking(client, database, make_case, flow_row):
    case_id = make_case()
    first, second = [insert_flow(database, case_id, flow_row) for _ in range(2)]
    assert client.post("/api/track", json={"case_id": case_id, "flow_ids": [first]}).status_code == 200
    assert client.post("/api/track", json={"case_id": case_id, "flow_ids": [second]}).status_code == 200
    response = restore(client, case_id, [first], [])
    assert response.status_code == 409, response.text
    assert tracked_ids(client, case_id) == [first, second]


def test_tracking_state_is_bound_to_case_and_canvas(client, database, make_case, flow_row):
    first_case, second_case = make_case("First"), make_case("Second")
    first, second = [insert_flow(database, first_case, flow_row) for _ in range(2)]
    first_canvas = create_canvas(client, first_case, "First canvas")
    second_canvas = create_canvas(client, first_case, "Second canvas")
    foreign_canvas = create_canvas(client, second_case)
    for canvas_id, ids in [(first_canvas, [first]), (second_canvas, [second])]:
        assert client.post("/api/track", json={"case_id": first_case, "flow_ids": ids, "canvas_id": canvas_id}).status_code == 200

    assert restore(client, first_case, [], [first], canvas_id=foreign_canvas).status_code == 404
    assert restore(client, second_case, [first], [], canvas_id=first_canvas).status_code == 404
    assert restore(client, first_case, [first], [], canvas_id=second_canvas).status_code == 409
    assert tracked_ids(client, first_case, first_canvas) == [first]
    assert tracked_ids(client, first_case, second_canvas) == [second]
    assert tracked_ids(client, first_case) == []
    assert tracked_ids(client, second_case, foreign_canvas) == []


@pytest.mark.parametrize("deleted", ["case", "canvas"])
def test_deleted_binding_cannot_be_restored(client, database, make_case, flow_row, deleted):
    case_id = make_case()
    flow_id = insert_flow(database, case_id, flow_row)
    canvas_id = create_canvas(client, case_id)
    assert restore(client, case_id, [], [flow_id], canvas_id=canvas_id).status_code == 200
    url = f"/api/cases/{case_id}" if deleted == "case" else f"/api/canvases/{canvas_id}?case_id={case_id}"
    assert client.delete(url).status_code == 200
    assert restore(client, case_id, [flow_id], [], canvas_id=canvas_id).status_code == 404
    assert not database.q("select * from tracked_flows where case_id=? and canvas_id=?", [case_id, canvas_id])


def test_insert_failure_rolls_back_removed_tracking(client, database, make_case, flow_row, monkeypatch):
    case_id = make_case()
    first, second = [insert_flow(database, case_id, flow_row) for _ in range(2)]
    assert client.post("/api/track", json={"case_id": case_id, "flow_ids": [first]}).status_code == 200
    original_x = main.db.x

    def fail_insert(sql, params=None):
        if sql.startswith("insert into tracked_flows"):
            assert not database.q("select flow_id from tracked_flows where case_id=?", [case_id])
            raise RuntimeError("synthetic tracking insert failure")
        return original_x(sql, params)

    monkeypatch.setattr(main.db, "x", fail_insert)
    with pytest.raises(RuntimeError, match="synthetic tracking insert failure"):
        restore(client, case_id, [first], [second])
    assert tracked_ids(client, case_id) == [first]


def test_two_restorations_with_same_expected_set_have_one_winner(client, database, make_case, flow_row):
    case_id = make_case()
    first, second = [insert_flow(database, case_id, flow_row) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [pool.submit(restore, client, case_id, [], [flow_id]) for flow_id in (first, second)]
        responses = [request.result() for request in requests]
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json()["tracked_ids"] for response in responses if response.status_code == 200)
    assert tracked_ids(client, case_id) == winner


@pytest.mark.parametrize("private_canvas", [False, True])
@pytest.mark.parametrize("text_ids", [False, True])
def test_bulk_tracking_returns_complete_canvas_set_for_compensation(client, database, make_case, flow_row, private_canvas, text_ids):
    case_id = make_case()
    canvas_id = create_canvas(client, case_id) if private_canvas else 0
    unrelated = insert_flow(database, case_id, flow_row, payer_account="SYN-C", payee_account="SYN-D")
    anchor = insert_flow(database, case_id, flow_row, trade_no="SYN-ANCHOR")
    next_flow = insert_flow(database, case_id, flow_row, trade_no="SYN-NEXT", payer_account="SYN-B",
                            payee_account="SYN-E", self_account="SYN-B", trade_time="2026-01-01 10:05:00")
    excluded = insert_flow(database, case_id, flow_row, trade_no="SYN-OTHER-CANVAS")
    other_canvas = create_canvas(client, case_id, "Other canvas")
    for target_canvas, ids in [(canvas_id, [unrelated]), (other_canvas, [excluded])]:
        assert client.post("/api/track", json={"case_id": case_id, "canvas_id": target_canvas, "flow_ids": ids}).status_code == 200
    database.x("insert into main_accounts(case_id,account,name) values (?,'SYN-B','Synthetic recipient')", [case_id])

    response = client.post("/api/track/bulk", json={"case_id": case_id, "canvas_id": canvas_id,
                                                   "flow_ids": [str(anchor) if text_ids else anchor], "direction": "target", "x": 1})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["tracked_ids"] == [unrelated, anchor, next_flow]
    assert result["ok"] is True and result["tracked"] == 2 and result["anchors"] == 1
    assert result["skipped_not_main"] == []
    assert len(result["detail"]) == 1 and result["detail"][0]["picked"] == 1
    assert tracked_ids(client, case_id, canvas_id) == result["tracked_ids"]
    assert tracked_ids(client, case_id, other_canvas) == [excluded]
    assert restore(client, case_id, result["tracked_ids"], [unrelated], canvas_id=canvas_id).status_code == 200
    assert tracked_ids(client, case_id, canvas_id) == [unrelated]


def test_bulk_tracking_expected_set_rejects_stale_view_without_removing_concurrent_tracking(client, database, make_case, flow_row):
    case_id = make_case()
    original, concurrent, anchor = [insert_flow(database, case_id, flow_row) for _ in range(3)]
    for flow_id in [original, concurrent]:
        assert client.post("/api/track", json={"case_id": case_id, "flow_ids": [flow_id]}).status_code == 200
    payload = {"case_id": case_id, "flow_ids": [anchor], "direction": "target", "expected_ids": [original]}
    response = client.post("/api/track/bulk", json=payload)
    assert response.status_code == 409, response.text
    assert tracked_ids(client, case_id) == [original, concurrent]

    # A refreshed, duplicate-bearing expected set is accepted with set semantics.
    payload["expected_ids"] = [concurrent, original, original]
    response = client.post("/api/track/bulk", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["tracked_ids"] == [original, concurrent, anchor]
    assert tracked_ids(client, case_id) == [original, concurrent, anchor]
