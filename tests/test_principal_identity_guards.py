"""Public imports cannot turn known card/identity uncertainty into a ledger."""
import pytest
from datetime import datetime

from app import principal, principal_ledger, principal_multihop
from app.principal_routes import NetworkIn

from test_folder_evidence import accepted, account, entities, table, transaction


def movements(number):
    return [transaction(**{"交易账号": number, "交易金额": "100.00", "交易余额": "100.00",
                           "收付标志": "入", "交易流水号": "SYN-SOURCE", "交易对手账卡号": "SYN-EXT-IN"}),
            transaction(**{"交易账号": number, "交易金额": "100.00", "交易余额": "0.00",
                           "交易时间": "2026-01-01 10:01:00", "交易流水号": "SYN-TARGET",
                           "交易对手账卡号": "SYN-EXT-OUT"})]


def request(client, case_id, network=False):
    response = client.get(f"/api/v1/cases/{case_id}/flows")
    assert response.status_code == 200, response.text
    rows = {row["trade_no"]: row for row in response.json()["items"]}
    source, target = rows["SYN-SOURCE"], rows["SYN-TARGET"]
    assert source["self_account"] == target["self_account"]
    conditions = {"coverage": "complete_declared", "opening_balance": "0.00", "no_overdraft": True,
                  "funds_available": True, "balance_kind": "post_book"}
    body = {"case_id": str(case_id), "currency": "CNY", "start": "2026-01-01T00:00:00",
            "end": "2026-01-01T23:59:59", "source_flow_id": source["id"], "target_flow_ids": [target["id"]]}
    if network:
        body.update(accounts=[{"account": source["self_account"], "conditions": conditions}],
                    boundary="terminal_no_return_declared")
    else:
        body.update(account=source["self_account"], conditions=conditions)
    return source["self_account"], body


def analyze(client, case_id, network=False):
    key, body = request(client, case_id, network)
    response = client.post("/api/analysis/principal" + ("/network" if network else ""), json=body)
    assert response.status_code == 200, response.text
    return key, response.json()


def assert_usable_model(client, case_id, *, network=False, body=None, expected=(10000, 10000, 10000)):
    """Identity can support a conditional model without proving coverage."""
    key, default = request(client, case_id, network)
    body = body or default
    if network:
        result = principal_multihop._calculate_conditioned(case_id, NetworkIn(**body))
        assert result["status"] == "ok", result
        target = result["targets"][0]
        assert expected == (10000, 10000, 10000)
        assert tuple(target[field] for field in ("min", "max", "fifo")) == ("100.00", "100.00", "100.00")
        assert result["ledger_count"] == 2
    else:
        loaded = principal_ledger.read(case_id, key, "CNY", datetime.fromisoformat(body["start"]), datetime.fromisoformat(body["end"]))
        assert loaded["status"] == "ready", loaded
        assert len(loaded["rows"]) == 2
        opening = principal_ledger.units(body["conditions"]["opening_balance"])
        entries, reason = principal_ledger.ordered(loaded["rows"], opening, {}, body["conditions"]["balance_kind"])
        assert reason is None
        target = principal.allocate_ledger(opening, entries, {body["source_flow_id"]}, body["target_flow_ids"])["targets"][0]
        assert tuple(target[field] for field in ("min", "max", "fifo")) == expected


def assert_missing_source_evidence(result, network=False):
    assert result["status"] == "unknown" and result["targets"] == []
    assert "account_ledger_identity_unknown" not in result["reasons"]
    assert ("opening_balance_unknown" if network else "ledger_coverage_unknown") in result["reasons"]


@pytest.mark.parametrize("network", [False, True])
def test_one_card_multiple_ledgers_cannot_be_declared_one_complete_account(client, database, make_case, network):
    case_id = make_case("Synthetic ambiguous card ledger")
    accepted(client, case_id, [("flows.csv", table("transaction", movements(""))),
                              ("accounts.csv", table("account", [account(number="SYN-L1"), account(number="SYN-L2")]))])
    key, result = analyze(client, case_id, network)
    own = next(item for item in entities(client, case_id)["entities"] if item["entity_id"] == key)
    assert own["kind"] == "card" and own["status"] == "card_only"
    states = database.q_raw("select state from resolved_endpoints where case_id=? and role='self'", [case_id])
    assert {item["state"] for item in states} == {"card_only"}
    assert result["status"] == "unknown"
    assert "account_ledger_identity_unknown" in result["reasons"]
    assert result["targets"] == []


