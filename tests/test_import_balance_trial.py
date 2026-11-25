"""Source-scoped balance mapping through the ordinary desktop trial entry point."""
import csv
import io
import json

import pytest

from app.services import desktop_model_trial
from test_api_v1 import poll


def sample(balance_heading):
    fields = ["本方账号", "交易时间", "借贷标志", "交易金额(分)", balance_heading,
              "可用余额", "对方余额", "对方账号", "币种", "交易流水号"]
    rows = [
        ["000021", "2041-02-03 07:08:00", "贷", "32", "40", "40", "40", "000031", "CNY", "POS-1"],
        ["000021", "2041-02-03 07:08:01", "借", "22", "", "90", "90", "000032", "CNY", "POS-2"],
        ["000021", "2041-02-03 07:08:02", "贷", "17", "0", "90", "0", "000033", "CNY", "POS-3"],
    ]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([fields, *rows])
    return stream.getvalue().encode(), rows


@pytest.mark.parametrize(("heading", "expected_factor", "expected_balance"),
                         [("联机余额", "1", "40.00"), ("交易余额(分)", "0.01", "0.40")])
def test_balance_role_binds_original_column_and_preserves_formal_confirmation(
        client, database, make_case, monkeypatch, heading, expected_factor, expected_balance):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *_: pytest.fail("Use provable original roles"))
    case = make_case()
    base = f"/api/v1/cases/{case}"
    raw, originals = sample(heading)
    before = client.post(base + "/imports/preview", files={"file": ("synthetic.csv", raw)}).raise_for_status().json()
    assert before["table_summary"]["profile"] == "funds-trace-table-v69"
    job = f"{base}/imports/{before['import_id']}"
    route = job + "/layout-review/model-trial"
    request = {"revision": before["layout_review"]["revision"], "member_path": "table.csv", "header_row": 1,
               "field": "balance"}
    for column, row, role in ((6, 2, "own_reported_balance"), (7, 2, "peer_balance"),
                              (5, 3, "own_reported_balance")):
        refused = client.post(route, json={**request, "candidate": {"column": column, "source_row": row, "role": role}})
        assert refused.raise_for_status().json()["reason"] == "balance_citation_rejected"
    # An unconfigured provider is sufficient to accept a verified positional
    # claim. The existing provenance checks run before preview or commit.
    decision = client.post(route, json={**request,
        "candidate": {"role": "own_reported_balance", "column": 5, "source_row": 2}}).raise_for_status().json()
    assert decision["state"] == "proposal" and decision["first_calls"] == decision["recheck_calls"] == 0
    binding = decision["prepared_preview"]["source_balance_binding"]
    assert binding == {"member_path": "table.csv", "header_row": 1, "column": 5, "source_rows": [2, 4],
                       "unit_factor": expected_factor, "interpretation": "own_reported_balance"}
    assert client.post(route, json=request).status_code == 409

    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    ready = decision["prepared_preview"]
    assert ready["mapping"]["balance"] == heading and not ready["automatic"]
    payload = {"mode": ready["mode"], "mapping": ready["mapping"], "mark_accounts": [],
               "ready_revision": ready["layout_review"]["revision"]}
    assert client.post(job + "/commit", json=payload).status_code == 200
    rec = poll(client, case, ready["import_id"])["result"]
    assert rec["ok"] == 3
    with database._lock:
        database._con.close()
        database._con = None
    restored = client.get(base + "/imports").raise_for_status().json()["items"][0]
    assert restored["mapping"]["balance"] == heading
    flows = client.get(base + "/flows?granularity=observations").raise_for_status().json()["items"]
    assert {flow["trade_no"]: (flow["amount"], flow["balance"]) for flow in flows} == {
        "POS-1": ("0.32", expected_balance), "POS-2": ("0.22", None), "POS-3": ("0.17", "0.00")}
    rows = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()["items"]
    assert [[cell["value"] for cell in row["original"]] for row in rows] == originals
    src = f"{base}/sources/{before['import_id']}"
    assert client.get(src + "/file").content == raw
    assert client.delete(src + "/operation", params={"expected_batch_id": rec["batch_id"]}).status_code == 200
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0
    assert client.post(route, json=request).status_code == 409


