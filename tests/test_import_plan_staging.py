"""Complete operation staging preserves facts without cumulative Python plans."""
import copy
import datetime as dt
import io
import gc
import weakref
import time
from decimal import Decimal

import pytest

from app import importer, import_staging as staging, import_validation, table_archive, table_cancellations
from app.services import imports
from test_folder_evidence import archive, complete, preview, table, transaction
from test_table_cancellations import round_trip, source


def close_plan(plan):
    for key in ("_plan_store", "_details_pool"):
        if plan.get(key) is not None:
            plan[key].close()


@pytest.mark.parametrize("chunk", [1, 2, 128])
def test_staged_archive_matches_every_in_memory_fact_and_late_update(database, monkeypatch, chunk):
    monkeypatch.setattr(staging, "PLAN_CHUNK", chunk)
    raw = source(round_trip())[0]
    content = archive([("first.csv", raw), ("nested.zip", archive([("second.csv", raw)]))])
    expected = table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE)
    actual = table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE, _stage=True)
    try:
        for key in ("records", "rows", "cancelled_rows", "cancellations"):
            assert copy.deepcopy(list(actual[key])) == copy.deepcopy(expected[key])
        for key in ("members", "documents", "mapping_evidence", "stats", "_member_families"):
            assert actual[key] == expected[key]
        held = actual["records"][0]
        for record in actual["records"]:
            record["details"]["staged_probe"] = str(record["record_no"])
        held["details"].update(staged_probe="late alias after eviction")
        actual["_plan_store"].flush()
        assert actual["records"][0]["details"]["staged_probe"] == "late alias after eviction"
        assert all(record["details"]["staged_probe"] == str(record["record_no"])
                   for record in actual["records"] if record["record_no"] != 1)
        metrics = actual["_plan_store"].metrics()
        assert metrics["peak_read_records"] <= chunk
        assert metrics["peak_dirty_records"] <= chunk
        assert metrics["encoded_bytes"] > 0 and metrics["file_bytes"] > 0
    finally:
        close_plan(actual)
        close_plan(expected)


def test_staging_codec_indexes_exact_types_and_closes_explicitly(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    store = staging.PlanStore()
    values = store.sequence("rows")
    when = dt.datetime(2026, 1, 1, 2, 3, 4, 123456)
    row = {"_source_row": 1, "amount": Decimal("9999999999999999.99"), "balance": None,
           "trade_time": when, "details": {"$staged_datetime": "source text, not a codec tag"}}
    values.extend([row, dict(row, _source_row=2), dict(row, _source_row=3)])
    index = store.mapping()
    key = ("occurrence", when, Decimal("10.20"), None)
    index[key] = (b"hash", {None, ("proof", "scope")})
    index.flush()
    assert index[key] == (b"hash", {None, ("proof", "scope")})
    assert copy.deepcopy(values[0]) == row
    assert list(values) == [row, dict(row, _source_row=2), dict(row, _source_row=3)]
    values.remove_numbers([2])
    assert [item["_source_row"] for item in values] == [1, 3]
    store.close()
    store.close()
    with pytest.raises(ValueError, match="closed"):
        list(values)
    with pytest.raises(ValueError, match="closed"):
        index[key]


def test_member_local_revocation_matches_complete_list_plan(database, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    raw = source(round_trip())[0]
    content = archive([("a.csv", raw), ("b.csv", raw)])
    expected = table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE)
    actual = table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE, _stage=True)
    try:
        requested = {expected["cancellations"][0]["original_record_no"]}
        table_cancellations.revoke_pairs(expected, requested)
        table_cancellations.revoke_pairs(actual, requested)
        for key in ("records", "rows", "cancelled_rows", "cancellations"):
            assert copy.deepcopy(list(actual[key])) == copy.deepcopy(expected[key])
        assert actual["stats"] == expected["stats"]
        assert len(actual["cancellations"]) == 1
    finally:
        close_plan(actual)
        close_plan(expected)


