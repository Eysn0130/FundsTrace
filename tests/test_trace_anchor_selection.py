"""A frozen relationship's anchor list is read-only, scoped and lossless."""
import pytest

from test_cases import insert_flow
from test_principal_book_model import book_row, import_books


def test_relationship_anchor_pages_keep_exact_ids_and_do_not_track(client, database, make_case, flow_row):
    cid = make_case("Synthetic trace anchor pages")
    ids = [insert_flow(database, cid, flow_row, trade_time=f"2026-01-01 10:00:{i:02d}") for i in range(3)]
    large_id = 9007199254740993
    database.x("update flows set id=? where case_id=? and id=?", [large_id, cid, ids[1]])
    selected = [str(ids[0]), str(large_id)]
    for page in (1, 2):
        response = client.post("/api/flows/by-ids", json={"case_id": cid, "flow_ids": selected,
            "page": page, "size": 1})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2 and body["page"] == page
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert isinstance(row["id"], str) and row["id"] in selected
        assert row["observation_ids"] == [row["id"]]
    assert client.get("/api/graph", params={"case_id": cid}).json()["tracked_ids"] == []


def test_relationship_anchor_read_rejects_cross_case_and_canvas(client, database, make_case, flow_row):
    first, second = make_case("Synthetic first"), make_case("Synthetic second")
    fid = insert_flow(database, first, flow_row)
    other = insert_flow(database, second, flow_row)
    canvas = database.q("insert into canvases(case_id,name,snapshot) values (?,'Synthetic','{}') returning id", [second])[0]["id"]
    for extra in ({"flow_ids": [str(fid), str(other)]}, {"canvas_id": canvas}):
        response = client.post("/api/flows/by-ids", json={"case_id": first, "flow_ids": [str(fid)], **extra})
        assert response.status_code == 404, response.text


@pytest.mark.parametrize("changes", [
    {"flow_ids": []}, {"flow_ids": [1.5]}, {"flow_ids": ["invalid"]},
    {"flow_ids": ["9223372036854775808"]}, {"page": 0}, {"size": 501},
])
def test_relationship_anchor_read_rejects_invalid_scope(client, changes):
    response = client.post("/api/flows/by-ids", json={"case_id": 1, "flow_ids": ["1"], **changes})
    assert response.status_code == 422, response.text


def test_confirmed_event_ids_are_limited_to_the_frozen_relationship_selection(client, make_case):
    cid = make_case("Synthetic confirmed anchor scope")
    imported = import_books(client, cid, [book_row("A", "A", "B", "100.00", "0.00", 1, 1, "AB"),
        book_row("B", "A", "B", "100.00", "100.00", 1, 1, "AB")])
    ids = [imported["SYN-A-AB"]["id"], imported["SYN-B-AB"]["id"]]
    for selected in (ids, ids[:1]):
        response = client.post("/api/flows/by-ids", json={"case_id": cid, "flow_ids": selected})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == 1 and len(data["rows"]) == 1
        assert set(data["rows"][0]["observation_ids"]) == set(selected)
        assert data["rows"][0]["id"] in selected


def test_mixed_relationship_can_read_all_observations_and_trace_only_valid_funds(client, make_case):
    from test_source_ledger import imported

    cid, _, _ = imported(client, make_case)
    page = client.get("/api/flows", params={"case_id": cid}).json()
    assert client.post("/api/track", json={"case_id": cid,
        "flow_ids": [str(row["id"]) for row in page["rows"]]}).status_code == 200
    edge, = client.get("/api/graph", params={"case_id": cid}).json()["edges"]
    assert edge.get("kind") != "source_ledger" and edge["source_ledger"]["count"] == 1
    response = client.post("/api/flows/by-ids", json={"case_id": cid,
        "flow_ids": [str(value) for value in edge["ids"]]})
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    valid = [row for row in rows if not row.get("source_ledger")]
    unavailable = [row for row in rows if row.get("source_ledger")]
    assert len(valid) == len(unavailable) == 1
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": valid[0]["payer_account"]}).status_code == 200
    traced = client.post("/api/track/bulk", json={"case_id": cid,
        "flow_ids": valid[0]["observation_ids"], "direction": "source", "x": 1})
    assert traced.status_code == 200, traced.text
    assert traced.json()["anchors"] == 1
    refused = client.post("/api/track/bulk", json={"case_id": cid,
        "flow_ids": unavailable[0]["observation_ids"], "direction": "source"})
    assert refused.status_code == 422, refused.text
