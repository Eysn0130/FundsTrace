"""Closed source statements are evidence, never arbitrary negative-value guesses."""
import csv
import io
from decimal import Decimal
import pytest
from app import table_import, table_sign_declarations as declarations, case_packages, evidence_resolution
from test_automatic_table_import import preview
from test_feedback_import_api import complete

STATEMENT='本文件交易金额负值表示本方支出，绝对值为元；正值表示本方收入。'
HEAD=['交易时间','本方账号','对方账号','交易金额','收付标志','备注']
def raw(rows,headers=HEAD):
    out=io.StringIO(newline='');csv.writer(out).writerows([headers,*rows]);return out.getvalue().encode('utf-8-sig')
def row(amount,note,direction='',account='SYN-A'):
    return ['2026-01-01 10:00:00.123456',account,'SYN-B',amount,direction,note]
def parse(rows,headers=HEAD,**kw):return table_import.parse(raw(rows,headers),suffix='.csv',**kw)

@pytest.mark.parametrize('amount,payer,payee',[('-12.34','SYN-A','SYN-B'),('12.34','SYN-B','SYN-A')])
def test_positive_and_negative_values_follow_complete_source_statement(amount,payer,payee):
    plan=parse([row(amount,STATEMENT)]);assert len(plan['rows'])==1
    assert (plan['rows'][0]['amount'],plan['rows'][0]['payer_account'],plan['rows'][0]['payee_account'])==(Decimal('12.34'),payer,payee)
    note=plan['records'][0]['details'][declarations.DETAIL_FIELD];assert declarations.validate_proof(note)
    assert STATEMENT in note

@pytest.mark.parametrize('note',[
    '并非'+STATEMENT,'假设'+STATEMENT,'“'+STATEMENT+'”',STATEMENT.replace('本文件',''),
    '本文件负值表示本方支出。','本文件交易金额负值表示本方支出。',STATEMENT+'以上只是例子。'])
def test_denial_quote_condition_and_unbound_scope_never_supply_direction(note):
    assert parse([row('-12.34',note)])['rows']==[]

@pytest.mark.parametrize('unit,value,expected',[('元','-12.34','12.34'),('分','-1234','12.34'),('万元','-0.001234','12.34')])
def test_default_unit_is_not_an_explicit_yuan_claim(unit,value,expected):
    plan=parse([row(value,STATEMENT.replace('为元','为'+unit))]);assert len(plan['rows'])==1
    assert plan['rows'][0]['amount']==Decimal(expected)

def test_order_reverse_semantics_and_repeated_late_statements():
    reverse='本表交易金额正数表示本方支出，绝对值为元；负数表示本方收入。'
    plan=parse([row('-1.00',''),row('2.00',reverse),row('3.00',reverse)])
    assert [(x['payer_account'],x['payee_account'],x['amount']) for x in plan['rows']]==[
        ('SYN-B','SYN-A',Decimal('1')),('SYN-A','SYN-B',Decimal('2')),('SYN-A','SYN-B',Decimal('3'))]
    conflict=parse([row('-1',STATEMENT),row('2',reverse)])
    assert conflict['rows']==[]
    assert parse([row('-1',STATEMENT),row('2','不是'+STATEMENT)])['rows']==[]

def test_unit_direction_owner_and_signed_header_conflicts():
    assert parse([row('-12.34',STATEMENT,'入')])['rows']==[]
    assert parse([row('-12.34',STATEMENT,account='')])['rows']==[]
    assert parse([row('-1234',STATEMENT.replace('为元','为分'))],HEAD[:3]+['交易金额(元)']+HEAD[4:])['rows']==[]
    assert parse([row('-1234',STATEMENT)+['分']],HEAD+['金额单位'])['rows']==[]
    assert parse([row('0',STATEMENT)])['rows']==[]
    assert parse([row('-12.34','')])['rows']==[]

@pytest.mark.parametrize('profile',[table_import.CURRENCY_REGION_PROFILE,table_import.GUARDED_PROFILE,table_import.GUARDED_EVIDENCE_PROFILE])
def test_historical_profiles_do_not_adopt_statement(profile):
    plan=parse([row('-12.34',STATEMENT)],profile=profile);assert plan['rows']==[]
    assert declarations.DETAIL_FIELD not in plan['records'][0]['details']

def test_statement_public_original_and_package_replay(client,database,make_case):
    cid=make_case('Synthetic source sign contract');content=raw([row('-12.34',STATEMENT),row('2.00',STATEMENT)])
    view=preview(client,cid,content);job=complete(client,cid,view);assert job['stage']=='done',job
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content==content
    expected=database.q_raw('select amount,payer_account,payee_account from flows where case_id=? order by amount',[cid])
    assert expected==[{'amount':Decimal('2'),'payer_account':'SYN-B','payee_account':'SYN-A'},{'amount':Decimal('12.34'),'payer_account':'SYN-A','payee_account':'SYN-B'}]
    restored=case_packages.import_package(case_packages.export_package(cid)[0])['case_id']
    assert database.q_raw('select amount,payer_account,payee_account from flows where case_id=? order by amount',[restored])==expected


@pytest.mark.parametrize("invalid_type", [False, True])
@pytest.mark.parametrize("earlier_note", ["", STATEMENT])
def test_cached_formula_and_invalid_typed_cells_cannot_declare_source(monkeypatch, invalid_type, earlier_note):
    rows = [HEAD, row('-12.34', earlier_note), row('2', STATEMENT)]
    rows[2][-1] = table_import._WorkbookValue(STATEMENT, '', not invalid_type, False, invalid_type)
    contracts = table_import._source_sign_contracts([{'path': 'synthetic.xlsx/Sheet1', 'rows': rows}], table_import.PRECISE_PROFILE)
    assert contracts['synthetic.xlsx/Sheet1']['error'] == 'invalid_direction'


