"""Automatic import facts remain effective in newer main account/canvas paths."""
import io

from openpyxl import load_workbook
import pytest

from test_automatic_table_import import preview
from test_cases import insert_flow
from test_feedback_import_api import complete
from test_cancelled_analysis import seed


def _seed_flow_identity_queries(database, make_case, flow_row):
    cid, anchor, cancelled, kept, refund = seed(database, make_case, flow_row)
    neighbor = insert_flow(database, cid, flow_row, trade_no='NEIGHBOR', amount='1.00',
                           payer_account='B-SUFFIX', payee_account='TAIL', self_account='B-SUFFIX')
    other = make_case('Synthetic other identity query case')
    foreign = insert_flow(database, other, flow_row, payer_account='B', payee_account='A',
                          self_account='B')
    for case_id, account, identity in [
        (cid, 'B', 'SYN-ID%TOKEN'),
        (cid, 'B-SUFFIX', 'SYN-ID-ANY-TOKEN'),
        (cid, 'CANCELLED-ONLY', 'SYN-CANCELLED-ID'),
        (cid, 'NO-FLOW', 'SYN-UNBOUND-ID'),
        (other, 'B', 'SYN-ID%TOKEN'),
        (other, 'A', 'SYN-FOREIGN-ID'),
    ]:
        database.x('insert into account_ids(case_id,account,id_no) values (?,?,?)',
                   [case_id, account, identity])
    return cid, [refund, kept, anchor], cancelled, neighbor, other, foreign


@pytest.mark.parametrize('query_key', ['entity', 'counter'])
def test_main_flow_identity_queries_page_effective_rows_and_bind_identity_to_case(
        client, database, make_case, flow_row, query_key):
    cid, expected, cancelled, neighbor, other, foreign = _seed_flow_identity_queries(
        database, make_case, flow_row)
    # '%' is literal identity text; the neighboring account has a different ID.
    query = {'case_id': cid, query_key: '  ＳＹＮ－ＩＤ％ＴＯＫＥＮ  ', 'size': 1}
    for page, expected_ids in enumerate(([expected[0]], [expected[1]], [expected[2]], []), 1):
        response = client.get('/api/flows', params={**query, 'page': page})
        assert response.status_code == 200, response.text
        value = response.json()
        assert (value['total'], value['page'], value['size']) == (3, page, 1)
        assert [row['id'] for row in value['rows']] == expected_ids
    for identity in ('SYN-CANCELLED-ID', 'SYN-UNBOUND-ID', 'SYN-FOREIGN-ID'):
        response = client.get('/api/flows', params={'case_id': cid, query_key: identity})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 0 and response.json()['rows'] == []
    foreign_response = client.get('/api/flows', params={**query, 'case_id': other})
    assert foreign_response.status_code == 200, foreign_response.text
    assert foreign_response.json()['total'] == 1
    assert [row['id'] for row in foreign_response.json()['rows']] == [foreign]
    assert database.q_raw('select id from flows where case_id=? and id=?', [cid, cancelled]) == [
        {'id': cancelled}]


def test_main_flow_exact_counter_and_existing_account_filters_keep_effective_rows(
        client, database, make_case, flow_row):
    cid, expected, cancelled, neighbor, other, foreign = _seed_flow_identity_queries(
        database, make_case, flow_row)
    base = {'case_id': cid, 'counter': ' B ', 'counter_exact': True, 'size': 2}
    for page, expected_ids in ((1, expected[:2]), (2, expected[2:])):
        response = client.get('/api/flows', params={**base, 'page': page})
        assert response.status_code == 200, response.text
        value = response.json()
        assert (value['total'], value['page'], value['size']) == (3, page, 2)
        assert [row['id'] for row in value['rows']] == expected_ids
    for counter in ('SYN-ID%TOKEN', 'CANCELLED-ONLY'):
        response = client.get('/api/flows', params={**base, 'counter': counter})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 0 and response.json()['rows'] == []
    foreign_response = client.get('/api/flows', params={**base, 'case_id': other})
    assert foreign_response.status_code == 200, foreign_response.text
    assert foreign_response.json()['total'] == 1
    assert [row['id'] for row in foreign_response.json()['rows']] == [foreign]
    ordinary = client.get('/api/flows', params={'case_id': cid, 'account': 'B'})
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()['total'] == 4
    assert {row['id'] for row in ordinary.json()['rows']} == set(expected) | {neighbor}
    assert client.get('/api/flows', params={'case_id': cid, 'account': 'B', 'entity': ''}).json() == ordinary.json()
    for account, expected_ids in ((' B ', expected), ('B-SUFFIX', [neighbor])):
        response = client.get('/api/flows', params={'case_id': cid, 'account': account,
                                                  'account_exact': True})
        assert response.status_code == 200, response.text
        assert response.json()['total'] == len(expected_ids)
        assert [row['id'] for row in response.json()['rows']] == expected_ids


