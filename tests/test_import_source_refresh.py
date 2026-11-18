"""Append source projections atomically; rebuild actual mutations in full."""
import time
from decimal import Decimal

import pytest

from app import analysis_snapshot, importer, source_ledger
from app.import_staging import StagedSequence
from test_import_report import complete, payload, row


def card_payload(card):
    from test_table_source_contracts import encoded_table
    return encoded_table(['date', '本方账号', '本方卡号', '对方账号', '收付标志', 'amount', 'currency', '交易流水号'],
                         [['2026-11-13 09:01:00', '000SYN-CARD-OWNER', card, '000SYN-CARD-COUNTER',
                           '支出', '0.20', 'CNY', 'SYN-CARD-TRANSFER']])


def select_writer_path(monkeypatch, materialized):
    commit = importer.commit_rows
    observed = []

    def write(filename, mode, rows, *args, **kwargs):
        observed.append(isinstance(rows, StagedSequence))
        if materialized:
            rows = list(rows)
        return commit(filename, mode, rows, *args, **kwargs)

    monkeypatch.setattr(importer, 'commit_rows', write)
    return observed


@pytest.mark.parametrize('materialized', [False, True], ids=['staged-input', 'materialized-input'])
def test_ledger_append_and_repeat_refresh_only_after_complete_journal(client, database, make_case, monkeypatch, materialized):
    cid = make_case('Synthetic final ledger refresh')
    before = analysis_snapshot.revision(cid)
    paths = select_writer_path(monkeypatch, materialized)
    refresh = source_ledger.refresh_index
    publications, applied, deleted = [], [], []

    class Connection:
        def __init__(self, connection): self.connection = connection
        def execute(self, sql, params=None):
            if sql.lstrip().startswith(('insert into source_ledger_index', 'insert into flow_source_cards')):
                applied.append(tuple(params[:2]))
            if sql.lstrip().startswith(('delete from source_ledger_index', 'delete from flow_source_cards')):
                deleted.append(sql)
            return self.connection.execute(sql, params or [])
        def __getattr__(self, name): return getattr(self.connection, name)

    def observe(connection, case_id=None, *source_scope):
        assert case_id == cid
        publications.append((database.q_raw('select count(*) n from import_records where case_id=?', [cid])[0]['n'],
                             analysis_snapshot.revision(cid)))
        return refresh(Connection(connection), case_id, *source_scope)

    monkeypatch.setattr(source_ledger, 'refresh_index', observe)
    data = payload([row('SYN-1', '收入', '9999999999999999.99', stamp='2026-11-13 09:00:00.123456'),
                    row('SYN-2', '支出', '0.01', currency='EUR')])
    first, first_view = complete(client, cid, data)
    second, second_view = complete(client, cid, data, 'synthetic-repeat.csv')
    assert [count for count, _ in publications] == [2, 4]
    assert publications[0][1] != before and publications[1][1] != publications[0][1]
    assert paths == [True, True]
    assert database.q_raw('select cast(amount as varchar) amount,currency from flows where case_id=? order by trade_no', [cid]) == [
        {'amount': '9999999999999999.99', 'currency': 'CNY'}, {'amount': '0.01', 'currency': 'EUR'}]
    assert database.q_raw('select count(*) n from flow_sources where case_id=?', [cid])[0]['n'] == 4
    assert database.q_raw('select disposition,count(*) n from import_records where case_id=? group by disposition order by disposition', [cid]) == [
        {'disposition': 'duplicate', 'n': 2}, {'disposition': 'imported', 'n': 2}]
    assert client.get(first).json()['stage'] == client.get(second).json()['stage'] == 'done'
    assert applied == [(cid, first_view['import_id'])] * 2 + [(cid, second_view['import_id'])] * 2
    assert deleted == []


