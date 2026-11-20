"""SYN data only. Expected coordinates come from the literal fixture."""
import io

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
    # Shape the entire comparison so the baseline displays errors for both
    # regions and the repeated-header row rather than hiding a later mismatch.
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


def test_normalized_preview_reports_and_canvas_link_to_exact_physical_rows(client, make_case):
    cid = make_case('Synthetic source linkbacks and report funds')
    case = f'/api/v1/cases/{cid}'
    data = workbook(REGIONS)
    member = 'worksheets/0001'
    base, view, job = complete(client, cid, data, 'coordinates.xlsx')
    source = view['import_id']
    assert [(row['source_member'], row['source_row'], row['source_record_no'], row['amount'], row['trade_no'], row['summary'])
            for row in view['preview']] == [
        (member, 2, 1, '1.23', 'SYN-GENERIC-REFERENCE', 'SYN-GENERIC-SUMMARY'),
        (member, 4, 3, '2.34', 'SYN-NATIVE-PRIMARY', 'SYN-NATIVE-SUMMARY'),
    ]
    expected_journal = [(1, 2, 'transaction_valid'), (2, 3, 'repeated_header'), (3, 4, 'transaction_valid')]
    raw_page = get_json(client, base + '/records', member_path=member, include_preview=True)
    assert [(row['record_no'], row['source_row'], row['reason_code']) for row in raw_page['items']] == expected_journal
    raw_report = get_json(client, f'{case}/sources/{source}/report')
    assert [(row['record_no'], row['source_row'], row['reason_code']) for row in raw_report['items']] == expected_journal
    assert all('original' not in row for row in raw_report['items'])
    accepted = [row for row in raw_page['items'] if row['flow_id']]
    for row in accepted:
        linked, = get_json(client, f'{case}/flows/{row["flow_id"]}/sources')['items']
        assert (linked['source_id'], linked['member_path'], linked['source_row'], linked['member_row']) == (
            source, member, row['record_no'], row['source_row'])
    report = get_json(client, base + '/report', member_path=member, currency='CNY')
    # The generic row has no source-declared self. The native row declares a
    # self account, so this is an own-ledger report with one unattributed row.
    assert report['perspective'] == 'own_ledger'
    assert (report['quality']['selected_transactions'], report['quality']['unattributed_transactions']) == (2, 1)
    assert (report['summary']['transaction_count'], report['summary']['amount']) == (1, '2.34')
    drill = get_json(client, base + '/report/records', member_path=member, currency='CNY',
                     expected_revision=report['analysis_revision'])
    assert [(row['record_no'], row['source_row'], row['preview']['amount']) for row in drill['items']] == [
        (3, 4, '2.34')]
    track = client.post('/api/track', json={'case_id': cid, 'flow_ids': [row['flow_id'] for row in accepted]})
    assert track.status_code == 200, track.text
    graph = get_json(client, '/api/graph', case_id=cid)
    assert sorted(edge['total'] for edge in graph['edges']) == ['1.23', '2.34']
    assert graph['transaction_count'] == 2
    inspection = client.post('/api/canvas/investigation/time', json={'case_id': cid})
    assert inspection.status_code == 200, inspection.text
    observed_links = [(flow['amount'], item['member_path'], item['source_row'], item['member_row'])
                      for flow in inspection.json()['flows'] for original in flow['provenance']
                      for item in original['sources']]
    assert observed_links == [('1.23', member, '1', '2'), ('2.34', member, '3', '4')]
    page = get_json(client, base + '/review', member_path=member)
    assert [(row['record_no'], row['source_row'], row['reason_code']) for row in page['items']] == expected_journal
    focused = get_json(client, base + '/review', member_path=member, source_row=4, page_size=1)
    assert focused['page'] == 3 and focused['items'][0]['source_row'] == 4
    assert client.get(base + '/review', params={'member_path': member, 'source_row': 524}).status_code == 404
    assert client.get(f'{case}/sources/{source}/file').content == data
    assert get_json(client, base)['result'] == job['result']


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
