"""Case packages use only synthetic rows and in-process public API calls."""
import io
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import zipfile

import duckdb
import pytest

from app import main


def insert_flow(database, case_id, flow_row, **overrides):
    row = flow_row(**overrides)
    cols = list(row)
    return database.q(
        f"insert into flows(case_id,{','.join(cols)}) values (?,{','.join('?' for _ in cols)}) returning id",
        [case_id, *row.values()],
    )[0]["id"]


def populated_case(database, make_case, flow_row, name="Package source"):
    cid = make_case(name)
    bid = database.q("insert into batches(case_id,filename,mode,rows_total,rows_imported) values (?, 'synthetic.csv','A',2,2) returning id", [cid])[0]["id"]
    ids = [insert_flow(database, cid, flow_row, batch_id=bid) for _ in range(2)]
    canvases = [database.q("insert into canvases(case_id,name,snapshot) values (?,?,'{}') returning id", [cid, f"Canvas {i}"])[0]["id"] for i in range(2)]
    for fid, cv in zip(ids, canvases):
        database.x("insert into tracked_flows(case_id,flow_id,canvas_id) values (?,?,?)", [cid, fid, cv])
    database.x("insert into tracked_flows(case_id,flow_id,canvas_id) values (?,?,0)", [cid, ids[0]])
    database.x("insert into main_accounts(case_id,account,name) values (?,'SYN-A','Synthetic sender')", [cid])
    database.x("insert into account_ids(case_id,account,id_no) values (?,'SYN-A','123456789012345')", [cid])
    database.x("insert into focus_nodes(case_id,account,scale) values (?,'SYN-A',2.0)", [cid])
    photo = database.DATA_DIR / "photos" / f"c{cid}"
    photo.mkdir(parents=True)
    (photo / "avatar.png").write_bytes(b"synthetic-photo-bytes")
    database.x("insert into account_photos(case_id,account,fname,crop_cx,crop_cy,crop_r) values (?,'SYN-A','avatar.png',0.2,0.3,0.4)", [cid])
    return cid


def exported(client, case_id):
    response = client.get(f"/api/cases/{case_id}/export")
    assert response.status_code == 200, response.text
    return response.content


def import_package(client, content):
    return client.post("/api/cases/import", files={"file": ("synthetic.fundcase", content, "application/octet-stream")})