def test_public_tiny_chunks_preview_repreview_commit_duplicate_and_receipt(client, database, make_case, monkeypatch):
    # Exercise the uncached staging lifetime; cache ownership is tested separately.
    monkeypatch.setattr(imports.import_preview_cache, "stamp", lambda _: None)
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    monkeypatch.setattr(imports.importer, "WRITE_CHUNK", 2)
    original, owners = staging.PlanStore.__init__, []
    def initialize(self):
        original(self)
        owners.append(self)
    monkeypatch.setattr(staging.PlanStore, "__init__", initialize)
    cid = make_case("Synthetic complete plan staging")
    members = [("transactions.csv", table("transaction", [transaction(**{"交易流水号": f"SYN-{index}", "交易金额": f"{index + 1}.01"})
                                                               for index in range(7)]))]
    view = preview(client, cid, members)
    assert all(owner._closed for owner in owners)
    revised = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview",
                          json={"mode": view["mode"], "mapping": view["mapping"]})
    assert revised.status_code == 200, revised.text
    assert revised.json()["preview"] == view["preview"]
    job = complete(client, cid, revised.json())
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 7 and job["result"]["rows_dup"] == 0
    first = database.q_raw("select trade_no,amount,balance,trade_time from flows where case_id=? order by id", [cid])
    assert [item["amount"] for item in first] == [Decimal(f"{index + 1}.01") for index in range(7)]
    again = complete(client, cid, preview(client, cid, members))
    assert again["stage"] == "done", again
    assert again["result"]["ok"] == 0 and again["result"]["rows_dup"] == 7
    assert database.q_raw("select trade_no,amount,balance,trade_time from flows where case_id=? order by id", [cid]) == first
    assert database.q_raw("select count(*) n from batches where case_id=?", [cid])[0]["n"] == 2
    assert database.q_raw("select count(*) n from flow_sources where case_id=?", [cid])[0]["n"] == 14
    assert all(owner._closed for owner in owners)
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == job["result"]


def test_public_failure_after_first_chunk_rolls_back_every_business_write(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic staged rollback")
    view = preview(client, cid, [("transactions.csv", table("transaction", [transaction(**{"交易流水号": f"SYN-{index}"}) for index in range(5)]))])
    monkeypatch.setattr(imports.importer, "WRITE_CHUNK", 2)
    original, writes = database.insert_dataframe, []
    def fail_later(name, frame, columns):
        if name == "flows":
            writes.append(len(frame))
            if len(writes) == 2:
                raise OSError("synthetic second chunk write failure")
        return original(name, frame, columns)
    monkeypatch.setattr(database, "insert_dataframe", fail_later)
    job = complete(client, cid, view)
    assert writes == [2, 2]
    assert job["stage"] == "error" and job["done"] == 0
    for name in ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records", "evidence_sources"):
        assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid])[0]["n"] == 0
    assert imports._source_available(imports._session(cid, view["import_id"]))


def test_parse_tail_failure_closes_store_with_retained_traceback(database, monkeypatch):
    original, owners = staging.PlanStore.__init__, []
    def initialize(self):
        original(self)
        owners.append(self)
    monkeypatch.setattr(staging.PlanStore, "__init__", initialize)
    content = archive([("first.csv", source(round_trip())[0]), ("bad.csv", b"\x00invalid")])
    with pytest.raises(table_archive.table_import.TableImportError) as caught:
        table_archive.parse(io.BytesIO(content), profile=table_archive.EVIDENCE_PROFILE, _stage=True)
    assert caught.value.__traceback__ is not None
    assert owners and all(owner._closed for owner in owners)


