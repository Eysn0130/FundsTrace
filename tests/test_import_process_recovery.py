"""Abrupt process exits exercise DuckDB/WAL and durable import receipts."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PREPARE = r'''
import asyncio,io,json,os,time
from pathlib import Path
from fastapi import UploadFile
from app import db
from app.api.models import CommitRequest
from app.services import imports
db._start_ckpt=lambda:None
if os.environ['LEGACY_SCHEMA']=='1':
    import duckdb
    with duckdb.connect(str(db.DB_PATH)) as old:
        old.execute('create table account_photos(case_id integer,account varchar,fname varchar,updated_at timestamp default current_timestamp,primary key(case_id,account))')
        old.execute('create table focus_nodes(case_id integer,account varchar,primary key(case_id,account))')
        old.execute("insert into focus_nodes values (0,'SYNTHETIC-LEGACY')")
cid=db.q("insert into cases(name) values ('Synthetic crash recovery') returning id")[0]['id']
csv='交易时间,付款账号,收款账号,交易金额,本方账号\n2026-01-01 10:00:00,000SYN-A,000SYN-B,123.45,000SYN-A\n'.encode()
preview=asyncio.run(imports.upload(str(cid),UploadFile(file=io.BytesIO(csv),filename='合成 流水.csv')))
state={'case_id':str(cid),'import_id':preview['import_id'],'mode':preview['mode'],'mapping':preview['mapping'],'mark_accounts':[]}
(db.DATA_DIR/'control.json').write_text(json.dumps(state))
if os.environ['CRASH_PHASE']=='during_write':
    real_insert=db.insert_dataframe
    def crash_insert(*args):
        real_insert(*args)
        os._exit(79)
    db.insert_dataframe=crash_insert
imports.commit(str(cid),preview['import_id'],CommitRequest(mode=preview['mode'],mapping=preview['mapping'],mark_accounts=[]))
for _ in range(1000):
    job=imports.progress(str(cid),preview['import_id'])
    if job['stage']=='done':
        os._exit(80)
    if job['stage']=='error':
        raise AssertionError(job)
    time.sleep(.005)
raise AssertionError('Synthetic crash did not occur')
'''

RECOVER = r'''
import json,os,time
from fastapi.testclient import TestClient
from app import db,license as licensing
from app.api.models import CommitRequest
from app.services import imports
db._start_ckpt=lambda:None
state=json.loads((db.DATA_DIR/'control.json').read_text())
cid,iid=state['case_id'],state['import_id']
# Exercise the real application's startup recovery and case-bound HTTP view.
licensing.machine_code=lambda:'SYNTHETIC-CRASH-TEST'
licensing.save_key(licensing.issue(licensing.machine_code()))
from app.main import app
with TestClient(app) as client:
    response=client.get(f'/api/v1/cases/{cid}/imports/{iid}')
    assert response.status_code==200,response.text
    job=response.json()
    assert client.get(f'/api/v1/cases/1/imports/{iid}').status_code==404
assert imports.recover_interrupted()=={'recovered':0}
assert imports.list_sessions(cid)['items'][0]['filename']=='合成 流水.csv'
initial_stage=job['stage']
if initial_stage=='error':
    assert job['error_code']=='import_interrupted' and job['recoverable']
    for table in ('flows','batches','main_accounts','import_sources','flow_sources'):
        assert db.q(f'select count(*) n from {table}')[0]['n']==0,table
    assert job['result'] is None
else:
    assert initial_stage=='done' and job['result']['ok']==1
request=imports.list_sessions(cid)['items'][0]['request']
assert request['mark_accounts']==[]
started=imports.commit(cid,iid,CommitRequest(**request))['started']
assert started==(initial_stage=='error')
for _ in range(1000):
    job=imports.progress(cid,iid)
    if job['stage'] in ('done','error'): break
    time.sleep(.005)
assert job['stage']=='done',job
assert db.q('select count(*) n from flows')[0]['n']==1
assert db.q('select count(*) n from batches')[0]['n']==1
assert db.q('select count(*) n from flow_sources')[0]['n']==1
assert db.q('select count(*) n from main_accounts')[0]['n']==0
assert db.q_raw('select cast(amount as varchar) amount from flows')[0]['amount']=='123.45'
if os.environ['LEGACY_SCHEMA']=='1':
    assert db.q('select scale from focus_nodes where case_id=0')[0]['scale']==1.6
    assert {'crop_cx','crop_cy','crop_r'} <= {r['name'] for r in db.q("pragma table_info('account_photos')")}
print(json.dumps({'initial_stage':initial_stage,'started':started,'final_stage':job['stage']}))
db._con.close()
db._con=None
'''


@pytest.mark.parametrize("phase,exit_code,initial", [("during_write", 79, "error"), ("after_receipt", 80, "done")])
@pytest.mark.parametrize("legacy_schema", [False, True], ids=["fresh", "upgraded"])
def test_real_process_interruption_and_committed_receipt_reopen(tmp_path, phase, exit_code, initial, legacy_schema):
    runtime = tmp_path / "中文 空格 任务"
    env = {**os.environ, "FUND_DATA_DIR": str(runtime), "MPLCONFIGDIR": str(tmp_path / "mpl"),
           "FUNDS_TRACE_LICENSE_SECRET": "synthetic-crash-key-not-for-use", "CRASH_PHASE": phase,
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1", "LEGACY_SCHEMA": str(int(legacy_schema))}
    root = Path(__file__).resolve().parents[1]
    crashed = subprocess.run([sys.executable, "-c", PREPARE], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert crashed.returncode == exit_code, crashed.stdout + crashed.stderr
    recovered = subprocess.run([sys.executable, "-c", RECOVER], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    result = json.loads(recovered.stdout)
    assert result == {"initial_stage": initial, "started": initial == "error", "final_stage": "done"}