def test_main_account_workspace_uses_effective_money_and_keeps_original_provenance(
        client, database, make_case, flow_row):
    cid, anchor, cancelled, kept, refund = seed(database, make_case, flow_row)
    registry = client.get('/api/account-registry', params={'case_id': cid, 'source': 'flows'})
    assert registry.status_code == 200
    assert {row['account'] for row in registry.json()['items']} == {'A', 'B', 'C'}
    profile = client.get('/api/account-registry/profile', params={'case_id': cid, 'account': 'B'})
    assert profile.status_code == 200
    value = profile.json()
    assert (value['cnt'], value['out_amt'], value['in_amt']) == (3, '10.00', '13.00')
    assert {row['acct'] for row in value['top_counterparties']} == {'A', 'C'}
    assert database.q_raw('select id from flows where case_id=? and id=?', [cid, cancelled]) == [{'id': cancelled}]
    sources = client.get(f'/api/v1/cases/{cid}/flows/{cancelled}/sources')
    assert sources.status_code == 200 and sources.json()['items'][0]['source_id'] == 'synthetic-v2'


def test_main_analysis_canvas_creation_keeps_its_retry_contract_and_rejects_excluded_money(
        client, database, make_case, flow_row):
    cid, anchor, cancelled, kept, refund = seed(database, make_case, flow_row)
    before = database.q_raw('select * from canvases where case_id=? order by id', [cid])
    body = {'case_id': cid, 'name': 'Synthetic effective analysis', 'flow_ids': [cancelled], 'snapshot': {}}
    assert client.post('/api/canvases/from-flows', json=body).status_code == 404
    assert database.q_raw('select * from canvases where case_id=? order by id', [cid]) == before

    body['flow_ids'] = [anchor, kept, refund]
    created = client.post('/api/canvases/from-flows', json=body)
    assert created.status_code == 200 and created.json()['added'] == 3
    repeated = client.post('/api/canvases/from-flows', json=body)
    assert repeated.status_code == 200
    assert (repeated.json()['canvas_id'], repeated.json()['added']) == (created.json()['canvas_id'], 0)
    graph = client.get('/api/graph', params={'case_id': cid, 'canvas_id': created.json()['canvas_id']}).json()
    assert set(graph['tracked_ids']) == {anchor, kept, refund}
    assert {flow for edge in graph['edges'] for flow in edge['ids']} == {anchor, kept, refund}
    database.x("update flows set payee_account='' where case_id=? and id=?", [cid, anchor])
    body.update(name='Synthetic missing counterparty', flow_ids=[anchor])
    before = database.q_raw('select * from canvases where case_id=? order by id', [cid])
    assert client.post('/api/canvases/from-flows', json=body).status_code == 422
    assert database.q_raw('select * from canvases where case_id=? order by id', [cid]) == before


def test_table_only_tracked_export_discloses_unresolved_source_coverage(
        client, database, make_case):
    cid = make_case('Synthetic table export coverage')
    data = (b'date,account,counterparty account,amount,direction\n'
            b'2026-01-01,SYN-OWN,SYN-OTHER,10.00,out\n'
            b'2026-01-02,SYN-OWN,SYN-UNKNOWN,99.00,unknown\n')
    view = preview(client, cid, data)
    assert view['automatic'] and view['table_summary']['unresolved'] == 1
    job = complete(client, cid, view)
    assert job['stage'] == 'done' and job['result']['ok'] == 1
    ids = [row['id'] for row in database.q_raw('select id from flows where case_id=?', [cid])]
    assert client.post('/api/track', json={'case_id': cid, 'flow_ids': ids}).status_code == 200
    response = client.get('/api/export/tracked.xlsx', params={'case_id': cid})
    assert response.status_code == 200
    book = load_workbook(io.BytesIO(response.content), read_only=True)
    try:
        assert '统计口径' in book.sheetnames
        notes = dict(list(book['统计口径'].values)[1:])
        assert notes['资金效果未确定记录数'] == 1
        assert notes['已导入来源资金语义是否完整'] == '否'
        assert '未计入流水统计' in notes['未确定记录']
    finally:
        book.close()
