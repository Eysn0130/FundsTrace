"""Isolated intake applies the exact source status field without changing history."""
import csv
import io
import zipfile

import pytest

from app import case_packages, feedback_import, import_preview_cache, table_import
from test_automatic_table_import import preview
from test_feedback_cancellations import round_trip
from test_feedback_import_api import archive, complete, transaction


_FAILED_STATUS = "支付失败（合成来源说明）"
_POLICY = "transaction-failure-literal-v1"
_POLICY_FIELD = "source_transaction_status_policy"
_MEMBER = "synthetic-status.csv"


def _table():
    header = ["交易时间", "交易流水号", "本方账号", "交易对手账卡号", "收付标志",
              "交易金额", "交易币种", "交易是否成功", "摘要说明", "备注", "查询反馈结果原因"]

    def row(number, amount, status, summary="", remark="", feedback="", direction="出"):
        return [f"2032-02-01 10:00:0{number}", f"SYN-{number}", "000SYN-A", "000SYN-B", direction,
                amount, "CNY", status, summary, remark, feedback]

    output = io.StringIO(newline="")
    csv.writer(output).writerows([
        header,
        row(1, "90.01", _FAILED_STATUS),
        row(2, "1.01", "01", summary="摘要中的失败"),
        row(3, "2.02", "", remark="备注中的失败"),
        row(4, "3.03", "77", feedback="查询反馈中的失败"),
        row(5, "80.01", "77", direction="待定"),
        [*header[:7], "其他来源附注", *header[8:]],
        row(6, "4.04", "其他表区中的失败"),
    ])
    return output.getvalue().encode("utf-8")


def _payload(use_archive):
    value = _table()
    if not use_archive:
        return value, _MEMBER, "table.csv"
    with io.BytesIO() as buffer:
        with zipfile.ZipFile(buffer, "w") as bundle:
            bundle.writestr(_MEMBER, value)
        return buffer.getvalue(), "synthetic-status.zip", _MEMBER


@pytest.mark.parametrize("use_archive", [False, True], ids=["csv", "zip"])
def test_mixed_source_status_flow_and_original_survive_cold_read(client, database, make_case, monkeypatch, use_archive):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic exact reported status")
    payload, filename, member_path = _payload(use_archive)
    view = preview(client, case, payload, filename)
    summary = view["table_summary"]
    assert summary["reason_counts"] == {
        "failed_transaction": 1, "invalid_direction": 1, "repeated_header": 1, "transaction_valid": 4,
    }
    assert summary["currency_totals"] == {"CNY": "10.10"}
    root = f"/api/v1/cases/{case}"
    source_id = view["import_id"]
    replay = client.post(root + f"/imports/{source_id}/preview", json={"mode": view["mode"], "mapping": view["mapping"]})
    assert replay.status_code == 200
    assert replay.json()["table_summary"]["reason_counts"] == summary["reason_counts"]
    import_preview_cache.discard(source_id)
    with database._lock:
        database._con.close()
        database._con = None
    job = complete(client, case, replay.json())
    assert job["stage"] == "done" and job["result"]["ok"] == 4
    flows = client.get(root + "/flows", params={"granularity": "observations"}).json()["items"]
    assert {flow["amount"] for flow in flows} == {"1.01", "2.02", "3.03", "4.04"}
    assert client.post("/api/track", json={"case_id": case, "flow_ids": [flow["id"] for flow in flows]}).status_code == 200

    def graph():
        response = client.get("/api/graph", params={"case_id": case})
        assert response.status_code == 200
        return response.json()

    assert graph()["transaction_count"] == 4
    assert graph()["edges"][0]["total"] == "10.10"
    with database._lock:
        database._con.close()
        database._con = None
    assert graph()["transaction_count"] == 4
    assert graph()["edges"][0]["total"] == "10.10"

    records = client.get(root + f"/imports/{source_id}/review", params={"member_path": member_path}).json()["items"]
    by_row = {item["source_row"]: item for item in records}
    blocked = by_row[2]
    assert (blocked["disposition"], blocked["reason_code"], blocked["flow_id"]) == (
        "non_transaction", "failed_transaction", None)
    assert blocked["details"]["reported_status"] == "explicit_failure"
    assert blocked["details"][_POLICY_FIELD] == _POLICY
    source_cell = blocked["original"][7]
    assert (source_cell["column"], source_cell["header"], source_cell["fields"], source_cell["value"]) == (
        8, "交易是否成功", ["status"], _FAILED_STATUS)
    assert all(by_row[row]["details"]["reported_status"] == "unknown" for row in (3, 4, 5))
    assert by_row[6]["flow_id"] is None and by_row[6]["reason_code"] == "invalid_direction"
    assert _POLICY_FIELD not in by_row[8]["details"]
    assert client.get(root + f"/sources/{source_id}/file").content == payload
    restored = case_packages.import_package(case_packages.export_package(case)[0])["case_id"]
    assert len(client.get(f"/api/v1/cases/{restored}/flows", params={"granularity": "observations"}).json()["items"]) == 4

    result = client.delete(root + f"/sources/{source_id}/operation", params={
        "expected_batch_id": job["result"]["batch_id"],
    })
    assert result.status_code == 200
    assert graph()["transaction_count"] == 0 and graph()["edges"] == []
    assert client.get(root + f"/sources/{source_id}/file").content == payload


