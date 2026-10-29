"""Public import boundaries for an optional, single-owner pure preview cache."""
import datetime as dt
import hashlib
import os
from pathlib import Path
import time

import pytest

from app import import_preview_cache as cache, importer, table_archive
from app.services import imports
from test_folder_evidence import complete, preview, transaction
from test_table_cancellations import source
from test_import_chunk_transactions import snapshot


@pytest.fixture(autouse=True)
def isolated_cache():
    cache.discard()
    yield
    cache.discard()


def view(client, cid, number='SYN-CACHE'):
    return preview(client, cid, [('rows.csv', source([transaction(**{
        '交易流水号': number, '交易时间': '2026-01-01 10:00:00.123456', '交易金额': '100.01'})])[0])], scope_bank=None)


def no_parse(*args, **kwargs):
    raise AssertionError('a sealed pure preview should be reused')


def test_public_commit_reuses_one_plan_and_keeps_original_receipt_and_sources(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic cache round trip')
    v = view(client, cid)
    assert cache._slot is not None
    parsed = cache._slot[1]; store = parsed[0].attrs['table_plan']['_plan_store']
    with monkeypatch.context() as hit:
        hit.setattr(imports, '_parse', no_parse)
        job = complete(client, cid, v)
    assert job['stage'] == 'done', job
    assert store._closed and cache._slot is None
    rows = client.get(f'/api/v1/cases/{cid}/flows').json()
    assert rows['total'] == 1
    assert rows['items'][0]['amount'] == '100.01'
    assert rows['items'][0]['trade_time'].endswith('.123456')
    assert client.get(f"/api/v1/cases/{cid}/flows/{rows['items'][0]['id']}/sources").json()['total'] == 1
    before = snapshot(database, cid)
    response = client.post(f"/api/v1/cases/{cid}/imports/{v['import_id']}/commit", json={'mode':v['mode'], 'mapping':v['mapping']})
    assert response.status_code == 200 and response.json()['started'] is False
    assert snapshot(database, cid) == before
    package = client.get(f'/api/cases/{cid}/export')
    restored = client.post('/api/cases/import', files={'file':('synthetic.fundcase', package.content)}).json()['case_id']
    assert client.get(f'/api/v1/cases/{restored}/flows').json()['total'] == 1


@pytest.mark.parametrize('damage', ['disk', 'metadata', 'notes', 'missing'])
def test_corrupt_cached_plan_fails_before_any_case_write(client, database, make_case, damage):
    cid = make_case('Synthetic damaged cache')
    v = view(client, cid); parsed = cache._slot[1]
    plan = parsed[0].attrs['table_plan']; store = plan['_plan_store']
    path = Path(store._con.execute('pragma database_list').fetchone()[2])
    before = snapshot(database, cid)
    if damage == 'disk':
        with path.open('r+b') as stream:
            stream.seek(-1, os.SEEK_END); byte = stream.read(1); stream.seek(-1, os.SEEK_END); stream.write(bytes([byte[0] ^ 1]))
    elif damage == 'metadata':
        plan['members'][0]['sha256'] = '0' * 64
    elif damage == 'notes':
        plan['records'][0]['details']['reason'] = 'Synthetic corrupt note'
    else:
        path.unlink()
    job = complete(client, cid, v)
    assert job['stage'] == 'error', job
    assert snapshot(database, cid) == before
    assert cache._slot is None and store._closed


def test_changed_original_is_rejected_even_with_valid_cached_plan(client, database, make_case):
    cid = make_case('Synthetic source verification')
    v = view(client, cid)
    session = database.q_raw('select * from import_sessions where import_id=?', [v['import_id']])[0]
    path = imports._path(v['import_id'], session['suffix'])
    path.write_bytes(path.read_bytes() + b'Synthetic damage')
    before = snapshot(database, cid)
    result = client.post(f"/api/v1/cases/{cid}/imports/{v['import_id']}/commit", json={'mode':v['mode'], 'mapping':v['mapping']})
    assert result.status_code == 409 and result.json()['error']['code'] == 'source_changed'
    assert snapshot(database, cid) == before


@pytest.mark.parametrize('reason', ['new_preview', 'expiry', 'repreview', 'recovery', 'missing'])
def test_cache_invalidation_recomputes_without_replaying_writes(client, database, make_case, monkeypatch, reason):
    cid = make_case('Synthetic cache invalidation'); v = view(client, cid)
    parsed = cache._slot[1]; store = parsed[0].attrs['table_plan']['_plan_store']
    if reason == 'new_preview':
        view(client, make_case('Synthetic other preview'), 'SYN-OTHER')
    elif reason == 'expiry':
        cache._expire(parsed)
    elif reason == 'repreview':
        result = client.post(f"/api/v1/cases/{cid}/imports/{v['import_id']}/preview", json={'mode':v['mode'], 'mapping':v['mapping']})
        assert result.status_code == 200, result.text
    elif reason == 'recovery':
        imports.recover_interrupted()
        with database._lock:
            database._con.close(); database._con = None; database.con()
    else:
        cache.discard()
    assert store._closed
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0
    original, calls = imports._parse, []
    def count(*args, **kwargs):
        calls.append(1); return original(*args, **kwargs)
    monkeypatch.setattr(imports, '_parse', count)
    job = complete(client, cid, v)
    assert job['stage'] == 'done' and job['result']['ok'] == 1, job
    assert calls == [1]


def test_cross_case_and_aba_do_not_consume_other_cases_cache(client, database, make_case, monkeypatch):
    first, second = make_case('Synthetic A'), make_case('Synthetic B')
    v = view(client, first); saved = cache._slot
    body = {'mode':v['mode'], 'mapping':v['mapping']}
    wrong = client.post(f"/api/v1/cases/{second}/imports/{v['import_id']}/commit", json=body)
    assert wrong.status_code in (404, 409)
    assert cache._slot is saved
    assert client.get(f'/api/v1/cases/{second}/flows').json()['total'] == 0
    monkeypatch.setattr(imports, '_parse', no_parse)
    assert complete(client, first, v)['stage'] == 'done'
    assert client.get(f'/api/v1/cases/{second}/flows').json()['total'] == 0


def test_commit_rebinds_current_case_inside_atomic_write_and_rolls_back(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic late case validation'); v = view(client, cid)
    store = cache._slot[1][0].attrs['table_plan']['_plan_store']
    before = snapshot(database, cid)
    calls = []
    original = imports._bind_table_plan
    def revalidate(case_id, parsed, **kwargs):
        calls.append(case_id)
        original(case_id, parsed, **kwargs)
        raise imports.ServiceError(409, 'evidence_context_overlap', 'Synthetic late conflict')
    with monkeypatch.context() as hit:
        hit.setattr(imports, '_parse', no_parse)
        hit.setattr(imports, '_bind_table_plan', revalidate)
        job = complete(client, cid, v)
    assert calls == [cid] and job['stage'] == 'error'
    assert job['error_code'] == 'evidence_context_overlap'
    assert snapshot(database, cid) == before and store._closed
    assert complete(client, cid, v)['stage'] == 'done'


def test_failed_writer_does_not_keep_cache_or_partial_facts(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic cached rollback'); v = view(client, cid)
    before = snapshot(database, cid)
    original = importer.commit_rows
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Synthetic failure after source and flow writes')
    monkeypatch.setattr(importer, 'commit_rows', fail)
    job = complete(client, cid, v)
    assert job['stage'] == 'error' and cache._slot is None
    assert snapshot(database, cid) == before


def test_expired_upload_cleanup_releases_registered_cache(client, database, make_case):
    cid = make_case('Synthetic expired preview'); v = view(client, cid)
    store = cache._slot[1][0].attrs['table_plan']['_plan_store']
    result = imports.cleanup_uploads(cid, now=imports._now() + dt.timedelta(days=8), dry_run=False)
    assert result['deleted'] == 1 and store._closed and cache._slot is None
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0


def test_case_modified_preview_is_ineligible_for_reuse(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic impure preview')
    original = imports._bind_table_plan
    def change(case_id, parsed, **kwargs):
        original(case_id, parsed, **kwargs)
        parsed[0].attrs['table_plan']['records'][0]['details']['reason'] = 'Synthetic case-bound outcome'
    monkeypatch.setattr(imports, '_bind_table_plan', change)
    view(client, cid)
    assert cache._slot is None


def test_withdrawal_between_preview_and_commit_is_revalidated(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic withdrawal before cached commit')
    old = complete(client, cid, view(client, cid, 'SYN-OLD'))
    assert old['stage'] == 'done'
    v = view(client, cid, 'SYN-NEW'); assert cache._slot is not None
    assert client.delete(f"/api/batches/{old['result']['batch_id']}", params={'case_id':cid}).status_code == 200
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0
    with monkeypatch.context() as hit:
        hit.setattr(imports, '_parse', no_parse)
        job = complete(client, cid, v)
    assert job['stage'] == 'done' and job['result']['ok'] == 1
    rows = client.get(f'/api/v1/cases/{cid}/flows').json()
    assert rows['total'] == 1 and rows['items'][0]['trade_no'] == 'SYN-NEW'


def test_another_import_invalidates_preview_and_commit_sees_new_facts(client, database, make_case):
    cid = make_case('Synthetic concurrent source')
    pending = view(client, cid)
    other = complete(client, cid, view(client, cid))
    assert other['stage'] == 'done'
    job = complete(client, cid, pending)
    assert job['stage'] == 'done' and job['result']['ok'] == 0 and job['result']['rows_dup'] == 1
    rows = client.get(f'/api/v1/cases/{cid}/flows').json()
    assert rows['total'] == 1
    assert client.get(f"/api/v1/cases/{cid}/flows/{rows['items'][0]['id']}/sources").json()['total'] == 2


def test_real_timer_expires_abandoned_preview(client, database, make_case, monkeypatch):
    import threading
    closed = threading.Event(); original = cache.close
    def observe(parsed):
        original(parsed); closed.set()
    monkeypatch.setattr(cache, 'close', observe)
    monkeypatch.setattr(cache, 'TTL_SECONDS', .05)
    cid = make_case('Synthetic abandoned preview')
    view(client, cid)
    assert closed.wait(2)
    assert cache._slot is None
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0


def test_profile_change_and_rejected_mapping_cannot_reuse_old_plan(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic changed preview settings'); v = view(client, cid)
    result = client.post(f"/api/v1/cases/{cid}/imports/{v['import_id']}/preview", json={'mode':'A', 'mapping':{}})
    assert result.status_code == 422 and cache._slot is None
    v = view(client, cid)
    with database.transaction():
        database.x('update import_sessions set request_json=? where import_id=?',
                   ['{"_table_profile":"synthetic-unsupported-profile"}', v['import_id']])
    before = snapshot(database, cid)
    result = client.post(f"/api/v1/cases/{cid}/imports/{v['import_id']}/commit", json={'mode':v['mode'], 'mapping':v['mapping']})
    # Mapping/profile validation may reject synchronously; if started, parsing
    # must reject the unsupported configuration rather than consume old facts.
    if result.status_code == 200:
        for _ in range(100):
            job = client.get(f"/api/v1/cases/{cid}/imports/{v['import_id']}").json()
            if job['stage'] == 'error':break
            time.sleep(.01)
        assert job['stage'] == 'error'
    else:
        assert result.status_code in (400, 422)
    assert snapshot(database, cid) == before


@pytest.mark.parametrize('abrupt', [False, True])
def test_process_exit_never_restores_or_replays_a_cached_preview(tmp_path, abrupt):
    import json
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    data = tmp_path / 'synthetic-process'; data.mkdir()
    env = dict(os.environ, FUND_DATA_DIR=str(data), MPLCONFIGDIR=str(tmp_path / 'mpl'),
               CACHE_EXIT_ABRUPT='1' if abrupt else '0')
    for name in ('FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE', 'FUNDS_TRACE_LICENSE_SECRET'):
        env.pop(name, None)
    prepare = r'''
import asyncio,io,json,os,zipfile
from fastapi import UploadFile
from app import db,import_preview_cache
from app.services import imports
from app.table_import import BANK_FEEDBACK_32_COLUMNS
import csv
stream=io.StringIO(newline=''); writer=csv.DictWriter(stream,fieldnames=sorted(BANK_FEEDBACK_32_COLUMNS));writer.writeheader()
writer.writerow({'交易账号':'SYN-A','交易对手账卡号':'SYN-B','交易时间':'2026-01-01 10:00:00','交易金额':'100.01','交易币种':'CNY','收付标志':'出','交易是否成功':'1','交易流水号':'SYN-CACHE'})
content=io.BytesIO()
with zipfile.ZipFile(content,'w') as archive:archive.writestr('rows.csv',stream.getvalue().encode())
cid=db.q("insert into cases(name) values ('Synthetic process cache') returning id")[0]['id']
v=asyncio.run(imports._upload(str(cid),UploadFile(file=io.BytesIO(content.getvalue()),filename='synthetic.zip'),'v1',resolve_evidence=True))
assert import_preview_cache._slot is not None
(db.DATA_DIR/'control.json').write_text(json.dumps({'case_id':str(cid),'view':v}))
db._con.close();db._con=None
if os.environ['CACHE_EXIT_ABRUPT']=='1':os._exit(0)
'''
    recover = r'''
import json,time
from fastapi.testclient import TestClient
from app import db,import_preview_cache
from app.main import app
state=json.loads((db.DATA_DIR/'control.json').read_text());cid=state['case_id'];v=state['view'];url=f"/api/v1/cases/{cid}/imports/{v['import_id']}"
assert import_preview_cache._slot is None
with TestClient(app) as client:
 response=client.get(url)
 assert response.status_code==404 and response.json()['error']['code']=='import_job_not_found'
 assert db.q_raw('select stage from import_sessions where import_id=?',[v['import_id']])[0]['stage']=='ready'
 assert client.get(f'/api/v1/cases/{cid}/flows').json()['total']==0
 assert client.post(url+'/commit',json={'mode':v['mode'],'mapping':v['mapping']}).status_code==200
 for _ in range(300):
  job=client.get(url).json()
  if job['stage'] in ('done','error'):break
  time.sleep(.01)
 assert job['stage']=='done',job
 assert client.get(f'/api/v1/cases/{cid}/flows').json()['total']==1
print('synthetic restart verified')
db._con.close();db._con=None
'''
    first = subprocess.run([sys.executable, '-c', prepare], cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stderr
    original_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (data/'uploads').glob('*') if p.is_file()}
    assert original_hashes
    if not abrupt:
        assert not list(data.glob('funds-trace-plan-*'))
    second = subprocess.run([sys.executable, '-c', recover], cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert 'synthetic restart verified' in second.stdout
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (data/'uploads').glob('*') if p.is_file()} == original_hashes
