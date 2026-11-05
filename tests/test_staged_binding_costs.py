"""Synthetic source binding keeps complete facts without full-journal rewrites."""
import copy
import hashlib
import io
import json
from decimal import Decimal

import pytest

from app import evidence_resolution, import_staging as staging, table_archive
from test_folder_evidence import account, archive, complete, preview, table, transaction
from test_table_cancellations import source


def close(plan):
    for key in ("_plan_store", "_details_pool"):
        if plan.get(key) is not None:
            plan[key].close()


def simple_plan(count=19):
    return {"members": [{"member_path": "rows.csv", "sha256": "a" * 64, "role": "table"}],
            "records": [{"record_no": number, "source_row": number + 1,
                         "member_path": "rows.csv", "disposition": "accepted",
                         "details": {"reason": "Synthetic source evidence", "source_row": str(number + 1)}}
                        for number in range(1, count + 1)], "rows": []}


def test_member_family_binding_neither_decodes_nor_rewrites_the_journal(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    expected = simple_plan()
    actual = staging.stage_plan(copy.deepcopy(expected))
    try:
        held = actual["records"][0]
        nested = held["details"]
        before = actual["_plan_store"].metrics()
        evidence_resolution.bind_families(expected)
        evidence_resolution.bind_families(actual)
        actual["_plan_store"].flush()
        after = actual["_plan_store"].metrics()
        assert after["decode_calls"] == before["decode_calls"]
        assert after["update_encoded_bytes"] == before["update_encoded_bytes"]
        print("synthetic_family_binding_cost", {"extra_decodes": after["decode_calls"] - before["decode_calls"],
                                                "rewrite_bytes": after["update_encoded_bytes"] - before["update_encoded_bytes"]})
        assert nested.copy() == expected["records"][0]["details"]
        assert copy.deepcopy(list(actual["records"])) == expected["records"]
    finally:
        close(actual)


def test_complete_binding_decodes_only_auxiliary_and_paired_financial_rows(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 4)
    original, observed = evidence_resolution.bind_occurrences, []

    def measure(plan):
        store = plan["_plan_store"]
        before = store.metrics()
        result = original(plan)
        store.flush()
        after = store.metrics()
        observed.append((after["decode_calls"] - before["decode_calls"],
                         len(plan["rows"]) + len(plan["cancelled_rows"])))
        return result

    monkeypatch.setattr(evidence_resolution, "bind_occurrences", measure)
    content = archive([
        ("SYN-REQUEST/Synthetic bank交易明细信息.csv", table("transaction", [
            transaction(**{"交易流水号": f"SYN-{number}"}) for number in range(41)])),
        ("account.csv", table("account", [account()])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}])),
    ])
    plan = table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE, _stage=True)
    try:
        assert len(observed) == 1
        decodes, financial = observed[0]
        # Two auxiliary passes and one row/record pair per financial observation.
        assert decodes <= 2 * 2 + 2 * financial
        print("synthetic_complete_binding_cost", {"decodes": decodes, "financial": financial})
        assert financial == 41
        assert all(row["_occurrence_scope"] == "Synthetic bank" for row in plan["rows"])
    finally:
        close(plan)


