"""Reuse own-entry evidence only within one locked public context request."""
import pytest

from app import ledger_navigation, principal_evidence
from test_event_graph import imported as import_events, pair
from test_event_import import csv_rows
from test_folder_evidence import accepted, assert_unresolved_funds
from test_principal_book_model import book_row, import_books


@pytest.fixture
def book(client, make_case):
    cid = make_case("Synthetic request-local own entry")
    rows = [book_row("B", "EXT", "B", "100.01", "140.01", 0, 1, "FIRST"),
            book_row("B", "EXT2", "B", "20.02", "160.03", 1, 2, "SECOND"),
            {**book_row("B", "USD", "B", "7.13", "7.13", 2, 1, "USD"), "交易币种": "USD"},
            book_row("B", "B", "D", "50.03", "110.00", 3, 3, "COMPETITOR"),
            book_row("B", "B", "C", "110.00", "0.00", 4, 4, "TARGET")]
    # Physical row order does not establish the public chronological order.
    return cid, import_books(client, cid, rows[::-1])


@pytest.fixture
def own_calls(monkeypatch):
    original = ledger_navigation.own_anchor
    calls = []

    def read(case_id, account, anchor):
        own = original(case_id, account, anchor)
        calls.append((case_id, account, str(anchor["id"]), str(own["id"]) if own else None))
        return own

    monkeypatch.setattr(ledger_navigation, "own_anchor", read)
    return calls


def context(client, cid, anchor, side, x=5):
    response = client.get(f"/api/context/{anchor['id']}", params={"case_id": cid, "side": side, "x": x})
    assert response.status_code == 200, response.text
    return response.json()


def assert_source_model(value, source, target, bounds):
    assert value["source_flow_id"] == source["id"]
    assert value["status"] == "ok" and value["reasons"] == []
    assert value["currency"] == "CNY" and value["ledger_count"] == 4
    assert value["conditions"] == {"coverage": "unknown", "no_overdraft": False,
        "funds_available": False, "opening_balance": "40.00", "balance_kind": "post_book"}
    assert value["condition_basis"] == "source_evidence"
    assert value["model_scope"]["entries"] == "all_recorded_own_entries_in_requested_interval"
    assert value["model_scope"]["boundary"] == "first_exit"
    assert value["model_scope"]["real_world_attribution"] == "unknown"
    assert value["interval"] == {"start": "2026-01-01T10:00:00", "end": "2026-01-01T10:04:00"}
    assert value["closing_balance"] == "0.00"
    assert len(value["targets"]) == 1
    result = value["targets"][0]
    assert result["id"] == target["id"] and result["amount"] == "110.00"
    assert tuple(result[key] for key in ("min", "max", "fifo")) == bounds
    assert value["amount_semantics"]["fifo"] == "rule_allocation_not_proof"


@pytest.mark.parametrize("missing_revision", [False, True])
def test_payer_context_reuses_own_entry_and_preserves_complete_book(client, database, book, own_calls, missing_revision):
    cid, rows = book
    anchor = rows["SYN-B-TARGET"]
    account = anchor["payer_account"]
    if missing_revision:
        with database.transaction():
            database.x("delete from analysis_state where case_id=?", [cid])
    assert database.q_raw("select count(*) n from tracked_flows where case_id=?", [cid])[0]["n"] == 0
    value = context(client, cid, anchor, "payer")
    assert set(value) == {"anchor", "side", "account", "rows", "total", "pos", "ordering",
                         "analysis_revision", "display_account", "suggestions", "funds", "principal"}
    assert (value["side"], value["account"], value["display_account"]) == ("payer", account, "B")
    assert str(value["anchor"]["id"]) == anchor["id"]
    assert value["anchor"]["amount"] == "110.00" and value["anchor"]["balance"] == "0.00"
    assert (value["total"], value["pos"]) == (5, 5)
    assert value["ordering"] == {"status": "ok", "reasons": []}
    assert [row["trade_no"] for row in value["rows"]] == [
        "SYN-B-FIRST", "SYN-B-SECOND", "SYN-B-USD", "SYN-B-COMPETITOR", "SYN-B-TARGET"]
    assert [row["amount"] for row in value["rows"]] == ["100.01", "20.02", "7.13", "50.03", "110.00"]
    assert [row["balance"] for row in value["rows"]] == ["140.01", "160.03", "7.13", "110.00", "0.00"]
    assert [row["currency"] for row in value["rows"]] == ["CNY", "CNY", "USD", "CNY", "CNY"]
    assert [row["dir"] for row in value["rows"]] == ["in", "in", "in", "out", "out"]
    assert [row["context_relation"] for row in value["rows"]] == ["before"] * 4 + ["anchor"]
    assert all(row["book"] == "self" and row["event_state"] == "unknown" and row["event_id"] is None
               and row["context_group"] == "record:" + str(row["id"]) for row in value["rows"])
    assert_unresolved_funds(value["funds"], 5, [("CNY", "280.06"), ("USD", "7.13")])
    assert {str(key) for item in value["suggestions"]["sources"] for key in item["ids"]} == {
        rows["SYN-B-FIRST"]["id"], rows["SYN-B-SECOND"]["id"]}
    assert value["suggestions"]["targets"] == []
    principal = value["principal"]
    assert principal["status"] == "ok" and principal["revision"] == value["analysis_revision"]
    assert principal["candidate_semantics"] == "independent_conditional_source_shares"
    assert principal["targets"] == [] and principal["reasons"] == []
    assert [item["source_flow_id"] for item in principal["source_candidates"]] == [
        rows["SYN-B-SECOND"]["id"], rows["SYN-B-FIRST"]["id"]]
    # Opening 40.00 and both credits compete with an untracked 50.03 debit.
    assert_source_model(principal["source_candidates"][0], rows["SYN-B-SECOND"], anchor,
                        ("0.00", "20.02", "20.02"))
    assert_source_model(principal["source_candidates"][1], rows["SYN-B-FIRST"], anchor,
                        ("49.98", "100.01", "89.98"))
    assert own_calls == [(cid, account, anchor["id"], anchor["id"])]


