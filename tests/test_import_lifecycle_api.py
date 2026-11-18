"""Public discovery, source access and retention use one explicit case scope."""
import csv
from datetime import datetime
import hashlib
import io
import time

import pytest

from app import importer
from app.services import imports


CSV = ("合成报表\n\n交易时间,付款账号,收款账号,交易金额\n"
       "2026-01-01 10:00:00,000SYN-A,000SYN-B,123.45\n\n"
       "2026-01-01 10:00:00,000SYN-A,000SYN-B,123.45\n"
       "invalid,000SYN-A,000SYN-B,5.00\n").encode()


def complete(client, cid, preview, **request):
    base = f"/api/v1/cases/{cid}/imports/{preview['import_id']}"
    assert client.post(base + "/commit", json={"mode": preview["mode"], "mapping": preview["mapping"], **request}).status_code == 200
    for _ in range(400):
        response = client.get(base)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            return job
        time.sleep(.01)
    raise AssertionError("Synthetic import timed out")


def upload(client, cid):
    response = client.post(f"/api/v1/cases/{cid}/imports/preview", files={"file": ("synthetic.csv", CSV)})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('occurrence', [False, True], ids=['ordinary-writer', 'staged-evidence-writer'])
@pytest.mark.parametrize('marks,extra,selected', [
    (None, [], [('000SYN-A', ''), ('000SYN-B', 'First B'), ('000SYN-C', 'Name C')]),
    ([], [], []),
    (['000SYN-C', '000SYN-A', '000SYN-B'], [], [('000SYN-C', 'Name C'), ('000SYN-A', ''), ('000SYN-B', 'First B')]),
    (['000SYN-UNKNOWN', '000SYN-B', '000SYN-A'], ['000SYN-B', '000SYN-EXTRA', '000SYN-A'],
     [('000SYN-UNKNOWN', ''), ('000SYN-B', 'First B'), ('000SYN-A', ''), ('000SYN-EXTRA', '')]),
    (None, ['000SYN-C', '000SYN-EXTRA', '000SYN-B'],
     [('000SYN-A', ''), ('000SYN-B', 'First B'), ('000SYN-C', 'Name C'), ('000SYN-EXTRA', '')]),
], ids=['automatic', 'empty', 'manual-order', 'unknown-and-overlap', 'automatic-and-extra'])
def test_public_main_marking_reuses_first_writer_declarations_without_source_reads(
        client, database, make_case, monkeypatch, record_property, occurrence, marks, extra, selected):
    from app.import_staging import StagedSequence

    cid = make_case('Synthetic first main declarations')
    headers = ['交易时间', '本方账号', '本方户名', '对方账号', '收付标志', '交易金额', '交易币种', '交易流水号']
    owners = [('000SYN-A', ''), ('000SYN-B', ' First B '), ('000SYN-A', 'Later A'),
              ('000SYN-B', 'Different B'), ('000SYN-C', ' Name C ')]
    cells = [[f'2026-01-01 10:00:0{i}', account, name, '000SYN-COUNTER', '出', '1.01', 'CNY', f'SYN-{i}']
             for i, (account, name) in enumerate(owners)]
    if occurrence:
        headers += ['参考号机构', '参考号渠道', '参考号业务域', '跨账簿事件参考号']
        cells = [row + ['SYN issuer', 'SYN channel', 'SYN scope', f'SYN-event-{i}'] for i, row in enumerate(cells)]
    cells.append(list(cells[0]))
    buffer = io.StringIO(newline='')
    csv.writer(buffer).writerows([headers, *cells])
    content = buffer.getvalue().encode('utf-8-sig')
    declarations = [{'account': account, 'name': name} for account, name in
                    [('000SYN-A', ''), ('000SYN-B', 'First B'), ('000SYN-C', 'Name C')]]
    phase = 'preview'
    reads = {'preview': 0, 'commit': 0, 'mark_rows': 0, 'mark_queries': 0}
    writer_receipts, staged_calls, mark_calls = [], [], []
    iterate, write, staged, mark = StagedSequence.__iter__, importer.commit_rows, importer._commit_staged_evidence, imports._mark_mains

    def counted_rows(self):
        for row in iterate(self):
            if self.kind == 'rows': reads[phase] += 1
            yield row

    def observed_write(*args, **kwargs):
        result = write(*args, **kwargs)
        writer_receipts.append(result['self_accounts'])
        return result

    def observed_staged(*args, **kwargs):
        staged_calls.append(True)
        return staged(*args, **kwargs)

    class MarkRows:
        def __init__(self, rows): self.rows = rows
        def __iter__(self):
            for row in self.rows:
                reads['mark_rows'] += 1
                yield row

    def observed_mark(*args):
        receipt = args[-2]
        assert database._transaction_state.depth > 0
        assert database.q_raw('select count(*) n from import_records where case_id=?', [cid])[0]['n'] == 6 * (len(mark_calls) + 1)
        assert database.q_raw('select count(*) n from flow_sources where case_id=? and source_id=?', [cid, receipt['source_id']])[0]['n'] == 6
        mark_calls.append(receipt['self_accounts'])
        if len(args) == 4:
            case, rows, receipt, request = args
            store = rows.store if isinstance(rows, StagedSequence) else None
            before = store.read_queries if store is not None else 0
            result = mark(case, MarkRows(rows), receipt, request)
            reads['mark_queries'] += store.read_queries - before if store is not None else 0
            return result
        return mark(*args)

    monkeypatch.setattr(StagedSequence, '__iter__', counted_rows)
    monkeypatch.setattr(importer, 'commit_rows', observed_write)
    monkeypatch.setattr(importer, '_commit_staged_evidence', observed_staged)
    monkeypatch.setattr(imports, '_mark_mains', observed_mark)
    for attempt in range(2):
        phase = 'preview'
        response = client.post(f'/api/v1/cases/{cid}/imports/preview', files={'file': (f'synthetic-{attempt}.csv', content)})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['table_summary']['profile'] == ('funds-trace-table-v71' if occurrence else 'funds-trace-table-v69')
        assert len(mark_calls) == attempt
        phase = 'commit'
        job = complete(client, cid, preview, mark_accounts=marks, self_accounts=extra)
        receipt = job['result']
        assert receipt['self_accounts'] == declarations and receipt['self_accounts_count'] == 3
        assert receipt['main_detected'] == [{'account': account, 'name': name} for account, name in selected]
        assert (receipt['ok'], receipt['rows_dup']) == ((0, 6) if attempt else (6, 0) if occurrence else (5, 1))
    assert writer_receipts == mark_calls == [declarations, declarations]
    assert len(staged_calls) == (2 if occurrence else 0)
    assert database.q_raw('select account,name,source from main_accounts where case_id=? order by account', [cid]) == [
        {'account': account, 'name': name, 'source': 'auto'} for account, name in sorted(selected)]
    record_property('main_source_reads', reads)
    print({'writer': 'staged' if occurrence else 'ordinary', 'reads': reads})
    assert reads['mark_rows'] == reads['mark_queries'] == 0


