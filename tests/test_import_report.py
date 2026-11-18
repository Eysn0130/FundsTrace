"""Synthetic public file reports: exact scope, money, source links and revisions."""
import csv
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import datetime,timedelta
from decimal import Decimal


HEADERS=['交易流水号','交易时间','本方账号','本方户名','对方账号','对方户名','收付标志','交易金额','币种']


def payload(rows):
    stream=io.StringIO(newline='');csv.writer(stream).writerows([HEADERS,*rows])
    return stream.getvalue().encode('utf-8-sig')


def complete(client,cid,data,name='synthetic.csv'):
    response=client.post(f'/api/v1/cases/{cid}/imports/preview',files={'file':(name,data)})
    assert response.status_code==200,response.text
    preview=response.json();base=f'/api/v1/cases/{cid}/imports/{preview["import_id"]}'
    assert client.post(base+'/commit',json={'mode':preview['mode'],'mapping':preview['mapping'],'mark_accounts':[]}).status_code==200
    until=time.monotonic()+20
    while time.monotonic()<until:
        job=client.get(base).json()
        if job['stage'] in {'done','error'}:
            break
        time.sleep(.01)
    assert job['stage']=='done',job
    return base,preview


def row(no,direction,amount,peer='000SYN-B',currency='CNY',stamp='2026-11-13 09:00:00',own='000SYN-A',name='合成本方'):
    return [no,stamp,own,name,peer,'合成同名对手',direction,amount,currency]


def test_report_full_member_precision_rank_timeline_and_drilldown(client,make_case):
    from app.services import import_report
    cid=make_case('Synthetic full member report')
    rows=[row(f'SYN-{i:03}', '收入' if i%2 else '支出',f'{100+i}.01',peer=f'000SYN-B{i%5}',stamp=f'2026-11-13 09:{i:02}:00') for i in range(60)]
    rows[6][1]='not a date'
    data=payload(rows);base,preview=complete(client,cid,data)
    # Direct call makes safe HTTP error wrapping unable to hide a SQL failure.
    direct=import_report.report(cid,preview['import_id'],'table.csv')
    response=client.get(base+'/report',params={'member_path':'table.csv'})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['quality']['source_records']==60
    assert result['quality']['dispositions']=={'imported':59,'unresolved':1}
    assert result['summary']['in_count']==30 and result['summary']['out_count']==29
    assert result['summary']['in_amount']=='3900.30'
    assert result['summary']['out_amount']=='3764.29'
    assert result['summary']['net_amount']=='136.01'
    assert result['summary']['transaction_count']==59
    assert result['perspective']=='own_ledger'
    assert result['accounts']['total']==1
    assert result['accounts']['items'][0]['cards']==[]
    assert result['accounts']['items'][0]['account_number']=='000SYN-A'
    assert len(result['top_in'])==5
    assert sum(bucket['in_count'] for bucket in result['timeline'])==30
    assert result['periods']==[{'first_time':'2026-11-13 09:00:00','last_time':'2026-11-13 09:59:00','time_basis':'unqualified'}]
    assert direct['summary']==result['summary']
    first=result['top_in'][0]
    drill=client.get(base+'/report/records',params={'member_path':'table.csv','expected_revision':result['analysis_revision'],'counterparty':first['account'],'direction':'in','currency':'CNY','page_size':2})
    assert drill.status_code==200,drill.text
    assert drill.json()['total']==6 and len(drill.json()['items'])==2
    assert all(item['member_path']=='table.csv' for item in drill.json()['items'])
    assert result['scope']['source_sha256']==hashlib.sha256(data).hexdigest()
    assert result['integrity']['status']=='registered'
    verification=client.post(base+'/report/verify',params={'member_path':'table.csv','expected_revision':result['analysis_revision']})
    assert verification.status_code==200,verification.text
    assert verification.json()['status']=='match' and verification.json()['observed_sha256']==hashlib.sha256(data).hexdigest()


def test_report_member_not_batch_and_duplicate_sources_keep_cohort(client,make_case):
    cid=make_case('Synthetic scoped duplicate report')
    one=payload([row('SYN-A','收入','9999999999999999.99')])
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('one.csv',one)
        archive.writestr('two.csv',payload([row('SYN-B','支出','0.01',peer='000SYN-C')]))
    first,_=complete(client,cid,stream.getvalue(),'synthetic.zip')
    second,_=complete(client,cid,one,'synthetic-copy.csv')
    a=client.get(first+'/report',params={'member_path':'one.csv'})
    assert a.status_code==200,a.text
    assert a.json()['summary']['in_amount']=='9999999999999999.99'
    assert a.json()['summary']['out_amount']=='0.00'
    assert a.json()['scope']['member_digest_kind']=='bytes'
    duplicate=client.get(second+'/report',params={'member_path':'table.csv'})
    assert duplicate.status_code==200,duplicate.text
    assert duplicate.json()['quality']['dispositions']=={'duplicate':1}
    assert duplicate.json()['summary']==a.json()['summary']
    assert duplicate.json()['accounts']['items'][0]['names']==['合成本方']
    assert duplicate.json()['accounts']['items'][0]['cards']==[]


