"""Ordinary totals collapse proved pairs while retaining their two ledgers."""
import io

from openpyxl import load_workbook
import pytest

from test_event_import import transaction, csv_rows
from test_folder_evidence import accepted


def observations(client, cid):
    return client.get(f'/api/v1/cases/{cid}/flows', params={'granularity': 'observations'}).json()['items']


def track(client, cid, rows):
    response = client.post('/api/track', json={'case_id': cid, 'flow_ids': [row['id'] for row in rows]})
    assert response.status_code == 200, response.text
    return client.get('/api/graph', params={'case_id': cid}).json()


def assert_pair_graph(graph, *, count=1, amount='100.00'):
    assert graph['transaction_count'] == count
    assert len(graph['edges']) == 1
    edge = graph['edges'][0]
    assert (edge['cnt'], edge['total']) == (count, amount)
    payer = next(node for node in graph['nodes'] if node['account'] == edge['source'])
    payee = next(node for node in graph['nodes'] if node['account'] == edge['target'])
    assert (payer['out_cnt'], payer['out_amt']) == (count, amount)
    assert (payee['in_cnt'], payee['in_amt']) == (count, amount)
    return edge


@pytest.mark.parametrize('both', [False, True])
def test_confirmed_pair_list_nodes_details_sources_and_export_share_scope(client, make_case, both):
    cid = make_case('Synthetic canonical selected scope')
    accepted(client, cid, [('pair.csv', csv_rows([transaction('SYN-A'), transaction('SYN-B')]))], scope_bank=None)
    rows = observations(client, cid)
    assert len(rows) == 2 and {row['balance'] for row in rows} == {'50.00', '200.00'}
    stats = client.get(f'/api/v1/cases/{cid}/stats').json()
    assert stats['flows'] == 1 and stats['amount_totals'] == [{'currency': 'CNY', 'amount': '100.00'}]
    assert stats['funds']['observation_count'] == 2
    page = client.get(f'/api/v1/cases/{cid}/flows').json()
    assert page['total'] == 1 and page['items'][0]['balance'] is None
    selected = rows if both else rows[:1]
    graph = track(client, cid, selected)
    edge = assert_pair_graph(graph)
    assert set(map(str, graph['tracked_ids'])) == {row['id'] for row in selected}
    assert graph['funds']['observation_count'] == len(selected)
    source = client.get(f"/api/v1/cases/{cid}/flows/{selected[0]['id']}/sources").json()
    assert source['total'] == 2
    assert {row['observation_balance'] for row in source['items']} == {'50.00', '200.00'}
    assert {row['observation_amount'] for row in source['items']} == {'100.00'}
    detail = client.get('/api/flows', params={'case_id': cid, 'tracked': 1,
        'account': edge['source'], 'counter': edge['target'], 'account_exact': True,
        'counter_exact': True, 'granularity': 'events'}).json()
    assert detail['total'] == 1 and detail['rows'][0]['amount'] == '100.00'
    profile = client.get('/api/account-registry/profile', params={'case_id': cid, 'account': edge['source']}).json()
    assert (profile['out_cnt'], profile['out_amt'], profile['out_observation_count']) == (1, '100.00', 2)
    response = client.get('/api/export/tracked.xlsx', params={'case_id': cid})
    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content), read_only=True)
    table = list(book['追踪流水'].values)
    assert len(table) == 2 and table[1][table[0].index('交易金额')] == '100.00'
    raw = list(book['原始账簿观察'].values)
    assert len(raw) == len(selected) + 1
    assert {row[raw[0].index('本方原余额')] for row in raw[1:]} == {row['balance'] for row in selected}
    book.close()
    package = client.get(f'/api/cases/{cid}/export')
    restored = client.post('/api/cases/import', files={'file': ('scope.fundcase', package.content)})
    assert restored.status_code == 200, restored.text
    new_id = restored.json()['case_id']
    assert client.get(f'/api/v1/cases/{new_id}/stats').json()['flows'] == 1
    assert {row['balance'] for row in observations(client, new_id)} == {'50.00', '200.00'}


