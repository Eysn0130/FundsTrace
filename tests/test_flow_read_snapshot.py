"""Committed legacy flow reads stay coherent while the single writer is active."""
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json

import duckdb
import pandas as pd
import pytest

from app import analysis_snapshot, source_ledger
from app.main import flows
from test_funds_projection import flow


@pytest.mark.parametrize('rollback', [False, True])
def test_public_flow_reads_complete_old_commit_during_writer(client, database, make_case, rollback):
    cid = make_case('Synthetic committed flow reader')
    flow(database, cid, 1, '90071992547409.91')
    flow(database, cid, 2, '2.03', 'USD')
    analysis_snapshot.invalidate(cid)
    params = {'case_id': cid, 'size': 50}
    before = client.get('/api/flows', params=params).json()
    assert before['total'] == 2
    assert sorted(row['amount'] for row in before['rows']) == ['2.03', '90071992547409.91']
    entered, release = threading.Event(), threading.Event()
    errors = []

    class SyntheticRollback(Exception):
        pass

    def write():
        try:
            with database.transaction():
                database.x("update flows set amount=1.01,summary='SYN new committed summary' where case_id=? and id=1", [cid])
                analysis_snapshot.invalidate(cid)
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
        response = client.get('/api/flows', params=params)
        assert response.status_code == 200, response.text
        assert not release.is_set(), 'flow request waited for the uncommitted writer'
        assert response.json() == before
    finally:
        release.set()
        safety.cancel()
        writer.join(5)
    assert not writer.is_alive() and not errors
    after = client.get('/api/flows', params=params).json()
    if rollback:
        assert after == before
    else:
        assert after['analysis_revision'] != before['analysis_revision']
        assert sorted(row['amount'] for row in after['rows']) == ['1.01', '2.03']
        assert after['funds']['totals'] == [
            {'currency': 'CNY', 'amount': None, 'lower': '0.00', 'upper': '1.01'},
            {'currency': 'USD', 'amount': None, 'lower': '0.00', 'upper': '2.03'}]


def test_commit_between_count_and_payload_keeps_all_old_metadata(client, database, make_case, monkeypatch):
    from test_funds_projection import event
    cid, other = make_case('Synthetic pinned read'), make_case('Synthetic isolated read')
    flow(database, cid, 1, '100.01'); flow(database, cid, 2, '100.01')
    flow(database, other, 3, '999.99')
    event(database, cid, 'SYN-OLD-EVENT', [1, 2], '100.01')
    database.x("insert into event_state values (?, 'old', ?)", [cid, json.dumps({'synthetic_generation': 'old'})])
    analysis_snapshot.invalidate(cid)
    before = client.get('/api/flows', params={'case_id': cid}).json()
    other_before = client.get('/api/flows', params={'case_id': other}).json()
    counted, release = threading.Event(), threading.Event()
    original = database.q_raw

    def pause_after_count(sql, params=None):
        result = original(sql, params)
        if database.in_read_snapshot() and 'select count(*) c from ordinary_facts' in sql:
            counted.set()
            assert release.wait(4)
        return result

    with ThreadPoolExecutor(1) as pool, monkeypatch.context() as patch:
        patch.setattr(database, 'q_raw', pause_after_count)
        pending = pool.submit(client.get, '/api/flows', params={'case_id': cid})
        try:
            assert counted.wait(2)
            with database.transaction():
                database.x('update flows set amount=200.02 where case_id=?', [cid])
                database.x("update resolved_events set amount=200.02 where case_id=?", [cid])
                database.x("update event_members set reason='SYN new event metadata' where case_id=?", [cid])
                database.x("update event_state set revision='new',summary_json=? where case_id=?",
                           [json.dumps({'synthetic_generation': 'new'}), cid])
                analysis_snapshot.invalidate(cid)
        finally:
            release.set()
        response = pending.result(4)
    assert response.status_code == 200 and response.json() == before
    after = client.get('/api/flows', params={'case_id': cid}).json()
    assert after['total'] == 1 and after['rows'][0]['amount'] == '200.02'
    assert after['case_event_summary'] == {'synthetic_generation': 'new'}
    assert before['case_event_summary'] == {'synthetic_generation': 'old'}
    assert after['analysis_revision'] != before['analysis_revision']
    assert client.get('/api/flows', params={'case_id': other}).json() == other_before


