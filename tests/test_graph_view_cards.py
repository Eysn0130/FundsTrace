"""The original canvas APIs select labels and money from the same business view."""
from test_folder_evidence import accepted, table, transaction


def test_graph_time_and_relationship_views_choose_their_own_card_and_totals(client, make_case):
    cid = make_case("Synthetic scoped graph cards")
    accepted(client, cid, [("cards.csv", table("transaction", [
        transaction(**{"交易卡号": "SYN-OLD", "交易流水号": "SYN-OLD-TX"}),
        transaction(**{"交易卡号": "SYN-NEW", "交易流水号": "SYN-NEW-TX", "交易时间": "2026-02-01 10:00:00", "交易对手账卡号": "SYN-C"}),
    ]))])
    observations = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    old = next(row for row in observations if row["trade_no"] == "SYN-OLD-TX")
    own = old["payer_account"]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in observations]}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert next(node for node in graph["nodes"] if node["account"] == own)["display_account"] == "SYN-NEW"
    for route in ("/api/graph/view", "/api/canvas/investigation/time"):
        for scope in ({"end": "2026-01-31T23:59:59"},
                      {"scope": "visible", "visible_accounts": [own, old["payee_account"]],
                       "visible_edges": [{"source": own, "target": old["payee_account"]}]}):
            response = client.post(route, json={"case_id": cid, **scope})
            assert response.status_code == 200, response.text
            value = response.json()
            node = next(node for node in value["nodes"] if node["account"] == own)
            assert node["display_account"] == "SYN-OLD"
            assert value["analysis_revision"] == graph["analysis_revision"]
            assert len(value["edges"]) == 1
            assert node["out_observation_count"] == 1
            assert node["out_funds"]["totals"][0]["upper"] == "10.25"
    # Free text aliases select the same entity without changing its graph label.
    for token in ("SYN-OLD", "SYN-NEW", "SYN-A"):
        for field in ("account", "entity", "counter"):
            response = client.get("/api/flows", params={"case_id": cid, field: token, "tto": "2026-01-31"})
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 1
    assert next(node for node in client.get("/api/graph", params={"case_id": cid}).json()["nodes"]
                if node["account"] == own)["display_account"] == "SYN-NEW"
    isolated = client.post("/api/graph/view", json={"case_id": cid, "scope": "visible",
        "visible_accounts": [own], "visible_edges": []}).json()
    assert isolated["edges"] == [] and len(isolated["nodes"]) == 1
    assert isolated["nodes"][0]["out_observation_count"] == 0
    assert isolated["nodes"][0]["display_basis"] == "associated_card"


def test_graph_view_rejects_inconsistent_or_foreign_scope(client, make_case):
    cid = make_case("Synthetic graph view guard")
    response = client.post("/api/graph/view", json={"case_id": cid, "scope": "visible",
        "visible_accounts": ["A"], "visible_edges": [{"source": "A", "target": "B"}]})
    assert response.status_code == 422
    assert client.post("/api/graph/view", json={"case_id": cid, "canvas_id": 99999}).status_code == 404
