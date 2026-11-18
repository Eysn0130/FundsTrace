"""A fresh archive releases compatibility keys before retaining writer rows."""
from contextlib import contextmanager
import csv
from decimal import Decimal
import io
import zipfile

import pytest

from app import evidence_resolution, import_preview_cache, import_validation, importer, import_staging as staging, table_archive, table_import
from app.services import imports
from test_import_history_filter import accepted, package
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def source(count=17):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["date", "payer_account", "payee_account", "amount", "currency", "balance", "description"])
    for number in range(count):
        amount, currency = ("90071992547409.91", "CNY") if number == 0 else ("0.01", "USD") if number == 1 else ("1.01", "CNY")
        writer.writerow(["2026-01-01 10:00:00.123456", "000SYN-A", f"000SYN-{number}", amount, currency,
                         "12.34" if number == 0 else "", "Synthetic ordinary archive"])
    return package([("rows.csv", stream.getvalue().encode("utf-8-sig"))])


def observe_writer(database, monkeypatch):
    original_commit, original_key, original_transaction = importer.commit_rows, importer._row_key, database.transaction
    original_metadata, original_iter = importer._provenance_metadata, staging.StagedSequence.__iter__
    original_insert = database.insert_dataframe
    original_manifest, original_record_key = import_validation.table_manifest, table_import.record_key
    observed = {"active": False, "metadata_active": False, "store": None, "requested": [],
                "row_key_calls": 0, "live_rows": [], "probes": 0, "service_probes": 0,
                "csv_keys": [], "row_refs": [], "metadata_peak_rows": [], "insert_live_rows": [],
                "manifest_record_paths": []}
    paths = None

    def manifest(*args, **options):
        nonlocal paths
        paths = []
        try:
            return original_manifest(*args, **options)
        finally:
            observed["manifest_record_paths"].append(paths)
            paths = None

    def record_key(path, *args):
        if paths is not None:
            paths.append(path)
        return original_record_key(path, *args)

    def commit(*args, **options):
        rows, provenance = args[2], options["provenance"]
        assert type(rows) is staging.StagedSequence
        assert provenance["parser_version"] == table_archive.PROFILE
        assert provenance["parser_version"] not in evidence_resolution.OCCURRENCE_PROFILES
        keys = options["standalone_csv_keys"]
        observed["csv_keys"].append(None if keys is None else type(keys))
        observed["store"] = rows.store
        observed["active"] = True
        try:
            return original_commit(*args, **options)
        finally:
            observed["active"] = False

    def row_key(*args, **options):
        if observed["active"]:
            observed["row_key_calls"] += 1
        return original_key(*args, **options)

    def metadata(*args, **options):
        observed["row_refs"].append(options.get("_row_refs") is not None)
        observed["metadata_peak_rows"].append(0)
        observed["metadata_active"] = True
        try:
            return original_metadata(*args, **options)
        finally:
            observed["metadata_active"] = False

    def iterate(sequence):
        for item in original_iter(sequence):
            if observed["metadata_active"] and sequence.kind == "rows":
                live = sum(kind == "rows" for kind, _ in sequence.store._live)
                observed["metadata_peak_rows"][-1] = max(observed["metadata_peak_rows"][-1], live)
            yield item

    def insert(table, frame, columns):
        if observed["active"] and table == "flows":
            observed["insert_live_rows"].append(sum(kind == "rows" for kind, _ in observed["store"]._live))
        return original_insert(table, frame, columns)

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=None):
            normalized = " ".join(sql.split()).lower()
            if normalized == "select 1 from flows where case_id=? limit 1":
                assert getattr(database._transaction_state, "depth", 0) > 0
                observed["probes" if observed["active"] else "service_probes"] += 1
            if observed["active"]:
                if "r.record_key in (select unnest(?::varchar[]))" in sql:
                    keys = [value for value in params if type(value) is list]
                    assert len(keys) == 1
                    observed["requested"].append(len(keys[0]))
                    observed["live_rows"].append(sum(kind == "rows" for kind, _ in observed["store"]._live))
            return self.connection.execute(sql, params or [])

        def __getattr__(self, name):
            return getattr(self.connection, name)

    @contextmanager
    def transaction():
        with original_transaction() as connection:
            yield Connection(connection)

    monkeypatch.setattr(importer, "commit_rows", commit)
    monkeypatch.setattr(importer, "_row_key", row_key)
    monkeypatch.setattr(importer, "_provenance_metadata", metadata)
    monkeypatch.setattr(import_validation, "table_manifest", manifest)
    monkeypatch.setattr(table_import, "record_key", record_key)
    monkeypatch.setattr(staging.StagedSequence, "__iter__", iterate)
    monkeypatch.setattr(database, "insert_dataframe", insert)
    monkeypatch.setattr(database, "transaction", transaction)
    return observed


