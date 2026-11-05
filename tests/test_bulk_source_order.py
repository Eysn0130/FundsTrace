"""Existing tracking actions must not turn import order into money chronology."""
import pytest

from test_principal_book_model import book_row, import_books
from test_event_import import csv_rows
from test_folder_evidence import accepted


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direction", ["source", "target"])
def test_bulk_respects_explicit_order_independently_of_file_rows(client, make_case, reverse, direction):
    cid = make_case("Synthetic posting order")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 2, "LATER-IN"),
            book_row("B", "B", "C", "100.00", "0.00", 0, 1, "EARLIER-OUT")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    incoming, outgoing = imported["SYN-B-LATER-IN"], imported["SYN-B-EARLIER-OUT"]
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": incoming["self_account"]}).status_code == 200
    anchor = incoming if direction == "target" else outgoing
    result = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
                                                "direction": direction, "x": 5})
    assert result.status_code == 200, result.text
    assert result.json()["detail"][0]["picked"] == 0
    assert result.json()["tracked_ids"] == [int(anchor["id"])]
    context = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid,
        "side": "payer" if direction == "source" else "payee"}).json()
    other = next(row for row in context["rows"] if str(row["id"]) != anchor["id"])
    assert other["context_relation"] == ("after" if direction == "source" else "before")


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("direction", ["source", "target"])
def test_bulk_includes_proven_same_time_neighbor(client, make_case, reverse, direction):
    cid = make_case("Synthetic ordered neighbor")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "IN"),
            book_row("B", "B", "C", "100.00", "0.00", 0, 2, "OUT")]
    imported = import_books(client, cid, rows[::-1] if reverse else rows)
    incoming, outgoing = imported["SYN-B-IN"], imported["SYN-B-OUT"]
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": incoming["self_account"]}).status_code == 200
    anchor = incoming if direction == "target" else outgoing
    result = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
                                                "direction": direction, "x": 5}).json()
    assert result["detail"][0]["picked"] == 1
    assert set(result["tracked_ids"]) == {int(incoming["id"]), int(outgoing["id"])}


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("direction", ["source", "target"])
def test_bulk_cannot_compare_missing_or_unrelated_source_orders(client, make_case, split, direction):
    cid = make_case("Synthetic unordered same time")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1 if split else "", "IN"),
            book_row("B", "B", "C", "100.00", "0.00", 0, 2 if split else "", "OUT")]
    if split:
        accepted(client, cid, [(f"synthetic-{i}.csv", csv_rows([row])) for i, row in enumerate(rows)], scope_bank=None)
        imported = {row["trade_no"]: row for row in client.get(f"/api/v1/cases/{cid}/flows",
                    params={"granularity": "observations"}).json()["items"]}
    else:
        imported = import_books(client, cid, rows)
    anchor = imported["SYN-B-IN" if direction == "target" else "SYN-B-OUT"]
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": anchor["self_account"]}).status_code == 200
    result = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
                                                "direction": direction}).json()
    assert result["detail"][0]["picked"] == 0
    assert result["detail"][0]["ordering"]["reasons"] == ["posting_order_unknown"]


def test_bulk_counter_observation_uses_only_confirmed_own_order_and_same_currency(client, make_case):
    cid = make_case("Synthetic opposite observation")
    rows = [book_row("A", "A", "B", "100.00", "0.00", 0, 99, "IN"),
            book_row("B", "A", "B", "100.00", "100.00", 0, 1, "IN"),
            book_row("B", "B", "C", "100.00", "0.00", 0, 2, "OUT"),
            {**book_row("B", "B", "D", "100.00", "0.00", 1, 3, "USD"), "交易币种": "USD"}]
    imported = import_books(client, cid, rows)
    anchor, own, outgoing = imported["SYN-A-IN"], imported["SYN-B-IN"], imported["SYN-B-OUT"]
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": own["self_account"]}).status_code == 200
    result = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
                                                "direction": "target"}).json()
    assert result["detail"][0]["picked"] == 1
    assert set(result["tracked_ids"]) == {int(anchor["id"]), int(outgoing["id"])}


def test_bulk_does_not_split_an_unordered_nearest_boundary_by_import_id(client, make_case):
    cid = make_case("Synthetic boundary tie")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, "", "IN"),
            book_row("B", "B", "C", "50.00", "", 1, "", "OUT1"),
            book_row("B", "B", "D", "50.00", "", 1, "", "OUT2")]
    imported = import_books(client, cid, rows)
    anchor = imported["SYN-B-IN"]
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": anchor["self_account"]}).status_code == 200
    for count, expected in [(1, 0), (2, 2)]:
        client.delete("/api/graph", params={"case_id": cid})
        result = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
                                                    "direction": "target", "x": count}).json()
        assert result["detail"][0]["picked"] == expected
        assert result["detail"][0]["ordering"]["reasons"] == (["neighbor_boundary_order_unknown"] if count == 1 else [])


