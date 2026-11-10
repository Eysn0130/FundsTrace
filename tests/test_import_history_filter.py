"""Ordinary envelope history is limited by complete current physical keys."""
from contextlib import contextmanager
import csv
import hashlib
import io
from decimal import Decimal
import uuid
import zipfile

import pytest

from app import importer, table_archive
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def data(tag, count=1):
    text = io.StringIO(newline='')
    writer = csv.writer(text)
    writer.writerow(['date', 'payer_account', 'payee_account', 'amount', 'summary'])
    writer.writerows(['2026-01-01', 'SYN-A', f'SYN-{tag}-{i}', '1.00', 'Synthetic history'] for i in range(count))
    return text.getvalue().encode()


def package(members):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        for name, content in members:
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), content)
    return out.getvalue()


def accepted(client, cid, content, filename='synthetic.zip'):
    view = preview(client, cid, content, filename)
    assert view['table_summary']['profile'] == table_archive.PROFILE
    job = complete(client, cid, view)
    assert job['stage'] == 'done', job
    return view, job['result']


def history_rows(monkeypatch, database):
    original, observed = database.transaction, {'queries': 0, 'rows': 0}
    class Cursor:
        def __init__(self, cursor): self.cursor = cursor
        def fetchmany(self, count):
            rows = self.cursor.fetchmany(count); observed['rows'] += len(rows)
            return rows
        def __getattr__(self, name): return getattr(self.cursor, name)
    class Connection:
        def __init__(self, connection): self.connection = connection
        def execute(self, *args, **kwargs):
            result = self.connection.execute(*args, **kwargs)
            columns = [item[0] for item in result.description] if result.description else []
            if columns[:3] == ['record_key', 'flow_id', 'trade_no']:
                observed['queries'] += 1
                return Cursor(result)
            return result
        def __getattr__(self, name): return getattr(self.connection, name)
    @contextmanager
    def transaction():
        with original() as connection:
            yield Connection(connection)
    monkeypatch.setattr(database, 'transaction', transaction)
    return observed


def test_public_unrelated_history_does_not_leave_the_database(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic unrelated history budget')
    accepted(client, cid, package([('old.csv', data('old', 40))]))
    observed = history_rows(monkeypatch, database)
    _, receipt = accepted(client, cid, package([('new.csv', data('new'))]))
    assert (receipt['ok'], receipt['rows_dup']) == (1, 0)
    assert database.q_raw('select count(*) n,sum(amount) amount from flows where case_id=?', [cid]) == [
        {'n': 41, 'amount': Decimal('41.00')}]
    assert observed['queries'] == 1
    assert observed['rows'] == 0


@pytest.mark.parametrize('variant,added,duplicates', [('same', 0, 5), ('partial', 1, 3), ('moved', 3, 0), ('container', 0, 5)])
@pytest.mark.parametrize('chunk', [1, 2])
def test_public_shared_members_paths_and_chunks_keep_exact_supports(client, database, make_case, monkeypatch, variant, added, duplicates, chunk):
    monkeypatch.setattr(importer, 'WRITE_CHUNK', chunk)
    cid = make_case('Synthetic physical history matches')
    a, b = data('a', 3), data('b', 2)
    members = [('a.csv', a), ('b.csv', b)]
    accepted(client, cid, package(members))
    if variant == 'partial': members = [('a.csv', a), ('c.csv', data('c'))]
    if variant == 'moved': members = [('moved.csv', a)]
    if variant == 'container': members = [*members, ('unknown.csv', b'unknown\nSYN-only\n')]
    view, receipt = accepted(client, cid, package(members), 'changed-container.zip')
    assert (receipt['ok'], receipt['rows_dup']) == (added, duplicates)
    records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()['items']
    assert sum(r['disposition'] == 'duplicate' for r in records) == duplicates
    assert database.q_raw('select count(*) n,sum(amount) amount from flows where case_id=?', [cid]) == [
        {'n': 5 + added, 'amount': Decimal(5 + added)}]


def test_empty_requested_and_other_case_keep_their_boundaries(client, database, make_case, monkeypatch):
    cid, other = make_case('Synthetic empty request'), make_case('Synthetic separate case')
    content = package([('a.csv', data('a', 2))])
    accepted(client, cid, content)
    observed = history_rows(monkeypatch, database)
    _, empty = accepted(client, cid, package([('unknown.csv', b'unknown\nSYN-only\n')]))
    assert (empty['ok'], empty['rows_dup'], observed['rows']) == (0, 0, 0)
    _, separate = accepted(client, other, content)
    assert (separate['ok'], separate['rows_dup']) == (2, 0)


@pytest.mark.parametrize('field,value', [('amount', Decimal('9.99')), ('summary', 'Changed synthetic fact'),
                                       ('self_account', 'SYN-OTHER-SELF'), ('self_name', 'Synthetic other owner')])
def test_direct_writer_still_compares_complete_facts_and_self_fields(client, database, make_case, field, value):
    cid = make_case('Synthetic full identity comparison')
    content = package([('a.csv', data('a'))])
    accepted(client, cid, content)
    plan = table_archive.parse(io.BytesIO(content))
    try:
        plan['rows'][0][field] = value
        filename = 'direct.zip'
        provenance = dict(source_id=uuid.uuid4().hex, filename=filename, sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content), mode=plan['mode'], mapping=plan['mapping'], sheet=plan['sheet'],
            parser_version=plan['profile'], row_kind='table_record')
        receipt = importer.commit_rows(filename, plan['mode'], plan['rows'], case_id=cid, provenance=provenance)
        assert (receipt['ok'], receipt['rows_dup']) == (1, 0)
        assert database.q_raw('select count(*) n from flows where case_id=?', [cid]) == [{'n': 2}]
    finally:
        for key in ['_plan_store', '_details_pool']:
            if plan.get(key) is not None: plan[key].close()


