"""The inspector reads frozen dispositions and original cells without importing again."""
import csv
from datetime import datetime
import io
import json
import time
import zipfile

import pytest


HEADERS = ['交易流水号', '交易时间', '本方账号', '对方账号', '收付标志', '交易金额', '币种']


def encoded(rows, encoding='utf-8-sig'):
    stream = io.StringIO(newline='')
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode(encoding)


def legacy_record_items(cid, source_id, *, disposition=None, member_path=None, source_row=None, limit=None, offset=0, include_preview=False, record_nos=None):
    from app import db, bank_single_postings
    from app.services.cases import preview_flow
    where, params = "r.case_id=? and r.source_id=?", [cid, source_id]
    if disposition is not None:
        where += " and r.disposition=?"
        params.append(disposition)
    if member_path is not None:
        where += " and r.member_path=?"
        params.append(member_path)
    if source_row is not None:
        where += " and r.source_row=?"
        params.append(source_row)
    if record_nos is not None:
        where += ' and r.record_no in (select unnest(?::bigint[]))'
        params.append(record_nos)
    paging = " limit ? offset ?" if limit is not None else ""
    if limit is not None:
        params += [limit, offset]
    rows = db.q_raw(f"""select r.*,f.flow_id,m.status current_event_state,m.reason current_event_reason,m.pending_group current_pending_group,
                        e.evidence_json current_single_proof from import_records r left join flow_sources f
                        on f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no
                        left join event_members m on m.case_id=f.case_id and m.flow_id=f.flow_id
                        left join resolved_events e on e.case_id=m.case_id and e.event_id=m.event_id and m.status='confirmed'
                            and json_extract_string(e.evidence_json,'$.policy')='{bank_single_postings.POLICY}'
                        where {where} order by r.record_no{paging}""", params)
    # Only this already bounded page is projected. Original record values and
    # frozen receipt counts remain in their existing provenance contracts.
    flow_ids = list({row["flow_id"] for row in rows if row["flow_id"] is not None}) if include_preview else []
    previews = ({row["id"]: preview_flow(row) for row in db.q_raw(
        "select * from flows where case_id=? and id in (select unnest(?::bigint[]))", [cid, flow_ids])}
        if flow_ids and limit is not None else {})
    return [{**{k: r[k] for k in ("record_no", "member_path", "source_row", "record_key", "disposition", "reason_code")},
             "details": {**json.loads(r["details_json"]),
                 **({bank_single_postings.PROOF_FIELD: r["current_single_proof"]} if r["current_single_proof"] else {}),
                 **({bank_single_postings.PENDING_FIELD if (r["current_event_reason"] or '').startswith('single_posting_') else "current_bank_posting_pair_pending": r["current_event_reason"]} if r["current_pending_group"] and r["current_event_state"] == "unknown" else {}),
                 **({"current_event_state": r["current_event_state"], "current_event_reason": r["current_event_reason"]} if (r["current_event_reason"] or '').startswith('single_posting_') else {}),
                 **({"current_event_state": "unknown", "current_event_reason": "time_basis_incomparable"} if r["current_event_reason"] == "time_basis_incomparable" else {"current_event_state": "conflict"} if r["current_event_state"] == "conflict" else {})},
             "flow_id": str(r["flow_id"]) if r["flow_id"] is not None else None,
             **({"preview": {**previews[r["flow_id"]], "source_member": r["member_path"],
                              "source_row": r["source_row"], "source_record_no": r["record_no"]}}
                if r["flow_id"] in previews else {})}
            for r in rows]