def test_report_case_revision_integrity_and_html_escape(client,database,make_case):
    from app.services import imports
    from app import analysis_snapshot
    cid=make_case('Synthetic report guards')
    raw=payload([row('SYN-A','收入','123.45',name='<script>alert(1)</script>')])
    base,preview=complete(client,cid,raw)
    response=client.get(base+'/report',params={'member_path':'table.csv'})
    assert response.status_code==200,response.text
    result=response.json();revision=result['analysis_revision']
    foreign=make_case('Synthetic report foreign')
    assert client.get(f'/api/v1/cases/{foreign}/imports/{preview["import_id"]}/report',params={'member_path':'table.csv'}).status_code==404
    assert client.get(base+'/report',params={'member_path':'../table.csv'}).status_code==404
    assert client.get(base+'/report',params={'member_path':'table.csv','page_size':51}).status_code==422
    assert client.get(base+'/report/records',params={'member_path':'table.csv'}).status_code==422
    doc=client.get(base+'/report/document',params={'member_path':'table.csv','expected_revision':revision})
    assert doc.status_code==200,doc.text
    assert '<script>alert' not in doc.text and '&lt;script&gt;' in doc.text
    assert revision in doc.text and '共1项' in doc.text
    embedded=json.loads(re.search(r'<script type="application/json" id="funds-report-data">(.*?)</script>',doc.text,re.S)[1])
    assert embedded['schema']=='import-member-funds-report.v1' and embedded['scope']=='current_view'
    assert embedded['report']['analysis_revision']==revision
    assert embedded['report']['summary']['in_amount']=='123.45'
    assert embedded['report']['accounts']['items'][0]['names']==['<script>alert(1)</script>']
    assert '\\u003cscript\\u003e' in doc.text
    path=imports._path(preview['import_id'],'.csv');path.write_bytes(b'X'*len(raw))
    verify=client.post(base+'/report/verify',params={'member_path':'table.csv','expected_revision':revision})
    assert verify.status_code==200 and verify.json()['status']=='mismatch'
    assert verify.json()['observed_sha256'] is None
    assert client.get(base+'/report',params={'member_path':'table.csv'}).json()['summary']==result['summary']
    with database.transaction():analysis_snapshot.invalidate(cid)
    assert client.get(base+'/report',params={'member_path':'table.csv','expected_revision':revision}).status_code==409
    assert client.post(base+'/report/verify',params={'member_path':'table.csv','expected_revision':revision}).status_code==409
    assert client.get(base+'/report/document',params={'member_path':'table.csv','expected_revision':revision}).status_code==409


def test_report_separates_currency_and_endpoint_fallback(client,make_case):
    cid=make_case('Synthetic report currencies')
    base,_=complete(client,cid,payload([row('CNY-A','收入','1.01'),row('USD-A','收入','2.02',currency='USD')]))
    default=client.get(base+'/report',params={'member_path':'table.csv'}).json()
    assert default['currencies']==['CNY','USD'] and default['summary']['in_amount']=='1.01'
    usd=client.get(base+'/report',params={'member_path':'table.csv','currency':'USD'}).json()
    assert usd['summary']['in_amount']=='2.02' and usd['summary']['transaction_count']==1
    assert 'USD' in usd['method_paragraphs'][0]['text'] and '交易共1笔' in usd['method_paragraphs'][0]['text']
    assert client.get(base+'/report',params={'member_path':'table.csv','currency':'EUR'}).status_code==422
    headers=['流水号','交易时间','付款账号','付款户名','收款账号','收款户名','金额','币种']
    stream=io.StringIO();csv.writer(stream).writerows([headers,['A','2026-11-13','SYN-P','合成甲','SYN-Q','合成乙','3.00','CNY']])
    other,_=complete(client,cid,stream.getvalue().encode('utf-8-sig'))
    fallback=client.get(other+'/report',params={'member_path':'table.csv'})
    assert fallback.status_code==200,fallback.text
    assert fallback.json()['perspective']=='transaction_endpoints'
    assert fallback.json()['summary']['transaction_count']==1
    bucket=fallback.json()['timeline'][0]
    assert bucket['in_count']+bucket['out_count']==2
    assert bucket.get('transaction_count')==1
    assert fallback.json()['summary']['amount']=='3.00'
    assert fallback.json()['summary']['in_amount']=='3.00' and fallback.json()['summary']['out_amount']=='3.00'
    assert '付款、收款端点' in fallback.json()['method_paragraphs'][1]['text']


def test_report_preserves_both_own_ledgers_without_double_counting_confirmed_transfer(client,make_case):
    from test_bank_posting_pairs import entries,encoded
    cid=make_case('Synthetic confirmed member report')
    base,_=complete(client,cid,encoded(entries()))
    response=client.get(base+'/report',params={'member_path':'table.csv'})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['quality']['linked_observations']==4 and data['quality']['selected_transactions']==3
    assert data['summary']['transaction_count']==3 and data['summary']['amount']=='39.41'
    assert sum(b['transaction_count'] for b in data['timeline'])==3
    assert data['summary']['in_amount']=='38.41' and data['summary']['out_amount']=='38.41'
    accounts={r['account_number']:r for r in data['accounts']['items']}
    assert set(accounts)=={'SYN-A','SYN-B'}
    assert accounts['SYN-A']['out_count']==2 and accounts['SYN-A']['out_amount']=='38.41'
    assert accounts['SYN-B']['in_count']==2 and accounts['SYN-B']['in_amount']=='38.41'
    own_a=client.get(base+'/report',params={'member_path':'table.csv','account':accounts['SYN-A']['account']})
    assert own_a.status_code==200,own_a.text
    assert own_a.json()['accounts']['total']==2
    assert own_a.json()['summary']['in_amount']=='0.00' and own_a.json()['summary']['out_amount']=='38.41'
    pair=next(r for r in data['relations']['items'] if r['account_number']=='SYN-A' and r['counterparty_number']=='SYN-B')
    support=client.get(base+'/report/records',params={'member_path':'table.csv','expected_revision':data['analysis_revision'],
        'account':pair['account'],'counterparty':pair['counterparty'],'direction':'out'})
    assert support.status_code==200,support.text
    assert support.json()['total']==2
    assert {r['source_row'] for r in support.json()['items']}=={3,5}


