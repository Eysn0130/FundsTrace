"""Synthetic integration checks for frozen, case-bound header interpretations."""
import asyncio
import json
import threading
import time

import httpx
import pytest

from app import db, header_model, import_preview_cache
from app.main import app
from app.services import imports
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


@pytest.mark.parametrize("when", ["connected", "before-model", "during-model", "before-publish"])
def test_preview_observes_post_body_asgi_disconnect(database, make_case, monkeypatch, when):
    case = make_case("Synthetic interrupted suggestion")
    other = make_case("Synthetic fresh preview")
    path = f"/api/v1/cases/{case}/imports/preview"
    started = threading.Event()
    outcome, disconnects, signals = [], [], []

    def model(_prompt, _schema, cancelled, _deadline):
        assert not getattr(db._transaction_state, "depth", 0)
        signals.append(cancelled)
        if when in {"connected", "during-model"}:
            started.set()
        outcome.append(cancelled.wait(1) if when == "during-model" else cancelled.is_set())
        if outcome[-1]:
            return None
        proposal = {"header_rows": [1, 2], "columns": [{"column": 3, "role": "payer_account"}]}
        return proposal, proposal

    monkeypatch.setattr(header_model, "configured", lambda: True)
    monkeypatch.setattr(header_model.local_model, "suggest", reject_call if when == "before-model" else model)
    if when == "before-model":
        parse = imports._parse

        def entered_parse(*args, **kwargs):
            started.set()
            assert kwargs["header_policy"].cancelled.wait(2)
            return parse(*args, **kwargs)

        monkeypatch.setattr(imports, "_parse", entered_parse)
    elif when == "before-publish":
        preview_result = imports._preview_result

        def registered(session, *args, **kwargs):
            if session["case_id"] == case:
                started.set()
                assert signals[0].wait(2)
            return preview_result(session, *args, **kwargs)

        monkeypatch.setattr(imports, "_preview_result", registered)
    parents = [*PARENTS]
    parents[2] = "划出一方"

    async def scenario():
        peer_closed = asyncio.Event()

        async def asgi(scope, receive, send):
            if scope["path"] != path:
                return await app(scope, receive, send)
            body_consumed = False

            async def wire_receive():
                nonlocal body_consumed
                if not body_consumed:
                    message = await receive()
                    body_consumed = message["type"] == "http.request" and not message.get("more_body", False)
                    return message
                await peer_closed.wait()
                # A receive channel may checkpoint even with an event ready.
                await asyncio.sleep(0)
                disconnects.append(True)
                return {"type": "http.disconnect"}

            return await app(scope, wire_receive, send)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi), base_url="http://synthetic") as client:
            pending = asyncio.create_task(client.post(path, files={"file": ("synthetic.csv", payload([parents, LEAVES, NEW_ROW]))}))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                if when != "connected":
                    peer_closed.set()
                response = await asyncio.wait_for(pending, 5)
            finally:
                if not pending.done():
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
            interrupted = when in {"during-model", "before-publish"}
            assert response.status_code == (499 if interrupted else 200), response.text
            sessions = database.q_raw("select stage from import_sessions where case_id=?", [case])
            if interrupted:
                assert response.json()["error"]["code"] == "preview_cancelled"
                assert sessions == []
                assert not list(imports._directory().glob("*.csv"))
                next_path = f"/api/v1/cases/{other}/imports/preview"
                response = await client.post(next_path, files={"file": ("known.csv", payload([PARENTS, LEAVES, NEW_ROW]))})
                assert response.status_code == 200 and response.json()["stats"]["ok"] == 1, response.text
            else:
                assert sessions == [{"stage": "ready"}]
                advice = response.json()["table_summary"]["documents"][0]["header_assistance"][0]
                assert advice.get("suggestions", []) == (
                    [{"column_index": 3, "role": "payer_account"}] if when == "connected" else [])

    asyncio.run(scenario())
    assert outcome == ([] if when == "before-model" else [when == "during-model"])
    assert disconnects == ([] if when == "connected" else [True])
