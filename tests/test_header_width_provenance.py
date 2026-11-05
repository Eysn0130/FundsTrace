"""New archive versions through synthetic public persistence and compatibility paths."""
import datetime as dt
import io
import json
import zipfile
from decimal import Decimal

import pytest

from app import feedback_import, table_archive, table_import
from test_folder_evidence import account, archive, complete, preview, scoped, table, transaction
from test_table_currency_region_lifecycle import source_snapshot
from test_table_write_integrity import persisted
from test_feedback_time_precision import flows
from xls_fixtures import workbook


def materials(xls=False, *, conflicting_account=False):
    rows = [transaction(**{'交易时间': '2026-01-01 10:00:00.123456', '交易金额': '90071992547409.93',
                           '交易余额': '', '交易流水号': 'SYN-WIDTH-1'}),
            transaction(**{'交易时间': '2026-01-01 10:00:00.123457', '交易金额': '2.03',
                           '交易余额': '0.00', '交易币种': 'USD', '收付标志': '进', '交易流水号': 'SYN-WIDTH-2'}),
            transaction(**{'交易时间': 'not-a-time', '交易流水号': 'SYN-WIDTH-INVALID'})]
    headers = list(feedback_import.COLUMNS)
    payload = (workbook([('Synthetic', [headers, *[[row.get(h, '') for h in headers] for row in rows]])])
               if xls else table('transaction', rows))
    return [('SYN-REQUEST/Synthetic bank交易明细信息' + ('.xls' if xls else '.csv'), payload),
            ('account.csv', table('account', [account(number='SYN-A2' if conflicting_account else 'SYN-A')])),
            ('manifest.csv', table('task', [{'请求单号': 'SYN-REQUEST', '银行名称': 'Synthetic bank'}])),
            ('readme.txt', b'Synthetic attachment, no transaction facts')]


def selected(xls, old=False):
    return ((table_archive.MICROSECOND_XLS_PROFILE if xls else table_archive.MICROSECOND_PROFILE)
            if old else (table_archive.NATIVE31_XLS_PROFILE if xls else table_archive.NATIVE31_EVIDENCE_PROFILE))


def ingest(client, cid, members, *, zipped=False):
    view = preview(client, cid, members, zipped=zipped, scope_bank=None)
    job = complete(client, cid, view)
    assert job['stage'] == 'done', job
    return view, job


def source_report(client, cid, sid):
    response = client.get(f'/api/v1/cases/{cid}/sources/{sid}/report')
    assert response.status_code == 200, response.text
    return response.json()


def export_restore(client, cid):
    response = client.get(f'/api/cases/{cid}/export')
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as packed:
        assert json.loads(packed.read('meta.json'))['format'] == 6
    restored = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', response.content)})
    assert restored.status_code == 200, restored.text
    return restored.json()['case_id']


@pytest.mark.parametrize('xls', [False, True], ids=['csv', 'xls'])
@pytest.mark.parametrize('zipped', [False, True], ids=['directory', 'zip'])
def test_default_profile_public_commit_sources_and_reopen(client, database, make_case, xls, zipped):
    cid = make_case('Synthetic header version ingress')
    view, job = ingest(client, cid, materials(xls), zipped=zipped)
    assert view['table_summary']['profile'] == selected(xls)
    assert (job['result']['ok'], job['result']['rows_dup']) == (2, 0)
    page = flows(client, cid)
    assert page['total'] == 2
    facts = {row['trade_no']: row for row in page['items']}
    for number, amount, balance, currency, micros, side in [
        ('SYN-WIDTH-1', '90071992547409.93', None, 'CNY', 123456, 'payer'),
        ('SYN-WIDTH-2', '2.03', '0.00', 'USD', 123457, 'payee'),
    ]:
        row = facts[number]
        assert (row['amount'], row['balance'], row['currency']) == (amount, balance, currency)
        assert dt.datetime.fromisoformat(row['trade_time']) == dt.datetime(2026, 1, 1, 10, 0, 0, micros)
        assert row[f'raw_{side}_account'] == 'SYN-A'
        assert row[f'{side}_card'] == 'SYN-C1'
        sources = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()['items']
        assert len(sources) == 1 and sources[0]['parser_version'] == selected(xls)
        assert sources[0]['member_row'] == (2 if micros == 123456 else 3)
    records = source_report(client, cid, view['import_id'])['items']
    tx = [r for r in records if '交易明细信息' in r['member_path']]
    assert [(r['source_row'], r['disposition']) for r in tx] == [(2, 'imported'), (3, 'imported'), (4, 'unresolved')]
    before = source_snapshot(database, cid, view['import_id'])
    with database._lock:
        database.con().execute('checkpoint')
        database._con.close()
        database._con = None
        database.con()
    assert source_snapshot(database, cid, view['import_id']) == before
    assert flows(client, cid) == page
    restored = export_restore(client, cid)
    assert flows(client, restored)['funds'] == page['funds']
    assert database.q_raw('select distinct parser_version from import_sources where case_id=?', [restored]) == [{'parser_version': selected(xls)}]