def members(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {item.filename: archive.read(item) for item in archive.infolist()}


def archive_of(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


def rewrite_parquet(entries, table, sql, tmp_path):
    source = tmp_path / f"{table}-source.parquet"
    target = tmp_path / f"{table}-target.parquet"
    source.write_bytes(entries[f"{table}.parquet"])
    with duckdb.connect() as con:
        con.execute("create table rows as select * from read_parquet(?)", [str(source)])
        con.execute(sql)
        con.table("rows").write_parquet(str(target))
    entries[f"{table}.parquet"] = target.read_bytes()


def test_package_round_trip_preserves_duplicate_rows_and_all_associations(client, database, make_case, flow_row):
    source = populated_case(database, make_case, flow_row)
    response = import_package(client, exported(client, source))
    assert response.status_code == 200, response.text
    new = response.json()["case_id"]
    assert new != source
    for table in main.CASE_PKG_TABLES:
        before = database.q(f"select count(*) n from {table} where case_id = ?", [source])[0]["n"]
        after = database.q(f"select count(*) n from {table} where case_id = ?", [new])[0]["n"]
        assert after == before, table
    refs = database.q("select tf.flow_id, c.name from tracked_flows tf join canvases c on tf.canvas_id=c.id where tf.case_id=? order by c.name", [new])
    assert len({row["flow_id"] for row in refs}) == 2
    assert [row["name"] for row in refs] == ["Canvas 0", "Canvas 1"]
    assert (database.DATA_DIR / "photos" / f"c{new}" / "avatar.png").read_bytes() == b"synthetic-photo-bytes"


@pytest.mark.parametrize("bad_name", ["../escape.parquet", "/escape.parquet", "C:/escape.parquet", "photos/../escape.png", "photos\\escape.png", "unexpected.parquet"])
def test_package_rejects_non_format_paths_before_writes(client, database, make_case, flow_row, bad_name):
    source = populated_case(database, make_case, flow_row)
    entries = members(exported(client, source))
    entries[bad_name] = b"invalid"
    before = database.q("select * from cases")
    response = import_package(client, archive_of(entries))
    assert response.status_code == 400
    assert database.q("select * from cases") == before


def test_package_rejects_duplicate_zip_members(client, database, make_case, flow_row):
    source = populated_case(database, make_case, flow_row)
    entries = members(exported(client, source))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
        with pytest.warns(UserWarning):
            archive.writestr("meta.json", entries["meta.json"])
    assert import_package(client, output.getvalue()).status_code == 400
    assert len(database.q("select * from cases")) == 2


@pytest.mark.parametrize("mutation", ["bad_parquet", "bad_json", "bad_format", "unknown_flow", "unknown_canvas", "unknown_batch", "unsafe_photo", "missing_photo"])
def test_invalid_package_never_leaves_partial_case(client, database, make_case, flow_row, tmp_path, mutation):
    source = populated_case(database, make_case, flow_row)
    entries = members(exported(client, source))
    if mutation == "bad_parquet":
        entries["account_ids.parquet"] = b"not parquet"
    elif mutation == "bad_json":
        entries["meta.json"] = b"[broken"
    elif mutation == "bad_format":
        entries["meta.json"] = json.dumps({"format": 99, "name": "Synthetic invalid"}).encode()
    elif mutation == "unknown_flow":
        rewrite_parquet(entries, "tracked_flows", "update rows set flow_id=999999", tmp_path)
    elif mutation == "unknown_canvas":
        rewrite_parquet(entries, "tracked_flows", "update rows set canvas_id=999999", tmp_path)
    elif mutation == "unknown_batch":
        rewrite_parquet(entries, "flows", "update rows set batch_id=999999", tmp_path)
    elif mutation == "unsafe_photo":
        rewrite_parquet(entries, "account_photos", "update rows set fname='../escape.png'", tmp_path)
    else:
        entries.pop("photos/avatar.png")
    before = {t: database.q(f"select count(*) n from {t}")[0]["n"] for t in ["cases", *main.CASE_PKG_TABLES]}
    response = import_package(client, archive_of(entries))
    assert response.status_code == 400, response.text
    assert {t: database.q(f"select count(*) n from {t}")[0]["n"] for t in before} == before
    assert not list((database.DATA_DIR / "photos").glob("c*/*escape*"))


def test_legacy_optional_tables_are_independent_and_default_tracking_survives(client, database, make_case, flow_row, tmp_path):
    source = populated_case(database, make_case, flow_row)
    entries = members(exported(client, source))
    entries.pop("batches.parquet")
    entries.pop("canvases.parquet")
    rewrite_parquet(entries, "tracked_flows", "delete from rows where canvas_id<>0", tmp_path)
    for _ in range(2):
        response = import_package(client, archive_of(entries))
        assert response.status_code == 200, response.text
        cid = response.json()["case_id"]
        assert database.q("select count(*) n from flows where case_id=? and batch_id is null", [cid])[0]["n"] == 2
        assert database.q("select count(*) n from tracked_flows where case_id=? and canvas_id=0", [cid])[0]["n"] == 1


def test_missing_canvas_table_cannot_silently_drop_tracking(client, database, make_case, flow_row):
    source = populated_case(database, make_case, flow_row)
    entries = members(exported(client, source))
    entries.pop("canvases.parquet")
    assert import_package(client, archive_of(entries)).status_code == 400


def test_concurrent_exports_have_independent_workspaces(client, database, make_case, flow_row, tmp_path):
    case_ids = [make_case(f"Concurrent export {i}") for i in range(2)]
    for i, cid in enumerate(case_ids):
        insert_flow(database, cid, flow_row, trade_no=f"EXPORT-{i}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda cid: (cid, exported(client, cid)), case_ids * 4))
    for i, (cid, content) in enumerate(results):
        entries = members(content)
        assert json.loads(entries["meta.json"])["name"] == f"Concurrent export {case_ids.index(cid)}"
        path = tmp_path / f"export-{i}.parquet"
        path.write_bytes(entries["flows.parquet"])
        with duckdb.connect() as con:
            assert con.execute("select trade_no from read_parquet(?)", [str(path)]).fetchall() == [(f"EXPORT-{case_ids.index(cid)}",)]


def test_concurrent_imports_allocate_distinct_names_and_ids(client, database, make_case, flow_row):
    source = populated_case(database, make_case, flow_row)
    content = exported(client, source)
    with ThreadPoolExecutor(max_workers=5) as pool:
        imports = [pool.submit(import_package, client, content) for _ in range(4)]
        deletion = pool.submit(client.delete, f"/api/cases/{source}")
        responses = [future.result() for future in imports]
        assert deletion.result().status_code == 200
    assert not database.q("select id from cases where id=?", [source])
    assert [r.status_code for r in responses] == [200] * 4
    assert len({r.json()["case_id"] for r in responses}) == 4
    assert len({r.json()["name"] for r in responses}) == 4
    for response in responses:
        cid = response.json()["case_id"]
        assert database.q("select count(distinct flow_id) n from tracked_flows where case_id=? and canvas_id<>0", [cid])[0]["n"] == 2


def test_export_fails_if_referenced_photo_is_missing(client, database, make_case, flow_row):
    source = populated_case(database, make_case, flow_row)
    (database.DATA_DIR / "photos" / f"c{source}" / "avatar.png").unlink()
    assert client.get(f"/api/cases/{source}/export").status_code == 409


def test_case_deletion_removes_every_case_table_and_photo_but_preserves_other_case(client, database, make_case, flow_row):
    victim = populated_case(database, make_case, flow_row, "Delete me")
    other = populated_case(database, make_case, flow_row, "Keep me")
    response = client.delete(f"/api/cases/{victim}")
    assert response.status_code == 200
    assert response.json()["deleted_flows"] == 2
    for table in main.CASE_PKG_TABLES:
        assert database.q(f"select count(*) n from {table} where case_id=?", [victim])[0]["n"] == 0, table
        assert database.q(f"select count(*) n from {table} where case_id=?", [other])[0]["n"] > 0, table
    assert not (database.DATA_DIR / "photos" / f"c{victim}").exists()
    assert (database.DATA_DIR / "photos" / f"c{other}" / "avatar.png").exists()
    assert client.delete("/api/cases/1").status_code == 400


def test_import_photo_write_failure_cannot_create_partial_case(client, database, make_case, flow_row, monkeypatch):
    source = populated_case(database, make_case, flow_row)
    content = exported(client, source)
    original = Path.write_bytes
    def fail_photo(path, data):
        if path.name == "avatar.png":
            raise OSError("synthetic photo write failure")
        return original(path, data)
    monkeypatch.setattr(Path, "write_bytes", fail_photo)
    with pytest.raises(OSError, match="synthetic photo write failure"):
        import_package(client, content)
    assert len(database.q("select * from cases")) == 2
    for table in main.CASE_PKG_TABLES:
        assert not database.q(f"select * from {table} where case_id not in (select id from cases)")
    assert [p.name for p in (database.DATA_DIR / "photos").iterdir()] == [f"c{source}"]


def test_case_delete_file_failure_rolls_back_database_and_restores_photos(client, database, make_case, flow_row, monkeypatch):
    source = populated_case(database, make_case, flow_row)
    photo_dir = database.DATA_DIR / "photos" / f"c{source}"
    original = shutil.rmtree
    def fail_delete(path, *args, **kwargs):
        if Path(path) == photo_dir:
            raise OSError("synthetic photo delete failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(shutil, "rmtree", fail_delete)
    with pytest.raises(OSError, match="synthetic photo delete failure"):
        client.delete(f"/api/cases/{source}")
    assert database.q("select id from cases where id=?", [source])
    for table in main.CASE_PKG_TABLES:
        assert database.q(f"select count(*) n from {table} where case_id=?", [source])[0]["n"] > 0, table
    assert (photo_dir / "avatar.png").read_bytes() == b"synthetic-photo-bytes"


def test_package_paths_support_apostrophes(client, database, make_case, flow_row, tmp_path, monkeypatch):
    import tempfile
    directory = tmp_path / "temporary folder's"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    source = populated_case(database, make_case, flow_row)
    response = import_package(client, exported(client, source))
    assert response.status_code == 200, response.text
    assert response.json()["flows"] == 2
    assert not list(directory.iterdir())


def test_import_commit_failure_restores_files_and_next_import_has_no_stale_mapping(client, database, make_case, flow_row, monkeypatch):
    from contextlib import contextmanager
    source = populated_case(database, make_case, flow_row)
    content = exported(client, source)
    original = database.transaction
    @contextmanager
    def fail_commit():
        with original() as connection:
            yield connection
            raise RuntimeError("synthetic database commit failure")
    monkeypatch.setattr(database, "transaction", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic database commit failure"):
        import_package(client, content)
    assert len(database.q("select * from cases")) == 2
    assert [p.name for p in (database.DATA_DIR / "photos").iterdir()] == [f"c{source}"]
    monkeypatch.setattr(database, "transaction", original)
    response = import_package(client, content)
    assert response.status_code == 200, response.text
    assert response.json()["flows"] == 2


def test_delete_database_failure_restores_original_photo_directory(client, database, make_case, flow_row, monkeypatch):
    from contextlib import contextmanager
    source = populated_case(database, make_case, flow_row)
    original = database.transaction
    @contextmanager
    def fail_commit():
        with original() as connection:
            yield connection
            raise RuntimeError("synthetic delete transaction failure")
    monkeypatch.setattr(database, "transaction", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic delete transaction failure"):
        client.delete(f"/api/cases/{source}")
    assert database.q("select id from cases where id=?", [source])
    assert database.q("select count(*) n from flows where case_id=?", [source])[0]["n"] == 2
    assert (database.DATA_DIR / "photos" / f"c{source}" / "avatar.png").read_bytes() == b"synthetic-photo-bytes"


@pytest.mark.parametrize("format_version", [1, 2])
def test_package_remaps_nonempty_snapshot_flow_references_without_changing_accounts(client, database, make_case, flow_row, format_version):
    source = populated_case(database, make_case, flow_row)
    ids = [row["id"] for row in database.q("select id from flows where case_id=? order by id", [source])]
    # BIGINT identities remain exact above JavaScript's safe-integer boundary.
    large_id = 9_007_199_254_740_993
    database.x("update flows set id=? where id=?", [large_id, ids[0]])
    database.x("update tracked_flows set flow_id=? where flow_id=?", [large_id, ids[0]])
    snapshot = {"nodes": [{"id": "SYN-A", "x": 10, "y": 20}, {"id": "SYN-B", "x": 100, "y": 20}],
                "edges": [{"source": "SYN-A", "target": "SYN-B", "type": "polyline",
                           "controlPoints": [{"x": 50, "y": 40}],
                           "data": {"ids": [large_id, str(ids[1])], "total": "246.90"}}],
                "display": {"showPhotos": False}, "legacy_extra": {"retained": True}}
    canvas = database.q("select id from canvases where case_id=? order by id", [source])[0]["id"]
    database.x("update canvases set snapshot=? where id=?", [json.dumps(snapshot), canvas])
    entries = members(exported(client, source))
    if format_version == 1:
        meta = json.loads(entries["meta.json"])
        meta["format"] = 1
        meta.pop("source_files")
        entries["meta.json"] = json.dumps(meta).encode()
        entries.pop("import_sources.parquet")
        entries.pop("flow_sources.parquet")
    response = import_package(client, archive_of(entries))
    assert response.status_code == 200, response.text
    imported = response.json()["case_id"]
    canvases = client.get(f"/api/canvases?case_id={imported}").json()
    restored = client.get(f"/api/canvases/{next(c['id'] for c in canvases if c['name'] == 'Canvas 0')}?case_id={imported}").json()["snapshot"]
    new_ids = {row["id"] for row in database.q("select id from flows where case_id=?", [imported])}
    snapshot_ids = restored["edges"][0]["data"]["ids"]
    assert {int(value) for value in snapshot_ids} == new_ids
    assert type(snapshot_ids[0]) is int and isinstance(snapshot_ids[1], str)
    assert restored["nodes"] == snapshot["nodes"]
    assert restored["edges"][0]["controlPoints"] == snapshot["edges"][0]["controlPoints"]
    assert restored["edges"][0]["data"]["total"] == "246.90"
    assert restored["display"] == snapshot["display"] and restored["legacy_extra"] == snapshot["legacy_extra"]
    assert json.loads(database.q("select snapshot from canvases where id=?", [canvas])[0]["snapshot"]) == snapshot


def test_package_drops_deleted_flow_metadata_but_preserves_saved_geometry(client, database, make_case, flow_row):
    source = populated_case(database, make_case, flow_row)
    flow = database.q("select id,batch_id from flows where case_id=? order by id", [source])[0]
    snapshot = {"nodes": [{"id": "SYN-A", "x": 10, "y": 20}],
                "edges": [{"source": "SYN-A", "target": "SYN-B", "data": {"ids": [flow["id"]]}}]}
    canvas = database.q("select id from canvases where case_id=? order by id", [source])[0]["id"]
    database.x("update canvases set snapshot=? where id=?", [json.dumps(snapshot), canvas])
    assert client.delete(f"/api/batches/{flow['batch_id']}?case_id={source}").status_code == 200
    # Deleting a batch intentionally leaves its historical display geometry.
    assert json.loads(database.q("select snapshot from canvases where id=?", [canvas])[0]["snapshot"]) == snapshot
    response = import_package(client, exported(client, source))
    assert response.status_code == 200, response.text
    imported = response.json()["case_id"]
    assert response.json()["flows"] == 0
    restored = json.loads(database.q("select snapshot from canvases where case_id=? and name='Canvas 0'", [imported])[0]["snapshot"])
    assert restored["nodes"] == snapshot["nodes"]
    assert restored["edges"][0]["source"] == "SYN-A"
    assert restored["edges"][0]["data"]["ids"] == []
    assert (database.DATA_DIR / "photos" / f"c{imported}" / "avatar.png").read_bytes() == b"synthetic-photo-bytes"


@pytest.mark.parametrize("snapshot", [
    {"nodes": {}, "edges": []}, {"nodes": [], "edges": {}},
    {"nodes": [None]}, {"nodes": [{"x": 1, "y": 2}]},
    {"nodes": [{"id": "SYN-A", "x": float("nan"), "y": 2}]},
    {"edges": ["broken"]}, {"edges": [{"source": "SYN-A"}]},
    {"edges": [{"source": "SYN-A", "target": "SYN-B", "controlPoints": {"x": 1}}]},
    {"edges": [{"source": "SYN-A", "target": "SYN-B", "controlPoints": [None]}]},
    {"edges": [{"source": "SYN-A", "target": "SYN-B", "data": {"ids": {}}}]},
    *[{"edges": [{"source": "SYN-A", "target": "SYN-B", "data": {"ids": [value]}}]}
      for value in (True, 1.4, "1.4", float("inf"), 2**63)],
])
def test_invalid_snapshot_cannot_create_case_or_photos(client, database, make_case, flow_row, tmp_path, snapshot):
    source = populated_case(database, make_case, flow_row)
    entries = members(exported(client, source))
    # Only the archived snapshot is damaged; the source remains untouched.
    path = tmp_path / "snapshot.parquet"
    path.write_bytes(entries["canvases.parquet"])
    target = tmp_path / "invalid-snapshot.parquet"
    with duckdb.connect() as connection:
        connection.execute("create table rows as select * from read_parquet(?)", [str(path)])
        connection.execute("update rows set snapshot=?", [json.dumps(snapshot)])
        connection.table("rows").write_parquet(str(target))
    entries["canvases.parquet"] = target.read_bytes()
    before = {t: database.q(f"select count(*) n from {t}")[0]["n"] for t in ["cases", *main.CASE_PKG_TABLES]}
    response = import_package(client, archive_of(entries))
    assert response.status_code == 400, response.text
    assert {t: database.q(f"select count(*) n from {t}")[0]["n"] for t in before} == before
    assert [path.name for path in (database.DATA_DIR / "photos").iterdir()] == [f"c{source}"]


@pytest.mark.parametrize("table,field,value", [
    ("flows", "id", "1.4"), ("flows", "batch_id", "1.4"),
    ("tracked_flows", "flow_id", "1.4"), ("tracked_flows", "canvas_id", "1.4"),
    ("canvases", "id", "1.4"), ("batches", "rows_total", "1.4"),
    ("batches", "rows_imported", "NaN"), ("batches", "rows_skipped", "Infinity"),
    ("batches", "rows_dup", "-Infinity"), ("flows", "id", "9223372036854775808"),
])
def test_package_rejects_nonintegral_and_out_of_range_integer_fields(client, database, make_case, flow_row, tmp_path, table, field, value):
    source = populated_case(database, make_case, flow_row)
    entries = members(exported(client, source))
    # VARCHAR preserves the input's exact fractional or out-of-range value.
    rewrite_parquet(entries, table, f"alter table rows alter column {field} type varchar; update rows set {field}='{value}' where rowid=(select min(rowid) from rows)", tmp_path)
    before = database.q("select count(*) n from cases")
    response = import_package(client, archive_of(entries))
    assert response.status_code == 400, response.text
    assert database.q("select count(*) n from cases") == before
    assert [path.name for path in (database.DATA_DIR / "photos").iterdir()] == [f"c{source}"]


@pytest.mark.parametrize("snapshot", [{}, {"nodes": [], "edges": []}, {
    "nodes": [{"id": "SYN-A", "x": 1, "y": 2}],
    "edges": [{"source": "SYN-A", "target": "SYN-B"},
              {"source": "SYN-B", "target": "SYN-A", "data": {}}]},
])
def test_legacy_snapshot_without_flow_metadata_remains_readable(client, database, make_case, flow_row, snapshot):
    source = populated_case(database, make_case, flow_row)
    database.x("update canvases set snapshot=? where case_id=?", [json.dumps(snapshot), source])
    response = import_package(client, exported(client, source))
    assert response.status_code == 200, response.text
    restored = database.q("select snapshot from canvases where case_id=?", [response.json()["case_id"]])
    assert [json.loads(row["snapshot"]) for row in restored] == [snapshot, snapshot]
