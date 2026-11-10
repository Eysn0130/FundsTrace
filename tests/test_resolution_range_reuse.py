"""Immutable rebuild scope reuses bounded keys/bounds, never evidence rows."""
import json
from collections import Counter

import pytest

from app import evidence_resolution as e, import_staging
from test_folder_evidence import accepted, account, assert_unresolved_funds, complete, preview, table, transaction
from test_evidence_replay import list_load, decisions
from test_resolution_replay_filter import ordinary
from test_import_chunk_transactions import snapshot


class RangeObserver:
    """Wrap only this module's SQL facade, never DataFrame writer frames."""
    def __init__(self, database):
        self.database = database
        self.queries = []
        self.fetchall_calls = 0
        self.max_fetchmany = 0
        self.active = False

    def __getattr__(self, name):
        return getattr(self.database, name)

    def con(self):
        observer = self
        class Connection:
            def execute(self, query, parameters=None):
                if query.startswith(e._EVIDENCE_QUERY):
                    assert not observer.active, "raw query replaced an unfinished DuckDB range cursor"
                result = observer.database.con().execute(query, parameters)
                if not query.startswith(e._EVIDENCE_RANGES):
                    return result
                observer.active = True
                entry = {"case_id": parameters[0], "auxiliary": " and r.disposition='auxiliary'" in query, "rows": []}
                observer.queries.append(entry)
                class Ranges:
                    def fetchall(self):
                        observer.fetchall_calls += 1
                        rows = result.fetchall(); entry["rows"].extend(rows); observer.active = False
                        return rows
                    def fetchmany(self, count):
                        observer.max_fetchmany = max(observer.max_fetchmany, count)
                        rows = result.fetchmany(count); entry["rows"].extend(rows)
                        if not rows: observer.active = False
                        return rows
                return Ranges()
        return Connection()


def observe(database, monkeypatch):
    observer = RangeObserver(database)
    monkeypatch.setattr(e, "db", observer)
    return observer


def assert_two_scans(observer, cid):
    assert Counter((row["case_id"], row["auxiliary"]) for row in observer.queries) == {(cid, False): 1, (cid, True): 1}
    assert observer.fetchall_calls == 0
    assert observer.max_fetchmany <= import_staging.PLAN_CHUNK


@pytest.mark.parametrize("typed", [False, True], ids=["empty-ranges", "nonempty-ranges"])
def test_public_import_executes_each_range_sql_once(client, database, make_case, monkeypatch, typed):
    cid = make_case("Synthetic bounded range reuse")
    observer = observe(database, monkeypatch)
    if typed:
        accepted(client, cid, [("account.csv", table("account", [account()])),
                              ("tx.csv", table("transaction", [transaction()]))])
    else:
        ordinary(client, cid, "plain.csv", [["SYN-PLAIN", "2026-01-01 10:00:00", "000SYN-A", "000SYN-B", "1.01", "CNY", "synthetic"]])
    assert_two_scans(observer, cid)
    assert bool(sum(len(row["rows"]) for row in observer.queries)) is typed
    rows = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    assert rows["total"] == 1
    assert rows["items"][0]["amount"] == ("10.25" if typed else "1.01")


def originals(database, cid):
    return {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid])
            for name in ("flows", "import_records", "flow_sources", "import_sources", "evidence_sources")}


def compare_to_list_rebuild(database, cid, monkeypatch, observer):
    before = originals(database, cid)
    with database.transaction():
        with monkeypatch.context() as reference:
            reference.setattr(e, "_load", lambda target: list_load(database, target))
            expected = e.rebuild(cid)
        stored = decisions(database, cid)
        database.x("delete from resolution_state where case_id=?", [cid])
        database.x("delete from resolution_revisions where case_id=? and revision=?", [cid, expected["revision"]])
        observer.queries.clear(); observer.fetchall_calls = 0
        actual = e.rebuild(cid)
        assert actual == expected and decisions(database, cid) == stored
        assert_two_scans(observer, cid)
    assert originals(database, cid) == before


