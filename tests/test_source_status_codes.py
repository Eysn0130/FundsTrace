"""A platform's numeric state is not a success declaration by its column name."""
import io,csv
from decimal import Decimal
import pytest
from app import table_import,case_packages
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_folder_evidence import table,transaction

def content(value='1'):
    out=io.StringIO(newline='');csv.writer(out).writerows([['交易时间','付款账号','收款账号','交易金额','交易是否成功','来源平台'],['2026-01-01 10:00:00','SYN-A','SYN-B','12.34',value,'SYN-Platform']]);return out.getvalue().encode('utf-8-sig')

@pytest.mark.parametrize('value',['1','01'])
def test_unknown_numeric_status_preserves_observation_without_claiming_success(client,database,make_case,value):
    cid=make_case('Synthetic unknown platform state');payload=content(value)
    view=preview(client,cid,payload);job=complete(client,cid,view);assert job['stage']=='done',job
    facts=database.q_raw('select amount,payer_account,payee_account from flows where case_id=?',[cid])
    assert facts==[{'amount':Decimal('12.34'),'payer_account':'SYN-A','payee_account':'SYN-B'}]
    records=client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()['items']
    assert records[0]['details']['reported_status']=='unknown'
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content==payload
    restored=case_packages.import_package(case_packages.export_package(cid)[0])['case_id']
    assert database.q_raw('select amount,payer_account,payee_account from flows where case_id=?',[restored])==facts
    assert database.q_raw("select details_json from import_records where case_id=?",[restored])[0]['details_json'].find('unknown')>=0

@pytest.mark.parametrize('value',['1','01'])
def test_exact_feedback_schema_preserves_numeric_observation_without_verified_codebook(value):
    plan=table_import.parse(table('transaction',[transaction(**{'交易是否成功':value})]),suffix='.csv')
    assert len(plan['rows'])==1
    assert plan['records'][0]['details']['reported_status']=='unknown'

def test_historical_table_status_rule_is_frozen():
    plan=table_import.parse(content(),suffix='.csv',profile=table_import.GUARDED_PROFILE)
    assert len(plan['rows'])==1 and plan['records'][0]['details']['reported_status']=='source_reported_success'
