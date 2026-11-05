"""Independent synthetic feedback examples: references have distinct roles."""
import csv
import io
import json
import zipfile

import pytest

from app import table_import, table_archive, case_packages
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete


HEADERS = ('交易卡号 交易账号 交易户名 交易证件号码 交易时间 交易金额 交易余额 收付标志 '
           '交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 '
           '交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 '
           '交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 商户代码 交易类型').split()


def payload(*, extra=(), changes=None, missing_peer=False):
    values = {'交易账号': '000SYN-A', '交易卡号': '000SYN-CARD', '交易户名': 'Synthetic owner',
              '交易时间': '2026-01-01 10:00:00', '交易金额': '12.34', '交易余额': '100.00',
              '收付标志': '出', '交易对手账卡号': '' if missing_peer else '000SYN-B',
              '交易币种': 'CNY', '交易是否成功': '01', '交易流水号': 'SYN-PRIMARY',
              '凭证号': 'SYN-VOUCHER'}
    values.update(changes or {})
    out = io.StringIO(newline='')
    csv.writer(out).writerows([[h + '\t' for h in HEADERS] + [h + '\t' for h, _ in extra],
                              [values.get(h, '') + '\t' for h in HEADERS] + [v + '\t' for _, v in extra]])
    return out.getvalue().encode('gb18030')


def archive(content):
    for path in ('leaf.csv', 'nested.zip', 'outer.zip'):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr(path, content)
        content = out.getvalue()
    return content


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('missing_peer', [False, True])
def test_public_reference_roles_keep_source_and_atomic_support_lifecycle(client, database, make_case, nested, missing_peer):
    cid = make_case('Synthetic independent references')
    content = payload(missing_peer=missing_peer)
    content = archive(content) if nested else content
    name = 'synthetic.zip' if nested else 'synthetic.csv'
    jobs = []
    for attempt in range(2):
        view = preview(client, cid, content, name)
        assert view['table_summary']['accepted'] == 1
        job = complete(client, cid, view)
        assert job['stage'] == 'done', job
        jobs.append(job)
        flows = client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations'}).json()
        assert flows['total'] == 1
        flow = flows['items'][0]
        assert (flow['trade_no'], flow['amount'], flow['currency']) == ('SYN-PRIMARY', '12.34', 'CNY')
        assert (flow.get('raw_payee_account') or flow['payee_account']) == ('' if missing_peer else '000SYN-B')
        sources = client.get(f"/api/v1/cases/{cid}/flows/{flow['id']}/sources").json()
        assert sources['total'] == attempt + 1
        assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == content
        records = client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}/records").json()['items']
        assert len(records) == 1 and records[0]['source_row'] == 2
        assert records[0]['details']['trade_no_basis'] == 'source_transaction_id'
        assert records[0]['details']['reported_status'] == 'unknown'
        refs = json.loads(records[0]['details']['source_field_evidence'])
        assert [(e['role'], e['column_index'], e['raw']) for e in refs] == [
            ('source_transaction_id', 23, 'SYN-PRIMARY\t'),
            ('independent_voucher_reference', 26, 'SYN-VOUCHER\t')]
        evidence = view['table_summary']['mapping_evidence']
        assert {e['basis'] for e in evidence if e['field'] in ('trade_no', 'voucher_reference')} == {
            'source_transaction_id', 'independent_voucher_reference'}
    for index, job in enumerate(jobs):
        response = client.delete(f"/api/batches/{job['result']['batch_id']}", params={'case_id': cid})
        assert response.status_code == 200
        stats = client.get(f'/api/v1/cases/{cid}/stats').json()
        assert stats['flows'] == 1 - index
        assert stats['amount_totals'] == ([{'currency': 'CNY', 'amount': '12.34'}] if index == 0 else [])