@pytest.mark.parametrize('xls', [False, True], ids=['v8-v10', 'v9-v11'])
def test_old_new_old_same_source_keeps_raw_facts_and_all_supports(client, database, make_case, monkeypatch, xls):
    cid = make_case('Synthetic historical header versions')
    members = materials(xls)
    with monkeypatch.context() as old:
        old.setattr(table_archive, 'evidence_profile', lambda _: selected(xls, old=True))
        prior, first = ingest(client, cid, members)
    assert prior['table_summary']['profile'] == selected(xls, old=True)
    historical = source_snapshot(database, cid, prior['import_id'])
    raw = database.q_raw('select * from flows where case_id=? order by id', [cid])
    prior_coverage = client.get(f'/api/v1/cases/{cid}/stats').json()['import_coverage']
    old_copy = export_restore(client, cid)
    assert database.q_raw('select distinct parser_version from import_sources where case_id=?', [old_copy]) == [{'parser_version': selected(xls, old=True)}]
    assert flows(client, old_copy)['funds'] == flows(client, cid)['funds']
    current, second = ingest(client, cid, members)
    assert current['table_summary']['profile'] == selected(xls)
    with monkeypatch.context() as old:
        old.setattr(table_archive, 'evidence_profile', lambda _: selected(xls, old=True))
        repeated, third = ingest(client, cid, members)
    for result in (second, third):
        assert (result['result']['ok'], result['result']['rows_dup']) == (0, 2)
    assert database.q_raw('select * from flows where case_id=? order by id', [cid]) == raw
    assert source_snapshot(database, cid, prior['import_id']) == historical
    current_coverage = client.get(f'/api/v1/cases/{cid}/stats').json()['import_coverage']
    for field in ('unresolved_records', 'cancelled_records', 'financial_interpretation_complete'):
        assert current_coverage[field] == prior_coverage[field]
    for row in flows(client, cid)['items']:
        supports = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()['items']
        assert {s['source_id'] for s in supports} == {prior['import_id'], current['import_id'], repeated['import_id']}
        assert {s['parser_version'] for s in supports} == {selected(xls), selected(xls, old=True)}
    old_records = source_report(client, cid, prior['import_id'])['items']
    new_records = source_report(client, cid, current['import_id'])['items']
    coordinate = lambda records: [(r['member_path'], r['source_row'], r['record_key']) for r in records]
    assert coordinate(old_records) == coordinate(new_records)
    # Old evidence package remains usable in this candidate; replay new defaults there.
    _, copied_replay = ingest(client, old_copy, members)
    assert (copied_replay['result']['ok'], copied_replay['result']['rows_dup']) == (0, 2)
    copy = export_restore(client, cid)
    assert flows(client, copy)['funds'] == flows(client, cid)['funds']
    assert {r['parser_version'] for r in database.q_raw('select parser_version from import_sources where case_id=?', [copy])} == {selected(xls), selected(xls, old=True)}
    assert client.delete(f"/api/batches/{first['result']['batch_id']}", params={'case_id': cid}).status_code == 200
    assert flows(client, cid)['total'] == 2
    assert client.delete(f"/api/batches/{second['result']['batch_id']}", params={'case_id': cid}).status_code == 200
    assert flows(client, cid)['total'] == 2


