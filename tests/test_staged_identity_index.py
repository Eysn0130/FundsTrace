"""A candidate digest cannot establish evidence observation identity."""
import datetime as dt
from decimal import Decimal

import pytest

from app import importer, import_staging, table_import
from test_event_import import csv_rows
from test_folder_evidence import accepted, account, complete, preview, table, transaction


@pytest.mark.parametrize('chunk', [1, 2, 128])
def test_collision_candidates_keep_complete_identity_and_namespace(database, monkeypatch, chunk):
    monkeypatch.setattr(importer, '_staged_identity_digest', lambda encoded: b'collision')
    monkeypatch.setattr(import_staging, 'PLAN_CHUNK', chunk)
    base = ('occurrence', 'scope', ('SYN-T', dt.datetime(2026, 1, 1, 1, 2, 3, 123456),
            Decimal('10.25'), None, 'CNY', 'summary'), 'SYN-SELF', 'Synthetic owner')
    keys = [base, ('another-occurrence', *base[1:]), (base[0], 'another-scope', *base[2:]),
            (*base[:3], 'ANOTHER-SELF', base[4]), (*base[:4], 'another name')]
    for position, value in [(0, 'SYN-T2'), (1, dt.datetime(2026, 1, 1, 1, 2, 3, 123457)),
                            (2, Decimal('10.26')), (3, Decimal('0.00')), (4, 'USD'), (5, 'other summary')]:
        facts = list(base[2]);facts[position] = value
        keys.append((base[0], base[1], tuple(facts), *base[3:]))
    store = import_staging.PlanStore()
    try:
        index = importer._StagedIdentityIndex(store)
        for fid, key in enumerate(keys, 1):
            assert index.get(key) is None
            index.add(key, fid)
            assert index.get(key) == fid
        index.flush()
        assert [index.get(key) for key in keys] == list(range(1, len(keys) + 1))
        assert importer._StagedIdentityIndex(store).get(base) is None
        plan = store._con.execute('explain query plan select identity,flow_id from evidence_identities where namespace=? and digest=? order by rowid', (index.namespace, b'collision')).fetchall()
        assert any('USING INDEX evidence_identity_candidates' in row[-1] for row in plan)
        assert not any('TEMP B-TREE' in row[-1] for row in plan)
    finally:
        store.close()
    with pytest.raises(ValueError, match='closed'):
        index.get(base)


@pytest.mark.parametrize('remove_first', [True, False])
def test_public_collision_repeats_scopes_withdrawal_and_reused_link_validation(client, database, make_case, monkeypatch, remove_first):
    monkeypatch.setattr(importer, '_staged_identity_digest', lambda encoded: b'collision')
    monkeypatch.setattr(importer, 'WRITE_CHUNK', 2)
    monkeypatch.setattr(import_staging, 'PLAN_CHUNK', 2)
    cid = make_case('Synthetic forced candidate collisions')
    rows = [transaction(**{'交易流水号': f'SYN-{i}',
                '交易时间': f'2026-01-01 10:00:00.{123456+i:06}',
                '交易币种': 'USD' if i == 2 else 'CNY',
                '交易户名': f'Synthetic owner {i}'}) for i in range(5)]
    payload = csv_rows([{field: row.get(field, '') for field in sorted(table_import.BANK_FEEDBACK_32_COLUMNS)} for row in rows])
    def sources(bank):
        return [(f'SYN-REQUEST/{bank}交易明细信息.csv', payload),
                ('manifest.csv', table('task', [{'请求单号': 'SYN-REQUEST', '银行名称': bank}])),
                ('accounts.csv', table('account', [account(bank=bank)]))]
    members = sources('Synthetic bank')
    _, first = accepted(client, cid, members)
    original = database.q_raw('select * from flows where case_id=? order by id', [cid])
    assert len(original) == 5 and {row['amount'] for row in original} == {Decimal('10.25')}
    _, second = accepted(client, cid, members, zipped=True)
    assert (second['ok'], second['rows_dup']) == (0, 5)
    assert database.q_raw('select * from flows where case_id=? order by id', [cid]) == original
    # The very same physical member under another institution is a new identity.
    _, other_scope = accepted(client, cid, sources('Synthetic alternate bank'), scope_bank=None)
    assert (other_scope['ok'], other_scope['rows_dup']) == (5, 0)
    snapshot_tables = ('flows', 'flow_sources', 'import_records', 'batches', 'import_sources', 'import_source_operations')
    def snapshot():
        return {name: database.q_raw(f'select * from {name} where case_id=? order by all', [cid]) for name in snapshot_tables}
    before = snapshot();view = preview(client, cid, members);write = database.insert_dataframe
    def corrupt(name, frame, columns):
        if name == 'flow_sources':
            frame = frame.copy(deep=True)
            assert set(frame['disposition']) == {'duplicate'}
            frame['flow_id'] = list(reversed(frame['flow_id'].tolist()))
        return write(name, frame, columns)
    with monkeypatch.context() as patch:
        patch.setattr(database, 'insert_dataframe', corrupt)
        failed = complete(client, cid, view)
    assert failed['stage'] == 'error' and failed['error_code'] == 'import_integrity_failed'
    assert snapshot() == before
    removed, kept = (first, second) if remove_first else (second, first)
    assert client.delete(f"/api/batches/{removed['batch_id']}", params={'case_id': cid}).status_code == 200
    assert len(database.q_raw('select * from flows where case_id=?', [cid])) == 10
    package = client.get(f'/api/cases/{cid}/export');assert package.status_code == 200
    restored = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', package.content)})
    assert restored.status_code == 200
    assert client.get(f"/api/v1/cases/{restored.json()['case_id']}/flows").json()['total'] == 10
    assert client.delete(f"/api/batches/{kept['batch_id']}", params={'case_id': cid}).status_code == 200
    remaining = database.q_raw('select id from flows where case_id=? order by id', [cid])
    assert len(remaining) == 5 and not {row['id'] for row in remaining} & {row['id'] for row in original}


@pytest.mark.parametrize('write_chunk', [2, 100])
def test_same_physical_members_within_batch_keep_exact_duplicate_ids(client, database, make_case, monkeypatch, write_chunk):
    from test_folder_evidence import archive
    monkeypatch.setattr(importer, '_staged_identity_digest', lambda encoded: b'collision')
    monkeypatch.setattr(importer, 'WRITE_CHUNK', write_chunk)
    monkeypatch.setattr(import_staging, 'PLAN_CHUNK', 2)
    cid = make_case('Synthetic repeated physical members')
    rows = [transaction(**{'交易流水号': f'SYN-{i}'}) for i in range(5)]
    payload = csv_rows([{field: row.get(field, '') for field in sorted(table_import.BANK_FEEDBACK_32_COLUMNS)} for row in rows])
    inner = archive([('SYN-REQUEST/Synthetic bank交易明细信息.csv', payload),
                     ('manifest.csv', table('task', [{'请求单号': 'SYN-REQUEST', '银行名称': 'Synthetic bank'}])),
                     ('accounts.csv', table('account', [account()]))])
    _, result = accepted(client, cid, [('first.zip', inner), ('second.zip', inner)], scope_bank=None)
    assert (result['ok'], result['rows_dup']) == (5, 5)
    links = database.q_raw('select flow_id,count(*) n from flow_sources where case_id=? group by flow_id order by flow_id', [cid])
    assert len(links) == 5 and all(row['n'] == 2 for row in links)