def test_completed_members_release_original_objects_before_next_parser(database, monkeypatch):
    """The regression is cumulative member retention, not merely list equality."""
    class SourceDict(dict):
        pass
    original = table_archive.table_import.parse
    references, prior_live = [], []
    def observed(*args, **kwargs):
        prior_live.append(sum(reference() is not None for reference in references))
        plan = original(*args, **kwargs)
        for name in ("rows", "records"):
            plan[name] = [SourceDict(value) for value in plan[name]]
            references.extend(weakref.ref(value) for value in plan[name])
        return plan
    monkeypatch.setattr(table_archive.table_import, "parse", observed)
    content = table("transaction", [transaction(**{"交易流水号": f"SYN-{index}"}) for index in range(32)])
    plan = table_archive.parse(io.BytesIO(archive([(f"member-{index:02}.csv", content) for index in range(32)])),
                               profile=table_archive.EVIDENCE_PROFILE, _stage=True)
    try:
        assert len(plan["rows"]) == 1024
        assert prior_live == [0] * 32
        gc.collect()
        assert all(reference() is None for reference in references)
        metrics = plan["_plan_store"].metrics()
        assert metrics["peak_read_records"] <= staging.PLAN_CHUNK
        assert metrics["peak_read_encoded_bytes"] <= staging.PLAN_CHUNK_BYTES
        print("synthetic_completed_member_metrics", metrics)
    finally:
        close_plan(plan)


def test_staging_scan_and_update_use_chunk_queries_not_one_query_per_record(database):
    store = staging.PlanStore()
    values = store.sequence("records")
    started = time.monotonic()
    count = 4096
    try:
        values.extend({"record_no": number, "member_path": "synthetic.csv", "source_row": number + 1,
                       "details": {"reason": "synthetic evidence", "notes": "x" * 512}}
                      for number in range(1, count + 1))
        for item in values:
            item["details"]["scope"] = "synthetic stable scope"
        store.flush()
        for number in range(count):
            assert values[number]["details"]["scope"] == "synthetic stable scope"
        index = store.mapping()
        for number in range(count):
            index[("synthetic", number)] = number
        index.flush()
        for number in range(count):
            assert index[("synthetic", number)] == number
        metrics = store.metrics()
        # Point-key lookup intentionally uses SQLite's index. Sequential plans
        # and writes must be batched; no wall-clock assertion masks slow hosts.
        assert metrics["read_queries"] <= count + count // staging.PLAN_CHUNK + 3
        assert metrics["write_batches"] <= 3 * (count // staging.PLAN_CHUNK) + 1
        print("synthetic_staging_io", {**metrics, "elapsed_seconds": time.monotonic() - started})
    finally:
        store.close()


def test_public_commit_shares_bounded_reads_for_manifest_and_csv_identity(
        client, database, make_case, monkeypatch):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    cid = make_case("Synthetic shared commit preparation")
    members, expected = [], []
    for member in ("first.csv", "second.csv"):
        values = []
        for number in range(1, 10):
            reference = f"SYN-{member}-{number}"
            values.append(transaction(**{"交易流水号": reference, "交易金额": f"{number}.01"}))
            expected.append((member, number + 1, reference, Decimal(f"{number}.01"), "SYN-A", "SYN-B"))
        members.append((member, source(values)[0]))
    observed = {}
    manifest, commit = import_validation.table_manifest, importer.commit_rows

    def prepare(plan, rows, *args, **kwargs):
        observed["store"] = plan["_plan_store"]
        observed["before"] = observed["store"].decode_calls
        return manifest(plan, rows, *args, **kwargs)

    def write(*args, **kwargs):
        observed["decodes"] = observed["store"].decode_calls - observed["before"]
        observed["metrics"] = observed["store"].metrics()
        return commit(*args, **kwargs)

    monkeypatch.setattr(import_validation, "table_manifest", prepare)
    monkeypatch.setattr(importer, "commit_rows", write)
    view = preview(client, cid, members, zipped=True, scope_bank=None)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert (job["result"]["ok"], job["result"]["rows_dup"]) == (18, 0)
    actual = database.con().execute("""
        select r.member_path,r.source_row,f.trade_no,f.amount,f.payer_account,f.payee_account
        from import_records r join flow_sources s
          on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        join flows f on f.case_id=s.case_id and f.id=s.flow_id
        where r.case_id=? and r.source_id=? order by r.record_no
        """, [cid, view["import_id"]]).fetchall()
    assert actual == expected
    # One complete journal proof plus one joined row/record scan. A second
    # identity replay would exceed this bound even with the tiny tail cache.
    assert observed["decodes"] <= 3 * len(expected), observed["decodes"]
    assert observed["metrics"]["peak_read_records"] <= 2
    assert observed["store"]._closed


@pytest.mark.parametrize("reuse", [False, True], ids=["cache-miss", "cache-hit"])
def test_public_import_reports_actual_stage_io_without_source_values(client, database, make_case, monkeypatch, reuse):
    """Count the real read/write shape separately from the index micro-probe."""
    from app import import_preview_cache
    metrics, phases, calls = [], {}, {}
    original_close = staging.PlanStore.close
    def observed_close(self):
        # Identity replay uses its own private indexes in the same owner type;
        # this probe measures only the complete importer plan.
        if not self._closed and "records" in self._counts:
            metrics.append(self.metrics())
        return original_close(self)
    monkeypatch.setattr(staging.PlanStore, "close", observed_close)
    for name in ("_parse", "_bind_table_plan", "_write_feedback_journal"):
        original = getattr(imports, name)
        def measure(*args, _name=name, _original=original, **kwargs):
            started = time.monotonic()
            calls[_name] = calls.get(_name, 0) + 1
            try:
                return _original(*args, **kwargs)
            finally:
                phases[_name] = phases.get(_name, 0) + time.monotonic() - started
        monkeypatch.setattr(imports, name, measure)
    cid = make_case("Synthetic production staging counters")
    members = [(f"member-{member}.csv", source([round_trip()[0] | {"交易流水号": f"SYN-{member}-{row}"}
                                               for row in range(128)])[0]) for member in range(4)]
    view = preview(client, cid, members, scope_bank=None)
    assert import_preview_cache._slot is not None
    if not reuse:
        import_preview_cache.discard(view["import_id"])
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 512
    assert len(metrics) == (1 if reuse else 2)
    assert calls == {"_parse": 1 if reuse else 2, "_bind_table_plan": 2, "_write_feedback_journal": 1}
    assert import_preview_cache._slot is None
    assert all(item["peak_read_records"] <= staging.PLAN_CHUNK for item in metrics)
    assert all(item["member_note_encoded_bytes"] > 0 for item in metrics)
    print("synthetic_public_operation_io", {"cache_reused": reuse, "closed_plans": metrics,
                                           "phase_seconds": phases, "calls": calls})


@pytest.mark.parametrize("nested_only", [False, True])
def test_evicted_live_aliases_share_updates_and_closed_owner_rejects_mutation(database, monkeypatch, nested_only):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 1)
    store = staging.PlanStore()
    records = store.sequence("records")
    records.extend([{"record_no": 1, "details": {"initial": "yes"}},
                    {"record_no": 2, "details": {"other": "yes"}}])
    old = records[0]
    if nested_only:
        old = old["details"]
    records[1]
    current = records[0]
    current["details"]["field_a"] = "new reader"
    if nested_only:
        old["field_b"] = "older nested alias"
    else:
        old["details"]["field_b"] = "older record alias"
    store.flush()
    assert records[0]["details"]["field_a"] == "new reader"
    assert "field_b" in records[0]["details"]
    store.close()
    with pytest.raises(ValueError, match="closed"):
        (old if nested_only else old["details"])["after_close"] = "forbidden"


