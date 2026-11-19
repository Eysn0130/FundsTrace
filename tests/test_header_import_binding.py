"""Synthetic integration checks for frozen, case-bound header interpretations."""
import asyncio
import io
import json
import threading
import time

import pytest

from app import db, header_model, import_preview_cache
from app.services import imports
from app.services.cases import ServiceError
from test_header_regions import LEAVES, NEW_ROW, PARENTS, payload, reject_call


def preview(client, case, data):
    response = client.post(f"/api/v1/cases/{case}/imports/preview",
                           files={"file": ("synthetic-regions.csv", data, "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()


def done(client, url):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["stage"] in {"done", "error"}:
            return result
        time.sleep(0.02)
    pytest.fail("synthetic task did not finish")


def test_verified_plan_repreviews_and_commits_without_inference(client, make_case, monkeypatch):
    case, other = make_case("Synthetic plan case"), make_case("Synthetic other case")
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    data = payload([PARENTS, LEAVES, NEW_ROW, [*NEW_ROW[:-1], "302"]])
    view = preview(client, case, data)
    assert [(row["source_row"], row["amount"], row["payer_account"], row["payee_account"], row["trade_time"][:10])
            for row in view["preview"]] == [(3, "3.01", "SYN-PAY", "SYN-REC", "2026-01-07")]
    digest = view["table_summary"]["header_plan_sha256"]
    assert len(digest) == 64
    url = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    body = {key: view[key] for key in ("mode", "mapping")}
    wrong = client.post(f"/api/v1/cases/{other}/imports/{view['import_id']}/preview", json=body)
    assert wrong.status_code == 404
    import_preview_cache.discard()
    read_source = imports._parse

    def parse_outside_transaction(*args, **kwargs):
        assert not getattr(db._transaction_state, "depth", 0)
        return read_source(*args, **kwargs)

    monkeypatch.setattr(imports, "_parse", parse_outside_transaction)
    redone = client.post(url + "/preview", json=body)
    assert redone.status_code == 200, redone.text
    assert redone.json()["table_summary"]["header_plan_sha256"] == digest
    assert client.post(url + "/commit", json=body).status_code == 200
    result = done(client, url)
    assert result["stage"] == "done", result
    assert result["result"]["table_summary"]["header_plan_sha256"] == digest
    assert result["result"]["ok"] == 1
    records = client.get(url + "/records", params={"member_path": "table.csv", "source_row": 4}).json()
    assert records["items"][0]["reason_code"] == "conflicting_amount"


def test_plan_binding_changes_fail_closed_before_writing(client, make_case, monkeypatch):
    case = make_case("Synthetic frozen proof")
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call)
    view = preview(client, case, payload([PARENTS, LEAVES, NEW_ROW]))
    iid = view["import_id"]
    url = f"/api/v1/cases/{case}/imports/{iid}"
    body = {key: view[key] for key in ("mode", "mapping")}
    captured = json.loads(db.q_raw("select request_json from import_sessions where import_id=?", [iid])[0]["request_json"])
    captured["_header_plan"]["decisions"][0]["member_path"] = "another.csv"
    db.x("update import_sessions set request_json=? where import_id=?", [json.dumps(captured), iid])
    assert client.post(url + "/preview", json=body).status_code == 409
    assert client.post(url + "/commit", json=body).status_code == 200
    job = done(client, url)
    assert (job["stage"], job["error_code"]) == ("error", "import_integrity_failed")
    assert db.q_raw("select count(*) n from flows where case_id=?", [case])[0]["n"] == 0
    assert db.q_raw("select count(*) n from import_sources where case_id=?", [case])[0]["n"] == 0


def test_disconnect_discards_inference_and_unpublished_upload(database, make_case, monkeypatch):
    case = make_case("Synthetic interrupted suggestion")
    started = threading.Event()

    def model(_prompt, _schema, cancelled, _deadline):
        assert not getattr(db._transaction_state, "depth", 0)
        started.set()
        assert cancelled.wait(2)
        proposal = {"header_rows": [1, 2], "columns": [{"column": 3, "role": "payer_account"}]}
        return proposal, proposal

    async def disconnected():
        return started.is_set()

    monkeypatch.setattr(header_model.local_model, "suggest", model)
    parents = [*PARENTS]
    parents[2] = "划出一方"
    data = io.BytesIO(payload([parents, LEAVES, NEW_ROW]))
    with pytest.raises(ServiceError) as stopped:
        asyncio.run(imports._execute_preview(imports._upload_sync, case, data, "synthetic.csv", "v1",
                                             disconnected=disconnected))
    assert stopped.value.code == "preview_cancelled"
    assert db.q_raw("select count(*) n from import_sessions where case_id=?", [case])[0]["n"] == 0
    assert not list(imports._directory().glob("*.csv"))