def test_report_sheet_locator_not_original_bytes_and_no_cross_sheet_amounts(client,make_case):
    from openpyxl import Workbook
    book=Workbook();book.active.title='合成第一表';book.active.append(HEADERS);book.active.append(row('A','收入','1.23'))
    second=book.create_sheet('合成第二表');second.append(HEADERS);second.append(row('B','收入','4.56'))
    stream=io.BytesIO();book.save(stream);book.close()
    base,_=complete(client,make_case('Synthetic worksheet scopes'),stream.getvalue(),'synthetic.xlsx')
    result=client.get(base+'/report',params={'member_path':'worksheets/0002'})
    assert result.status_code==200,result.text
    data=result.json()
    assert data['scope']['member_digest_kind']=='worksheet_locator'
    assert data['scope']['member_size_bytes'] is None
    assert data['scope']['member_sha256']!=data['scope']['source_sha256']
    assert data['summary']['in_amount']=='4.56' and data['quality']['source_records']==1
    assert '工作表摘要仅用于定位' in data['method_paragraphs'][3]['text']
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        archive.writestr('worksheets/0002.csv',payload([row('CSV','收入','7.89')]))
    csv_base,_=complete(client,make_case('Synthetic worksheet-named CSV path'),stream.getvalue(),'synthetic.zip')
    actual_csv=client.get(csv_base+'/report',params={'member_path':'worksheets/0002.csv'}).json()
    assert actual_csv['scope']['member_digest_kind']=='bytes' and actual_csv['scope']['member_size_bytes']>0


def test_report_cards_are_aliases_not_money_rows_and_withdrawn_source_does_not_revive(client,database,make_case):
    from test_bank_posting_pairs import entries,encoded
    cid=make_case('Synthetic card aliases report')
    values=entries()[:2]
    values[0]['交易卡号']='SYN-CARD-A1';values[1]['交易卡号']='SYN-CARD-A2'
    base,preview=complete(client,cid,encoded(values))
    response=client.get(base+'/report',params={'member_path':'table.csv'})
    assert response.status_code==200,response.text
    data=response.json();own,=data['accounts']['items']
    assert own['account_number']=='SYN-A' and own['out_amount']=='38.41' and own['out_count']==2
    assert own['cards']==['SYN-CARD-A1','SYN-CARD-A2']
    # A later identical source may support the same flows, but cannot revive
    # the retired original operation as a report selection.
    copy,_=complete(client,cid,encoded(values),'synthetic-copy.csv')
    batch=database.q_raw('select batch_id from import_source_operations where source_id=?',[preview['import_id']])[0]['batch_id']
    assert client.delete(f'/api/batches/{batch}',params={'case_id':cid}).status_code==200
    assert client.get(base+'/report',params={'member_path':'table.csv'}).status_code==409
    active=client.get(copy+'/report',params={'member_path':'table.csv'})
    assert active.status_code==200,active.text
    assert active.json()['summary']['amount']=='38.41'


def test_report_long_period_buckets_cover_entire_range_and_mixed_time_domains(client,make_case):
    rows=[row('SYN-FIRST','收入','1.01',stamp='2000-01-01 00:00:00'),
          row('SYN-LAST','收入','2.02',stamp='2026-11-13 12:00:00'),
          row('SYN-UTC','收入','3.03',stamp='2026-11-13T12:00:00+08:00')]
    base,_=complete(client,make_case('Synthetic time domain report'),payload(rows))
    result=client.get(base+'/report',params={'member_path':'table.csv'})
    assert result.status_code==200,result.text
    report=result.json()
    assert len(report['timeline'])<=242
    assert sum(r['in_count'] for r in report['timeline'])==3
    assert {r['time_basis'] for r in report['periods']}=={'utc','unqualified'}
    assert next(r for r in report['periods'] if r['time_basis']=='utc')['first_time']=='2026-11-13 04:00:00Z'
    relation,=report['relations']['items']
    assert relation['time_basis']=='mixed' and relation['first_time'] is None and relation['last_time'] is None
    assert '时间分别归集，不直接混排' in report['method_paragraphs'][2]['text']


def test_report_duplicate_names_and_cards_belong_to_selected_source_not_original_flow(client,database,make_case):
    cid=make_case('Synthetic scoped labels')
    values=[row('SYN-A','支出','12.34')]
    _,first_preview=complete(client,cid,payload(values),'synthetic-first.csv')
    values[0][5]='合成第二来源户名'
    second,second_preview=complete(client,cid,payload(values),'synthetic-second.csv')
    # Seed a historical/package provenance shape: a later source support
    # references an existing financial observation with a different label.
    # Today's CSV hash includes names, so this tests the reader contract rather
    # than asking the parser to change its deduplication semantics.
    original=database.q_raw('select flow_id from flow_sources where source_id=?',[first_preview['import_id']])[0]['flow_id']
    database.x("update flow_sources set flow_id=?,disposition='duplicate' where source_id=?",[original,second_preview['import_id']])
    database.x("update import_records set disposition='duplicate' where source_id=?",[second_preview['import_id']])
    data=client.get(second+'/report',params={'member_path':'table.csv'})
    assert data.status_code==200,data.text
    report=data.json()
    assert report['quality']['dispositions']=={'duplicate':1}
    assert report['top_out'][0]['names']==['合成第二来源户名']
    assert report['relations']['items'][0]['names']==['合成第二来源户名']
    assert report['accounts']['items'][0]['names']==['合成本方']
    drill=client.get(second+'/report/records',params={'member_path':'table.csv','expected_revision':report['analysis_revision']})
    assert drill.status_code==200,drill.text
    assert drill.json()['items'][0]['preview']['payee_name']=='合成第二来源户名'
    # Two differently labelled source supports can reference a single flow.
    # Aliases stay visible, while its financial amount remains counted once.
    from test_bank_posting_pairs import entries,encoded
    bank=entries()[1]
    another={**bank,'交易卡号':'SYN-CARD-A2'}
    bank['交易卡号']='SYN-CARD-A1'
    cards,_=complete(client,cid,encoded([bank,another]),'synthetic-aliases.csv')
    current=client.get(cards+'/report',params={'member_path':'table.csv'})
    assert current.status_code==200,current.text
    account,=current.json()['accounts']['items']
    assert account['cards']==['SYN-CARD-A1','SYN-CARD-A2']
    assert account['out_amount']=='37.41' and account['out_count']==1