def record_page_fixture(database, make_case):
    from app import bank_single_postings
    cid, foreign = make_case('SYN record page joins'), make_case('SYN foreign record page joins')
    source_id = 'a' * 32
    numbers = [-7, 0, 5, 9, 17, 23, 9007199254740993, 9223372036854775807]
    states = [('confirmed', 'single_posting_independent_balance_proof', None, 'SYN-single'),
              ('confirmed', 'single_posting_independent_balance_proof', None, 'SYN-single'),
              ('confirmed', 'bank_primary_reference_and_opposite_balance_postings', None, 'SYN-pair'),
              ('unknown', 'single_posting_cross_book_candidate', 'SYN-single-pending', 'SYN-single'),
              ('unknown', 'bank_pair_reference_reused', 'SYN-pair-pending', None),
              ('conflict', 'conflicting_reference_declarations', None, 'SYN-single'),
              ('unknown', 'time_basis_incomparable', None, None)]
    single = json.dumps({'policy': bank_single_postings.POLICY, 'SYN': 'current'})
    for case, event_id, proof in ((cid, 'SYN-single', single), (cid, 'SYN-pair', json.dumps({'policy': 'bank-feedback31-paired-postings-v1'})),
                                 (foreign, 'SYN-single', json.dumps({'policy': bank_single_postings.POLICY, 'SYN': 'foreign'}))):
        database.x("insert into resolved_events values (?,?, 'SYN-A','SYN-B','CNY',1.13,?,?)",
                   [case, event_id, datetime(2036, 2, 3, 4, 5, 6, 123456), proof])
    for index, number in enumerate(numbers):
        disposition = ('imported', 'duplicate', 'review', 'unresolved', 'non_monetary', 'cancelled', 'removed', 'auxiliary')[index]
        member = 'A.csv' if index % 2 == 0 else 'B.csv'
        database.x("insert into import_records values (?,?,?,?,?,'SYN-key',?,'SYN-reason',?)",
                   [cid, source_id, number, member, 700 + index, disposition, json.dumps({'SYN-original': index})])
        if index == 7:
            continue
        fid = 9007199254740993 if index == 1 else 9007199254740993 + index
        if index != 1:
            database.x("""insert into flows(id,case_id,trade_no,payer_account,payee_account,self_account,currency,amount,balance,trade_time)
                values (?,?,'SYN-trade','SYN-A','SYN-B','SYN-A','USD',?,null,?)""",
                [fid, cid, '9007199254740993.13', datetime(2036, 2, 3, 4, 5, 6, 123456)])
            status, reason, pending, event_id = states[index]
            database.x('insert into event_members values (?,?,?,?,?,?)', [cid, fid, event_id, status, reason, pending])
        database.x('insert into flow_sources values (?,?,?,?,?)', [cid, source_id, number, fid, disposition])
    for case, source in ((cid, 'b' * 32), (foreign, 'c' * 32)):
        database.x("insert into import_records values (?,?,5,'A.csv',2,'SYN-other','imported','SYN-reason','{}')", [case, source])
    return cid, source_id, numbers, single


def test_record_page_join_preserves_complete_old_mapping_and_scope(database, make_case):
    from app import bank_single_postings
    from app.services import imports
    cid, source, numbers, single = record_page_fixture(database, make_case)
    options = [dict(limit=3, offset=0), dict(limit=3, offset=5), dict(limit=3, offset=8),
               dict(record_nos=[]), dict(record_nos=[numbers[-1], numbers[0], numbers[3]], limit=2),
               dict(member_path='A.csv', limit=2), dict(disposition='unresolved', limit=2),
               dict(source_row=706, limit=2), dict(limit=None)]
    for option in options:
        for preview in (False, True):
            assert imports._record_items(cid, source, include_preview=preview, **option) == legacy_record_items(cid, source, include_preview=preview, **option)
    all_rows = imports._record_items(cid, source, include_preview=True, limit=50)
    assert [row['record_no'] for row in all_rows] == numbers
    assert [row['source_row'] for row in all_rows] == list(range(700, 708))
    assert all_rows[0]['details'][bank_single_postings.PROOF_FIELD] == all_rows[1]['details'][bank_single_postings.PROOF_FIELD] == single
    assert bank_single_postings.PROOF_FIELD not in all_rows[2]['details']
    assert bank_single_postings.PROOF_FIELD not in all_rows[3]['details']
    assert all_rows[3]['details'][bank_single_postings.PENDING_FIELD] == 'single_posting_cross_book_candidate'
    assert all_rows[4]['details']['current_bank_posting_pair_pending'] == 'bank_pair_reference_reused'
    assert all_rows[5]['details']['current_event_state'] == 'conflict'
    assert all_rows[6]['details']['current_event_reason'] == 'time_basis_incomparable'
    assert all_rows[0]['preview']['amount'] == '9007199254740993.13'
    assert all_rows[0]['flow_id'] == all_rows[1]['flow_id'] == '9007199254740993'
    assert all_rows[-1]['flow_id'] is None and 'preview' not in all_rows[-1]