@pytest.mark.parametrize('extra,changes', [
    ((('交易流水号', 'SYN-OTHER'),), {}),
    ((('凭证号', 'SYN-OTHER'),), {}),
    ((('交易金额', '99.99'),), {}),
    ((('收付标志', '入'),), {}),
    ((('交易账号', '000SYN-OTHER'),), {}),
])
def test_true_same_role_money_direction_and_identity_conflicts_remain_unresolved(client, make_case, extra, changes):
    cid = make_case('Synthetic true conflicts')
    view = preview(client, cid, payload(extra=extra, changes=changes))
    assert view['table_summary']['accepted'] == 0
    assert view['table_summary']['unresolved'] == 1
    job = complete(client, cid, view)
    assert job['stage'] == 'done'
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0
    examples = view['table_summary']['unresolved_examples']
    evidence = json.loads(examples[0]['details']['source_field_evidence'])
    assert len([e for e in evidence if e['column'] == extra[0][0]]) == 2


def test_old_parser_keeps_reference_conflict():
    plan = table_import.parse(payload(), suffix='.csv', profile=table_import.PRECISE_PROFILE)
    assert not plan['rows']
    assert plan['records'][0]['reason_code'] == 'conflicting_field'


def test_source_reference_excerpts_round_trip_without_original_bytes_in_package(client, make_case):
    cid = make_case('Synthetic reference package')
    view = preview(client, cid, payload())
    assert complete(client, cid, view)['stage'] == 'done'
    restored = case_packages.import_package(case_packages.export_package(cid)[0])['case_id']
    flows = client.get(f'/api/v1/cases/{restored}/flows').json()['items']
    sources = client.get(f"/api/v1/cases/{restored}/flows/{flows[0]['id']}/sources").json()['items']
    assert sources[0]['origin'] == 'package' and not sources[0]['source_available']
    assert [e['raw'] for e in json.loads(sources[0]['record_details']['source_field_evidence'])] == ['SYN-PRIMARY\t', 'SYN-VOUCHER\t']


@pytest.mark.parametrize('nested', [False, True])
def test_explicit_reimport_adds_missing_records_without_rewriting_old_receipt(client, database, make_case, monkeypatch, nested):
    cid = make_case('Synthetic historical correction')
    rows = list(csv.reader(io.StringIO(payload().decode('gb18030'))))
    for index in range(2):
        row = rows[1].copy()
        row[4] = f'2026-01-01 10:00:0{index + 1}\t'
        row[22] = f'SYN-PRIMARY-{index}\t'
        row[25] = row[22] if index == 0 else f'SYN-VOUCHER-{index}\t'
        rows.append(row)
    out = io.StringIO(newline=''); csv.writer(out).writerows(rows)
    content = out.getvalue().encode('gb18030')
    content = archive(content) if nested else content
    name = 'synthetic.zip' if nested else 'synthetic.csv'
    original = imports._parse
    def old_parse(*args, **kwargs):
        kwargs['table_profile'] = table_archive.PRECISE_PROFILE if nested else table_import.PRECISE_PROFILE
        kwargs['_auto_event_profile'] = False
        return original(*args, **kwargs)
    with monkeypatch.context() as old:
        old.setattr(imports, '_parse', old_parse)
        old_view = preview(client, cid, content, name)
        assert old_view['table_summary']['accepted'] == 1
        old_job = complete(client, cid, old_view)
    assert old_job['result']['ok'] == 1
    old_records = client.get(f"/api/v1/cases/{cid}/imports/{old_view['import_id']}/records").json()
    current = preview(client, cid, content, name)
    assert current['table_summary']['accepted'] == 3
    job = complete(client, cid, current)
    assert job['stage'] == 'done' and job['result']['ok'] == 2 and job['result']['rows_dup'] == 1
    assert client.get(f'/api/v1/cases/{cid}/stats').json()['flows'] == 3
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_view['import_id']}").json()['result'] == old_job['result']
    assert client.get(f"/api/v1/cases/{cid}/imports/{old_view['import_id']}/records").json() == old_records