def test_twenty_identical_source_statements_keep_public_financial_interpretation(client,database,make_case):
    cid=make_case('Synthetic repeated declaration')
    content=raw([row(str(-value),STATEMENT) for value in range(1,21)])
    view=preview(client,cid,content);job=complete(client,cid,view)
    assert job['stage']=='done',job
    facts=database.q_raw('select amount,payer_account,payee_account from flows where case_id=? order by amount',[cid])
    assert facts==[{'amount':Decimal(value),'payer_account':'SYN-A','payee_account':'SYN-B'} for value in range(1,21)]
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content==content
    records=client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()['items']
    assert len(records)==20
    assert {x['source_row'] for x in records}==set(range(2,22))
    assert all(declarations.validate_proof(x['details'][declarations.DETAIL_FIELD]) for x in records)


def test_plain_file_checks_each_transaction_region_once(monkeypatch):
    calls = {"_later_header": 0, "_overlapping_header": 0}
    def counted(name, original):
        def observe(*args, **kwargs):
            calls[name] += 1
            return original(*args, **kwargs)
        return observe
    for name in calls:
        monkeypatch.setattr(table_import, name, counted(name, getattr(table_import, name)))
    values = [row('12.34', '合成普通备注', '出'), row('0.01', '合成尾行', '入')]
    plan = parse(values)
    assert [(x['amount'], x['payer_account'], x['payee_account']) for x in plan['rows']] == [
        (Decimal('12.34'), 'SYN-A', 'SYN-B'), (Decimal('0.01'), 'SYN-B', 'SYN-A')]
    assert len(plan['records']) == 2
    assert all(declarations.DETAIL_FIELD not in x['details'] for x in plan['records'])
    assert all(count <= len(values) for count in calls.values()), calls


@pytest.mark.parametrize('rows', [
    [[STATEMENT], HEAD, row('12.34', '', '出')],
    [HEAD, row('12.34', '', '出', account=STATEMENT)],
    [[*HEAD, '附加源列'], [*row('12.34', '', '出'), STATEMENT]],
    [HEAD, row('-12.34', STATEMENT), HEAD, row('2.00', '')],
    [HEAD, row('-12.34', STATEMENT), [*HEAD[:3], '交易金额(分)', *HEAD[4:]], row('200', '')],
])
def test_invalid_statement_position_or_later_region_still_blocks_flows(rows):
    out = io.StringIO(newline=''); csv.writer(out).writerows(rows)
    assert table_import.parse(out.getvalue().encode('utf-8-sig'), suffix='.csv')['rows'] == []


@pytest.mark.parametrize('valid', [False, True])
def test_statement_beyond_first_hundred_rows_is_checked_before_any_flow(valid):
    values = [row('1.00', '', '入') for _ in range(149)] + [row('-12.34', '', '出')]
    values.append(row('', STATEMENT if valid else '并非' + STATEMENT))
    plan = parse(values)
    if valid:
        assert len(plan['rows']) == 150
        assert sum(x['amount'] for x in plan['rows']) == Decimal('161.34')
        assert all(declarations.validate_proof(x['details'][declarations.DETAIL_FIELD])
                   for x in plan['records'][:150])
    else:
        assert plan['rows'] == []


@pytest.mark.parametrize('note,metadata,expected', [
    (STATEMENT, False, [('12.34', 'SYN-A', 'SYN-B'), ('2.00', 'SYN-B', 'SYN-A')]),
    (STATEMENT.replace('本文件', '本表'), False, [('2.00', 'SYN-B', 'SYN-A')]),
    ('并非' + STATEMENT, False, []),
    (STATEMENT, True, []),
])
def test_all_sheets_supply_file_claims_and_invalid_claims(note, metadata, expected):
    from openpyxl import Workbook
    book = Workbook(); first = book.active
    first.append(HEAD); first.append(row('-12.34', ''))
    second = book.create_sheet('合成另一表')
    second.append(['合成元数据'] if metadata else HEAD)
    second.append([note] if metadata else row('2.00', note))
    content = io.BytesIO(); book.save(content); book.close()
    plan = table_import.parse(content.getvalue(), suffix='.xlsx')
    assert [(x['amount'], x['payer_account'], x['payee_account']) for x in plan['rows']] == [
        (Decimal(amount), payer, payee) for amount, payer, payee in expected]


def test_unproved_sheet_iterator_and_cell_keep_original_processing():
    sheets = [{'path': 'synthetic.csv', 'rows': [HEAD, row('-12.34', STATEMENT)]}]
    contract = table_import._source_sign_contracts(iter(sheets), table_import.PRECISE_PROFILE)
    assert contract['synthetic.csv']['negative'] == 'out'
    class Unproved:
        def __str__(self):
            raise TypeError('synthetic original cell responsibility')
    with pytest.raises(TypeError, match='synthetic original cell responsibility'):
        table_import._source_sign_contracts([{'path': 'synthetic.csv', 'rows': [[Unproved()]]}], table_import.PRECISE_PROFILE)
    with pytest.raises(KeyError, match='path'):
        table_import._source_sign_contracts([{'rows': [HEAD]}], table_import.PRECISE_PROFILE)