def test_report_late_conflict_keeps_known_source_amounts_as_distinct_observations(client,make_case):
    from test_event_import import csv_rows,transaction
    from test_folder_evidence import accepted
    cid=make_case('Synthetic report late conflict')
    view,_=accepted(client,cid,[('events.csv',csv_rows([transaction(own) for own in ('SYN-A','SYN-B')]))],scope_bank=None)
    base=f'/api/v1/cases/{cid}/imports/{view["import_id"]}'
    first=client.get(base+'/report',params={'member_path':'events.csv'})
    assert first.status_code==200,first.text
    before=first.json()
    assert before['summary']['transaction_count']==1 and before['summary']['amount']=='100.00'
    _,conflict=accepted(client,cid,[('conflict.csv',csv_rows([transaction('SYN-A',**{'交易金额':'101.00'})]))],scope_bank=None)
    changed=client.get(base+'/report',params={'member_path':'events.csv'})
    assert changed.status_code==200,changed.text
    after=changed.json()
    assert after['quality']['linked_observations']==2 and after['quality']['pending_observations']==2
    assert after['summary']['transaction_count']==2 and after['summary']['amount']=='200.00'
    assert client.get(base+'/report',params={'member_path':'events.csv','expected_revision':before['analysis_revision']}).status_code==409
    assert client.delete(f'/api/batches/{conflict["batch_id"]}',params={'case_id':cid}).status_code==200
    restored=client.get(base+'/report',params={'member_path':'events.csv'}).json()
    assert restored['summary']['transaction_count']==1 and restored['summary']['amount']=='100.00'


def test_report_account_and_relation_pagination_are_independent(client,make_case):
    base,_=complete(client,make_case('Synthetic independent report tables'),payload([
        row(f'SYN-{i}','支出','1.01',peer=f'SYN-PEER-{i:03}') for i in range(45)]))
    params={'member_path':'table.csv','accounts_page':1,'relations_page':2,'page_size':20}
    response=client.get(base+'/report',params=params)
    assert response.status_code==200,response.text
    data=response.json()
    assert data['accounts']['page']==1 and data['accounts']['total']==1 and len(data['accounts']['items'])==1
    assert data['relations']['page']==2 and data['relations']['total']==45 and len(data['relations']['items'])==20
    assert data['relations']['items'][0]['counterparty_number']=='SYN-PEER-020'
    doc=client.get(base+'/report/document',params={**params,'expected_revision':data['analysis_revision']})
    assert doc.status_code==200,doc.text
    assert '第1页，共1项' in doc.text and '第2页，共45项' in doc.text
    assert client.get(base+'/report',params={**params,'accounts_page':0}).status_code==422


def test_report_partial_own_evidence_keeps_coverage_gap_explicit(client,database,make_case):
    cid=make_case('Synthetic partial own-role history')
    base,preview=complete(client,cid,payload([row('SYN-1','收入','1.01'),row('SYN-2','收入','2.02')]))
    # A legacy/package member may lack a frozen own-ledger snapshot on one row.
    saved=database.q_raw('select record_no,details_json from import_records where source_id=? order by record_no',[preview['import_id']])[1]
    details=json.loads(saved['details_json']);details.pop('source_ledger_evidence')
    database.x('update import_records set details_json=? where source_id=? and record_no=?',[json.dumps(details),preview['import_id'],saved['record_no']])
    response=client.get(base+'/report',params={'member_path':'table.csv'})
    assert response.status_code==200,response.text
    report=response.json()
    assert report['quality']['selected_transactions']==2
    assert report['quality']['unattributed_transactions']==1
    assert report['summary']['transaction_count']==1 and report['summary']['in_amount']=='1.01'


def test_report_overview_counts_full_scope_names_numbers_and_offpage_selection(client,make_case):
    from test_bank_posting_pairs import entries,encoded
    template=entries()[1]
    values=[{**template,'交易账号':f'SYN-OWN-{i:03}','交易卡号':f'SYN-CARD-{i:03}',
        '交易方户名':'合成同一登记户名' if i<34 else '', '交易流水号':f'SYN-{i}',
        '交易时间':f'2026-11-13 09:{i:02}:00','交易金额':'1.01','交易余额':'100.00',
        '交易对手账卡号':'SYN-PEER'} for i in range(35)]
    values.append({**values[0],'交易卡号':'SYN-CARD-EXTRA','交易流水号':'SYN-EXTRA',
                   '交易时间':'2026-11-13 10:00:00','交易金额':'2.02'})
    base,_=complete(client,make_case('Synthetic full report overview'),encoded(values))
    response=client.get(base+'/report',params={'member_path':'table.csv'})
    assert response.status_code==200,response.text
    report=response.json()
    assert len(report['accounts']['items'])==20 and report['accounts']['total']==35
    assert {k:v for k,v in report['overview'].items() if k!='selected_account'}=={
        'name_count':1,'account_count':35,'number_count':71,'unnamed_account_count':1}
    assert report['summary']['out_amount']=='37.37'
    offpage=client.get(base+'/report',params={'member_path':'table.csv','accounts_page':2}).json()['accounts']['items'][0]
    narrowed=client.get(base+'/report',params={'member_path':'table.csv','account':offpage['account'],'accounts_page':1}).json()
    assert all(item['account']!=offpage['account'] for item in narrowed['accounts']['items'])
    assert narrowed['overview']['account_count']==1
    assert narrowed['overview']['selected_account']['account']==offpage['account']
    assert narrowed['overview']['selected_account']['out_amount']==offpage['out_amount']


