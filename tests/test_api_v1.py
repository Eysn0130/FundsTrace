"""Lossless, case-bound public v1 behavior on synthetic temporary DuckDB."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pandas as pd
import pytest

from app import main
from app import license as licensing
from app.services import cases, imports


def assert_error(response, status, code=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["message"]
    if code:
        assert body["error"]["code"] == code
    return body


def synthetic_file(extension="csv", mode="A"):
    if mode == "A":
        frame = pd.DataFrame([
            {"流水号": "SYN-001", "交易时间": "2026-01-01 10:00:00", "付款人账号": "000SYN-A",
             "付款人名称": "Synthetic sender", "收款人账号": "000SYN-B", "收款人名称": "Synthetic receiver",
             "交易金额": "9999999999999999.99", "余额": "8888888888888888.88", "本方账号": "000SYN-A"},
            {"流水号": "SYN-002", "交易时间": "2026-01-01 10:00:00", "付款人账号": "000SYN-B",
             "付款人名称": "Synthetic receiver", "收款人账号": "000SYN-C", "收款人名称": "Synthetic next",
             "交易金额": "0.01", "余额": "0.00", "本方账号": "000SYN-A"},
        ])
    else:
        frame = pd.DataFrame([
            {"交易时间": "2026-01-02 10:00:00", "本方账号": "000SYN-SELF", "本方户名": "Synthetic self",
             "对方账号": "000SYN-OTHER", "对方户名": "Synthetic other", "收支标志": "收入", "交易金额": "23.45"},
            {"交易时间": "2026-01-02 11:00:00", "本方账号": "000SYN-SELF", "本方户名": "Synthetic self",
             "对方账号": "000SYN-OTHER", "对方户名": "Synthetic other", "收支标志": "支出", "交易金额": "12.34"},
        ])
    if extension == "csv":
        return frame.to_csv(index=False).encode("utf-8-sig")
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    return buffer.getvalue()


def upload(client, case_id, extension="csv", mode="A"):
    response = client.post(f"/api/v1/cases/{case_id}/imports/preview",
                           files={"file": (f"synthetic.{extension}", synthetic_file(extension, mode))})
    assert response.status_code == 200, response.text
    return response.json()


def poll(client, case_id, import_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/cases/{case_id}/imports/{import_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in ("done", "error"):
            return job
        time.sleep(0.01)
    pytest.fail("Synthetic v1 import did not finish within ten seconds")


def test_cases_creation_and_error_contract(client):
    response = client.post("/api/v1/cases", json={"name": "  Synthetic v1  "})
    assert response.status_code == 201
    case = response.json()
    assert case["id"].isdigit() and case["name"] == "Synthetic v1"
    assert isinstance(case["created_at"], str)
    assert case in client.get("/api/v1/cases").json()["items"]
    assert_error(client.post("/api/v1/cases", json={"name": "Synthetic v1"}), 409, "case_name_exists")
    assert_error(client.post("/api/v1/cases", json={"name": " "}), 422)
    assert_error(client.post("/api/v1/cases", json={"name": "Synthetic other", "case_id": 1}), 422)
    assert_error(client.post("/api/v1/cases", content="{broken", headers={"Content-Type": "application/json"}), 422)


def test_routing_errors_use_v1_shape_and_legacy_errors_remain_compatible(client):
    assert_error(client.get("/api/v1/not-a-route"), 404, "http_404")
    response = client.put("/api/v1/cases", json={})
    assert_error(response, 405, "http_405")
    assert "allow" in response.headers
    legacy = client.get("/api/not-a-route")
    assert legacy.status_code == 404 and legacy.json() == {"detail": "Not Found"}


def test_license_gate_preserves_v1_error_shape(client, monkeypatch):
    monkeypatch.setattr(licensing, "is_activated", lambda: False)
    assert_error(client.get("/api/v1/cases"), 403, "not_activated")
    assert client.get("/api/cases").json() == {"detail": "系统未激活"}


def test_precise_identifiers_amounts_stats_filters_and_stable_pagination(client, database, make_case):
    cid, other = make_case("Precision"), make_case("Other")
    high = 9007199254740993
    database.x("insert into batches(id,case_id,filename) values (?,?,?)", [high, cid, "synthetic.csv"])
    values = [
        (high, cid, high, "SYN-first", "2026-01-01 10:00:00", "000A", "000B", Decimal("9999999999999999.99"), Decimal("8888888888888888.88"), "Needle"),
        (high + 1, cid, high, "SYN-second", "2026-01-01 10:00:00", "000A", "000C", Decimal("0.01"), None, "Second"),
        (high + 2, cid, None, "SYN-null-time", None, "000Z", "000Y", Decimal("1.00"), None, None),
        (high + 3, other, None, "SYN-other", "2026-02-01", "000A", "000B", Decimal("50.00"), None, "Needle"),
    ]
    database.executemany("insert into flows(id,case_id,batch_id,trade_no,trade_time,payer_account,payee_account,amount,balance,summary) values (?,?,?,?,?,?,?,?,?,?)", values)
    stats = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert stats == {"case_id": str(cid), "flows": 3, "accounts": 5, "batches": 1,
                     "amount_total": "10000000000000001.00", "currency": "CNY",
                     "amount_totals": [{"currency": "CNY", "amount": "10000000000000001.00"}]}
    pages = [client.get(f"/api/v1/cases/{cid}/flows", params={"page": page, "page_size": 1}).json() for page in (1, 2, 3, 4)]
    assert [page["items"][0]["id"] for page in pages[:3]] == [str(high + 1), str(high), str(high + 2)]
    assert pages[3]["items"] == []
    assert all(page["total"] == 3 and page["case_id"] == str(cid) and page["page_size"] == 1 for page in pages)
    precise = pages[1]["items"][0]
    assert precise["amount"] == "9999999999999999.99" and precise["balance"] == "8888888888888888.88"
    assert precise["batch_id"] == str(high) and precise["case_id"] == str(cid)
    nullable = pages[2]["items"][0]
    assert nullable["trade_time"] is None and nullable["batch_id"] is None and nullable["summary"] == "" and nullable["payer_name"] == ""
    filtered = client.get(f"/api/v1/cases/{cid}/flows", params={"account": "000a", "kw": "needle"}).json()
    assert filtered["total"] == 1 and filtered["items"][0]["id"] == str(high)
    assert client.get(f"/api/v1/cases/{other}/stats").json()["amount_total"] == "50.00"


def test_case_stats_keep_currencies_separate_and_empty_case_explicit(client, database, make_case):
    cid = make_case()
    empty = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert empty["amount_total"] == "0.00" and empty["currency"] is None and empty["amount_totals"] == []
    database.executemany("insert into flows(case_id,amount,currency) values (?,?,?)", [
        (cid, Decimal("12.34"), "CNY"), (cid, Decimal("0.01"), "CNY"), (cid, Decimal("9.87"), "USD")])
    mixed = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert mixed["amount_total"] is None and mixed["currency"] is None
    assert mixed["amount_totals"] == [{"currency": "CNY", "amount": "12.35"}, {"currency": "USD", "amount": "9.87"}]
    database.x("update flows set currency=null where currency='USD'")
    unknown = client.get(f"/api/v1/cases/{cid}/stats").json()
    assert unknown["amount_total"] is None
    assert unknown["amount_totals"] == [{"currency": "", "amount": "9.87"}, {"currency": "CNY", "amount": "12.35"}]


@pytest.mark.parametrize("query", [{"page": 0}, {"page": -1}, {"page": 2147483648}, {"page_size": 0}, {"page_size": 201}, {"page": "no"}])
def test_pagination_validation(client, make_case, query):
    cid = make_case()
    assert_error(client.get(f"/api/v1/cases/{cid}/flows", params=query), 422, "invalid_request")


def test_missing_cases_and_internal_error_do_not_leak_paths(client, make_case, monkeypatch):
    for endpoint in ("stats", "flows"):
        assert_error(client.get(f"/api/v1/cases/9999999999999999999/{endpoint}"), 404, "case_not_found")
    assert_error(client.get("/api/v1/cases/1.5/stats"), 422)
    cid = make_case()
    def fail(*args):
        raise RuntimeError("/private/synthetic/path traceback secret stack")
    monkeypatch.setattr(cases, "stats", fail)
    response = client.get(f"/api/v1/cases/{cid}/stats")
    assert_error(response, 500, "internal_error")
    assert "/private" not in response.text and "traceback" not in response.text


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_bound_import_preview_repreview_commit_poll_and_exact_decimal(client, database, make_case, extension):
    cid = make_case(f"Import {extension}")
    preview = upload(client, cid, extension)
    assert preview["case_id"] == str(cid) and preview["stats"]["total"] == 2
    assert preview["preview"][0]["amount"] == "9999999999999999.99"
    assert preview["preview"][0]["balance"] == "8888888888888888.88"
    import_id = preview["import_id"]
    metadata = database.q_raw("select case_id,surface from import_sessions where import_id=?", [import_id])[0]
    assert metadata["case_id"] == cid and metadata["surface"] == "v1"
    base = f"/api/v1/cases/{cid}/imports/{import_id}"
    assert_error(client.get(base), 404, "import_job_not_found")
    mapping = {**preview["mapping"], "self_account": "本方账号"}
    repreview = client.post(f"{base}/preview", json={"mode": "A", "mapping": mapping})
    assert repreview.status_code == 200, repreview.text
    assert repreview.json()["filename"] == f"synthetic.{extension}"
    assert repreview.json()["self_accounts_in_file"] == [{"account": "000SYN-A", "name": "", "count": 2}]
    started = client.post(f"{base}/commit", json={"mode": "A", "mapping": mapping, "mark_accounts": []})
    assert started.status_code == 200 and started.json() == {"case_id": str(cid), "import_id": import_id, "started": True}
    job = poll(client, cid, import_id)
    assert job["stage"] == "done" and job["error"] is None
    assert job["result"]["ok"] == 2 and job["result"]["rows_dup"] == 0
    assert job["result"]["batch_id"].isdigit() and job["result"]["main_detected"] == []
    assert client.post(f"{base}/commit", json={"mode": "A", "mapping": mapping}).json()["started"] is False
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert {row["amount"] for row in flows} == {"9999999999999999.99", "0.01"}
    assert client.get(f"/api/v1/cases/{cid}/stats").json()["amount_total"] == "10000000000000000.00"


def test_mode_b_direction_and_default_main_marking(client, make_case):
    cid = make_case()
    preview = upload(client, cid, mode="B")
    assert preview["mode"] == "B"
    response = client.post(f"/api/v1/cases/{cid}/imports/{preview['import_id']}/commit",
                           json={"mode": "B", "mapping": preview["mapping"], "self_accounts": ["000EXTRA"]})
    assert response.status_code == 200
    job = poll(client, cid, preview["import_id"])
    assert job["stage"] == "done", job
    assert {item["account"] for item in job["result"]["main_detected"]} == {"000SYN-SELF", "000EXTRA"}
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert [(row["payer_account"], row["payee_account"], row["amount"]) for row in rows] == [
        ("000SYN-SELF", "000SYN-OTHER", "12.34"), ("000SYN-OTHER", "000SYN-SELF", "23.45")]


def test_all_import_endpoints_reject_other_case_unbound_metadata_and_deleted_case(client, database, make_case):
    cid, other = make_case("Owner"), make_case("Other")
    preview = upload(client, cid)
    body = {"mode": "A", "mapping": preview["mapping"]}
    iid = preview["import_id"]
    for suffix in ("preview", "commit"):
        assert_error(client.post(f"/api/v1/cases/{other}/imports/{iid}/{suffix}", json=body), 404, "import_not_found")
    assert_error(client.get(f"/api/v1/cases/{other}/imports/{iid}"), 404, "import_not_found")
    database.x("update import_sessions set surface='legacy',case_id=null where import_id=?", [iid])
    for suffix in ("preview", "commit"):
        assert_error(client.post(f"/api/v1/cases/{cid}/imports/{iid}/{suffix}", json=body), 404, "import_not_found")
    assert_error(client.get(f"/api/v1/cases/{cid}/imports/{iid}"), 404, "import_not_found")
    database.x("update import_sessions set surface='v1',case_id=? where import_id=?", [cid, iid])
    database.x("delete from cases where id=?", [cid])
    assert_error(client.post(f"/api/v1/cases/{cid}/imports/{iid}/commit", json=body), 404, "case_not_found")
    assert database.q("select count(*) n from batches") == [{"n": 0}]


def test_upload_and_mapping_errors_are_actionable_and_failed_upload_is_cleaned(client, database, make_case):
    cid = make_case()
    base = f"/api/v1/cases/{cid}/imports/preview"
    assert_error(client.post(base, files={"file": ("bad.exe", b"x")}), 400, "unsupported_file_type")
    response = client.post(base, files={"file": ("/private/synthetic/bad.csv", b"cannot,identify\none,two")})
    assert_error(response, 400, "invalid_import_file")
    assert "/private" not in response.text
    assert list((database.DATA_DIR / "uploads").iterdir()) == []
    assert_error(client.post(base), 422)
    preview = upload(client, cid)
    route = f"/api/v1/cases/{cid}/imports/{preview['import_id']}"
    for suffix in ("preview", "commit"):
        assert_error(client.post(f"{route}/{suffix}", json={"mode": "A", "mapping": {"trade_time": "does not exist"}}), 422, "invalid_mapping")
    assert_error(client.post(f"{route}/commit", json={"mode": "A", "mapping": preview["mapping"], "self_accounts": ["bad,account"]}), 422)
    assert_error(client.post(f"{route}/preview", json={"mode": "C", "mapping": {}}), 422)


def test_concurrent_same_session_commit_starts_one_atomic_job(client, database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(client, cid)
    entered, release = threading.Event(), threading.Event()
    original = imports._run
    def blocked(*args):
        entered.set()
        assert release.wait(timeout=10)
        return original(*args)
    monkeypatch.setattr(imports, "_run", blocked)
    body = {"mode": "A", "mapping": preview["mapping"]}
    route = f"/api/v1/cases/{cid}/imports/{preview['import_id']}/commit"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.post, route, json=body) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        assert entered.wait(timeout=5)
        assert all(response.status_code == 200 for response in results)
        assert sorted(response.json()["started"] for response in results) == [False, True]
    finally:
        release.set()
    assert poll(client, cid, preview["import_id"])["stage"] == "done"
    assert database.q("select count(*) n from batches where case_id=?", [cid]) == [{"n": 1}]


def test_job_error_is_safe_and_real_commit_rolls_back(client, database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(client, cid)
    def fail(*args):
        raise RuntimeError("/private/synthetic/path traceback internal SQL")
    monkeypatch.setattr(database, "insert_dataframe", fail)
    route = f"/api/v1/cases/{cid}/imports/{preview['import_id']}/commit"
    assert client.post(route, json={"mode": "A", "mapping": preview["mapping"]}).status_code == 200
    job = poll(client, cid, preview["import_id"])
    assert job["stage"] == "error" and job["result"] is None
    assert "/private" not in json.dumps(job) and "traceback" not in json.dumps(job)
    assert job["error"] == job["message"]
    assert database.q("select count(*) n from batches") == [{"n": 0}]
    assert database.q("select count(*) n from flows") == [{"n": 0}]


def test_poll_waits_for_atomic_result_before_reporting_terminal_success(client, database, make_case, monkeypatch):
    cid = make_case()
    preview = upload(client, cid)
    entered, release = threading.Event(), threading.Event()
    original = imports._run
    def delayed_import(import_id):
        entered.set()
        assert release.wait(timeout=10)
        return original(import_id)
    monkeypatch.setattr(imports, "_run", delayed_import)
    base = f"/api/v1/cases/{cid}/imports/{preview['import_id']}"
    try:
        assert client.post(f"{base}/commit", json={"mode": "A", "mapping": preview["mapping"]}).status_code == 200
        assert entered.wait(timeout=5)
        pending = client.get(base).json()
        assert pending["stage"] == "parsing" and pending["result"] is None
        assert database.q("select count(*) n from flows where case_id=?", [cid]) == [{"n": 0}]
    finally:
        release.set()
    finished = poll(client, cid, preview["import_id"])
    assert finished["stage"] == "done" and finished["result"]["ok"] == 2
    assert database.q_raw("select result_json from import_sessions where import_id=?", [preview['import_id']])[0]['result_json'] is not None


def test_contract_generation_is_current_and_ignores_inherited_data_directory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    forbidden_data = tmp_path / "inherited-runtime-must-stay-absent"
    env = {**os.environ, "FUND_DATA_DIR": str(forbidden_data)}
    result = subprocess.run([str(root / ".venv/bin/python"), str(root / "scripts/export_api_contract.py"), "--check"],
                            cwd=root, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not forbidden_data.exists()
    schema = json.loads((root / "contracts/openapi-v1.json").read_text())
    assert all(path.startswith("/api/v1/") for path in schema["paths"])
    operations = [operation for path in schema["paths"].values() for operation in path.values()]
    assert len(operations) == 12 and len({operation["operationId"] for operation in operations}) == 12
    download = schema["paths"]["/api/v1/cases/{case_id}/sources/{source_id}/file"]["get"]
    assert download["responses"]["200"]["content"]["application/octet-stream"]["schema"] == {"type": "string", "format": "binary"}
    for operation in operations:
        for status in ("400", "403", "404", "405", "409", "422", "500"):
            assert operation["responses"][status]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ApiError"}
    properties = schema["components"]["schemas"]["Flow"]["properties"]
    assert properties["id"]["type"] == "string" and properties["amount"]["type"] == "string"
