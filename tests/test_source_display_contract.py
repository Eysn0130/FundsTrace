"""User P0/P1: ordinary display is exact selected source data, not event bounds."""
import csv
import io
from decimal import Decimal

import pytest
from app import funds_projection as funds
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_funds_projection import flow, event


def payload(rows):
    out = io.StringIO(newline='')
    csv.writer(out).writerows([['交易时间','付款账号','收款账号','交易金额','币种','交易流水号'], *rows])
    return out.getvalue().encode('utf-8-sig')


@pytest.mark.parametrize('scenario', ['H01','R01','R02','X01','X02','T01'])
def test_public_source_counts_amounts_graph_and_profiles(client,database,make_case,scenario):
    cid = make_case('Synthetic source display '+scenario)
    rows = [['2026-01-01 10:00:00.123456','SYN-A','SYN-B','12.34','CNY','SYN-1']]
    if scenario == 'X01': rows[0][3] = '90071992547409.91'
    if scenario in ('R02','X02','T01'):
        rows.append(['2026-01-01 10:00:00.123457','SYN-A','SYN-B','12.34','CNY','SYN-2'])
    if scenario == 'X02': rows[1][3:5] = ['56.78','USD']
    if scenario == 'T01': rows[1][1:3] = ['SYN-B','SYN-C']
    data = payload(rows)
    view=preview(client,cid,data);job=complete(client,cid,view)
    assert job['stage']=='done',job
    if scenario == 'R01':
        repeated=complete(client,cid,preview(client,cid,data,'repeat.csv'));assert repeated['stage']=='done',repeated
    page=client.get(f'/api/v1/cases/{cid}/flows',params={'granularity':'observations'}).json()
    assert len(page['items'])==page['total']==len(rows)
    totals={}
    for row in rows: totals[row[4]]=totals.get(row[4],Decimal(0))+Decimal(row[3])
    expected=[{'currency':c,'amount':format(a,'.2f')} for c,a in sorted(totals.items())]
    stats=client.get(f'/api/v1/cases/{cid}/stats').json()
    assert (stats['flows'],stats['amount_totals'],stats['statistics_policy'])==(len(rows),expected,funds.SELECTION_POLICY)
    assert stats['funds']['confirmed_event_count']==0
    assert client.get('/api/stats',params={'case_id':cid}).json()['flows']==len(rows)
    assert client.post('/api/track',json={'case_id':cid,'flow_ids':[x['id'] for x in page['items']]}).status_code==200
    graph=client.get('/api/graph',params={'case_id':cid}).json()
    assert graph['statistics_policy']==funds.SELECTION_POLICY
    for edge in graph['edges']:
        selected=[row for row in rows if row[1:3]==[edge['source'],edge['target']]]
        by_currency={}
        for row in selected: by_currency[row[4]]=by_currency.get(row[4],Decimal(0))+Decimal(row[3])
        assert edge['cnt']==len(selected)
        assert edge['amount_totals']==[{'currency':c,'amount':format(a,'.2f')} for c,a in sorted(by_currency.items())]
        assert edge['funds']['confirmed_event_count']==0
    for node in graph['nodes']:
        profile=client.get('/api/account-registry/profile',params={'case_id':cid,'account':node['account']}).json()
        for direction,column in [('out',1),('in',2)]:
            selected=[row for row in rows if row[column]==node['account']]
            by_currency={}
            for row in selected: by_currency[row[4]]=by_currency.get(row[4],Decimal(0))+Decimal(row[3])
            assert node[direction+'_cnt']==profile[direction+'_cnt']==len(selected)
            assert node[direction+'_totals']==profile[direction+'_totals']==[{'currency':c,'amount':format(a,'.2f')} for c,a in sorted(by_currency.items())]
    scoped=client.post('/api/graph/view',json={'case_id':cid,'start':'2026-01-01T10:00:00.123456','end':'2026-01-01T10:00:00.123456'}).json()
    assert sum(x['cnt'] for x in scoped['edges'])==1
    deleted=client.delete(f"/api/batches/{job['result']['batch_id']}",params={'case_id':cid})
    assert deleted.status_code==200,deleted.text
    remaining=client.get('/api/graph',params={'case_id':cid}).json()['edges']
    if scenario=='R01':
        assert len(remaining)==1
        assert remaining[0]['cnt']==1 and remaining[0]['amount_totals']==[{'currency':'CNY','amount':'12.34'}]
        withdrawn=client.delete(f"/api/batches/{repeated['result']['batch_id']}",params={'case_id':cid})
        assert withdrawn.status_code==200,withdrawn.text
        assert client.get('/api/graph',params={'case_id':cid}).json()['edges']==[]
    else: assert remaining==[]


def test_source_summary_reads_originals_not_event_representative(database,make_case):
    cid=make_case('Synthetic independent event and source')
    flow(database,cid,1,amount='12.34',currency='CNY')
    flow(database,cid,2,amount='56.78',currency='USD')
    event(database,cid,'SYN-E',[1,2],amount='999.99',currency='CNY')
    sql='select * from flows where case_id=? union all select * from flows where case_id=?'
    assert funds.summary(sql,[cid,cid],observations=True)=={'cnt':2,'total':None,'amount_totals':[{'currency':'CNY','amount':'12.34'},{'currency':'USD','amount':'56.78'}]}
    conditional=funds.summary(sql,[cid,cid])
    assert conditional['confirmed_event_count']==1 and conditional['totals'][0]['amount']=='999.99'
    assert funds.public_fields(conditional)['cnt']==1


def test_source_summary_missing_values_negative_amount_and_case_boundary(database,make_case):
    cid=make_case('Synthetic missing source values');other=make_case('Synthetic other scope')
    flow(database,cid,1,amount=None,currency='CNY');flow(database,cid,2,amount='-1.23',currency='')
    flow(database,other,3,amount='99.99')
    result=funds.summary('select * from flows where case_id=?',[cid],observations=True)
    assert result=={'cnt':2,'total':None,'amount_totals':[{'currency':'','amount':'-1.23'},{'currency':'CNY','amount':None}]}
    with pytest.raises(ValueError,match='one case'):
        funds.account_summaries('select * from flows',observations=True)
