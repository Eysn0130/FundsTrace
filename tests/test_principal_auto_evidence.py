"""The existing flow action prepares evidence; no client checkbox proves it."""
from test_principal_api import ledger, body, run
from test_event_graph import imported, pair


def test_existing_context_prepares_source_evidence_without_configuration(client, database, make_case):
    cid = make_case("Synthetic automatic context")
    ids = ledger(database, cid, [("in", "100.00", "100.00"), ("out", "100.00", "0.00")])
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    result = client.get(f"/api/context/{ids[0]}", params={"case_id": cid}).json()
    assert result["principal"]["condition_basis"] == "source_evidence"
    assert result["principal"]["status"] == "unknown"
    assert result["principal"]["conditions"]["coverage"] == "unknown"
    assert result["principal"]["conditions"]["opening_balance"] is None
    assert {row["balance"] for row in result["rows"]} == {"100.00", "0.00"}
    assert result["suggestions"]  # Existing heuristic leads remain available.
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before


def test_client_conditions_cannot_replace_source_evidence(client, database, make_case):
    cid = make_case("Synthetic no declaration authority")
    ids = ledger(database, cid, [("in", "100.00", "100.00"), ("out", "100.00", "0.00")])
    result = run(client, body(cid, ids))
    assert result["status"] == "unknown"
    assert result["condition_basis"] == "source_evidence"
    assert result["conditions"] == {"coverage": "unknown", "no_overdraft": False,
        "funds_available": False, "opening_balance": None, "balance_kind": "unknown"}
    assert {item["kind"] for item in result["assessment"]} == {"missing_evidence"}


def test_explicit_after_book_balance_supports_only_its_own_derived_condition(client, make_case):
    cid = make_case("Synthetic source basis")
    rows = imported(client, cid, pair("SYN-BOOK"))
    own = next(row for row in rows if row["self_display_account"] == "SYN-B")
    request = body(cid, [own["id"]], account=own["self_account"], conditions={})
    result = run(client, request)
    assert result["status"] == "unknown"
    assert result["conditions"]["opening_balance"] == "100.00"
    evidence = {item["condition"]: item for item in result["assessment"]}
    assert evidence["opening_balance"]["kind"] == "derived_from_source"
    assert evidence["opening_balance"]["source_ids"]
    assert evidence["balance_kind"]["kind"] == "source_declaration"
    assert evidence["coverage"]["status"] == evidence["funds_available"]["status"] == "unknown"
