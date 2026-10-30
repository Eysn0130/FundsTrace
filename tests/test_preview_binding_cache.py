"""Public pure-plan cache admission with cancellation and auxiliary evidence."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from app import import_preview_cache as cache, import_staging, table_archive
from app.services import imports
from test_folder_evidence import account, complete, preview, table
from test_table_cancellations import round_trip, source, transaction


@pytest.fixture(autouse=True)
def isolated_cache():
    cache.discard()
    yield
    cache.discard()


def members(cancelled, *, conflicting=False):
    rows = round_trip()
    for row, tail in zip(rows, ('100000', '100001', '100002')):
        row['交易时间'] = '2026-01-01 10:00:00.' + tail
    ordinary = transaction(**{'交易流水号':'SYN-OTHER', '交易金额':'10.03', '交易余额':'',
        '交易币种':'USD', '收付标志':'入', '交易时间':'2026-01-01 10:00:01.123456'})
    if not cancelled:
        rows = rows[:1]
    rows.append(ordinary)
    if conflicting:
        for row in rows:
            row['IP地址'] = row['摘要说明']
        rows[1]['IP地址'] = 'Synthetic historical alternate summary'
    return [('SYN-REQUEST/Synthetic bank交易明细信息.csv', source(rows)[0]),
        ('accounts.csv', table('account',[account(number='SYN-OWN',card='')])),
        ('children.csv', table('subaccount',[{'银行名称':'Synthetic bank','开户账号':'SYN-PARENT','子账户账号':'SYN-OWN'}])),
        ('manifest.csv', table('task',[{'请求单号':'SYN-REQUEST','银行名称':'Synthetic bank'}]))]


def observe(monkeypatch):
    """Read scalars/hashes only after the existing stamp; never call it extra."""
    observations = {'retain':[], 'take':[], 'key_fields_equal':[], 'parse_calls':0,
                    'stamp_equal':[], 'file_equal':[], 'metadata_equal':[], 'items_equal':[],
                    'lookup_rows':[], 'namespaces':[]}
    snapshots, key_hashes = [], []
    def digest(value):
        return hashlib.sha256(import_staging._encode_value(value)).digest()
    original_stamp, original_retain, original_take, original_parse = cache.stamp, cache.retain, cache.take, imports._parse
    def stamped(parsed):
        result = original_stamp(parsed)
        frame,sheet,mode,mapping,rows,stats = parsed
        plan = frame.attrs.get('table_plan')
        if result is None or plan is None:
            return result
        store = plan['_plan_store']
        kinds = ('records','rows','cancelled_rows','cancellations')
        metadata = {k:v for k,v in plan.items() if k not in (*kinds,'_plan_store','_details_pool')}
        metadata.update(_frame_columns=list(frame.columns),_sheet=sheet,_mode=mode,_mapping=mapping,
            _stats=stats,_counts=store._counts,_ends=store._ends,_member_details=store._member_details,
            _details_version=store._details_version,
            _frame_attrs={k:v for k,v in frame.attrs.items() if k!='table_plan'})
        path = Path(store._con.execute('pragma database_list').fetchone()[2])
        current = (result,hashlib.sha256(path.read_bytes()).digest(),digest(metadata),
            digest(store._con.execute('select * from items order by kind,position').fetchall()))
        if snapshots:
            for key,before,after in zip(('stamp_equal','file_equal','metadata_equal','items_equal'),snapshots[-1],current):
                observations[key].append(before==after)
        snapshots.append(current)
        observations['lookup_rows'].append(store._con.execute('select count(*) from lookup').fetchone()[0])
        observations['namespaces'].append(store._namespace)
        return result
    def retained(key,parsed,original_stamp):
        key_hashes[:] = [digest(v) for v in key]
        result = original_retain(key,parsed,original_stamp)
        observations['retain'].append(result)
        return result
    def taken(key):
        observations['key_fields_equal'].append([a==digest(b) for a,b in zip(key_hashes,key)])
        result = original_take(key)
        observations['take'].append(result is not None)
        return result
    def parsed(*args,**kwargs):
        observations['parse_calls'] += 1
        return original_parse(*args,**kwargs)
    monkeypatch.setattr(cache,'stamp',stamped)
    monkeypatch.setattr(cache,'retain',retained)
    monkeypatch.setattr(cache,'take',taken)
    monkeypatch.setattr(imports,'_parse',parsed)
    return observations


@pytest.mark.parametrize('zipped',[False,True])
@pytest.mark.parametrize('cancelled',[False,True])
def test_pure_cancellation_preview_is_reused_without_case_facts(client,database,make_case,monkeypatch,zipped,cancelled):
    cid = make_case('Synthetic binding cache')
    seen = observe(monkeypatch)
    view = preview(client,cid,members(cancelled),zipped=zipped,scope_bank=None)
    assert view['table_summary']['profile'] == table_archive.MICROSECOND_PROFILE
    assert view['table_summary']['accepted'] == 2
    assert view['table_summary']['cancelled'] == (2 if cancelled else 0)
    job = complete(client,cid,view)
    assert job['stage']=='done',job
    page = client.get(f'/api/v1/cases/{cid}/flows').json()
    assert page['total']==2
    by_no = {r['trade_no']:r for r in page['items']}
    assert set(by_no)=={'SYN-WITNESS','SYN-OTHER'}
    assert (by_no['SYN-WITNESS']['amount'],by_no['SYN-WITNESS']['currency'],by_no['SYN-WITNESS']['trade_time']) == ('1.00','CNY','2026-01-01 10:00:00.100000')
    assert (by_no['SYN-OTHER']['amount'],by_no['SYN-OTHER']['currency'],by_no['SYN-OTHER']['trade_time'],by_no['SYN-OTHER']['balance']) == ('10.03','USD','2026-01-01 10:00:01.123456',None)
    assert (by_no['SYN-WITNESS']['raw_payer_account'],by_no['SYN-WITNESS']['raw_payee_account']) == ('SYN-OWN','SYN-COUNTER')
    assert (by_no['SYN-OTHER']['raw_payer_account'],by_no['SYN-OTHER']['raw_payee_account']) == ('SYN-COUNTER','SYN-OWN')
    print('CACHE_ADMISSION_SAFE '+json.dumps({'zipped':zipped,'cancelled':cancelled,**seen},sort_keys=True))
    assert seen['key_fields_equal'] and all(all(fields) for fields in seen['key_fields_equal'])
    assert seen['retain']==[True]
    assert seen['take']==[True]
    assert seen['parse_calls']==1


@pytest.mark.parametrize('reason', ['hit', 'expiry', 'missing'])
def test_cancellation_cache_support_withdrawal_and_package(client,database,make_case,monkeypatch,reason):
    cid = make_case('Synthetic binding lifetime')
    original = members(True)
    seen = observe(monkeypatch)
    view = preview(client,cid,original,scope_bank=None)
    if reason == 'expiry':
        cache._expire(cache._slot[1])
    elif reason == 'missing':
        cache.discard()
    first = complete(client,cid,view)
    assert first['stage'] == 'done'
    assert seen['take'] == [reason == 'hit']
    assert seen['parse_calls'] == (1 if reason == 'hit' else 2)
    repeated = preview(client,cid,original,zipped=True,scope_bank=None)
    assert cache._slot is not None
    second = complete(client,cid,repeated)
    assert second['stage'] == 'done'
    page = client.get(f'/api/v1/cases/{cid}/flows').json()
    assert page['total'] == 2
    for row in page['items']:
        assert client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()['total'] == 2
    for result in (first['result'], second['result']):
        response = client.get(f"/api/v1/cases/{cid}/sources/{result['source_id']}/report")
        assert response.status_code == 200
        tx = [r for r in response.json()['items'] if r['member_path'] == original[0][0]]
        assert [r['source_row'] for r in tx] == [2,3,4,5]
        assert [r['disposition'] for r in tx][1:3] == ['cancelled','cancelled']
        downloaded = client.get(f"/api/v1/cases/{cid}/sources/{result['source_id']}/file")
        assert downloaded.status_code == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as package:
            assert set(package.namelist()) == {name for name,_ in original}
            assert all(package.read(name) == content for name,content in original)
    saved = client.get(f'/api/cases/{cid}/export')
    restored = client.post('/api/cases/import',files={'file':('synthetic.fundcase',saved.content)})
    assert restored.status_code == 200
    new_id = restored.json()['case_id']
    new_rows = client.get(f'/api/v1/cases/{new_id}/flows').json()
    fields = ('trade_no','amount','currency','balance','trade_time','raw_payer_account','raw_payee_account')
    assert sorted(tuple(r[k] for k in fields) for r in new_rows['items']) == sorted(tuple(r[k] for k in fields) for r in page['items'])
    for result, remaining in ((first['result'],2),(second['result'],0)):
        assert client.delete(f"/api/batches/{result['batch_id']}",params={'case_id':cid}).status_code == 200
        assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == remaining
    assert not list(Path(database.DATA_DIR).glob('funds-trace-binding-*'))


def test_real_historical_mapping_change_still_rejects_pure_plan_cache(client,database,make_case,monkeypatch):
    from test_table_cancellation_container import summary_ready, done, raw
    from test_table_currency_region_lifecycle import source_snapshot, receipt
    cid = make_case('Synthetic historical binding conflict')
    payload = members(True,conflicting=True)[0][1]
    historical = summary_ready(client,cid,payload,monkeypatch,'funds-trace-table-v19')
    old_job = done(client,cid,historical)
    before, old_source = raw(database,cid), source_snapshot(database,cid,historical['import_id'])
    seen = observe(monkeypatch)
    from test_archive_profile_upgrade import frozen_preview
    from test_folder_evidence import archive
    # Preserve the ordinary CSV's canonical member path and family. The
    # supported v19 summary mapping is a real historical interpretation;
    # no database mutation or replacement binding function manufactures it.
    view = frozen_preview(client,cid,archive([('table.csv',payload)]),monkeypatch,table_archive.MICROSECOND_PROFILE)
    assert view['table_summary']['cancelled'] == 0
    assert cache._slot is None
    assert seen['retain'] == [False]
    assert seen['metadata_equal'] == [False]
    assert seen['items_equal'] == [False]
    job = done(client,cid,view)
    assert seen['take'] == [False] and seen['parse_calls'] == 2
    assert job['result']['table_summary']['cancelled'] == 0
    assert raw(database,cid) == before
    assert source_snapshot(database,cid,historical['import_id']) == old_source
    assert receipt(client,cid,historical['import_id']) == old_job['result']
    assert not list(Path(database.DATA_DIR).glob('funds-trace-binding-*'))
