"""Write endpoints reject missing cases and references owned by another case."""
import io
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from app import main
from test_cases import insert_flow


@pytest.mark.parametrize("method,url,extra", [
    ("post", "/api/track", {}),
    ("delete", "/api/track", {}),
    ("post", "/api/track/bulk", {"direction": "target"}),
])
@pytest.mark.parametrize("wrong_reference", ["flow", "canvas"])
def test_tracking_checks_case_ownership(client, database, make_case, flow_row, method, url, extra, wrong_reference):
    first, second = make_case("First"), make_case("Second")
    first_flow = insert_flow(database, first, flow_row)
    second_flow = insert_flow(database, second, flow_row)
    second_canvas = database.q("insert into canvases(case_id,name,snapshot) values (?,'Other canvas','{}') returning id", [second])[0]["id"]
    body = {"case_id": first, "flow_ids": [second_flow if wrong_reference == "flow" else first_flow], "canvas_id": second_canvas if wrong_reference == "canvas" else 0, **extra}
    response = client.request(method, url, json=body)
    assert response.status_code == 404, response.text
    assert database.q("select count(*) n from tracked_flows")[0]["n"] == 0


def test_batch_delete_requires_case_and_cannot_delete_another_case(client, database, make_case, flow_row):
    first, second = make_case("First"), make_case("Second")
    bid = database.q("insert into batches(case_id,filename) values (?,'synthetic.csv') returning id", [second])[0]["id"]
    fid = insert_flow(database, second, flow_row, batch_id=bid)
    assert client.delete(f"/api/batches/{bid}").status_code == 422
    assert client.delete(f"/api/batches/{bid}?case_id={first}").status_code == 404
    assert database.q("select id from flows where id=?", [fid])
    assert client.delete(f"/api/batches/{bid}?case_id={second}").status_code == 200
    assert not database.q("select id from flows where id=?", [fid])


@pytest.mark.parametrize("method,url,body", [
    ("post", "/api/sample-data", {"case_id": 99999}),
    ("post", "/api/main-accounts", {"case_id": 99999, "account": "SYN-A"}),
    ("delete", "/api/main-accounts", {"case_id": 99999, "account": "SYN-A"}),
    ("post", "/api/main-accounts/auto", {"case_id": 99999}),
    ("post", "/api/main-accounts/clear-auto", {"case_id": 99999}),
    ("post", "/api/canvases/save", {"case_id": 99999, "name": "Synthetic", "snapshot": {}}),
    ("post", "/api/canvases/clone", {"case_id": 99999, "name": "Synthetic", "snapshot": {}}),
    ("post", "/api/canvases/rename", {"case_id": 99999, "canvas_id": 1, "name": "Synthetic"}),
    ("post", "/api/focus-nodes/set", {"case_id": 99999, "account": "SYN-A", "on": True}),
    ("post", "/api/account-ids/set", {"case_id": 99999, "account": "SYN-A", "id_no": "123456789012345"}),
    ("post", "/api/account-photos/clear", {"case_id": 99999, "account": "SYN-A"}),
    ("post", "/api/track", {"case_id": 99999, "flow_ids": []}),
])
def test_attached_writes_reject_missing_case(client, database, method, url, body):
    assert client.request(method, url, json=body).status_code == 404
    for table in main.CASE_PKG_TABLES:
        assert not database.q(f"select * from {table} where case_id=99999")
    assert not (database.DATA_DIR / "photos" / "c99999").exists()


def test_account_id_and_photo_upload_reject_missing_case(client, database):
    image = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(image, "PNG")
    response = client.post("/api/account-photos/set", data={"case_id": 99999, "account": "SYN-A"}, files={"file": ("synthetic.png", image.getvalue())})
    assert response.status_code == 404
    response = client.post("/api/account-ids/import", data={"case_id": 99999}, files={"file": ("synthetic.csv", b"account,id_no\nSYN-A,123456789012345\n")})
    assert response.status_code == 404
    assert not (database.DATA_DIR / "photos" / "c99999").exists()


def test_clone_rejects_canvas_from_other_case(client, database, make_case):
    first, second = make_case("First"), make_case("Second")
    cv = database.q("insert into canvases(case_id,name,snapshot) values (?,'Other','{}') returning id", [second])[0]["id"]
    response = client.post("/api/canvases/clone", json={"case_id": first, "from_canvas": cv, "name": "Clone", "snapshot": {}})
    assert response.status_code == 404
    assert not database.q("select id from canvases where case_id=?", [first])