@pytest.mark.parametrize("foreign_history", [False, True])
@pytest.mark.parametrize("cached", [False, True], ids=["cache-miss", "cache-hit"])
def test_fresh_public_archive_drops_csv_keys_before_writer_and_keeps_rows_bounded(client, database, make_case, monkeypatch, foreign_history, cached):
    monkeypatch.setattr(staging, "PLAN_CHUNK", 2)
    if foreign_history:
        accepted(client, make_case("Synthetic unrelated other case"), source())
    cid = make_case("Synthetic fresh ordinary archive")
    observed = observe_writer(database, monkeypatch)
    content = source()
    view = preview(client, cid, content, "synthetic.zip")
    assert import_preview_cache._slot is not None
    if not cached:
        import_preview_cache.discard(view["import_id"])
    parse, parses = imports._parse, []

    def commit_parse(*args, **options):
        parses.append(True)
        return parse(*args, **options)

    monkeypatch.setattr(imports, "_parse", commit_parse)
    job = complete(client, cid, view)
    assert job["stage"] == "done", job
    assert len(parses) == (0 if cached else 1)
    receipt = job["result"]
    assert (receipt["ok"], receipt["rows_dup"]) == (17, 0)
    assert database.q_raw("select currency,count(*) n,sum(amount) amount from flows where case_id=? group by currency order by currency", [cid]) == [
        {"currency": "CNY", "n": 16, "amount": Decimal("90071992547425.06")},
        {"currency": "USD", "n": 1, "amount": Decimal("0.01")}]
    for table in ("import_records", "flow_sources"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 17}]
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()["items"]
    assert [(record["record_no"], record["source_row"], record["member_path"], record["disposition"])
            for record in records] == [(number + 1, number + 2, "rows.csv", "imported") for number in range(17)]
    actual = database.q_raw("""
        select s.source_row,f.payer_account,f.payee_account,f.amount,f.currency,f.balance,f.summary
        from flow_sources s join flows f on f.case_id=s.case_id and f.id=s.flow_id
        where s.case_id=? and s.source_id=? order by s.source_row
        """, [cid, view["import_id"]])
    assert actual == [{"source_row": number + 1, "payer_account": "000SYN-A", "payee_account": f"000SYN-{number}",
                       "amount": Decimal("90071992547409.91" if number == 0 else "0.01" if number == 1 else "1.01"),
                       "currency": "USD" if number == 1 else "CNY", "balance": Decimal("12.34") if number == 0 else None,
                       "summary": "Synthetic ordinary archive"} for number in range(17)]
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["result"] == receipt
    assert observed["csv_keys"] == [None], {name: observed[name] for name in ("row_refs", "metadata_peak_rows")}
    assert observed["manifest_record_paths"] == [["rows.csv"] * 17]
    assert observed["row_refs"] == [False]
    assert observed["metadata_peak_rows"] and max(observed["metadata_peak_rows"]) <= staging.PLAN_CHUNK + 2
    assert observed["requested"] == [0], {name: observed[name] for name in ("requested", "row_key_calls", "live_rows")}
    assert observed["row_key_calls"] == 0
    assert observed["live_rows"] and max(observed["live_rows"]) <= staging.PLAN_CHUNK + 2
    assert observed["insert_live_rows"] and max(observed["insert_live_rows"]) <= staging.PLAN_CHUNK + 2
    assert observed["probes"] == 1
    assert observed["service_probes"] == 1