def test_family_rebinding_preserves_old_aliases_explicit_edits_and_deleted_fields(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    expected = simple_plan(5)
    actual = staging.stage_plan(copy.deepcopy(expected))
    store = actual["_plan_store"]
    try:
        nested = actual["records"][0]["details"]
        evidence_resolution.bind_families(expected)
        evidence_resolution.bind_families(actual)
        nested["evidence_family"] = "Synthetic explicit late edit"
        del actual["records"][1]["details"]["family_member_sha256"]
        store.flush()
        # Read beyond the cache, while retaining only the first nested alias.
        for _record in actual["records"]:
            pass
        assert actual["records"][0]["details"]["evidence_family"] == "Synthetic explicit late edit"
        assert "family_member_sha256" not in actual["records"][1]["details"]
        extra = {"member_path": "extra.csv", "sha256": "b" * 64, "role": "auxiliary"}
        for plan in (expected, actual):
            plan["members"].append(extra)
            evidence_resolution.bind_families(plan)
        assert nested.copy() == expected["records"][0]["details"]
        nested["reason"] = "Synthetic nested late update"
        expected["records"][0]["details"]["reason"] = "Synthetic nested late update"
        store.flush()
        assert copy.deepcopy(list(actual["records"])) == expected["records"]
    finally:
        close(actual)
    with pytest.raises(ValueError, match="closed"):
        nested["reason"] = "forbidden"
    with pytest.raises(ValueError, match="closed"):
        evidence_resolution.bind_families(actual)


def test_auxiliary_selection_tracks_flushed_classification_without_decoding_other_rows(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    plan = simple_plan(23)
    plan["records"][2]["disposition"] = "auxiliary"
    actual = staging.stage_plan(plan)
    store = actual["_plan_store"]
    try:
        old = actual["records"][2]
        replacement = actual["records"][17]
        old["disposition"] = "unresolved"
        replacement["disposition"] = "auxiliary"
        # The selector itself flushes pending classification changes.
        selected = list(staging.auxiliary_records(actual))
        assert [row["record_no"] for row in selected] == [18]
        del selected, old, replacement
        for _row in actual["records"]:
            pass
        before = store.metrics()
        assert [row["record_no"] for row in staging.auxiliary_records(actual)] == [18]
        after = store.metrics()
        assert after["decode_calls"] - before["decode_calls"] == 1
        assert after["peak_read_records"] <= 1
    finally:
        close(actual)
    with pytest.raises(ValueError, match="closed"):
        list(staging.auxiliary_records(actual))


def test_member_notes_keep_cache_budget_and_do_not_bind_later_appends_early(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK_BYTES", 1000)
    plan = staging.stage_plan(simple_plan(9))
    store = plan["_plan_store"]
    try:
        evidence_resolution.bind_families(plan)
        for record in plan["records"]:
            assert record["details"]["evidence_family"]
            assert store._read_bytes <= staging.PLAN_CHUNK_BYTES
        extra = {"record_no": 10, "source_row": 11, "member_path": "rows.csv",
                 "disposition": "auxiliary", "details": {"reason": "Synthetic later append"}}
        plan["records"].append(extra)
        assert plan["records"][9]["details"].copy() == extra["details"]
        copied = plan["records"][0]
        copied["details"]["evidence_family"] = "Synthetic explicit copied edit"
        del copied["details"]["family_member_sha256"]
        plan["records"].append(copied)
        assert plan["records"][10]["details"].copy() == copied["details"].copy()
        evidence_resolution.bind_families(plan)
        assert plan["records"][9]["details"]["evidence_family"]
        assert plan["records"][10]["details"]["evidence_family"] != "Synthetic explicit copied edit"
    finally:
        close(plan)


def test_paired_rows_preserve_order_sparse_sources_cancelled_entries_and_late_updates(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    plan = simple_plan(7)
    plan["rows"] = [{"_source_row": 5}, {"_source_row": 1}]
    plan["cancelled_rows"] = [{"_source_row": 7}, {"_source_row": 3}]
    actual = staging.stage_plan(plan)
    store = actual["_plan_store"]
    try:
        row = actual["rows"][0]
        row["_source_row"] = 6
        pairs = []
        for row, record in staging.row_records(actual):
            pairs.append((row["_source_row"], record["record_no"]))
            record["details"]["paired_probe"] = str(row["_source_row"])
        assert pairs == [(6, 6), (1, 1), (7, 7), (3, 3)]
        store.flush()
        assert actual["records"][5]["details"]["paired_probe"] == "6"
        actual["rows"][0]["_source_row"] = 999
        with pytest.raises(KeyError):
            list(staging.row_records(actual))
    finally:
        close(actual)


def test_public_complete_import_persists_independent_family_and_money_expectations(client, database, make_case):
    members = [
        ("SYN-REQUEST/Synthetic bank交易明细信息.csv", source([
            transaction(**{"交易流水号": "SYN-1", "交易时间": "2026-01-01 10:00:00.123456"}),
            transaction(**{"交易流水号": "SYN-2", "交易金额": "20.03", "交易币种": "USD"})])[0]),
        ("account.csv", table("account", [account()])),
        ("manifest.csv", table("task", [{"请求单号": "SYN-REQUEST", "银行名称": "Synthetic bank"}])),
    ]
    cid = make_case("Synthetic bounded source binding")
    view = preview(client, cid, [("nested.zip", archive(members))], scope_bank=None)
    assert view["table_summary"]["accepted"] == 2 and view["table_summary"]["auxiliary"] == 2
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 2
    flows = database.q_raw("select trade_no,amount,currency,trade_time from flows where case_id=? order by trade_no", [cid])
    assert [(row["trade_no"], row["amount"], row["currency"]) for row in flows] == [
        ("SYN-1", Decimal("10.25"), "CNY"), ("SYN-2", Decimal("20.03"), "USD")]
    assert flows[0]["trade_time"].microsecond == 123456
    identities = sorted((path, hashlib.sha256(content).hexdigest(), "table" if index == 0 else "auxiliary")
                        for index, (path, content) in enumerate(members))
    expected_family = hashlib.sha256(json.dumps(["logical-source-family-v1", identities], ensure_ascii=False,
                                               separators=(",", ":")).encode()).hexdigest()
    journal = database.q_raw("select member_path,details_json from import_records where case_id=? order by record_no", [cid])
    assert len(journal) == 4
    for record in journal:
        details = json.loads(record["details_json"])
        assert details["evidence_family"] == expected_family
        within = record["member_path"].removeprefix("nested.zip/")
        assert details["family_member_sha256"] == hashlib.sha256(within.encode()).hexdigest()
        if within == members[0][0]:
            assert details["occurrence_scope"] == "Synthetic bank"


def test_source_position_binding_keeps_payloads_and_live_aliases_lossless(database, monkeypatch):
    import datetime as dt
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    expected = simple_plan(9)
    expected["rows"] = [{"_source_row": n, "amount": Decimal("9007199254740993.01"),
        "trade_time": dt.datetime(2026, 1, 1, microsecond=123456), "balance": None}
        for n in (8, 1, 5)]
    expected["cancelled_rows"] = [{"_source_row": 3, "amount": Decimal("-0.01")}]
    actual = staging.stage_plan(copy.deepcopy(expected))
    store = actual["_plan_store"]
    try:
        row_alias = actual["rows"][0]
        note_alias = actual["records"][7]["details"]
        before = store._con.execute("select kind,position,payload from items order by kind,position").fetchall()
        evidence_resolution.bind_occurrences(expected)
        evidence_resolution.bind_occurrences(actual)
        store.flush()
        after = store._con.execute("select kind,position,payload from items order by kind,position").fetchall()
        # Derived source-position fields must not rewrite the complete financial
        # or source payload. This assertion fails on the original implementation.
        assert after == before
        assert row_alias["_occurrence_key"] == expected["rows"][0]["_occurrence_key"]
        assert note_alias["occurrence_scope"] == ""
        for kind in ("records", "rows", "cancelled_rows"):
            assert copy.deepcopy(list(actual[kind])) == expected[kind]
        # Ordinary late edits/deletions supersede the binding until rebinding.
        del row_alias["_occurrence_key"]
        note_alias["occurrence_scope"] = "Synthetic explicit edit"
        store.flush()
        for _ in actual["records"]:
            pass
        assert "_occurrence_key" not in actual["rows"][0]
        assert actual["records"][7]["details"]["occurrence_scope"] == "Synthetic explicit edit"
        actual["members"][0]["sha256"] = expected["members"][0]["sha256"] = "b" * 64
        for plan in (actual, expected):
            evidence_resolution.bind_occurrences(plan)
        assert note_alias["occurrence_scope"] == ""
        for kind in ("records", "rows", "cancelled_rows"):
            assert copy.deepcopy(list(actual[kind])) == expected[kind]
    finally:
        close(actual)


@pytest.mark.parametrize("binding", ['{"amount":"12.00"}', '{"occurrence_scope":5}', '{'])
def test_damaged_occurrence_overlay_fails_closed(database, binding):
    plan = staging.stage_plan(simple_plan(1))
    try:
        plan["_plan_store"]._con.execute("update items set binding=?", [binding])
        with pytest.raises(ValueError):
            list(plan["records"])
    finally:
        close(plan)


def test_public_occurrence_write_failure_closes_staging_and_preserves_case(client, database, make_case, monkeypatch):
    from test_import_chunk_transactions import snapshot
    cid = make_case("Synthetic occurrence binding write failure")
    view = preview(client, cid, [("rows.csv", source([transaction()])[0]),
        ("second.csv", source([transaction(**{"交易流水号": "SYN-SECOND"})])[0])], scope_bank=None)
    # Exercise the still-supported cache-miss parser/write failure path.
    # Cache-hit corruption and writer rollback have separate public regressions.
    from app import import_preview_cache
    import_preview_cache.discard()
    before = snapshot(database, cid)
    initialize, stores = staging.PlanStore.__init__, []
    def fail_binding(self):
        initialize(self)
        stores.append(self)
        self._con.execute("""create trigger fail_binding before update of binding on items
            begin select raise(abort, 'Synthetic binding write failure'); end""")
    monkeypatch.setattr(staging.PlanStore, "__init__", fail_binding)
    job = complete(client, cid, view)
    assert job["stage"] == "error"
    assert snapshot(database, cid) == before
    assert stores and all(store._closed for store in stores)


def test_staging_serializes_packed_notes_with_one_read_per_record(database, monkeypatch):
    pool = staging.DetailsPool()
    notes = pool.pack({f"synthetic-{n}": str(n) for n in range(80)})
    reads = []
    original = pool._decode
    def read(*args):
        reads.append(1)
        return original(*args)
    monkeypatch.setattr(pool, "_decode", read)
    store = staging.PlanStore()
    try:
        store.sequence("records").append({"record_no": 1, "details": notes})
        assert len(reads) == 1
        assert store.sequence("records")[0]["details"].copy() == {f"synthetic-{n}": str(n) for n in range(80)}
    finally:
        store.close()
        pool.close()


def test_operation_scope_binding_keeps_full_payloads_and_list_semantics(database):
    from types import SimpleNamespace
    expected = simple_plan(7)
    expected['profile'] = table_archive.EVIDENCE_PROFILE
    expected['rows'] = [{'_source_row': n, 'amount': Decimal('10.03')} for n in (2, 7)]
    evidence_resolution.bind_occurrences(expected)
    actual = staging.stage_plan(copy.deepcopy(expected))
    store = actual['_plan_store']
    try:
        before = store._con.execute('select kind,position,payload from items order by kind,position').fetchall()
        for plan in (expected, actual):
            evidence_resolution.operation_context((SimpleNamespace(attrs={'table_plan': plan}),), 'synthetic-envelope')
        store.flush()
        assert store._con.execute('select kind,position,payload from items order by kind,position').fetchall() == before
        for kind in ('records', 'rows'):
            assert copy.deepcopy(list(actual[kind])) == expected[kind]
        assert all(row['_occurrence_scope'].startswith('unscoped:') for row in actual['rows'])
    finally:
        close(actual)


def test_preview_source_samples_do_not_scan_complete_staged_journal(database, monkeypatch):
    from app.services import imports
    content = archive([('synthetic.csv', source([transaction(**{'交易流水号': f'SYN-{n}'}) for n in range(31)])[0])])
    parsed = imports._parse(io.BytesIO(content), suffix='.zip', table_profile=table_archive.EVIDENCE_PROFILE)
    # Isolate sample lookup from the separate complete evidence projection;
    # the public lifecycle regressions exercise that projection without mocks.
    monkeypatch.setattr(evidence_resolution, 'preview', lambda plan: {})
    scanned = []
    iterate = staging.StagedSequence.__iter__
    def observe(self):
        for item in iterate(self):
            if self.kind == 'records':scanned.append(item['record_no'])
            yield item
    monkeypatch.setattr(staging.StagedSequence, '__iter__', observe)
    try:
        value = imports._preview_result({'import_id':'synthetic', 'filename':'synthetic.zip', 'case_id':1}, parsed)
        assert [(row['source_record_no'], row['source_row']) for row in value['preview']] == [(n,n+1) for n in range(1,9)]
        assert scanned == []
    finally:
        imports._close_staging(parsed)


def test_summary_retains_first_ten_unresolved_source_positions(database):
    from app.services import imports
    rows = [transaction(**{'交易流水号':f'SYN-{n}', '交易金额':'-10.03' if n % 2 else '10.03'}) for n in range(31)]
    content = archive([('synthetic.csv', source(rows)[0])])
    parsed = imports._parse(io.BytesIO(content), suffix='.zip', table_profile=table_archive.EVIDENCE_PROFILE)
    try:
        summary = imports._table_summary(parsed[0].attrs['table_plan'])
        assert [r['source_row'] for r in summary['unresolved_examples']] == list(range(3,23,2))
        assert len(summary['unresolved_examples']) == 10
        assert [(r['member_path'], r['reason_code']) for r in summary['unresolved_examples']] == [('synthetic.csv', 'cancellation_action_unproven')] * 10
    finally:
        imports._close_staging(parsed)


def test_operation_context_skips_bound_payloads_but_keeps_late_edits_and_broken_links(database, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(staging, 'PLAN_CHUNK', 1)
    expected = simple_plan(31)
    expected['profile'] = table_archive.EVIDENCE_PROFILE
    expected['rows'] = [{'_source_row': n, 'amount': Decimal('100.01')} for n in range(1, 32)]
    expected['cancelled_rows'] = [{'_source_row': 31, 'amount': Decimal('100.01')}]
    actual = staging.stage_plan(copy.deepcopy(expected))
    def context(plan):
        evidence_resolution.operation_context((SimpleNamespace(attrs={'table_plan': plan}),), 'synthetic')
    try:
        for plan in (actual, expected):
            evidence_resolution.bind_occurrences(plan)
            for row, record in staging.row_records(plan):
                staging.bind_occurrence_fields(row, record, row['_occurrence_key'], 'Synthetic bank')
        store = actual['_plan_store']; store.flush()
        before = store.metrics()
        context(actual); context(expected)
        after = store.metrics()
        assert after['decode_calls'] == before['decode_calls']
        print('synthetic_bound_operation_context_cost', {'financial': 31, 'extra_decodes': after['decode_calls'] - before['decode_calls']})
        row_alias = actual['rows'][0]
        for plan in (actual, expected):
            del plan['rows'][0]['_occurrence_scope']
            plan['rows'][0]['amount'] = Decimal('200.02')
            plan['records'][1]['details']['reason'] = 'Synthetic late journal note'
            context(plan)
        assert row_alias['_occurrence_scope'].startswith('unscoped:')
        for kind in ('rows', 'records', 'cancelled_rows'):
            assert copy.deepcopy(list(actual[kind])) == expected[kind]
        actual['records'][2]['record_no'] = 999
        with pytest.raises(ValueError, match='non-consecutive'):
            context(actual)
    finally:
        close(actual)


@pytest.mark.parametrize('kind', ['cancellation', 'duplicate-bytes', 'auxiliary-only', 'omitted-bank', 'parent-child', 'nested'])
@pytest.mark.parametrize('profile', sorted(table_archive.EVIDENCE_PROFILES))
def test_runtime_scope_fusion_equals_full_decode_path(database, monkeypatch, kind, profile):
    from types import SimpleNamespace
    from test_archive_binding_once import members
    monkeypatch.setattr(staging, 'PLAN_CHUNK', 1)
    content = archive(members(kind))
    expected = table_archive.parse(io.BytesIO(content), profile=profile)
    actual = table_archive.parse(io.BytesIO(content), profile=profile, _stage=True, _operation_scopes=True)
    try:
        def context(plan):
            evidence_resolution.operation_context((SimpleNamespace(attrs={'table_plan': plan}),), 'synthetic')
        context(expected)
        store = actual['_plan_store']; store.flush()
        before = store.metrics()
        context(actual)
        assert store.metrics()['decode_calls'] == before['decode_calls']
        for field in ('rows', 'records', 'cancelled_rows', 'cancellations'):
            assert copy.deepcopy(list(actual[field])) == copy.deepcopy(expected[field])
        assert actual['stats'] == expected['stats']
        if actual['rows']:
            # Case-bound cancellation/history may later change a staged row.
            # Revalidation must still observe edits, aliases, and bad references.
            held = actual['rows'][0]
            for plan in (expected, actual):
                del plan['rows'][0]['_occurrence_scope']
                plan['rows'][0]['amount'] = Decimal('123.45')
                context(plan)
            assert held['_occurrence_scope'] == expected['rows'][0]['_occurrence_scope']
            assert copy.deepcopy(list(actual['rows'])) == expected['rows']
            actual['rows'][0]['_source_row'] = 0
            with pytest.raises(KeyError):
                context(actual)
    finally:
        close(actual)
        close(expected)


def test_merged_statistics_match_independent_complete_journal_totals(database):
    from collections import Counter
    from test_archive_binding_once import members
    content = archive([*members('nested'), ('extra.csv', source([
        transaction(**{'交易流水号':'SYN-USD', '交易币种':'USD', '交易金额':'9007199254740993.01'}),
        transaction(**{'交易时间':'invalid'}), transaction(**{'交易金额':'0'}),
        transaction(**{'交易时间':'invalid-again'})])[0])])
    plan = table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE, _stage=True)
    try:
        journal = copy.deepcopy(list(plan['records'])); rows = list(plan['rows'])
        counts = Counter(item['disposition'] for item in journal)
        stats = plan['stats']
        assert stats['total'] == len(journal) and stats['ok'] == len(rows)
        assert stats['skipped'] == len(journal) - len(rows)
        assert stats['reason_counts'] == dict(Counter(item['reason_code'] for item in journal))
        for disposition in ('accepted', 'unresolved', 'non_monetary', 'non_transaction', 'auxiliary', 'cancelled'):
            assert stats[disposition] == counts[disposition]
        assert stats['currency_totals']['USD'] == '9007199254740993.01'
        unresolved = [item for item in journal if item['disposition'] == 'unresolved'][:3]
        assert stats['bad_examples'] == [{'row':'来源记录', 'source_row':item['record_no'], 'reason':item['details']['reason']} for item in unresolved]
    finally:
        close(plan)