def test_record_page_plan_materializes_before_joins_and_replaces_nested_loop(database, make_case, monkeypatch):
    from app.services import imports
    cid, source, numbers, _ = record_page_fixture(database, make_case)
    original, queries = database.q_raw, []

    def observe(sql, params=None):
        if 'current_single_proof' in sql:
            queries.append((sql, params))
        return original(sql, params)

    monkeypatch.setattr(database, 'q_raw', observe)
    keys = [numbers[-1], numbers[2], numbers[0]]
    expected = legacy_record_items(cid, source, record_nos=keys, limit=2)
    assert imports._record_items(cid, source, record_nos=keys, limit=2) == expected
    assert len(queries) == 2
    plans = [json.loads(original('explain (format json) ' + sql, params)[0]['explain_value']) for sql, params in queries]

    def walk(value):
        for node in value:
            yield node
            yield from walk(node.get('children', []))

    assert any(node['name'] == 'BLOCKWISE_NL_JOIN' for node in walk(plans[0]))
    assert not any(node['name'] == 'BLOCKWISE_NL_JOIN' for node in walk(plans[1]))
    page = next(node for node in walk(plans[1]) if node.get('extra_info', {}).get('CTE Name') == '_record_page')
    producer = list(walk(page['children'][:1]))
    assert any(node['name'] == 'TOP_N' for node in producer)
    assert not any(node.get('extra_info', {}).get('Table') in {'flow_sources', 'event_members', 'resolved_events'} for node in producer)
    assert any(node['name'] == 'CTE_SCAN' for node in walk(plans[1]))
    assert 'record_no between ?::bigint and ?::bigint' in queries[1][0]
    assert min(keys) in queries[1][1] and max(keys) in queries[1][1]


def complete(client, cid, payload, filename='synthetic.csv'):
    response = client.post(f'/api/v1/cases/{cid}/imports/preview', files={'file': (filename, payload)})
    assert response.status_code == 200, response.text
    view = response.json()
    base = f'/api/v1/cases/{cid}/imports/{view["import_id"]}'
    assert client.post(base + '/commit', json={'mode': view['mode'], 'mapping': view['mapping'], 'mark_accounts': []}).status_code == 200
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get(base).json()
        if job['stage'] in {'done', 'error'}:
            break
        time.sleep(.01)
    assert job['stage'] == 'done', job
    return base, view, job