@pytest.mark.parametrize("kind", ["account", "subaccount"])
@pytest.mark.parametrize("network", [False, True])
def test_explicit_account_and_subaccount_remain_separate_usable_ledgers(client, make_case, kind, network):
    case_id = make_case("Synthetic explicit book identity")
    number = "SYN-A" if kind == "account" else "SYN-S1"
    auxiliary = [("accounts.csv", table("account", [account(number="SYN-A" if kind == "account" else "SYN-P")]))]
    if kind == "subaccount":
        auxiliary.append(("subaccounts.csv", table("subaccount", [
            {"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": "SYN-S1", "币种": "CNY"},
            {"银行名称": "Synthetic bank", "开户账号": "SYN-P", "子账户账号": "SYN-S2", "币种": "CNY"}])))
    accepted(client, case_id, [("flows.csv", table("transaction", movements(number))), *auxiliary])
    key, result = analyze(client, case_id, network)
    own = next(item for item in entities(client, case_id)["entities"] if item["entity_id"] == key)
    assert own["kind"] == kind and own["status"] == "resolved"
    if kind == "subaccount":
        assert own["parent_entity_id"] and own["parent_entity_id"] != key
    assert_missing_source_evidence(result, network)
    assert_usable_model(client, case_id, network=network)


def test_another_card_target_does_not_erase_an_explicit_account(client, make_case):
    case_id = make_case("Synthetic contradictory book identity")
    accepted(client, case_id, [("flows.csv", table("transaction", movements("SYN-A"))),
                              ("accounts.csv", table("account", [account(number="SYN-A")]))])
    _, before = analyze(client, case_id)
    assert_missing_source_evidence(before)
    assert_usable_model(client, case_id)
    accepted(client, case_id, [("contradiction.csv", table("account", [account(number="SYN-OTHER")]))])
    key, after = analyze(client, case_id)
    own = next(item for item in entities(client, case_id)["entities"] if item["entity_id"] == key)
    # Explicit A remains A: no rewrite to the competing declaration is allowed.
    assert own["kind"] == "account" and own["account_number"] == "SYN-A"
    # A second card target alone does not contradict the separately stated A.
    assert_missing_source_evidence(after)
    assert_usable_model(client, case_id)


def test_explicit_account_with_only_contradicting_card_declaration_is_unknown(client, database, make_case):
    case_id = make_case("Synthetic known endpoint conflict")
    accepted(client, case_id, [("flows.csv", table("transaction", movements("SYN-A"))),
                              ("accounts.csv", table("account", [account(number="SYN-OTHER")]))])
    key, result = analyze(client, case_id)
    own = next(item for item in entities(client, case_id)["entities"] if item["entity_id"] == key)
    assert own["kind"] == "account" and own["status"] == "conflict"
    states = database.q_raw("select state from resolved_endpoints where case_id=? and role='self'", [case_id])
    assert {item["state"] for item in states} == {"conflict"}
    assert result["status"] == "unknown" and result["targets"] == []
    assert "account_ledger_identity_unknown" in result["reasons"]


