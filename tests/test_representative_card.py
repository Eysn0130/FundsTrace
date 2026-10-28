"""Representative cards are evidenced display values, never ledger identities."""
from test_folder_evidence import accepted, account, entities, table, transaction


def test_unique_evidenced_card_is_visible_on_original_graph(client, make_case):
    cid = make_case("Synthetic representative card")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()])),
                           ("accounts.csv", table("account", [account()]))])
    own = next(row for row in entities(client, cid)["entities"] if row["account_number"] == "SYN-A")
    flows = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [int(row["id"]) for row in flows]}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    node = next(row for row in graph["nodes"] if row["account"] == own["entity_id"])
    assert node["display_account"] == "SYN-C1"


def own(client, cid):
    return next(row for row in entities(client, cid)["entities"] if row["account_number"] == "SYN-A")


def scoped_display(database, cid, entity_id, *, after=None, before=None):
    from app import evidence_resolution
    source, params = "select * from effective_flows where case_id=?", [cid]
    if after:
        source += " and trade_time>=?"
        params.append(after)
    if before:
        source += " and trade_time<=?"
        params.append(before)
    rows = [{"account": entity_id}]
    with database._lock:
        evidence_resolution.decorate_accounts(cid, rows, source_sql=source, params=params)
    return rows[0]


def test_latest_explicit_card_missing_card_and_scope_keep_history(client, database, make_case):
    cid = make_case("Synthetic card timeline")
    accepted(client, cid, [("tx.csv", table("transaction", [
        transaction(**{"交易卡号": "SYN-Z-OLD", "交易流水号": "SYN-OLD"}),
        transaction(**{"交易卡号": "SYN-A-NEW", "交易流水号": "SYN-NEW", "交易时间": "2026-02-01 10:00:00"}),
        transaction(**{"交易卡号": "", "交易流水号": "SYN-NO-CARD", "交易时间": "2026-03-01 10:00:00"}),
    ]))])
    entity = own(client, cid)
    assert entity["display_account"] == "SYN-A-NEW"
    assert entity["display_basis"] == "transaction_card"
    assert entity["display_card_order"] == "timestamp"
    assert scoped_display(database, cid, entity["entity_id"], before="2026-01-31")["display_account"] == "SYN-Z-OLD"
    # The range without an observed card uses a separately labelled reliable
    # association; it does not pretend that the blank March row has a card.
    fallback = scoped_display(database, cid, entity["entity_id"], after="2026-03-01")
    assert (fallback["display_account"], fallback["display_basis"]) == ("SYN-A-NEW", "associated_card")
    flows = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    assert {row["trade_no"]: row["payer_display_account"] for row in flows} == {
        "SYN-OLD": "SYN-Z-OLD", "SYN-NEW": "SYN-A-NEW", "SYN-NO-CARD": "SYN-A"}
    for token in ("SYN-A", "SYN-Z-OLD", "SYN-A-NEW"):
        assert client.get(f"/api/v1/cases/{cid}/flows", params={"account": token}).json()["total"] == 3


def test_same_second_without_order_is_stable_and_explicitly_tied(client, make_case):
    cid = make_case("Synthetic tied cards")
    tx = [("tx.csv", table("transaction", [
        transaction(**{"交易卡号": "SYN-Z", "交易流水号": "SYN-Z-ROW"}),
        transaction(**{"交易卡号": "SYN-A", "交易流水号": "SYN-A-ROW"}),
    ]))]
    accepted(client, cid, tx)
    before = own(client, cid)
    assert (before["display_account"], before["display_basis"], before["display_card_tied"]) == (
        "SYN-A", "tied_transaction_cards", True)
    assert before["display_card_candidates"] == ["SYN-A", "SYN-Z"]
    accepted(client, cid, list(reversed(tx)), zipped=True)
    assert own(client, cid) == before
    exported = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    assert own(client, restored.json()["case_id"]) == before


def ordered_row(card, order, *, other=False):
    from test_event_import import transaction as ledger_transaction
    return ledger_transaction("SYN-B" if other else "SYN-A", **{
        "交易卡号": "SYN-OWN-OTHER" if other else card,
        "交易流水号": "SYN-POST-" + order, "记账顺序号": order,
        **({"付款卡号": card} if other else {})})


