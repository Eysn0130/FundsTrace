"""Case creation keeps duplicate-name errors separate from storage failures."""
from fastapi.testclient import TestClient

from app import main


def test_case_names_are_trimmed_and_duplicates_are_rejected(client, database):
    created = client.post('/api/cases', json={'name': '  Synthetic case  '})
    assert created.status_code == 200
    assert created.json()['name'] == 'Synthetic case'
    duplicate = client.post('/api/cases', json={'name': 'Synthetic case'})
    assert duplicate.status_code == 400
    assert duplicate.json()['detail'] == '案件名已存在'
    assert client.post('/api/cases', json={'name': '  '}).status_code == 400
    assert len(database.q('select * from cases')) == 2


def test_storage_failure_does_not_claim_duplicate_name(database, monkeypatch):
    query = main.db.q

    def failed_write(sql, params=None):
        if sql.startswith('insert into cases'):
            raise OSError('synthetic storage failure')
        return query(sql, params)

    monkeypatch.setattr(main.db, 'q', failed_write)
    with TestClient(main.app, raise_server_exceptions=False) as client:
        response = client.post('/api/cases', json={'name': 'Synthetic failed case'})
    assert response.status_code == 500
    assert '案件名已存在' not in response.text
    assert len(query('select * from cases')) == 1
