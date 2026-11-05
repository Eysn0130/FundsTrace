"""Existing case declarations decorate source-only nodes without new identity claims."""
from test_graph_view_export import export_data
from test_source_ledger import add_source, changed_payload


def mark(client, cid, account, name="SYN owner"):
    response = client.post("/api/main-accounts", json={"case_id": cid, "account": account, "name": name})
    assert response.status_code == 200, response.text


def graph(client, cid):
    response = client.get("/api/graph", params={"case_id": cid})
    assert response.status_code == 200, response.text
    return response.json()


def track(client, cid, rows):
    response = client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in rows]})
    assert response.status_code == 200, response.text


def test_registered_main_and_same_name_flags_apply_to_source_only_graph_nodes(client, make_case):
    cid = make_case("SYN source-only existing declaration")
    add_source(client, cid, changed_payload([(1, {"对手户名": "SYN owner"})]))
    mark(client, cid, "000A")
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(rows) == 3
    track(client, cid, [row for row in rows if row["source_ledger"]])
    result = graph(client, cid)
    nodes = {row["account"]: row for row in result["nodes"]}
    assert (nodes["000A"]["is_main"], nodes["000B"]["is_main"]) == (1, 0)
    assert nodes["000B"]["is_same_name"] == 1
    assert result["transaction_count"] == 0 and result["source_ledger"]["count"] == 2
    edge, = result["edges"]
    assert (edge["source"], edge["target"], edge["cnt"], edge["total"]) == ("000A", "000B", 0, None)
    assert edge["source_ledger"]["records"][0]["signed_amount"] == "-5.00"
    response = client.post("/api/export/graph.html", json={"case_id": cid,
        "nodes": [{"id": row["account"], "label": row["name"], "data": row} for row in result["nodes"]],
        "edges": [{**edge, "label": "", "data": edge}]})
    assert response.status_code == 200, response.text
    exported = {row["id"]: row["data"] for row in export_data(response.text)["nodes"]}
    assert exported["000A"]["is_main"] == 1 and exported["000B"]["is_same_name"] == 1


def test_source_only_counterparty_manual_mark_remove_reopen_and_case_isolation(client, database, make_case):
    data = changed_payload([(1, {"交易账号": "000C", "交易对手账卡号": "000D", "对手户名": "SYN owner"}),
                            (2, {"交易账号": "000E", "交易方户名": ""})])
    cid = make_case("SYN financial and source declarations")
    add_source(client, cid, data)
    mark(client, cid, "000A")
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert {row["amount"] for row in rows} == {"10.00", "-5.00", "0.00"}
    track(client, cid, rows)
    before = graph(client, cid)
    mark(client, cid, "000D")
    marked = graph(client, cid)
    nodes = {row["account"]: row for row in marked["nodes"]}
    assert nodes["000A"]["is_main"] == nodes["000D"]["is_main"] == 1
    assert nodes["000C"]["is_main"] == 0 and nodes["000C"]["is_same_name"] == 1
    assert nodes["000E"]["is_main"] == nodes["000E"]["is_same_name"] == 0
    assert marked["funds"] == before["funds"] and marked["source_ledger"] == before["source_ledger"]
    financial = next(edge for edge in marked["edges"] if edge["source"] == "000A")
    assert (financial["target"], financial["cnt"], financial["total"]) == ("000B", 1, "10.00")
    with database._lock:
        database._con.close()
        database._con = None
    assert next(row for row in graph(client, cid)["nodes"] if row["account"] == "000D")["is_main"] == 1
    response = client.request("DELETE", "/api/main-accounts", json={"case_id": cid, "account": "000D"})
    assert response.status_code == 200, response.text
    unmarked = graph(client, cid)
    node = next(row for row in unmarked["nodes"] if row["account"] == "000D")
    assert node["is_main"] == 0 and node["is_same_name"] == 1
    assert unmarked["funds"] == before["funds"] and unmarked["source_ledger"] == before["source_ledger"]
    other = make_case("SYN isolated declarations")
    add_source(client, other, data)
    other_rows = client.get(f"/api/v1/cases/{other}/flows").json()["items"]
    track(client, other, other_rows)
    assert all(not row["is_main"] and not row["is_same_name"] for row in graph(client, other)["nodes"])