def test_same_second_shared_own_ledger_order_can_choose_last(client, make_case):
    from test_event_import import csv_rows
    cid = make_case("Synthetic ordered card evidence")
    # Reverse file order and lexical card order: only the explicit source
    # sequence gives the expected Z card, independently of database IDs.
    accepted(client, cid, [("ledger.csv", csv_rows([
        ordered_row("SYN-Z-LAST", "20"), ordered_row("SYN-A-FIRST", "10")]))], scope_bank=None)
    chosen = own(client, cid)
    assert (chosen["display_account"], chosen["display_card_order"], chosen["display_card_tied"]) == (
        "SYN-Z-LAST", "source_posting", False)


def test_same_second_other_ledger_order_cannot_choose_last(client, make_case):
    from test_event_import import csv_rows
    cid = make_case("Synthetic foreign ledger order")
    accepted(client, cid, [("other-ledger.csv", csv_rows([
        ordered_row("SYN-Z-LAST", "20", other=True), ordered_row("SYN-A-FIRST", "10", other=True)]))], scope_bank=None)
    chosen = own(client, cid)
    assert (chosen["display_account"], chosen["display_card_order"], chosen["display_card_tied"]) == (
        "SYN-A-FIRST", "tied", True)


def test_card_declaration_conflict_withdrawal_deletion_and_reopen_recompute(client, database, make_case):
    cid = make_case("Synthetic card withdrawal")
    accepted(client, cid, [("old.csv", table("transaction", [transaction(**{"交易卡号": "SYN-OLD"})]))])
    _, new_batch = accepted(client, cid, [("new.csv", table("transaction", [transaction(**{
        "交易卡号": "SYN-NEW", "交易时间": "2026-02-01 10:00:00", "交易流水号": "SYN-NEW-ROW"})])),
        ("new-account.csv", table("account", [account(card="SYN-NEW")]))])
    key = own(client, cid)["entity_id"]
    assert own(client, cid)["display_account"] == "SYN-NEW"
    _, conflict = accepted(client, cid, [("conflict.csv", table("account", [account(number="SYN-OTHER", card="SYN-NEW")]))])
    assert own(client, cid)["display_account"] == "SYN-OLD"
    assert client.delete(f"/api/batches/{conflict['batch_id']}", params={"case_id": cid}).status_code == 200
    assert own(client, cid)["display_account"] == "SYN-NEW"
    assert client.delete(f"/api/batches/{new_batch['batch_id']}", params={"case_id": cid}).status_code == 200
    assert own(client, cid)["display_account"] == "SYN-OLD"
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    reopened = own(client, cid)
    assert (reopened["entity_id"], reopened["display_account"]) == (key, "SYN-OLD")


def test_old_projection_schema_upgrades_without_rewriting_prior_revision(client, database, make_case):
    cid = make_case("Synthetic old derived card projection")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    old = database.q_raw("select * from resolution_revisions where case_id=?", [cid])
    # Simulate an earlier catalog: source journal and immutable audits survive.
    with database._lock:
        database.x("drop view effective_flows")
        for column in ("endpoint_card", "endpoint_account", "card_state"):
            database.x("alter table resolved_endpoints drop column " + column)
        database.x("delete from resolution_projection_versions where case_id=?", [cid])
        database._con.close()
        database._con = None
        database.con()
    chosen = own(client, cid)
    assert chosen["display_account"] == "SYN-C1"
    for record in old:
        assert database.q_raw("select * from resolution_revisions where case_id=? and revision=?", [cid, record["revision"]]) == [record]


