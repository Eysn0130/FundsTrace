"""Retired managed proofs cannot exclude another active source's records."""
import pytest

from app import db
from app.services import imports
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_table_cancellations import source, transaction


@pytest.mark.parametrize('archive_first', [False, True])
@pytest.mark.parametrize('withdraw_proof_first', [False, True])
def test_live_operation_controls_cancellation_exclusion_and_reopen(
        client, database, make_case, monkeypatch, archive_first, withdraw_proof_first):
    cid = make_case('Synthetic revocable cancellation proof')
    rows = [transaction(**{'交易金额': '8.00', '交易余额': '20.00'}),
            transaction(**{'交易时间': '2026-01-01 10:01:00', '交易流水号': 'SYN-REVOKE', '交易金额': '3.21', '交易余额': '16.79'}),
            transaction(**{'交易时间': '2026-01-01 10:02:00', '交易流水号': 'SYN-REVOKE', '交易金额': '-3.21', '交易余额': '20.00', '摘要说明': '冲正'})]
    text, zipped = source(rows)
    parse = imports._parse
    def old(*args, **kwargs):
        kwargs.update(table_profile='funds-trace-table-v27', _auto_event_profile=False)
        return parse(*args, **kwargs)
    receipts = {}
    for kind in (['zip', 'csv'] if archive_first else ['csv', 'zip']):
        if kind == 'csv':
            with monkeypatch.context() as pinned:
                pinned.setattr(imports, '_parse', old)
                view = preview(client, cid, text)
        else:
            view = preview(client, cid, zipped, 'proof.zip')
        result = complete(client, cid, view)
        assert result['stage'] == 'done', result
        receipts[kind] = result['result']
        if len(receipts) == 1:
            selected = client.get(f'/api/v1/cases/{cid}/flows').json()['items']
            assert client.post('/api/track', json={'case_id': cid, 'flow_ids': [row['id'] for row in selected]}).status_code == 200
    def assert_current(count, total):
        stats = client.get(f'/api/v1/cases/{cid}/stats').json()
        assert stats['flows'] == count and stats['amount_totals'] == [{'currency': 'CNY', 'amount': total}]
        page = client.get(f'/api/v1/cases/{cid}/flows').json()
        assert len(page['items']) == (3 if count == 1 else 2)
        assert page['funds']['observation_count'] == count
    assert_current(1, '8.00')
    first, last = ('zip', 'csv') if withdraw_proof_first else ('csv', 'zip')
    assert client.delete(f"/api/batches/{receipts[first]['batch_id']}", params={'case_id': cid}).status_code == 200
    count, total = (2, '11.21') if withdraw_proof_first else (1, '8.00')
    assert_current(count, total)
    graph = client.get('/api/graph', params={'case_id': cid}).json()
    # The current ordinary list also makes both cancelled source rows selectable.
    selected_count = 2 if withdraw_proof_first else 1
    selected_total = '11.21' if selected_count == 2 else '8.00'
    assert len(graph['edges']) == 1
    assert (graph['edges'][0]['cnt'], graph['edges'][0]['total']) == (selected_count, selected_total)
    with db._lock:
        db._con.close()
        db._con = None
    assert_current(count, total)
    graph = client.get('/api/graph', params={'case_id': cid}).json()
    assert (graph['edges'][0]['cnt'], graph['edges'][0]['total']) == (selected_count, selected_total)
    assert client.delete(f"/api/batches/{receipts[last]['batch_id']}", params={'case_id': cid}).status_code == 200
    assert client.get(f'/api/v1/cases/{cid}/stats').json()['flows'] == 0