def test_multi_step_write_cannot_revive_a_deleted_case(client, database, make_case, flow_row, monkeypatch):
    cid = make_case("Concurrent delete")
    insert_flow(database, cid, flow_row)
    entered, proceed = threading.Event(), threading.Event()
    # Pause the writer after its case check while the transaction lock is held.
    original_q = main.db.q
    def pause_q(sql, params=None):
        if sql.startswith("insert into canvases"):
            entered.set()
            assert proceed.wait(5)
        return original_q(sql, params)
    monkeypatch.setattr(main.db, "q", pause_q)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(client.post, "/api/canvases/save", json={"case_id": cid, "name": "Concurrent", "snapshot": {}})
        assert entered.wait(5)
        deleting = pool.submit(client.delete, f"/api/cases/{cid}")
        proceed.set()
        assert saving.result().status_code == 200
        assert deleting.result().status_code == 200
    assert not database.q("select id from cases where id=?", [cid])
    for table in main.CASE_PKG_TABLES:
        assert not database.q(f"select * from {table} where case_id=?", [cid])


def test_photo_backup_failure_preserves_original_files_and_database(client, database, make_case, monkeypatch):
    import shutil
    cid = make_case("Photo backup failure")
    directory = database.DATA_DIR / "photos" / f"c{cid}"
    directory.mkdir(parents=True)
    original_file = directory / f"{main._photo_hash('SYN-A')}.orig.png"
    original_file.write_bytes(b"synthetic original photo")
    database.x("insert into account_photos(case_id,account,fname) values (?,'SYN-A',?)", [cid, original_file.name])
    def fail_backup(*args, **kwargs):
        raise OSError("synthetic backup failure")
    monkeypatch.setattr(shutil, "copytree", fail_backup)
    with pytest.raises(OSError, match="synthetic backup failure"):
        client.post("/api/account-photos/clear", json={"case_id": cid, "account": "SYN-A"})
    assert original_file.read_bytes() == b"synthetic original photo"
    assert database.q("select fname from account_photos where case_id=?", [cid]) == [{"fname": original_file.name}]


def uploaded_import(client):
    content = "交易时间,付款账号,收款账号,交易金额,本方账号\n2026-01-01 10:00:00,SYN-A,SYN-B,123.45,SYN-A\n".encode()
    response = client.post("/api/import/preview", files={"file": ("synthetic.csv", content)})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"import_id": body["import_id"], "mode": "A", "mapping": {**body["mapping"], "self_account": "本方账号"}}


def finished_import(client, payload):
    import time
    response = client.post("/api/import/commit", json=payload)
    assert response.status_code == 200, response.text
    for _ in range(200):
        progress = client.get("/api/import/progress", params={"import_id": payload["import_id"], "case_id": payload["case_id"]}).json()
        if progress["stage"] in ("done", "error"):
            return progress
        time.sleep(0.01)
    raise AssertionError("Synthetic import did not finish")


@pytest.mark.parametrize("explicit", [False, True])
def test_import_explicit_empty_main_selection_differs_from_omitted_selection(client, database, make_case, explicit):
    cid = make_case("Explicit selection")
    body = {**uploaded_import(client), "case_id": cid}
    if explicit:
        body["mark_accounts"] = ""
    result = finished_import(client, body)
    assert result["stage"] == "done", result
    assert database.q("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1
    assert database.q("select account from main_accounts where case_id=?", [cid]) == ([] if explicit else [{"account": "SYN-A"}])


def test_background_import_main_account_failure_rolls_back_batch_and_flows(client, database, make_case, monkeypatch):
    cid = make_case("Atomic main marks")
    body = {**uploaded_import(client), "case_id": cid}
    original = database.x
    def fail_mark(sql, params=None):
        if sql.startswith("insert or replace into main_accounts"):
            # The real nested importer has already written the batch and row.
            assert database.q("select count(*) n from flows where case_id=?", [cid])[0]["n"] == 1
            raise RuntimeError("synthetic main account failure")
        return original(sql, params)
    monkeypatch.setattr(database, "x", fail_mark)
    result = finished_import(client, body)
    assert result["stage"] == "error", result
    assert result["error_code"] == "import_failed"
    assert "synthetic main account failure" not in result["error"]
    for table in ("flows", "batches", "main_accounts"):
        assert not database.q(f"select * from {table} where case_id=?", [cid])
