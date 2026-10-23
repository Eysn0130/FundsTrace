"""Synthetic persistence, isolation, rollback and retention behavior."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from fastapi import UploadFile
import pytest

from app import importer
from app.services import imports
from app.services.cases import ServiceError


CSV = ("交易时间,付款人账号,收款人账号,交易金额,本方账号\n"
       "2026-01-01 10:00:00,000SYN-A,000SYN-B,9999999999999999.99,000SYN-A\n"
       "2026-01-01 11:00:00,000SYN-A,000SYN-C,0.01,000SYN-A\n").encode()


def upload(cid, legacy=False):
    file = UploadFile(filename="synthetic.csv", file=io.BytesIO(CSV))
    return asyncio.run(imports.legacy_upload(file, cid) if legacy else imports.upload(str(cid), file))


def body(preview, **extra):
    return SimpleNamespace(mode=preview["mode"], mapping={**preview["mapping"], "self_account": "本方账号"}, self_accounts=[], mark_accounts=None, **extra)


def poll(cid, iid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = imports.progress(str(cid), iid)
        if result["stage"] in ("done", "error"):
            return result
        time.sleep(.01)
    pytest.fail("Synthetic import did not finish")


def test_durable_receipt_exact_money_and_source_download(database, make_case):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    assert imports.commit(str(cid), iid, body(preview))["started"]
    job = poll(cid, iid)
    assert job["stage"] == "done", job
    assert job["result"]["ok"] == 2 and job["result"]["main_detected"] == [{"account": "000SYN-A", "name": ""}]
    assert database.q_raw("select sum(amount) n from flows where case_id=?", [cid])[0]["n"] == 10_000_000_000_000_000
    row = database.q_raw("select * from import_sessions where import_id=?", [iid])[0]
    assert json.loads(row["result_json"]) == job["result"]
    assert json.loads(row["request_json"])["mapping"] == {**preview["mapping"], "self_account": "本方账号"}
    assert imports.commit(str(cid), iid, body(preview))["started"] is False
    snapshot, name = imports.get_source_file(str(cid), iid)
    with snapshot:
        assert snapshot.read() == CSV and name == "synthetic.csv"
    listing = imports.list_sessions(str(cid))
    assert listing["items"][0]["source_available"] is True
    assert "owner_token" not in listing["items"][0] and "suffix" not in listing["items"][0]
    assert listing["items"][0]["result"]["table_summary"]["members"][0]["member_path"] == "table.csv"

    def reject_host_path_keys(value):
        if isinstance(value, dict):
            assert "path" not in value
            for nested in value.values():
                reject_host_path_keys(nested)
        elif isinstance(value, list):
            for nested in value:
                reject_host_path_keys(nested)

    reject_host_path_keys(listing)
    serialized = json.dumps(listing)
    assert str(database.DATA_DIR) not in serialized and str(database.DB_PATH) not in serialized
    other = make_case("Synthetic foreign source request")
    with pytest.raises(ServiceError) as foreign:
        imports.get_source_file(str(other), iid)
    assert foreign.value.status == 404 and foreign.value.code == "import_not_found"


def test_concurrent_start_freezes_one_request(database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(cid)
    entered, release = threading.Event(), threading.Event()
    original = imports._run
    def blocked(iid):
        entered.set()
        assert release.wait(10)
        original(iid)
    monkeypatch.setattr(imports, "_run", blocked)
    iid = preview["import_id"]
    try:
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(imports.commit, str(cid), iid, body(preview)) for _ in range(2)]
            assert sorted(f.result()["started"] for f in futures) == [False, True]
        assert entered.wait(2)
        frozen = database.q_raw("select request_json,attempt from import_sessions where import_id=?", [iid])[0]
        assert frozen["attempt"] == 1
        preview["mapping"]["summary"] = "交易时间"
        assert json.loads(frozen["request_json"])["mapping"].get("summary") is None
    finally:
        release.set()
    assert poll(cid, iid)["stage"] == "done"
    assert database.q("select count(*) n from batches")[0]["n"] == 1


def test_failure_rolls_back_rows_mains_sources_and_receipt_then_explicit_retry(database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    original = imports._mark_mains
    def fail_after_main(*args):
        original(*args)
        raise RuntimeError("/private/synthetic/raw failure must not leak")
    with monkeypatch.context() as patch:
        patch.setattr(imports, "_mark_mains", fail_after_main)
        imports.commit(str(cid), iid, body(preview))
        failed = poll(cid, iid)
    assert failed["stage"] == "error" and failed["error_code"] == "import_failed"
    assert "/private" not in json.dumps(failed)
    for table in ("flows", "batches", "main_accounts", "import_sources", "flow_sources"):
        assert database.q(f"select count(*) n from {table}")[0]["n"] == 0
    state = database.q_raw("select result_json,expires_at from import_sessions where import_id=?", [iid])[0]
    assert state["result_json"] is None and state["expires_at"] > imports._now() + dt.timedelta(days=29)
    assert imports.repreview(str(cid), iid, body(preview))["import_id"] == iid
    assert imports.commit(str(cid), iid, body(preview))["started"]
    assert poll(cid, iid)["stage"] == "done"
    assert database.q("select attempt from import_sessions where import_id=?", [iid])[0]["attempt"] == 2


def test_source_tamper_rejected_before_preview_commit_or_download(database, make_case):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    path = database.DATA_DIR / "uploads" / f"{iid}.csv"
    path.write_bytes(CSV + b"modified")
    for operation in (lambda: imports.repreview(str(cid), iid, body(preview)),
                      lambda: imports.commit(str(cid), iid, body(preview))):
        with pytest.raises(ServiceError) as error:
            operation()
        assert error.value.code == "source_changed"
    assert database.q("select count(*) n from batches")[0]["n"] == 0


def test_case_surface_isolation_and_legacy_single_bind(database, make_case):
    cid, other = make_case("Owner"), make_case("Other")
    preview = upload(cid)
    iid = preview["import_id"]
    for operation in (lambda: imports.repreview(str(other), iid, body(preview)),
                      lambda: imports.commit(str(other), iid, body(preview)),
                      lambda: imports.legacy_repreview(body(preview, case_id=cid, import_id=iid))):
        with pytest.raises(ServiceError) as error:
            operation()
        assert error.value.code == "import_not_found"
    legacy = upload(None, legacy=True)
    legacy_id = legacy["import_id"]
    with pytest.raises(ServiceError):
        imports.repreview(str(cid), legacy_id, body(legacy))
    imports.legacy_repreview(body(legacy, case_id=cid, import_id=legacy_id))
    with pytest.raises(ServiceError):
        imports.legacy_repreview(body(legacy, case_id=other, import_id=legacy_id))
    assert database.q("select case_id from import_sessions where import_id=?", [legacy_id])[0]["case_id"] == cid
    with pytest.raises(ServiceError):
        imports.legacy_progress(legacy_id)
    assert imports.list_sessions(str(other))["items"] == []


def test_restart_recovery_marks_old_owner_only_and_does_not_replay(database, make_case):
    cid = make_case()
    old = upload(cid)["import_id"]
    current = upload(cid)["import_id"]
    database.x("update import_sessions set stage='importing',owner_token='previous-process',done=1 where import_id=?", [old])
    database.x("update import_sessions set stage='parsing',owner_token=? where import_id=?", [imports.OWNER_TOKEN, current])
    assert imports.recover_interrupted() == {"recovered": 1}
    job = imports.progress(str(cid), old)
    assert job["stage"] == "error" and job["error_code"] == "import_interrupted" and job["recoverable"]
    assert imports.progress(str(cid), current)["stage"] == "parsing"
    assert imports.recover_interrupted() == {"recovered": 0}
    assert database.q("select count(*) n from batches")[0]["n"] == 0


def expire(database, iid):
    database.x("update import_sessions set expires_at=? where import_id=?", [imports._now()-dt.timedelta(seconds=1), iid])


def test_expired_cleanup_protects_live_and_referenced_files_and_unknown_files(database, make_case):
    cid = make_case()
    ready = upload(cid)["import_id"]
    active = upload(cid)["import_id"]
    complete = upload(cid)
    done = complete["import_id"]
    imports.commit(str(cid), done, body(complete))
    assert poll(cid, done)["stage"] == "done"
    for iid in (ready, active, done):
        expire(database, iid)
    database.x("update import_sessions set stage='parsing',owner_token=? where import_id=?", [imports.OWNER_TOKEN, active])
    unknown = database.DATA_DIR / "uploads" / "unregistered.csv"
    unknown.write_bytes(b"preserve")
    dry = imports.cleanup_uploads(str(cid))
    assert dry["dry_run"] and dry["eligible"] == 1 and dry["protected"] == 2 and dry["deleted"] == 0
    result = imports.cleanup_uploads(str(cid), dry_run=False)
    assert result["deleted"] == 1 and result["protected"] == 2
    assert not (database.DATA_DIR / "uploads" / f"{ready}.csv").exists()
    assert all((database.DATA_DIR / "uploads" / f"{iid}.csv").exists() for iid in (active, done))
    assert unknown.read_bytes() == b"preserve"
    assert imports.progress(str(cid), done)["result"]["ok"] == 2


def test_cleanup_failure_is_queued_and_retryable_without_deleting_symlink_target(database, make_case, monkeypatch):
    cid = make_case()
    iid = upload(cid)["import_id"]
    expire(database, iid)
    original_unlink = Path.unlink
    def fail(path, *args, **kwargs):
        if path.name == f"{iid}.csv":
            raise OSError("synthetic unlink denied")
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        result = imports.cleanup_uploads(str(cid), dry_run=False)
    assert result["failed"] == 1 and database.q("select count(*) n from import_file_gc")[0]["n"] == 1
    assert imports.cleanup_uploads(str(cid), dry_run=False)["deleted"] == 1
    assert database.q("select count(*) n from import_file_gc")[0]["n"] == 0
    second = upload(cid)["import_id"]
    expire(database, second)
    source = database.DATA_DIR / "uploads" / f"{second}.csv"
    source.unlink()
    target = database.DATA_DIR / "preserved.txt"
    target.write_text("synthetic preserved")
    source.symlink_to(target)
    assert imports.cleanup_uploads(str(cid), dry_run=False)["failed"] == 1
    assert target.read_text() == "synthetic preserved" and source.is_symlink()


@pytest.mark.parametrize("legacy_without_journal", [False, True])
def test_completed_receipt_survives_batch_deletion_and_source_retention_policy(
        client, database, make_case, monkeypatch, legacy_without_journal):
    cid = make_case()
    with monkeypatch.context() as profile:
        if legacy_without_journal:
            profile.setattr(imports, "TABLE_PROFILE", importer.PARSER_VERSION)
        preview = upload(cid)
    iid = preview["import_id"]
    imports.commit(str(cid), iid, body(preview))
    completed = poll(cid, iid)
    assert completed["stage"] == "done", completed
    receipt = completed["result"]
    removed = client.delete(f"/api/batches/{receipt['batch_id']}", params={"case_id": cid})
    assert removed.status_code == 200
    assert database.q("select count(*) n from flows where case_id=?", [cid]) == [{"n": 0}]
    assert database.q("select count(*) n from flow_sources where source_id=?", [iid]) == [{"n": 0}]
    expire(database, iid)
    cleanup = imports.cleanup_uploads(str(cid), dry_run=False)
    job = imports.progress(str(cid), iid)
    assert job["result"] == receipt and job["stage"] == "done"
    assert imports.commit(str(cid), iid, body(preview))["started"] is False
    # A real batch deletion retires every accepted record. Supported removed
    # records remain portable metadata but no longer require original bytes.
    assert cleanup["deleted"] == 1 and cleanup["protected"] == 0 and not job["source_available"]
    if legacy_without_journal:
        assert database.q("select count(*) n from import_records where source_id=?", [iid]) == [{"n": 0}]
    else:
        assert database.q("select record_no,source_row,disposition,reason_code from import_records where source_id=? order by record_no", [iid]) == [
            {"record_no": 1, "source_row": 2, "disposition": "removed", "reason_code": "flow_deleted"},
            {"record_no": 2, "source_row": 3, "disposition": "removed", "reason_code": "flow_deleted"}]
    with pytest.raises(ServiceError) as error:
        imports.get_source_file(str(cid), iid)
    assert error.value.code == "source_missing"


def test_registered_source_download_checks_hash_and_case_and_rejects_symlink(database, make_case):
    cid, other = make_case("Source owner"), make_case("Other source case")
    preview = upload(cid)
    iid = preview["import_id"]
    imports.commit(str(cid), iid, body(preview))
    assert poll(cid, iid)["stage"] == "done"
    with pytest.raises(ServiceError):
        imports.get_source_file(str(other), iid)
    path = database.DATA_DIR / "uploads" / f"{iid}.csv"
    path.write_bytes(CSV + b"changed")
    with pytest.raises(ServiceError) as error:
        imports.get_source_file(str(cid), iid)
    assert error.value.code == "source_changed"
    path.unlink()
    target = database.DATA_DIR / "synthetic-other.csv"
    target.write_bytes(CSV)
    path.symlink_to(target)
    with pytest.raises(ServiceError) as error:
        imports.get_source_file(str(cid), iid)
    assert error.value.code == "source_missing" and target.read_bytes() == CSV


def test_cleanup_preserves_tampered_registered_file_and_its_retry_queue(database, make_case):
    cid = make_case()
    iid = upload(cid)["import_id"]
    expire(database, iid)
    path = database.DATA_DIR / "uploads" / f"{iid}.csv"
    replacement = b"synthetic unrelated replacement bytes"
    path.write_bytes(replacement)
    result = imports.cleanup_uploads(str(cid), dry_run=False)
    assert result["failed"] == 1 and result["deleted"] == 0
    assert path.read_bytes() == replacement
    assert database.q("select count(*) n from import_file_gc where import_id=?", [iid])[0]["n"] == 1
    assert database.q("select source_state from import_sessions where import_id=?", [iid])[0]["source_state"] == "retained"
    path.write_bytes(CSV)
    assert imports.cleanup_uploads(str(cid), dry_run=False)["deleted"] == 1
    assert database.q("select count(*) n from import_file_gc where import_id=?", [iid])[0]["n"] == 0


def test_listing_and_progress_availability_do_not_rehash_source(database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    imports.commit(str(cid), iid, body(preview))
    assert poll(cid, iid)["stage"] == "done"
    def disallow_hash(*args, **kwargs):
        raise AssertionError("Availability must only inspect filesystem metadata")
    monkeypatch.setattr(imports.hashlib, "sha256", disallow_hash)
    assert imports.progress(str(cid), iid)["source_available"]
    assert imports.list_sessions(str(cid))["items"][0]["source_available"]


def test_commit_returns_before_background_parse_and_does_not_clean_twice(database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    entered, release = threading.Event(), threading.Event()
    parse_threads = []
    original = imports._parse
    def blocked(*args, **kwargs):
        parse_threads.append(threading.current_thread().name)
        entered.set()
        if not release.wait(5):
            raise AssertionError("Commit synchronously waited for parsing")
        return original(*args, **kwargs)
    monkeypatch.setattr(imports, "_parse", blocked)
    try:
        started = imports.commit(str(cid), iid, body(preview))
        assert started["started"] and entered.wait(2)
        assert len(parse_threads) == 1 and parse_threads[0].startswith("import-")
        assert imports.progress(str(cid), iid)["stage"] == "parsing"
    finally:
        release.set()
    assert poll(cid, iid)["stage"] == "done"
    assert len(parse_threads) == 1


def test_nonempty_session_listing_matches_public_contract(client, database, make_case):
    from app.api.models import ImportSessionList
    cid = make_case()
    preview = upload(cid)
    listing = imports.list_sessions(str(cid))
    assert ImportSessionList.model_validate(listing).items[0].import_id == preview["import_id"]
    response = client.get(f"/api/v1/cases/{cid}/imports")
    assert response.status_code == 200, response.text
    assert response.json() == listing


def test_failed_gc_queue_cannot_delete_session_renewed_by_repreview(database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    expire(database, iid)
    original = Path.unlink
    def deny(path, *args, **kwargs):
        if path.name == f"{iid}.csv":
            raise OSError("synthetic failed cleanup")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny)
        assert imports.cleanup_uploads(str(cid), dry_run=False)["failed"] == 1
    queue = database.q_raw("select * from import_file_gc where import_id=?", [iid])[0]
    assert imports.repreview(str(cid), iid, body(preview))["import_id"] == iid
    assert database.q("select count(*) n from import_file_gc where import_id=?", [iid])[0]["n"] == 0
    # Defensive execution guard also rejects a stale queue reintroduced after
    # renewal, rather than treating its old delete_after as current authority.
    database.x("insert into import_file_gc(import_id,suffix,sha256,delete_after) values (?,?,?,?)",
               [iid, queue["suffix"], queue["sha256"], queue["delete_after"]])
    result = imports.cleanup_uploads(str(cid), dry_run=False)
    assert result["deleted"] == 0 and result["protected"] == 1
    assert (database.DATA_DIR / "uploads" / f"{iid}.csv").read_bytes() == CSV


def test_download_snapshot_remains_verified_bytes_after_canonical_path_replaced(database, make_case):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    imports.commit(str(cid), iid, body(preview))
    assert poll(cid, iid)["stage"] == "done"
    snapshot, name = imports.get_source_file(str(cid), iid)
    path = database.DATA_DIR / "uploads" / f"{iid}.csv"
    target = database.DATA_DIR / "replacement.csv"
    target.write_bytes(b"synthetic unverified replacement")
    path.unlink()
    path.symlink_to(target)
    with snapshot:
        assert snapshot.read() == CSV and name == "synthetic.csv"
    assert snapshot.closed
    with pytest.raises(ServiceError):
        imports.get_source_file(str(cid), iid)


def test_background_parser_reads_verified_snapshot_after_canonical_path_replaced(database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(cid)
    iid = preview["import_id"]
    path = database.DATA_DIR / "uploads" / f"{iid}.csv"
    target = database.DATA_DIR / "replacement.csv"
    target.write_bytes(CSV.replace(b"9999999999999999.99", b"1.00"))
    original = imports._parse
    def replace_then_parse(source, *args, **kwargs):
        assert hasattr(source, "read") and not isinstance(source, Path)
        path.unlink()
        path.symlink_to(target)
        return original(source, *args, **kwargs)
    monkeypatch.setattr(imports, "_parse", replace_then_parse)
    imports.commit(str(cid), iid, body(preview))
    job = poll(cid, iid)
    assert job["stage"] == "done", job
    assert database.q_raw("select sum(amount) n from flows where case_id=?", [cid])[0]["n"] == 10_000_000_000_000_000
    assert path.is_symlink()
