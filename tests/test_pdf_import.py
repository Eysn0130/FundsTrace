"""Synthetic PDF source/import boundary. The CLI protocol matches MinerU 4.2.3."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import threading
import time

import httpx
import pytest

from app import import_originals, pdf_import, windows_import_job
from app.main import app
from test_feedback_import_api import complete
from test_feedback_package import _archive, _members, _mutate


MEMBER = "document.pdf"
WINDOWS = sys.platform == "win32"


def _mineru_launcher():
    return (windows_import_job, "start") if WINDOWS else (pdf_import.subprocess, "Popen")


def pdf_bytes():
    """Stable one-page PDF with a complete xref, independent of the OCR fixture."""
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
    )
    output = io.BytesIO(b"%PDF-1.4\n")
    output.seek(0, 2)
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = output.tell()
    output.write(b"xref\n0 4\n0000000000 65535 f \n")
    for offset in offsets:
        output.write(f"{offset:010d} 00000 n \n".encode())
    output.write(f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return output.getvalue()


PAYLOAD = pdf_bytes()


def table(index, html):
    box = [0.12, 0.10, 0.90, 0.63]
    return {"type": "table", "index": index, "bbox": box,
            "content": [{"type": "table_body", "index": index, "bbox": box, "content": html}]}


def middle(*blocks):
    return {
        "schema": "docvortex.middle", "schema_version": "2.0", "is_full_document": True,
        "metadata": {"file_suffix": "pdf", "producer": {"name": "mineru", "version": "4.2.3"},
                     "document": {"page_count": len(blocks), "page_count_kind": "physical"}},
        "pages": [{"page_idx": idx, "blocks": values} for idx, values in enumerate(blocks)],
    }


def result():
    return middle([
        {"type": "paragraph_title", "content": ["忽略的标题"]},
        table(2, "<table><tbody><tr><th>付款账号</th><th>金额</th><th>附注</th></tr>"
                 "<tr><td>00001234</td><td>1,231.01</td><td></td></tr></tbody></table>"),
        table(3, "<table><tr><td>上一页的第二张表</td></tr></table>"),
    ], [], [
        table(2, "<table><tr><td></td><td></td></tr>"
                 "<tr><td>00009876</td><td>-7.01</td><td>&lt;合成&gt;<br>两行</td></tr></table>"),
    ])


@pytest.fixture(autouse=True)
def no_mineru(monkeypatch):
    monkeypatch.delenv("FUND_MINERU_EXECUTABLE", raising=False)
    monkeypatch.delenv("FUND_MINERU_MODEL_DIR", raising=False)


@pytest.fixture
def mineru(monkeypatch, tmp_path):
    exe = tmp_path / ("mineru-kit.exe" if WINDOWS else "mineru-kit")
    exe.touch()
    asset_path = tmp_path / "合成模型"
    asset_path.mkdir()
    monkeypatch.setenv("FUND_MINERU_EXECUTABLE", str(exe))
    monkeypatch.setenv("FUND_MINERU_MODEL_DIR", str(asset_path))
    monkeypatch.setenv("DOCVORTEX_REMOTE_URL", "https://synthetic.invalid")
    monkeypatch.setenv("HF_TOKEN", "synthetic-test-only")
    harness = SimpleNamespace(calls=[], stopped=[], code=0, output=lambda: json.dumps(result()).encode())

    def launch(command, *, cwd, env, **options):
        assert command == [str(exe), "parse", str(cwd / "source.pdf"), "--pages",
                           f"1-{pdf_import.MAX_PAGES + 1}", "--tier", "basic",
                           "--format", "middle_json", "-o", str(cwd / "middle.json")]
        assert Path(command[2]).read_bytes() == PAYLOAD
        assert cwd == Path(env["MINERU_HOME"]) == Path(env["TMPDIR"])
        assert json.loads(Path(env["MINERU_CONFIG"]).read_text(encoding="utf-8")) == {
            "model": {"base_dir": str(asset_path)}}
        assert {key: env[key] for key in ("MINERU_MODEL_SOURCE", "MINERU_MODEL_SMALL_BACKEND",
                                         "MINERU_MODEL_ONNX_DEVICE", "MINERU_TELEMETRY_DISABLED", "HF_HUB_OFFLINE")} == {
            "MINERU_MODEL_SOURCE": "local", "MINERU_MODEL_SMALL_BACKEND": "onnx", "MINERU_MODEL_ONNX_DEVICE": "cpu",
            "MINERU_TELEMETRY_DISABLED": "1", "HF_HUB_OFFLINE": "1",
        }
        assert not {"HF_TOKEN", "DOCVORTEX_REMOTE_URL"} & env.keys()
        assert (env["MINERU_PROCESSING_WINDOW_SIZE"], env["MINERU_PDF_RENDER_THREADS"], env["OMP_NUM_THREADS"]) == (
            "4", "1", "2")
        if WINDOWS:
            assert options == {}
        else:
            sink = pdf_import.subprocess.DEVNULL
            assert options == {"stdin": sink, "stdout": sink, "stderr": sink, "start_new_session": True}
        assert not harness.calls or all(not path.exists() for path in harness.calls)
        harness.calls.append(cwd)
        if data := harness.output():
            Path(command[-1]).write_bytes(data)
        def stop():
            assert cwd.is_dir()
            harness.stopped.append(cwd)

        return SimpleNamespace(returncode=harness.code, poll=lambda: harness.code, stop=stop)

    owner, name = _mineru_launcher()
    monkeypatch.setattr(owner, name, launch)
    yield harness
    assert harness.stopped == (harness.calls if WINDOWS else [])


def upload(client, case, payload=PAYLOAD, name="合成资料.pdf"):
    response = client.post(f"/api/v1/cases/{case}/imports/preview",
                           files={"file": (name, payload, "application/pdf")})
    assert response.status_code == 200, response.text
    return response.json()


def review(client, case, source_id, **params):
    response = client.get(f"/api/v1/cases/{case}/imports/{source_id}/review",
                          params={"member_path": MEMBER, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_pdf_public_import_replays_candidates_once_and_never_writes_flows(client, database, make_case, mineru, monkeypatch):
    case, other = make_case(), make_case("Synthetic unrelated")
    base = f"/api/v1/cases/{case}"
    preview = upload(client, case, name="合成资料.PDF")
    import_id = preview["import_id"]
    url = f"{base}/imports/{import_id}"
    original = f"{base}/sources/{import_id}"
    intent = {"mode": "B", "mapping": {}}
    summary = preview["table_summary"]
    assert preview["automatic"] is True and preview["preview"] == [] and preview["mapping"] == {}
    assert preview["stats"]["ok"] == 0 and preview["self_accounts_in_file"] == []
    assert summary["profile"] == "pdf-review-v1"
    assert summary["accepted"] == 0 and summary["unresolved"] == 4
    assert summary["members"] == [{"member_path": MEMBER, "role": "pdf", "encoding": "pdf",
                                   "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "size_bytes": len(PAYLOAD), "row_count": 4}]
    assert summary["documents"][0]["pdf_status"] == "pdf_candidate"
    assert (summary["documents"][0]["pdf_total_pages"], summary["documents"][0]["pdf_candidate_pages"]) == (3, 2)
    assert len(mineru.calls) == 1 and not mineru.calls[0].exists()
    assert client.post(f"/api/v1/cases/{other}/imports/{import_id}/preview", json=intent).status_code == 404
    assert client.post(f"/api/v1/cases/{other}/imports/{import_id}/commit", json=intent).status_code == 404
    for mutation in ({"mode": "A"}, {"mapping": {"amount": "金额"}}, {"reference_policy": "transaction-reference-v1"}):
        forbidden = client.post(url + "/preview", json={**intent, **mutation})
        assert forbidden.status_code == 422 and forbidden.json()["error"]["code"] == "invalid_pdf_mapping"
    for mutation in ({"mark_accounts": ["SYN-ACCOUNT"]}, {"self_accounts": ["SYN-ACCOUNT"]}, {"mapping": {"amount": "金额"}}):
        assert client.post(url + "/commit", json={**intent, **mutation}).status_code == 422
    with database._lock:
        database._con.close()
        database._con = None
    rechecked = client.post(url + "/preview", json=intent)
    assert rechecked.status_code == 200, rechecked.text
    assert rechecked.json()["table_summary"] == summary
    job = complete(client, case, rechecked.json())
    assert job["stage"] == "done", job
    receipt = job["result"]
    assert receipt["ok"] == receipt["rows_dup"] == 0 and receipt["skipped"] == 4
    assert receipt["table_summary"]["effective_added"] == 0 and receipt["main_detected"] == []
    assert len(mineru.calls) == 1
    assert client.post(url + "/commit", json=intent).json()["started"] is False
    assert client.get(base + "/flows").json()["total"] == 0
    assert client.get(base + "/stats").json()["import_coverage"]["unresolved_records"] == 0
    assert client.get(url + "/report", params={"member_path": MEMBER}).status_code == 404

    def never_decode(*_args, **_kwargs):
        pytest.fail("PDF review must not open the original as a table")

    monkeypatch.setattr(import_originals, "read_cells", never_decode)
    page = review(client, case, import_id)
    assert page["original_error"] is None
    assert page["counts"] == {"all": 4, "review": 4, "accepted": 0}
    points = []
    for number, row in enumerate(page["items"], 1):
        assert (row["record_no"], row["source_row"], row["reason_code"]) == (number, number, "pdf_candidate")
        assert row["flow_id"] is row["original"] is row["preview"] is None
        assert row["disposition"] == "unresolved"
        details = row["details"]
        points.append((details["pdf_page"], details["pdf_table"], details["pdf_row"],
                       json.loads(details["pdf_candidate_cells"])))
    assert points == [
        ("1", "1", "1", ["付款账号", "金额", "附注"]),
        ("1", "1", "2", ["00001234", "1,231.01", ""]),
        ("1", "2", "1", ["上一页的第二张表"]),
        ("3", "1", "2", ["00009876", "-7.01", "<合成>\n两行"]),
    ]
    assert review(client, case, import_id, source_row=4, page_size=1)["page"] == 4
    assert review(client, case, import_id, view="accepted")["items"] == []
    assert client.get(original + "/file").content == PAYLOAD
    assert client.get(f"/api/v1/cases/{other}/sources/{import_id}/file").status_code == 404
    assert client.get(f"/api/v1/cases/{other}/imports/{import_id}/review",
                      params={"member_path": MEMBER}).status_code == 404
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(url).json()["result"] == receipt
    assert review(client, case, import_id)["items"] == page["items"]


@pytest.mark.parametrize("html,expected", [
    (
        '<table><tbody><tr><td rowspan="2">A</td><td colspan="2">B1</td><td rowspan="2">D</td></tr>'
        '<tr><td>B2</td><td>C2</td></tr></tbody></table>',
        [("1", ["A", "B1", "", "D"]), ("2", ["", "B2", "C2", ""])],
    ),
    (
        '<table><tr><th rowspan="2" colspan="2">合计</th></tr><tr></tr>'
        '<tr><td colspan="2"></td><td>1,201.05</td></tr></table>',
        [("1", ["合计", ""]), ("3", ["", "", "1,201.05"])],
    ),
], ids=["rowspan-and-trailing-slot", "two-dimensional-span-and-empty-physical-row"])
def test_pdf_spans_keep_candidate_text_in_its_physical_column(client, make_case, mineru, html, expected):
    case = make_case()
    mineru.output = lambda: json.dumps(middle([table(0, html)])).encode()
    preview = upload(client, case)
    job = complete(client, case, preview)
    assert job["stage"] == "done" and job["result"]["ok"] == 0, job
    items = review(client, case, preview["import_id"])["items"]
    assert [item["source_row"] for item in items] == [1, 2]
    assert all(item["flow_id"] is item["original"] is None and item["disposition"] == "unresolved" for item in items)
    assert [(item["details"]["pdf_row"], json.loads(item["details"]["pdf_candidate_cells"])) for item in items] == expected


@pytest.mark.parametrize("html,reason", [
    ('<table><tr><td>First</td><td rowspan="2">Reserved</td></tr>'
     '<tr><td colspan="2">Overlap</td></tr></table>', "pdf_result_invalid"),
    ('<table><tr><td rowspan="2">Past the last row</td></tr></table>', "pdf_result_invalid"),
    ('<table><thead><tr><th rowspan="2">Past the header</th></tr></thead>'
     '<tbody><tr><td>First body row</td></tr></tbody></table>', "pdf_result_invalid"),
    ('<table><tr><td rowspan="0">Unsupported row group span</td></tr></table>', "pdf_result_invalid"),
    ('<table><tr><td colspan="1.5">Malformed</td></tr></table>', "pdf_result_invalid"),
    ('<table><tr><td colspan="2" colspan="1">Duplicate</td></tr></table>', "pdf_result_invalid"),
    ('<table><tr><td colspan="32">Full width</td><td>Exceeds it</td></tr></table>', "pdf_result_limit"),
    ('<table><tr><td rowspan="1001">Too tall</td></tr></table>', "pdf_result_limit"),
], ids=["overlap", "past-table", "cross-row-group", "zero-rowspan", "fractional", "duplicate",
        "column-bound", "row-bound"])
def test_pdf_spans_reject_invalid_or_over_limit_grid_before_publication(client, make_case, mineru, html, reason):
    case = make_case()
    mineru.output = lambda: json.dumps(middle([table(0, "<table><tr><td>Earlier candidate</td></tr></table>"),
                                               table(1, html)])).encode()
    preview = upload(client, case)
    assert preview["table_summary"]["reason_counts"] == {reason: 1}
    job = complete(client, case, preview)
    assert job["stage"] == "done" and job["result"]["ok"] == 0
    item, = review(client, case, preview["import_id"])["items"]
    assert item["reason_code"] == reason and item["details"] == {}
    assert client.get(f"/api/v1/cases/{case}/sources/{preview['import_id']}/file").content == PAYLOAD


@pytest.mark.parametrize("state,expected", [
    ("missing_tool", "pdf_tool_unavailable"),
    ("bad_pdf", "pdf_invalid"),
    ("large_pdf", "pdf_input_limit"),
    ("invalid_result", "pdf_result_invalid"),
    ("wrong_producer", "pdf_result_invalid"),
    ("process_failure", "pdf_ocr_failed"),
    ("large_result", "pdf_result_limit"),
    ("too_many_pages", "pdf_result_limit"),
    ("partial_result", "pdf_result_limit"),
    ("too_many_rows", "pdf_result_limit"),
    ("no_table", "pdf_no_tables"),
])
def test_pdf_issue_is_retained_without_a_fabricated_source_position(client, make_case, mineru, monkeypatch, state, expected):
    case = make_case()
    payload = PAYLOAD
    if state == "missing_tool":
        monkeypatch.delenv("FUND_MINERU_EXECUTABLE")
    elif state == "bad_pdf":
        payload = b"synthetic non-PDF file"
    elif state == "large_pdf":
        monkeypatch.setattr(pdf_import, "MAX_INPUT_BYTES", len(payload) - 1)
    elif state == "invalid_result":
        mineru.output = lambda: b"{no valid result"
    elif state == "wrong_producer":
        wrong = result()
        wrong["metadata"]["producer"]["version"] = "99.0"
        mineru.output = lambda: json.dumps(wrong).encode()
    elif state == "process_failure":
        mineru.code = 1
    elif state == "large_result":
        monkeypatch.setattr(pdf_import, "MAX_RESULT_BYTES", 16)
    elif state == "too_many_pages":
        monkeypatch.setattr(pdf_import, "MAX_PAGES", 1)
    elif state == "partial_result":
        mineru.output = lambda: json.dumps({**result(), "is_full_document": False}).encode()
    elif state == "too_many_rows":
        monkeypatch.setattr(pdf_import, "MAX_ROWS", 1)
    elif state == "no_table":
        mineru.output = lambda: json.dumps(middle([])).encode()
    preview = upload(client, case, payload)
    assert preview["table_summary"]["reason_counts"] == {expected: 1}
    document = preview["table_summary"]["documents"][0]
    if state == "no_table":
        assert (document["pdf_total_pages"], document["pdf_candidate_pages"]) == (1, 0)
    else:
        assert "pdf_total_pages" not in document and "pdf_candidate_pages" not in document
    assert preview["table_summary"]["members"][0]["sha256"] == hashlib.sha256(payload).hexdigest()
    job = complete(client, case, preview)
    assert job["stage"] == "done" and job["result"]["ok"] == 0, job
    import_id = preview["import_id"]
    item, = review(client, case, import_id)["items"]
    assert item["reason_code"] == expected and item["original"] is None and item["details"] == {}
    assert client.get(f"/api/v1/cases/{case}/sources/{import_id}/file").content == payload
    assert len(mineru.calls) == (0 if state in ("missing_tool", "bad_pdf", "large_pdf") else 1)
    assert all(not path.exists() for path in mineru.calls)


def test_legacy_direct_pdf_keeps_bound_session_and_reports_via_shared_review(client, make_case, mineru):
    case = make_case()
    route = "/api/import/preview"
    files = {"file": ("synthetic.pdf", PAYLOAD)}
    assert client.post(route, files=files).status_code == 422
    response = client.post(route, params={"case_id": case}, files=files)
    assert response.status_code == 200, response.text
    view = response.json()
    body = {"case_id": case, "mode": "B", "mapping": {}, "import_id": view["import_id"]}
    assert client.post(f"/api/v1/cases/{case}/imports/{view['import_id']}/commit",
                       json={"mode": "B", "mapping": {}}).status_code == 404
    replayed = client.post("/api/import/repreview", json=body)
    assert replayed.status_code == 200 and replayed.json()["table_summary"] == view["table_summary"], replayed.text
    rejected = client.post("/api/import/commit", json={**body, "mark_accounts": "synthetic"})
    assert rejected.status_code == 422, rejected.text
    response = client.post("/api/import/commit", json=body)
    assert response.status_code == 200 and response.json()["started"], response.text
    for _ in range(200):
        job = client.get("/api/import/progress", params={"case_id": case, "import_id": view["import_id"]}).json()
        if job["stage"] in {"done", "error"}:
            break
        time.sleep(0.02)
    assert job["stage"] == "done" and job["result"]["ok"] == 0, job
    assert len(mineru.calls) == 1
    assert review(client, case, view["import_id"])["total"] == 4


def test_pdf_frozen_review_binding_and_original_sha_are_checked(client, database, make_case, mineru):
    case, other = make_case(), make_case("Synthetic separate case")
    first, second = upload(client, case), upload(client, other)
    query = "select request_json from import_sessions where import_id=?"
    request = json.loads(database.q_raw(query, [first["import_id"]])[0]["request_json"])
    other_request = json.loads(database.q_raw(query, [second["import_id"]])[0]["request_json"])
    request["_pdf_plan"] = other_request["_pdf_plan"]
    database.x("update import_sessions set request_json=? where import_id=?", [json.dumps(request), first["import_id"]])
    intent = {"mode": "B", "mapping": {}}
    response = client.post(f"/api/v1/cases/{case}/imports/{first['import_id']}/preview", json=intent)
    assert response.status_code == 409 and response.json()["error"]["code"] == "pdf_plan_changed"
    job = complete(client, case, first)
    assert job["stage"] == "error" and job["error_code"] == "import_integrity_failed", job
    assert not database.q_raw("select 1 from import_sources where case_id=?", [case])
    path = Path(database.DATA_DIR) / "uploads" / (second["import_id"] + ".pdf")
    path.write_bytes(PAYLOAD[:-1] + b" ")
    response = client.post(f"/api/v1/cases/{other}/imports/{second['import_id']}/commit", json=intent)
    assert response.status_code == 409 and response.json()["error"]["code"] == "source_changed", response.text
    assert len(mineru.calls) == 2


def test_pdf_package_and_withdraw_preserve_a_source_only_journal(client, database, make_case, mineru):
    case = make_case()
    view = upload(client, case)
    source_id = view["import_id"]
    base = f"/api/v1/cases/{case}"
    job = complete(client, case, view)
    assert job["stage"] == "done", job
    receipt = job["result"]
    before = review(client, case, source_id)
    res = client.delete(f"{base}/sources/{source_id}/operation",
                        params={"expected_batch_id": receipt["batch_id"]})
    assert res.status_code == 200, res.text
    assert client.get(f"{base}/sources/{source_id}/metadata").json()["withdrawn"] is True
    assert client.get(f"{base}/imports/{source_id}").json()["result"] == receipt
    assert review(client, case, source_id)["items"] == before["items"]
    assert client.get(f"{base}/sources/{source_id}/file").content == PAYLOAD
    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    source, = database.q_raw("select source_id,origin,parser_version from import_sources where case_id=?", [restored_case])
    assert source["source_id"] != source_id and (source["origin"], source["parser_version"]) == ("package", "pdf-review-v1")
    returned = review(client, restored_case, source["source_id"])
    assert returned["items"] == before["items"]
    assert returned["original_error"] == "PDF 原件不可用"
    assert client.get(f"/api/v1/cases/{restored_case}/sources/{source['source_id']}/file").status_code == 404
    assert client.get(f"/api/v1/cases/{restored_case}/flows").json()["total"] == 0 and len(mineru.calls) == 1


@pytest.mark.parametrize("table_name,change", [
    ("import_records", "update entries set disposition='imported' where record_no=1"),
    ("import_members", "update entries set role='table'"),
    ("batches", "update entries set rows_imported=1"),
])
def test_pdf_package_rejects_forged_metadata_and_money(client, database, make_case, mineru, tmp_path, table_name, change):
    case = make_case()
    assert complete(client, case, upload(client, case))["stage"] == "done"
    response = client.get(f"/api/cases/{case}/export")
    assert response.status_code == 200, response.text
    members = _members(response.content)
    _mutate(members, table_name, change, tmp_path)
    cases = database.q_raw("select id from cases order by id")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", _archive(members))})
    assert restored.status_code == 400 and "PDF" in restored.json()["detail"], restored.text
    assert database.q_raw("select id from cases order by id") == cases


@pytest.mark.parametrize("cause", ["cancel", "deadline"])
def test_owned_pdf_child_and_private_directory_are_removed_on_interrupt(mineru, monkeypatch, cause):
    cancelled = threading.Event()
    result = SimpleNamespace(path=None, stopped=False, waited=False)
    clock = [0.0]
    monkeypatch.setattr(pdf_import, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def stop():
        result.stopped = True

    if not WINDOWS:
        def kill_group(pid, _signal):
            assert pid == 42
            stop()
        monkeypatch.setattr(pdf_import.os, "killpg", kill_group)

    def wait():
        result.waited = True

    def stop_job():
        stop()
        wait()

    def launch(_cmd, *, cwd, **_kwargs):
        result.path = cwd
        if cause == "cancel":
            cancelled.set()
        else:
            clock[0] = pdf_import.SECONDS + 1
        if WINDOWS:
            return SimpleNamespace(poll=lambda: None, stop=stop_job)
        return SimpleNamespace(pid=42, poll=lambda: None, wait=wait)

    owner, name = _mineru_launcher()
    monkeypatch.setattr(owner, name, launch)
    report = pdf_import.extract(io.BytesIO(PAYLOAD), len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest(), cancelled)
    assert report == (None if cause == "cancel" else {"reason": "pdf_ocr_timeout", "rows": []})
    assert result.stopped and result.waited and not result.path.exists()


def test_public_pdf_disconnect_kills_the_owned_child_and_discards_preview(database, make_case, monkeypatch, tmp_path):
    case = make_case()
    later_case = make_case("Synthetic new preview after cancellation")
    path = f"/api/v1/cases/{case}/imports/preview"
    executable = tmp_path / ("synthetic-mineru.exe" if WINDOWS else "synthetic-mineru")
    executable.touch()
    monkeypatch.setenv("FUND_MINERU_EXECUTABLE", str(executable))
    monkeypatch.setenv("FUND_MINERU_MODEL_DIR", str(tmp_path))
    owner, name = _mineru_launcher()
    start = getattr(owner, name)
    ready = threading.Event()
    launched = []

    def run_child(command, **kwargs):
        assert command[0] == str(executable)
        script = (
            "from pathlib import Path\n"
            "import time\n"
            "with open('source.pdf', 'rb'):\n"
            "    Path('started').touch()\n"
            "    time.sleep(60)\n"
        )
        try:
            child = start([sys.executable, "-I", "-B", "-c", script], **kwargs)
        except OSError as error:
            raise AssertionError("could not start the controlled PDF child") from error
        launched.append((child, kwargs["cwd"]))
        ready.set()
        return child

    monkeypatch.setattr(owner, name, run_child)

    async def scenario():
        peer_closed = asyncio.Event()

        async def disconnected(scope, receive, send):
            if scope["path"] != path:
                return await app(scope, receive, send)
            consumed = False

            async def after_body():
                nonlocal consumed
                if not consumed:
                    message = await receive()
                    consumed = message["type"] == "http.request" and not message.get("more_body")
                    return message
                await peer_closed.wait()
                return {"type": "http.disconnect"}

            return await app(scope, after_body, send)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=disconnected), base_url="http://synthetic") as client:
            async with asyncio.TaskGroup() as tasks:
                upload_task = tasks.create_task(client.post(path, files={"file": ("synthetic.pdf", PAYLOAD)}))
                assert await asyncio.to_thread(ready.wait, 10), "the PDF child was never launched"
                (child, directory), = launched
                deadline = time.monotonic() + 10
                while not (directory / "started").is_file():
                    assert child.poll() is None, "the controlled PDF child exited before opening the source"
                    assert time.monotonic() < deadline, "the controlled PDF child did not report readiness"
                    await asyncio.sleep(0.02)
                assert child.poll() is None
                peer_closed.set()
                response = await asyncio.wait_for(upload_task, 5)
            assert response.status_code == 499, response.text
            assert response.json()["error"]["code"] == "preview_cancelled"
            subsequent = await client.post(f"/api/v1/cases/{later_case}/imports/preview",
                                           files={"file": ("invalid.pdf", b"synthetic invalid")})
            assert subsequent.status_code == 200
            assert subsequent.json()["table_summary"]["reason_counts"] == {"pdf_invalid": 1}
            assert database.q_raw("select import_id from import_sessions where case_id=?", [case]) == []
            assert database.q_raw("select import_id from import_sessions where case_id=?", [later_case]) == [
                {"import_id": subsequent.json()["import_id"]}]
            assert sorted(p.name for p in (database.DATA_DIR / "uploads").iterdir()) == [
                subsequent.json()["import_id"] + ".pdf"]

    try:
        asyncio.run(scenario())
        (child, directory), = launched
        assert child.poll() not in (None, 0) and not directory.exists()
    finally:
        for child, _ in launched:
            if WINDOWS:
                child.stop()
            elif child.poll() is None:
                child.kill()
                child.wait(timeout=5)
