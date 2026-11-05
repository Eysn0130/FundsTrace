"""Public imported book models stay separate from real availability evidence."""
from collections import defaultdict
from decimal import Decimal

import pytest

from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted


def book_row(own, payer, payee, amount, balance, minute, order, ref):
    return transaction(own, **{
        "付款账号": payer, "收款账号": payee, "交易金额": amount,
        "本笔交易后账面余额": balance, "交易时间": f"2026-01-01 10:{minute:02d}:00",
        "记账顺序号": str(order), "交易流水号": f"SYN-{own}-{ref}", "跨账簿事件参考号": ref})


def import_books(client, cid, rows):
    accepted(client, cid, [("synthetic-book.csv", csv_rows(rows))], scope_bank=None)
    response = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"})
    assert response.status_code == 200, response.text
    return {row["trade_no"]: row for row in response.json()["items"]}


def request(cid, source, targets):
    return {"case_id": str(cid), "currency": "CNY", "start": "2026-01-01T00:00:00",
            "end": "2026-01-01T23:59:59", "source_flow_id": source["id"],
            "target_flow_ids": [row["id"] for row in targets]}


@pytest.mark.parametrize("same_time", [False, True])
def test_public_imported_book_model_includes_untracked_competitor(client, database, make_case, same_time):
    cid = make_case("Synthetic public book model")
    rows = [book_row("B", "EXT", "B", "100.00", "140.00", 0, 1, "SOURCE"),
            book_row("B", "B", "D", "50.00", "90.00", 0 if same_time else 1, 2, "COMPETITOR"),
            book_row("B", "B", "C", "90.00", "0.00", 0 if same_time else 2, 3, "TARGET")]
    imported = import_books(client, cid, rows)
    source, target = imported["SYN-B-SOURCE"], imported["SYN-B-TARGET"]
    body = {**request(cid, source, [target]), "account": source["self_account"]}
    assert database.q_raw("select count(*) n from tracked_flows where case_id=?", [cid])[0]["n"] == 0
    response = client.post("/api/analysis/principal", json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ok", value
    assert value["ledger_count"] == 3
    assert value["conditions"]["opening_balance"] == "40.00"
    assert value["conditions"]["coverage"] == "unknown"
    assert value["conditions"]["funds_available"] is False
    assert value["model_scope"]["kind"] == "recorded_book_ledger"
    assert value["model_scope"]["opening_boundary"] == "immediately_before_first_recorded_entry"
    assert value["model_scope"]["real_world_attribution"] == "unknown"
    assert value["targets"][0]["amount"] == "90.00"
    assert tuple(value["targets"][0][field] for field in ("min", "max", "fifo")) == ("50.00", "90.00", "90.00")
    assert len(value["fifo_witness"]["entries"]) == 3
    # The existing context action reaches the same public model without configuration.
    context = client.get(f"/api/context/{source['id']}", params={"case_id": cid}).json()["principal"]
    assert context["status"] == "ok" and context["ledger_count"] == 3
    exported = client.post("/api/analysis/principal/export", json={**body, "expected_revision": value["revision"]})
    assert exported.status_code == 200 and "recorded_book_ledger" in exported.text
    assert "funds_availability_unknown" in exported.text


def test_public_multihop_joint_witness_does_not_sum_independent_maxima(client, make_case, monkeypatch):
    cid = make_case("Synthetic public joint first-exit model")
    rows = [book_row("A", "EXT", "A", "100.00", "200.00", 0, 1, "SOURCE"),
            book_row("A", "A", "B", "100.00", "100.00", 1, 2, "AB"),
            book_row("B", "A", "B", "100.00", "100.00", 1, 1, "AB"),
            book_row("A", "A", "C", "100.00", "0.00", 2, 3, "AC"),
            book_row("C", "A", "C", "100.00", "100.00", 2, 1, "AC"),
            book_row("B", "B", "XB", "100.00", "0.00", 3, 2, "BEXIT"),
            book_row("C", "C", "XC", "100.00", "0.00", 4, 2, "CEXIT")]
    imported = import_books(client, cid, rows)
    body = {**request(cid, imported["SYN-A-SOURCE"], [imported["SYN-B-BEXIT"], imported["SYN-C-CEXIT"]]),
            "accounts": [{"account": account} for account in sorted({r["self_account"] for r in imported.values()})]}
    response = client.post("/api/analysis/principal/network", json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ok", value
    assert value["model_scope"]["kind"] == "recorded_book_network"
    assert value["model_scope"]["boundary"] == "first_exit"
    assert value["model_scope"]["real_world_attribution"] == "unknown"
    assert (value["ledger_count"], value["event_count"]) == (7, 5)
    assert all(item["conditions"]["coverage"] == "unknown" for item in value["accounts"])
    assert [(t["min"], t["max"], t["fifo"]) for t in value["targets"]] == [
        ("0.00", "100.00", "0.00"), ("0.00", "100.00", "100.00")]
    for witness in [value["fifo_witness"], *(t[key] for t in value["targets"] for key in ("min_witness", "max_witness"))]:
        mass = defaultdict(Decimal)
        for edge in witness["edge_flows"]:
            amount, capacity = Decimal(edge["amount"]), Decimal(edge["capacity"])
            assert 0 < amount <= capacity
            mass[edge["from"]] -= amount
            mass[edge["to"]] += amount
        assert mass.pop(witness["source_node"]) == Decimal("-100.00")
        assert mass.pop(witness["sink_node"]) == Decimal("100.00")
        assert all(amount == 0 for amount in mass.values())
        assert Decimal(witness["external_total"]) + Decimal(witness["retained_total"]) == Decimal("100.00")
    exported = client.post("/api/analysis/principal/network/export", json={**body, "expected_revision": value["revision"]})
    assert exported.status_code == 200 and "first_exit" in exported.text
    assert "external_boundary_returns_unknown" in exported.text
    from app import principal_multihop
    monkeypatch.setattr(principal_multihop, "MAX_EVENTS", 1)
    limited = client.post("/api/analysis/principal/network", json=body).json()
    assert limited["status"] == "limited" and limited["targets"] == []
    assert limited["reasons"] == ["network_interval_resource_limit"]
    assert "fifo_witness" not in limited


def single_body(cid, imported, target="TARGET"):
    source = imported["SYN-B-SOURCE"]
    return {**request(cid, source, [imported[f"SYN-B-{target}"]]), "account": source["self_account"]}


@pytest.mark.parametrize("network", [False, True])
@pytest.mark.parametrize("anchor", ["", "generic"])
def test_missing_typed_opening_anchor_is_not_defaulted_or_replaced_by_request(client, make_case, network, anchor):
    cid = make_case("Synthetic absent book anchor")
    rows = [book_row("B", "EXT", "B", "100.00", "" if anchor == "" else "100.00", 0, 1, "SOURCE"),
            book_row("B", "B", "C", "100.00", "0.00", 1, 2, "TARGET")]
    if anchor == "generic":
        for row in rows:
            row["交易余额"] = row.pop("本笔交易后账面余额")
    body = single_body(cid, import_books(client, cid, rows))
    body["conditions"] = {"opening_balance": "0.00", "balance_kind": "post_book", "coverage": "complete_declared",
                          "no_overdraft": True, "funds_available": True}
    if network:
        body["accounts"] = [{"account": body.pop("account"), "conditions": body.pop("conditions")}]
        body["boundary"] = "terminal_no_return_declared"
    value = client.post("/api/analysis/principal" + ("/network" if network else ""), json=body).json()
    assert value["status"] == "unknown" and value["targets"] == []
    assert "opening_balance_unknown" in value["reasons"]


@pytest.mark.parametrize("balance,target_amount,target_balance,reason", [
    ("-1.00", "100.00", "0.00", "negative_book_balance"),
    ("50.00", "50.00", "0.00", "negative_opening_book_balance"),
    ("100.00", "101.00", "-1.00", "negative_book_balance"),
    ("100.00", "100.00", "50.00", "post_balance_mismatch"),
    ("100.00", "101.00", "", "insufficient_funds"),
])
@pytest.mark.parametrize("network", [False, True])
def test_invalid_book_capacity_returns_no_attribution(client, make_case, balance, target_amount, target_balance, reason, network):
    cid = make_case("Synthetic incompatible book capacity")
    rows = [book_row("B", "EXT", "B", "100.00", balance, 0, 1, "SOURCE"),
            book_row("B", "B", "C", target_amount, target_balance, 1, 2, "TARGET")]
    body = single_body(cid, import_books(client, cid, rows))
    if network:
        body["accounts"] = [{"account": body.pop("account")}]
    response = client.post("/api/analysis/principal" + ("/network" if network else ""), json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "inconsistency" and value["targets"] == [], value
    assert reason in value["reasons"]
    assert "fifo_witness" not in value


@pytest.mark.parametrize("missing_later_balances", [False, True])
def test_fee_participates_and_missing_later_balances_do_not_invent_an_opening(client, make_case, missing_later_balances):
    cid = make_case("Synthetic source-backed fee model")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "SOURCE"),
            book_row("B", "B", "FEE", "3.00", "" if missing_later_balances else "97.00", 1, 2, "FEE"),
            book_row("B", "B", "C", "97.00", "" if missing_later_balances else "0.00", 2, 3, "TARGET")]
    body = single_body(cid, import_books(client, cid, rows))
    value = client.post("/api/analysis/principal", json=body).json()
    assert value["status"] == "ok", value
    assert tuple(value["targets"][0][field] for field in ("min", "max", "fifo")) == ("97.00",) * 3
    assert [entry["source_amount"] for entry in value["fifo_witness"]["entries"]] == ["100.00", "3.00", "97.00"]


def test_missing_fee_is_not_reconstructed_from_balance_difference(client, make_case):
    cid = make_case("Synthetic absent fee")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "SOURCE"),
            book_row("B", "B", "C", "97.00", "0.00", 1, 2, "TARGET")]
    value = client.post("/api/analysis/principal", json=single_body(cid, import_books(client, cid, rows))).json()
    assert value["status"] == "inconsistency" and value["targets"] == []
    assert value["reasons"] == ["post_balance_mismatch"]


