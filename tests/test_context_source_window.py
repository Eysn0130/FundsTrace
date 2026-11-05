"""The existing detail window must use posting evidence before clipping."""
import pytest

from test_principal_book_model import book_row, import_books


@pytest.mark.parametrize("permutation", [(0, 1, 2), (0, 2, 1), (1, 2, 0)])
@pytest.mark.parametrize("side", ["payee", "payer"])
def test_context_window_members_follow_posting_order(client, make_case, permutation, side):
    cid = make_case("Synthetic context posting window")
    if side == "payee":
        rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "ANCHOR"),
                book_row("B", "B", "C", "50.00", "50.00", 0, 2, "NEAREST"),
                book_row("B", "B", "D", "50.00", "0.00", 0, 3, "FARTHER")]
    else:
        rows = [book_row("B", "B", "EXT", "100.00", "0.00", 0, 3, "ANCHOR"),
                book_row("B", "C", "B", "50.00", "100.00", 0, 2, "NEAREST"),
                book_row("B", "D", "B", "50.00", "50.00", 0, 1, "FARTHER")]
    imported = import_books(client, cid, [rows[i] for i in permutation])
    anchor, nearest = imported["SYN-B-ANCHOR"], imported["SYN-B-NEAREST"]
    client.post("/api/main-accounts", json={"case_id": cid, "account": anchor["self_account"]})
    response = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "side": side, "x": 1})
    assert response.status_code == 200
    body = response.json()
    expected = {anchor["id"], nearest["id"]}
    assert {str(row["id"]) for row in body["rows"]} == expected
    kind = "targets" if side == "payee" else "sources"
    assert {str(i) for item in body["suggestions"][kind] for i in item["ids"]} == {nearest["id"]}
    principal = body["principal"]
    if side == "payee":
        assert {str(t["id"]) for t in principal["targets"]} == {nearest["id"]}
    else:
        assert len(principal["source_candidates"]) == 1
        assert principal["source_candidates"][0]["source_flow_id"] == nearest["id"]
    tracked = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
        "direction": "target" if side == "payee" else "source", "x": 1}).json()
    assert {str(i) for i in tracked["tracked_ids"]} == expected


@pytest.mark.parametrize("reverse", [False, True])
def test_unknown_context_boundary_never_selects_an_import_id(client, make_case, reverse):
    cid = make_case("Synthetic unknown context boundary")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, "", "ANCHOR"),
            book_row("B", "B", "C", "50.00", "50.00", 1, "", "ONE"),
            book_row("B", "B", "D", "50.00", "0.00", 1, "", "TWO")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    anchor = imported["SYN-B-ANCHOR"]
    for count, size in [(1, 1), (2, 3)]:
        body = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "x": count}).json()
        assert len(body["rows"]) == size
        assert body["ordering"]["reasons"] == (["neighbor_boundary_order_unknown"] if count == 1 else [])
        if count == 1:
            assert body["suggestions"]["targets"] == []
            assert body["principal"]["targets"] == []


def test_anchor_time_peers_are_bounded_and_not_called_before_or_after(client, make_case):
    cid = make_case("Synthetic anchor tie bound")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, "", "ANCHOR")]
    rows += [book_row("B", "B", f"C{i}", "10.00", "", 0, "", f"OUT{i}") for i in range(3)]
    imported = import_books(client, cid, rows)
    anchor = imported["SYN-B-ANCHOR"]
    small = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "x": 1}).json()
    assert [str(r["id"]) for r in small["rows"]] == [anchor["id"]]
    assert "anchor_tie_window_limit" in small["ordering"]["reasons"]
    assert small["pos"] is None
    full = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "x": 2}).json()
    assert len(full["rows"]) == 4
    assert {r["context_relation"] for r in full["rows"]} == {"anchor", "same_time"}
    assert full["suggestions"] == {"sources": [], "targets": []}


def test_context_opposite_anchor_and_currency_do_not_borrow_posting_order(client, make_case):
    cid = make_case("Synthetic context opposite own ledger")
    rows = [book_row("A", "A", "B", "100.00", "0.00", 0, 99, "IN"),
            book_row("B", "A", "B", "100.00", "100.00", 0, 1, "IN"),
            book_row("B", "B", "C", "50.00", "50.00", 0, 2, "OUT"),
            {**book_row("B", "B", "D", "50.00", "0.00", 0, 3, "USD"), "交易币种": "USD"}]
    imported = import_books(client, cid, rows[::-1])
    anchor = imported["SYN-A-IN"]
    body = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "x": 1}).json()
    relations = {r["trade_no"]: r["context_relation"] for r in body["rows"]}
    assert relations == {"SYN-A-IN": "anchor", "SYN-B-IN": "same_transaction", "SYN-B-OUT": "after", "SYN-B-USD": "same_time"}
    assert {str(t["id"]) for t in body["principal"]["targets"]} == {imported["SYN-B-OUT"]["id"]}
    assert {str(i) for t in body["suggestions"]["targets"] for i in t["ids"]} == {imported["SYN-B-OUT"]["id"]}


def test_grouped_amount_clue_does_not_claim_a_posting_prefix(client, make_case):
    cid = make_case("Synthetic grouped context clue")
    imported = import_books(client, cid, [
        book_row("B", "D", "B", "50.00", "50.00", 0, 1, "EARLY"),
        book_row("B", "EXT", "B", "100.00", "150.00", 0, 2, "ANCHOR"),
        book_row("B", "B", "C", "50.00", "100.00", 0, 3, "NEAREST"),
        book_row("B", "B", "D", "100.00", "0.00", 0, 4, "LATER")])
    anchor = imported["SYN-B-ANCHOR"]
    body = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "x": 5}).json()
    tags = [tag for item in body["suggestions"]["targets"] for tag in item["tags"]]
    assert any("金额组合线索" in tag for tag in tags)
    assert not any(tag.startswith("前 ") for tag in tags)