def test_report_overview_does_not_count_only_truncated_alias_arrays(client,make_case):
    from test_bank_posting_pairs import entries,encoded
    template=entries()[1]
    values=[{**template,'交易账号':'SYN-SAME-ACCOUNT','交易卡号':f'SYN-CARD-{i:03}',
        '交易方户名':f'合成来源名称{i:02}','交易流水号':f'SYN-{i}',
        '交易时间':f'2026-11-13 09:{i:02}:00','交易金额':'1.01','交易余额':'100.00'} for i in range(25)]
    base,_=complete(client,make_case('Synthetic bounded aliases overview'),encoded(values))
    response=client.get(base+'/report',params={'member_path':'table.csv'})
    assert response.status_code==200,response.text
    report=response.json();account,=report['accounts']['items']
    assert len(account['names'])==20 and len(account['cards'])==20
    assert report['overview']['name_count']==25 and report['overview']['number_count']==26
    assert report['overview']['account_count']==1 and report['summary']['out_amount']=='25.25'


def test_report_calendar_grains_keep_exact_money_cross_year_and_time_domains(client,make_case):
    rows=[row('SYN-DEC','收入','9999999999999999.99',stamp='2025-12-31 23:59:59'),
          row('SYN-JAN1','支出','0.01',stamp='2026-01-01 00:00:00'),
          row('SYN-JAN2','收入','0.02',stamp='2026-01-31 23:59:59'),
          row('SYN-FEB','支出','0.03',stamp='2026-02-01 00:00:00'),
          row('SYN-UTC','收入','0.04',stamp='2026-01-01T00:30:00+08:00')]
    base,_=complete(client,make_case('Synthetic calendar report'),payload(rows))
    response=client.get(base+'/report',params={'member_path':'table.csv','timeline_grain':'month'})
    assert response.status_code==200,response.text
    result=response.json();buckets={(b['time_basis'],b['start']):b for b in result['timeline']}
    assert result['timeline_mode']=='month' and result['timeline_grain']=='月'
    assert result['timeline_pagination']=={'total':4,'page':1,'page_size':120}
    assert buckets['unqualified','2025-12-01 00:00:00']['end']=='2026-01-01 00:00:00'
    assert buckets['unqualified','2025-12-01 00:00:00']['in_amount']=='9999999999999999.99'
    assert buckets['unqualified','2026-01-01 00:00:00']['out_amount']=='0.01'
    assert buckets['unqualified','2026-01-01 00:00:00']['in_amount']=='0.02'
    assert buckets['unqualified','2026-02-01 00:00:00']['end']=='2026-03-01 00:00:00'
    assert buckets['utc','2025-12-01 00:00:00Z']['in_amount']=='0.04'
    yearly=client.get(base+'/report',params={'member_path':'table.csv','timeline_grain':'year'}).json()
    assert yearly['timeline_mode']=='year' and yearly['timeline_grain']=='年'
    assert {(b['time_basis'],b['start']) for b in yearly['timeline']}=={
        ('unqualified','2025-01-01 00:00:00'),('unqualified','2026-01-01 00:00:00'),('utc','2025-01-01 00:00:00Z')}
    assert yearly['summary']==result['summary']
    assert client.get(base+'/report',params={'member_path':'table.csv','timeline_grain':'week'}).status_code==422


def test_report_explicit_calendar_days_page_full_range_without_regraining(client,make_case):
    start=datetime(2024,12,1)
    base,_=complete(client,make_case('Synthetic long daily series'),payload([
        row(f'SYN-{i}','收入','1.01',stamp=str(start+timedelta(days=i))) for i in range(250)]))
    params={'member_path':'table.csv','timeline_grain':'day'}
    first=client.get(base+'/report',params=params).json()
    assert first['timeline_pagination']=={'total':250,'page':1,'page_size':120}
    assert first['timeline_mode']=='day' and first['timeline_grain']=='日' and len(first['timeline'])==120
    second=client.get(base+'/report',params={**params,'timeline_page':2,'expected_revision':first['analysis_revision']}).json()
    third=client.get(base+'/report',params={**params,'timeline_page':99,'expected_revision':first['analysis_revision']}).json()
    assert second['timeline_pagination']['page']==2 and third['timeline_pagination']['page']==3
    assert len(second['timeline'])==120 and len(third['timeline'])==10
    combined=[*first['timeline'],*second['timeline'],*third['timeline']]
    assert len({b['start'] for b in combined})==250
    assert sum(Decimal(b['in_amount']) for b in combined)==Decimal('252.50')
    assert third['summary']==first['summary'] and third['periods']==first['periods']
    assert third['timeline'][-1]['end']==str(start+timedelta(days=250))
    assert '统计期间和总额仍覆盖完整所选范围' in third['method_paragraphs'][2]['text']
    document=client.get(base+'/report/document',params={**params,'timeline_page':2,'expected_revision':first['analysis_revision']})
    assert document.status_code==200,document.text
    embedded=json.loads(re.search(r'<script type="application/json" id="funds-report-data">(.*?)</script>',document.text,re.S)[1])
    assert embedded['report']['timeline_mode']=='day' and embedded['report']['timeline_pagination']['page']==2


