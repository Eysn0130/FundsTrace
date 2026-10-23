"""Public mixed-table imports preserve exact facts across append and replay."""
import csv
from datetime import datetime, timedelta
import io
from decimal import Decimal

from openpyxl import Workbook
import pytest

from app import case_packages, table_import
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_contract_lifecycle import facts, source_records
from test_table_mapping_coverage import coverage
from test_table_source_roles import ACCOUNT


def encoded(sheets, suffix):
    if suffix == '.csv':
        text = io.StringIO(newline='')
        csv.writer(text).writerows(sheets[0][1])
        return text.getvalue().encode()
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets:
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    data = io.BytesIO()
    book.save(data)
    book.close()
    return data.getvalue()


DATE_HEADER_ROWS = [
    ['date', 'payer_account', 'payee_account', 'amount', 'unused'],
    ['2026-01-01', 'SYN-A', 'SYN-B', '1.23', ''],
    ['2026-01-01', 'date', 'payer_account', 'payee_account', 'amount'],
    ['2026-01-03', '2026-01-02', 'SYN-C', '12345', '2.34'],
]


@pytest.mark.parametrize('suffix', ['.csv', '.xlsx'])
def test_date_named_extra_column_cannot_pin_the_previous_time_coordinate(suffix):
    plan = table_import.parse(encoded([('Synthetic changed time', DATE_HEADER_ROWS)], suffix), suffix=suffix)
    assert [(row['trade_time'].isoformat(), row['payer_account'], row['payee_account'], row['amount'])
            for row in plan['rows']] == [
        ('2026-01-01T00:00:00', 'SYN-A', 'SYN-B', Decimal('1.23')),
        ('2026-01-02T00:00:00', 'SYN-C', '12345', Decimal('2.34')),
    ]
    assert plan['stats']['currency_totals'] == {'CNY': '3.57'}
    assert plan['documents'][0]['header_rows'] == [1, 3]
    assert [record['source_row'] for record in plan['records']] == [2, 3, 4]
    assert plan['records'][1]['reason_code'] == 'repeated_header'


@pytest.mark.parametrize('suffix', ['.csv', '.xlsx'])
def test_valid_transaction_with_complete_header_words_in_extra_cells_remains_data(suffix):
    headers = ['date', 'payer_account', 'payee_account', 'amount', 'extra1', 'extra2', 'extra3', 'extra4']
    rows = [headers,
            ['2026-01-01', 'SYN-A', 'SYN-B', '1.23', 'date', 'payer_account', 'payee_account', 'amount'],
            ['2026-01-02', 'SYN-C', 'SYN-D', '2.34', '', '', '', '']]
    plan = table_import.parse(encoded([('Synthetic values', rows)], suffix), suffix=suffix)
    assert plan['documents'][0]['header_rows'] == [1]
    assert [row['amount'] for row in plan['rows']] == [Decimal('1.23'), Decimal('2.34')]
    assert [record['reason_code'] for record in plan['records']] == ['transaction_valid'] * 2


@pytest.mark.parametrize('suffix', ['.csv', '.xlsx'])
def test_dated_partial_labels_do_not_replace_current_region(suffix):
    rows = [DATE_HEADER_ROWS[0], ['2026-01-02', 'date', 'amount', '', ''], DATE_HEADER_ROWS[1]]
    plan = table_import.parse(encoded([('Synthetic partial values', rows)], suffix), suffix=suffix)
    assert plan['documents'][0]['header_rows'] == [1]
    assert plan['records'][0]['reason_code'] == 'missing_amount'
    assert [(row['payer_account'], row['payee_account'], row['amount']) for row in plan['rows']] == [
        ('SYN-A', 'SYN-B', Decimal('1.23'))]


@pytest.mark.parametrize('profile', ['funds-trace-table-v14', 'funds-trace-table-v15'])
@pytest.mark.parametrize('suffix', ['.csv', '.xlsx'])
def test_previous_profiles_retain_their_original_dated_header_interpretation(profile, suffix):
    plan = table_import.parse(encoded([('Synthetic frozen header', DATE_HEADER_ROWS)], suffix),
                              suffix=suffix, profile=profile)
    assert plan['profile'] == profile
    assert plan['documents'][0]['header_rows'] == [1]
    assert plan['stats']['currency_totals'] == {'CNY': '12346.23'}
    assert [record['reason_code'] for record in plan['records']] == [
        'transaction_valid', 'invalid_amount', 'transaction_valid']


def mixed_workbook(reverse):
    own = ('Synthetic own cents', [
        ['本方账号', '交易日期', '交易时分秒', '交易对方账号', '借贷标志', '交易金额(分)', '币种', '额外说明'],
        ['SYN-OWN', '2026-01-01', '09:00:00', 'SYN-C1', '借', '123', 'CNY', ''],
    ])
    parties = ('Synthetic parties large unit', [
        ['发生金额(万元)', '收款人账号', '付款人账号', '交易日期时间', '币种'],
        ['0.000234', 'SYN-PAYEE', 'SYN-PAYER', '2026-01-02 10:00:00', 'CNY'],
    ])
    usd = ('Synthetic foreign currency', [
        ['currency', 'account', 'transaction time', 'counterparty account', 'credit amount'],
        ['USD', 'SYN-OWN', '2026-01-03 11:00:00', 'SYN-USD', '4.56'],
    ])
    transactions = [parties, own] if reverse else [own, parties]
    return encoded([('Synthetic empty', []), ('Synthetic auxiliary', [ACCOUNT, [''] * len(ACCOUNT)]),
                    *transactions, usd], '.xlsx')


