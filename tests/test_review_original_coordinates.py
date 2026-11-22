"""Public original-cell review follows each synthetic member and physical header."""
import io
import json
import time

import pytest
from openpyxl import Workbook

from test_import_review import complete, encoded


# Both roles share the identical name and coordinate across two header regions.
BANK = (
    '交易卡号 交易账号 交易时间 交易金额 交易余额 收付标志 '
    '交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 '
    '摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 '
    '传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 '
    '凭证种类 凭证号 交易柜员号 备注 交易类型 查询反馈结果原因'
).split()
GENERIC = [
    'date', 'payer_account', 'payee_account', 'amount', 'currency',
    *[f'SYN-unused-{column}' for column in range(6, 24)],
    '凭证号', 'SYN-unused-25', '备注', 'SYN-unused-27', 'SYN-unused-28',
]
GENERIC_VALUES = [
    '2026-01-01 09:00:00', 'SYN-GENERIC-A', 'SYN-GENERIC-B', '1.23', 'CNY',
    *[''] * 18, 'SYN-GENERIC-REFERENCE', '', 'SYN-GENERIC-SUMMARY', '', '',
]
BANK_VALUES = {
    '交易卡号': 'SYN-CARD',
    '交易账号': 'SYN-NATIVE-A',
    '交易时间': '2026-01-02 10:00:00',
    '交易金额': '2.34',
    '交易余额': '97.66',
    '收付标志': '出',
    '交易对手账卡号': 'SYN-NATIVE-B',
    '摘要说明': 'SYN-NATIVE-SUMMARY',
    '交易币种': 'CNY',
    '交易是否成功': '1',
    '交易流水号': 'SYN-NATIVE-PRIMARY',
    '凭证号': 'SYN-NATIVE-VOUCHER',
    '备注': 'SYN-NATIVE-REMARK',
}
REGIONS = [GENERIC, GENERIC_VALUES, BANK, [BANK_VALUES.get(header, '') for header in BANK]]


def workbook(rows):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Synthetic coordinates'
    for row in rows:
        sheet.append(row)
    target = io.BytesIO()
    book.save(target)
    book.close()
    return target.getvalue()