@pytest.mark.parametrize("split_members", [False, True])
def test_same_time_without_common_source_order_stays_unknown(client, make_case, split_members):
    cid = make_case("Synthetic ambiguous same-time debits")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "SOURCE"),
            book_row("B", "B", "D", "50.00", "", 1, 2 if split_members else "", "COMPETITOR"),
            book_row("B", "B", "C", "50.00", "", 1, 3 if split_members else "", "TARGET")]
    if split_members:
        accepted(client, cid, [(f"synthetic-{i}.csv", csv_rows([row])) for i, row in enumerate(rows)], scope_bank=None)
        imported = {row["trade_no"]: row for row in client.get(f"/api/v1/cases/{cid}/flows",
                    params={"granularity": "observations"}).json()["items"]}
    else:
        imported = import_books(client, cid, rows)
    value = client.post("/api/analysis/principal", json=single_body(cid, imported)).json()
    assert value["status"] == "unknown" and value["targets"] == []
    assert value["reasons"] == ["posting_order_unknown"]


def test_request_opening_and_availability_do_not_change_source_derived_model(client, make_case):
    cid = make_case("Synthetic request assertions")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "SOURCE"),
            book_row("B", "B", "C", "100.00", "0.00", 1, 2, "TARGET")]
    body = single_body(cid, import_books(client, cid, rows))
    before = client.post("/api/analysis/principal", json=body).json()
    after = client.post("/api/analysis/principal", json={**body, "conditions": {
        "opening_balance": "999.00", "balance_kind": "unknown", "coverage": "complete_declared",
        "no_overdraft": True, "funds_available": True}}).json()
    assert before == after
    assert after["status"] == "ok" and after["conditions"]["opening_balance"] == "0.00"
    assert after["conditions"]["coverage"] == "unknown" and not after["conditions"]["funds_available"]