@pytest.mark.parametrize('materialized', [False, True], ids=['staged-input', 'materialized-input'])
def test_final_refresh_failure_rolls_back_facts_sources_and_generation(client, database, make_case, monkeypatch, materialized):
    from test_source_ledger_batch import complex_source

    cid = make_case('Synthetic final refresh rollback')
    old, _ = complex_source()
    complete(client, cid, old, 'synthetic-old.zip')
    tables = ['flows', 'account_ids', 'batches', 'flow_sources', 'import_sources', 'import_members', 'import_records',
              'import_source_operations', 'source_ledger_index', 'flow_source_cards', 'analysis_state', 'evidence_sources',
              'resolved_events', 'event_members', 'resolution_entities', 'resolved_endpoints']

    def snapshot():
        return {table: database.q_raw(f'select * from {table} where case_id=? order by all', [cid]) for table in tables}

    before, revision = snapshot(), analysis_snapshot.revision(cid)
    paths = select_writer_path(monkeypatch, materialized)
    refresh, attempted = source_ledger.refresh_index, []

    def fail_after_publication(connection, case_id=None, *source_scope):
        assert case_id == cid
        assert analysis_snapshot.revision(cid) != revision
        attempted.append(database.q_raw('select count(*) n from import_records where case_id=?', [cid])[0]['n'])
        refresh(connection, case_id, *source_scope)
        raise RuntimeError('synthetic final index publication failure')

    monkeypatch.setattr(source_ledger, 'refresh_index', fail_after_publication)
    response = client.post(f'/api/v1/cases/{cid}/imports/preview', files={'file': ('synthetic-new.csv', card_payload('000SYN-NEW-CARD'))})
    assert response.status_code == 200
    preview = response.json()
    base = f'/api/v1/cases/{cid}/imports/{preview["import_id"]}'
    assert client.post(base + '/commit', json={'mode': preview['mode'], 'mapping': preview['mapping'], 'mark_accounts': []}).status_code == 200
    until = time.monotonic() + 20
    while time.monotonic() < until:
        job = client.get(base).json()
        if job['stage'] in {'done', 'error'}:
            break
        time.sleep(.01)
    assert job['stage'] == 'error' and job['error_code'] == 'import_failed' and job['result'] is None
    assert paths == [True] and attempted == [14]
    assert snapshot() == before
    assert analysis_snapshot.revision(cid) == revision


def test_append_duplicate_cards_matches_full_rebuild_and_withdrawal_removes_only_retired_support(
        client, database, make_case):
    from test_source_ledger_scope_plan import legacy_refresh_index

    cid, foreign = make_case('Synthetic appended cards'), make_case('Synthetic untouched cards')
    complete(client, foreign, card_payload('000SYN-FOREIGN-CARD'))

    def projection():
        return {table: database.q_raw(f'select * from {table} order by all')
                for table in ('source_ledger_index', 'flow_source_cards')}

    def check_full():
        before = projection()
        with database.import_transaction() as connection:
            legacy_refresh_index(connection, cid)
        assert projection() == before

    first, view = complete(client, cid, card_payload('000SYN-FIRST-CARD'))
    assert view['table_summary']['profile'] == 'funds-trace-table-v69'
    original = database.q_raw('select * from flows where case_id=? order by id', [cid])
    first_receipt = client.get(first).json()
    check_full()
    repeated, _ = complete(client, cid, card_payload('000SYN-SECOND-CARD'), 'synthetic-second.csv')
    receipt = client.get(repeated).json()['result']
    assert (receipt['ok'], receipt['rows_dup']) == (0, 1)
    assert database.q_raw('select * from flows where case_id=? order by id', [cid]) == original
    assert database.q_raw('select own_card from flow_source_cards where case_id=? order by own_card', [cid]) == [
        {'own_card': '000SYN-FIRST-CARD'}, {'own_card': '000SYN-SECOND-CARD'}]
    assert database.q_raw('select * from flow_source_card_projection where case_id=?', [cid]) == []
    check_full()
    assert client.delete(f"/api/batches/{receipt['batch_id']}", params={'case_id': cid}).status_code == 200
    assert database.q_raw('select own_card from flow_source_cards where case_id=?', [cid]) == [{'own_card': '000SYN-FIRST-CARD'}]
    check_full()
    assert client.get(first).json() == first_receipt
    assert database.q_raw('select own_card from flow_source_cards where case_id=?', [foreign]) == [{'own_card': '000SYN-FOREIGN-CARD'}]


