"""Persistent display identities for successfully registered original files.

Call allocation helpers only inside the owning import/package transaction.
No parsing, financial receipt, source-byte or observation identity is changed.
"""
from datetime import datetime
import json
import re
import uuid

from app import db
from .cases import ServiceError


def local_day():
    return datetime.now().astimezone().strftime('%Y%m%d')


def name(day,batch_no,file_no):
    return f'{day}-{batch_no}-{file_no}号检材'


def commit_group(case_id,source_id,requested,session_request):
    previous=json.loads(session_request or '{}').get('material_group_id')
    if previous is not None and requested is not None and previous!=requested:
        raise ServiceError(409,'material_group_changed','该文件已绑定导入批次，请重新选择文件')
    group=requested or previous or source_id
    registered=db.q_raw('select case_id from import_material_groups where group_id=?',[group])
    pending=db.q_raw("""select 1 from import_sessions where import_id<>? and case_id<>?
        and json_extract_string(request_json,'$.material_group_id')=? limit 1""",[source_id,case_id,group])
    if registered and registered[0]['case_id']!=case_id or pending:
        raise ServiceError(409,'material_group_case_mismatch','导入批次不属于当前案件，请重新选择文件')
    return group


def _batch(day):
    rows=db.q_raw('select last_batch from material_day_counters where import_day=?',[day])
    floor=rows[0]['last_batch'] if rows else 0
    number=floor+1
    db.x('''insert into material_day_counters(import_day,last_batch) values (?,?)
        on conflict(import_day) do update set last_batch=excluded.last_batch''',[day,number])
    return number


def register(case_id,source_id,group_id):
    existing=db.q_raw('select material_name from import_materials where case_id=? and source_id=?',[case_id,source_id])
    if existing:
        return existing[0]['material_name']
    groups=db.q_raw('select * from import_material_groups where group_id=?',[group_id])
    if groups and groups[0]['case_id']!=case_id:
        raise ServiceError(409,'material_group_case_mismatch','导入批次不属于当前案件')
    if groups:
        group=groups[0]
    else:
        day=local_day();batch_no=_batch(day)
        db.x('insert into import_material_groups values (?,?,?,?)',[group_id,case_id,day,batch_no])
        group={'import_day':day,'batch_no':batch_no}
    file_no=db.q_raw('select coalesce(max(file_no),0)+1 n from import_materials where group_id=?',[group_id])[0]['n']
    label=name(group['import_day'],group['batch_no'],file_no)
    db.x('''insert into import_materials(case_id,source_id,group_id,file_no,material_name,origin_name)
        values (?,?,?,?,?,null)''',[case_id,source_id,group_id,file_no,label])
    return label


def source(case_id,source_id):
    rows=db.q_raw('select material_name,origin_name from import_materials where case_id=? and source_id=?',[case_id,source_id])
    return rows[0] if rows else {'material_name':None,'origin_name':None}


def package_metadata(case_id):
    rows=db.q_raw('''select g.group_id,g.import_day,g.batch_no,m.source_id,m.file_no,m.origin_name
        from import_material_groups g join import_materials m on m.group_id=g.group_id and m.case_id=g.case_id
        where g.case_id=? order by g.import_day,g.batch_no,m.file_no''',[case_id])
    groups={}
    for row in rows:
        group=groups.setdefault(row['group_id'],{'group_id':row['group_id'],'import_day':row['import_day'],'batch_no':row['batch_no'],'files':[]})
        group['files'].append({'source_id':row['source_id'],'file_no':row['file_no'],**({'origin_name':row['origin_name']} if row['origin_name'] else {})})
    return {'version':1,'groups':list(groups.values())} if groups else None


def validate_package(metadata,source_ids):
    if metadata is None:
        return []
    if not isinstance(metadata,dict) or set(metadata)!={'version','groups'} or type(metadata['version']) is not int or metadata['version']!=1 or not isinstance(metadata['groups'],list):
        raise ValueError('material metadata invalid')
    seen_groups,seen_batches,seen_sources=set(),set(),set()
    def ordinal(value):
        return type(value) is int and 1<=value<=2**63-1
    def day(value):
        return isinstance(value,str) and bool(re.fullmatch(r'[0-9]{8}',value)) and datetime.strptime(value,'%Y%m%d').strftime('%Y%m%d')==value
    def label(value):
        if not isinstance(value,str) or len(value)>80:
            return False
        match=re.fullmatch(r'([0-9]{8})-([1-9][0-9]*)-([1-9][0-9]*)号检材',value)
        return bool(match) and day(match[1]) and ordinal(int(match[2])) and ordinal(int(match[3]))
    for group in metadata['groups']:
        if (not isinstance(group,dict) or set(group)!={'group_id','import_day','batch_no','files'}
                or not isinstance(group['group_id'],str) or not re.fullmatch(r'[0-9a-f]{32}',group['group_id'])
                or group['group_id'] in seen_groups or not day(group['import_day']) or not ordinal(group['batch_no'])
                or (group['import_day'],group['batch_no']) in seen_batches or not isinstance(group['files'],list) or not group['files']):
            raise ValueError('material group invalid')
        seen_groups.add(group['group_id']);seen_batches.add((group['import_day'],group['batch_no']))
        seen_files=set()
        for item in group['files']:
            if (not isinstance(item,dict) or not {'source_id','file_no'}<=set(item)<= {'source_id','file_no','origin_name'}
                    or not isinstance(item['source_id'],str) or item['source_id'] not in source_ids or item['source_id'] in seen_sources
                    or not ordinal(item['file_no']) or item['file_no'] in seen_files
                    or 'origin_name' in item and not label(item['origin_name'])):
                raise ValueError('material file invalid')
            seen_sources.add(item['source_id']);seen_files.add(item['file_no'])
    return metadata['groups']


def restore(case_id,groups,source_mapping):
    # Package labels are provenance claims, never authority over this store's
    # date/counters. A restore is a new local registration with original labels.
    day=local_day()
    for original in groups:
        group_id=uuid.uuid4().hex
        batch_no=_batch(day)
        db.x('insert into import_material_groups values (?,?,?,?)',[group_id,case_id,day,batch_no])
        for file_no,item in enumerate(sorted(original['files'],key=lambda file:file['file_no']),1):
            before=name(original['import_day'],original['batch_no'],item['file_no'])
            current=name(day,batch_no,file_no)
            earliest=item.get('origin_name') or before
            origin=earliest if earliest!=current else None
            db.x('''insert into import_materials(case_id,source_id,group_id,file_no,material_name,origin_name)
                values (?,?,?,?,?,?)''',[case_id,source_mapping[item['source_id']],group_id,file_no,current,origin])