def test_undated_card_does_not_replace_latest_dated_card(client, database, make_case):
    cid = make_case("Synthetic undated card")
    accepted(client, cid, [("tx.csv", table("transaction", [
        transaction(**{"交易卡号": "SYN-Z-DATED", "交易流水号": "SYN-DATED"}),
        transaction(**{"交易卡号": "SYN-A-UNDATED", "交易流水号": "SYN-UNDATED", "交易时间": "2025-01-01 10:00:00"}),
    ]))])
    # Current import admission rejects blank timestamps. Exercise the nullable
    # historical-storage case directly inside this disposable synthetic DB.
    database.x("update flows set trade_time=null where case_id=? and trade_no='SYN-UNDATED'", [cid])
    chosen = own(client, cid)
    assert (chosen["display_account"], chosen["display_card_order"]) == ("SYN-Z-DATED", "timestamp")
    historical = database.q_raw("select payer_card from effective_flows where case_id=? order by payer_card", [cid])
    assert historical == [{"payer_card": "SYN-A-UNDATED"}, {"payer_card": "SYN-Z-DATED"}]


def test_same_second_unique_card_needs_no_transaction_order(client, make_case):
    cid = make_case("Synthetic same card tied observations")
    accepted(client, cid, [("tx.csv", table("transaction", [
        transaction(**{"交易流水号": "SYN-FIRST"}), transaction(**{"交易流水号": "SYN-SECOND"})]))])
    chosen = own(client, cid)
    assert (chosen["display_account"], chosen["display_basis"], chosen["display_card_tied"]) == (
        "SYN-C1", "transaction_card", False)


def test_association_fallback_excludes_conflicting_first_card(client, database, make_case):
    cid = make_case("Synthetic reliable association fallback")
    accepted(client, cid, [("accounts.csv", table("account", [
        account(card="SYN-A-CONFLICT"), account(number="SYN-OTHER", card="SYN-A-CONFLICT"),
        account(card="SYN-Z-RELIABLE")]))])
    chosen = own(client, cid)
    assert chosen["cards"] == ["SYN-A-CONFLICT", "SYN-Z-RELIABLE"]
    assert (chosen["display_account"], chosen["display_basis"]) == ("SYN-Z-RELIABLE", "associated_card")


def test_disagreeing_cards_for_same_observation_revoke_only_card_display(database):
    import copy
    from app import evidence_resolution as resolution
    endpoint = {"account": "SYN-A", "card": "SYN-C1", "bank": "Synthetic bank", "name": "", "identifier_type": "account"}
    counter = {**endpoint, "account": "SYN-B", "card": ""}
    transaction_note = {"role": "transaction", "own_side": "payer", "scope_eligible": "no",
                        "endpoints": {"payer": endpoint, "payee": counter, "self": endpoint}}
    first = [("source-one", 1, 42, transaction_note)]
    before, before_endpoints, _, _ = resolution._compute(copy.deepcopy(first))
    other = copy.deepcopy(transaction_note)
    other["endpoints"]["payer"]["card"] = "SYN-C2"
    other["endpoints"]["self"]["card"] = "SYN-C2"
    third = copy.deepcopy(transaction_note)
    third["endpoints"]["payer"]["card"] = "SYN-C3"
    third["endpoints"]["self"]["card"] = "SYN-C3"
    combined = [*first, ("source-two", 1, 42, other), ("source-three", 1, 42, third)]
    after, endpoints, _, _ = resolution._compute(copy.deepcopy(combined))
    before_own = next(item for item in before if item["account_number"] == "SYN-A")
    after_own = next(item for item in after if item["account_number"] == "SYN-A")
    assert after_own["entity_id"] == before_own["entity_id"]
    assert after_own["cards"] == ["SYN-C1", "SYN-C2", "SYN-C3"]
    assert after_own["reliable_cards"] == []
    assert {(item["state"], item["card_state"], item["endpoint_card"]) for item in endpoints if item["role"] in {"self", "payer"}} == {
        ("resolved", "conflict", "")}
    # Source order and disk spilling must not turn a conflict into a card.
    with resolution._ResolutionStaging() as staging:
        staged = resolution._compute(copy.deepcopy(list(reversed(combined))), staging=staging)
        assert staged[0] == after and list(staged[1]) == endpoints
    restored, restored_endpoints, _, _ = resolution._compute(copy.deepcopy(first))
    assert (restored, restored_endpoints) == (before, before_endpoints)
