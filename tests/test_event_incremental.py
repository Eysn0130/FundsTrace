"""Incremental source/reference closures equal complete event replay."""
from decimal import Decimal
import hashlib
import json

import pytest

from app import event_resolution as events
from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted


def pair(reference, amount="100.00"):
    return [transaction(own, **{"交易流水号": reference + "-" + own,
        "跨账簿事件参考号": reference, "交易金额": amount}) for own in ("SYN-A", "SYN-B")]


def ingest(client, cid, reference, rows=None):
    return accepted(client, cid, [(reference + ".csv", csv_rows(pair(reference) if rows is None else rows))], scope_bank=None)[1]


def projection(database, cid):
    return {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid])
            for name in ("resolved_events", "event_members", "event_state")}


def assert_full_equal(database, cid):
    before = projection(database, cid)
    with database.transaction():
        events.rebuild(cid, force_full=True)
    assert projection(database, cid) == before


class Calls:
    def __init__(self, monkeypatch):
        self.sources, self.groups = [], []
        observe, resolve = events._observations, events.resolve

        def read(cid, source_ids=None):
            self.sources.append(source_ids)
            return observe(cid, source_ids)

        def solve(rows):
            self.groups.append([json.loads(row["evidence"])["reference"] for row in rows])
            return resolve(rows)

        monkeypatch.setattr(events, "_observations", read)
        monkeypatch.setattr(events, "resolve", solve)

    def clear(self):
        self.sources.clear()
        self.groups.clear()

    def only(self, references):
        assert self.sources and all(source is not None for source in self.sources)
        assert len(self.groups) == 1 and set(self.groups[0]) == set(references)


