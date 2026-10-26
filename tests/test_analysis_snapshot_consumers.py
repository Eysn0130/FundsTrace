"""One public import generation binds table, graph and exported facts."""
import io

import openpyxl

from test_event_import import csv_rows
from test_folder_evidence import accepted
from test_principal_network_api import imported_network, own
from test_graph_view_export import graph_snapshot


def test_table_graph_and_exports_share_revision_and_reject_old_generation(client, make_case):
    cid = make_case()
    imported_network(client, cid)
    table = client.get('/api/flows', params={'case_id': cid}).json()
    revision = table['analysis_revision']
    assert sum(row['event_state'] == 'confirmed' for row in table['rows']) == 2
    assert client.post('/api/track', json={'case_id': cid, 'flow_ids': [row['id'] for row in table['rows']]}).status_code == 200
    graph = client.get('/api/graph', params={'case_id': cid}).json()
    assert graph['analysis_revision'] == revision
    assert graph['case_event_summary'] == table['case_event_summary']
    params = {'case_id': cid, 'expected_revision': revision}
    response = client.get('/api/export/tracked.xlsx', params=params)
    assert response.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(response.content))
    assert dict(list(book['分析修订'].values)[1:])['分析修订'] == revision
    values = list(book['追踪流水'].values)
    exported = [dict(zip(values[0], row)) for row in values[1:]]
    assert sum(row['事件证据状态'] == 'confirmed' for row in exported) == 2
    snapshot = graph_snapshot()
    snapshot.update(case_id=cid)
    snapshot['view']['analysis_revision'] = revision
    response = client.post('/api/export/graph.html', json=snapshot)
    assert response.status_code == 200 and revision in response.text
    accepted(client, cid, [('synthetic-next.csv', csv_rows([own('SYN-B','SYN-E','SYN-B','SYN-E4',3,'1.00','1.00')]))], scope_bank=None)
    assert client.get('/api/export/tracked.xlsx', params=params).status_code == 409
    assert client.post('/api/export/graph.html', json=snapshot).status_code == 409
    for kind in ('png', 'pdf'):
        assert client.post('/api/export/graph.' + kind, json={
            'image': 'unused because stale', 'case_id': cid, 'expected_revision': revision}).status_code == 409
    fresh = client.get('/api/flows', params={'case_id': cid}).json()
    assert fresh['analysis_revision'] != revision


def test_context_displays_source_numbers_without_internal_identity_keys(client, make_case):
    cid = make_case()
    request = imported_network(client, cid)
    response = client.get('/api/context/' + request['source_flow_id'], params={'case_id': cid})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['analysis_revision']
    assert not value['display_account'].startswith('fte_')