def test_cancellation_journal_does_not_disappear_from_public_book_model(client, make_case):
    from test_cancellation_import_lifecycle import paired_source
    from test_feedback_import_api import preview, complete

    cid = make_case("Synthetic retained cancellation gap")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "SOURCE"),
            book_row("B", "B", "C", "100.00", "0.00", 1, 2, "TARGET")]
    body = single_body(cid, import_books(client, cid, rows))
    assert client.post("/api/analysis/principal", json=body).json()["status"] == "ok"
    job = complete(client, cid, preview(client, cid, paired_source()))
    assert job["stage"] == "done", job
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["import_coverage"]["cancelled_records"] == 2
    value = client.post("/api/analysis/principal", json=body).json()
    assert value["status"] == "unknown" and value["targets"] == []
    assert "cancellation_availability_not_established" in value["reasons"]


def test_public_first_exit_model_does_not_claim_external_return_is_new_real_money(client, make_case):
    cid = make_case("Synthetic external return boundary")
    rows = [book_row("B", "EXT", "B", "100.00", "100.00", 0, 1, "SOURCE"),
            book_row("B", "B", "X", "100.00", "0.00", 1, 2, "EXIT"),
            book_row("B", "X", "B", "100.00", "100.00", 2, 3, "RETURN"),
            book_row("B", "B", "C", "100.00", "0.00", 3, 4, "TARGET")]
    imported = import_books(client, cid, rows)
    body = {**request(cid, imported["SYN-B-SOURCE"], [imported["SYN-B-EXIT"], imported["SYN-B-TARGET"]]),
            "accounts": [{"account": imported["SYN-B-SOURCE"]["self_account"]}]}
    value = client.post("/api/analysis/principal/network", json=body).json()
    assert value["status"] == "ok", value
    assert [(t["min"], t["max"], t["fifo"]) for t in value["targets"]] == [("100.00",) * 3, ("0.00",) * 3]
    assert value["fifo_witness"]["external_total"] == "100.00"
    assert value["fifo_witness"]["retained_total"] == "0.00"
    assert value["model_scope"]["boundary"] == "first_exit"
    assert value["model_scope"]["real_world_attribution"] == "unknown"
    assert "external_boundary_returns_unknown" in value["limitations"]
