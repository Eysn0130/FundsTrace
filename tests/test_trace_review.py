"""Public review reads and case/canvas/revision-bound manual graph writes."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from threading import Event

import pytest

from test_event_graph import imported, pair


def review(client, case, anchor, canvas=0, x=1):
    response = client.get(f"/api/context/{anchor}", params={
        "case_id": case, "canvas_id": canvas, "x": x, "review": "true",
    })
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("crowded", [False, True])
def test_trial_raw_card_text_competitors_keep_both_originals_pending(client, make_case, monkeypatch, crowded):
    from app import table_import
    from app.services import desktop_model_trial
    from test_automatic_table_import import complete, preview
    from test_table_multisheet_append import encoded

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("SYN card text leads")
    headers = sorted(table_import.BANK_FEEDBACK_39_COLUMNS)
    first_card, second_card = "1234500000000001", "1234500000000002"

    def entry(serial, owner, peer, own_card, marker, *, day="02", clock="03:04:05", currency="CNY"):
        values = {
            "交易卡号": own_card, "交易账号": owner, "主户名": "Synthetic holder",
            "交易日期": f"2026-01-{day}", "交易时间": f"2026-01-{day} {clock}",
            "收付标志": marker, "交易金额": "18.73", "交易余额": "35.80",
            "交易对手账卡号": peer, "交易币种": currency, "交易流水号": serial,
            "交易是否成功": "01",
        }
        return [values.get(column, "") for column in headers]

    first = entry("SYN-OUT", "SYN-A", second_card, first_card, "出")
    shared = ["SYN-B", first_card, second_card, "入"]
    remaining = [
        entry("SYN-BOUND-LATER", *shared, day="03"),
        entry("SYN-IN1", *shared),
        entry("SYN-IN2", *shared),
        entry("SYN-ALT", "SYN-B", "1234500000000003", second_card, "入"),
        entry("SYN-ALT-OWN", "SYN-B", first_card, "1234500000000003", "入"),
        entry("SYN-CLOCK", *shared, clock="03:04:06"),
        entry("SYN-CLOCK-BEFORE", *shared, clock="03:04:04"),
        entry("SYN-BEYOND-LATER", *shared, day="03", clock="03:04:06"),
        entry("SYN-BEYOND-BEFORE", *shared, day="01", clock="03:04:04"),
        entry("SYN-BEFORE-BOUND", *shared, day="01"),
        entry("SYN-CCY", *shared, currency="HKD"),
        entry("SYN-SAME-DIRECTION", first_card, "SYN-STRANGER", second_card, "出"),
        entry("SYN-OTHER-CLOCK-BASIS", *shared, clock="03:04:05+08:00"),
    ]
    extra = [f"SYN-COMPETITOR-{index}" for index in range(65)] if crowded else []
    remaining.extend(entry(label, *shared, clock="04:04:05") for label in extra)
    elsewhere = [entry(f"SYN-OTHER-SHEET-{index}", *shared) for index in range(65)]
    raw = encoded([("Independent sheet", [headers, *elsewhere]),
                   ("Transactions", [headers, first, *remaining, headers, entry("SYN-OTHER-REGION", *shared)])], ".xlsx")
    view = preview(client, cid, raw, "synthetic-card-mirrors.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v95"
    job = complete(client, cid, view)
    assert job["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations", "page_size": 200}).json()["items"]
    ids = {row["trade_no"]: row["id"] for row in rows}
    anchor = ids["SYN-OUT"]
    left = review(client, cid, anchor)
    card = left["review"]["pair"]
    assert card["status"] == "unknown" and card["reason"] == "source_card_text_pending"
    expected = ["SYN-IN1", "SYN-IN2", "SYN-CLOCK", "SYN-CLOCK-BEFORE", *extra,
                "SYN-BOUND-LATER", "SYN-BEFORE-BOUND"]
    assert card["candidate_ids"] == [ids[label] for label in expected[:64]]
    assert card["more"] is crowded
    right = review(client, cid, ids["SYN-CLOCK"])
    assert right["review"]["pair"]["candidate_ids"] == [anchor]
    original = client.get(
        f"/api/context/{anchor}/record/{ids['SYN-CLOCK']}", params={"case_id": cid},
        headers={"X-FundsTrace-Review": left["review"]["token"]},
    )
    position = 3 + next(index for index, row in enumerate(remaining) if row[headers.index("交易流水号")] == "SYN-CLOCK")
    assert original.status_code == 200 and original.json()["originals"][0]["source_row"] == position
    assert "同秒" not in original.json()["message"]
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (object(),))
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *_: pytest.fail("candidate leads must not call models"))
    request = {"kind": "pair", "anchor_id": anchor, "review_token": left["review"]["token"]}
    response = client.post(f"/api/v1/cases/{cid}/review-model-trial", json=request)
    value = response.json()
    assert response.status_code == 200, response.text
    assert value["receipt"]["status"] == "pending" and value["first_calls"] == value["recheck_calls"] == 0
    assert all("同秒" not in item["description"] for item in value["packet"]["candidates"])
    source = job["result"]["source_id"]
    response = client.delete(f"/api/v1/cases/{cid}/sources/{source}/operation",
                             params={"expected_batch_id": job["result"]["batch_id"]})
    assert response.status_code == 200, response.text
    assert client.get(f"/api/context/{anchor}", params={"case_id": cid, "review": "true"}).status_code == 404
    assert client.post(f"/api/v1/cases/{cid}/review-model-trial", json=request).status_code == 409


def update(client, case, token, desired, *, canvas=0, expected=()):
    return client.put("/api/track/state", json={
        "case_id": case, "canvas_id": canvas, "expected_ids": list(expected),
        "desired_ids": list(desired), "review_token": token,
    })


def test_review_rechecks_reference_competitors_outside_window_and_preserves_bounds(client, make_case):
    cid = make_case("SYN trace review proof")
    rows = imported(client, cid, pair("SYN-ANCHOR", "37.41"))
    anchor = next(row["id"] for row in rows if row["raw_self_account"] == "SYN-A")
    value = review(client, cid, anchor)
    assert value["review"]["pair"]["status"] == "confirmed"
    assert set(value["review"]["pair"]["candidate_ids"]) == {row["id"] for row in rows}
    assert value["review"]["directional_funds"]["forward"]["totals"][0]["amount"] == "37.41"
    assert value["review"]["directional_funds"]["booked"]["forward"] == {
        "cnt": 1, "total": "37.41", "amount_totals": [{"currency": "CNY", "amount": "37.41"}]}
    assert {row["balance"] for row in value["rows"]} == {"200.00", "50.00"}

    rows = imported(client, cid, pair("SYN-OTHER-ONE", day="02")
                    + pair("SYN-OTHER-TWO", day="03") + pair("SYN-ANCHOR", "38.42", day="04")[:1])
    far = next(row["id"] for row in rows if row["amount"] == "38.42")
    response = update(client, cid, value["review"]["token"], [anchor])
    assert response.status_code == 409, response.text
    changed = review(client, cid, anchor)
    assert changed["analysis_revision"] != value["analysis_revision"]
    assert changed["review"]["pair"]["status"] == "conflict"
    assert far in changed["review"]["pair"]["candidate_ids"]
    assert far in {row["id"] for row in changed["rows"]}
    funds = changed["review"]["directional_funds"]["forward"]
    assert funds["totals"][0]["amount"] is None
    assert funds["count"]["lower"] < funds["count"]["upper"]
    assert changed["review"]["directional_funds"]["booked"]["forward"]["cnt"] == 1
    assert changed["review"]["directional_funds"]["booked"]["forward"]["total"] == "37.41"


def test_review_manual_append_is_limited_to_current_canvas_window_and_original(client, make_case):
    cid, other = make_case("SYN bound review"), make_case("SYN another case")
    canvas = client.post("/api/canvases/save", json={
        "case_id": cid, "name": "SYN separate canvas", "snapshot": {},
    }).json()["canvas_id"]
    rows = imported(client, cid, pair("SYN-BIND") + pair("SYN-ADJACENT", day="02")
                    + pair("SYN-DISTANT", day="12"))
    anchor = next(row["id"] for row in rows if row["trade_no"] == "SYN-BIND-SYN-A")
    allowed = next(row["id"] for row in rows if row["trade_no"] == "SYN-BIND-SYN-B")
    outside = next(row["id"] for row in rows if row["trade_no"] == "SYN-DISTANT-SYN-A")
    value = review(client, cid, anchor, canvas)
    token = value["review"]["token"]
    link = f"/api/context/{anchor}/record/{allowed}"
    params = {"case_id": cid, "canvas_id": canvas}
    headers = {"X-FundsTrace-Review": token}
    original = client.get(link, params=params, headers=headers)
    assert original.status_code == 200, original.text
    record = original.json()["originals"][0]
    assert record["source_row"] == 3
    assert record["cells"][0] == {
        "column": 1, "header": "交易流水号", "value": "SYN-BIND-SYN-B", "fields": ["trade_no"], "kind": "text",
    }
    assert client.get(record["download_url"]).status_code == 200
    assert client.get(link, params={**params, "case_id": other}, headers=headers).status_code == 409
    assert update(client, cid, token, [allowed]).status_code == 409
    assert update(client, cid, token, [outside], canvas=canvas).status_code == 409
    applied = update(client, cid, token, [allowed], canvas=canvas)
    assert applied.status_code == 200, applied.text
    assert list(map(str, applied.json()["tracked_ids"])) == [allowed]
    assert update(client, cid, token, [], canvas=canvas, expected=[allowed]).status_code == 409
    assert client.delete("/api/context/review", params=params, headers=headers).status_code == 200
    assert client.get(link, params=params, headers=headers).status_code == 409
    assert update(client, cid, token, [allowed, anchor], canvas=canvas, expected=[allowed]).status_code == 409


def ledger_row(database, case, payer, payee, own, stamp, reference, amount="25.00"):
    return database.q_raw("""insert into flows(case_id,payer_account,payee_account,self_account,
        trade_time,trade_no,amount,currency,balance) values(?,?,?,?,?,?,?,'CNY',10) returning id""",
        [case, payer, payee, own, stamp, reference, amount])[0]["id"]


def test_only_own_ledger_and_proved_anchor_confer_clue_order(client, database, make_case):
    cid = make_case("SYN own ledger recheck")
    anchor = ledger_row(database, cid, "A", "B", "A", "2026-01-01 10:00:00", "SYN-ANCHOR")
    credit = ledger_row(database, cid, "C", "A", "A", "2026-01-01 09:59:00", "SYN-A-CREDIT")
    other_book = ledger_row(database, cid, "B", "D", "D", "2026-01-01 10:01:00", "SYN-OTHER-BOOK")
    undated = ledger_row(database, cid, "B", "F", "B", None, "SYN-NO-TIME")
    value = review(client, cid, anchor, x=5)
    assert value["suggestions"]["sources"][0]["ids"] == [str(credit)]
    assert value["suggestions"]["targets"] == []
    assert value["review"]["principal"]["target"]["reasons"] == ["own_ledger_anchor_missing"]
    roles = {row["id"]: row for row in value["rows"]}
    assert roles[str(other_book)]["review_roles"] == []
    assert roles[str(other_book)]["context_relation"] == "unordered"
    assert roles[str(undated)]["trade_time"] is None
    assert roles[str(undated)]["review_fields"]["book_direction"] is None
    assert roles[str(undated)]["review_fields"]["trade_time_precision"] is None
    assert roles[str(undated)]["review_roles"] == []

    mirror = ledger_row(database, cid, "A", "B", "B", "2026-01-01 10:00:00", "SYN-MIRROR")
    actual_debit = ledger_row(database, cid, "B", "E", "B", "2026-01-01 10:02:00", "SYN-B-DEBIT")
    after = review(client, cid, anchor, x=5)
    assert after["review"]["pair"]["status"] == "unknown"
    assert after["review"]["pair"]["candidate_ids"] == [str(mirror)]
    assert after["suggestions"]["targets"] == []
    assert next(row for row in after["rows"] if row["id"] == str(actual_debit))["context_relation"] == "unordered"


def test_native_single_booking_is_exact_money_without_claiming_paired_ledgers(client, make_case):
    from test_bank_posting_pairs import encoded
    from test_bank_single_postings import entries
    from test_source_ledger import add_source

    cid = make_case("SYN single booking review")
    records = entries()
    records[1]["备注"] = "合成备注与摘要分开"
    records[1]["摘要说明"] = "合成摘要"
    add_source(client, cid, encoded(records))
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    anchor = next(row["id"] for row in rows if row["trade_no"] == "SYN-SINGLE-1")
    value = review(client, cid, anchor)
    assert value["review"]["pair"]["status"] == "unknown"
    assert value["review"]["pair"]["reason"] == "single_posting_independent_balance_proof"
    assert value["review"]["directional_funds"]["forward"]["status"] == "exact"
    row = next(row for row in value["rows"] if row["id"] == anchor)
    assert row["review_fields"]["direction"] == "出"
    assert (row["review_fields"]["book_direction"], row["review_fields"]["book_direction_basis"]) == ("out", "source_marker")
    assert row["review_fields"]["counter_kind"] == "account_or_card"
    assert row["review_fields"]["counter_card"] == ""
    assert row["review_fields"]["counter_account"] == "SYN-SINGLE-B"
    assert row["review_fields"]["remark"] == "合成备注与摘要分开"
    assert row["summary"] == "合成摘要"


def test_review_source_accounts_supply_direction_and_original_time_precision(client, make_case):
    from test_automatic_table_import import preview
    from test_direction_recheck import csv_bytes
    from test_feedback_import_api import complete

    case = make_case("SYN explicit payer and payee review")
    headers = ["交易流水号", "交易时间", "付款账号", "付款人开户行", "收款账号", "收款人开户行",
               "本方账号", "本方开户银行", "交易金额", "本笔交易后账面余额", "交易币种", "记账顺序号",
               "参考号机构", "参考号渠道", "参考号业务域", "跨账簿事件参考号"]
    records = [
        ("SYN-DATE", "2026-07-04", "SYN-PEER", "SYN-BOOK", "SYN-BOOK", "0.40"),
        ("SYN-MIDNIGHT", "2026-07-05 00:00:00", "SYN-BOOK", "SYN-PEER", "SYN-BOOK", "0.60"),
        ("SYN-NO-BOOK", "2026-07-05 01:10:00", "SYN-PEER", "SYN-OTHER", "", "0.80"),
    ]
    payload = csv_bytes(headers, *[
        (ref, stamp, payer, "SYN BANK", payee, "SYN BANK", own, "SYN BANK" if own else "", amount,
         "10.00", "CNY", line, "SYN ISSUER", "SYN CHANNEL", "SYN DOMAIN", ref)
        for line, (ref, stamp, payer, payee, own, amount) in enumerate(records, 1)
    ])
    imported = preview(client, case, payload)
    assert complete(client, case, imported)["result"]["ok"] == 3
    path = f"/api/v1/cases/{case}/flows"
    before = client.get(path).json()
    entries = {row["trade_no"]: row for row in before["items"]}
    expectations = {
        "SYN-DATE": ("in", "source_own_endpoint", "date", "2026-07-04"),
        "SYN-MIDNIGHT": ("out", "source_own_endpoint", "time", None),
        "SYN-NO-BOOK": (None, None, "time", None),
    }
    for line, (reference, raw_stamp, payer, payee, own, _) in enumerate(records, 2):
        flow = entries[reference]
        assert flow["trade_time"] == ("2026-07-04 00:00:00" if reference == "SYN-DATE" else raw_stamp)
        opened = review(client, case, flow["id"])
        anchor = next(row for row in opened["rows"] if row["id"] == flow["id"])
        fields = anchor["review_fields"]
        assert fields["direction"] is None
        assert tuple(fields[key] for key in ("book_direction", "book_direction_basis",
                                             "trade_time_precision", "trade_time_source_date")) == expectations[reference]
        assert anchor["review_original"]["source_row"] == line
        source = client.get(f"/api/context/{flow['id']}/record/{flow['id']}", params={"case_id": case},
                            headers={"X-FundsTrace-Review": opened["review"]["token"]})
        assert source.status_code == 200, source.text
        [original] = source.json()["originals"]
        cells = {cell["header"]: cell["value"] for cell in original["cells"]}
        assert original["source_row"] == line
        # The original detail still shows the actual source bytes and columns.
        assert [cells[key] for key in ("交易时间", "付款账号", "收款账号", "本方账号")] == [raw_stamp, payer, payee, own]
        assert client.get(original["download_url"]).content == payload
    assert client.get(path).json() == before


def test_review_marks_workbook_date_or_midnight_when_journal_loses_the_cell_type(client, make_case):
    from datetime import date, datetime
    from io import BytesIO

    from openpyxl import Workbook
    from test_automatic_table_import import preview
    from test_feedback_import_api import complete

    case = make_case("SYN workbook calendar precision")
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["交易流水号", "交易时间", "付款账号", "收款账号", "交易金额", "交易币种"])
    sheet.append(["SYN-TYPED-DATE", date(2026, 7, 5), "SYN-A", "SYN-B", "1.00", "CNY"])
    sheet.append(["SYN-TYPED-MIDNIGHT", datetime(2026, 7, 6), "SYN-A", "SYN-B", "2.00", "CNY"])
    sheet.append(["SYN-TEXT-DATE", "2026-07-07", "SYN-A", "SYN-B", "3.00", "CNY"])
    buffer = BytesIO()
    workbook.save(buffer)
    source = preview(client, case, buffer.getvalue(), "synthetic.xlsx")
    assert complete(client, case, source)["result"]["ok"] == 3
    expected = {"SYN-TYPED-DATE": ("date_or_midnight", "2026-07-05"),
                "SYN-TYPED-MIDNIGHT": ("date_or_midnight", "2026-07-06"),
                "SYN-TEXT-DATE": ("date", "2026-07-07")}
    for row in client.get(f"/api/v1/cases/{case}/flows").json()["items"]:
        opened = review(client, case, row["id"])
        fields = next(item["review_fields"] for item in opened["rows"] if item["id"] == row["id"])
        assert (fields["trade_time_precision"], fields["trade_time_source_date"]) == expected[row["trade_no"]]
    assert client.get(f"/api/v1/cases/{case}/sources/{source['import_id']}/file").content == buffer.getvalue()


def test_review_marks_midnight_unproved_when_original_clock_excerpts_are_limited(client, make_case):
    from test_automatic_table_import import preview
    from test_direction_recheck import csv_bytes
    from test_feedback_import_api import complete

    case = make_case("SYN truncated source clock evidence")
    payload = csv_bytes(
        ["交易流水号", "交易时间", *(["记账时间"] * 9), "本方账号", "付款账号", "收款账号", "交易金额", "交易币种"],
        ("SYN-LIMITED-TIME", "2026-07-05", *(["2026-07-06 01:02:03"] * 9), "SYN-A", "SYN-A", "SYN-B", "0.50", "CNY"),
    )
    uploaded = preview(client, case, payload)
    assert complete(client, case, uploaded)["result"]["ok"] == 1
    base = f"/api/v1/cases/{case}"
    [row] = client.get(base + "/flows").json()["items"]
    assert row["trade_time"] == "2026-07-05 00:00:00"
    records = client.get(base + "/imports/" + uploaded["import_id"] + "/records").json()["items"]
    [original_row] = [item for item in records if item["disposition"] == "imported"]
    assert original_row["details"]["source_time_evidence_limited"] == "true"
    opened = review(client, case, row["id"])
    fields = next(item["review_fields"] for item in opened["rows"] if item["id"] == row["id"])
    assert (fields["trade_time_precision"], fields["trade_time_source_date"]) == ("date_or_midnight", "2026-07-05")


@pytest.mark.parametrize("headers,records,expected", [
    (["交易流水号", "交易时间", "付款账号", "收款账号", "本方账号", "交易金额", "交易币种"],
     [("SYN-MISSING-TIME", "", "SYN-PEER", "SYN-BOOK", "SYN-BOOK", "4.20", "CNY")],
     "missing_time"),
    (["交易流水号", "交易时间", "本方账号", "对方账号", "收付标志", "交易金额", "交易币种"],
     [("SYN-MISSING-DIRECTION", "2026-07-05 12:00:00", "SYN-BOOK", "SYN-PEER", "", "4.20", "CNY")],
     "missing_direction"),
])
def test_review_required_source_facts_are_retained_without_effective_flow(client, make_case, headers, records, expected):
    from test_automatic_table_import import preview
    from test_direction_recheck import csv_bytes
    from test_feedback_import_api import complete

    case = make_case("SYN missing or conflicting source facts")
    payload = csv_bytes(headers, *records)
    imported = preview(client, case, payload)
    result = complete(client, case, imported)
    assert result["stage"] == "done" and result["result"]["ok"] == 0, json.dumps(result, ensure_ascii=False)
    [record] = client.get(f"/api/v1/cases/{case}/imports/{imported['import_id']}/records").json()["items"]
    assert record["disposition"] == "unresolved" and record["flow_id"] is None
    assert expected in record["details"]["validation_issues"].split(",")
    flow_route = f"/api/v1/cases/{case}/flows"
    effective = client.get(flow_route, params={"granularity": "events"})
    assert effective.status_code == 200 and effective.json()["total"] == 0
    assert client.get(f"/api/v1/cases/{case}/sources/{imported['import_id']}/file").content == payload


@pytest.mark.parametrize("include_valid_row", [False, True])
def test_import_conflicting_book_direction_retains_original_without_rolling_back_valid_rows(client, make_case, include_valid_row):
    from test_automatic_table_import import preview
    from test_direction_recheck import csv_bytes
    from test_feedback_import_api import complete

    case = make_case("SYN opposite declared and observed source direction")
    headers = ["交易流水号", "交易时间", "付款账号", "收款账号", "本方账号", "收付标志", "交易金额", "交易币种"]
    rows = [("SYN-CONFLICT", "2026-07-05 12:00:00", "SYN-PEER", "SYN-BOOK", "SYN-BOOK", "出", "4.20", "CNY")]
    if include_valid_row:
        rows.append(("SYN-VALID", "2026-07-05 13:00:00", "SYN-PEER", "SYN-BOOK", "SYN-BOOK", "入", "0.85", "CNY"))
    payload = csv_bytes(headers, *rows)
    imported = preview(client, case, payload)
    job = complete(client, case, imported)
    assert job["stage"] == "done" and job["result"]["ok"] == int(include_valid_row), json.dumps(job, ensure_ascii=False)
    result = client.get(f"/api/v1/cases/{case}/imports/{imported['import_id']}/records").json()
    first = result["items"][0]
    assert (first["source_row"], first["disposition"], first["reason_code"], first["flow_id"]) == (
        2, "unresolved", "conflicting_direction", None)
    assert "conflicting_direction" in first["details"]["validation_issues"].split(",")
    assert "source_ledger_evidence" not in first["details"]
    effective = client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "events"})
    assert effective.status_code == 200
    assert [item["trade_no"] for item in effective.json()["items"]] == (["SYN-VALID"] if include_valid_row else [])
    assert client.get(f"/api/v1/cases/{case}/sources/{imported['import_id']}/file").content == payload


def test_review_retains_conflicting_historical_marker_without_confirming_book_direction():
    from app.services import trace_review

    accounts = {"self": "SYN-BOOK", "payer": "SYN-BOOK", "payee": "SYN-PEER"}
    row = {name + "_account": account for name, account in accounts.items()}
    row.update({"raw_" + name + "_account": account for name, account in accounts.items()})
    evidence = {"role": "transaction", "own_side": "payer",
                "endpoints": {name: {"account": account} for name, account in accounts.items()}}
    fields = trace_review._outline(row, {"direction": "入", "identity_evidence": json.dumps(evidence)})
    assert (fields["direction"], fields["book_direction"], fields["book_direction_basis"]) == ("入", None, None)


def test_review_uses_counter_account_only_from_the_row_bound_table_heading(client, make_case):
    from test_automatic_table_import import preview
    from test_direction_recheck import HEADER, csv_bytes, row
    from test_feedback_import_api import complete

    cid = make_case("SYN table type retained in review")
    uploaded = preview(client, cid, csv_bytes(HEADER, row("X2", "405")))
    assert complete(client, cid, uploaded)["stage"] == "done"
    anchor = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["id"]
    fields = review(client, cid, anchor)["rows"][0]["review_fields"]
    assert fields["direction"] == "X2"
    assert fields["counter_card"] is None
    assert fields["counter_account"] == "000SYN-B"
    assert fields["counter_kind"] == "account"


def test_review_does_not_label_card_fallback_as_an_account(client, make_case):
    from test_automatic_table_import import preview
    from test_direction_recheck import HEADER, csv_bytes, row
    from test_feedback_import_api import complete

    cid = make_case("SYN counterparty account and card columns")
    blank = row("X2", "405")
    blank[2] = ""
    uploaded = preview(client, cid, csv_bytes(
        [*HEADER, "交易对方卡号"], [*blank, "SYN-CARD-ONLY"], [*row("X2", "607", hour=11), "SYN-CARD-BOTH"],
    ))
    assert complete(client, cid, uploaded)["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    assert {item["raw_payee_account"] for item in rows} == {"SYN-CARD-ONLY", "000SYN-B"}
    for item in rows:
        anchor = item["id"]
        result = review(client, cid, anchor)
        fields = next(posting["review_fields"] for posting in result["rows"] if posting["id"] == anchor)
        assert fields["counter_account"] is None
        assert fields["counter_kind"] is None
        response = client.get(f"/api/context/{anchor}/record/{anchor}", params={"case_id": cid},
                              headers={"X-FundsTrace-Review": result["review"]["token"]})
        assert response.status_code == 200, response.text
        cells = {entry["header"]: entry["value"] for entry in response.json()["originals"][0]["cells"]}
        assert cells["对方账号"] == ("" if item["amount"] == "4.05" else "000SYN-B")
        assert cells["交易对方卡号"] == ("SYN-CARD-ONLY" if item["amount"] == "4.05" else "SYN-CARD-BOTH")


@pytest.mark.parametrize("layout", ["automatic_account", "mapped_note", "two_accounts", "equal_card", "mapped_card"])
def test_review_checks_old_manual_mapping_roles_without_rewriting_the_receipt(client, database, make_case, monkeypatch, layout):
    from app.services import imports
    from test_automatic_table_import import preview
    from test_direction_recheck import csv_bytes
    from test_feedback_import_api import complete

    case = make_case("SYN historical manual table mapping")
    headings = ["交易时间", "本方账号", "对方账号", "复核说明账号", "交易金额", "收付标志", "交易币种", "户名说明"]
    account = "" if layout == "mapped_card" else "SYN-PEER"
    cells = ["2026-01-01 10:00:00", "SYN-OWN", account, "SYN-NOTE", "100.00", "出", "CNY", "合成户名"]
    if layout == "equal_card":
        headings.append("交易对方卡号")
        cells.append("SYN-NOTE")
    if layout == "two_accounts":
        headings.append("对手账号")
        cells.append("SYN-COMPETING-PEER")
    raw = csv_bytes(headings, cells)
    view = preview(client, case, raw)
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    path = f"/api/v1/cases/{case}/imports/{view['import_id']}"
    trusted = layout == "automatic_account"
    field = "counter_card" if layout == "mapped_card" else "counter_account"
    old_column = "对方账号" if layout in {"automatic_account", "two_accounts"} else "复核说明账号"
    mapping = {**view["mapping"], "self_name": "户名说明", field: old_column}

    # Write a real completion through the public workflow with the historical
    # producer's evidence encoding and request shape.
    parse = imports._parse

    def historical_parse(*args, **kwargs):
        return parse(*args, **{**kwargs, "manual_mapping_evidence": False})

    with monkeypatch.context() as historical:
        historical.setattr(imports, "_parse", historical_parse)
        if not trusted:
            ready = client.post(path + "/preview", json={"mode": "B", "mapping": mapping})
            assert ready.status_code == 200, ready.text
            view = ready.json()
        finished = complete(client, case, view)
        assert finished["stage"] == "done" and finished["result"]["ok"] == 1, finished
    stored = database.q_raw("select request_json,result_json from import_sessions where import_id=?", [view["import_id"]])[0]
    request = json.loads(stored["request_json"])
    assert request["_table_overrides"] is (not trusted)
    assert request.pop("_manual_mapping_evidence_v1") is (not trusted)
    database.x("update import_sessions set request_json=? where import_id=?", [json.dumps(request), view["import_id"]])
    [old_evidence] = [entry for entry in finished["result"]["table_summary"]["mapping_evidence"]
                      if (entry["field"], entry["column"]) == (field, old_column)]
    assert old_evidence["basis"] == "exact_header_alias"

    [flow] = client.get(f"/api/v1/cases/{case}/flows").json()["items"]
    assert flow["raw_payee_account"] == ("SYN-PEER" if old_column == "对方账号" else "SYN-NOTE")
    result = review(client, case, flow["id"])
    [row] = result["rows"]
    token = {"X-FundsTrace-Review": result["review"]["token"]}
    link = f"/api/context/{flow['id']}/record/{flow['id']}"
    record = client.get(link, params={"case_id": case}, headers=token)
    assert record.status_code == 200, record.text
    [original] = record.json()["originals"]
    column = {cell["header"]: cell for cell in original["cells"]}
    cited_column = "交易对方卡号" if layout == "equal_card" else old_column
    scope = {"source_id": view["import_id"], "sha256": sha256(raw).hexdigest(),
             "parser_version": "funds-trace-table-v69", "member_path": "table.csv",
             "source_row": 2, "header_row": 1}
    response = client.post(link + "/verify", headers=token, json={
        "case_id": case, "analysis_revision": result["analysis_revision"],
        "candidate": "card" if layout in {"equal_card", "mapped_card"} else "account",
        "citation": {**scope, "column": column[cited_column]["column"]},
    })
    assert response.status_code == 200, response.text
    assert response.json()["evidence_status"] == ("supported" if trusted else "insufficient")
    assert row["review_fields"]["counter_kind"] == ("account" if trusted else None)
    assert row["review_fields"]["counter_account"] == ("SYN-PEER" if trusted else None)
    assert column["复核说明账号"]["value"] == "SYN-NOTE"
    assert "amount" in column["交易金额"]["fields"]
    assert (field in column[old_column]["fields"]) is trusted
    if layout in {"automatic_account", "two_accounts", "mapped_note"}:
        assert original["citation_scope"] == (scope if trusted else None)
    assert client.get(original["download_url"]).content == raw
    assert client.get(path).json()["result"] == finished["result"]
    assert database.q_raw("select result_json from import_sessions where import_id=?",
                          [view["import_id"]])[0]["result_json"] == stored["result_json"]


@pytest.mark.parametrize("manual_counterparty", [False, True])
def test_event_review_uses_source_columns_for_typed_accounts(client, make_case, manual_counterparty):
    from app import event_resolution, import_preview_cache
    from test_automatic_table_import import preview
    from test_event_graph import graph, track
    from test_event_import import csv_rows
    from test_feedback_import_api import complete

    case = make_case("SYN manual observation with event reference")
    values = {
        "交易时间": "2026-01-01 10:00:00", "本方开户银行": "SYN-BANK", "对手开户银行": "SYN-BANK",
        "参考号机构": "SYN-ISSUER", "参考号渠道": "SYN-CHANNEL", "参考号业务域": "SYN-SCOPE",
        "跨账簿事件参考号": "SYN-REFERENCE", "交易金额": "100.00", "交易币种": "CNY",
    }
    sender = {**values, "交易流水号": "SYN-LEFT", "本方账号": "SYN-A", "收付标志": "出",
              "对方账号": "SYN-OTHER" if manual_counterparty else "SYN-B",
              "复核说明账号": "SYN-B", "户名说明": "合成户名"}
    recipient = {**values, "交易流水号": "SYN-RIGHT", "本方账号": "SYN-B", "收付标志": "进", "对方账号": "SYN-A"}
    source = preview(client, case, csv_rows([sender]))
    assert source["table_summary"]["profile"] == "funds-trace-table-v71"
    source_path = f"/api/v1/cases/{case}/imports/{source['import_id']}"
    mapping = {**source["mapping"], "self_name": "户名说明"}
    if manual_counterparty:
        mapping["counter_account"] = "复核说明账号"
    confirmed = client.post(source_path + "/preview", json={"mode": "B", "mapping": mapping})
    assert confirmed.status_code == 200, confirmed.text
    ready = confirmed.json()
    [entry] = [entry for entry in ready["table_summary"]["mapping_evidence"] if entry["field"] == "counter_account"]
    assert entry["basis"] == ("manual_override" if manual_counterparty else "exact_header_alias")
    import_preview_cache.discard(source["import_id"])
    assert complete(client, case, ready)["result"]["ok"] == 1
    opposite = preview(client, case, csv_rows([recipient]), "synthetic-receiving.csv")
    assert complete(client, case, opposite)["result"]["ok"] == 1

    flows = client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "observations"}).json()["items"]
    assert len(flows) == 2
    anchor = next(item for item in flows if item["trade_no"] == "SYN-LEFT")
    assert (anchor["raw_payer_account"], anchor["raw_payee_account"], anchor["amount"]) == ("SYN-A", "SYN-B", "100.00")
    [record] = client.get(source_path + "/records").json()["items"]
    assert event_resolution.validate_reference(record["details"][event_resolution.DETAIL_FIELD])["reference"] == "SYN-REFERENCE"
    if manual_counterparty:
        assert "resolution_evidence" not in record["details"]
    else:
        identity = json.loads(record["details"]["resolution_evidence"])
        for role, account in zip(("payer", "payee", "self"), ("SYN-A", "SYN-B", "SYN-A"), strict=True):
            assert (identity["endpoints"][role]["account"], identity["endpoints"][role]["identifier_type"]) == (account, "account")
    result = review(client, case, anchor["id"])
    selected = next(item for item in result["rows"] if item["id"] == anchor["id"])
    assert selected["review_fields"]["counter_account"] == (None if manual_counterparty else "SYN-B")
    expected = "unknown" if manual_counterparty else "confirmed"
    assert result["review"]["pair"]["status"] == expected
    track(client, case, flows)
    assert graph(client, case)["case_event_summary"]["confirmed_events"] == (0 if manual_counterparty else 1)
    assert client.get(f"/api/v1/cases/{case}/sources/{source['import_id']}/file").content == csv_rows([sender])

    package = client.get(f"/api/cases/{case}/export")
    assert package.status_code == 200
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    restored_case = restored.json()["case_id"]
    restored_flows = client.get(f"/api/v1/cases/{restored_case}/flows", params={"granularity": "observations"}).json()["items"]
    assert len(restored_flows) == 2
    restored_anchor = next(item for item in restored_flows if item["trade_no"] == "SYN-LEFT")
    assert tuple(restored_anchor[key] for key in ("raw_payer_account", "raw_payee_account", "amount")) == (
        "SYN-A", "SYN-B", "100.00",
    )
    assert review(client, restored_case, restored_anchor["id"])["review"]["pair"]["status"] == expected
    assert graph(client, restored_case)["case_event_summary"]["confirmed_events"] == (0 if manual_counterparty else 1)


@pytest.mark.parametrize(("mode", "override", "direction", "expected"), [
    ("A", "payer_account", "进", None),
    ("A", "payee_account", "出", None),
    ("A", "payer_account", "出", ("SYN-A", "SYN-B", "SYN-A")),
    ("B", "self_account", "出", None),
    ("B", "counter_account", "进", None),
    ("B", "counter_card", "出", None),
])
def test_manual_account_proof_keeps_independently_declared_roles(client, make_case, mode, override, direction, expected):
    from test_automatic_table_import import preview
    from test_event_import import csv_rows
    from test_feedback_import_api import complete

    case = make_case("SYN manually mapped identity roles")
    outgoing = direction == "出"
    row = {
        "交易流水号": "SYN-ROLES", "交易时间": "2026-01-01 10:00:00", "本方开户银行": "SYN-BANK",
        "交易金额": "100.00", "交易币种": "CNY", "收付标志": direction,
        "参考号机构": "SYN-ISSUER", "参考号渠道": "SYN-CHANNEL", "参考号业务域": "SYN-SCOPE",
        "跨账簿事件参考号": "SYN-REFERENCE", "本方账号": "SYN-A" if outgoing else "SYN-B",
        "复核说明账号": "SYN-B" if override in {"payee_account", "counter_card"} else "SYN-A",
    }
    if mode == "A":
        row.update({"付款账号": "SYN-OTHER" if override == "payer_account" else "SYN-A",
                    "收款账号": "SYN-OTHER" if override == "payee_account" else "SYN-B",
                    "付款人开户行": "SYN-BANK", "收款人开户行": "SYN-BANK"})
    else:
        row.update({"对手开户银行": "SYN-BANK", "对方账号": "SYN-B" if outgoing else "SYN-OTHER"})
        if override == "self_account":
            row["本方账号"] = "SYN-OTHER"
        if override == "counter_card":
            row["对方账号"] = ""
    source = preview(client, case, csv_rows([row]))
    assert source["table_summary"]["profile"] == "funds-trace-table-v71"
    base = f"/api/v1/cases/{case}/imports/{source['import_id']}"
    ready = client.post(base + "/preview", json={"mode": mode,
                                                "mapping": {**source["mapping"], override: "复核说明账号"}})
    assert ready.status_code == 200, ready.text
    [entry] = [item for item in ready.json()["table_summary"]["mapping_evidence"] if item["field"] == override]
    assert entry["basis"] == "manual_override"
    assert complete(client, case, ready.json())["result"]["ok"] == 1
    [record] = client.get(base + "/records").json()["items"]
    if expected is None:
        assert "resolution_evidence" not in record["details"]
    else:
        roles = json.loads(record["details"]["resolution_evidence"])["endpoints"]
        for role, account in zip(("payer", "payee", "self"), expected, strict=True):
            assert (roles[role]["account"], roles[role]["identifier_type"]) == (account, "account")
    [observation] = client.get(f"/api/v1/cases/{case}/flows").json()["items"]
    assert (observation["raw_payer_account"], observation["raw_payee_account"]) == ("SYN-A", "SYN-B")


def test_review_verifies_only_the_exact_counterparty_identifier_column(client, make_case):
    from test_automatic_table_import import preview
    from test_direction_recheck import HEADER, csv_bytes, row
    from test_feedback_import_api import complete

    cid = make_case("SYN exact source column proof")
    card = row("X2", "405")
    card[2] = ""
    account = row("X2", "405", hour=11)
    raw = csv_bytes([*HEADER, "交易对方卡号", "备注"], [*card, "SYN-SAME", "SYN-SAME"],
                    [*account, account[2], account[2]])
    source = preview(client, cid, raw)
    assert complete(client, cid, source)["stage"] == "done"
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    for expected_kind, expected_column, position, member in (
        ("card", 7, 2, "SYN-SAME"), ("account", 3, 3, account[2]),
    ):
        anchor = next(item["id"] for item in flows if item["raw_payee_account"] == member)
        result = review(client, cid, anchor)
        params = {"case_id": cid}
        headers = {"X-FundsTrace-Review": result["review"]["token"]}
        link = f"/api/context/{anchor}/record/{anchor}"
        scope = {"source_id": source["import_id"], "sha256": sha256(raw).hexdigest(),
                 "parser_version": source["table_summary"]["profile"], "member_path": "table.csv",
                 "source_row": position, "header_row": 1}
        body = {**params, "analysis_revision": result["analysis_revision"],
                "candidate": expected_kind, "citation": {**scope, "column": expected_column}}
        response = client.post(link + "/verify", json=body, headers=headers)
        assert response.status_code == 200, response.text
        original = client.get(link, params=params, headers=headers).json()["originals"][0]
        assert original["citation_scope"] == scope
        outcome = response.json()
        assert outcome["evidence_status"] == "supported"
        assert outcome["business_status"] == "pending"
        assert outcome["classification"] == "rule_control"
        assert outcome["analysis_revision"] == result["analysis_revision"]
        for changed in ({"column": 8}, {"source_row": 3 if position == 2 else 2},
                        {"member_path": "different/table.csv"}, {"header_row": 2}, {"sha256": "0" * 64},
                        {"source_id": "0" * 32}, {"parser_version": "unsupported"}):
            wrong = client.post(link + "/verify", json={**body, "citation": {**body["citation"], **changed}},
                                headers=headers)
            assert wrong.status_code == 200, wrong.text
            assert wrong.json()["evidence_status"] == "refuted"
        opposite = "account" if expected_kind == "card" else "card"
        wrong_type = client.post(link + "/verify", json={**body, "candidate": opposite,
            "citation": {**scope, "column": 3 if expected_column == 7 else 7}}, headers=headers)
        assert wrong_type.status_code == 200, wrong_type.text
        assert wrong_type.json()["evidence_status"] == "refuted"
        assert client.get("/api/graph", params=params).json()["tracked_ids"] == []
        assert client.get(link, params=params, headers=headers).json()["originals"][0]["cells"] == original["cells"]


def test_review_checks_both_header_cells_in_the_bound_xlsx_region(client, make_case):
    from test_automatic_table_import import preview
    from test_feedback_import_api import complete
    from test_xlsx_tiered_headers import workbook

    cid = make_case("SYN two-level counterparty column")
    headings = [
        ("交易", "本方", "对方", "本笔发生额", "账面余额", "业务", "凭据"),
        ("年月日", "账户标识", "账户标识", "分", "元", "收付标志", "币种"),
    ]
    raw = workbook(
        [*headings, ("2026-01-07", "000SYN-A", "000SYN-B", "301", "700.00", "出", "CNY")],
        [*headings, ("2026-01-08", "000SYN-A", "000SYN-B", "301", "400.00", "出", "CNY")],
    )
    source = preview(client, cid, raw, "tier.xlsx")
    assert source["table_summary"]["profile"] == "funds-trace-table-v94"
    job = complete(client, cid, source)
    assert job["stage"] == "done" and job["result"]["ok"] == 2, job
    rows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    anchor = next(row["id"] for row in rows if row["trade_time"].startswith("2026-01-07"))
    value = review(client, cid, anchor)
    headers = {"X-FundsTrace-Review": value["review"]["token"]}
    path = f"/api/context/{anchor}/record/{anchor}"
    original = client.get(path, params={"case_id": cid}, headers=headers).json()["originals"][0]
    assert (original["citation_scope"]["header_row"], original["citation_scope"]["source_row"]) == (2, 3)
    for citation, expected in [
        ({"column": 3}, "supported"), ({"column": 2}, "refuted"),
        ({"member_path": "worksheets/0002", "column": 3}, "refuted"),
    ]:
        response = client.post(path + "/verify", headers=headers, json={
            "case_id": cid, "analysis_revision": value["analysis_revision"], "candidate": "account",
            "citation": {**original["citation_scope"], **citation},
        })
        assert response.status_code == 200, response.text
        assert response.json()["evidence_status"] == expected


@pytest.mark.parametrize(("first_is_b", "counter_heading"), [(False, "对方账号"), (True, "对方账户"),
                                                               (True, "对方账户号码")])
def test_review_identifies_the_cited_region_on_mixed_mode_tables(client, make_case, first_is_b, counter_heading):
    from test_automatic_table_import import preview
    from test_direction_recheck import HEADER, csv_bytes, row
    from test_feedback_import_api import complete

    cid = make_case("SYN mixed transaction and ledger regions")
    a = (("交易时间", "付款人账号", "收款人账号", "交易金额(分)", "收付标志", "币种", "本方账号", "对方账号"),
         ("2026-01-01 10:00:00", "SYN-A-PAY", "SYN-A-PEER", "405", "出", "CNY", "SYN-A-PAY", "SYN-A-PEER"))
    b = ([*HEADER[:2], counter_heading, *HEADER[3:]], row("X2", "405", hour=11))
    raw = csv_bytes(*b, *a) if first_is_b else csv_bytes(*a, *b)
    source = preview(client, cid, raw)
    summary = source["table_summary"]
    assert summary["profile"] == "funds-trace-table-v87"
    assert summary["documents"][0]["mode"] == ("B" if first_is_b else "A")
    assert summary["documents"][0]["header_rows"] == [1, 3]
    job = complete(client, cid, source)
    assert job["stage"] == "done" and job["result"]["ok"] == 2, job

    flows = {item["raw_payee_account"]: item["id"] for item in client.get(f"/api/v1/cases/{cid}/flows").json()["items"]}
    for peer, header_row, column, expected in (
        ("000SYN-B", (1 if first_is_b else 3), 3, "supported"),
        ("SYN-A-PEER", (3 if first_is_b else 1), 8, "insufficient"),
    ):
        anchor = flows[peer]
        result = review(client, cid, anchor)
        path = f"/api/context/{anchor}/record/{anchor}"
        params = {"case_id": cid}
        headers = {"X-FundsTrace-Review": result["review"]["token"]}
        scope = {"source_id": source["import_id"], "sha256": sha256(raw).hexdigest(),
                 "parser_version": summary["profile"], "member_path": "table.csv",
                 "source_row": header_row + 1, "header_row": header_row}
        response = client.post(path + "/verify", json={**params, "analysis_revision": result["analysis_revision"],
                               "candidate": "account", "citation": {**scope, "column": column}}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["evidence_status"] == expected
        original = client.get(path, params=params, headers=headers).json()["originals"][0]
        assert original["citation_scope"] == (scope if expected == "supported" else None)


@pytest.mark.parametrize("during", ["window", "canvas", "source"])
def test_review_rechecks_binding_at_acceptance_after_reading_original(client, make_case, monkeypatch, during):
    from app import import_originals
    from test_automatic_table_import import preview
    from test_direction_recheck import HEADER, csv_bytes, row
    from test_feedback_import_api import complete

    cid, other = make_case("SYN proof race"), make_case("SYN different binding")
    canvas = client.post("/api/canvases/save", json={
        "case_id": cid, "name": "SYN bound canvas", "snapshot": {},
    }).json()["canvas_id"]
    raw = csv_bytes(HEADER, row("X2", "405"))
    source = preview(client, cid, raw)
    batch = complete(client, cid, source)["result"]["batch_id"]
    anchor = client.get(f"/api/v1/cases/{cid}/flows").json()["items"][0]["id"]
    data = review(client, cid, anchor, canvas=canvas)
    headers = {"X-FundsTrace-Review": data["review"]["token"]}
    params = {"case_id": cid, "canvas_id": canvas}
    path = f"/api/context/{anchor}/record/{anchor}"
    scope = client.get(path, params=params, headers=headers).json()["originals"][0]["citation_scope"]
    body = {**params, "analysis_revision": data["analysis_revision"],
            "candidate": "account", "citation": {**scope, "column": 3}}
    assert client.post(path + "/verify", json={**body, "case_id": other}, headers=headers).status_code == 409
    assert client.post(path + "/verify", json={**body, "canvas_id": 0}, headers=headers).status_code == 409
    assert client.post(path + "/verify", json={**body, "analysis_revision": "0" * 32},
                       headers=headers).status_code == 409

    saved_read = import_originals.read_cells
    reading, continue_read = Event(), Event()

    def hold(*args, **kwargs):
        read = saved_read(*args, **kwargs)
        reading.set()
        assert continue_read.wait(5)
        return read

    monkeypatch.setattr(import_originals, "read_cells", hold)
    with ThreadPoolExecutor(max_workers=1) as pool:
        verifier = pool.submit(client.post, path + "/verify", json=body, headers=headers)
        try:
            assert reading.wait(5)
            if during == "window":
                response = client.delete("/api/context/review", params=params, headers=headers)
            elif during == "canvas":
                response = client.delete(f"/api/canvases/{canvas}", params={"case_id": cid})
            else:
                response = client.delete(f"/api/batches/{batch}", params={"case_id": cid})
            assert response.status_code == 200, response.text
        finally:
            continue_read.set()
        rejected = verifier.result(timeout=5)
    assert rejected.status_code == 409, rejected.text
    assert "evidence_status" not in rejected.json()
    if during == "source":
        history = client.get(f"/api/v1/cases/{cid}/sources/{source['import_id']}/file")
        assert history.status_code == 200 and history.content == raw


def test_canvas_edge_opens_unanchored_and_uses_only_selected_direction_members(client, make_case):
    from app import event_resolution
    from test_event_graph import graph, track
    from test_event_import import transaction

    cid = make_case("SYN edge review")
    fwd = pair("SYN-V6-A-B", "8000.00", day="02")
    rev = pair("SYN-V6-B-A", "2100.00", day="03")
    for row in rev:
        row.update({"付款账号": "SYN-B", "收款账号": "SYN-A"})
    pending = pair("SYN-V6-PENDING", "1200.00", day="01")
    for row, balance in zip(pending, ("8400.00", "10100.00"), strict=True):
        row.update({header: "" for header in event_resolution.HEADERS.values()})
        row["本笔交易后账面余额"] = balance
    credits = [transaction("SYN-A", **{"付款账号": "SYN-C", "收款账号": "SYN-A",
        "交易流水号": "SYN-V6-CREDIT", "交易时间": "2026-01-01 09:42:00", "交易金额": "8000.00",
        **{header: "" for header in event_resolution.HEADERS.values()}})]
    unrelated = transaction("SYN-U", **{"本方户名": "合成同名",
        "付款账号": "SYN-U", "收款账号": "SYN-V", "交易流水号": "SYN-OUTSIDE",
        **{header: "" for header in event_resolution.HEADERS.values()}})
    source_rows = fwd + rev + pending + credits + [unrelated]
    for row in source_rows:
        row.setdefault("本方户名", "合成同名")
    all_rows = imported(client, cid, source_rows)
    track(client, cid, [row for row in all_rows if row["trade_no"].startswith(("SYN-V6-A-B", "SYN-V6-B-A"))])
    current = graph(client, cid)
    forward = next(edge for edge in current["edges"] if edge["total"] == "8000.00")
    reverse = next(edge for edge in current["edges"] if edge["total"] == "2100.00")
    payload = {"case_id": cid, "source": forward["source"], "target": forward["target"],
               "forward_ids": list(map(str, forward["ids"])), "reverse_ids": list(map(str, reverse["ids"])),
               "analysis_revision": current["analysis_revision"], "membership_digest": current["membership_digest"]}
    response = client.post("/api/context/edge", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["anchor"]["id"] is None and result["review"]["kind"] == "edge"
    assert result["review"]["reference"]["amount"] == "8000.00"
    funds = result["review"]["directional_funds"]
    assert funds["forward"]["totals"][0]["amount"] == "8000.00"
    assert funds["reverse"]["totals"][0]["amount"] == "2100.00"
    assert funds["totals"] == {"currency": "CNY", "total": "10100.00", "net": "5900.00", "direction": "forward"}
    assert funds["annotations"]["forward"]["times"][0].startswith("2026-01-02")
    assert all(row.get("context_relation") != "anchor" and not row["review_roles"] for row in result["rows"])
    assert not any(row["trade_no"] == "SYN-OUTSIDE" for row in result["rows"])
    mirrors = [row for row in result["rows"] if row.get("pair_badge") == "pending"]
    assert {row["balance"] for row in mirrors} == {"8400.00", "10100.00"}
    assert len(mirrors) == 2
    credit_ids = {row["id"] for row in result["rows"] if row["trade_no"] == "SYN-V6-CREDIT"}
    assert credit_ids and credit_ids <= {key for item in result["suggestions"]["sources"] for key in item["ids"]}

    token = result["review"]["token"]
    headers = {"X-FundsTrace-Review": token}
    summary = client.post("/api/context/summary", json={"case_id": cid, "flow_ids": payload["reverse_ids"]}, headers=headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["forward"]["observation_count"] == 0
    assert summary.json()["reverse"]["totals"][0]["amount"] == "2100.00"
    assert summary.json()["booked"]["forward"] == {"cnt": 0, "total": "0.00", "amount_totals": []}
    assert summary.json()["booked"]["reverse"] == {
        "cnt": 1, "total": "2100.00", "amount_totals": [{"currency": "CNY", "amount": "2100.00"}]}
    original = client.get(f"/api/context/0/record/{mirrors[0]['id']}", params={"case_id": cid}, headers=headers)
    assert original.status_code == 200, original.text
    assert original.json()["pair"]["candidate_ids"] == [mirrors[1]["id"]]
    assert len(original.json()["originals"]) == 1
    invalid = client.post("/api/context/edge", json={**payload, "forward_ids": [mirrors[0]["id"]]})
    assert invalid.status_code == 409
    # A new canvas member invalidates the frozen review even if the original
    # two directions are still tracked and source evidence has not changed.
    track(client, cid, [mirrors[0]])
    assert graph(client, cid)["analysis_revision"] == payload["analysis_revision"]
    assert client.post("/api/context/edge", json=payload).status_code == 409
    stale = client.post("/api/context/summary", json={"case_id": cid, "flow_ids": []}, headers=headers)
    assert stale.status_code == 409