@pytest.mark.parametrize("network", [False, True])
@pytest.mark.parametrize("competitor_currency", ["CNY", "UNKNOWN", None])
def test_unassigned_competing_card_entry_cannot_disappear_from_explicit_account(client, database, make_case, network, competitor_currency):
    case_id = make_case("Synthetic unassigned competing debit")
    rows = movements("SYN-A")
    rows[0]["交易余额"] = ""
    rows[1].update({"交易金额": "150.00", "交易余额": ""})
    rows.insert(1, transaction(**{"交易账号": "", "交易金额": "50.00", "交易余额": "",
                                  "交易时间": "2026-01-01 10:00:30", "交易流水号": "SYN-COMPETITOR"}))
    accepted(client, case_id, [("flows.csv", table("transaction", rows)),
                              ("accounts.csv", table("account", [account(number="SYN-A"), account(number="SYN-A2")]))])
    key, body = request(client, case_id, network)
    conditions = body["accounts"][0]["conditions"] if network else body["conditions"]
    conditions.update(opening_balance="100.00", balance_kind="unknown")
    own = next(item for item in entities(client, case_id)["entities"] if item["entity_id"] == key)
    assert own["kind"] == "account" and own["status"] == "resolved"
    states = database.q_raw("select state from resolved_endpoints where case_id=? and role='self' order by flow_id", [case_id])
    assert [item["state"] for item in states] == ["resolved", "card_only", "resolved"]
    if competitor_currency != "CNY":
        database.x("update flows set currency=? where case_id=? and trade_no='SYN-COMPETITOR'", [competitor_currency,case_id])
    response = client.post("/api/analysis/principal" + ("/network" if network else ""), json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "unknown" and result["targets"] == []
    assert "account_ledger_identity_unknown" in result["reasons"]


@pytest.mark.parametrize("outside", [{"交易币种": "USD"}, {"交易时间": "2026-01-02 10:00:30"}])
def test_uncertain_own_entry_outside_currency_or_interval_does_not_block(client, make_case, outside):
    case_id = make_case("Synthetic outside-scope ambiguous ledger")
    rows = movements("SYN-A")
    rows[0]["交易余额"] = ""
    rows[1].update({"交易金额": "150.00", "交易余额": ""})
    rows.insert(1, transaction(**{"交易账号": "", "交易金额": "50.00", "交易余额": "",
                                  "交易时间": "2026-01-01 10:00:30", "交易流水号": "SYN-COMPETITOR", **outside}))
    accepted(client, case_id, [("flows.csv", table("transaction", rows)),
                              ("accounts.csv", table("account", [account(number="SYN-A"), account(number="SYN-A2")]))])
    _, body = request(client, case_id)
    body["conditions"].update(opening_balance="100.00", balance_kind="unknown")
    response = client.post("/api/analysis/principal", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert_missing_source_evidence(result)
    assert result["ledger_count"] == 2
    assert_usable_model(client, case_id, body=body, expected=(5000, 10000, 5000))


def test_unscoped_explicit_number_is_not_a_proven_typed_ledger(client, make_case):
    case_id = make_case("Synthetic unscoped account identity")
    accepted(client, case_id, [("flows.csv", table("transaction", movements("SYN-A")))], scope_bank=None)
    key, result = analyze(client, case_id)
    own = next(item for item in entities(client, case_id)["entities"] if item["entity_id"] == key)
    assert own["kind"] == "account" and own["status"] == "unscoped"
    assert result["status"] == "unknown" and result["targets"] == []
    assert "account_ledger_identity_unknown" in result["reasons"]


def test_different_names_do_not_erase_resolved_account_identity(client, make_case):
    case_id = make_case("Synthetic account name disagreement")
    accepted(client, case_id, [("flows.csv", table("transaction", movements("SYN-A"))),
                              ("accounts.csv", table("account", [account(), account(**{"账户开户名称": "Synthetic alternate name"})]))])
    key, result = analyze(client, case_id)
    own = next(item for item in entities(client, case_id)["entities"] if item["entity_id"] == key)
    assert own["kind"] == "account" and own["status"] == "name_conflict"
    assert_missing_source_evidence(result)
    assert_usable_model(client, case_id)


@pytest.mark.parametrize("network", [False, True])
def test_historical_review_record_cannot_be_overridden_by_complete_ledger_declaration(client, database, make_case, monkeypatch, network):
    from app import feedback_import
    from test_feedback_import_api import archive, complete, preview, transaction as legacy_transaction

    case_id = make_case("Synthetic historical ledger gap")
    rows = [legacy_transaction(**{"交易流水号": "SYN-SOURCE", "收付标志": "入",
                                  "交易金额": "100.00", "交易余额": "100.00"}),
            legacy_transaction(**{"交易流水号": "SYN-COMPETITOR", "交易时间": "2026-01-01 10:00:30",
                                  "收付标志": "", "交易金额": "50.00", "交易余额": ""}),
            legacy_transaction(**{"交易流水号": "SYN-TARGET", "交易时间": "2026-01-01 10:01:00",
                                  "交易金额": "100.00", "交易余额": "0.00"})]
    with monkeypatch.context() as historical:
        historical.setattr(feedback_import, "PROFILE", feedback_import.IDENTITY_PROFILE)
        view = preview(client, case_id, archive(rows, account=False))
        completed = complete(client, case_id, view)
    assert completed["stage"] == "done", completed
    dispositions = database.q_raw("select disposition from import_records where case_id=? order by record_no", [case_id])
    assert [row["disposition"] for row in dispositions] == ["imported", "review", "imported"]
    assert client.get(f"/api/v1/cases/{case_id}/stats").json()["import_coverage"]["unresolved_records"] == 1
    _, result = analyze(client, case_id, network)
    assert result["status"] == "unknown" and result["targets"] == []
    assert "unresolved_source_records" in result["reasons"]
