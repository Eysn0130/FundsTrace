"""A new analysis interpretation must revoke exports even without a write."""
from app import analysis_snapshot
from test_principal_book_model import book_row, import_books, single_body


def test_policy_upgrade_rejects_previous_principal_and_default_export(client, database, make_case, monkeypatch):
    cid = make_case('Synthetic policy revision')
    rows = import_books(client, cid, [
        book_row('B', 'EXT', 'B', '100.00', '100.00', 0, 1, 'SOURCE'),
        book_row('B', 'B', 'C', '100.00', '0.00', 1, 2, 'TARGET'),
    ])
    body = single_body(cid, rows)
    before = client.post('/api/analysis/principal', json=body).json()
    assert before['status'] == 'ok'
    generation = database.q_raw('select revision from analysis_state where case_id=?', [cid])
    monkeypatch.setattr(analysis_snapshot, 'POLICY', 'synthetic-next-interpretation', raising=False)
    response = client.post('/api/analysis/principal/export', json={**body, 'expected_revision': before['revision']})
    assert response.status_code == 409
    assert client.get('/api/export/tracked.xlsx', params={'case_id': cid, 'expected_revision': before['revision']}).status_code == 409
    after = client.post('/api/analysis/principal', json=body).json()
    assert after['revision'] != before['revision']
    assert after['targets'] == before['targets']
    assert database.q_raw('select revision from analysis_state where case_id=?', [cid]) == generation


def test_pre_versioned_generation_is_not_an_export_token(client, database, make_case):
    cid = make_case('Synthetic old generation')
    original = analysis_snapshot.invalidate(cid)
    current = analysis_snapshot.revision(cid)
    assert current != original
    assert len(current) == 32 and all(character in '0123456789abcdef' for character in current)
    response = client.get('/api/export/tracked.xlsx', params={'case_id': cid, 'expected_revision': original})
    assert response.status_code == 409