def test_existing_inactive_operation_remains_in_original_history_range(client, database, make_case):
    cid = make_case('Synthetic inactive historical source')
    content = package([('a.csv', data('a'))])
    first, _ = accepted(client, cid, content)
    original = database.q_raw('select id from flows where case_id=?', [cid])[0]['id']
    database.x('update import_source_operations set active=false where case_id=? and source_id=?', [cid, first['import_id']])
    database.x('update evidence_sources set active=false where case_id=? and source_id=?', [cid, first['import_id']])
    again, receipt = accepted(client, cid, content)
    assert (receipt['ok'], receipt['rows_dup']) == (0, 1)
    assert database.q_raw('select flow_id from flow_sources where case_id=? and source_id=?', [cid, again['import_id']]) == [{'flow_id': original}]


@pytest.mark.parametrize('older_first', [False, True])
def test_profile_precedence_then_lowest_fid_survive_multiple_full_matches(client, database, make_case, monkeypatch, older_first):
    from test_archive_profile_upgrade import frozen_preview
    cid = make_case('Synthetic legacy matching supports')
    content = package([('a.csv', data('a'))])
    if older_first:
        with monkeypatch.context() as legacy:
            first = frozen_preview(client, cid, content, legacy, 'funds-trace-table-archive-v18')
            assert complete(client, cid, first)['stage'] == 'done'
    else:
        accepted(client, cid, content)
    low = database.q_raw('select id from flows where case_id=?', [cid])[0]['id']
    second, receipt = accepted(client, cid, content)
    assert receipt['rows_dup'] == 1
    # A controlled legacy fixture with two complete matching supports.
    high = database.con().execute("select nextval('flow_seq')").fetchone()[0]
    columns = [r[1] for r in database.con().execute("pragma table_info('flows')").fetchall() if r[1] not in {'id', 'batch_id'}]
    fields = ','.join(columns)
    database.con().execute(f'insert into flows(id,batch_id,{fields}) select ?,?,{fields} from flows where case_id=? and id=?',
                           [high, receipt['batch_id'], cid, low])
    database.x("update flow_sources set flow_id=?,disposition='imported' where case_id=? and source_id=?", [high, cid, second['import_id']])
    database.x("update import_records set disposition='imported',reason_code='imported' where case_id=? and source_id=?", [cid, second['import_id']])
    database.x('update batches set rows_imported=1,rows_dup=0 where case_id=? and id=?', [cid, receipt['batch_id']])
    third, repeated = accepted(client, cid, content)
    assert (repeated['ok'], repeated['rows_dup']) == (0, 1)
    expected = high if older_first else low
    assert database.q_raw('select flow_id from flow_sources where case_id=? and source_id=?', [cid, third['import_id']]) == [{'flow_id': expected}]


def test_later_failed_envelope_rolls_back_all_business_rows(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic filtered history rollback')
    accepted(client, cid, package([('old.csv', data('old', 2))]))
    tables = ['flows', 'batches', 'import_sources', 'flow_sources', 'import_members', 'import_records', 'import_source_operations', 'evidence_sources']
    def snapshot(): return {name: database.q_raw(f'select * from {name} where case_id=? order by all', [cid]) for name in tables}
    before = snapshot()
    view = preview(client, cid, package([('new.csv', data('new', 3))]), 'new.zip')
    original, calls = database.insert_dataframe, []
    def fail(name, frame, columns):
        if name == 'flows':
            calls.append(len(frame))
            if len(calls) == 2: raise OSError('synthetic later history-filter write failure')
        return original(name, frame, columns)
    monkeypatch.setattr(importer, 'WRITE_CHUNK', 1)
    monkeypatch.setattr(database, 'insert_dataframe', fail)
    job = complete(client, cid, view)
    assert calls == [1, 1] and job['stage'] == 'error'
    assert snapshot() == before
