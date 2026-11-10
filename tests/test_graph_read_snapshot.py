"""Complete graph facts and metadata share one committed case read."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import analysis_snapshot, main, source_ledger
from test_canvas_management import create_canvas, track
from test_flow_read_snapshot import readers  # Shared reader-failure fixture.
from test_funds_projection import event, flow


def graph_request(client, method, cid, canvas_id=0):
    binding = {"case_id": cid, "canvas_id": canvas_id}
    return (client.get("/api/graph", params=binding) if method == "GET"
            else client.post("/api/graph/view", json=binding))


def graph_value(response):
    assert response.status_code == 200, response.text
    result = response.json()
    # These API collections have no order contract.
    result["nodes"].sort(key=lambda row: row["account"])
    result["edges"].sort(key=lambda row: (row["source"], row["target"]))
    result["tracked_ids"].sort()
    result["main_accounts"].sort()
    result["focus_nodes"].sort(key=lambda row: row["account"])
    return result


def seeded(database, make_case):
    cid = make_case("Synthetic committed graph")
    flow(database, cid, 1, "90071992547409.91")
    flow(database, cid, 2, "90071992547409.91")
    flow(database, cid, 3, "2.03", "USD", payee="SYN-C")
    event(database, cid, "SYN-EVENT", [1, 2], "90071992547409.91")
    database.x("insert into tracked_flows(case_id,canvas_id,flow_id) values (?,0,1),(?,0,2),(?,0,3)", [cid] * 3)
    database.x("insert into main_accounts(case_id,account,name) values (?,'SYN-A','Synthetic old owner')", [cid])
    database.x("insert into account_ids(case_id,account,id_no) values (?,'SYN-A','SYN-ID-OLD')", [cid])
    database.x("insert into account_photos(case_id,account,fname,updated_at) values (?,'SYN-A','synthetic.png',timestamp '2026-01-01 00:00:00')", [cid])
    database.x("insert into focus_nodes values (?,'SYN-A',1.2)", [cid])
    database.x("insert into event_state values (?,'old',?)", [cid, json.dumps({"synthetic_generation": "old"})])
    analysis_snapshot.invalidate(cid)
    return cid


def change_graph(database, cid):
    database.x("update flows set amount=1.01,payee_account='SYN-D',summary='Synthetic new summary' where case_id=? and currency='CNY'", [cid])
    database.x("update resolved_events set amount=1.01,payee_account='SYN-D' where case_id=?", [cid])
    database.x("update event_members set reason='Synthetic new proof' where case_id=?", [cid])
    database.x("update account_ids set id_no='SYN-ID-NEW' where case_id=?", [cid])
    database.x("update account_photos set updated_at=timestamp '2026-02-01 00:00:00' where case_id=?", [cid])
    database.x("update focus_nodes set scale=2.4 where case_id=?", [cid])
    database.x("update event_state set revision='new',summary_json=? where case_id=?", [json.dumps({"synthetic_generation": "new"}), cid])
    analysis_snapshot.invalidate(cid)


def assert_generation(value, *, new=False):
    target, amount, generation = ("SYN-D", "1.01", "new") if new else ("SYN-B", "90071992547409.91", "old")
    assert {node["account"] for node in value["nodes"]} == {"SYN-A", target, "SYN-C"}
    edges = {(edge["source"], edge["target"]): edge for edge in value["edges"]}
    assert set(edges) == {("SYN-A", target), ("SYN-A", "SYN-C")}
    assert edges[("SYN-A", target)]["amount_totals"] == [{"currency": "CNY", "amount": amount}]
    assert edges[("SYN-A", target)]["event_summary"]["confirmed_amount_totals"] == [{"currency": "CNY", "amount": amount}]
    assert edges[("SYN-A", "SYN-C")]["amount_totals"] == [{"currency": "USD", "amount": "2.03"}]
    assert value["case_event_summary"] == {"synthetic_generation": generation}
    assert value["tracked_ids"] == [1, 2, 3]
    assert value["main_accounts"] == ["SYN-A"]
    assert value["focus_nodes"] == [{"account": "SYN-A", "scale": 2.4 if new else 1.2}]
    own = next(node for node in value["nodes"] if node["account"] == "SYN-A")
    assert own["id_no"] == ("SYN-ID-NEW" if new else "SYN-ID-OLD")
    assert own["photo_at"] == ("2026-02-01 00:00:00" if new else "2026-01-01 00:00:00")


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("rollback", [False, True], ids=["commit", "rollback"])
def test_public_graph_reads_complete_old_commit_during_writer(client, database, make_case, method, rollback):
    cid = seeded(database, make_case)
    before = graph_value(graph_request(client, method, cid))
    assert_generation(before)
    entered, release = threading.Event(), threading.Event()
    errors = []

    class SyntheticRollback(Exception):
        pass

    def write():
        try:
            with database.transaction():
                change_graph(database, cid)
                entered.set()
                assert release.wait(5)
                if rollback:
                    raise SyntheticRollback()
        except SyntheticRollback:
            pass
        except BaseException as error:
            errors.append(repr(error))

    writer = threading.Thread(target=write)
    writer.start()
    assert entered.wait(2)
    safety = threading.Timer(3, release.set)
    safety.start()
    try:
        actual = graph_value(graph_request(client, method, cid))
        assert not release.is_set(), "graph request waited for the uncommitted writer"
        assert actual == before
    finally:
        release.set(); safety.cancel(); writer.join(5)
    assert not writer.is_alive() and not errors
    after = graph_value(graph_request(client, method, cid))
    if rollback:
        assert after == before
    else:
        assert after["analysis_revision"] != before["analysis_revision"]
        assert_generation(after, new=True)


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("phase", ["group by n.account", "list(id order by id) ids", "select account,scale from focus_nodes"],
                         ids=["nodes", "edges", "metadata"])
def test_commit_between_graph_reads_keeps_complete_old_generation(client, database, make_case, monkeypatch, method, phase):
    cid = seeded(database, make_case)
    before = graph_value(graph_request(client, method, cid))
    entered, release = threading.Event(), threading.Event()
    query = database.q_raw

    def paused(sql, params=None):
        result = query(sql, params)
        if database.in_read_snapshot() and phase in sql:
            entered.set()
            assert release.wait(4)
        return result

    with ThreadPoolExecutor(1) as pool, monkeypatch.context() as patch:
        patch.setattr(database, "q_raw", paused)
        pending = pool.submit(graph_request, client, method, cid)
        try:
            assert entered.wait(2)
            with database.transaction():
                change_graph(database, cid)
        finally:
            release.set()
        actual = graph_value(pending.result(4))
    assert actual == before
    after = graph_value(graph_request(client, method, cid))
    assert after["analysis_revision"] != before["analysis_revision"]
    assert_generation(after, new=True)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_missing_generation_initializes_then_reads_complete_graph_under_lock(client, database, make_case, monkeypatch, method):
    cid = seeded(database, make_case)
    database.x("delete from analysis_state where case_id=?", [cid])
    refresh, query = source_ledger.refresh_index, database.q_raw
    initialized, reads = [], []

    def refresh_index(*args, **kwargs):
        assert not database.in_read_snapshot()
        assert database._lock._is_owned() and database._transaction_state.depth > 0
        initialized.append(True)
        return refresh(*args, **kwargs)

    def observed(sql, params=None):
        if "group by n.account" in sql:
            active = database.in_read_snapshot()
            assert active or database._lock._is_owned()
            reads.append(active)
        return query(sql, params)

    monkeypatch.setattr(source_ledger, "refresh_index", refresh_index)
    monkeypatch.setattr(database, "q_raw", observed)
    before = graph_value(graph_request(client, method, cid))
    after = graph_value(graph_request(client, method, cid))
    assert_generation(before)
    assert before == after
    assert initialized == [True] and reads == [False, True]


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_graph_unsupported_read_only_syntax_keeps_complete_locked_fallback(client, database, make_case, readers, monkeypatch, method):
    cid = seeded(database, make_case)
    before = graph_value(graph_request(client, method, cid))
    opened, fault = readers
    fault["kind"] = "unsupported"
    query, reads = database.q_raw, []

    def observed(sql, params=None):
        assert all(reader.closed for reader in opened)
        assert database._lock._is_owned() and not database.in_read_snapshot()
        reads.append(sql)
        return query(sql, params)

    monkeypatch.setattr(database, "q_raw", observed)
    assert graph_value(graph_request(client, method, cid)) == before
    assert any("group by n.account" in sql for sql in reads)
    assert any("from event_state" in sql for sql in reads)
    assert all(reader.closed for reader in opened)


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("context", ["transaction", "read_snapshot"])
def test_graph_entries_reject_nested_snapshot_or_writer(database, make_case, method, context):
    cid = seeded(database, make_case)
    with getattr(database, context)():
        with pytest.raises(RuntimeError, match="Cannot nest"):
            if method == "GET":
                main.graph(cid)
            else:
                main.graph_view(main.canvas_investigation.TimeInvestigationIn(case_id=cid))


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("deleted", ["canvas", "case"])
def test_case_or_canvas_withdrawal_during_read_keeps_binding_and_other_scopes(client, database, make_case, monkeypatch, method, deleted):
    cid = seeded(database, make_case)
    other = make_case("Synthetic other graph case")
    flow(database, other, 4, "7.07", "USD", payer="SYN-OTHER-A", payee="SYN-OTHER-B")
    track(database, other, 0, 4)
    target = create_canvas(client, cid, "Synthetic target")
    retained = create_canvas(client, cid, "Synthetic retained")
    track(database, cid, target, 1, 2)
    track(database, cid, retained, 3)
    before = graph_value(graph_request(client, method, cid, target))
    default_before = graph_value(graph_request(client, method, cid))
    retained_before = graph_value(graph_request(client, method, cid, retained))
    other_before = graph_value(graph_request(client, method, other))
    entered, release = threading.Event(), threading.Event()
    query = database.q_raw

    def paused(sql, params=None):
        result = query(sql, params)
        if database.in_read_snapshot() and "group by n.account" in sql:
            entered.set()
            assert release.wait(4)
        return result

    with ThreadPoolExecutor(1) as pool, monkeypatch.context() as patch:
        patch.setattr(database, "q_raw", paused)
        pending = pool.submit(graph_request, client, method, cid, target)
        try:
            assert entered.wait(2)
            removed = (client.delete(f"/api/cases/{cid}") if deleted == "case" else
                       client.delete(f"/api/canvases/{target}", params={"case_id": cid}))
            assert removed.status_code == 200, removed.text
        finally:
            release.set()
        assert graph_value(pending.result(4)) == before
    if deleted == "case" or method == "POST":
        assert graph_request(client, method, cid, target).status_code == 404
    else:
        empty = graph_value(graph_request(client, method, cid, target))
        assert empty["nodes"] == empty["edges"] == empty["tracked_ids"] == []
    if deleted == "canvas":
        assert graph_value(graph_request(client, method, cid)) == default_before
        assert graph_value(graph_request(client, method, cid, retained)) == retained_before
        # The legacy GET deliberately returns an empty foreign canvas; POST validates it.
        foreign = graph_request(client, method, other, retained)
        assert foreign.status_code == (200 if method == "GET" else 404)
        if method == "GET":
            assert foreign.json()["nodes"] == foreign.json()["edges"] == []
    assert graph_value(graph_request(client, method, other)) == other_before


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_source_batch_withdrawal_between_nodes_and_ledger_metadata_keeps_old_graph(client, database, make_case, monkeypatch, method):
    from test_source_ledger import imported
    cid, view, _ = imported(client, make_case)
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    ids = [row["id"] for row in page["items"]]
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": ids}).status_code == 200
    before = graph_value(graph_request(client, method, cid))
    assert before["source_ledger"] == {"count": 2, "unknown_effect_count": 1}
    assert before["transaction_count"] == 1
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"]
    entered, release = threading.Event(), threading.Event()
    query = database.q_raw

    def paused(sql, params=None):
        result = query(sql, params)
        if database.in_read_snapshot() and "group by n.account" in sql:
            entered.set()
            assert release.wait(4)
        return result

    with ThreadPoolExecutor(1) as pool, monkeypatch.context() as patch:
        patch.setattr(database, "q_raw", paused)
        pending = pool.submit(graph_request, client, method, cid)
        try:
            assert entered.wait(2)
            removed = client.delete(f"/api/batches/{receipt['batch_id']}", params={"case_id": cid})
            assert removed.status_code == 200, removed.text
        finally:
            release.set()
        assert graph_value(pending.result(4)) == before
    after = graph_value(graph_request(client, method, cid))
    assert after["analysis_revision"] != before["analysis_revision"]
    assert after["nodes"] == after["edges"] == after["tracked_ids"] == []
    assert "source_ledger" not in after