def test_independent_same_amount_and_same_second_are_two_transfers(client, make_case):
    cid = make_case('Synthetic two independent same amount')
    rows = [transaction(own, **{'跨账簿事件参考号': ref, '交易流水号': own + ref})
            for ref in ('SYN-E1', 'SYN-E2') for own in ('SYN-A', 'SYN-B')]
    accepted(client, cid, [('independent.csv', csv_rows(rows))], scope_bank=None)
    assert_pair_graph(track(client, cid, observations(client, cid)), count=2, amount='200.00')
    page = client.get(f'/api/v1/cases/{cid}/flows').json()
    assert page['total'] == 2 and all(row['amount'] == '100.00' for row in page['items'])


def test_global_late_conflict_cannot_be_hidden_by_filter_or_selection(client, make_case):
    cid = make_case('Synthetic global contradiction')
    source, receipt = accepted(client, cid, [('pair.csv', csv_rows([transaction('SYN-A'), transaction('SYN-B')]))], scope_bank=None)
    selected = observations(client, cid)
    assert_pair_graph(track(client, cid, selected))
    _, conflict = accepted(client, cid, [('late.csv', csv_rows([transaction('SYN-A', **{'交易金额': '101.00'})]))], scope_bank=None)
    graph = client.get('/api/graph', params={'case_id': cid}).json()
    assert graph['edges'] == [] and graph['nodes'] == []
    assert graph['transaction_count'] == 0 and len(graph['tracked_ids']) == 2
    stats = client.get(f'/api/v1/cases/{cid}/stats').json()
    assert stats['flows'] == 0 and stats['amount_totals'] == []
    assert stats['accounts'] == 0 and stats['funds']['observation_count'] == 3
    legacy = client.get('/api/stats', params={'case_id': cid}).json()
    assert legacy['flows'] == 0 and legacy['accounts'] == 0 and legacy['funds']['observation_count'] == 3
    for endpoint in ('/api/account-registry/profile', f"/api/accounts/{selected[0]['payer_account']}/profile"):
        profile = client.get(endpoint, params={'case_id': cid, 'account': selected[0]['payer_account']}).json()
        assert profile['top_counterparties'] == [] and profile['tmin'] is profile['tmax'] is None
    assert stats['event_summary']['conflict_entries'] == 3
    assert not stats['import_coverage']['financial_interpretation_complete']
    assert len(observations(client, cid)) == 3
    record_view = client.get(f"/api/v1/cases/{cid}/imports/{source['import_id']}/records").json()
    assert all(row['details']['current_event_state'] == 'conflict' for row in record_view['items'])
    assert client.get(f"/api/v1/cases/{cid}/imports/{source['import_id']}").json()['result'] == receipt
    filtered = client.get('/api/flows', params={'case_id': cid, 'max_amount': '100.00', 'tracked': 1}).json()
    assert filtered['total'] == 0 and filtered['funds']['confirmed_event_count'] == 0
    window = client.post('/api/canvas/investigation/time', json={'case_id': cid}).json()
    assert window['edges'] == [] and window['nodes'] == []
    assert window['summary']['selected_count'] == 0 and window['summary']['amount_totals'] == []
    for row in selected:
        sources = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()
        assert sources['items'][0]['observation_amount'] == '100.00'
    snapshot = client.get('/api/export/tracked.xlsx', params={'case_id': cid})
    assert snapshot.status_code == 200, snapshot.text
    book = load_workbook(io.BytesIO(snapshot.content), read_only=True)
    assert len(list(book['追踪流水'].values)) == 1
    assert len(list(book['原始账簿观察'].values)) == 3
    book.close()
    package = client.get(f'/api/cases/{cid}/export')
    restored = client.post('/api/cases/import', files={'file': ('conflict.fundcase', package.content)})
    assert restored.status_code == 200, restored.text
    reopened = restored.json()['case_id']
    assert client.get(f'/api/v1/cases/{reopened}/stats').json()['flows'] == 0
    assert client.get('/api/graph', params={'case_id': reopened}).json()['edges'] == []
    assert len(observations(client, reopened)) == 3
    assert client.delete(f"/api/batches/{conflict['batch_id']}", params={'case_id': cid}).status_code == 200
    assert_pair_graph(client.get('/api/graph', params={'case_id': cid}).json())
    record_view = client.get(f"/api/v1/cases/{cid}/imports/{source['import_id']}/records").json()
    assert all('current_event_state' not in row['details'] for row in record_view['items'])
