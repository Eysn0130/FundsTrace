"""Public context keeps source ledgers without inventing transaction ordering."""
from app import event_resolution
from test_event_graph import imported, pair


def read_context(client, cid, flow_id):
    response = client.get(f"/api/context/{flow_id}", params={"case_id": cid, "side": "payee"})
    assert response.status_code == 200, response.text
    return response.json()


def test_confirmed_pair_is_one_context_group_with_two_unchanged_ledger_balances(client, make_case):
    cid = make_case("Synthetic same transaction context")
    observations = imported(client, cid, pair("SYN-CONTEXT"))
    anchor = next(row for row in observations if row["raw_self_account"] == "SYN-A")
    result = read_context(client, cid, anchor["id"])
    assert len(result["rows"]) == 2
    assert {str(row["id"]) for row in result["rows"]} == {row["id"] for row in observations}
    assert {row["amount"] for row in result["rows"]} == {"100.00"}
    assert {row["balance"] for row in result["rows"]} == {"50.00", "200.00"}
    assert len({row["context_group"] for row in result["rows"]}) == 1
    assert {row["context_relation"] for row in result["rows"]} == {"anchor", "same_transaction"}
    assert {row["event_state"] for row in result["rows"]} == {"confirmed"}
    assert result["funds"]["count"] == {"lower": 1, "upper": 1}
    assert result["funds"]["totals"][0]["amount"] == "100.00"


def test_equal_time_unknown_records_are_not_ordered_by_amount_or_id(client, make_case):
    cid = make_case("Synthetic unordered context")
    ordinary = [{key: value for key, value in row.items() if key not in event_resolution.HEADERS.values()}
                for row in pair("SYN-NO-PROOF")]
    observations = imported(client, cid, ordinary)
    for anchor in observations:
        result = read_context(client, cid, anchor["id"])
        assert len({row["context_group"] for row in result["rows"]}) == 2
        assert {row["context_relation"] for row in result["rows"]} == {"anchor", "same_time"}
        assert all(row["event_id"] is None for row in result["rows"])
        assert result["funds"]["count"] == {"lower": 0, "upper": 2}


def test_context_uses_recorded_time_for_before_after_and_withdraws_conflicting_membership(client, make_case):
    cid = make_case("Synthetic chronological context")
    observations = imported(client, cid, pair("SYN-BEFORE", day="01") + pair("SYN-ANCHOR", day="02") + pair("SYN-AFTER", day="03"))
    anchor = next(row for row in observations if row["trade_no"] == "SYN-ANCHOR-SYN-A")
    result = read_context(client, cid, anchor["id"])
    assert {row["context_relation"] for row in result["rows"] if row["trade_no"].startswith("SYN-BEFORE-")} == {"before"}
    assert {row["context_relation"] for row in result["rows"] if row["trade_no"].startswith("SYN-AFTER-")} == {"after"}
    imported(client, cid, pair("SYN-ANCHOR", amount="101.00", day="02")[:1])
    changed = read_context(client, cid, anchor["id"])
    assert changed["analysis_revision"] != result["analysis_revision"]
    assert not any(row["context_relation"] == "same_transaction" for row in changed["rows"])
    assert all(row["event_id"] is None for row in changed["rows"] if row["trade_no"].startswith("SYN-ANCHOR-"))
