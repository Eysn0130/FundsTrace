"""Imported synthetic money reaches graph totals only after full validation."""

from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_financial_mapping import table_bytes


def test_imported_refund_signed_amount_and_gaps_keep_graph_money_exact(client, make_case):
    cid = make_case("Synthetic financial graph contract")
    source = table_bytes(["date", "account", "counterparty account", "direction", "amount", "currency", "memo"], [
        ["2026-01-01 10:00:00", "SYN-A", "SYN-B", "out", "100.05", "CNY", "payment"],
        ["2026-01-02 10:00:00", "SYN-A", "SYN-B", "out", "0.02", "USD", "payment"],
        ["2026-01-03 10:00:00", "SYN-A", "SYN-B", "in", "0.33", "CNY", "退款"],
        ["2026-01-04 10:00:00", "SYN-A", "SYN-B", "in", "-100.05", "CNY", "退款"],
        ["2026-01-05 10:00:00", "SYN-A", "SYN-B", "out", "", "CNY", "missing amount"],
        ["", "SYN-A", "SYN-B", "out", "2.34", "CNY", "missing time"],
    ])
    first = preview(client, cid, source)
    assert (first["table_summary"]["accepted"], first["table_summary"]["unresolved"]) == (3, 3)
    first_job = complete(client, cid, first)
    assert first_job["stage"] == "done" and first_job["result"]["ok"] == 3
    signed = table_bytes(["date", "account", "counterparty account", "signed amount/分", "currency"], [
        ["2026-01-06 10:00:00", "SYN-A", "SYN-B", "-234", "CNY"],
        ["2026-01-07 10:00:00", "SYN-A", "SYN-B", "345", "CNY"],
    ])
    second = complete(client, cid, preview(client, cid, signed, "synthetic-signed.csv"))
    assert second["stage"] == "done" and second["result"]["ok"] == 2
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert len(flows) == 5
    assert all(row["trade_time"] and row["amount"] != "0.00" for row in flows)
    ids = [int(row["id"]) for row in flows]
    tracked = client.post("/api/track", json={"case_id": cid, "flow_ids": ids})
    assert tracked.status_code == 200, tracked.text
    response = client.get("/api/graph", params={"case_id": cid})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert sorted(graph["tracked_ids"]) == sorted(ids)
    assert sorted(flow_id for edge in graph["edges"] for flow_id in edge["ids"]) == sorted(ids)
    edges = {(edge["source"], edge["target"]): edge for edge in graph["edges"]}
    assert set(edges) == {("SYN-A", "SYN-B"), ("SYN-B", "SYN-A")}
    outward = edges[("SYN-A", "SYN-B")]
    assert outward["cnt"] == 3 and outward["total"] is None
    assert outward["amount_totals"] == [
        {"currency": "CNY", "amount": "102.39"}, {"currency": "USD", "amount": "0.02"}]
    reverse = edges[("SYN-B", "SYN-A")]
    assert (reverse["cnt"], reverse["total"], reverse["amount_totals"]) == (
        2, "3.78", [{"currency": "CNY", "amount": "3.78"}])
    records = client.get(f"/api/v1/cases/{cid}/sources/{first['import_id']}/report").json()["items"]
    assert [record["reason_code"] for record in records[3:]] == ["negative_amount", "missing_amount", "missing_time"]
    assert all(record["disposition"] == "unresolved" and record["flow_id"] is None for record in records[3:])
