"""Public batch withdrawal retains observations with another active source."""
import pytest

from app import evidence_resolution, event_resolution
from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted
from test_export_integrity import workbook, records


def setup_pair(client, cid):
    inputs = [("sender.csv", csv_rows([transaction("SYN-A")])),
              ("recipient.csv", csv_rows([transaction("SYN-B")]))]
    _, first = accepted(client, cid, inputs, scope_bank=None)
    _, second = accepted(client, cid, list(reversed(inputs)), scope_bank=None, zipped=True)
    assert (first["ok"], second["ok"], second["rows_dup"]) == (2, 0, 2)
    rows = client.get("/api/flows", params={"case_id": cid, "granularity": "observations"}).json()["rows"]
    ids = [row["id"] for row in rows]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": ids}).status_code == 200
    return first, second, ids


@pytest.mark.parametrize("first_removed", [True, False])
def test_last_support_controls_observations_graph_events_and_package(client, database, make_case, first_removed):
    cid = make_case("Synthetic independently supported observation")
    first, second, ids = setup_pair(client, cid)
    removed, kept = (first, second) if first_removed else (second, first)
    receipt_url = f"/api/v1/cases/{cid}/imports/{removed['source_id']}"
    source_url = f"/api/v1/cases/{cid}/sources/{removed['source_id']}/file"
    historical_receipt = client.get(receipt_url).json()["result"]
    original_file = client.get(source_url).content
    original = database.q_raw("select id,trade_time,amount,balance,trade_no,self_account,payer_account,payee_account from flows where case_id=? order by id", [cid])
    assert client.delete(f"/api/batches/{removed['batch_id']}", params={"case_id": cid}).status_code == 200
    assert database.q_raw("select id,trade_time,amount,balance,trade_no,self_account,payer_account,payee_account from flows where case_id=? order by id", [cid]) == original
    assert database.q_raw("select distinct batch_id from flows where case_id=?", [cid]) == [{"batch_id": int(kept["batch_id"])}]
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert set(map(str, graph["tracked_ids"])) == set(map(str, ids))
    assert event_resolution.summary(cid)["confirmed_events"] == 1
    assert graph["funds"]["totals"][0]["amount"] == "100.00"
    assert database.q_raw("select count(*) n from flow_sources where case_id=?", [cid]) == [{"n": 2}]
    assert database.q_raw("select count(*) n from import_records where case_id=? and disposition='removed'", [cid]) == [{"n": 2}]
    assert client.get(receipt_url).json()["result"] == historical_receipt
    assert client.get(source_url).content == original_file
    package = client.get(f"/api/cases/{cid}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    other = restored.json()["case_id"]
    assert event_resolution.summary(other)["confirmed_events"] == 1
    assert client.delete(f"/api/batches/{kept['batch_id']}", params={"case_id": cid}).status_code == 200
    assert client.get("/api/flows", params={"case_id": cid, "granularity": "observations"}).json()["total"] == 0
    assert client.get("/api/graph", params={"case_id": cid}).json()["tracked_ids"] == []
    assert event_resolution.summary(cid)["confirmed_events"] == 0
    assert event_resolution.summary(other)["confirmed_events"] == 1


def test_withdrawal_failure_rolls_back_support_ownership_and_graph(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic atomic support withdrawal")
    first, _, _ = setup_pair(client, cid)
    tables = ("flows", "flow_sources", "import_records", "evidence_sources", "batches", "tracked_flows")
    def snapshot():
        return {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid]) for name in tables}
    before = snapshot()
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic rebuild failure")
    monkeypatch.setattr(evidence_resolution, "rebuild", fail)
    with pytest.raises(RuntimeError, match="synthetic rebuild failure"):
        client.delete(f"/api/batches/{first['batch_id']}", params={"case_id": cid})
    assert snapshot() == before


def test_mixed_batch_and_cross_case_with_three_supports(client, database, make_case):
    cid, other = make_case("Synthetic mixed support"), make_case("Synthetic unrelated case")
    shared = [("sender.csv", csv_rows([transaction("SYN-A")])),
              ("recipient.csv", csv_rows([transaction("SYN-B")]))]
    unique = transaction("SYN-A", **{"交易流水号": "SYN-UNIQUE", "跨账簿事件参考号": "SYN-E2", "交易金额": "1.01"})
    _, first = accepted(client, cid, [*shared, ("unique.csv", csv_rows([unique]))], scope_bank=None)
    _, second = accepted(client, cid, shared, scope_bank=None)
    _, third = accepted(client, cid, shared, scope_bank=None, zipped=True)
    setup_pair(client, other)
    rows = client.get("/api/flows", params={"case_id": cid, "granularity": "observations"}).json()["rows"]
    shared_ids = {row["id"] for row in rows if row["trade_no"] != "SYN-UNIQUE"}
    assert len(rows) == 3 and len(shared_ids) == 2
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in rows]}).status_code == 200
    other_before = client.get("/api/graph", params={"case_id": other}).json()
    assert client.delete(f"/api/batches/{first['batch_id']}", params={"case_id": other}).status_code == 404
    before = client.get("/api/graph", params={"case_id": cid}).json()
    assert client.delete(f"/api/batches/{first['batch_id']}", params={"case_id": cid}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["analysis_revision"] != before["analysis_revision"]
    assert client.get("/api/export/tracked.xlsx", params={"case_id": cid, "expected_revision": before["analysis_revision"]}).status_code == 409
    assert set(map(str, graph["tracked_ids"])) == set(map(str, shared_ids))
    book = workbook(client, cid)
    assert {row["原流水ID"] for row in records(book["原始账簿观察"])} == set(map(str, shared_ids))
    book.close()
    for receipt, remaining in ((second, 2), (third, 0)):
        assert client.delete(f"/api/batches/{receipt['batch_id']}", params={"case_id": cid}).status_code == 200
        assert client.get("/api/flows", params={"case_id": cid, "granularity": "observations"}).json()["total"] == remaining
    assert client.get("/api/graph", params={"case_id": other}).json() == other_before
