"""Commander review: synthetic public CSV and portable-source withdrawal."""
import time

import pytest


CSV = ("交易时间,付款账号,收款账号,交易金额\n"
       "2026-01-01 10:00:00.123456,SYN-COMMANDER-A,SYN-COMMANDER-B,100.01\n").encode()


def import_csv(client, case_id):
    response = client.post(f"/api/v1/cases/{case_id}/imports/preview",
                           files={"file": ("synthetic-review.csv", CSV)})
    assert response.status_code == 200, response.text
    view = response.json()
    endpoint = f"/api/v1/cases/{case_id}/imports/{view['import_id']}"
    response = client.post(endpoint + "/commit", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert response.status_code == 200, response.text
    for _ in range(400):
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            return view["import_id"], job["result"]
        time.sleep(.01)
    raise AssertionError("synthetic import timed out")


@pytest.mark.parametrize("restore_package", [False, True])
@pytest.mark.parametrize("remove_original_first", [False, True])
def test_public_csv_and_two_support_package_keep_last_supported_fact(
        client, restore_package, remove_original_first):
    response = client.post("/api/cases", json={"name": "Synthetic commander support review"})
    assert response.status_code == 200, response.text
    origin = response.json()["id"]
    first_source, first = import_csv(client, origin)
    _, second = import_csv(client, origin)
    assert (first["ok"], second["ok"], second["rows_dup"]) == (1, 0, 1)
    origin_rows = client.get(f"/api/v1/cases/{origin}/flows").json()
    assert origin_rows["total"] == 1
    origin_id = origin_rows["items"][0]["id"]
    assert client.post("/api/track", json={"case_id": origin, "flow_ids": [origin_id]}).status_code == 200
    cid = origin
    if restore_package:
        package = client.get(f"/api/cases/{origin}/export")
        assert package.status_code == 200, package.text
        response = client.post("/api/cases/import", files={"file": ("synthetic-review.fundcase", package.content)})
        assert response.status_code == 200, response.text
        cid = response.json()["case_id"]
    rows_url = f"/api/v1/cases/{cid}/flows"
    before = client.get(rows_url).json()["items"][0]
    fid = before["id"]
    sources_url = f"/api/v1/cases/{cid}/flows/{fid}/sources"
    assert client.get(sources_url).json()["total"] == 2
    batches = client.get("/api/batches", params={"case_id": cid}).json()
    assert len(batches) == 2
    ordered = sorted(batches, key=lambda row: int(row["id"]))
    retired, kept = ordered if remove_original_first else ordered[::-1]
    assert client.delete(f"/api/batches/{retired['id']}", params={"case_id": cid}).status_code == 200
    after = client.get(rows_url).json()
    assert after["total"] == 1
    fact = after["items"][0]
    for key in ("id", "amount", "trade_time", "payer_account", "payee_account"):
        assert fact[key] == before[key]
    assert fact["amount"] == "100.01"
    assert "123456" in fact["trade_time"]
    assert client.get(sources_url).json()["total"] == 1
    assert set(map(str, client.get("/api/graph", params={"case_id": cid}).json()["tracked_ids"])) == {str(fid)}
    assert client.delete(f"/api/batches/{kept['id']}", params={"case_id": cid}).status_code == 200
    assert client.get(rows_url).json()["total"] == 0
    assert client.get("/api/graph", params={"case_id": cid}).json()["tracked_ids"] == []
    if restore_package:
        assert client.get(f"/api/v1/cases/{origin}/flows").json() == origin_rows
        assert client.get(f"/api/v1/cases/{origin}/flows/{origin_id}/sources").json()["total"] == 2
        assert client.get(f"/api/v1/cases/{origin}/sources/{first_source}/file").content == CSV


def reopen(database):
    with database._lock:
        database._con.close()
        database._con = None
        database.con()


@pytest.mark.parametrize("original_first", [True, False])
def test_ordinary_receipt_source_and_post_withdrawal_package(client, database, original_first):
    cid = client.post('/api/cases', json={'name': 'Synthetic operation receipt'}).json()['id']
    first_id, first = import_csv(client, cid)
    second_id, second = import_csv(client, cid)
    sid, removed, kept = (first_id, first, second) if original_first else (second_id, second, first)
    receipt_url = f'/api/v1/cases/{cid}/imports/{sid}'
    before = client.get(receipt_url).json()['result']
    assert client.delete(f"/api/batches/{removed['batch_id']}", params={'case_id': cid}).status_code == 200
    assert client.get(receipt_url).json()['result'] == before
    assert client.get(f'/api/v1/cases/{cid}/sources/{sid}/file').content == CSV
    reopen(database)
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 1
    package = client.get(f'/api/cases/{cid}/export')
    restored = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', package.content)})
    assert restored.status_code == 200, restored.text
    other = restored.json()['case_id']
    batch = client.get('/api/batches', params={'case_id': other}).json()[0]['id']
    assert client.delete(f'/api/batches/{batch}', params={'case_id': cid}).status_code == 404
    assert client.delete(f'/api/batches/{batch}', params={'case_id': other}).status_code == 200
    assert client.get(f'/api/v1/cases/{other}/flows').json()['total'] == 0
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 1
    assert client.delete(f"/api/batches/{kept['batch_id']}", params={'case_id': cid}).status_code == 200
    reopen(database)
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0


def test_ordinary_withdrawal_rolls_back_every_operation_table(client, database, monkeypatch):
    from app import evidence_resolution
    cid = client.post('/api/cases', json={'name': 'Synthetic atomic operation'}).json()['id']
    _, first = import_csv(client, cid)
    import_csv(client, cid)
    tables = ('flows', 'flow_sources', 'import_records', 'import_source_operations', 'import_sources', 'import_sessions', 'batches')
    def snapshot():
        return {t: database.q_raw(f'select * from {t} where case_id=? order by all', [cid]) for t in tables}
    before = snapshot()
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic withdrawal failure')
    monkeypatch.setattr(evidence_resolution, 'rebuild', fail)
    with pytest.raises(RuntimeError, match='synthetic withdrawal failure'):
        client.delete(f"/api/batches/{first['batch_id']}", params={'case_id': cid})
    assert snapshot() == before


def test_explicit_receipt_migration_is_idempotent_and_never_recreates_facts(client, database):
    cid = client.post('/api/cases', json={'name': 'Synthetic pre-metadata database'}).json()['id']
    _, first = import_csv(client, cid)
    _, second = import_csv(client, cid)
    # Model the previous schema, retaining its exact committed public receipts.
    database.x('drop table import_source_operations')
    reopen(database)
    assert database.q_raw('select count(*) n from import_source_operations where case_id=? and active', [cid]) == [{'n': 2}]
    assert client.delete(f"/api/batches/{first['batch_id']}", params={'case_id': cid}).status_code == 200
    reopen(database)
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 1
    assert client.delete(f"/api/batches/{second['batch_id']}", params={'case_id': cid}).status_code == 200
    database.x('drop table import_source_operations')
    reopen(database)
    assert database.q_raw('select count(*) n from import_source_operations where case_id=? and active', [cid]) == [{'n': 0}]
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0


@pytest.mark.parametrize('evidence_first', [False, True])
def test_mixed_parser_operations_do_not_change_profile_or_support(client, database, evidence_first):
    from test_folder_evidence import accepted
    cid = client.post('/api/cases', json={'name': 'Synthetic mixed parser operations'}).json()['id']
    _, ordinary = import_csv(client, cid)
    _, evidence = accepted(client, cid, [('synthetic-evidence.csv', CSV.replace(b'100.01', b'200.02'))], scope_bank=None)
    # Different original bytes may coexist under both profile contracts;
    # the existing same-original profile-switch rejection stays in force.
    before = database.q_raw('select source_id,flow_id from flow_sources where case_id=? order by all', [cid])
    assert database.q_raw('select parser_version from import_sources where source_id=?', [ordinary['source_id']]) == [{'parser_version': 'funds-trace-table-v17'}]
    removed, kept = (evidence, ordinary) if evidence_first else (ordinary, evidence)
    expected = {r['flow_id'] for r in before if r['source_id'] == kept['source_id']}
    assert client.delete(f"/api/batches/{removed['batch_id']}", params={'case_id': cid}).status_code == 200
    assert {r['id'] for r in database.q_raw('select id from flows where case_id=?', [cid])} == expected
    assert client.delete(f"/api/batches/{kept['batch_id']}", params={'case_id': cid}).status_code == 200
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 0


def package_members(content):
    import io, zipfile
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def package_bytes(members):
    import io, zipfile
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return output.getvalue()


@pytest.mark.parametrize('defect', ['missing', 'unknown_source', 'missing_batch', 'inactive_link', 'duplicate', 'old_version'])
def test_operation_package_rejects_invalid_support_atomically(client, database, tmp_path, defect):
    import json, duckdb
    cid = client.post('/api/cases', json={'name': 'Synthetic tampered operation package'}).json()['id']
    import_csv(client, cid)
    members = package_members(client.get(f'/api/cases/{cid}/export').content)
    assert json.loads(members['meta.json'])['format'] == 6
    if defect == 'missing':
        del members['import_source_operations.parquet']
    elif defect == 'old_version':
        meta = json.loads(members['meta.json']); meta['format'] = 4
        members['meta.json'] = json.dumps(meta).encode()
    else:
        path = tmp_path / 'operations.parquet'; path.write_bytes(members['import_source_operations.parquet'])
        with duckdb.connect() as con:
            con.execute('create table ops as select * from read_parquet(?)', [str(path)])
            if defect == 'unknown_source':
                con.execute("update ops set source_id='synthetic-nonexistent-source'")
            elif defect == 'missing_batch':
                con.execute('update ops set batch_id=9223372036854775807')
            elif defect == 'inactive_link':
                con.execute('update ops set active=false,batch_id=null')
            else:
                con.execute('insert into ops select * from ops')
            con.table('ops').write_parquet(str(path))
        members['import_source_operations.parquet'] = path.read_bytes()
    before = database.q_raw('select * from cases order by id')
    response = client.post('/api/cases/import', files={'file': ('synthetic.fundcase', package_bytes(members))})
    assert response.status_code == 400, response.text
    assert database.q_raw('select * from cases order by id') == before
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 1


def test_format5_explicit_evidence_operations_remain_portable(client, database, make_case):
    import json
    from test_batch_support_withdrawal import setup_pair
    cid = make_case('Synthetic prior format 5')
    setup_pair(client, cid)
    members = package_members(client.get(f'/api/cases/{cid}/export').content)
    del members['import_source_operations.parquet']
    meta = json.loads(members['meta.json']); meta['format'] = 5
    members['meta.json'] = json.dumps(meta).encode()
    restored = client.post('/api/cases/import', files={'file': ('synthetic-v5.fundcase', package_bytes(members))})
    assert restored.status_code == 200, restored.text
    other = restored.json()['case_id']
    batches = sorted(client.get('/api/batches', params={'case_id': other}).json(), key=lambda r: int(r['id']))
    assert client.delete(f"/api/batches/{batches[0]['id']}", params={'case_id': other}).status_code == 200
    assert client.get(f'/api/v1/cases/{other}/flows', params={'granularity': 'observations'}).json()['total'] == 2
    assert client.delete(f"/api/batches/{batches[1]['id']}", params={'case_id': other}).status_code == 200
    assert client.get(f'/api/v1/cases/{other}/flows').json()['total'] == 0


def test_retired_receipt_migration_clears_stale_links_without_guessing_unknown_sources(client, database):
    import json
    cid = client.post('/api/cases', json={'name': 'Synthetic retired historical replay'}).json()['id']
    first_id, first = import_csv(client, cid)
    second_id, second = import_csv(client, cid)
    # Exact prior failure: removing the replay batch left both flow links.
    database.x('delete from batches where case_id=? and id=?', [cid, second['batch_id']])
    database.x('drop table import_source_operations')
    reopen(database)
    assert database.q_raw('select source_id from flow_sources where case_id=?', [cid]) == [{'source_id': first_id}]
    assert database.q_raw('select active from import_source_operations where source_id=?', [second_id]) == [{'active': False}]
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 1
    package = client.get(f'/api/cases/{cid}/export')
    assert client.post('/api/cases/import', files={'file': ('synthetic.fundcase', package.content)}).status_code == 200
    # A malformed receipt is not authority to fill a missing mapping.
    database.x('delete from import_source_operations where source_id=?', [first_id])
    database.x('update import_sessions set result_json=? where import_id=?', [json.dumps({'source_id': second_id, 'batch_id': first['batch_id']}), first_id])
    reopen(database)
    assert database.q_raw('select * from import_source_operations where source_id=?', [first_id]) == []
    assert client.get(f'/api/v1/cases/{cid}/flows').json()['total'] == 1


def test_migration_never_treats_another_cases_batch_as_retired_support(client, database):
    import json
    cid = client.post('/api/cases', json={'name': 'Synthetic migration case isolation'}).json()['id']
    other = client.post('/api/cases', json={'name': 'Synthetic other operation'}).json()['id']
    sid, _ = import_csv(client, cid)
    _, other_receipt = import_csv(client, other)
    database.x('delete from import_source_operations where source_id=?', [sid])
    database.x('update import_sessions set result_json=? where import_id=?', [json.dumps({'source_id': sid, 'batch_id': other_receipt['batch_id']}), sid])
    reopen(database)
    assert database.q_raw('select * from import_source_operations where source_id=?', [sid]) == []
    assert database.q_raw('select source_id from flow_sources where case_id=?', [cid]) == [{'source_id': sid}]