@pytest.mark.parametrize("direction", ["source", "target"])
def test_payee_context_and_default_bulk_keep_independent_lookups(client, book, own_calls, direction):
    cid, rows = book
    anchor = rows["SYN-B-TARGET" if direction == "source" else "SYN-B-FIRST"]
    side = "payer" if direction == "source" else "payee"
    account = anchor[side + "_account"]
    value = context(client, cid, anchor, side)
    assert own_calls == [(cid, account, anchor["id"], anchor["id"])]
    if direction == "target":
        assert value["principal"]["ledger_count"] == 4
        target = value["principal"]["targets"][-1]
        assert tuple(target[key] for key in ("id", "min", "max", "fifo")) == (
            rows["SYN-B-TARGET"]["id"], "49.98", "100.01", "89.98")
    assert client.post("/api/main-accounts", json={"case_id": cid, "account": account}).status_code == 200
    own_calls.clear()
    response = client.post("/api/track/bulk", json={"case_id": cid, "flow_ids": [int(anchor["id"])],
                                                  "direction": direction, "x": 5})
    assert response.status_code == 200, response.text
    result = response.json()
    picked = [rows["SYN-B-SECOND"]["id"], rows["SYN-B-FIRST"]["id"]] if direction == "source" else [
        rows["SYN-B-COMPETITOR"]["id"], rows["SYN-B-TARGET"]["id"]]
    assert result["detail"][0]["picked_ids"] == picked
    assert {str(key) for key in result["tracked_ids"]} == {anchor["id"], *picked}
    actual = result["detail"][0]["principal"]
    key = "source_candidates" if direction == "source" else "targets"
    assert actual[key] == value["principal"][key]
    assert own_calls == [(cid, account, anchor["id"], anchor["id"])] * 2


def test_none_is_reused_but_next_context_rechecks_after_conflict_and_withdrawal(client, database, make_case, own_calls):
    cid = make_case("Synthetic own-entry lifetime")
    observations = import_events(client, cid, pair("SYN-OWN-LIFETIME"))
    anchor = next(row for row in observations if row["raw_self_account"] == "SYN-B")
    own = next(row for row in observations if row["raw_self_account"] == "SYN-A")
    account = anchor["payer_account"]
    first = context(client, cid, anchor, "payer")
    assert own_calls == [(cid, account, anchor["id"], own["id"])]
    assert first["principal"]["reasons"] == ["no_proven_earlier_credit_candidates"]
    assert {row["context_relation"] for row in first["rows"]} == {"anchor", "same_transaction"}
    _, receipt = accepted(client, cid, [("synthetic-late-conflict.csv", csv_rows(
        pair("SYN-OWN-LIFETIME", amount="101.00")[:1]))], scope_bank=None)
    own_calls.clear()
    changed = context(client, cid, anchor, "payer")
    assert changed["analysis_revision"] != first["analysis_revision"]
    assert changed["principal"]["revision"] == changed["analysis_revision"]
    assert changed["principal"]["status"] == "unknown"
    assert changed["principal"]["reasons"] == ["target_not_in_own_ledger"]
    assert not any(row["context_relation"] == "same_transaction" for row in changed["rows"])
    assert own_calls == [(cid, account, anchor["id"], None)]
    # The target-direction adapter must pass an explicit missing result through.
    own_calls.clear()
    with database._lock:
        target = principal_evidence.tracking_result(cid, own["payee_account"], own, [], "target", own_entry=None)
    # A known full-ledger conflict blocks allocation before source membership.
    assert target["status"] == "unknown" and target["reasons"] == ["conflicting_event_evidence"]
    assert own_calls == []
    assert client.delete(f"/api/batches/{receipt['batch_id']}", params={"case_id": cid}).status_code == 200
    restored = context(client, cid, anchor, "payer")
    assert restored["analysis_revision"] not in {first["analysis_revision"], changed["analysis_revision"]}
    assert restored["principal"]["reasons"] == ["no_proven_earlier_credit_candidates"]
    assert {row["context_relation"] for row in restored["rows"]} == {"anchor", "same_transaction"}
    assert own_calls == [(cid, account, anchor["id"], own["id"])]
    own_calls.clear()
    other = make_case("Synthetic foreign context")
    response = client.get(f"/api/context/{anchor['id']}", params={"case_id": other, "side": "payer"})
    assert response.status_code == 404 and own_calls == []