def test_review_filters_and_navigation_are_full_file_and_keep_raw_values(client, database, make_case, monkeypatch):
    from app import table_import
    cid = make_case('Synthetic review paging')
    rows = [HEADERS]
    for index in range(70):
        rows.append([f'SYN-{index:03d}', '2026-11-13 09:00:00', '000SYN-A', '000SYN-B', '出', '9999999999999999.99', 'CNY'])
    rows[6][1] = ' 合成无效日期\n第二行 '
    rows[61][5] = '合成金额无法解析'
    payload = encoded(rows)
    base, view, job = complete(client, cid, payload)
    before = database.q_raw('select * from import_records where case_id=? order by record_no', [cid])
    monkeypatch.setattr(table_import, 'parse', lambda *a, **k: pytest.fail('review must never reparse financial facts'))
    params = {'member_path': 'table.csv', 'page_size': 10, 'view': 'all'}
    page = client.get(base + '/review', params=params)
    assert page.status_code == 200, page.text
    data = page.json()
    assert data['counts'] == {'all': 70, 'review': 2, 'accepted': 68}
    assert data['total'] == 70 and len(data['items']) == 10
    invalid = next(row for row in data['items'] if row['source_row'] == 7)
    assert invalid['original'][1]['value'] == ' 合成无效日期\n第二行 '
    assert invalid['original'][1]['fields'] == ['trade_time']
    assert invalid['preview'] is None
    good = data['items'][0]
    assert good['preview']['amount'] == '9999999999999999.99'
    assert good['original'][2]['value'] == '000SYN-A'
    filtered = client.get(base + '/review', params={**params, 'view': 'review'}).json()
    assert filtered['total'] == 2 and [row['source_row'] for row in filtered['items']] == [7, 62]
    located = client.get(base + '/review', params={**params, 'issue_index': 2}).json()
    assert located['page'] == 7 and located['issue']['index'] == 2
    assert located['issue']['source_row'] == 62
    assert located['items'][0]['original'][5]['value'] == '合成金额无法解析'
    accepted = client.get(base + '/review', params={**params, 'view': 'accepted', 'page': 7}).json()
    assert accepted['total'] == 68 and all(row['disposition'] in {'imported', 'duplicate'} for row in accepted['items'])
    assert database.q_raw('select * from import_records where case_id=? order by record_no', [cid]) == before
    assert client.get(base).json()['result'] == job['result']
    assert client.get(f'/api/v1/cases/{cid}/sources/{view["import_id"]}/file').content == payload


def test_review_scope_and_registered_original_guards(client, database, make_case):
    from app.services import imports
    cid = make_case('Synthetic review scope')
    raw = encoded([HEADERS, ['SYN-1', 'invalid', '000SYN-A', '000SYN-B', '出', '1.23', 'CNY']])
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('folder/data.csv', raw)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, 'w') as archive:
        archive.writestr('nested.zip', stream.getvalue())
    base, view, _ = complete(client, cid, outer.getvalue(), 'synthetic.zip')
    params = {'member_path': 'nested.zip/folder/data.csv', 'issue_index': 1}
    response = client.get(base + '/review', params=params)
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['original'][1]['value'] == 'invalid'
    foreign = make_case('Synthetic foreign review')
    assert client.get(f'/api/v1/cases/{foreign}/imports/{view["import_id"]}/review', params=params).status_code == 404
    assert client.get(base + '/review', params={**params, 'member_path': '../data.csv'}).status_code == 404
    assert client.get(base + '/review', params={**params, 'issue_index': 0}).status_code == 422
    assert client.get(base + '/review', params={**params, 'view': 'invented'}).status_code == 422
    path = imports._path(view['import_id'], '.zip')
    path.write_bytes(b'x' * len(outer.getvalue()))
    missing = client.get(base + '/review', params=params)
    assert missing.status_code == 200
    assert missing.json()['original_error'] and missing.json()['items'][0]['original'] is None
    assert missing.json()['counts']['review'] == 1


def test_review_multisheet_xlsx_keeps_formulas_and_exact_numeric_source(client, make_case):
    from openpyxl import Workbook
    book = Workbook()
    book.active.title = '第一表'
    book.active.append(HEADERS)
    book.active.append(['SYN-A', '2026-11-13', '000SYN-A', '000SYN-B', '出', '1.00', 'CNY'])
    sheet = book.create_sheet('第二表')
    sheet.append(['合成说明'])
    sheet.append(HEADERS)
    sheet.append(['SYN-B', '2026-11-13', '000SYN-A', '000SYN-B', '出', '=1+1', 'CNY'])
    stream = io.BytesIO(); book.save(stream); book.close()
    cid = make_case('Synthetic workbook review')
    base, _, _ = complete(client, cid, stream.getvalue(), 'synthetic.xlsx')
    response = client.get(base + '/review', params={'member_path': 'worksheets/0002', 'view': 'review', 'issue_index': 1})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page['counts'] == {'all': 2, 'review': 1, 'accepted': 0}
    assert page['items'][0]['source_row'] == 3
    assert page['items'][0]['original'][5]['value'] == '=1+1'


