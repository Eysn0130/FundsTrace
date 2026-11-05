"""An explicit own-side account opening name is source text, not identity merging."""
import csv,io,zipfile
import pytest
from app import table_import,case_packages
from test_automatic_table_import import preview
from test_feedback_import_api import complete

def data(headers,rows):
    out=io.StringIO(newline='');csv.writer(out).writerows([headers,*rows]);return out.getvalue().encode('utf-8-sig')
HEAD=['交易时间','交易账号','交易卡号','账户开户名称','交易对手账卡号','交易金额','收付标志']

@pytest.mark.parametrize('suffix',['.csv','.zip'])
def test_source_name_reaches_public_flow_and_distinct_graph_nodes(client,database,make_case,suffix):
    payload=data(HEAD,[['2026-01-01','SYN-A','SYN-C1','SYN同名人','SYN-B','12.34','出'],['2026-01-01','SYN-Z','SYN-C2','SYN同名人','SYN-B','2.00','出']])
    if suffix=='.zip':
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as package:package.writestr('ledger.csv',payload)
        payload=stream.getvalue()
    cid=make_case('Synthetic source opening names');view=preview(client,cid,payload,'source'+suffix);job=complete(client,cid,view);assert job['stage']=='done',job
    flows=client.get(f'/api/v1/cases/{cid}/flows').json()['items']
    assert len(flows)==2 and {x['payer_name'] for x in flows}=={'SYN同名人'}
    assert {x['payer_account'] for x in flows}=={'SYN-A','SYN-Z'}
    assert client.post('/api/track',json={'case_id':cid,'flow_ids':[x['id'] for x in flows]}).status_code==200
    nodes=client.get('/api/graph',params={'case_id':cid}).json()['nodes']
    assert {x['account'] for x in nodes if x['name']=='SYN同名人'}=={'SYN-A','SYN-Z'}
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content==payload
    restored=case_packages.import_package(case_packages.export_package(cid)[0])['case_id']
    assert {x['payer_name'] for x in client.get(f'/api/v1/cases/{restored}/flows').json()['items']}=={'SYN同名人'}

def test_unanchored_opening_name_cannot_be_assigned_to_payer():
    payload=data(['交易时间','付款账号','收款账号','账户开户名称','交易金额'],[['2026-01-01','SYN-A','SYN-B','UNBOUND','1.00']])
    plan=table_import.parse(payload,suffix='.csv');assert len(plan['rows'])==1
    assert plan['rows'][0]['payer_name']==plan['rows'][0]['payee_name']==''

def test_conflicting_own_names_cannot_be_arbitrarily_selected():
    payload=data(HEAD+['交易户名'],[['2026-01-01','SYN-A','SYN-C','SYN开账户名','SYN-B','1.00','出','SYN另一个名称']])
    plan=table_import.parse(payload,suffix='.csv');assert plan['rows']==[]
    assert 'conflicting_field' in plan['records'][0]['details']['validation_issues']

def test_old_profile_keeps_its_original_name_mapping():
    payload=data(HEAD,[['2026-01-01','SYN-A','SYN-C','SYN开账户名','SYN-B','1.00','出']])
    plan=table_import.parse(payload,suffix='.csv',profile=table_import.GUARDED_PROFILE)
    assert plan['rows'][0]['payer_name']==''