@pytest.mark.parametrize("direction", ["source", "target"])
def test_original_bulk_action_calculates_conditional_share_with_untracked_competitor(client, make_case, direction):
    cid = make_case("Synthetic original tracking allocation")
    rows = [book_row("B", "EXT", "B", "100.00", "140.00", 0, 1, "IN"),
            book_row("B", "B", "D", "50.00", "90.00", 1, 2, "COMPETITOR"),
            book_row("B", "B", "C", "90.00", "0.00", 2, 3, "OUT")]
    imported = import_books(client, cid, rows)
    incoming, outgoing = imported["SYN-B-IN"], imported["SYN-B-OUT"]
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": incoming["self_account"]}).status_code == 200
    anchor = incoming if direction == "target" else outgoing
    response = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
                                                  "direction": direction, "x": 5})
    assert response.status_code == 200, response.text
    value = response.json()["detail"][0]["principal"]
    assert value["revision"]
    if direction == "source":
        assert value["candidate_semantics"] == "independent_conditional_source_shares"
        value = value["source_candidates"][0]
        assert imported["SYN-B-COMPETITOR"]["id"] not in {str(key) for key in response.json()["tracked_ids"]}
    assert value["status"] == "ok" and value["ledger_count"] == 3
    target = next(row for row in value["targets"] if str(row["id"]) == outgoing["id"])
    assert (target["min"], target["max"], target["fifo"]) == ("50.00", "90.00", "90.00")
    context = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid,
                         "side": "payee" if direction == "target" else "payer"}).json()["principal"]
    if direction == "source":
        context = context["source_candidates"][0]
    assert context["targets"] == value["targets"]


def test_context_source_candidates_stay_inside_displayed_window(client, make_case):
    cid = make_case('Synthetic context bounded candidates')
    imported = import_books(client, cid, [
        book_row('B', 'EXT', 'B', '100.00', '100.00', 0, 1, 'IN'),
        book_row('B', 'B', 'C', '50.00', '50.00', 1, 2, 'OUT1'),
        book_row('B', 'B', 'D', '50.00', '0.00', 2, 3, 'OUT2')])
    anchor = imported['SYN-B-OUT2']
    result = client.get(f"/api/context/{anchor['id']}", params={'case_id': cid, 'side': 'payer', 'x': 1})
    assert result.status_code == 200
    body = result.json()
    assert str(imported['SYN-B-IN']['id']) not in {str(row['id']) for row in body['rows']}
    assert not body['principal'].get('source_candidates')


def test_context_and_bulk_preserve_fractional_second_chronology(client, make_case):
    cid = make_case('Synthetic fractional seconds')
    inputs = [book_row('B', 'EXT', 'B', '100.00', '100.00', 0, '', 'IN'),
              book_row('B', 'B', 'C', '50.00', '50.00', 0, '', 'OUT1'),
              book_row('B', 'B', 'D', '50.00', '0.00', 0, '', 'OUT2')]
    for row, fraction in zip(inputs, ('100000', '200000', '300000')):
        row['交易时间'] = '2026-01-01 10:00:00.' + fraction
    imported = import_books(client, cid, inputs[::-1])
    anchor = imported['SYN-B-OUT2']
    result = client.get(f"/api/context/{anchor['id']}", params={'case_id': cid, 'side': 'payer', 'x': 1}).json()
    assert result['pos'] == 3
    assert {str(row['id']) for row in result['rows']} == {imported['SYN-B-OUT1']['id'], anchor['id']}
    assert next(row for row in result['rows'] if str(row['id']) != anchor['id'])['context_relation'] == 'before'
    wider = client.get(f"/api/context/{anchor['id']}", params={'case_id': cid, 'side': 'payer', 'x': 3}).json()
    assert {str(i) for item in wider['suggestions']['sources'] for i in item['ids']} == {imported['SYN-B-IN']['id']}
    assert client.post('/api/main-accounts', json={'case_id': cid, 'account': anchor['self_account']}).status_code == 200
    tracked = client.post('/api/track/bulk', json={'case_id': cid, 'flow_ids': [int(anchor['id'])], 'direction': 'source'}).json()
    assert tracked['detail'][0]['picked'] == 1
    assert set(tracked['tracked_ids']) == {int(anchor['id']), int(imported['SYN-B-IN']['id'])}