def test_nonempty_session_discovery_and_paginated_sources_are_case_bound(client, database, make_case):
    cid, other = make_case("Source API"), make_case("Other source API")
    preview = upload(client, cid)
    sessions = client.get(f"/api/v1/cases/{cid}/imports")
    assert sessions.status_code == 200, sessions.text
    item = sessions.json()["items"][0]
    assert item["import_id"] == preview["import_id"] and item["stage"] == "ready"
    assert item["created_at"].endswith("Z") and item["expires_at"].endswith("Z")
    assert "owner_token" not in item and "suffix" not in item and "columns_json" not in item
    assert client.get(f"/api/v1/cases/{other}/imports").json()["items"] == []
    assert preview["stats"]["bad_examples"][0]["source_row"] == 7
    complete(client, cid, preview)
    fid = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["id"]
    base = f"/api/v1/cases/{cid}/flows/{fid}/sources"
    pages = [client.get(base, params={"page": page, "page_size": 1}) for page in (1, 2)]
    assert all(response.status_code == 200 for response in pages)
    records = [response.json()["items"][0] for response in pages]
    # Flow links use the journal sequence; member_row remains the physical CSV
    # position, including preamble/blank records excluded from the flow table.
    assert [record["source_row"] for record in records] == [3, 5]
    assert [record["member_row"] for record in records] == [4, 6]
    journal = client.get(f"/api/v1/cases/{cid}/imports/{preview['import_id']}/records").json()
    assert journal["total"] == 6
    assert [(record["record_no"], record["source_row"]) for record in journal["items"]] == [
        (1, 1), (2, 2), (3, 4), (4, 5), (5, 6), (6, 7)]
    assert journal["items"][-1]["disposition"] == "unresolved"
    assert journal["items"][-1]["reason_code"] == "invalid_time"
    assert {record["disposition"] for record in records} == {"imported", "duplicate"}
    for record in records:
        assert record["sha256"] == hashlib.sha256(CSV).hexdigest()
        assert record["source_available"] and record["row_kind"] == "table_record" and record["sheet"] == ""
        assert record["member_path"] == "table.csv"
        matched = journal["items"][record["source_row"] - 1]
        assert matched["member_path"] == record["member_path"] and matched["source_row"] == record["member_row"]
        assert matched["flow_id"] == fid and matched["disposition"] == record["disposition"]
        assert matched["details"] == record["record_details"]
        elapsed = datetime.fromisoformat(record["imported_at"]) - datetime.fromisoformat(item["created_at"])
        assert 0 <= elapsed.total_seconds() < 60
    assert pages[0].json()["total"] == 2
    assert client.get(base, params={"page_size": 201}).status_code == 422
    assert client.get(f"/api/v1/cases/{other}/flows/{fid}/sources").status_code == 404
    source_url = f"/api/v1/cases/{cid}/sources/{preview['import_id']}/file"
    original = client.get(source_url)
    assert original.status_code == 200 and original.content == CSV
    assert original.headers["content-type"] == "application/octet-stream"
    assert original.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/cases/{other}/sources/{preview['import_id']}/file").status_code == 404


