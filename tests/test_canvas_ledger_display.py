"""The ordinary canvas can count paired source observations once."""
from app import feedback_import
from test_automatic_table_import import preview
from test_event_graph import graph, projection, track
from test_feedback31_reference import payload
from test_feedback_import_api import complete
from test_folder_evidence import transaction


def test_canvas_counts_only_selected_ledger_actions_and_keeps_originals(client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic source ledger display")
    a, b, third = "000100011", "000100012", "000100013"
    card_a, card_b, card_third = "0007000011", "0007000012", "0007000013"

    def posting(account, card, other, minute, direction, amount, balance, reference, voucher=""):
        return transaction(**{"交易账号": account, "交易卡号": card, "交易对手账卡号": other,
            "交易时间": f"2037-02-01 09:{minute:02d}:01", "交易流水号": reference,
            "收付标志": direction, "交易金额": amount, "交易余额": balance, "交易币种": "CNY",
            "交易是否成功": "09", "凭证号": voucher, "查询反馈结果原因": "成功"})

    # Deliberately use a different file order from the two account clocks.
    rows = [
        posting(a, card_a, card_third, 58, "入", "0.02", "24.02", "SYN-NEXT-P"),
        posting(b, card_b, card_a, 56, "入", "18.73", "27.00", "SYN-PAIR", "SYN-VOUCHER"),
        posting(a, card_a, card_third, 55, "出", "18.73", "42.73", "SYN-NEAR"),
        posting(b, card_b, card_third, 53, "入", "1.01", "8.27", "SYN-PREV-R"),
        posting(a, card_a, card_b, 56, "出", "18.73", "24.00", "SYN-PAIR", "SYN-VOUCHER"),
        posting(b, card_b, card_third, 57, "出", "0.51", "26.49", "SYN-NEXT-R"),
    ]
    # Older unrelated occurrences of the voucher occupy the bounded list.
    noise = [posting(third, card_third, card_a, minute, "入", "1.00", str(minute + 1),
                     f"SYN-VOUCHER-OTHER-{minute}", "SYN-VOUCHER") for minute in range(12)]
    ready = preview(client, case, payload(noise + rows, feedback_import.COLUMNS), "ledger-actions.csv")
    saved = complete(client, case, ready)
    assert saved["stage"] == "done" and saved["result"]["ok"] == len(noise + rows)
    base = f"/api/v1/cases/{case}"
    source = ready["import_id"]
    records = client.get(f"{base}/imports/{source}/review", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    by_row = {row["source_row"] - len(noise): row for row in records}
    in_id, near_id, out_id = (str(by_row[number]["flow_id"]) for number in (3, 4, 6))
    assert "ledger_summary" not in graph(client, case)

    def ordinary_counts(result, count, observations, amount):
        assert result["ledger_summary"] == {
            "policy": "selected-ledger-actions-v1", "count": count, "observation_count": observations,
            "folded_pairs": observations - count, "amount_totals": [{"currency": "CNY", "amount": amount}]}
        assert result["funds"]["confirmed_event_count"] == 0

    track(client, case, [{"id": in_id}])
    ordinary_counts(graph(client, case), 1, 1, "18.73")
    track(client, case, [{"id": out_id}])
    paired = graph(client, case)
    ordinary_counts(paired, 1, 2, "18.73")
    assert paired["transaction_count"] == len(paired["edges"]) == 2
    assert {str(i) for edge in paired["edges"] for i in edge["ids"]} == {in_id, out_id}
    one_edge = next(edge for edge in paired["edges"] if str(edge["ids"][0]) == out_id)
    filtered = client.post("/api/graph/view", json={"case_id": case, "scope": "visible",
        "visible_accounts": [one_edge["source"], one_edge["target"]],
        "visible_edges": [{"source": one_edge["source"], "target": one_edge["target"]}]}).raise_for_status().json()
    ordinary_counts(filtered, 1, 1, "18.73")
    track(client, case, [{"id": near_id}])
    ordinary_counts(graph(client, case), 2, 3, "37.46")
    ordinary_counts(projection(client, case, start="2037-02-01T09:56:01", end="2037-02-01T09:56:01"), 1, 2, "18.73")

    for anchor, peer in ((out_id, in_id), (in_id, out_id)):
        card = client.get(f"/api/context/{anchor}", params={"case_id": case, "review": "true"}).raise_for_status().json()
        answer = client.post(f"{base}/review-model-trial", json={"kind": "pair", "anchor_id": anchor,
            "review_token": card["review"]["token"], "canvas_id": "0"}).raise_for_status().json()
        assert (answer["state"], answer["reason"], answer["first_calls"], answer["recheck_calls"]) == (
            "rule", "ledger_observations_linked", 0, 0)
        assert answer["receipt"]["status"] == "pending"
        packet = answer["packet"]
        assert len(packet["candidates"]) == 8 and packet["candidates"][0]["id"] == "F" + peer
        assert packet["facts"]["candidate_total"] == len(noise) + 1
        assert packet["facts"]["candidate_total_exact"] and packet["facts"]["more_candidates"]
        assert packet["facts"]["ledger_correspondence"]["witness_rows"] == [[17, 19], [16, 14]]

    before_close = graph(client, case)["ledger_summary"]
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert graph(client, case)["ledger_summary"] == before_close
    monkeypatch.setattr(client.app.state, "desktop_model_trial", None)
    assert "ledger_summary" not in graph(client, case)
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,))
    client.delete(f"{base}/sources/{source}/operation",
                  params={"expected_batch_id": saved["result"]["batch_id"]}).raise_for_status()
    cleared = graph(client, case)
    assert "ledger_summary" not in cleared and cleared["transaction_count"] == 0
