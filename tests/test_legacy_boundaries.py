"""Compatibility routes cannot bypass the case-bound v1 import contract."""
import time


def wait_legacy(client, key):
    for _ in range(200):
        job = client.get('/api/import/progress', params={'import_id': key})
        if job.status_code != 200 or job.json()['stage'] in ('done', 'error'):
            return
        time.sleep(.01)


def test_legacy_routes_cannot_adopt_v1_bound_upload(client, database, make_case):
    first, second = make_case('Bound case'), make_case('Other case')
    content = '交易时间,付款账号,收款账号,交易金额\n2026-01-01 10:00:00,SYN-A,SYN-B,123.45\n'.encode()
    preview = client.post(f'/api/v1/cases/{first}/imports/preview', files={'file': ('synthetic.csv', content)}).json()
    payload = {'import_id': preview['import_id'], 'mode': preview['mode'], 'mapping': preview['mapping'], 'case_id': second}
    commit = client.post('/api/import/commit', json=payload)
    if commit.status_code == 200:
        wait_legacy(client, preview['import_id'])
    assert commit.status_code == 404
    assert client.post('/api/import/repreview', json=payload).status_code == 404
    assert database.q('select count(*) n from flows where case_id=?', [second]) == [{'n': 0}]


def test_legacy_upload_lookup_rejects_glob_identifiers(client, make_case):
    content = '交易时间,付款账号,收款账号,交易金额\n2026-01-01 10:00:00,SYN-A,SYN-B,123.45\n'.encode()
    preview = client.post('/api/import/preview', files={'file': ('synthetic.csv', content)}).json()
    payload = {'import_id': '*', 'mode': preview['mode'], 'mapping': preview['mapping'], 'case_id': make_case()}
    response = client.post('/api/import/repreview', json=payload)
    assert response.status_code == 404


def test_legacy_progress_cannot_read_v1_job_namespace(client, make_case):
    cid = make_case()
    content = '交易时间,付款账号,收款账号,交易金额\n2026-01-01 10:00:00,SYN-A,SYN-B,123.45\n'.encode()
    preview = client.post(f'/api/v1/cases/{cid}/imports/preview', files={'file': ('synthetic.csv', content)}).json()
    iid = preview['import_id']
    assert client.post(f'/api/v1/cases/{cid}/imports/{iid}/commit', json={'mode':'A','mapping':preview['mapping']}).status_code == 200
    for _ in range(200):
        job = client.get(f'/api/v1/cases/{cid}/imports/{iid}').json()
        if job['stage'] in ('done', 'error'):
            break
        time.sleep(.01)
    assert job['stage'] == 'done'
    for key in (iid, 'v1:' + iid):
        assert client.get('/api/import/progress', params={'import_id': key, 'case_id': cid}).status_code == 404