def test_pre_policy_session_replays_frozen_status_treatment(client, database, make_case, monkeypatch):
    case = make_case("Synthetic historical exact reported status")
    old_view = preview(client, case, _table())
    assert old_view["table_summary"]["reason_counts"] == {"unknown_status": 4, "repeated_header": 1, "transaction_valid": 2}
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    import_preview_cache.discard(old_view["import_id"])
    with database._lock:
        database._con.close()
        database._con = None
    result = complete(client, case, old_view)
    assert result["stage"] == "done" and result["result"]["ok"] == 2
    items = client.get(f"/api/v1/cases/{case}/imports/{old_view['import_id']}/records").json()["items"]
    assert items[0]["reason_code"] == "unknown_status" and all(_POLICY_FIELD not in item["details"] for item in items)


def test_failed_row_stays_unlinked_across_old_active_source_and_repeat_upload(
        client, database, make_case, monkeypatch):
    case = make_case("Synthetic status support boundaries")
    root = f"/api/v1/cases/{case}"
    header = ["交易时间", "交易流水号", "本方账号", "交易对手账卡号", "收付标志", "交易金额", "交易币种", "交易是否成功"]
    attempted = ["2032-02-01 10:00:01", "SYN-TRY", "SYN-A", "SYN-B", "出", "90.01", "CNY", _FAILED_STATUS]
    valid = ["2032-02-01 10:00:02", "SYN-OK", "SYN-A", "SYN-B", "出", "1.01", "CNY", "01"]

    def payload(*rows):
        output = io.StringIO(newline="")
        csv.writer(output).writerows([header, *rows])
        return output.getvalue().encode()

    def register(raw, name):
        job = complete(client, case, preview(client, case, raw, name))
        assert job["stage"] == "done"
        return job["result"]

    def records(receipt):
        return client.get(root + f"/imports/{receipt['source_id']}/records").raise_for_status().json()["items"]

    def chart():
        value = client.get("/api/graph", params={"case_id": case}).raise_for_status().json()
        return value["transaction_count"], value["edges"][0]["total"] if value["edges"] else "0.00"

    def withdraw(receipt):
        result = client.delete(root + f"/sources/{receipt['source_id']}/operation",
                               params={"expected_batch_id": receipt["batch_id"]})
        assert result.status_code == 200

    mixed = payload(attempted, valid)
    old = register(mixed, "old.csv")
    assert old["ok"] == 1
    assert records(old)[0]["reason_code"] == "unknown_status"

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    new = register(mixed, "repeat.csv")
    assert (new["ok"], new["rows_dup"]) == (0, 1)
    first, second = records(new)
    assert (first["reason_code"], first["flow_id"]) == ("failed_transaction", None)
    assert second["flow_id"] == records(old)[1]["flow_id"]

    # Another original can report a completed record with matching visible
    # fields. This alone does not attach the failed physical row to that flow.
    separate = register(payload([*attempted[:-1], "01"]), "another.csv")
    assert separate["ok"] == 1
    [separate_record] = records(separate)
    assert separate_record["flow_id"] != second["flow_id"]
    assert records(old)[0]["flow_id"] is None and records(new)[0]["flow_id"] is None
    track = client.post("/api/track", json={"case_id": case, "flow_ids": [second["flow_id"], separate_record["flow_id"]]})
    assert track.status_code == 200
    with database._lock:
        database._con.close()
        database._con = None
    assert chart() == (2, "91.02")

    withdraw(old)
    assert chart() == (2, "91.02")
    withdraw(new)
    assert chart() == (1, "90.01")
    withdraw(separate)
    assert chart() == (0, "0.00")
    assert client.get(root + f"/sources/{new['source_id']}/file").content == mixed


