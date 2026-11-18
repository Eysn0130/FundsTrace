"""Progress remains responsive without showing an in-flight import's rows."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest


@pytest.mark.parametrize('surface', ['v1', 'legacy'])
def test_progress_reads_committed_state_while_import_writer_is_blocked(client, make_case, monkeypatch, surface):
    from app.services import imports
    from test_archive_auxiliary_rows import source

    cid = make_case('Synthetic responsive import progress')
    other = make_case('Synthetic foreign progress')
    entered, release = threading.Event(), threading.Event()
    write = imports._write_feedback_journal

    def paused(*args):
        result = write(*args)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(imports, '_write_feedback_journal', paused)
    prefix = f'/api/v1/cases/{cid}/imports' if surface == 'v1' else '/api/import'
    preview = client.post(prefix + '/preview', params={} if surface == 'v1' else {'case_id': cid},
                          files={'file': ('synthetic.zip', source('evidence', malformed=False))})
    assert preview.status_code == 200
    view = preview.json()
    body = {'mode': view['mode'], 'mapping': view['mapping'], 'mark_accounts': [] if surface == 'v1' else ''}
    if surface == 'legacy':
        body.update(case_id=int(cid), import_id=view['import_id'])
    base = prefix + '/' + view['import_id'] if surface == 'v1' else prefix
    assert client.post(base + '/commit', json=body).status_code == 200
    assert entered.wait(5)
    route = base if surface == 'v1' else prefix + '/progress'
    params = {} if surface == 'v1' else {'case_id': cid, 'import_id': view['import_id']}
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            result = pool.submit(client.get, route, params=params).result(timeout=1)
            assert result.status_code == 200
            value = result.json()
            assert value['stage'] == 'importing' and value['done'] == 0 and value['result'] is None
            assert value.get('member_results', []) == []
            wrong_route = f"/api/v1/cases/{other}/imports/{view['import_id']}" if surface == 'v1' else route
            wrong_params = {} if surface == 'v1' else {**params, 'case_id': other}
            assert pool.submit(client.get, wrong_route, params=wrong_params).result(timeout=1).status_code == 404
        finally:
            release.set()
    until = time.monotonic() + 10
    while time.monotonic() < until:
        final = client.get(route, params=params).json()
        if final['stage'] in {'done', 'error'}:
            break
        time.sleep(.01)
    assert final['stage'] == 'done' and final['done'] > 0 and final['member_results']