def test_reimport_uses_original_history_and_keeps_every_source_link(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic fresh then repeated archive")
    observed = observe_writer(database, monkeypatch)
    content = source(3)
    first, initial = accepted(client, cid, content)
    original = database.q_raw("select * from flows where case_id=? order by id", [cid])
    second, repeated = accepted(client, cid, content)
    assert (initial["ok"], repeated["ok"], repeated["rows_dup"]) == (3, 0, 3)
    assert observed["requested"] == [0, 3]
    assert observed["csv_keys"] == [None, dict]
    assert observed["manifest_record_paths"] == [["rows.csv"] * 3, ["rows.csv", "table.csv"] * 3]
    assert observed["row_refs"] == [False, True]
    assert observed["row_key_calls"] > 0 and observed["probes"] == 2
    assert observed["service_probes"] == 2
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == original
    links = "select source_row,flow_id from flow_sources where case_id=? and source_id=? order by source_row"
    assert database.q_raw(links, [cid, first["import_id"]]) == database.q_raw(links, [cid, second["import_id"]])
    assert database.q_raw("select count(*) n from import_records where case_id=?", [cid]) == [{"n": 6}]
    assert all(row["trade_time"].microsecond == 123456 for row in original)
    assert sum(row["balance"] is None for row in original) == 2
    for view in (first, second):
        assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


def test_existing_standalone_csv_still_supplies_archive_fallback(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic CSV history bridge")
    content = source(3)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        raw = archive.read("rows.csv")
    ordinary = preview(client, cid, raw, "synthetic.csv")
    first = complete(client, cid, ordinary)
    assert first["stage"] == "done" and first["result"]["ok"] == 3
    original = database.q_raw("select * from flows where case_id=? order by id", [cid])
    observed = observe_writer(database, monkeypatch)
    _, archived = accepted(client, cid, content)
    assert (archived["ok"], archived["rows_dup"]) == (0, 3)
    assert observed["requested"] == [3] and observed["row_key_calls"] > 0
    assert observed["csv_keys"] == [dict] and observed["row_refs"] == [True]
    assert observed["probes"] == 1
    assert observed["service_probes"] == 1
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == original


@pytest.mark.parametrize("damage", ["sha256", "encoding", "role", "source_row", "record_key"])
def test_fresh_manifest_rejects_invalid_archive_identity(client, database, make_case, monkeypatch, damage):
    cid = make_case("Synthetic damaged archive identity")
    view = preview(client, cid, source(3), "synthetic.zip")
    manifest = import_validation.table_manifest

    def damaged(plan, rows, **options):
        if damage == "source_row":
            plan["records"][-1]["source_row"] = 0
        elif damage == "record_key":
            plan["records"][-1]["record_key"] = rows[-1]["_record_key"] = "0" * 64
        else:
            plan["members"][-1][damage] = {"sha256": "not-a-digest", "encoding": "unknown", "role": "auxiliary"}[damage]
        return manifest(plan, rows, **options)

    monkeypatch.setattr(import_validation, "table_manifest", damaged)
    job = complete(client, cid, view)
    assert job["stage"] == "error" and job["result"] is None
    assert job["error_code"] == "import_integrity_failed"
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]


def test_fresh_invalid_tail_is_validated_before_history_probe(database, make_case, monkeypatch):
    from test_import_plan_staging import close_plan, staged_writer_input
    cid = make_case("Synthetic fresh invalid archive tail")
    plan, options = staged_writer_input(source(3), cid)
    plan["rows"][2]["_record_key"] = "invalid"
    observed = observe_writer(database, monkeypatch)
    try:
        with pytest.raises(importer.ImportError):
            importer.commit_rows("synthetic.zip", plan["mode"], plan["rows"], **options)
        assert observed["probes"] == 0
        for name in ("flows", "batches", "import_sources", "flow_sources"):
            assert database.q_raw(f"select count(*) n from {name} where case_id=?", [cid]) == [{"n": 0}]
    finally:
        close_plan(plan)


def test_fresh_public_invalid_metadata_tail_cannot_write_without_row_refs(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic fresh public metadata tail")
    view = preview(client, cid, source(3), "synthetic.zip")
    observed = observe_writer(database, monkeypatch)
    metadata = importer._provenance_metadata

    def invalid_tail(*args, **options):
        args[3][-1]["_record_key"] = "invalid"
        return metadata(*args, **options)

    monkeypatch.setattr(importer, "_provenance_metadata", invalid_tail)
    job = complete(client, cid, view)
    assert job["stage"] == "error" and job["result"] is None
    assert observed["csv_keys"] == [None] and observed["row_refs"] == [False]
    assert observed["service_probes"] == 1 and observed["probes"] == 0
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records", "import_source_operations", "evidence_sources"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]


def test_fresh_late_write_failure_rolls_back_complete_source(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic fresh archive rollback")
    view = preview(client, cid, source(3), "synthetic.zip")
    observed = observe_writer(database, monkeypatch)
    monkeypatch.setattr(importer, "WRITE_CHUNK", 1)
    insert, writes = database.insert_dataframe, []

    def fail(table, frame, columns):
        if table == "flows":
            writes.append(len(frame))
            if len(writes) == 2:
                raise OSError("Synthetic second fresh chunk failure")
        return insert(table, frame, columns)

    monkeypatch.setattr(database, "insert_dataframe", fail)
    job = complete(client, cid, view)
    assert job["stage"] == "error" and writes == [1, 1]
    assert observed["csv_keys"] == [None] and observed["row_refs"] == [False]
    assert observed["requested"] == [0] and observed["row_key_calls"] == 0
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_members", "import_records", "import_source_operations", "evidence_sources"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid]) == [{"n": 0}]