def test_strict_feedback_route_freezes_old_reader_and_can_archive_new_status(client, database, make_case, monkeypatch,
                                                                           legacy_feedback_reader):
    payload = archive([
        transaction(**{"交易流水号": "SYN-F1", "交易金额": "90.01", "交易是否成功": _FAILED_STATUS}),
        transaction(**{"交易流水号": "SYN-F2", "交易金额": "1.01", "交易是否成功": "", "摘要说明": "摘要失败"}),
        transaction(**{"交易流水号": "SYN-F3", "交易金额": "2.02", "交易是否成功": "77"}),
        transaction(**{"交易流水号": "SYN-F4", "交易金额": "3.03", "交易是否成功": "01"}),
        transaction(**{"交易流水号": "SYN-F5", "交易金额": "80.01", "交易是否成功": "", "交易币种": ""}),
    ], account=False)
    case = make_case("Synthetic strict feedback status")
    old_view = preview(client, case, payload, "synthetic.zip")
    assert old_view["feedback"]["profile"] == feedback_import.MICROSECOND_PROFILE
    assert old_view["feedback"]["reason_counts"] == {"transaction_valid": 1, "unknown_status": 3, "invalid_currency": 1}
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    old_result = complete(client, case, old_view)
    assert old_result["stage"] == "done" and old_result["result"]["ok"] == 1
    old_restored = case_packages.import_package(case_packages.export_package(case)[0])["case_id"]
    assert len(client.get(f"/api/v1/cases/{old_restored}/flows", params={"granularity": "observations"}).json()["items"]) == 1

    fresh_case = make_case("Synthetic policy-bound strict feedback status")
    view = preview(client, fresh_case, payload, "synthetic.zip")
    assert view["feedback"]["reason_counts"] == {
        "failed_transaction": 1, "transaction_valid": 3, "invalid_currency": 1,
    }
    assert view["feedback"]["currency_totals"] == {"CNY": "6.06"}
    import_preview_cache.discard(view["import_id"])
    job = complete(client, fresh_case, view)
    assert job["stage"] == "done" and job["result"]["ok"] == 3
    source_id = view["import_id"]
    root = f"/api/v1/cases/{fresh_case}"
    with database._lock:
        database._con.close()
        database._con = None
    items = client.get(root + f"/imports/{source_id}/records").json()["items"]
    assert items[0]["reason_code"] == "failed_transaction" and items[0]["flow_id"] is None
    assert items[0]["details"]["reported_status"] == _FAILED_STATUS
    assert items[-1]["flow_id"] is None and items[-1]["details"]["validation_issues"] == "invalid_currency"
    assert client.get(root + f"/sources/{source_id}/file").content == payload
    restored = case_packages.import_package(case_packages.export_package(fresh_case)[0])["case_id"]
    assert len(client.get(f"/api/v1/cases/{restored}/flows", params={"granularity": "observations"}).json()["items"]) == 3


def test_status_alias_keeps_historical_interpretation_under_literal_policy():
    payload = _table().decode().replace("交易是否成功", "交易状态").encode()
    baseline = table_import.parse(payload, suffix=".csv")
    fresh = table_import.parse(payload, suffix=".csv", transaction_status_policy=_POLICY)
    assert fresh["stats"]["reason_counts"] == baseline["stats"]["reason_counts"]
    assert fresh["records"] == baseline["records"]


@pytest.mark.parametrize("unknown_leg", [None, 1, 2], ids=["proved", "original-unproved", "reversal-unproved"])
def test_strict_feedback_keeps_independent_cancellation_admission(unknown_leg):
    rows = round_trip()
    if unknown_leg is not None:
        rows[unknown_leg]["交易是否成功"] = "77"
    plan = feedback_import.parse(io.BytesIO(archive(rows, account=False)), transaction_status_policy=_POLICY)
    assert plan["stats"]["cancelled"] == (2 if unknown_leg is None else 0)
    if unknown_leg is not None:
        assert len(plan["rows"]) == 2
        assert plan["records"][2]["reason_code"] == "negative_reversal_text"
