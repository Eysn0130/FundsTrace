"""Known optional feedback metadata never changes independent reference roles."""
import csv
import io

import pytest

from app import table_import
from test_reference_roles import payload
from test_automatic_table_import import preview
from test_feedback_import_api import complete


def variant(kind, *, extra=(), changes=None):
    rows = list(csv.reader(io.StringIO(payload(extra=extra, changes=changes).decode('gb18030'))))
    if kind == 'empty-note':
        rows[0].append('附加说明\t'); rows[1].append('')
    elif kind == 'no-ip':
        index = next(i for i, h in enumerate(rows[0]) if h.strip() == 'IP地址')
        rows = [row[:index] + row[index + 1:] for row in rows]
    elif kind == 'reordered':
        rows = [list(reversed(row)) for row in rows]
    elif kind == 'empty-extra':
        rows = [row + ['计算机导出自定义列' if index == 0 else ''] for index, row in enumerate(rows)]
    elif kind == 'normalized-main':
        rows[0] = ['交易 流水号\t' if header.strip() == '交易流水号' else header for header in rows[0]]
    elif kind == 'normalized-voucher':
        rows[0] = ['凭 证号\t' if header.strip() == '凭证号' else header for header in rows[0]]
    out = io.StringIO(newline=''); csv.writer(out).writerows(rows)
    return out.getvalue().encode('gb18030')


@pytest.mark.parametrize('kind', ['empty-note', 'no-ip', 'reordered', 'empty-extra', 'normalized-main', 'normalized-voucher'])
def test_public_optional_metadata_keeps_primary_voucher_and_original(client, make_case, kind):
    cid = make_case('Synthetic optional feedback metadata')
    content = variant(kind)
    view = preview(client, cid, content)
    assert view['table_summary']['accepted'] == 1
    job = complete(client, cid, view)
    assert job['stage'] == 'done' and job['result']['ok'] == 1
    rows = client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations'}).json()['items']
    assert len(rows) == 1 and rows[0]['trade_no'] == 'SYN-PRIMARY' and rows[0]['amount'] == '12.34'
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content


@pytest.mark.parametrize('kind', ['empty-note', 'no-ip'])
@pytest.mark.parametrize('extra', [
    (('交易流水号', 'SYN-CONFLICT'),), (('凭证号', 'SYN-CONFLICT'),),
    (('交易金额', '99.00'),), (('收付标志', '入'),), (('交易账号', 'SYN-OTHER'),),
    (('交易 流水号', 'SYN-CONFLICT'),), (('凭 证号', 'SYN-CONFLICT'),),
])
def test_optional_metadata_does_not_discharge_real_conflicts(client, make_case, kind, extra):
    cid = make_case('Synthetic optional true conflict')
    view = preview(client, cid, variant(kind, extra=extra))
    assert (view['table_summary']['accepted'], view['table_summary']['unresolved']) == (0, 1)
    assert complete(client, cid, view)['stage'] == 'done'
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0


@pytest.mark.parametrize('kind', ['empty-note', 'no-ip'])
def test_optional_contract_does_not_reinterpret_historical_profile(kind):
    historical = table_import.parse(variant(kind), suffix='.csv', profile=table_import.REFERENCE_ROLE_PROFILE)
    assert not historical['rows']
    assert historical['records'][0]['reason_code'] == 'conflicting_field'


@pytest.mark.parametrize('extra', [(('附加说明', 'nonempty unrecognized instruction'),),
                                   (('未知凭证编号', 'SYN-OTHER'),)])
def test_unknown_or_nonempty_additional_fields_do_not_gain_reference_authority(extra):
    result = table_import.parse(payload(extra=extra), suffix='.csv')
    assert not result['rows']
    assert result['stats']['unresolved'] == 1


@pytest.mark.parametrize('replacement', ['凭证号', '商户名称'])
def test_current_contract_rejects_replacing_primary_reference_through_public_repreview(client, make_case, replacement):
    cid = make_case('Synthetic closed current reference mapping')
    content = payload(changes={'商户名称': 'SYN-UNRELATED-MERCHANT'})
    view = preview(client, cid, content)
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        'mode': view['mode'], 'mapping': {**view['mapping'], 'trade_no': replacement}})
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_table_file'
    assert client.get(f'/api/v1/cases/{cid}/stats').json()['flows'] == 0
    fresh = preview(client, cid, content)
    assert complete(client, cid, fresh)['stage'] == 'done'
    row = client.get(f'/api/v1/cases/{cid}/flows').json()['items'][0]
    assert row['trade_no'] == 'SYN-PRIMARY' and row['amount'] == '12.34'


@pytest.mark.parametrize('kind', ['normalized-main', 'normalized-voucher'])
@pytest.mark.parametrize('change_summary', [False, True])
def test_normalized_reference_roles_survive_explicit_public_repreview(client, make_case, kind, change_summary):
    cid = make_case('Synthetic normalized reference repreview')
    content = variant(kind)
    first = preview(client, cid, content)
    mapping = {**first['mapping'], 'summary': '商户名称'} if change_summary else first['mapping']
    response = client.post(f"/api/v1/cases/{cid}/imports/{first['import_id']}/preview", json={
        'mode': first['mode'], 'mapping': mapping})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view['table_summary']['accepted'] == 1
    assert view['mapping']['trade_no'] == first['mapping']['trade_no']
    assert complete(client, cid, view)['stage'] == 'done'
    rows = client.get(f'/api/v1/cases/{cid}/flows').json()['items']
    assert len(rows) == 1 and rows[0]['trade_no'] == 'SYN-PRIMARY' and rows[0]['amount'] == '12.34'
    assert client.get(f"/api/v1/cases/{cid}/sources/{first['import_id']}/file").content == content


@pytest.mark.parametrize('extra', [(('交易 流水号', 'SYN-CONFLICT'),), (('凭 证号', 'SYN-CONFLICT'),)])
def test_normalized_public_repreview_keeps_conflicting_role_validators(client, make_case, extra):
    cid = make_case('Synthetic normalized repreview conflict')
    view = preview(client, cid, variant('normalized-main', extra=extra))
    assert (view['table_summary']['accepted'], view['table_summary']['unresolved']) == (0, 1)
    response = client.post(f"/api/v1/cases/{cid}/imports/{view['import_id']}/preview", json={
        'mode': view['mode'], 'mapping': view['mapping']})
    assert response.status_code == 200, response.text
    again = response.json()
    assert (again['table_summary']['accepted'], again['table_summary']['unresolved']) == (0, 1)
    assert complete(client, cid, again)['stage'] == 'done'
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0