def test_append_projection_merges_maxima_and_cards_with_frozen_inactive_and_unlinked_history(database, make_case):
    import json
    from app import table_import
    from test_source_ledger_scope_plan import card_sources, legacy_refresh_index

    cid, _ = card_sources(database, make_case)
    legacy_refresh_index(database.con())
    sid = '8' * 32
    database.x("""insert into import_sources(source_id,case_id,filename,sha256,size_bytes,mode,mapping_json,sheet,parser_version,row_kind)
        values (?,?,'SYN-append.csv',?,0,'B','{}','',?,'table_record')""", [sid, cid, 'c' * 64, table_import.PROFILE])
    database.x("insert into import_members values (?,?, 'SYN-append.csv','table',?,0,'utf-8-sig',4)", [cid, sid, 'd' * 64])
    for number, (flow, account, name, bank) in enumerate([(42, None, 'z', None), (42, 'Ω', 'a', 'c'),
                                                       (43, None, None, ''), (None, 'SYN-UNLINKED', None, None)], 1):
        evidence = {'policy': source_ledger.POLICY, 'kind': 'ledger', 'direction': 'out',
                    'row': {'self_account': 'SYN-A'}, 'own_card': 'SYN-APPENDED-CARD',
                    'counterparty': {'account': account, 'name': name, 'bank': bank}}
        database.x("insert into import_records values (?,?,?,'SYN-append.csv',?,'SYN-key','imported','SYN-reason',?)",
                   [cid, sid, number, number + 100, json.dumps({source_ledger.FIELD: json.dumps(evidence)})])
        if flow is not None:
            database.x("insert into flow_sources values (?,?,?,?,'imported')", [cid, sid, number, flow])
    with database.import_transaction() as connection:
        source_ledger.refresh_index(connection, cid, sid)
    before = {table: database.q_raw(f'select * from {table} order by all')
              for table in ('source_ledger_index', 'flow_source_cards')}
    legacy_refresh_index(database.con(), cid)
    assert {table: database.q_raw(f'select * from {table} order by all') for table in before} == before
    assert database.q_raw('select counter_account,counter_name,counter_bank from source_ledger_index where case_id=? and flow_id=42', [cid]) == [
        {'counter_account': 'Ω', 'counter_name': 'z', 'counter_bank': 'c'}]
    assert {'case_id': cid, 'flow_id': 42, 'role': 'payer', 'own_account': 'SYN-A', 'own_card': 'SYN-APPENDED-CARD'} in before['flow_source_cards']


def test_standalone_writer_still_refreshes_source_index(database, make_case, flow_row, monkeypatch):
    cid = make_case('Synthetic standalone writer refresh')
    refresh, calls = source_ledger.refresh_index, []

    def observe(connection, case_id=None):
        calls.append(case_id)
        return refresh(connection, case_id)

    monkeypatch.setattr(source_ledger, 'refresh_index', observe)
    result = importer.commit_rows('synthetic.csv', 'B', [flow_row(amount=Decimal('0.01'), balance=None)], case_id=cid)
    assert result['ok'] == 1 and calls == [cid]
    assert database.q_raw('select revision from analysis_state where case_id=?', [cid])


def test_occurrence_staged_writer_forwards_deferred_refresh(client, database, make_case, monkeypatch):
    from app import evidence_resolution
    from test_archive_auxiliary_rows import source

    cid = make_case('Synthetic occurrence writer publication')
    analysis_snapshot.revision(cid)
    staged, refresh = importer._commit_staged_evidence, source_ledger.refresh_index
    paths, publications = [], []

    def write(filename, mode, rows, stats, progress, case_id, provenance, **options):
        assert isinstance(rows, StagedSequence)
        assert provenance['parser_version'] in evidence_resolution.OCCURRENCE_PROFILES
        paths.append(options['refresh_source_index'])
        return staged(filename, mode, rows, stats, progress, case_id, provenance, **options)

    def publish(connection, case_id=None, *source_scope):
        publications.append(database.q_raw('select count(*) n from import_records where case_id=?', [cid])[0]['n'])
        return refresh(connection, case_id, *source_scope)

    monkeypatch.setattr(importer, '_commit_staged_evidence', write)
    monkeypatch.setattr(source_ledger, 'refresh_index', publish)
    base, _ = complete(client, cid, source('evidence', malformed=False), 'synthetic-occurrence.zip')
    job = client.get(base).json()
    assert paths == [False]
    assert publications == [job['total']] and publications[0] > 0