def test_report_peer_filter_overrides_only_top_lists_and_downstream_scope(client,database,make_case):
    base,_=complete(client,make_case('Synthetic local peer filter'),payload([
        row('A-IN','收入','10.01',own='SYN-OWN-A',peer='SYN-PEER-A1',name='合成甲登记户名'),
        row('A-OUT','支出','20.02',own='SYN-OWN-A',peer='SYN-PEER-A2',name='合成甲登记户名'),
        row('B-IN','收入','30.03',own='SYN-OWN-B',peer='SYN-PEER-B1',name='合成乙登记户名'),
        row('B-OUT','支出','40.04',own='SYN-OWN-B',peer='SYN-PEER-B2',name='合成乙登记户名')]))
    initial=client.get(base+'/report',params={'member_path':'table.csv'}).json()
    accounts={r['account_number']:r['account'] for r in initial['accounts']['items']}
    params={'member_path':'table.csv','account':accounts['SYN-OWN-A']}
    before=client.get(base+'/report',params=params).json()
    assert before['peer_account'] is None and before['peer_selected_account'] is None
    assert [r['amount'] for r in before['top_in']]==['30.03','10.01']
    assert [r['amount'] for r in before['top_out']]==['40.04','20.02']
    changed_response=client.get(base+'/report',params={**params,'peer_account':accounts['SYN-OWN-B']})
    assert changed_response.status_code==200,changed_response.text
    changed=changed_response.json()
    for key in ('summary','overview','accounts','timeline','periods','relations'):
        assert changed[key]==before[key]
    assert changed['top_in'][0]['amount']=='30.03' and changed['top_out'][0]['amount']=='40.04'
    assert len(changed['top_in'])==len(changed['top_out'])==1
    assert changed['peer_selected_account']['account_number']=='SYN-OWN-B'
    drill=client.get(base+'/report/records',params={'member_path':'table.csv','expected_revision':changed['analysis_revision'],
        'account':accounts['SYN-OWN-B'],'direction':'in','counterparty':changed['top_in'][0]['account']})
    assert drill.status_code==200 and drill.json()['total']==1
    assert drill.json()['items'][0]['preview']['amount']=='30.03'
    assert client.get(base+'/report',params={**params,'peer_account':'SYN-NOT-IN-FILE'}).status_code==404
    document=client.get(base+'/report/document',params={**params,'peer_account':accounts['SYN-OWN-B'],
        'timeline_grain':'month','expected_revision':changed['analysis_revision']})
    assert document.status_code==200,document.text
    assert '主要往来范围：SYN-OWN-B' in document.text and '粒度：月' in document.text
    assert '<p>全局统计范围（总述、时间变化、关联明细）：合成甲登记户名（SYN-OWN-A）。</p>' in document.text
    assert '<caption>全文件当前币种账户</caption>' in document.text
    assert '<p>所选交易2笔；进账1笔，10.01元；出账1笔，20.02元。</p>' in document.text
    assert '合成乙登记户名' in document.text
    foreign=make_case('Synthetic other case for local filter')
    assert client.get(f'/api/v1/cases/{foreign}/imports/{initial["source_id"]}/report',params={**params,'peer_account':accounts['SYN-OWN-B'],'timeline_grain':'year'}).status_code==404
    from app import analysis_snapshot
    with database.transaction():analysis_snapshot.invalidate(initial['case_id'])
    assert client.get(base+'/report',params={**params,'peer_account':accounts['SYN-OWN-B'],
        'timeline_grain':'month','timeline_page':2,'expected_revision':changed['analysis_revision']}).status_code==409


def test_report_method_paragraphs_keep_source_cohort_and_selected_scope_distinct(client,make_case):
    from html import escape
    rows=[row('METHOD-A','收入','10.01',own='000SYN-METHOD-A'),
          row('METHOD-B','支出','20.02',own='000SYN-METHOD-B',name='合成另一户名'),
          row('METHOD-BAD','收入','30.03',stamp='synthetic invalid date')]
    base,_=complete(client,make_case('Synthetic readable methods'),payload(rows))
    full=client.get(base+'/report',params={'member_path':'table.csv'}).json()
    assert [p['title'] for p in full['method_paragraphs']]==['资料范围','归集方法','结果说明','留存复核']
    assert '来源记录3条' in full['method_paragraphs'][0]['text']
    assert '1条记录尚待核对，未计入资金统计' in full['method_paragraphs'][0]['text']
    assert '纳入当前范围的交易共2笔' in full['method_paragraphs'][0]['text']
    selected=next(r['account'] for r in full['accounts']['items'] if r['account_number']=='000SYN-METHOD-A')
    params={'member_path':'table.csv','account':selected,'expected_revision':full['analysis_revision']}
    response=client.get(base+'/report',params=params)
    assert response.status_code==200,response.text
    data=response.json()
    assert data['quality']['selected_transactions']==2 and data['summary']['transaction_count']==1
    assert '来源记录3条' in data['method_paragraphs'][0]['text']
    assert '纳入当前范围的交易共1笔' in data['method_paragraphs'][0]['text']
    assert '000SYN-METHOD-A' in data['method_paragraphs'][0]['text']
    assert '账户收支表仍列示资料中的同币种账户' in data['method_paragraphs'][0]['text']
    assert data['accounts']['total']==2
    assert '不重复计数' in data['method_paragraphs'][1]['text']
    assert '户名数量按记载名称去重，不作为主体数量认定' in data['method_paragraphs'][1]['text']
    assert '统计数量不因展示省略而减少' in data['method_paragraphs'][1]['text']
    assert '不补充推定时区' in data['method_paragraphs'][2]['text']
    assert '不代替对交易真实性的核实' in data['method_paragraphs'][3]['text']
    assert '检材编号、原件信息与SHA-256校验值列于“资料与核验”' in data['method_paragraphs'][3]['text']
    assert '复核通过' not in data['method_paragraphs'][3]['text']
    document=client.get(base+'/report/document',params=params)
    assert document.status_code==200,document.text
    for paragraph in data['method_paragraphs']:
        assert '<strong>'+escape(paragraph['title'])+'。</strong>'+escape(paragraph['text']) in document.text
    visible=document.text.split('<script type="application/json"')[0]
    assert '来源与处理记录' not in visible and '<details>' not in visible
    for technical in (data['policy'],data['analysis_revision'],data['scope']['parser_version']):
        assert technical not in visible
    embedded=json.loads(re.search(r'id="funds-report-data">(.*?)</script>',document.text,re.S)[1])['report']
    assert embedded['method_paragraphs']==data['method_paragraphs']
    assert embedded['notes']==data['notes']
    for key in ('scope','quality','integrity','analysis_revision','policy'):
        assert embedded[key]==data[key]
    generated=datetime.fromisoformat(embedded['generated_at'])
    assert generated.utcoffset()==timedelta(0)
    assert generated>=datetime.fromisoformat(data['generated_at'])


