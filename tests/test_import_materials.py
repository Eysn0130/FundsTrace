"""Synthetic display registry lifecycle, independent of financial semantics."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import time
import threading
import uuid
import zipfile

import pytest

from test_import_report import payload,row


def preview(client,cid,data=None,filename='same-name.csv'):
    raw=data if data is not None else payload([row(uuid.uuid4().hex,'收入','1.23')])
    response=client.post(f'/api/v1/cases/{cid}/imports/preview',files={'file':(filename,raw)})
    assert response.status_code==200,response.text
    view=response.json();base=f'/api/v1/cases/{cid}/imports/{view["import_id"]}'
    return base,view,raw


def start(client,base,view,group=None):
    body={'mode':view['mode'],'mapping':view['mapping'],'mark_accounts':[]}
    if group is not None:body['material_group_id']=group
    return client.post(base+'/commit',json=body)


def finished(client,base):
    until=time.monotonic()+20
    while time.monotonic()<until:
        response=client.get(base)
        assert response.status_code==200,response.text
        job=response.json()
        if job['stage'] in {'done','error'}:return job
        time.sleep(.01)
    pytest.fail('synthetic import did not finish')


def complete(client,cid,group=None,**options):
    base,view,raw=preview(client,cid,**options)
    assert start(client,base,view,group).status_code==200
    job=finished(client,base);assert job['stage']=='done',job
    return base,view,raw,job


def report(client,base,member='table.csv'):
    response=client.get(base+'/report',params={'member_path':member})
    assert response.status_code==200,response.text
    return response.json()


def test_selection_group_original_names_are_unique_frozen_and_reopenable(client,database,make_case,monkeypatch):
    from app.services import materials,imports
    from app import db
    monkeypatch.setattr(materials,'local_day',lambda:'20261114')
    cid=make_case('Synthetic material selection')
    group=uuid.uuid4().hex
    first,first_view,raw,job=complete(client,cid,group)
    before=database.q_raw('select result_json from import_sessions where import_id=?',[first_view['import_id']])[0]['result_json']
    first_scope=report(client,first)['scope']
    assert first_scope['material_name']=='20261114-1-1号检材'
    assert first_scope['filename']=='same-name.csv' and first_scope['source_record_total']==1
    assert first_scope['source_sha256']==hashlib.sha256(raw).hexdigest()
    assert imports._path(first_view['import_id'],'.csv').read_bytes()==raw
    monkeypatch.setattr(materials,'local_day',lambda:'20261115')
    second,_,_,second_job=complete(client,cid,group,data=raw)
    assert report(client,second)['scope']['material_name']=='20261114-1-2号检材'
    assert second_job['result']['rows_dup']==1
    third,_,_,_=complete(client,cid,uuid.uuid4().hex)
    assert report(client,third)['scope']['material_name']=='20261115-1-1号检材'
    assert database.q_raw('select result_json from import_sessions where import_id=?',[first_view['import_id']])[0]['result_json']==before
    assert start(client,first,first_view,group).json()['started'] is False
    with db._lock:
        db._con.close();db._con=None
        db.con()
    assert report(client,first)['scope']['material_name']==first_scope['material_name']
    assert client.get(first).json()['result']==job['result']


def test_daily_batches_are_global_group_case_binding_and_rebinding_rejected(client,make_case,monkeypatch):
    from app.services import materials
    monkeypatch.setattr(materials,'local_day',lambda:'20261114')
    a,b=make_case('Synthetic group A'),make_case('Synthetic group B')
    group=uuid.uuid4().hex
    first,view,_,_=complete(client,a,group)
    assert report(client,first)['scope']['material_name']=='20261114-1-1号检材'
    other,other_view,_=preview(client,b)
    response=start(client,other,other_view,group)
    assert response.status_code==409 and response.json()['error']['code']=='material_group_case_mismatch'
    assert start(client,first,view,uuid.uuid4().hex).status_code==409
    assert start(client,other,other_view,'not-a-group').status_code==422
    second,_,_,_=complete(client,b,uuid.uuid4().hex)
    assert report(client,second)['scope']['material_name']=='20261114-2-1号检材'


def test_group_is_case_bound_while_first_file_is_pending(client,make_case,monkeypatch):
    from app.services import imports
    a,b=make_case('Synthetic pending A'),make_case('Synthetic pending B')
    first,view,_=preview(client,a);other,other_view,_=preview(client,b)
    release=threading.Event();run=imports._run
    def delayed(import_id):
        assert release.wait(5)
        run(import_id)
    monkeypatch.setattr(imports,'_run',delayed)
    group=uuid.uuid4().hex
    try:
        assert start(client,first,view,group).status_code==200
        rejected=start(client,other,other_view,group)
        assert rejected.status_code==409 and rejected.json()['error']['code']=='material_group_case_mismatch'
    finally:
        release.set()
    assert finished(client,first)['stage']=='done'


def test_simultaneous_files_allocate_one_batch_and_distinct_file_numbers(client,make_case,monkeypatch):
    from app.services import materials
    monkeypatch.setattr(materials,'local_day',lambda:'20261114')
    cid=make_case('Synthetic concurrent selection');group=uuid.uuid4().hex
    inputs=[preview(client,cid) for _ in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses=list(pool.map(lambda value:start(client,value[0],value[1],group),inputs))
    assert all(r.status_code==200 for r in responses)
    for base,_,_ in inputs:assert finished(client,base)['stage']=='done'
    labels={report(client,base)['scope']['material_name'] for base,_,_ in inputs}
    assert labels=={f'20261114-1-{i}号检材' for i in (1,2,3)}


def test_failure_rolls_back_material_registry_and_receipt_with_originals_intact(client,database,make_case,monkeypatch):
    from app.services import materials,imports
    cid=make_case('Synthetic material rollback');group=uuid.uuid4().hex
    base,view,raw=preview(client,cid)
    register=materials.register
    def fail(*args):
        register(*args)
        raise RuntimeError('synthetic post-registration failure')
    monkeypatch.setattr(materials,'register',fail)
    assert start(client,base,view,group).status_code==200
    assert finished(client,base)['stage']=='error'
    for table in ('import_materials','import_material_groups','material_day_counters','flows','import_sources'):
        assert database.q_raw(f'select count(*) n from {table}')[0]['n']==0
    assert imports._path(view['import_id'],'.csv').read_bytes()==raw
    monkeypatch.setattr(materials,'register',register)
    assert start(client,base,view,None).status_code==200
    assert finished(client,base)['stage']=='done'
    assert database.q_raw('select group_id from import_materials')[0]['group_id']==group


def test_workbook_members_share_original_identity_and_source_total(client,make_case,monkeypatch):
    from app.services import materials
    from openpyxl import Workbook
    from test_import_report import HEADERS
    monkeypatch.setattr(materials,'local_day',lambda:'20261114')
    book=Workbook();book.active.append(HEADERS);book.active.append(row('SYN-1','收入','1.23'))
    second=book.create_sheet('Second');second.append(HEADERS);second.append(row('SYN-2','收入','4.56'));second.append(row('SYN-BAD','收入','7.89',stamp='invalid'))
    stream=io.BytesIO();book.save(stream);book.close()
    base,_,_,_=complete(client,make_case('Synthetic workbook material'),uuid.uuid4().hex,data=stream.getvalue(),filename='synthetic.xlsx')
    first=report(client,base,'worksheets/0001')['scope'];second=report(client,base,'worksheets/0002')['scope']
    assert first['material_name']==second['material_name']=='20261114-1-1号检材'
    assert first['source_record_total']==second['source_record_total']==3
    assert first['filename']==second['filename']=='synthetic.xlsx'
    assert first['source_sha256']==second['source_sha256']==hashlib.sha256(stream.getvalue()).hexdigest()


def test_outer_zip_total_excludes_retained_attachment_placeholders(client,make_case):
    cid=make_case('Synthetic attachment count')
    response=client.post(f'/api/v1/cases/{cid}/imports/directory/preview',files=[
        ('files',('data.csv',payload([row('SYN-DATA','收入','1.23')]))),
        ('files',('attachment.pdf',b'%PDF-1.7\nSynthetic attachment only'))])
    assert response.status_code==200,response.text
    view=response.json();base=f'/api/v1/cases/{cid}/imports/{view["import_id"]}'
    assert start(client,base,view,uuid.uuid4().hex).status_code==200
    assert finished(client,base)['stage']=='done'
    retained=client.get(f'/api/v1/cases/{cid}/sources/{view["import_id"]}/file').content
    scope=report(client,base,'data.csv')['scope']
    assert scope['filename']=='目录原件.zip' and scope['source_record_total']==1
    assert scope['source_size_bytes']==len(retained)
    assert scope['source_sha256']==hashlib.sha256(retained).hexdigest()


def test_package_roundtrip_collision_origin_delete_and_counter_floor(client,database,make_case,monkeypatch):
    from app.services import materials
    monkeypatch.setattr(materials,'local_day',lambda:'20261114')
    cid=make_case('Synthetic material package');group=uuid.uuid4().hex
    original,view,_,_=complete(client,cid,group)
    original_scope=report(client,original)['scope']
    package=client.get(f'/api/cases/{cid}/export');assert package.status_code==200,package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        metadata=json.loads(archive.read('meta.json'))
    assert metadata['materials']['version']==1
    assert metadata['materials']['groups'][0]['files']==[{'source_id':view['import_id'],'file_no':1}]
    restored=client.post('/api/cases/import',files={'file':('synthetic.fundcase',package.content)})
    assert restored.status_code==200,restored.text
    copied=restored.json()['case_id']
    copied_source=database.q_raw('select source_id from import_sources where case_id=?',[copied])[0]['source_id']
    copied_report=report(client,f'/api/v1/cases/{copied}/imports/{copied_source}')
    assert copied_report['scope']['material_name']=='20261114-2-1号检材'
    assert copied_report['scope']['material_origin_name']==original_scope['material_name']
    assert copied_report['summary']==report(client,original)['summary']
    assert client.delete(f'/api/cases/{cid}').status_code==200
    assert database.q_raw('select count(*) n from import_materials where case_id=?',[cid])[0]['n']==0
    assert database.q_raw('select count(*) n from import_material_groups where case_id=?',[cid])[0]['n']==0
    latest,_,_,_=complete(client,make_case('Synthetic after deletion'),uuid.uuid4().hex)
    assert report(client,latest)['scope']['material_name']=='20261114-3-1号检材'
    reexport=client.get(f'/api/cases/{copied}/export')
    again=client.post('/api/cases/import',files={'file':('synthetic.fundcase',reexport.content)})
    assert again.status_code==200,again.text
    last_source=database.q_raw('select source_id from import_sources where case_id=?',[again.json()['case_id']])[0]['source_id']
    assert report(client,f'/api/v1/cases/{again.json()["case_id"]}/imports/{last_source}')['scope']['material_origin_name']==original_scope['material_name']


def test_legacy_unregistered_read_does_not_allocate_or_change_receipt(client,database,make_case):
    base,view,_,job=complete(client,make_case('Synthetic prior source'))
    database.x('delete from import_materials where source_id=?',[view['import_id']])
    before=database.q_raw('select * from material_day_counters')
    assert report(client,base)['scope']['material_name'] is None
    assert database.q_raw('select * from material_day_counters')==before
    assert client.get(base).json()['result']==job['result']


def test_invalid_package_material_metadata_rejected_atomically(client,database,make_case):
    cid=make_case('Synthetic invalid package material')
    complete(client,cid,uuid.uuid4().hex)
    package=client.get(f'/api/cases/{cid}/export').content
    before=database.q_raw('select count(*) n from cases')[0]['n']
    for mutation in ('bad-date','unknown-source','duplicate-file'):
        output=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(package)) as source,zipfile.ZipFile(output,'w') as target:
            for info in source.infolist():
                value=source.read(info)
                if info.filename=='meta.json':
                    meta=json.loads(value);group=meta['materials']['groups'][0]
                    if mutation=='bad-date':group['import_day']='20261340'
                    elif mutation=='unknown-source':group['files'][0]['source_id']='f'*32
                    else:group['files'].append(group['files'][0].copy())
                    value=json.dumps(meta).encode()
                target.writestr(info,value)
        response=client.post('/api/cases/import',files={'file':('synthetic-invalid.fundcase',output.getvalue())})
        assert response.status_code==400,response.text
        assert database.q_raw('select count(*) n from cases')[0]['n']==before


def test_package_remote_dates_and_extreme_ordinals_do_not_control_local_registry(client,database,make_case,monkeypatch):
    from app.services import materials
    monkeypatch.setattr(materials,'local_day',lambda:'20261114')
    cid=make_case('Synthetic remote material package')
    complete(client,cid,uuid.uuid4().hex)
    original=client.get(f'/api/cases/{cid}/export').content
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source,zipfile.ZipFile(output,'w') as target:
        for info in source.infolist():
            data=source.read(info)
            if info.filename=='meta.json':
                meta=json.loads(data);group=meta['materials']['groups'][0]
                group['import_day']='20990101';group['batch_no']=2**63-1
                group['files'][0]['file_no']=2**63-1
                data=json.dumps(meta).encode()
            target.writestr(info,data)
    restored=client.post('/api/cases/import',files={'file':('synthetic-remote.fundcase',output.getvalue())})
    assert restored.status_code==200,restored.text
    restored_cid=restored.json()['case_id']
    source=database.q_raw('select source_id from import_sources where case_id=?',[restored_cid])[0]['source_id']
    scope=report(client,f'/api/v1/cases/{restored_cid}/imports/{source}')['scope']
    assert scope['material_name']=='20261114-2-1号检材'
    assert scope['material_origin_name']==f'20990101-{2**63-1}-{2**63-1}号检材'
    newer,_,_,_=complete(client,make_case('Synthetic counter floor'),uuid.uuid4().hex)
    assert report(client,newer)['scope']['material_name']=='20261114-3-1号检材'
    assert database.q_raw('select import_day from material_day_counters')==[{'import_day':'20261114'}]