def totals(rows):
    return {currency: sum((row['amount'] for row in rows if row['currency'] == currency), Decimal(0))
            for currency in {row['currency'] for row in rows}}


@pytest.mark.parametrize('reverse', [False, True])
def test_mixed_sheets_append_repreview_full_validation_and_analysis_stay_case_bound(
        client, database, make_case, reverse):
    cid = make_case('Synthetic mixed append')
    workbook = mixed_workbook(reverse)
    first = preview(client, cid, workbook, 'synthetic-mixed.xlsx')
    assert first['automatic'] and first['table_summary']['profile'] == table_import.PROFILE
    assert (first['table_summary']['record_total'], first['table_summary']['accepted'],
            first['table_summary']['auxiliary']) == (4, 3, 1)
    assert first['table_summary']['currency_totals'] == {'CNY': '3.57', 'USD': '4.56'}
    response = client.post(f"/api/v1/cases/{cid}/imports/{first['import_id']}/preview", json={
        'mode': first['mode'], 'mapping': first['mapping']})
    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed['preview'] == first['preview'] and refreshed['table_summary'] == first['table_summary']
    initial = complete(client, cid, refreshed)
    assert initial['stage'] == 'done' and initial['result']['ok'] == 3
    original = facts(database, cid)
    assert totals(original) == {'CNY': Decimal('3.57'), 'USD': Decimal('4.56')}
    assert {(row['payer_account'], row['payee_account'], row['amount']) for row in original} == {
        ('SYN-OWN', 'SYN-C1', Decimal('1.23')),
        ('SYN-PAYER', 'SYN-PAYEE', Decimal('2.34')),
        ('SYN-USD', 'SYN-OWN', Decimal('4.56'))}
    old_records = source_records(client, cid, first['import_id'])
    assert len({row['member_path'] for row in old_records}) == 4
    assert all(row['source_row'] == 2 for row in old_records)

    append_rows = [['amount(分)', 'payee_account', 'date', 'payer_account', 'currency', '交易金额(元)'],
                   ['123', 'SYN-C1', '2026-01-01 09:00:00', 'SYN-OWN', 'CNY', '1.23']]
    # Distinct trades are outside the existing graph mirror window. Display
    # pairing and persisted transaction deduplication have separate contracts.
    append_rows.extend([['345', 'SYN-C1', (datetime(2026, 1, 4, 12) + timedelta(minutes=3 * i)).isoformat(' '),
                         'SYN-OWN', 'CNY', '3.45'] for i in range(103)])
    append_rows.append(['456', 'SYN-REJECTED', '2026-01-05', 'SYN-OWN', 'CNY', '6.78'])
    append_data = encoded([('Synthetic append', append_rows)], '.csv')
    second = preview(client, cid, append_data, 'synthetic-append.csv')
    assert second['automatic'] and (second['table_summary']['accepted'], second['table_summary']['unresolved']) == (104, 1)
    appended = complete(client, cid, second)
    assert appended['stage'] == 'done' and (appended['result']['ok'], appended['result']['rows_dup']) == (103, 1)
    after = facts(database, cid)
    assert len(after) == 106 and totals(after) == {'CNY': Decimal('358.92'), 'USD': Decimal('4.56')}
    assert all(row in after for row in original)
    assert source_records(client, cid, first['import_id']) == old_records
    assert client.get(f"/api/v1/cases/{cid}/imports/{first['import_id']}").json()['result'] == initial['result']
    appended_records = source_records(client, cid, second['import_id'])
    assert appended_records[-1]['source_row'] == 106
    assert appended_records[-1]['reason_code'] == 'conflicting_amount' and appended_records[-1]['flow_id'] is None
    assert appended_records[0]['disposition'] == 'duplicate'
    duplicate_sources = client.get(f"/api/v1/cases/{cid}/flows/{appended_records[0]['flow_id']}/sources").json()['items']
    assert {row['source_id'] for row in duplicate_sources} == {first['import_id'], second['import_id']}
    assert coverage(client, cid)['unresolved_records'] == 1
    for data, name, expected_duplicate in [(workbook, 'repeat.xlsx', 3), (append_data, 'repeat.csv', 104)]:
        replay = complete(client, cid, preview(client, cid, data, name))
        assert (replay['result']['ok'], replay['result']['rows_dup']) == (0, expected_duplicate)
    assert facts(database, cid) == after and coverage(client, cid)['unresolved_records'] == 1

    ids = [row['id'] for row in database.q_raw('select id from flows where case_id=? order by id', [cid])]
    assert client.post('/api/track', json={'case_id': cid, 'flow_ids': ids}).status_code == 200
    graph = client.get('/api/graph', params={'case_id': cid}).json()
    edge = next(row for row in graph['edges'] if (row['source'], row['target']) == ('SYN-OWN', 'SYN-C1'))
    assert (edge['cnt'], edge['total'], edge['amount_totals']) == (
        104, '356.58', [{'currency': 'CNY', 'amount': '356.58'}])

    other = make_case('Synthetic isolated append')
    assert client.get(f"/api/v1/cases/{other}/sources/{second['import_id']}/report").status_code == 404
    isolated = complete(client, other, preview(client, other, append_data))
    assert (isolated['result']['ok'], isolated['result']['rows_dup']) == (104, 0)
    assert facts(database, cid) == after
    restored = case_packages.import_package(case_packages.export_package(cid)[0])['case_id']
    assert facts(database, restored) == after and coverage(client, restored) == coverage(client, cid)
    replay = complete(client, restored, preview(client, restored, append_data))
    assert (replay['result']['ok'], replay['result']['rows_dup']) == (0, 104)