def test_review_duplicate_and_normal_exclusions_remain_distinct(client, make_case):
    cid = make_case('Synthetic disposition groups')
    headers = [*HEADERS, '交易是否成功']
    payload = encoded([headers,
        ['SYN-OK', '2026-11-13', '000SYN-A', '000SYN-B', '出', '1.23', 'CNY', '交易成功'],
        ['SYN-FAIL', '2026-11-13', '000SYN-A', '000SYN-B', '出', '2.34', 'CNY', '交易失败']])
    complete(client, cid, payload)
    base, _, _ = complete(client, cid, payload, 'synthetic-copy.csv')
    page = client.get(base + '/review', params={'member_path': 'table.csv'}).json()
    assert page['counts'] == {'all': 2, 'review': 0, 'accepted': 1}
    assert [row['disposition'] for row in page['items']] == ['duplicate', 'non_transaction']
    filtered = client.get(base + '/review', params={'member_path': 'table.csv', 'view': 'review'}).json()
    assert filtered['items'] == [] and filtered['total'] == 0 and filtered['issue'] is None


def test_review_xls_uses_the_registered_sheet_without_precision_reconstruction(client, make_case):
    import xlwt
    book = xlwt.Workbook()
    for name, value in [('First', 'first bad date'), ('Second', 'second bad date')]:
        sheet = book.add_sheet(name)
        for r, row in enumerate([HEADERS, ['SYN-XLS', value, '000012345678901234567890', '000SYN-B', '出', '1.23', 'CNY']]):
            for c, cell in enumerate(row):
                sheet.write(r, c, cell)
    stream = io.BytesIO(); book.save(stream)
    base, _, _ = complete(client, make_case('Synthetic XLS review'), stream.getvalue(), 'synthetic.xls')
    response = client.get(base + '/review', params={'member_path': 'worksheets/0002', 'view': 'review'})
    assert response.status_code == 200, response.text
    row = response.json()['items'][0]
    assert row['original'][1]['value'] == 'second bad date'
    assert row['original'][2]['value'] == '000012345678901234567890'


def test_review_xlsx_numeric_xml_and_sparse_blank_rows_are_not_rewritten(client, make_case):
    from openpyxl import Workbook
    book = Workbook(); sheet = book.active
    sheet.append(HEADERS)
    sheet.append(['SYN-1', 'invalid', '000SYN-A', '000SYN-B', '出', 123, 'CNY'])
    sheet.append([])
    sheet.append(['SYN-2', 'invalid', '000SYN-A', '000SYN-B', '出', '1.23', 'CNY'])
    stream = io.BytesIO(); book.save(stream); book.close()
    rewritten = io.BytesIO()
    with zipfile.ZipFile(stream) as source, zipfile.ZipFile(rewritten, 'w', zipfile.ZIP_DEFLATED) as output:
        for info in source.infolist():
            value = source.read(info)
            if info.filename == 'xl/worksheets/sheet1.xml':
                value = value.replace(b'<v>123</v>', b'<v>9999999999999999.99</v>')
            output.writestr(info, value)
    base, _, _ = complete(client, make_case('Synthetic XML scalar review'), rewritten.getvalue(), 'synthetic.xlsx')
    response = client.get(base + '/review', params={'member_path': 'worksheets/0001'})
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    assert next(row for row in rows if row['source_row'] == 2)['original'][5]['value'] == '9999999999999999.99'
    assert next(row for row in rows if row['source_row'] == 3)['original'] == []