def test_sparse_multiple_sources_spool_bounded_ranges_and_keep_canonical_projection(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic sparse source range buckets")
    expected_facts, expected_links = {}, set()
    for source in range(2):
        _, receipt = accepted(client, cid, [(f"tx-{source}.csv", table("transaction", [
            transaction(**{"交易流水号": f"SYN-{source}-{n}", "交易金额": "0.01" if n % 2 else "90071992547409.91",
                           "交易币种": "USD" if n % 2 else "CNY"}) for n in range(6)]))])
        for n in range(6):
            trade = f"SYN-{source}-{n}"
            expected_facts[trade] = ("0.01", "USD") if n % 2 else ("90071992547409.91", "CNY")
            expected_links.add((receipt["source_id"], trade, n + 2))
    # Legal old journals can omit typed metadata for some physical positions.
    # Keep sparse eligible buckets without changing positions, facts or links.
    frozen = database.q_raw("select source_id,record_no,disposition,details_json from import_records where case_id=? order by source_id,record_no", [cid])
    eligible = []
    for row in frozen:
        notes = json.loads(row["details_json"])
        if row["disposition"] != "auxiliary" and row["record_no"] % 3 != 1:
            notes.pop(e.DETAIL_FIELD, None)
            database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
                       [json.dumps(notes, ensure_ascii=False), cid, row["source_id"], row["record_no"]])
        elif notes.get(e.DETAIL_FIELD):
            eligible.append((row["source_id"], row["record_no"], row["disposition"] == "auxiliary"))
    monkeypatch.setattr(e, "REPLAY_CHUNK", 2)
    monkeypatch.setattr(import_staging, "PLAN_CHUNK", 2)
    observer = observe(database, monkeypatch)
    owners = []
    enter = e._ResolutionStaging.__enter__
    def entered(owner):
        owners.append(owner)
        return enter(owner)
    monkeypatch.setattr(e._ResolutionStaging, "__enter__", entered)
    compare_to_list_rebuild(database, cid, monkeypatch, observer)
    for query in observer.queries:
        groups = {}
        for source, number, auxiliary in eligible:
            if query["auxiliary"] and not auxiliary: continue
            groups.setdefault((source, number // 2), []).append(number)
        expected = sorted((source, min(numbers), max(numbers)) for (source, _), numbers in groups.items())
        assert query["rows"] == expected
    assert sum(len(query["rows"]) for query in observer.queries) > 2
    assert owners and all(owner.owner._closed and owner.peak_range_batch <= 2 for owner in owners)
    assert owners[-1].range_query_count == 2 and owners[-1].range_row_count > 2
    # Fixed source expectations are separate from the list replay comparator.
    rows = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    assert rows["total"] == 12
    assert {row["trade_no"]: (row["amount"], row["currency"]) for row in rows["items"]} == expected_facts
    links = database.q_raw("""select s.source_id,f.trade_no,r.source_row from flows f
        join flow_sources s on s.case_id=f.case_id and s.flow_id=f.id
        join import_records r on r.case_id=s.case_id and r.source_id=s.source_id and r.record_no=s.source_row
        where f.case_id=?""", [cid])
    assert {(row["source_id"], row["trade_no"], row["source_row"]) for row in links} == expected_links
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats["amount_totals"] == [{"currency": "CNY", "amount": "540431955284459.46"},
                                      {"currency": "USD", "amount": "0.06"}]
    assert_unresolved_funds(stats["funds"], 12, [("CNY", "540431955284459.46"), ("USD", "0.06")])


def test_supplier_is_fresh_after_scope_and_later_source_write(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic supplier lifetime")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    supplier = e._load(cid)
    load = e._load
    monkeypatch.setattr(e, "_load", lambda target: supplier if target == cid else load(target))
    observer = observe(database, monkeypatch)
    with database.transaction():
        previous = e.rebuild(cid)
    assert_two_scans(observer, cid)
    assert supplier._range_state is None and supplier.staging is None and supplier._validated_values is None
    before = list(supplier)
    saved = database.q_raw("select source_id,record_no,details_json from import_records where case_id=? and disposition='imported'", [cid])[0]
    notes = json.loads(saved["details_json"]); notes.pop(e.DETAIL_FIELD)
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [json.dumps(notes), cid, saved["source_id"], saved["record_no"]])
    after = list(supplier)
    assert len(after) < len(before) and after == list_load(database, cid)
    observer.queries.clear(); observer.fetchall_calls = 0
    with database.transaction():
        current = e.rebuild(cid)
    assert current["revision"] != previous["revision"]
    assert_two_scans(observer, cid)
    assert supplier._range_state is None and supplier.staging is None


def test_early_stop_keeps_scope_ranges_replayable_then_discards_them(client, database, make_case):
    cid = make_case("Synthetic partial replay lifetime")
    accepted(client, cid, [("tx.csv", table("transaction", [transaction()]))])
    supplier = e._load(cid)
    with database.transaction(), e._ResolutionStaging() as staging:
        with supplier._fixed_ranges(staging):
            iterator = iter(supplier)
            first = next(iterator); iterator.close()
            complete_rows = list(supplier)
            assert first == complete_rows[0]
            assert staging.range_query_count == 1
            assert staging.connection.execute("select count(*) from resolution_ranges").fetchone()[0] > 0
        assert supplier._range_state is None and supplier.staging is None
        assert staging.connection.execute("select count(*) from resolution_ranges").fetchone()[0] == 0
    assert staging.owner._closed


def test_range_fill_failure_rolls_back_and_next_public_rebuild_starts_fresh(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic range spool rollback")
    accepted(client, cid, [("old.csv", table("transaction", [transaction()]))])
    view = preview(client, cid, [("new.csv", table("transaction", [transaction(**{"交易流水号": "SYN-FAIL"})]))])
    before = snapshot(database, cid)
    owners = []
    original = e._ResolutionStaging.stage_ranges
    def fail(staging, *args):
        owners.append(staging)
        original(staging, *args)
        raise OSError("synthetic range spool failure after complete fill")
    with monkeypatch.context() as broken:
        broken.setattr(e._ResolutionStaging, "stage_ranges", fail)
        result = complete(client, cid, view)
    assert result["stage"] == "error" and result["done"] == 0
    assert snapshot(database, cid) == before
    assert owners and all(owner.owner._closed for owner in owners)
    observer = observe(database, monkeypatch)
    accepted(client, cid, [("after.csv", table("transaction", [transaction(**{"交易流水号": "SYN-AFTER-FAIL"})]))])
    assert_two_scans(observer, cid)
    compare_to_list_rebuild(database, cid, monkeypatch, observer)