def test_case_delete_during_read_returns_old_case_then_404_and_package_restores(
        client, database, make_case, monkeypatch):
    cid, other = make_case('Synthetic deleted read'), make_case('Synthetic retained read')
    flow(database, cid, 1, '10.25'); flow(database, other, 2, '20.03', 'USD')
    before = client.get('/api/flows', params={'case_id': cid}).json()
    other_before = client.get('/api/flows', params={'case_id': other}).json()
    package = client.get(f'/api/cases/{cid}/export')
    assert package.status_code == 200
    entered, release = threading.Event(), threading.Event()
    original = database.q_raw

    def paused(sql, params=None):
        result = original(sql, params)
        if database.in_read_snapshot() and 'select count(*) c from ordinary_facts' in sql:
            entered.set()
            assert release.wait(4)
        return result

    with ThreadPoolExecutor(1) as pool, monkeypatch.context() as patch:
        patch.setattr(database, 'q_raw', paused)
        pending = pool.submit(client.get, '/api/flows', params={'case_id': cid})
        try:
            assert entered.wait(2)
            deleted = client.delete(f'/api/cases/{cid}')
            assert deleted.status_code == 200, deleted.text
        finally:
            release.set()
        response = pending.result(4)
    assert response.status_code == 200 and response.json() == before
    assert client.get('/api/flows', params={'case_id': cid}).status_code == 404
    assert client.get('/api/flows', params={'case_id': other}).json() == other_before
    restored = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', package.content)})
    assert restored.status_code == 200, restored.text
    copied = client.get('/api/flows', params={'case_id': restored.json()['case_id']})
    assert copied.status_code == 200
    assert copied.json()['total'] == 1 and copied.json()['funds'] == before['funds']
    assert copied.json()['rows'][0]['amount'] == '10.25'