def test_trend_calendar_scope_search_and_month_records_share_exact_source(client,make_case):
    rows=[row('T-DEC','收入','100000.01',stamp='2025-12-31 23:59:00',own='SYN-A1',name='合成甲公司'),
          row('T-JAN','支出','20000.02',stamp='2026-01-01 00:00:00',own='SYN-A1',name='合成甲公司'),
          row('T-FEB-IN','收入','30000.03',stamp='2026-02-01 00:00:00',own='SYN-A2',name='合成甲公司'),
          row('T-FEB-OUT','支出','4000.04',stamp='2026-02-28 23:59:59',own='SYN-A2',name='合成甲公司'),
          row('T-MAR','收入','50000.05',stamp='2026-03-01 00:00:00',own='SYN-B1',name='合成乙公司'),
          row('T-NOV-OUT','支出','6000.06',stamp='2026-11-13 00:00:00',own='SYN-B1',name='合成乙公司'),
          row('T-NOV-IN','收入','700.07',stamp='2026-11-14 00:00:00',own='SYN-C1',name=''),
          row('T-BAD','收入','100.00',stamp='synthetic invalid date',own='SYN-A2',name='合成甲公司')]
    cid=make_case('Synthetic combined trend');base,_=complete(client,cid,payload(rows))
    full=client.get(base+'/report',params={'member_path':'table.csv'}).json();revision=full['analysis_revision']
    assert full['timeline_grain']=='月' and full['timeline_mode']=='auto'
    assert full['timeline_pagination']=={'total':12,'page':1,'page_size':120}
    assert len(full['timeline'])==12
    gaps=[b for b in full['timeline'] if not b['has_records']]
    assert [b['start'][5:7] for b in gaps]==['04','05','06','07','08','09','10']
    assert all(b['in_amount'] is None and b['out_amount'] is None and b['net_amount'] is None for b in gaps)
    months={b['start'][:7]:b for b in full['timeline'] if b['has_records']}
    assert months['2026-02']['in_amount']=='30000.03' and months['2026-02']['out_amount']=='4000.04'
    assert months['2026-02']['in_count']==months['2026-02']['out_count']==1
    scope=full['timeline_scope']
    assert scope['summary']['in_amount']=='180700.16' and scope['summary']['out_amount']=='30000.12'
    assert scope['account_count']==scope['available_account_count']==4
    options=client.get(base+'/report/accounts',params={'member_path':'table.csv','expected_revision':revision,'currency':'CNY','search':'合成甲'}).json()
    assert options['total']==2 and {a['account_number'] for a in options['items']}=={'SYN-A1','SYN-A2'}
    ids=[a['account'] for a in options['items']]
    selected={'member_path':'table.csv','expected_revision':revision,'timeline_scope':'selected','timeline_accounts':ids}
    narrowed=client.get(base+'/report',params=selected).json()
    assert narrowed['timeline_scope']['summary']['in_amount']=='130000.04'
    assert narrowed['timeline_scope']['summary']['out_amount']=='24000.06'
    for key in ('summary','overview','accounts','top_in','top_out','relations','periods'):
        assert narrowed[key]==full[key]
    searched=client.get(base+'/report',params={'member_path':'table.csv','timeline_search':'合成甲'}).json()
    assert searched['timeline_scope']['summary']==narrowed['timeline_scope']['summary']
    records=client.get(base+'/report/records',params={**selected,'timeline_search':'合成甲','time_start':'2026-02-01 00:00:00','time_end':'2026-03-01 00:00:00','time_basis':'unqualified','direction':'out','currency':'CNY'})
    assert records.status_code==200,records.text
    assert records.json()['total']==1 and records.json()['items'][0]['preview']['amount']=='4000.04'
    assert records.json()['items'][0]['preview']['trade_no']=='T-FEB-OUT'
    empty=client.get(base+'/report',params={'member_path':'table.csv','timeline_scope':'selected'}).json()
    assert empty['timeline_scope']['account_count']==0 and empty['timeline']==[] and empty['timeline_scope']['summary']['in_count']==0
    excluded=client.get(base+'/report',params={**selected,'timeline_scope':'except'}).json()
    assert excluded['timeline_scope']['summary']['in_amount']=='50700.12'
    document=client.get(base+'/report/document',params=selected)
    assert document.status_code==200
    embedded=json.loads(re.search(r'id="funds-report-data">(.*?)</script>',document.text,re.S)[1])['report']
    assert embedded['timeline_scope']==narrowed['timeline_scope']
    assert client.get(base+'/report',params={**selected,'timeline_accounts':['SYN-foreign']}).status_code==404
    assert client.get(base+'/report/accounts',params={'member_path':'table.csv','currency':'CNY','expected_revision':'0'*32}).status_code==409
    foreign=make_case('Synthetic foreign trend')
    assert client.get(base.replace(f'/cases/{cid}/',f'/cases/{foreign}/')+'/report/accounts',params={'member_path':'table.csv','currency':'CNY','expected_revision':revision}).status_code==404
    for patch in ({'time_start':'2026-02-01'}, {'time_start':'2026-02-01','time_end':'2026-02-01','time_basis':'unqualified'}, {'time_start':'2026-02-01','time_end':'2026-03-01','time_basis':'utc'}):
        assert client.get(base+'/report/records',params={**selected,**patch}).status_code==422


