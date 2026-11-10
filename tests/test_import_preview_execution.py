"""Public preview execution uses synthetic request files and controlled barriers."""
import asyncio
import hashlib
import io
import socket
from contextlib import contextmanager
from pathlib import Path
import threading
import zipfile

import anyio
import httpx
import pytest
import uvicorn
from starlette.datastructures import UploadFile

from app import import_preview_cache, table_archive
from app.main import app
from app.services import imports


CSV = ("交易流水号,date,payer_account,payee_account,amount,currency\n"
       "SYN-PREVIEW,2026-01-01 10:00:00.123456,000001,SYN-B,1.01,CNY\n").encode()


@pytest.fixture(autouse=True)
def private_cache():
    import_preview_cache.discard()
    yield
    import_preview_cache.discard()


def request_files(cid, directory=False, legacy=False):
    if legacy:
        url = f"/api/import/{'directory/' if directory else ''}preview?case_id={cid}"
    else:
        url = f"/api/v1/cases/{cid}/imports/{'directory/' if directory else ''}preview"
    files = [("files", ("folder/synthetic.csv", CSV))] if directory else {"file": ("synthetic.csv", CSV)}
    return url, files


def block_parse(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    parse = imports._parse
    calls = []

    def blocked(source, *args, **kwargs):
        calls.append(source)
        entered.set()
        assert release.wait(5), "test failed to release synthetic parser"
        return parse(source, *args, **kwargs)

    monkeypatch.setattr(imports, "_parse", blocked)
    return entered, release, calls


async def entered_worker(entered):
    assert await asyncio.to_thread(entered.wait, 4), "preview worker did not enter parser"


def no_financial_write(database):
    for table in ("flows", "batches", "import_sources", "flow_sources", "import_records"):
        assert database.q_raw(f"select count(*) n from {table}") == [{"n": 0}]


def no_unowned_files(database):
    assert not list(database.DATA_DIR.glob("uploads/*"))
    assert not list(database.DATA_DIR.glob("funds-trace-*"))
    assert import_preview_cache._slot is None


@pytest.mark.parametrize("directory,legacy", [(False, False), (True, False), (False, True), (True, True)])
def test_preview_allows_light_request_and_rejects_another_large_preview(
        database, make_case, monkeypatch, directory, legacy):
    cid = make_case("Synthetic preview admission")
    entered, release, calls = block_parse(monkeypatch)
    # A safety release also lets the original blocking implementation fail
    # normally, without hanging the test process's event loop.
    safety = threading.Timer(3, release.set)
    safety.start()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            url, files = request_files(cid, directory, legacy)
            pending = asyncio.create_task(client.post(url, files=files))
            try:
                await entered_worker(entered)
                light = await client.get("/api/v1/cases")
                assert light.status_code == 200
                assert not release.is_set(), "light request waited for synchronous preview"
                assert not pending.done()
                other_url, other_files = request_files(cid, not directory, not legacy)
                rejected = await client.post(other_url, files=other_files)
                assert rejected.status_code == 409, rejected.text
                assert len(calls) == 1
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200, response.text
            assert response.json()["automatic"]

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        safety.cancel()
    assert database.q_raw("select stage,attempt from import_sessions") == [{"stage": "ready", "attempt": 0}]
    no_financial_write(database)


def test_preview_parses_existing_request_file_without_async_full_read(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic seekable preview")
    handles, sources = [], []
    initialize, parse = UploadFile.__init__, imports._parse

    def observed(upload, *args, **kwargs):
        initialize(upload, *args, **kwargs)
        handles.append(upload.file)

    async def full_read_forbidden(*args, **kwargs):
        raise AssertionError("preview must use the existing seekable request file")

    def parsed(source, *args, **kwargs):
        sources.append(source)
        return parse(source, *args, **kwargs)

    monkeypatch.setattr(UploadFile, "__init__", observed)
    monkeypatch.setattr(UploadFile, "read", full_read_forbidden)
    monkeypatch.setattr(imports, "_parse", parsed)
    url, files = request_files(cid)
    response = client.post(url, files=files)
    assert response.status_code == 200, response.text
    assert len(handles) == 1 and sources == handles and handles[0].closed
    view = response.json()
    session = database.q_raw("select sha256,size_bytes from import_sessions")[0]
    assert session == {"sha256": hashlib.sha256(CSV).hexdigest(), "size_bytes": len(CSV)}
    assert (database.DATA_DIR / "uploads" / (view["import_id"] + ".csv")).read_bytes() == CSV


def test_directory_streaming_preserves_original_archive_bytes(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic bounded directory")
    members = [("folder/synthetic.csv", CSV), ("folder/notes.bin", b"synthetic attachment")]
    expected = io.BytesIO()
    with zipfile.ZipFile(expected, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, payload in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)

    async def full_read_forbidden(*args, **kwargs):
        raise AssertionError("directory must copy request members in bounded blocks")

    monkeypatch.setattr(UploadFile, "read", full_read_forbidden)
    response = client.post(f"/api/v1/cases/{cid}/imports/directory/preview",
                           files=[("files", member) for member in members])
    assert response.status_code == 200, response.text
    path = database.DATA_DIR / "uploads" / (response.json()["import_id"] + ".zip")
    assert path.read_bytes() == expected.getvalue()
    session = database.q_raw("select sha256,size_bytes,stage from import_sessions")[0]
    assert session == {"sha256": hashlib.sha256(expected.getvalue()).hexdigest(),
                       "size_bytes": len(expected.getvalue()), "stage": "ready"}
    assert not list(database.DATA_DIR.glob("funds-trace-preview-*"))


def test_returned_preview_metadata_cannot_mutate_cached_plan_or_money(client, database, make_case):
    cid = make_case("Synthetic preview metadata ownership")
    # Use the actual service return, before HTTP serialization detaches aliases.
    upload = UploadFile(io.BytesIO(CSV), filename="synthetic.csv")
    try:
        view = asyncio.run(imports.upload(str(cid), upload))
    finally:
        upload.file.close()
    frozen = {"mode": view["mode"], "mapping": dict(view["mapping"]), "mark_accounts": []}
    assert import_preview_cache._slot is not None
    original_seal = import_preview_cache.stamp(import_preview_cache._slot[1])
    view["mapping"]["summary"] = "date"
    view["stats"]["ok"] = 999
    view["stats"]["bad_examples"].append({"reason": "synthetic caller mutation"})
    view["table_summary"]["members"][0]["member_path"] = "synthetic altered member"
    view["table_summary"]["documents"].append({"synthetic": "caller mutation"})
    view["table_summary"]["reason_counts"]["synthetic_mutation"] = 999
    assert import_preview_cache.stamp(import_preview_cache._slot[1]) == original_seal
    base = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    # The existing helper explicitly commits and waits for done.
    from test_import_lifecycle_api import complete
    job = complete(client, cid, {"import_id": view["import_id"], **frozen})
    assert job["result"]["ok"] == 1
    assert job["result"]["table_summary"]["members"][0]["member_path"] == "table.csv"
    facts = client.get(f"/api/v1/cases/{cid}/flows").json()
    assert facts["total"] == 1 and facts["items"][0]["amount"] == "1.01"
    assert facts["items"][0]["payer_account"] == "000001"
    no_metadata = database.q_raw("select details_json from import_records")[0]["details_json"]
    assert "synthetic caller mutation" not in no_metadata


@pytest.mark.parametrize("cancel_kind", ["asyncio", "anyio"])
def test_cancelled_public_preview_keeps_handle_until_worker_cleans_up(
        database, make_case, monkeypatch, cancel_kind):
    cid = make_case("Synthetic cancelled preview")
    entered, release, sources = block_parse(monkeypatch)
    safety = threading.Timer(3, release.set)
    safety.start()

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            scopes = []

            async def request():
                with anyio.CancelScope() as scope:
                    scopes.append(scope)
                    url, files = request_files(cid)
                    await client.post(url, files=files)

            pending = asyncio.create_task(request())
            try:
                await entered_worker(entered)
                if cancel_kind == "anyio":
                    scopes[0].cancel()
                else:
                    pending.cancel()
                await asyncio.sleep(.01)
                assert not sources[0].closed
                assert not pending.done()
                if cancel_kind == "asyncio":
                    pending.cancel()  # Repeated Task.cancel must also keep ownership.
                url, files = request_files(cid, directory=True)
                assert (await client.post(url, files=files)).status_code == 409
            finally:
                release.set()
                if cancel_kind == "asyncio":
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                else:
                    await pending
            assert sources[0].closed
            assert database.q_raw("select count(*) n from import_sessions") == [{"n": 0}]
            no_unowned_files(database)
            # Admission is released only after the cancelled worker is done.
            url, files = request_files(cid)
            response = await client.post(url, files=files)
            assert response.status_code == 200, response.text

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        safety.cancel()
    assert database.q_raw("select count(*) n from import_sessions") == [{"n": 1}]
    no_financial_write(database)


@pytest.mark.parametrize("failure", ["upload_limit", "member_limit", "directory_limit", "bad_zip", "retained_write", "registration", "preview_result", "deleted_case"])
def test_preview_failures_leave_no_session_source_or_financial_write(
        client, database, make_case, monkeypatch, failure):
    cid = make_case("Synthetic failed preview")
    reached = []
    url, files = request_files(cid, directory=failure in {"member_limit", "directory_limit"})
    if failure == "upload_limit":
        monkeypatch.setattr(imports, "_max_upload", len(CSV) - 1)
    elif failure == "member_limit":
        monkeypatch.setattr(table_archive, "MAX_MEMBER_BYTES", len(CSV) - 1)
    elif failure == "directory_limit":
        monkeypatch.setattr(imports, "_max_upload", len(CSV) * 2 - 1)
        files.append(("files", ("other.csv", CSV)))
    elif failure == "bad_zip":
        files = {"file": ("synthetic.zip", b"synthetic invalid archive")}
    elif failure == "registration":
        execute = database.x

        def fail_registration(sql, *args, **kwargs):
            if "insert into import_sessions" in sql:
                reached.append(failure)
                raise OSError("synthetic registration failure")
            return execute(sql, *args, **kwargs)

        monkeypatch.setattr(database, "x", fail_registration)
    elif failure == "preview_result":
        def fail_result(*args, **kwargs):
            reached.append(failure)
            raise OSError("synthetic preview scratch failure")

        monkeypatch.setattr(imports, "_preview_result", fail_result)
    elif failure == "retained_write":
        opened = Path.open

        @contextmanager
        def fail_write(path, mode="r", *args, **kwargs):
            with opened(path, mode, *args, **kwargs) as stream:
                if mode != "xb":
                    yield stream
                    return

                class PartialWrite:
                    def write(self, block):
                        reached.append(failure)
                        stream.write(block[:len(block) // 2])
                        raise OSError("synthetic partial original write")

                yield PartialWrite()

        monkeypatch.setattr(Path, "open", fail_write)
    elif failure == "deleted_case":
        parse = imports._parse

        def delete_after_parse(*args, **kwargs):
            result = parse(*args, **kwargs)
            assert client.delete(f"/api/cases/{cid}").status_code == 200
            return result

        monkeypatch.setattr(imports, "_parse", delete_after_parse)
    if failure in {"retained_write", "registration", "preview_result"}:
        response = client.post(url, files=files)
        assert response.status_code == 500, response.text
        assert response.json()["error"]["code"] == "internal_error"
        assert reached == [failure]
    else:
        assert client.post(url, files=files).status_code in {400, 404}
    assert database.q_raw("select count(*) n from import_sessions") == [{"n": 0}]
    no_financial_write(database)
    no_unowned_files(database)


def test_cancel_after_registration_keeps_recoverable_ready_source(database, make_case, monkeypatch):
    cid = make_case("Synthetic late preview cancellation")
    entered, release = threading.Event(), threading.Event()
    result = imports._preview_result

    def registered(*args, **kwargs):
        entered.set()
        assert release.wait(4)
        return result(*args, **kwargs)

    monkeypatch.setattr(imports, "_preview_result", registered)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            url, files = request_files(cid)
            pending = asyncio.create_task(client.post(url, files=files))
            try:
                await entered_worker(entered)
                pending.cancel()
                await asyncio.sleep(.01)
                assert not pending.done()
            finally:
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            listing = await client.get(f"/api/v1/cases/{cid}/imports")
            assert listing.status_code == 200
            item, = listing.json()["items"]
            assert item["stage"] == "ready" and item["attempt"] == 0 and item["source_available"]

    asyncio.run(scenario())
    retained, = (database.DATA_DIR / "uploads").iterdir()
    assert retained.read_bytes() == CSV
    no_financial_write(database)


def test_real_client_disconnect_leaves_only_registered_ready_source(database, make_case, monkeypatch):
    cid = make_case("Synthetic disconnected preview")
    entered, release, sources = block_parse(monkeypatch)
    closed = threading.Event()
    close = UploadFile.close

    async def observe_close(upload):
        await close(upload)
        closed.set()

    monkeypatch.setattr(UploadFile, "close", observe_close)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, loop="asyncio", lifespan="off", access_log=False, log_level="critical"))
        serving = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        serving.start()
        try:
            url, files = request_files(cid)
            request = httpx.Request("POST", f"http://127.0.0.1:{port}{url}", files=files)
            content = request.read()
            headers = b"\r\n".join(key + b": " + value for key, value in request.headers.raw)
            with socket.create_connection(("127.0.0.1", port), timeout=3) as peer:
                peer.sendall(b"POST " + url.encode() + b" HTTP/1.1\r\n" + headers + b"\r\n\r\n" + content)
                assert entered.wait(3)
            # Uvicorn does not cancel this ordinary endpoint on TCP disconnect.
            # It finishes registration, closes the spool and never starts commit.
            assert not sources[0].closed
            release.set()
            assert closed.wait(3)
            assert sources[0].closed
            session, = database.q_raw("select stage,attempt,sha256,size_bytes from import_sessions")
            assert session == {"stage": "ready", "attempt": 0,
                               "sha256": hashlib.sha256(CSV).hexdigest(), "size_bytes": len(CSV)}
            retained, = (database.DATA_DIR / "uploads").iterdir()
            assert retained.read_bytes() == CSV
            no_financial_write(database)
        finally:
            release.set()
            server.should_exit = True
            serving.join(5)
            assert not serving.is_alive()