def test_missing_revision_initializes_under_writer_lock_before_reading(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic old case without revision')
    flow(database, cid, 1, '90071992547409.91')
    initialized, reads = [], []
    refresh, query = source_ledger.refresh_index, database.q_raw

    def refresh_index(*args, **kwargs):
        assert not database.in_read_snapshot()
        assert database._lock._is_owned() and database._transaction_state.depth > 0
        initialized.append(True)
        return refresh(*args, **kwargs)

    def observe(sql, params=None):
        if 'select count(*) c from ordinary_facts' in sql:
            reads.append(database.in_read_snapshot())
        return query(sql, params)

    monkeypatch.setattr(source_ledger, 'refresh_index', refresh_index)
    monkeypatch.setattr(database, 'q_raw', observe)
    first = client.get('/api/flows', params={'case_id': cid})
    second = client.get('/api/flows', params={'case_id': cid})
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() and first.json()['rows'][0]['amount'] == '90071992547409.91'
    assert initialized == [True] and reads == [False, True]


def test_new_empty_case_and_reopened_database_use_current_binding(client, database, make_case):
    cid = make_case('Synthetic new empty case')
    before = client.get('/api/flows', params={'case_id': cid})
    assert before.status_code == 200
    assert before.json()['total'] == 0 and before.json()['rows'] == []
    with database._lock:
        database.con().execute('checkpoint')
        database._con.close()
        database._con = None
    after = client.get('/api/flows', params={'case_id': cid})
    assert after.status_code == 200 and after.json() == before.json()
    assert database._read_ready[0] is database._con


@pytest.fixture
def readers(database, monkeypatch):
    connect = database.duckdb.connect
    opened, fault = [], {'kind': None}

    class Reader:
        def __init__(self, connection):
            self.connection, self.closed = connection, False

        def execute(self, sql, *args, **kwargs):
            if sql == 'begin transaction read only':
                if fault['kind'] == 'unsupported':
                    raise duckdb.ParserException('synthetic unsupported READ ONLY syntax')
                if fault['kind'] == 'begin':
                    raise OSError('synthetic begin failure')
            elif fault['kind'] == 'query' and sql.startswith('select 1 from analysis_state'):
                raise duckdb.ParserException('synthetic ordinary query parser failure')
            result = self.connection.execute(sql, *args, **kwargs)
            if sql == 'rollback' and fault['kind'] == 'rollback':
                raise OSError('synthetic rollback failure')
            return result

        def close(self):
            self.connection.close()
            self.closed = True
            if fault['kind'] == 'close':
                raise OSError('synthetic close failure')

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def private_connect(*args, **kwargs):
        if fault['kind'] == 'connect':
            raise OSError('synthetic connect failure')
        reader = Reader(connect(*args, **kwargs))
        opened.append(reader)
        return reader

    monkeypatch.setattr(database.duckdb, 'connect', private_connect)
    return opened, fault


def test_snapshot_rejects_writes_shared_handles_and_transaction_escape(database, make_case, readers):
    cid = make_case('Synthetic read-only enforcement')
    flow(database, cid, 1, '1.01')
    analysis_snapshot.invalidate(cid)
    opened, _ = readers
    with database.read_snapshot() as active:
        assert active
        for operation in (
            lambda: database.con(),
            lambda: database.x('update flows set amount=2.02'),
            lambda: database.executemany('delete from flows where id=?', [[1]]),
            lambda: database.insert_dataframe('flows', pd.DataFrame({'id': [2]}), ['id']),
        ):
            with pytest.raises(RuntimeError, match='read snapshot'):
                operation()
        for manager in (database.transaction, database.import_transaction, database.read_snapshot):
            with pytest.raises(RuntimeError, match='read snapshot'):
                with manager():
                    pytest.fail('write/nested context entered')
        for sql in ('commit', 'select 1; commit', 'update flows set amount=2.02', 'set disabled_optimizers=\'\''):
            with pytest.raises(RuntimeError, match='SELECT'):
                database.q_raw(sql)
        assert database.q_raw('select amount from flows where case_id=?', [cid])[0]['amount'] == Decimal('1.01')
    assert len(opened) == 1 and opened[0].closed and not database.in_read_snapshot()


def test_read_only_syntax_capability_failure_closes_then_uses_complete_lock_path(client, database, make_case, readers, monkeypatch):
    cid = make_case('Synthetic older engine capability')
    flow(database, cid, 1, '1.01')
    analysis_snapshot.invalidate(cid)
    opened, fault = readers
    fault['kind'] = 'unsupported'
    query, observations = database.q_raw, []

    def locked_query(sql, params=None):
        assert opened and all(reader.closed for reader in opened)
        assert database._lock._is_owned() and not database.in_read_snapshot()
        observations.append(sql)
        return query(sql, params)

    monkeypatch.setattr(database, 'q_raw', locked_query)
    response = client.get('/api/flows', params={'case_id': cid})
    assert response.status_code == 200 and response.json()['rows'][0]['amount'] == '1.01'
    assert observations and len(opened) == 1 and opened[0].closed


@pytest.mark.parametrize('kind', ['connect', 'begin', 'query', 'rollback', 'close'])
def test_connection_errors_do_not_fallback_and_reused_thread_has_no_context(database, make_case, readers, kind):
    cid = make_case('Synthetic failed reader cleanup')
    flow(database, cid, 1, '1.01')
    analysis_snapshot.invalidate(cid)
    opened, fault = readers
    fault['kind'] = kind
    expected = duckdb.ParserException if kind == 'query' else OSError
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(flows, case_id=cid, size=50)
        with pytest.raises(expected):
            pending.result(4)
        fault['kind'] = None

        def reused():
            assert not database.in_read_snapshot()
            with database.transaction():
                database.x('update flows set amount=2.02 where case_id=?', [cid])
                analysis_snapshot.invalidate(cid)
            return flows(case_id=cid, size=50)

        result = pool.submit(reused).result(4)
    assert result['rows'][0]['amount'] == '2.02'
    assert all(reader.closed for reader in opened)


def test_base_exception_clears_thread_state_and_reader(database, make_case, readers):
    cid = make_case('Synthetic reader base exception')
    opened, _ = readers

    class SyntheticFatal(BaseException):
        pass

    with pytest.raises(SyntheticFatal):
        with database.read_snapshot():
            raise SyntheticFatal()
    assert opened[0].closed and not database.in_read_snapshot()
    with database.transaction():
        database.x("update cases set name='Synthetic recovered thread' where id=?", [cid])


def test_failed_initialization_does_not_publish_reader(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic reader initialization failure')
    flow(database, cid, 1, '1.01')
    before = client.get('/api/flows', params={'case_id': cid}).json()
    with database._lock:
        database._con.close()
        database._con = None
    with monkeypatch.context() as patch:
        def fail(connection):
            connection.execute('create table synthetic_uncommitted_reader_schema(value integer)')
            raise RuntimeError('synthetic initialization failure')
        patch.setattr(database, '_migrate', fail)
        with pytest.raises(RuntimeError, match='synthetic initialization failure'):
            client.get('/api/flows', params={'case_id': cid})
    assert database._con is None
    after = client.get('/api/flows', params={'case_id': cid})
    assert after.status_code == 200 and after.json() == before


def test_checkpoint_and_writer_commit_preserve_open_read_snapshot(database, make_case):
    cid = make_case('Synthetic checkpoint snapshot')
    flow(database, cid, 1, '1.01')
    analysis_snapshot.invalidate(cid)
    errors, checkpoint_errors = [], []
    checkpoint_done = threading.Event()

    def write():
        try:
            with database.transaction():
                database.x('update flows set amount=2.02 where case_id=?', [cid])
                analysis_snapshot.invalidate(cid)
        except BaseException as error:
            errors.append(repr(error))

    def checkpoint():
        try:
            with database._lock:
                database.con().execute('checkpoint')
        except duckdb.TransactionException as error:
            checkpoint_errors.append(str(error))
        finally:
            checkpoint_done.set()

    with database.read_snapshot():
        before = database.q_raw('select amount from flows where case_id=?', [cid])
        writer = threading.Thread(target=write)
        writer.start(); writer.join(3)
        assert not writer.is_alive() and not errors
        checkpoint_thread = threading.Thread(target=checkpoint)
        checkpoint_thread.start()
        assert database.q_raw('select amount from flows where case_id=?', [cid]) == before
    assert checkpoint_done.wait(4)
    checkpoint_thread.join(4)
    assert not checkpoint_thread.is_alive()
    assert all('CHECKPOINT' in error for error in checkpoint_errors)
    with database._lock:
        database.con().execute('checkpoint')
    assert database.q('select amount from flows where case_id=?', [cid]) == [{'amount': '2.02'}]


@pytest.mark.parametrize('fail_first', [False, True])
def test_single_reader_admission_waits_and_releases_after_success_or_exception(
        database, make_case, readers, monkeypatch, fail_first):
    cid = make_case('Synthetic bounded reader admission')
    flow(database, cid, 1, '1.01')
    analysis_snapshot.invalidate(cid)
    before = flows(case_id=cid, size=50)
    opened, _ = readers
    entered, release = threading.Event(), threading.Event()
    second_attempted, second_acquired = threading.Event(), threading.Event()
    labels = threading.local()
    gate, query = database._read_admission, database.q_raw

    class ObservedGate:
        def __enter__(self):
            second = getattr(labels, 'value', '') == 'second'
            if second:
                second_attempted.set()
            gate.__enter__()
            if second:
                assert opened[-1].closed  # First closes before second can open.
                second_acquired.set()
            return self

        def __exit__(self, *args):
            return gate.__exit__(*args)

    def paused(sql, params=None):
        result = query(sql, params)
        if getattr(labels, 'value', '') == 'first' and 'select count(*) c from ordinary_facts' in sql:
            entered.set()
            assert release.wait(5)
            if fail_first:
                raise RuntimeError('synthetic first reader failure')
        return result

    def read(label):
        labels.value = label
        return flows(case_id=cid, size=50)

    # The warm reference reader above has closed; following connections must
    # still be serialized from construction until their close.
    monkeypatch.setattr(database, '_read_admission', ObservedGate())
    monkeypatch.setattr(database, 'q_raw', paused)
    safety = threading.Timer(4, release.set)
    safety.start()
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(read, 'first')
        try:
            assert entered.wait(2)
            second = pool.submit(read, 'second')
            assert second_attempted.wait(2)
            assert not second_acquired.is_set() and not second.done()
            # Reject a writer's nested flow call before waiting on admission.
            with database.transaction():
                with pytest.raises(RuntimeError, match='write transaction'):
                    flows(case_id=cid, size=50)
            assert not release.is_set()
        finally:
            release.set()
            safety.cancel()
        if fail_first:
            with pytest.raises(RuntimeError, match='synthetic first reader failure'):
                first.result(4)
        else:
            assert first.result(4) == before
        assert second.result(4) == before
    assert second_acquired.is_set() and all(reader.closed for reader in opened)


def test_utc_comparability_check_and_page_share_the_old_commit(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic pinned UTC check')
    flow(database, cid, 1, '1.01')
    database.x("""update flows set trade_time_utc=timestamp '2026-01-01 04:00:00.123456',
        trade_time_original='2026-01-01 12:00:00.123456+08:00',trade_time_offset='+08:00' where case_id=?""", [cid])
    params = {'case_id': cid, 'tfrom': '2026-01-01T00:00:00Z', 'tto': '2026-01-02T00:00:00Z'}
    before = client.get('/api/flows', params=params)
    assert before.status_code == 200 and before.json()['total'] == 1
    entered, release = threading.Event(), threading.Event()
    query = database.q_raw

    def paused(sql, params=None):
        result = query(sql, params)
        if sql.endswith('trade_time_utc is not null limit 1'):
            assert database.in_read_snapshot()
            entered.set()
            assert release.wait(4)
        return result

    with ThreadPoolExecutor(1) as pool, monkeypatch.context() as patch:
        patch.setattr(database, 'q_raw', paused)
        pending = pool.submit(client.get, '/api/flows', params=params)
        try:
            assert entered.wait(2)
            with database.transaction():
                database.x('update flows set amount=2.02,trade_time_utc=null,trade_time_original=null,trade_time_offset=null where case_id=?', [cid])
                analysis_snapshot.invalidate(cid)
        finally:
            release.set()
        response = pending.result(4)
    assert response.status_code == 200 and response.json() == before.json()
    assert client.get('/api/flows', params=params).status_code == 422


def test_actual_import_writer_keeps_unknown_signed_currency_and_source_rows_coherent(
        client, database, make_case, monkeypatch):
    from app.services import imports
    from test_automatic_table_import import preview
    from test_folder_evidence import transaction
    from test_native_bank_observations import upload_bytes
    from test_source_ledger import add_source
    from test_unknown_direction_source import unknown

    cid = make_case('Synthetic import and committed flow reader')
    rows = [transaction(**{'交易流水号': 'SYN-FIN', '交易金额': '10.00'}),
            transaction(**{'交易流水号': 'SYN-NEG', '交易金额': '-5.00'}),
            transaction(**{'交易流水号': 'SYN-ZERO', '交易金额': '0.00'}),
            unknown('7.00', **{'交易流水号': 'SYN-UNKNOWN'}),
            transaction(**{'交易流水号': 'SYN-FX', '交易金额': '90071992547409.91', '交易币种': 'USD'})]
    raw, name = upload_bytes(rows)
    add_source(client, cid, raw, name)
    before = client.get('/api/flows', params={'case_id': cid}).json()
    assert before['total'] == 5
    facts = {row['trade_no']: row for row in before['rows']}
    assert facts['SYN-NEG']['amount'] == '-5.00' and facts['SYN-ZERO']['amount'] == '0.00'
    assert facts['SYN-UNKNOWN']['payer_account'] == facts['SYN-UNKNOWN']['payee_account'] == ''
    assert facts['SYN-UNKNOWN']['source_ledger']['direction'] == 'unknown'
    assert [(t['currency'], t['upper']) for t in before['funds']['totals']] == [('CNY', '10.00'), ('USD', '90071992547409.91')]
    added, name = upload_bytes([transaction(**{'交易流水号': 'SYN-ADDED', '交易时间': '2026-01-02 10:00:00',
        '交易金额': '1.01', '交易币种': 'USD'})])
    view = preview(client, cid, added, name)
    entered, release = threading.Event(), threading.Event()
    mark = imports._mark_mains

    def paused(*args, **kwargs):
        mark(*args, **kwargs)
        entered.set()
        assert release.wait(5)

    with monkeypatch.context() as patch:
        patch.setattr(imports, '_mark_mains', paused)
        base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
        started = client.post(base + '/commit', json={'mode': view['mode'], 'mapping': view['mapping'], 'mark_accounts': []})
        assert started.status_code == 200 and started.json()['started']
        assert entered.wait(4)
        safety = threading.Timer(4, release.set)
        safety.start()
        try:
            response = client.get('/api/flows', params={'case_id': cid})
            assert response.status_code == 200 and not release.is_set()
            assert response.json() == before
        finally:
            release.set()
            safety.cancel()
        completed = client.get(base)
        assert completed.status_code == 200 and completed.json()['stage'] == 'done'
    after = client.get('/api/flows', params={'case_id': cid}).json()
    assert after['total'] == 6 and after['analysis_revision'] != before['analysis_revision']
    assert [(t['currency'], t['upper']) for t in after['funds']['totals']] == [('CNY', '10.00'), ('USD', '90071992547410.92')]
    assert {row['trade_no']: row for row in after['rows']}['SYN-UNKNOWN'] == facts['SYN-UNKNOWN']