def source_balance_notes(client, job):
    intake = client.get(job + "/intake").raise_for_status().json()
    return [item["balance_role"] for item in intake.get("source_field_notes", ()) if "balance_role" in item]


@pytest.mark.parametrize("heading", ["可用余额", "交易后可用余额"])
def test_available_balance_role_survives_confirmation_without_mapping_it(
        client, database, make_case, monkeypatch, heading):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    header = ["本方账号", "交易时间", "借贷标志", "交易金额", "对方账号", heading, "对方余额", "币种", "交易流水号"]
    rows = [
        ["000021", "2041-02-03 07:08:00", "贷", "32", "000031", "0", "0", "CNY", "AV-1"],
        ["000021", "2041-02-03 07:08:01", "借", "22", "000032", "", "18", "CNY", "AV-2"],
    ]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows([header, *rows])
    raw = stream.getvalue().encode()
    ready = client.post(base + "/imports/preview", files={"file": ("sample.csv", raw)}).raise_for_status().json()
    assert ready["table_summary"]["profile"] == "funds-trace-table-v69"
    assert ready["table_summary"]["accepted"] == 2 and "balance" not in ready["mapping"]
    job = base + "/imports/" + ready["import_id"]
    query = {"field": "balance", "member_path": "table.csv", "header_row": 1,
             "revision": ready["layout_review"]["revision"]}
    route = job + "/layout-review/model-trial"

    [declared] = source_balance_notes(client, job)
    assert declared == {"member_path": "table.csv", "header_row": 1, "column": 6, "heading": heading,
                        "source_rows": [2], "role": "available_balance", "account_column": None,
                        "subaccount_column": None, "unit_factor": "1",
                        "book_balance_timing": "unproved", "financial_admission": False}
    for col, row, role in ((6, 2, "own_reported_balance"), (7, 2, "available_balance"),
                           (6, 3, "available_balance")):
        refused = client.post(route, json={**query, "candidate": {"column": col, "source_row": row, "role": role}})
        assert refused.raise_for_status().json()["reason"] == "balance_citation_rejected"
    supported = client.post(route, json={**query,
            "candidate": {"column": 6, "source_row": 2, "role": "available_balance"}}).raise_for_status().json()
    assert supported["state"] == "rule" and supported["source_balance_roles"] == [declared]
    assert supported["prepared_preview"] is None and supported["first_calls"] == supported["recheck_calls"] == 0
    body = {"mode": ready["mode"], "mapping": ready["mapping"], "ready_revision": query["revision"]}
    wrong = client.post(job + "/preview", json={**body, "mapping": {**ready["mapping"], "balance": heading}})
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "invalid_mapping"
    updated = client.post(job + "/preview", json=body).raise_for_status().json()
    assert client.post(route, json=query).status_code == 409
    assert client.post(job + "/commit", json={**body, "ready_revision": updated["layout_review"]["revision"]}).status_code == 200
    receipt = poll(client, case, ready["import_id"])["result"]
    assert receipt["ok"] == 2
    with database._lock:
        database._con.close()
        database._con = None
    assert source_balance_notes(client, job) == [declared]
    source_rows = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()
    assert [[cell["value"] for cell in item["original"]] for item in source_rows["items"]] == rows
    flows = client.get(base + "/flows").raise_for_status().json()["items"]
    assert len(flows) == 2 and all(row["balance"] is None for row in flows)
    src = base + "/sources/" + ready["import_id"]
    assert client.delete(src + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert not source_balance_notes(client, job) and client.get(src + "/file").content == raw


def test_current_balance_cites_unpadded_tail_row_without_expanding_admission(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    columns = ["本方账号", "子账户序号", "交易时间", "借贷标志", "交易金额", "当前余额", "对方账号",
               "币种", "交易流水号", "原交易键"]
    rows = [
        ["000010", "02", "2041-04-03 08:00:00", "借", "3.80", "8.00", "000015", "CNY", "CB-1", ""],
        ["000010", "02", "2041-04-03 08:00:01", "贷", "2.10", "10.10", "000016", "CNY", "CB-2", ""],
        ["000010", "02", "2041-04-03 08:00:02", "贷", "0.50", "10.60", "000017", "CNY", "CB-3"],
    ]
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter="\t").writerows([columns, *rows])
    ready = client.post(base + "/imports/preview", files={"file": ("sample.csv", stream.getvalue().encode())}).raise_for_status().json()
    assert ready["table_summary"]["profile"] == "funds-trace-table-v108"
    assert ready["table_summary"]["reason_counts"] == {"transaction_valid": 2, "csv_tail_incomplete_record": 1}
    job = base + "/imports/" + ready["import_id"]
    [declared] = source_balance_notes(client, job)
    assert declared == {"member_path": "table.csv", "header_row": 1, "column": 6, "heading": "当前余额",
                        "source_rows": [2, 3, 4], "role": "own_reported_balance", "account_column": 1,
                        "subaccount_column": 2, "unit_factor": "1",
                        "book_balance_timing": "unproved", "financial_admission": False}
    query = {"field": "balance", "member_path": "table.csv", "header_row": 1,
             "revision": ready["layout_review"]["revision"],
             "candidate": {"column": 6, "source_row": 4, "role": "own_reported_balance"}}
    result = client.post(job + "/layout-review/model-trial", json=query).raise_for_status().json()
    assert result["state"] == "rule" and result["source_balance_roles"] == [declared]
    assert result["prepared_preview"] is None
    body = {"mode": ready["mode"], "mapping": ready["mapping"], "ready_revision": query["revision"]}
    assert client.post(job + "/commit", json=body).status_code == 200
    assert poll(client, case, ready["import_id"])["result"]["ok"] == 2
    review = client.get(job + "/review", params={"member_path": "table.csv", "view": "all"}).raise_for_status().json()["items"]
    assert [bool(row["flow_id"]) for row in review] == [True, True, False]
    assert [cell["value"] for cell in review[-1]["original"]] == rows[-1]
    assert source_balance_notes(client, job) == [declared]


@pytest.mark.parametrize("context", ["agreeing_owners", "owner_conflict", "recognized_owner_conflict",
                                     "same_peer_text", "本方卡号", "本人卡号", "卡号"])
def test_reported_balance_role_does_not_require_an_executable_financial_mapping(
        client, database, make_case, monkeypatch, context):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    # The first two fixtures retain a source header with clock, direction
    # and currency, without an admitted amount mapper. The third retains a
    # separately labeled opponent role whose text equals the book's account.
    card_only = context.endswith("卡号")
    if card_only:
        header = [context, "交易时间", "借贷标志", "币种", "反馈用途", "本次余额", "交易对手账卡号"]
        rows = [["000021", "2041-02-03 07:08:00", "贷", "CNY", "原表记载", "30.25", "000028"]]
    elif context == "same_peer_text":
        header = ["本方账号", "交易时间", "借贷标志", "币种", "交易金额", "本次余额", "对方账号"]
        rows = [["000021", "2041-02-03 07:08:00", "贷", "CNY", "10.25", "30.25", "000021"]]
    else:
        header = ["本人账号", "公司账号", "交易时间", "借贷标志", "币种", "本次余额", "对方账号"]
        if context == "recognized_owner_conflict":
            header[:2] = ["本方账号", "本方账户号码"]
        other = "000029" if context in {"owner_conflict", "recognized_owner_conflict"} else "000021"
        rows = [["000021", other, "2041-02-03 07:08:00", "贷", "CNY", "30.25", "000028"]]
    stream = io.StringIO(newline="")
    csv.writer(stream, delimiter="\t").writerows([header, *rows])
    ready = client.post(base + "/imports/preview", files={"file": ("source.csv", stream.getvalue().encode())}).raise_for_status().json()
    job = base + "/imports/" + ready["import_id"]
    body = {"field": "balance", "member_path": "table.csv", "header_row": 1,
            "revision": ready["layout_review"]["revision"]}
    action = job + "/layout-review/model-trial"
    answer = client.post(action, json=body).raise_for_status().json()
    assert answer["prepared_preview"] is None
    if context in {"owner_conflict", "recognized_owner_conflict"}:
        assert answer["state"] == "pending" and not source_balance_notes(client, job)
        return
    [declared] = source_balance_notes(client, job)
    assert answer["state"] == "rule" and answer["source_balance_roles"] == [declared]
    assert (declared["column"], declared["source_rows"]) == (6, [2])
    if card_only:
        assert declared["account_column"] is None and declared["card_column"] == 1
        note = client.get(job + "/intake").raise_for_status().json()["source_field_notes"][0]["text"]
        assert "原卡号回查" in note and "原账号回查" not in note
    else:
        assert declared["account_column"] == 1 and "card_column" not in declared
    assert declared["book_balance_timing"] == "unproved" and not declared["financial_admission"]
    rejected = client.post(action, json={**body, "candidate": {"column": 6, "source_row": 2,
                                                                "role": "available_balance"}}).raise_for_status().json()
    assert rejected["state"] == "pending"
    if card_only:
        confirmation = {"mode": ready["mode"], "mapping": ready["mapping"], "ready_revision": body["revision"]}
        assert client.post(job + "/commit", json=confirmation).status_code == 200
        assert poll(client, case, ready["import_id"])["result"]["ok"] == 0
        with database._lock:
            database._con.close()
            database._con = None
        assert source_balance_notes(client, job) == [declared]


@pytest.mark.parametrize("last_balance", ["0", "--"])
def test_balance_placeholders_do_not_witness_source_proof_even_after_old_cached_commit(
        client, database, make_case, monkeypatch, last_balance):
    from app.services import import_ready_evidence

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    rows = [
        ["本方账号", "交易时间", "借贷标志", "交易金额", "对方账号", "可用余额", "币种", "交易流水号"],
        ["000021", "2041-02-03 07:08:00", "贷", "32", "000031", "--", "CNY", "PLACE-1"],
        ["000021", "2041-02-03 07:08:01", "借", "22", "000032", last_balance, "CNY", "PLACE-2"],
    ]
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    raw = stream.getvalue().encode()
    ready = client.post(base + "/imports/preview", files={"file": ("sample.csv", raw)}).raise_for_status().json()
    job = base + "/imports/" + ready["import_id"]
    action = job + "/layout-review/model-trial"
    query = {"field": "balance", "member_path": "table.csv", "header_row": 1,
             "revision": ready["layout_review"]["revision"]}
    expect = [3] if last_balance == "0" else []
    assert [n for x in source_balance_notes(client, job) for n in x["source_rows"]] == expect
    response = client.post(action, json={**query, "candidate": {"column": 6, "source_row": 2, "role": "available_balance"}})
    assert response.raise_for_status().json()["state"] == "pending"
    # Simulate a receipt written before numeric witnesses had a proof version.
    stored = database.q_raw("select request_json,sha256 from import_sessions where import_id=?", [ready["import_id"]])[0]
    document = json.loads(stored["request_json"])
    key = import_ready_evidence._SOURCE_BALANCE_ROLES
    document[key] = {"sha256": stored["sha256"], "profile": ready["table_summary"]["profile"], "revision": query["revision"],
                     "roles": [{"member_path": "table.csv", "header_row": 1, "column": 6, "heading": "可用余额",
                                "source_rows": [2, 3], "unit_factor": "1", "role": "available_balance",
                                "book_balance_timing": "unproved", "financial_admission": False}]}
    database.x("update import_sessions set request_json=? where import_id=?", [json.dumps(document), ready["import_id"]])
    assert [n for x in source_balance_notes(client, job) for n in x["source_rows"]] == expect
    assert client.post(job + "/commit", json={"mode": ready["mode"], "mapping": ready["mapping"],
                                            "ready_revision": query["revision"]}).raise_for_status().json()["started"]
    receipt = poll(client, case, ready["import_id"])["result"]
    with database._lock:
        database._con.close()
        database._con = None
    assert [n for x in source_balance_notes(client, job) for n in x["source_rows"]] == expect
    source = base + "/sources/" + ready["import_id"]
    assert client.delete(source + "/operation", params={"expected_batch_id": receipt["batch_id"]}).status_code == 200
    assert not source_balance_notes(client, job) and client.get(source + "/file").content == raw
