"""Region-local header reuse preserves source order and row declarations."""
import csv
from decimal import Decimal
import io
import json

from openpyxl import Workbook
import pytest

from app import event_resolution, feedback_import, table_import


def payload(sheets, suffix):
    if suffix == '.csv':
        text = io.StringIO(newline='')
        csv.writer(text).writerows(sheets[0])
        return text.getvalue().encode('utf-8-sig')
    book = Workbook()
    book.remove(book.active)
    for index, rows in enumerate(sheets):
        sheet = book.create_sheet(f'Synthetic {index}')
        for row in rows:
            sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def event_regions():
    headers = ['date', 'payer_account', 'payee_account', 'amount', '参考号机构',
               '参考号渠道', '参考号业务域', '跨账簿事件参考号', '记账顺序号']
    row = ['2026-01-01 10:00:00.123456', 'SYN-A', 'SYN-B', '1.23',
           'Synthetic issuer', 'Synthetic channel', 'Synthetic scope', 'SYN-E1', '1']
    return [headers, row, list(reversed(headers)), list(reversed(row)), headers, row]


@pytest.mark.parametrize('suffix', ['.csv', '.xlsx'])
@pytest.mark.parametrize('profile', [table_import.LEGACY_EVIDENCE_PROFILE, table_import.EVIDENCE_PROFILE])
def test_region_reorder_and_return_preserve_event_scope_and_positions(suffix, profile):
    regions = event_regions()
    sheets = [regions] if suffix == '.csv' else [regions, [regions[2], regions[3]]]
    plan = table_import.parse(payload(sheets, suffix), suffix=suffix, profile=profile)
    assert [doc['header_rows'] for doc in plan['documents']] == ([[1, 3, 5]] if suffix == '.csv' else [[1, 3, 5], [1]])
    assert [(row['payer_account'], row['payee_account'], row['amount'], row['trade_time'].microsecond)
            for row in plan['rows']] == [('SYN-A', 'SYN-B', Decimal('1.23'), 123456)] * (3 if suffix == '.csv' else 4)
    accepted = [r for r in plan['records'] if r['disposition'] == 'accepted']
    assert [r['source_row'] for r in accepted] == ([2, 4, 6] if suffix == '.csv' else [2, 4, 6, 2])
    if profile == table_import.EVIDENCE_PROFILE:
        assert [json.loads(r['details'][event_resolution.LEDGER_FIELD])['posting_scope'] for r in accepted] == (['1', '3', '5'] if suffix == '.csv' else ['1', '3', '5', '1'])
        assert all(json.loads(r['details'][event_resolution.DETAIL_FIELD])['reference'] == 'SYN-E1' for r in accepted)
    else:
        assert all(event_resolution.DETAIL_FIELD not in r['details'] for r in accepted)


@pytest.mark.parametrize('profile', [table_import.EVIDENCE_ARCHIVE_PROFILE, table_import.MICROSECOND_ARCHIVE_PROFILE])
def test_feedback_region_reorder_keeps_original_text_and_each_row_time_policy(profile):
    headers = list(feedback_import.COLUMNS)
    values = {'交易账号': 'SYN-OWN', '交易对手账卡号': 'SYN-OTHER',
              '交易时间': '2026-01-01 10:00:00', '交易金额': '10.03', '交易余额': '',
              '收付标志': '出', '交易币种': 'CNY', '交易是否成功': '1', '交易流水号': 'SYN-ONE'}
    normal = [values.get(h, '') for h in headers]
    fractional = [values.get(h, '') if h != '交易时间' else '2026-01-01 10:00:01.123456' for h in headers]
    rows = [[' \t' + h + ' ' for h in headers], normal, list(reversed(headers)), list(reversed(fractional)), headers, normal]
    plan = table_import.parse(payload([rows], '.csv'), suffix='.csv', profile=profile,
                              reference_policy=table_import.TRANSACTION_REFERENCE_POLICY)
    assert plan['documents'][0]['header_rows'] == [1, 3, 5]
    assert [r['source_row'] for r in plan['records']] == [2, 3, 4, 5, 6]
    expected = [2, 4, 6] if profile == table_import.MICROSECOND_ARCHIVE_PROFILE else [2, 6]
    assert [r['source_row'] for r in plan['records'] if r['disposition'] == 'accepted'] == expected
    assert all(row['amount'] == Decimal('10.03') and row['balance'] is None for row in plan['rows'])
    assert [row['trade_time'].microsecond for row in plan['rows']] == ([0, 123456, 0] if len(expected) == 3 else [0, 0])


def test_duplicate_event_header_does_not_gain_authority_after_region_switch():
    headers, row, *_ = event_regions()
    duplicate = headers + ['跨账簿事件参考号']
    plan = table_import.parse(payload([[headers, row, duplicate, row + ['SYN-E1'], headers, row]], '.csv'),
                              suffix='.csv', profile=table_import.EVIDENCE_PROFILE)
    accepted = [r for r in plan['records'] if r['disposition'] == 'accepted']
    assert [r['source_row'] for r in accepted] == [2, 4, 6]
    assert [event_resolution.DETAIL_FIELD in r['details'] for r in accepted] == [True, False, True]