def test_append_replay_conflict_and_batch_delete_recompute_only_affected_groups(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic incremental independent references")
    initial = accepted(client, cid, [(f"SYN-E{i}.csv", csv_rows(pair(f"SYN-E{i}"))) for i in range(4)], scope_bank=None)[1]
    identity_before = database.q_raw("select revision from resolution_state where case_id=?", [cid])[0]["revision"]
    calls = Calls(monkeypatch)
    ingest(client, cid, "SYN-NEW")
    calls.only({"SYN-NEW"})
    assert len(calls.groups[0]) == 2
    assert database.q_raw("select revision from resolution_state where case_id=?", [cid])[0]["revision"] != identity_before
    assert events.summary(cid)["confirmed_events"] == 5
    assert_full_equal(database, cid)

    calls.clear()
    replay = ingest(client, cid, "SYN-E2")
    calls.only({"SYN-E2"})
    assert replay["ok"] == 0 and len(calls.groups[0]) == 4
    assert_full_equal(database, cid)

    calls.clear()
    contradiction = ingest(client, cid, "SYN-CONFLICT", [pair("SYN-E2", "101.00")[0]])
    calls.only({"SYN-E2"})
    assert events.summary(cid)["confirmed_events"] == 4
    assert_full_equal(database, cid)

    calls.clear()
    assert client.delete(f"/api/batches/{contradiction['batch_id']}", params={"case_id": cid}).status_code == 200
    calls.only({"SYN-E2"})
    assert events.summary(cid)["confirmed_events"] == 5
    assert_full_equal(database, cid)

    calls.clear()
    assert client.delete(f"/api/batches/{initial['batch_id']}", params={"case_id": cid}).status_code == 200
    # The replay still supports E2; only the other three original groups vanish.
    assert all(source is not None for source in calls.sources)
    calls.only({"SYN-E2"})
    assert events.summary(cid)["confirmed_events"] == 2
    assert events.summary(cid)["reference_observations"] == 4
    assert_full_equal(database, cid)
    calls.clear()
    assert client.delete(f"/api/batches/{replay['batch_id']}", params={"case_id": cid}).status_code == 200
    assert len(calls.groups) == 1 and calls.groups[0] == []
    assert events.summary(cid)["confirmed_events"] == 1
    assert events.summary(cid)["reference_observations"] == 2
    assert_full_equal(database, cid)


def test_unresolved_claim_and_empty_batch_deletion_withdraw_and_restore_incrementally(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic unresolved incremental reference")
    ingest(client, cid, "SYN-E1")
    calls = Calls(monkeypatch)
    bad = pair("SYN-E1")[0] | {"交易金额": "not-an-amount"}
    receipt = ingest(client, cid, "SYN-UNRESOLVED", [bad])
    calls.only({"SYN-E1"})
    assert receipt["ok"] == 0
    assert events.summary(cid)["unresolved_reference_observations"] == 1
    assert events.summary(cid)["confirmed_events"] == 0
    assert_full_equal(database, cid)
    calls.clear()
    assert client.delete(f"/api/batches/{receipt['batch_id']}", params={"case_id": cid}).status_code == 200
    calls.only({"SYN-E1"})
    assert events.summary(cid)["confirmed_events"] == 1
    assert_full_equal(database, cid)


def bridge_source(database, cid, targets):
    """Synthetic stored declarations model a legacy/shared-flow association.

    This isolates the multi-reference graph contract without asking the public
    occurrence importer to invent another link for different physical bytes.
    """
    source_id = "synthetic-bridge"
    first_source = targets[0][0]
    database.x("insert into import_sources select ?,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,origin,row_kind,imported_at "
               "from import_sources where case_id=? and source_id=?", [source_id, cid, first_source])
    database.x("insert into evidence_sources values (?,?,null,true)", [cid, source_id])
    for number, (original_source, flow_id, reference) in enumerate(targets, 1):
        record = database.q_raw("select r.* from import_records r join flow_sources fs on fs.case_id=r.case_id "
                               "and fs.source_id=r.source_id and fs.source_row=r.record_no "
                               "where r.case_id=? and r.source_id=? and fs.flow_id=?", [cid, original_source, flow_id])[0]
        details = json.loads(record["details_json"])
        evidence = json.loads(details[events.DETAIL_FIELD])
        evidence["reference"] = reference
        details[events.DETAIL_FIELD] = events._json(evidence)
        database.x("insert into import_records values (?,?,?,?,?,?,'duplicate','transaction_valid',?)",
                   [cid, source_id, number, record["member_path"], record["source_row"], record["record_key"], json.dumps(details)])
        database.x("insert into flow_sources values (?,?,?,?,'duplicate')", [cid, source_id, number, flow_id])
    return source_id


def test_reference_flow_closure_spans_multiple_groups_and_retracts_after_bridge_removal(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic connected event references")
    receipts = [ingest(client, cid, f"SYN-E{i}") for i in range(4)]
    ids = [database.q_raw("select flow_id from flow_sources where case_id=? and source_id=? order by source_row", [cid, receipt["source_id"]])
           for receipt in receipts]
    calls = Calls(monkeypatch)
    with database.transaction():
        bridge = bridge_source(database, cid, [(receipts[0]["source_id"], ids[0][1]["flow_id"], "SYN-E1"),
                                               (receipts[1]["source_id"], ids[1][1]["flow_id"], "SYN-E2")])
        events.rebuild(cid, changed_sources=[bridge])
    calls.only({"SYN-E0", "SYN-E1", "SYN-E2"})
    assert len(calls.groups[0]) == 8
    assert events.summary(cid)["confirmed_events"] == 1
    assert {row["status"] for row in events.members(cid, [row["flow_id"] for pair_ids in ids[:3] for row in pair_ids])} == {"conflict"}
    assert_full_equal(database, cid)
    calls.clear()
    with database.transaction():
        database.x("update evidence_sources set active=false where case_id=? and source_id=?", [cid, bridge])
        events.rebuild(cid, changed_sources=[bridge])
    calls.only({"SYN-E0", "SYN-E1", "SYN-E2"})
    assert events.summary(cid)["confirmed_events"] == 4
    assert_full_equal(database, cid)


@pytest.mark.parametrize("change", ["identity", "effective"])
def test_changed_existing_identity_or_cancellation_effect_uses_full_fallback(client, database, make_case, monkeypatch, change):
    cid = make_case("Synthetic changing event inputs")
    ingest(client, cid, "SYN-E1")
    ingest(client, cid, "SYN-E2")
    first = database.q_raw("select min(id) id from flows where case_id=?", [cid])[0]["id"]
    calls = Calls(monkeypatch)
    with database.transaction():
        if change == "identity":
            database.x("update resolved_endpoints set state='conflict' where case_id=? and flow_id=? and role='self'", [cid, first])
        else:
            # Simulate the existing cancellation view withdrawing a retained
            # ledger row: neither source bytes nor its flow link are deleted.
            database.x(f"create or replace view source_effective_flows as select * from flows where id<>{int(first)}")
        events.rebuild(cid, changed_sources=[])
    assert calls.sources == [[], None]
    assert set(calls.groups[-1]) == {"SYN-E1", "SYN-E2"}
    assert events.summary(cid)["confirmed_events"] == 1
    if change == "effective":
        assert events.summary(cid)["excluded_reference_entries"] == 1
        assert events.summary(cid)["unknown_entries"] >= 0
    assert_full_equal(database, cid)


@pytest.mark.parametrize("damage", ["version", "missing-row", "reference-key", "source-manifest"])
def test_disposable_cache_damage_falls_back_to_original_journal(client, database, make_case, monkeypatch, damage):
    cid = make_case("Synthetic invalid event cache")
    receipt = ingest(client, cid, "SYN-E1")
    before = projection(database, cid)
    calls = Calls(monkeypatch)
    with database.transaction():
        if damage == "version":
            database.x("update event_projection_cache_state set version='incompatible' where case_id=?", [cid])
        elif damage == "missing-row":
            database.x("delete from event_observation_cache where case_id=? and record_no=1", [cid])
        elif damage == "source-manifest":
            database.x("update event_projection_cache_state set sources_json='invalid' where case_id=?", [cid])
        else:
            database.x("update event_observation_cache set reference_key='invalid' where case_id=?", [cid])
        events.rebuild(cid, changed_sources=[])
    assert None in calls.sources
    assert projection(database, cid) == before
    assert database.q_raw("select count(*) n from event_observation_cache where case_id=? and source_id=?", [cid, receipt["source_id"]])[0]["n"] == 2


def test_incremental_failure_rolls_back_cache_projection_and_analysis_generation(client, database, make_case, monkeypatch):
    from test_folder_evidence import complete, preview
    cid = make_case("Synthetic incremental atomicity")
    ingest(client, cid, "SYN-E1")
    tables = ("flows", "import_sources", "import_records", "flow_sources", "evidence_sources", "resolved_events", "event_members", "event_state",
              "event_observation_cache", "event_projection_cache_state", "analysis_state")
    before = {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid]) for name in tables}
    view = preview(client, cid, [("new.csv", csv_rows(pair("SYN-NEW")))], scope_bank=None)
    write = database.insert_dataframe

    def fail_after_write(table, frame, columns):
        result = write(table, frame, columns)
        if table == "event_members":
            raise RuntimeError("Synthetic incremental projection failure")
        return result

    monkeypatch.setattr(database, "insert_dataframe", fail_after_write)
    assert complete(client, cid, view)["stage"] == "error"
    assert {name: database.q_raw(f"select * from {name} where case_id=? order by all", [cid]) for name in tables} == before


def test_streamed_revision_is_identical_to_original_canonical_protocol(client, database, make_case):
    cid = make_case("Synthetic canonical event revision")
    for reference in ("SYN-Z", "SYN-A", "SYN-中文"):
        ingest(client, cid, reference)
    result = events.summary(cid)
    revision = result.pop("revision")
    resolved, memberships = events.resolve(events._observations(cid))
    canonical = [{key: str(value) if isinstance(value, Decimal) or hasattr(value, "isoformat") else value
                  for key, value in event.items()} for event in resolved]
    assert revision == hashlib.sha256(events._json([result, canonical, memberships]).encode()).hexdigest()


@pytest.mark.parametrize("order", [("SYN-A", "SYN-B"), ("SYN-B", "SYN-A")])
def test_opposite_side_arrival_order_matches_full_rebuild(client, database, make_case, monkeypatch, order):
    cid = make_case("Synthetic incremental arrival order")
    by_own = {row["本方账号"]: row for row in pair("SYN-E1")}
    ingest(client, cid, "first", [by_own[order[0]]])
    assert events.summary(cid)["confirmed_events"] == 0
    calls = Calls(monkeypatch)
    ingest(client, cid, "second", [by_own[order[1]]])
    calls.only({"SYN-E1"})
    assert events.summary(cid)["confirmed_events"] == 1
    assert_full_equal(database, cid)


@pytest.mark.parametrize("operation", ["append", "deactivate"])
def test_missing_source_delta_hint_falls_back_instead_of_skipping_declarations(client, database, make_case, monkeypatch, operation):
    cid = make_case("Synthetic incomplete event delta hint")
    first = ingest(client, cid, "SYN-E1")
    calls, rebuild = Calls(monkeypatch), events.rebuild

    def missing_hint(case_id, **kwargs):
        return rebuild(case_id, **{**kwargs, "changed_sources": []})

    monkeypatch.setattr(events, "rebuild", missing_hint)
    if operation == "append":
        ingest(client, cid, "SYN-E2")
        assert events.summary(cid)["confirmed_events"] == 2
    else:
        assert client.delete(f"/api/batches/{first['batch_id']}", params={"case_id": cid}).status_code == 200
        assert events.summary(cid)["confirmed_events"] == 0
    assert calls.sources == [None]
    assert_full_equal(database, cid)


def test_same_reference_in_other_case_is_never_in_dirty_closure(client, database, make_case, monkeypatch):
    cid, other = make_case("Synthetic scoped delta"), make_case("Synthetic unrelated delta")
    ingest(client, cid, "SYN-E1")
    ingest(client, other, "SYN-E1", pair("SYN-E1", "999.00"))
    before = projection(database, other)
    calls = Calls(monkeypatch)
    ingest(client, cid, "conflict", [pair("SYN-E1", "101.00")[0]])
    calls.only({"SYN-E1"})
    assert len(calls.groups[0]) == 3
    assert projection(database, other) == before
    assert events.summary(other)["confirmed_amount_totals"] == [{"currency": "CNY", "amount": "999.00"}]
    assert_full_equal(database, cid)


def precise_nullable_observations(client, database, make_case):
    """Public source journals with valid adjacent BIGINT observation identities.

    Synthetic renumbering avoids allocating quadrillions of sequence values.
    Source links are renumbered together and identity/event projections rebuilt;
    no event decision or source evidence is invented by this fixture.
    """
    from app import evidence_resolution

    cid = make_case("Synthetic exact nullable event identifiers")
    first = ingest(client, cid, "SYN-E1")
    unresolved = pair("SYN-E2")[1] | {"交易金额": "not-an-amount"}
    second_rows = [pair("SYN-E2")[0], unresolved]
    second = ingest(client, cid, "SYN-E2", second_rows)
    high = 9_007_199_254_740_992
    with database.transaction():
        for receipt, own, new_id in ((first, "SYN-B", high - 1),
                                     (first, "SYN-A", high),
                                     (second, "SYN-A", high + 1)):
            old_id = database.q_raw("select f.id from flows f join flow_sources s "
                                   "on s.case_id=f.case_id and s.flow_id=f.id "
                                   "where f.case_id=? and s.source_id=? and f.self_account=?",
                                   [cid, receipt["source_id"], own])[0]["id"]
            database.x("update flows set id=? where case_id=? and id=?", [new_id, cid, old_id])
            database.x("update flow_sources set flow_id=? where case_id=? and flow_id=?", [new_id, cid, old_id])
        evidence_resolution.rebuild(cid)
        events.rebuild(cid, force_full=True)
        # Start each experiment with an exact disposable cache, independently
        # of the full builder's use of the same writer being exercised below.
        database.x("""update event_observation_cache c set flow_id=s.flow_id
            from flow_sources s where c.case_id=? and s.case_id=c.case_id
                and s.source_id=c.source_id and s.source_row=c.record_no""", [cid])
    assert events.summary(cid)["confirmed_events"] == 1
    source_ids = database.q_raw("select flow_id from flow_sources where case_id=? and source_id=?",
                               [cid, second["source_id"]])
    assert source_ids == [{"flow_id": high + 1}]
    return cid, first, second, second_rows, high


def test_nullable_bigint_cache_append_preserves_source_linkage_and_unrelated_confirmed_event(client, database, make_case):
    cid, _, _, second_rows, high = precise_nullable_observations(client, database, make_case)
    replay = ingest(client, cid, "SYN-E2", second_rows)
    assert replay["ok"] == 0
    assert events.summary(cid)["confirmed_events"] == 1
    assert_full_equal(database, cid)
    linked = database.q_raw("""select c.flow_id cached_id,s.flow_id source_id
        from event_observation_cache c left join flow_sources s
          on s.case_id=c.case_id and s.source_id=c.source_id and s.source_row=c.record_no
        where c.case_id=? and c.source_id=? order by c.record_no""", [cid, replay["source_id"]])
    assert linked == [{"cached_id": high + 1, "source_id": high + 1},
                      {"cached_id": None, "source_id": None}]


def test_same_fact_wrong_cached_flow_link_is_rejected_before_reference_replay(client, database, make_case, monkeypatch):
    cid, _, second, _, high = precise_nullable_observations(client, database, make_case)
    # The neighbouring observation has identical effective facts and endpoint
    # types, but belongs to the separate confirmed reference. Facts alone
    # cannot validate the cached source-row-to-flow binding.
    with database.transaction():
        database.x("update event_observation_cache set flow_id=? "
                   "where case_id=? and source_id=? and flow_id=?",
                   [high, cid, second["source_id"], high + 1])
    calls = Calls(monkeypatch)
    replay = ingest(client, cid, "SYN-E1")
    assert replay["ok"] == 0
    assert events.summary(cid)["confirmed_events"] == 1
    assert None in calls.sources
    assert_full_equal(database, cid)