@pytest.mark.parametrize("legacy_without_journal", [False, True])
def test_package_sources_remain_metadata_and_cleanup_respects_retained_evidence(
        client, database, make_case, monkeypatch, legacy_without_journal):
    cid = make_case("Source lifecycle")
    with monkeypatch.context() as profile:
        if legacy_without_journal:
            profile.setattr(imports, "TABLE_PROFILE", importer.PARSER_VERSION)
        preview = upload(client, cid)
    job = complete(client, cid, preview)
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_id = restored.json()["case_id"]
    fid = client.get(f"/api/v1/cases/{restored_id}/flows").json()["items"][0]["id"]
    origins = client.get(f"/api/v1/cases/{restored_id}/flows/{fid}/sources").json()["items"]
    assert len(origins) == 2
    assert all(item["origin"] == "package" and not item["source_available"] for item in origins)
    assert all(item["source_id"] != preview["import_id"] for item in origins)
    assert client.get(f"/api/v1/cases/{restored_id}/sources/{origins[0]['source_id']}/file").status_code == 404
    database.x("update import_sessions set expires_at=current_timestamp-interval '1 day' where import_id=?", [preview["import_id"]])
    cleanup = f"/api/v1/cases/{cid}/imports/cleanup"
    protected = client.post(cleanup, json={"dry_run": False})
    assert protected.status_code == 200 and protected.json()["protected"] == 1
    path = database.DATA_DIR / "uploads" / f"{preview['import_id']}.csv"
    assert path.read_bytes() == CSV
    assert client.delete(f"/api/batches/{job['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    plan = client.post(cleanup, json={})
    assert plan.status_code == 200 and plan.json()["dry_run"]
    assert path.is_file()
    applied = client.post(cleanup, json={"dry_run": False})
    assert applied.status_code == 200
    if legacy_without_journal:
        assert plan.json()["eligible"] == 1 and plan.json()["protected"] == 0
        assert applied.json()["deleted"] == 1 and applied.json()["protected"] == 0 and not path.exists()
        assert database.q("select count(*) n from import_records where source_id=?", [preview["import_id"]]) == [{"n": 0}]
    else:
        assert plan.json()["eligible"] == 0 and plan.json()["protected"] == 1
        assert applied.json()["deleted"] == 0 and applied.json()["protected"] == 1 and path.read_bytes() == CSV
        assert database.q("select record_no,source_row,reason_code from import_records where source_id=? and disposition='removed' order by record_no",
                          [preview["import_id"]]) == [
            {"record_no": 3, "source_row": 4, "reason_code": "flow_deleted"},
            {"record_no": 5, "source_row": 6, "reason_code": "flow_deleted"}]
        assert database.q("select record_no,source_row,reason_code from import_records where source_id=? and disposition='unresolved'",
                          [preview["import_id"]]) == [{"record_no": 6, "source_row": 7, "reason_code": "invalid_time"}]
        assert database.q("select count(*) n from import_records where source_id=?", [preview["import_id"]]) == [{"n": 6}]
        original = client.get(f"/api/v1/cases/{cid}/sources/{preview['import_id']}/file")
        assert original.status_code == 200 and original.content == CSV
    receipt = client.get(f"/api/v1/cases/{cid}/imports/{preview['import_id']}").json()
    assert receipt["stage"] == "done" and receipt["result"] == job["result"]
    assert receipt["source_available"] is (not legacy_without_journal)
    assert client.get(f"/api/v1/cases/{restored_id}/flows").json()["total"] == 1


def test_additive_import_schema_preserves_an_existing_synthetic_database(database, make_case, flow_row):
    from app import importer
    cid = make_case("Before import catalog")
    importer.commit_rows("legacy-synthetic.csv", "A", [flow_row()], case_id=cid)
    with database._lock:
        for table in ("flow_sources", "import_sources", "import_sessions", "import_file_gc"):
            database.con().execute(f"drop table {table}")
        database._con.close()
        database._con = None
        database.con()
    assert database.q("select name from cases where id=?", [cid]) == [{"name": "Before import catalog"}]
    assert database.q_raw("select cast(amount as varchar) amount from flows where case_id=?", [cid]) == [{"amount": "123.45"}]
    for table in ("flow_sources", "import_sources", "import_sessions", "import_file_gc"):
        assert database.q(f"select count(*) n from {table}") == [{"n": 0}]


def test_failed_initialization_does_not_publish_a_partially_initialized_connection(database, make_case, monkeypatch):
    import pytest

    cid = make_case("Synthetic initialization failure")
    database._con.close()
    database._con = None
    migrate = database._migrate

    def fail(connection):
        connection.execute("create table synthetic_uncommitted_schema(value integer)")
        raise RuntimeError("Synthetic initialization failure")

    monkeypatch.setattr(database, "_migrate", fail)
    with pytest.raises(RuntimeError, match="Synthetic initialization failure"):
        database.con()
    assert database._con is None
    monkeypatch.setattr(database, "_migrate", migrate)
    assert database.q("select name from cases where id=?", [cid]) == [{"name": "Synthetic initialization failure"}]
    assert not database.q("select table_name from information_schema.tables where table_name='synthetic_uncommitted_schema'")