def get_json(client, path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(('suffix', 'member', 'serialize'), [
    ('.csv', 'table.csv', encoded),
    ('.xlsx', 'worksheets/0001', workbook),
])
def test_region_coordinates_own_their_role_in_public_review(client, make_case, suffix, member, serialize):
    cid = make_case('Synthetic region coordinate roles')
    base, view, _job = complete(client, cid, serialize(REGIONS), 'coordinates' + suffix)
    summary = view['table_summary']
    assert summary['documents'][0]['header_rows'] == [1, 3]
    evidence = {(entry['header_row'], entry['column_index'], entry['column'], entry['field'])
                for entry in summary['mapping_evidence'] if entry['column'] in {'凭证号', '备注'}}
    assert evidence == {('1', '24', '凭证号', 'trade_no'), ('1', '26', '备注', 'summary'),
                        ('3', '24', '凭证号', 'voucher_reference'), ('3', '26', '备注', 'remark')}
    actual = {}
    for page_number in (1, 2, 3):
        page = get_json(client, base + '/review', member_path=member, page_size=1, page=page_number)
        assert page['counts'] == {'all': 3, 'review': 0, 'accepted': 2}
        assert page['original_error'] is None
        item, = page['items']
        actual[item['source_row']] = [(cell['column'], cell['header'], cell['value'], cell['fields'])
                                      for cell in item['original'] if cell['column'] in (24, 26)]
    assert actual == {
        2: [(24, '凭证号', 'SYN-GENERIC-REFERENCE', ['trade_no']), (26, '备注', 'SYN-GENERIC-SUMMARY', ['summary'])],
        3: [(24, '凭证号', '凭证号', ['voucher_reference']), (26, '备注', '备注', ['remark'])],
        4: [(24, '凭证号', 'SYN-NATIVE-VOUCHER', ['voucher_reference']), (26, '备注', 'SYN-NATIVE-REMARK', ['remark'])],
    }


def test_repeated_headers_keep_separate_columns_with_same_display_label(client, make_case):
    # Col 5's generated display label collides with the literal header in col 7.
    data = encoded([
        ['date', 'payer_account', 'payee_account', 'amount', '备注', '备注', '备注 [列5]', 'currency'],
        ['2026-01-03', 'SYN-A', 'SYN-B', '3.45', 'SYN-NOTE-5', '', 'SYN-UNMAPPED-7', 'CNY'],
    ])
    cid = make_case('Synthetic colliding display labels')
    base, view, _job = complete(client, cid, data, 'duplicate-headers.csv')
    evidence = [(entry['column_index'], entry['column'], entry['field'])
                for entry in view['table_summary']['mapping_evidence'] if entry['field'] == 'summary']
    assert evidence == [('5', '备注 [列5]', 'summary'), ('6', '备注 [列6]', 'summary')]
    page = get_json(client, base + '/review', member_path='table.csv')
    assert page['original_error'] is None
    item, = page['items']
    assert item['source_row'] == 2
    actual = [(cell['column'], cell['header'], cell['value'], cell['fields'])
              for cell in item['original'] if 5 <= cell['column'] <= 7]
    assert actual == [
        (5, '备注 [列5]', 'SYN-NOTE-5', ['summary']),
        (6, '备注 [列6]', '', ['summary']),
        (7, '备注 [列5]', 'SYN-UNMAPPED-7', []),
    ]


def test_workbook_auxiliary_review_does_not_inherit_another_sheets_mapping(client, make_case):
    cid = make_case('Synthetic unbound auxiliary worksheet')
    book = Workbook()
    sheet = book.active
    sheet.title = 'Synthetic financial'
    sheet.append(GENERIC)
    sheet.append(GENERIC_VALUES)
    aux = book.create_sheet('Synthetic auxiliary')
    aux.append('账号 卡号 账户类别 币种 可用余额 账户余额 账户状态 开户日期 开户网点 销户日期 销户网点 开户网点代码 最后交易时间 钞汇标志 备注'.split())
    aux.append(['SYN-AUX', 'SYN-AUX-CARD', '', 'CNY', '', '', '', '', '', '', '', '', '', '', 'SYN-AUX-REMARK'])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    base, view, _job = complete(client, cid, stream.getvalue(), 'auxiliary-origins.xlsx')
    member = 'worksheets/0002'
    summary = view['table_summary']
    document, = [doc for doc in summary['documents'] if doc['member_path'] == member]
    assert document['mapping'] == {} and document['source_role'] == 'account'
    assert all(item['member_path'] != member for item in summary['mapping_evidence'])
    page = get_json(client, base + '/review', member_path=member)
    assert page['original_error'] is None
    item, = page['items']
    assert (item['source_row'], item['reason_code']) == (2, 'auxiliary_table')
    assert item['original'][14] == {
        'column': 15, 'header': '备注', 'value': 'SYN-AUX-REMARK', 'fields': [], 'kind': 'text'}


def test_historical_feedback_mapping_belongs_to_the_transaction_member(client, make_case, legacy_feedback_reader):
    from test_feedback_import_api import archive, transaction

    cid = make_case('Synthetic legacy member review')
    data = archive([transaction()])
    view = client.post('/api/import/preview', params={'case_id': cid},
                       files={'file': ('members.zip', data)})
    assert view.status_code == 200, view.text
    preview = view.json()
    import_id = preview['import_id']
    reply = client.post('/api/import/commit', json={'case_id': cid, 'import_id': import_id,
                        'mode': preview['mode'], 'mapping': preview['mapping']})
    assert reply.status_code == 200, reply.text
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = get_json(client, '/api/import/progress', import_id=import_id, case_id=cid)
        if job['stage'] in {'done', 'error'}:
            break
        time.sleep(.01)
    assert job['stage'] == 'done', job
    base = f'/api/v1/cases/{cid}/imports/{import_id}/review'
    financial, = get_json(client, base, member_path='synthetic/transactions.csv')['items']
    auxiliary, = get_json(client, base, member_path='synthetic/accounts.csv')['items']
    own = next(cell for cell in financial['original'] if cell['header'] == '交易账号')
    opening = next(cell for cell in auxiliary['original'] if cell['header'] == '交易账号')
    assert (own['value'], own['fields']) == ('SYN-A\t', ['self_account'])
    assert (opening['value'], opening['fields']) == ('SYN-A\t', [])


@pytest.mark.parametrize('partial', [False, True], ids=['name-only', 'partial-coordinate'])
def test_historical_member_evidence_requires_a_unique_name_or_complete_coordinates(client, database, make_case, partial):
    cid = make_case('Synthetic historical name mapping')
    data = encoded([
        ['date', 'payer_account', 'payee_account', 'amount', '备注', '备注', '备注 [列5]'],
        ['2026-01-03', 'SYN-A', 'SYN-B', '3.45', 'SYN-AUX', '', ''],
    ])
    base, view, job = complete(client, cid, data)
    receipt = job['result']
    evidence = [{'member_path': 'table.csv', 'column': 'amount', 'field': 'amount'},
                {'member_path': 'table.csv', 'column': '备注 [列5]', 'field': 'summary'}]
    if partial:
        evidence[0]['header_row'] = '1'
    receipt['table_summary']['mapping_evidence'] = evidence
    database.x('update import_sessions set result_json=? where case_id=? and import_id=?',
               [json.dumps(receipt, ensure_ascii=False), cid, view['import_id']])
    item, = get_json(client, base + '/review', member_path='table.csv')['items']
    cells = {cell['column']: cell['fields'] for cell in item['original']}
    assert (cells[4], cells[5], cells[7]) == ([] if partial else ['amount'], [], [])