def test_review_single_column_csv_retains_equivalent_source_readings(client, make_case):
    payload = encoded([['说明'], ['原值'], ['合成原文,含逗号']])
    base, _, _ = complete(client, make_case('Synthetic single column'), payload)
    response = client.get(base + '/review', params={'member_path': 'table.csv'})
    assert response.status_code == 200, response.text
    assert response.json()['original_error'] is None
    assert [row['original'][0]['value'] for row in response.json()['items']] == ['说明', '原值', '合成原文,含逗号']


def test_review_workbook_chart_does_not_shift_the_registered_worksheet_index(client, make_case):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    book = Workbook(); sheet = book.active
    sheet.title = 'Data'
    sheet.append(HEADERS)
    sheet.append(['SYN-CHART', '合成坏日期', '000SYN-A', '000SYN-B', '出', '1.23', 'CNY'])
    chart = BarChart(); chart.add_data(Reference(sheet, min_col=6, min_row=1, max_row=2), titles_from_data=True)
    book.create_chartsheet('Chart', index=0).add_chart(chart)
    stream = io.BytesIO(); book.save(stream); book.close()
    base, _, _ = complete(client, make_case('Synthetic chart preceding data'), stream.getvalue(), 'synthetic.xlsx')
    response = client.get(base + '/review', params={'member_path': 'worksheets/0001'})
    assert response.status_code == 200, response.text
    assert response.json()['original_error'] is None
    assert response.json()['items'][0]['original'][1]['value'] == '合成坏日期'


def test_review_shared_formula_without_stored_expression_is_explicitly_unavailable(client, make_case):
    from openpyxl import Workbook
    book = Workbook(); sheet = book.active; sheet.append(HEADERS)
    for number in (1, 2):
        sheet.append([f'SYN-SHARED-{number}', '2026-11-13', '000SYN-A', '000SYN-B', '出', '=1+1', 'CNY'])
    stream = io.BytesIO(); book.save(stream); book.close()
    output = io.BytesIO()
    with zipfile.ZipFile(stream) as source, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            value = source.read(info)
            if info.filename == 'xl/worksheets/sheet1.xml':
                value = value.replace(b'<f>1+1</f>', b'<f t="shared" si="0" ref="F2:F3">1+1</f>', 1)
                value = value.replace(b'<f>1+1</f>', b'<f t="shared" si="0"/>', 1)
            target.writestr(info, value)
    base, _, _ = complete(client, make_case('Synthetic shared formula'), output.getvalue(), 'synthetic.xlsx')
    page = client.get(base + '/review', params={'member_path': 'worksheets/0001'}).json()
    assert page['original_error']
    assert all(row['original'] is None for row in page['items'])
    assert page['counts']['review'] == 2


def test_historical_feedback_status_keeps_original_field_and_all_reasons(client, database, make_case, monkeypatch, legacy_feedback_reader):
    from app import feedback_import
    from test_feedback_import_api import archive, transaction
    monkeypatch.setattr(feedback_import, 'PROFILE', feedback_import.IDENTITY_PROFILE)
    payload = archive([transaction(**{'交易金额': 'SYN-BAD-AMOUNT', '交易时间': 'SYN-BAD-TIME', '交易是否成功': 'SYN-UNKNOWN-STATUS'})])
    cid = make_case('Synthetic historical feedback review')
    base, _, job = complete(client, cid, payload, 'synthetic.zip')
    before = database.q_raw('select * from import_records where case_id=? order by record_no', [cid])
    response = client.get(base + '/review', params={'member_path': 'synthetic/transactions.csv', 'view': 'review'})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page['original_error'] is None
    row = page['items'][0]
    assert {'invalid_amount', 'invalid_time', 'unknown_status'} <= set(row['details']['review_reasons'].split(','))
    status = next(cell for cell in row['original'] if cell['header'] == '交易是否成功')
    assert status['fields'] == ['status'] and status['value'] == 'SYN-UNKNOWN-STATUS\t'
    assert database.q_raw('select * from import_records where case_id=? order by record_no', [cid]) == before
    assert client.get(base).json()['result'] == job['result']