def test_trend_options_search_card_alias_and_page_without_duplicating_money(client,make_case):
    from test_bank_posting_pairs import entries,encoded
    template=entries()[1]
    values=[{**template,'交易账号':f'SYN-FILTER-{i:03}','交易卡号':f'SYN-CARD-{i:03}',
        '交易方户名':'合成同名公司','交易流水号':f'SYN-F-{i}','交易时间':f'2026-11-13 09:{i:02}:00',
        '交易金额':'0.01','交易对手账卡号':'SYN-PEER'} for i in range(25)]
    base,_=complete(client,make_case('Synthetic paged filter aliases'),encoded(values))
    member='table.csv'
    report=client.get(base+'/report',params={'member_path':member}).json();revision=report['analysis_revision']
    first=client.get(base+'/report/accounts',params={'member_path':member,'currency':'CNY','expected_revision':revision,'search':'合成同名','page':1}).json()
    second=client.get(base+'/report/accounts',params={'member_path':member,'currency':'CNY','expected_revision':revision,'search':'合成同名','page':2}).json()
    assert first['total']==second['total']==25 and len(first['items'])==20 and len(second['items'])==5
    card=client.get(base+'/report/accounts',params={'member_path':member,'currency':'CNY','expected_revision':revision,'search':'SYN-CARD-024'}).json()
    assert card['total']==1 and card['items'][0]['account_number']=='SYN-FILTER-024'
    selected=client.get(base+'/report',params={'member_path':member,'timeline_search':'SYN-CARD-024'}).json()
    assert selected['timeline_scope']['account_count']==1
    assert selected['timeline_scope']['summary']['out_amount']=='0.01' and selected['timeline_scope']['summary']['in_amount']=='0.00'


def test_foreign_currency_brief_labels_all_amount_sections(client,make_case):
    base,_=complete(client,make_case('Synthetic foreign trend brief'),payload([row('SYN-USD-BRIEF','支出','19.99',currency='USD')]))
    report=client.get(base+'/report',params={'member_path':'table.csv','currency':'USD'}).json()
    document=client.get(base+'/report/document',params={'member_path':'table.csv','currency':'USD','expected_revision':report['analysis_revision']})
    assert document.status_code==200
    human=document.text.split('<script type="application/json"')[0]
    assert '出账1笔，19.99 USD' in human.split('<h2>账户收支</h2>')[0]
    assert human.count('<th>出账金额（USD）</th>')==3
    assert human.count('<th>金额（USD）</th>')==2
    section=human.split('<h2>时间变化</h2>')[1].split('<h2>关联明细</h2>')[0]
    assert '19.99 USD' in section and '19.99元' not in human


def test_unknown_counterparty_is_drillable_but_does_not_rank_as_one_peer(client,make_case):
    known=[row(f'SYN-RANK-{i}','收入',str(i),peer=f'SYN-P-{i:02}') for i in range(1,11)]
    records=known+[row('SYN-MISSING-IN','收入','1000.00',peer=''),
                   row('SYN-MISSING-OUT','支出','2000.00',peer=''),
                   row('SYN-RANK-OUT','支出','20.00',peer='SYN-P-01')]
    base,_=complete(client,make_case('Synthetic peer identifiers'),payload(records))
    scope={'member_path':'table.csv'}
    response=client.get(base+'/report',params=scope)
    assert response.status_code==200
    data=response.json()
    assert data['summary']['transaction_count']==13
    assert (data['summary']['in_amount'],data['summary']['out_amount'])==('1055.00','2020.00')
    unknown=next(relation for relation in data['relations']['items'] if relation['counterparty']=='')
    assert (unknown['in_amount'],unknown['out_amount'])==('1000.00','2000.00')
    drill=client.get(base+'/report/records',params={**scope,'expected_revision':data['analysis_revision'],
                     'account':unknown['account'],'counterparty':'','direction':'in'})
    assert drill.status_code==200 and drill.json()['total']==1
    assert drill.json()['items'][0]['source_row']==12
    assert [peer['account_number'] for peer in data['top_in']]==[f'SYN-P-{i:02}' for i in range(10,0,-1)]
    assert [peer['account_number'] for peer in data['top_out']]==['SYN-P-01']


def test_trend_card_search_beyond_display_alias_limit_keeps_whole_account_scope(client,make_case):
    from test_bank_posting_pairs import entries,encoded
    template=entries()[1]
    rows=[{**template,'交易账号':'SYN-SAME-ALIAS-ACCOUNT','交易卡号':f'SYN-ALIAS-{i:03}',
        '交易流水号':f'SYN-ALIAS-FLOW-{i}','交易时间':f'2026-11-13 09:{i:02}:00',
        '交易金额':'0.01','交易对手账卡号':'SYN-PEER'} for i in range(25)]
    base,_=complete(client,make_case('Synthetic full card alias lookup'),encoded(rows))
    full=client.get(base+'/report',params={'member_path':'table.csv'}).json()
    assert full['accounts']['total']==1 and len(full['accounts']['items'][0]['cards'])==20
    options=client.get(base+'/report/accounts',params={'member_path':'table.csv','expected_revision':full['analysis_revision'],'currency':'CNY','search':'SYN-ALIAS-024'}).json()
    assert options['total']==1 and options['items'][0]['account']==full['accounts']['items'][0]['account']
    selected=client.get(base+'/report',params={'member_path':'table.csv','timeline_search':'SYN-ALIAS-024'}).json()
    assert selected['timeline_scope']['account_count']==1 and selected['timeline_scope']['summary']['out_amount']=='0.25'
    assert selected['timeline_scope']['summary']['out_count']==25
