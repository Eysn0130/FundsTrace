"""Independent exact-clock examples, public queries and frozen-version replay."""
import csv
import io
from decimal import Decimal

import pytest
from app import table_import, table_archive, case_packages
from test_declared_field_safety import zipped
from test_automatic_table_import import preview
from test_feedback_import_api import complete

HEAD = ['交易日期(YYYYMMDD)', '交易时刻(HHmmssffffff)', '本方账号', '对方账号', '收付标志', '交易金额']

def payload(rows, headers=HEAD):
    out = io.StringIO(newline='');csv.writer(out).writerows([headers, *rows]);return out.getvalue().encode('utf-8-sig')

def row(date, clock):
    return [date, clock, 'SYN-A', 'SYN-B', '出', '12.34']

@pytest.mark.parametrize('date,clock,expected', [
    ('20260101','100000123456','2026-01-01T10:00:00.123456'),
    ('20260101','100000123457','2026-01-01T10:00:00.123457'),
    ('20260101','000000000000','2026-01-01T00:00:00.000000'),
    ('20260101','235959999999','2026-01-01T23:59:59.999999'),
    ('20240229','000000000001','2024-02-29T00:00:00.000001')])
@pytest.mark.parametrize('archive', [False, True])
def test_declared_twelve_digits_are_exact(date, clock, expected, archive):
    raw = payload([row(date, clock)])
    plan = table_archive.parse(io.BytesIO(zipped(raw))) if archive else table_import.parse(raw, suffix='.csv')
    assert len(plan['rows']) == 1
    assert plan['rows'][0]['trade_time'].isoformat(timespec='microseconds') == expected
    assert plan['rows'][0]['amount'] == Decimal('12.34')

@pytest.mark.parametrize('date,clock', [
    ('20260101','1000001234'),('20260101','10000012345'),('20260101','1000001234567'),
    ('20260101','240000000000'),('20260101','106000000000'),('20260101','100060000000'),
    ('20260229','100000123456'),('20260230','100000123456'),('20260101','100000.123456'),('', '100000123456')])
def test_invalid_declared_clock_never_becomes_midnight(date,clock):
    plan=table_import.parse(payload([row(date,clock)]),suffix='.csv')
    assert plan['rows']==[] and plan['records'][0]['disposition']=='unresolved'

@pytest.mark.parametrize('profile', [table_import.GUARDED_PROFILE, table_import.GUARDED_EVIDENCE_PROFILE])
def test_guarded_history_stays_pending(profile):
    plan=table_import.parse(payload([row('20260101','100000123456')]),suffix='.csv',profile=profile)
    assert plan['rows']==[] and plan['records'][0]['reason_code']=='invalid_time'

def test_unannotated_and_conflicting_clocks_are_not_guessed():
    unannotated=table_import.parse(payload([row('20260101','100000123456')], [HEAD[0],'交易时刻',*HEAD[2:]]),suffix='.csv')
    assert unannotated['rows']==[]
    for header,value in [('交易时刻(HHmmssffffff)','100000123457'),('交易时间','2026-01-02 10:00:00.123456')]:
        plan=table_import.parse(payload([row('20260101','100000123456')+[value]],HEAD+[header]),suffix='.csv')
        assert plan['rows']==[]

@pytest.mark.parametrize('entry',['csv','zip','directory'])
def test_public_precise_boundaries_sources_and_package(client,database,make_case,entry):
    cid=make_case('Synthetic exact clock public')
    raw=payload([row('20260101','100000123456'),row('20260101','100000123457')])
    content=raw if entry=='csv' else zipped(raw)
    if entry=='directory':
        response=client.post(f'/api/v1/cases/{cid}/imports/directory/preview',files=[('files',('source.zip',content))])
        assert response.status_code==200,response.text
        view=response.json()
    else:view=preview(client,cid,content,'source.csv' if entry=='csv' else 'source.zip')
    job=complete(client,cid,view);assert job['stage']=='done',job
    page=client.get(f'/api/v1/cases/{cid}/flows').json();assert page['total']==2
    assert {x['trade_time'] for x in page['items']}=={'2026-01-01 10:00:00.123456','2026-01-01 10:00:00.123457'}
    for fraction in ['123456','123457']:
        result=client.get('/api/flows',params={'case_id':cid,'tfrom':'2026-01-01 10:00:00.'+fraction,'tto':'2026-01-01 10:00:00.'+fraction}).json()
        assert result['total']==1 and result['rows'][0]['trade_time'].endswith(fraction)
    source=client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content
    if entry!='directory':assert source==content
    restored=case_packages.import_package(case_packages.export_package(cid)[0])['case_id']
    assert database.q_raw('select trade_time,amount from flows where case_id=? order by trade_time',[restored])==database.q_raw('select trade_time,amount from flows where case_id=? order by trade_time',[cid])