def test_public_ordinary_writer_reuses_validated_row_objects(client, database, make_case, monkeypatch):
    from test_import_history_filter import data, package, accepted
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    original_meta, original_commit = importer._provenance_metadata, importer.commit_rows
    observed = {}
    def metadata(provenance, filename, mode, rows, **kwargs):
        assert type(rows) is staging.StagedSequence
        observed.update(store=rows.store, before=rows.store.decode_calls)
        source = original_meta(provenance, filename, mode, rows, **kwargs)
        assert set(source) == set(provenance)
        return source
    def write(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        observed["decodes"] = observed["store"].decode_calls-observed["before"]
        return result
    monkeypatch.setattr(importer, "_provenance_metadata", metadata)
    monkeypatch.setattr(importer, "commit_rows", write)
    cid, count = make_case("Synthetic validated row reuse"), 257
    _, receipt = accepted(client, cid, package([("rows.csv", data("reuse", count))]))
    assert (receipt["ok"], receipt["rows_dup"]) == (count, 0)
    assert database.q_raw("select count(*) n,sum(amount) amount from flows where case_id=?", [cid]) == [{"n": count, "amount": Decimal(count)}]
    assert observed["decodes"] <= count + staging.PLAN_CHUNK, observed["decodes"]
    assert observed["store"]._closed


def staged_writer_input(content, cid):
    import hashlib
    import uuid
    plan = table_archive.parse(io.BytesIO(content), _stage=True)
    if "_plan_store" not in plan:
        staging.stage_plan(plan)
    keys = {}
    import_validation.table_manifest(plan, plan["rows"], standalone_csv_keys=keys)
    source = dict(source_id=uuid.uuid4().hex, filename="synthetic.zip", sha256=hashlib.sha256(content).hexdigest(),
                  size_bytes=len(content), mode=plan["mode"], mapping=plan["mapping"], sheet=plan["sheet"],
                  parser_version=plan["profile"], row_kind="table_record")
    return plan, {"case_id": cid, "provenance": source, "standalone_csv_keys": keys}


@pytest.mark.parametrize("mutation", ["tracked", "flushed", "replace"])
def test_late_row_alias_and_replacement_keep_original_requested_rebuild(client, database, make_case, monkeypatch, mutation):
    from test_import_history_filter import data, package, accepted
    cid, raw = make_case("Synthetic late validated alias"), data("alias")
    first, _ = accepted(client, cid, package([("original.csv", raw)]))
    key = database.q_raw("select record_key from import_records where case_id=? and source_id=?", [cid, first["import_id"]])[0]["record_key"]
    plan, kwargs = staged_writer_input(package([("moved.csv", raw)]), cid)
    original = importer._provenance_metadata
    def metadata(*args, **kw):
        result = original(*args, **kw)
        row = args[3][0]; row["_record_key"] = key
        if mutation == "flushed": plan["_plan_store"].flush()
        if mutation == "replace":
            replacement = dict(row)
            args[3].clear(); args[3].extend([replacement])
        return result
    monkeypatch.setattr(importer, "_provenance_metadata", metadata)
    try:
        result = importer.commit_rows("synthetic.zip", plan["mode"], plan["rows"], **kwargs)
        assert (result["ok"], result["rows_dup"]) == (0, 1)
        assert database.q_raw("select count(*) n from flows where case_id=?", [cid]) == [{"n": 1}]
    finally:
        close_plan(plan)


@pytest.mark.parametrize("field,value", [("_source_row", 0), ("_record_key", "invalid"), ("_source_sheet", "SYN-other")])
def test_invalid_later_staged_row_never_writes_partial_source(database, make_case, field, value):
    from test_import_history_filter import data, package
    cid = make_case("Synthetic invalid validated tail")
    plan, kwargs = staged_writer_input(package([("rows.csv", data("invalid", 2))]), cid)
    plan["rows"][1][field] = value
    try:
        with pytest.raises(importer.ImportError):
            importer.commit_rows("synthetic.zip", plan["mode"], plan["rows"], **kwargs)
        for name in ["flows", "batches", "import_sources", "flow_sources"]:
            assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid]) == [{"n": 0}]
    finally:
        close_plan(plan)


@pytest.mark.parametrize("when", ["before", "transaction"])
def test_closed_staged_owner_still_fails_before_business_writes(database, make_case, monkeypatch, when):
    from contextlib import contextmanager
    from test_import_history_filter import data, package
    cid = make_case("Synthetic closed validated owner")
    plan, kwargs = staged_writer_input(package([("rows.csv", data("closed"))]), cid)
    if when == "before": plan["_plan_store"].close()
    else:
        original = database.transaction
        @contextmanager
        def transaction():
            with original() as connection:
                plan["_plan_store"].close()
                yield connection
        monkeypatch.setattr(database, "transaction", transaction)
    try:
        with pytest.raises(ValueError, match="closed"):
            importer.commit_rows("synthetic.zip", plan["mode"], plan["rows"], **kwargs)
        for name in ["flows", "batches", "import_sources", "flow_sources"]:
            assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid]) == [{"n": 0}]
    finally:
        close_plan(plan)