@pytest.mark.parametrize('xls', [False, True])
def test_new_profile_failure_after_write_is_atomic(client, database, make_case, monkeypatch, xls):
    cid = make_case('Synthetic version rollback')
    before = persisted(database)
    view = preview(client, cid, materials(xls), scope_bank=None)
    assert view['table_summary']['profile'] == selected(xls)
    original = database.insert_dataframe
    def broken(name, frame, columns):
        result = original(name, frame, columns)
        if name == 'flows':
            raise RuntimeError('synthetic post-write failure')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(database, 'insert_dataframe', broken)
        failed = complete(client, cid, view)
    assert failed['stage'] == 'error' and failed['result'] is None
    assert persisted(database) == before
    assert complete(client, cid, view)['stage'] == 'done'


def test_conflicting_new_auxiliary_keeps_old_source_and_revokes_unsafe_relation(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic cross-version conflicting evidence')
    tx = transaction(**{'交易账号': '', '交易时间': '2026-01-01 10:00:00.123456'})
    members = scoped([('tx.csv', table('transaction', [tx])), ('acc.csv', table('account', [account()])),
                      ('readme.txt', b'Synthetic attachment')], 'Synthetic bank')
    with monkeypatch.context() as old:
        old.setattr(table_archive, 'evidence_profile', lambda _: table_archive.MICROSECOND_PROFILE)
        prior, _ = ingest(client, cid, members)
    historical = source_snapshot(database, cid, prior['import_id'])
    raw = database.q_raw('select * from flows where case_id=?', [cid])
    before = flows(client, cid)['items'][0]
    registry = client.get(f'/api/v1/cases/{cid}/resolution').json()['entities']
    owner = next(item for item in registry if item['entity_id'] == before['payer_account'])
    assert (owner['kind'], owner['account_number']) == ('account', 'SYN-A')
    assert before['payer_display_account'] == 'SYN-C1'  # Row display retains its source card.
    _, conflict = ingest(client, cid, [('conflict.csv', table('account', [account(number='SYN-A2')])),
                                      ('readme.txt', b'Synthetic attachment')])
    after = flows(client, cid)['items'][0]
    assert after['payer_display_account'] == 'SYN-C1'
    assert after['payer_card_state'] == 'conflict'
    assert after['payer_account'] != before['payer_account']
    assert database.q_raw('select * from flows where case_id=?', [cid]) == raw
    assert source_snapshot(database, cid, prior['import_id']) == historical
    assert client.delete(f"/api/batches/{conflict['result']['batch_id']}", params={'case_id': cid}).status_code == 200
    assert flows(client, cid)['items'][0]['payer_account'] == before['payer_account']


@pytest.mark.parametrize('conflicting_new', [False, True])
def test_explicit_archive_precedence_prefers_v11_then_v10_then_v9_equal_facts(client, database, make_case, monkeypatch, conflicting_new):
    from test_archive_profile_upgrade import source, transaction as historical_row, frozen_preview, report
    cid = make_case('Synthetic two-digit precedence')
    _, content = source([historical_row('SYN-PREFER', 'SYN-PREFER')])
    views = []
    # Seed legacy assessments via public parsing. The later fixture models
    # independently retained same-key assessments to exercise ordering, like
    # the existing v2/v1 precedence regression; it is not a parser receipt claim.
    for _ in range(3):
        view = frozen_preview(client, cid, content, monkeypatch)
        assert complete(client, cid, view)['stage'] == 'done'
        views.append(view)
    original = database.q_raw('select id from flows where case_id=?', [cid])[0]['id']
    fields = 'case_id,batch_id,' + ','.join((*table_import.FLOW_FIELDS, 'self_account', 'self_name'))
    candidates = {}
    for version, view in zip((9, 10, 11), views):
        fid = database.q_raw(f'insert into flows({fields}) select {fields} from flows where id=? returning id', [original])[0]['id']
        database.x('update flow_sources set flow_id=? where source_id=?', [fid, view['import_id']])
        database.x('update import_sources set parser_version=? where source_id=?', [f'funds-trace-table-archive-v{version}', view['import_id']])
        candidates[version] = fid
    if conflicting_new:
        database.x("update flows set self_account='SYN-DIFFERENT' where id=?", [candidates[11]])
    repeated = frozen_preview(client, cid, content, monkeypatch)
    job = complete(client, cid, repeated)
    assert job['stage'] == 'done' and (job['result']['ok'], job['result']['rows_dup']) == (0, 1)
    assert report(client, cid, repeated)['items'][0]['flow_id'] == str(candidates[10 if conflicting_new else 11])


@pytest.mark.parametrize('xls', [False, True])
def test_new_profiles_keep_microsecond_cancellation_and_package_state(client, database, make_case, xls):
    from test_feedback_cancellations import round_trip
    rows = round_trip()
    for row, tail in zip(rows, ('.099999', '.100000', '.100001')):
        row['交易时间'] = '2026-01-01 10:00:00' + tail
    members = [('transactions.csv', table('transaction', rows)),
               ('readme.txt', b'Synthetic attachment')]
    if xls:
        members.append(('other.xls', workbook([('Synthetic', [
            ['date', 'payer_account', 'payee_account', 'amount'],
            ['2026-01-02', 'SYN-X', 'SYN-Y', '3.00'],
        ])])))
    cid = make_case('Synthetic new-version cancellation')
    view, _ = ingest(client, cid, members)
    assert view['table_summary']['profile'] == selected(xls)
    assert flows(client, cid)['total'] == (4 if xls else 3)
    assert flows(client, cid)['funds']['observation_count'] == (2 if xls else 1)
    records = source_report(client, cid, view['import_id'])['items']
    assert sum(r['disposition'] == 'cancelled' for r in records) == 2
    copied = export_restore(client, cid)
    assert flows(client, copied)['funds'] == flows(client, cid)['funds']


def test_xls_negative_member_keeps_v9_unresolved_boundary():
    from test_feedback_cancellations import round_trip
    headers = list(feedback_import.COLUMNS)
    payload = workbook([('Synthetic', [headers, *[[row.get(h, '') for h in headers] for row in round_trip()]])])
    content = archive([('transactions.xls', payload)])
    plans = [table_archive.parse(io.BytesIO(content), profile=p)
             for p in (table_archive.MICROSECOND_XLS_PROFILE, table_archive.HEADER_WIDTH_XLS_PROFILE)]
    try:
        # Existing pairing is CSV-only. The new performance version must not
        # silently add XLS cancellation semantics or drop its positive facts.
        for plan in plans:
            assert [r['disposition'] for r in plan['records']] == ['accepted', 'accepted', 'unresolved']
            assert plan['records'][2]['reason_code'] == 'negative_amount'
            assert [r['amount'] for r in plan['rows']] == [Decimal('1.00'), Decimal('10.00')]
            assert plan['cancellations'] == []
        for field in ('rows', 'records', 'members', 'documents', 'stats'):
            assert plans[0][field] == plans[1][field], field
    finally:
        for plan in plans:
            plan['_details_pool'].close()


@pytest.mark.parametrize('profile', [table_archive.MICROSECOND_PROFILE, table_archive.MICROSECOND_XLS_PROFILE,
                                     table_archive.HEADER_WIDTH_PROFILE, table_archive.HEADER_WIDTH_XLS_PROFILE])
def test_new_cancellation_proof_still_excludes_later_legacy_positive_and_survives_reopen(
        client, database, make_case, monkeypatch, profile):
    from test_table_cancellation_lifecycle import paired_source, import_version, count, coverage
    cid = make_case('Synthetic new proof and historical positive')
    _, content = paired_source()
    current, current_job = import_version(client, cid, content, monkeypatch, profile)
    assert count(database, 'flows', cid) == count(database, 'effective_flows', cid) == 1
    # A later explicit historical parser admits the positive source row again.
    # Its raw fact may be retained, but the new-version proof must still exclude it.
    historical, _ = import_version(client, cid, content, monkeypatch, table_archive.REFERENCE_PROFILE)
    assert count(database, 'flows', cid) == 2
    assert count(database, 'effective_flows', cid) == 1
    raw = database.q_raw('select * from flows where case_id=? order by id', [cid])
    old = source_snapshot(database, cid, historical['import_id'])
    result = coverage(client, cid)
    assert result['cancelled_records'] == 2 and result['financial_interpretation_complete']
    with database._lock:
        database.con().execute('checkpoint')
        database._con.close()
        database._con = None
        database.con()
    assert count(database, 'effective_flows', cid) == 1
    assert database.q_raw('select * from flows where case_id=? order by id', [cid]) == raw
    assert source_snapshot(database, cid, historical['import_id']) == old
    copied = export_restore(client, cid)
    assert count(database, 'flows', copied) == 2 and count(database, 'effective_flows', copied) == 1
    # Withdrawing the sole proof revokes its current exclusion authority. The
    # independent historical positive returns, while its facts and the withdrawn
    # source's matched-cancellation journal remain unchanged.
    response = client.delete(f"/api/batches/{current_job['result']['batch_id']}", params={'case_id': cid})
    assert response.status_code == 200, response.text
    assert count(database, 'flows', cid) == 2 and count(database, 'effective_flows', cid) == 2
    assert database.q_raw('select active from evidence_sources where case_id=? and source_id=?',
                          [cid, current['import_id']]) == [{'active': False}]
    assert sum(r['disposition'] == 'cancelled' for r in source_report(client, cid, current['import_id'])['items']) == 2
    assert source_snapshot(database, cid, historical['import_id']) == old
    fields = (*table_import.FLOW_FIELDS, 'self_account', 'self_name')
    after = database.q_raw('select * from flows where case_id=? order by id', [cid])
    assert [tuple(row[k] for k in fields) for row in after] == [tuple(row[k] for k in fields) for row in raw]
    with database._lock:
        database.con().execute('checkpoint')
        database._con.close()
        database._con = None
        database.con()
    assert count(database, 'flows', cid) == 2 and count(database, 'effective_flows', cid) == 2
    assert source_snapshot(database, cid, historical['import_id']) == old
    assert database.q_raw('select active from evidence_sources where case_id=?', [cid]) == [{'active': False}]
    assert sum(r['disposition'] == 'cancelled' for r in source_report(client, cid, current['import_id'])['items']) == 2
    restored_after_withdrawal = export_restore(client, cid)
    assert count(database, 'flows', restored_after_withdrawal) == 2
    assert count(database, 'effective_flows', restored_after_withdrawal) == 2
    assert database.q_raw('select active from evidence_sources where case_id=?', [restored_after_withdrawal]) == [{'active': False}]
    assert database.q_raw('select active from import_source_operations where case_id=? order by active',
                          [restored_after_withdrawal]) == [{'active': False}, {'active': True}]
    assert database.q_raw("select count(*) n from import_records where case_id=? and disposition='cancelled'",
                          [restored_after_withdrawal]) == [{'n': 2}]
    restored_facts = database.q_raw('select * from flows where case_id=? order by trade_no,trade_time', [restored_after_withdrawal])
    expected_facts = sorted(raw, key=lambda row: (row['trade_no'], row['trade_time']))
    assert [tuple(row[k] for k in fields) for row in restored_facts] == [tuple(row[k] for k in fields) for row in expected_facts]
