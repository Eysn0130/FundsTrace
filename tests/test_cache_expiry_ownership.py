"""Independent synthetic public checks for late cache-expiry ownership."""
import csv
import io
import time

import pytest


def preview(client, case_id, reference):
    from app.table_import import BANK_FEEDBACK_32_COLUMNS
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=sorted(BANK_FEEDBACK_32_COLUMNS))
    writer.writeheader()
    writer.writerow({"交易账号": "SYN-COMMANDER-CACHE-A", "交易对手账卡号": "SYN-COMMANDER-CACHE-B",
                     "交易时间": "2026-01-01 10:00:00.123456", "交易金额": "100.01",
                     "交易币种": "CNY", "收付标志": "出", "交易是否成功": "1", "交易流水号": reference})
    response = client.post(f"/api/v1/cases/{case_id}/imports/directory/preview",
                           files=[("files", ("synthetic.csv", stream.getvalue().encode(), "text/csv"))])
    assert response.status_code == 200, response.text
    return response.json()


def commit(client, case_id, view):
    url = f"/api/v1/cases/{case_id}/imports/{view['import_id']}"
    response = client.post(url + "/commit", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert response.status_code == 200, response.text
    for _ in range(400):
        response = client.get(url)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["stage"] in ("done", "error"):
            assert job["stage"] == "done", job
            return
        time.sleep(.01)
    raise AssertionError("synthetic commit timed out")


@pytest.mark.parametrize("expiry_target", ["replaced", "already-taken"])
def test_late_expiry_never_closes_replacement_or_claimed_plan(client, monkeypatch, expiry_target):
    from app import import_preview_cache as cache
    from app.services import imports
    cache.discard()
    try:
        first_case = client.post("/api/cases", json={"name": "Synthetic expiry first"}).json()["id"]
        view = preview(client, first_case, "SYN-FIRST")
        assert cache._slot is not None
        old_parsed = cache._slot[1]
        old_store = old_parsed[0].attrs["table_plan"]["_plan_store"]
        case_id, reference = first_case, "SYN-FIRST"
        callbacks = []
        if expiry_target == "replaced":
            case_id = client.post("/api/cases", json={"name": "Synthetic expiry replacement"}).json()["id"]
            reference = "SYN-REPLACEMENT"
            view = preview(client, case_id, reference)
            assert old_store._closed
            saved = cache._slot
            cache._expire(old_parsed)
            assert cache._slot is saved
        else:
            original = imports._bind_table_plan
            def expire_after_take(cid, parsed, **kwargs):
                assert parsed is old_parsed and cache._slot is None
                cache._expire(old_parsed)
                assert not old_store._closed
                callbacks.append(cid)
                return original(cid, parsed, **kwargs)
            monkeypatch.setattr(imports, "_bind_table_plan", expire_after_take)
        def must_reuse(*args, **kwargs):
            raise AssertionError("late expiry unexpectedly destroyed the eligible plan")
        monkeypatch.setattr(imports, "_parse", must_reuse)
        commit(client, case_id, view)
        result = client.get(f"/api/v1/cases/{case_id}/flows", params={"granularity": "observations"}).json()
        assert result["total"] == 1
        flow = result["items"][0]
        assert (flow["amount"], flow["currency"], flow["trade_no"]) == ("100.01", "CNY", reference)
        assert flow["trade_time"].endswith(".123456")
        assert client.get(f"/api/v1/cases/{case_id}/flows/{flow['id']}/sources").json()["total"] == 1
        assert cache._slot is None
        if expiry_target == "replaced":
            assert client.get(f"/api/v1/cases/{first_case}/flows").json()["total"] == 0
        else:
            assert callbacks == [case_id] and old_store._closed
    finally:
        cache.discard()