def test_review_attachment_occurrence_is_not_presented_as_a_physical_data_row(client, database, make_case):
    cid = make_case('Synthetic opaque retained member')
    base, view, _ = complete(client, cid, encoded([HEADERS, ['SYN-1', 'invalid', '000SYN-A', '000SYN-B', '出', '1.23', 'CNY']]))
    # Closed package journals can retain an opaque member occurrence with row 1;
    # it is not evidence of a physical PDF/image/spreadsheet row.
    database.x("update import_members set role='attachment' where case_id=? and source_id=?", [cid, view['import_id']])
    response = client.get(base + '/review', params={'member_path': 'table.csv'})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'source_rows_unavailable'


def test_review_xlsx_typed_boolean_is_not_disguised_as_money(client, make_case):
    from openpyxl import Workbook
    book = Workbook();sheet=book.active;sheet.append(HEADERS)
    sheet.append(['SYN-BOOL','2026-11-13','000SYN-A','000SYN-B','出',True,'CNY'])
    stream=io.BytesIO();book.save(stream);book.close()
    base,_,_=complete(client,make_case('Synthetic typed cell'),stream.getvalue(),'synthetic.xlsx')
    data=client.get(base+'/review',params={'member_path':'worksheets/0001','view':'review'}).json()
    cell=data['items'][0]['original'][5]
    assert cell['value']=='1' and cell['kind']=='boolean' and cell['fields']==['amount']


def test_review_xlsx_shared_rich_text_excludes_phonetic_annotations(client, make_case):
    import xml.etree.ElementTree as ET
    from openpyxl import Workbook
    book=Workbook();sheet=book.active;sheet.append(HEADERS)
    sheet.append(['SYN-STRING','2026-11-13','000SYN-A','000SYN-B','出','SYN-SHARED-STRING-BAD','CNY'])
    stream=io.BytesIO();book.save(stream);book.close();output=io.BytesIO()
    with zipfile.ZipFile(stream) as source,zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            value=source.read(info)
            if info.filename=='xl/worksheets/sheet1.xml':
                root=ET.fromstring(value);cell=next(c for c in root.iter() if c.tag.endswith('}c') and c.attrib.get('r')=='F2')
                cell.clear();cell.attrib.update(r='F2',t='s');ET.SubElement(cell,'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v').text='1';value=ET.tostring(root)
            elif info.filename=='[Content_Types].xml':
                root=ET.fromstring(value);ET.SubElement(root,'{http://schemas.openxmlformats.org/package/2006/content-types}Override',PartName='/xl/sharedStrings.xml',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml');value=ET.tostring(root)
            target.writestr(info,value)
        target.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>unused</t></si><si><r><t>SYN-SHARED-</t></r><r><t>STRING-BAD</t></r><rPh sb="0" eb="1"><t>not part of the cell</t></rPh></si></sst>')
    base,_,_=complete(client,make_case('Synthetic shared strings'),output.getvalue(),'synthetic.xlsx')
    page=client.get(base+'/review',params={'member_path':'worksheets/0001'}).json()
    assert page['original_error'] is None
    assert page['items'][0]['original'][5]['value']=='SYN-SHARED-STRING-BAD'


def test_review_large_original_is_explicitly_unavailable_without_silent_truncation(client, make_case):
    payload=encoded([HEADERS,['SYN-LARGE','2026-11-13','000SYN-A','000SYN-B','出','SYN-'+('x'*18000),'CNY']])
    cid=make_case('Synthetic bounded original')
    base,view,job=complete(client,cid,payload)
    page=client.get(base+'/review',params={'member_path':'table.csv'}).json()
    assert page['counts']=={'all':1,'review':1,'accepted':0}
    assert page['original_error'] and page['items'][0]['original'] is None
    assert client.get(base).json()['result']==job['result']
    assert client.get(f'/api/v1/cases/{cid}/sources/{view["import_id"]}/file').content==payload
