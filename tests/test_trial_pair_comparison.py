"""The source reviewer can exclude opposite postings without creating flows."""
import json
from decimal import Decimal

import pytest

from app import table_source_roles
from app.services import imports, trace_review
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_uniform35_reference import COLUMNS, row, workbook


def registered(client, case, *, first_account="000000000123"):
    def posting(number, **fields):
        reference = f"SYN-PRIMARY-{number}"
        values = {"本方账号": "000000000123", "本方卡号": "", "查询账号": "SYN-QUERY",
                  "交易流水号": reference, "凭证号": reference, "交易时间": f"20460101080{number}00",
                  "交易是否成功": "成功", "借贷标志": "出", "交易摘要": "", "备注": ""}
        return row(**{**values, **fields})

    raw = workbook([
        posting(1, **{"本方账号": first_account}), posting(2, **{"本方账号": first_account}),
        posting(3, **{"凭证号": "SYN-VOUCHER-3"}),
        posting(4, **{"凭证号": "SYN-VOUCHER-4"}),
        posting(5, **{"本方账号": "00000000123"}), posting(6, **{"借贷标志": "进"}),
        posting(7, **{"本方账号": "", "查询卡号": "SYN-QUERY-CARD"}),
        posting(8, **{"借贷标志": "02"}),
        posting(9, **{"交易流水号": "SYN-PRIMARY-1", "凭证号": "SYN-PRIMARY-1"}),
    ], headers=COLUMNS, stale=True, padding=("", ""))
    view = preview(client, case, raw, "source.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    finished = complete(client, case, view)
    assert finished["stage"] == "done"
    base = f"/api/v1/cases/{case}"
    original = view["import_id"]
    source = f"{base}/imports/{original}/review"
    members = {"member_path": "worksheets/0001"}
    return base, source, members, original, finished["result"]["batch_id"], raw


def test_verbatim_pair_question_is_shared_by_every_native_choice_and_recheck(monkeypatch):
    from hashlib import sha256
    from types import SimpleNamespace

    from app.services import pair_verbatim_trial as pair

    evidence = {"范围": {"case_id": "11", "revision": "b" * 64}, "锚": "S1R2", "物理列": [1],
                "原表": {"S1": {"表头": ["C1｜对手"], "原行": {"R2": ["SYN-OTHER"]}},
                         "S2": {"表头": ["C1｜本方"], "原行": {"R4": ["SYN-OTHER"]}}}}
    valid = {"level": "BOOK", "peer": "S2R4", "effect": "UNKNOWN", "witness": "S1C1"}
    deferred = {"level": "DEFER", "peer": "NONE", "effect": "UNKNOWN", "witness": "NONE"}
    monkeypatch.setattr(pair.import_verbatim_balance, "_host_allowed", lambda _endpoints: True)
    monkeypatch.setattr(pair, "_source_data", lambda _cid, _grant, _active: (evidence, "S2R4", {"S1C1"}))
    native, reviews = [], []

    def capture(_, tag, route, data):
        assert route == "/v1/systemone"
        native.append(data)
        choices = valid if tag == pair.models._FIRST[0] else deferred
        return {"answers": {key: {"choice": value} for key, value in choices.items()},
                "usage": {"output_tokens": 0}}

    def review(_, text, _schema, *, before_request):
        reviews.append((text, before_request()))
        return valid

    monkeypatch.setattr(pair.models._LoopbackProvider, "_request", capture)
    monkeypatch.setattr(pair.models._LoopbackProvider, "_completion", review)
    body = SimpleNamespace(kind="pair", anchor_id="2", canvas_id="0", review_token="c" * 64)
    endpoints = {}
    results = []
    questions = ["如何归并\n原据=只是一段问题字面。", "这两条观察应该在画布上算几笔？"]
    for question in questions:
        grant = pair.LocalPairReview(11, 2, 4, 0, body.review_token,
            (pair.LocalPairSource("s1", "1" * 64, "table.csv", (2,)),
             pair.LocalPairSource("s2", "2" * 64, "table.csv", (4,))),
             (1,), question, "two_plus_one", lambda: pair.models._LoopbackProvider(endpoints))
        results.append(pair.run("11", body, endpoints, grant, lambda: True))

    assert len(native) == 4 and native[0] == native[1] and native[2] == native[3]
    assert native[0]["questions"] == native[2]["questions"]
    for question, wire, (text, proved), result in zip(questions, native[::2], reviews, results, strict=True):
        question_line, original = wire["state"].split("\n", 1)
        assert json.loads(question_line.removeprefix("待核问题=")) == question
        assert json.loads(original.split("\n原据=", 1)[1]) == evidence
        assert text.startswith(wire["state"] + "\n输出字段及代号=") and proved == {"S1C1"}
        facts = result["packet"]["facts"]
        assert facts["local_model_original_sha256"] == sha256(original.encode()).hexdigest()
        assert facts["local_question_sha256"] == sha256(question.encode()).hexdigest()
        assert (result["first_calls"], result["recheck_calls"], facts["financial_admission"]) == (2, 1, False)


def test_compare_uses_current_accepted_and_unresolved_original_rows(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    # Preserve a source registered before the trial began selecting its reference policy.
    with monkeypatch.context() as prior:
        prior.setattr(client.app.state, "desktop_model_trial", None)
        base, source, members, original, batch, raw = registered(client, case)
    rows = {}
    for stage in ("accepted", "review"):
        page = client.get(source, params={**members, "view": stage}).raise_for_status().json()
        rows.update({r["source_row"]: r for r in page["items"]})
    assert all(rows[number]["original"] for number in (2, 3, 4, 5))
    assert all(rows[number]["flow_id"] for number in (2, 3))
    assert all(rows[number]["disposition"] == "unresolved" and rows[number]["flow_id"] is None for number in (4, 5))
    before = client.get(base + "/flows?granularity=events").raise_for_status().json()

    def compare(left, right):
        return client.post(source + "/compare", json={**members, "record_nos": [rows[left]["record_no"], rows[right]["record_no"]]})

    for selected in ((2, 3), (4, 5), (2, 10)):
        response = compare(*selected)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == {
            "case_id": str(case), "source_id": original, **members, "scope": "selected_source_rows",
            "status": "excluded", "reason": "same_ledger_same_direction", "event_attribution": "unknown",
            **({"selected_reference_column": 11} if selected == (2, 10) else {}),
            "rows": [{"record_no": rows[number]["record_no"], "source_row": number,
                      "own_account_column": 34, "direction_column": 6, "direction": "out"} for number in selected],
        }
    for other, reason in ((6, "different_ledger"), (7, "different_direction"),
                          (8, "source_role_unproved"), (9, "source_role_unproved")):
        pending = compare(2, other).raise_for_status().json()
        assert (pending["status"], pending["reason"], pending["event_attribution"]) == ("pending", reason, "unknown")
    assert compare(2, 2).status_code == 422
    selected = {**members, "record_nos": [rows[2]["record_no"], rows[3]["record_no"]]}
    assert client.post(source + "/compare", json={**selected, "member_path": "worksheets/0002"}).status_code == 422
    other = make_case("Other case")
    assert client.post(source.replace(f"/cases/{case}/", f"/cases/{other}/") + "/compare", json=selected).status_code == 404
    assert client.get(base + "/flows?granularity=events").raise_for_status().json() == before

    stored = imports._path(original, ".xlsx")
    stored.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    broken = client.post(source + "/compare", json=selected)
    assert (broken.status_code, broken.json()["error"]["code"]) == (409, "source_changed")
    stored.write_bytes(raw)
    assert client.delete(base + f"/sources/{original}/operation", params={"expected_batch_id": batch}).status_code == 200
    retired = client.post(source + "/compare", json=selected)
    assert (retired.status_code, retired.json()["error"]["code"]) == (409, "source_operation_changed")
    history = client.get(source, params=members).raise_for_status().json()["items"]
    assert {r["source_row"] for r in history} >= {2, 3, 4, 5}


@pytest.mark.parametrize("account", ["****1234", "尾号1234", "未提供", "000000000000", "１２３４"])
def test_matching_incomplete_or_unproved_owner_text_does_not_establish_one_ledger(client, make_case, monkeypatch, account):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    _, source, member, _, _, _ = registered(client, make_case(), first_account=account)
    log = client.get(source, params=member).raise_for_status().json()["items"]
    [first, second] = [row for row in log if row["source_row"] in {2, 3}]
    for row in (first, second):
        assert row["original"][33]["value"] == account
    result = client.post(source + "/compare", json={**member, "record_nos": [first["record_no"], second["record_no"]]})
    assert result.status_code == 200, result.text
    assert (result.json()["status"], result.json()["reason"]) == ("pending", "source_role_unproved")


def test_compare_checks_activity_after_decoding_and_is_trial_only(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base, source, members, original, batch, _ = registered(client, case)
    items = client.get(source, params=members).raise_for_status().json()["items"]
    select = {**members, "record_nos": [items[0]["record_no"], items[1]["record_no"]]}
    original_cells = trace_review._original_cells

    def withdraw_after_read(*args, **kwargs):
        values = original_cells(*args, **kwargs)
        assert client.delete(base + f"/sources/{original}/operation", params={"expected_batch_id": batch}).status_code == 200
        return values

    with monkeypatch.context() as sync:
        sync.setattr(trace_review, "_original_cells", withdraw_after_read)
        response = client.post(source + "/compare", json=select)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "source_operation_changed")
    monkeypatch.setattr(client.app.state, "desktop_model_trial", None)
    disabled = client.post(source + "/compare", json=select)
    assert (disabled.status_code, disabled.json()["error"]["code"]) == (404, "model_trial_disabled")


def test_registered_nonuniform_postings_keep_same_ledger_distinct_from_an_event(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    url = f"/api/v1/cases/{case}"
    entries = [
        row(**{"本方账号": "000880001", "借贷标志": "出", "交易流水号": "SYN-P1", "凭证号": "SYN-P1"}),
        row(**{"本方账号": "000880001", "借贷标志": "进", "交易流水号": "SYN-P2", "凭证号": "SYN-P2"}),
        row(**{"本方账号": "000880002", "借贷标志": "进", "交易流水号": "SYN-P3", "凭证号": "SYN-P3"}),
    ]
    headings = table_source_roles._TRANSACTION_EXPLICIT
    view = preview(client, case, workbook(entries, headers=headings), "registered-role.xlsx")
    done = complete(client, case, view)
    assert done["stage"] == "done" and done["result"]["ok"] == 3
    source = f"{url}/imports/{view['import_id']}/review"
    scope = {"member_path": "worksheets/0001"}
    journal = client.get(source, params={**scope, "view": "accepted"}).raise_for_status().json()["items"]
    records = {item["source_row"]: item["record_no"] for item in journal}

    for second, reason in ((3, "different_direction"), (4, "different_ledger")):
        result = client.post(source + "/compare", json={**scope, "record_nos": [records[2], records[second]]}).raise_for_status().json()
        assert (result["status"], result["reason"], result["event_attribution"]) == ("pending", reason, "unknown")
        assert [entry["source_row"] for entry in result["rows"]] == [2, second]
        assert {(entry["own_account_column"], entry["direction_column"]) for entry in result["rows"]} == {
            (headings.index("本方账号") + 1, headings.index("借贷标志") + 1)}
    assert client.get(url + "/flows").raise_for_status().json()["funds"]["confirmed_event_count"] == 0


def test_explicit_owner_and_certified_query_alias_preserve_distinct_same_clock_postings(
        client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    account = "000540017"
    headings = table_source_roles._TRANSACTION_EXPLICIT
    raw = workbook([row(**{"查询帐号": inquiry, "本方账号": account, "借贷标志": "贷",
                           "交易金额": "12.34", "交易余额": balance, "交易时间": "2046-01-03 08:01:01",
                           "交易流水号": f"SYN-A{rank}", "凭证号": f"SYN-A{rank}", "交易是否成功": "成功"})
                    for rank, (inquiry, balance) in enumerate(
                        ((account, "52.34"), (account, "64.68"), ("000540019", "77.02")), 1)],
                   headers=headings)
    prepared = preview(client, case, raw, "alias-postings.xlsx")
    completed = complete(client, case, prepared)
    assert completed["stage"] == "done" and completed["result"]["ok"] == 2
    source = prepared["import_id"]
    review = f"{base}/imports/{source}/review"
    scope = {"member_path": "worksheets/0001"}
    by_row = {item["source_row"]: item for item in client.get(review, params=scope).raise_for_status().json()["items"]}
    assert by_row[2]["details"]["source_account_column"] == "查询帐号"
    assert by_row[3]["details"]["source_account_column"] == "查询帐号"
    assert by_row[4]["disposition"] == "unresolved"

    def compare(other=3):
        return client.post(review + "/compare", json={**scope, "record_nos": [by_row[2]["record_no"], by_row[other]["record_no"]]})

    verified = compare().raise_for_status().json()
    assert (verified["status"], verified["reason"], verified["event_attribution"]) == (
        "excluded", "same_ledger_same_direction", "unknown")
    assert [(r["source_row"], r["own_account_column"], r["direction_column"], r["direction"]) for r in verified["rows"]] == [
        (number, headings.index("本方账号") + 1, headings.index("借贷标志") + 1, "in") for number in (2, 3)]
    mismatch = compare(4).raise_for_status().json()
    assert (mismatch["status"], mismatch["reason"]) == ("pending", "source_role_unproved")
    confirmed = client.get(base + "/flows").raise_for_status().json()["funds"]["confirmed_event_count"]
    assert confirmed == 0
    with database._lock:
        database._con.close()
        database._con = None
    assert compare().raise_for_status().json() == verified
    assert client.delete(base + f"/sources/{source}/operation",
                         params={"expected_batch_id": completed["result"]["batch_id"]}).status_code == 200
    assert compare().status_code == 409


def registered_query_postings(client, case, headers, *, owner_field="查询帐号"):
    def posting(index, account="00070001", direction="借"):
        return row(**{owner_field: account, "查询帐号": account if owner_field == "查询帐号" else "00070001",
                      "查询卡号": "000876543", "交易对方账卡号": "", "借贷标志": direction,
                      "交易时间": f"2046-01-14 08:0{index}:00", "交易是否成功": "成功",
                      "交易流水号": f"SYN-Q{index}", "凭证号": f"SYN-Q{index}"})

    originals = [posting(1), posting(2, direction="出"), posting(3, direction="贷"),
                 posting(4, account="00070002"), posting(5, account=""),
                 posting(6, direction="02"), posting(7, account="****70001")]
    raw = workbook(originals, headers=headers, stale=True, padding=("", ""))
    view = preview(client, case, raw, "query-postings.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    finished = complete(client, case, view)
    assert finished["stage"] == "done"
    case_url = f"/api/v1/cases/{case}"
    review = f"{case_url}/imports/{view['import_id']}/review"
    scope = {"member_path": "worksheets/0001"}
    records = client.get(review, params=scope).raise_for_status().json()["items"]
    return (case_url, review, scope, {record["source_row"]: record for record in records},
            view["import_id"], finished["result"]["batch_id"])


@pytest.mark.parametrize("placeholder_cards", [False, True])
def test_query_workbook_comparison_returns_same_reference_competition_and_amount_difference(
        client, make_case, monkeypatch, placeholder_cards):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic same-reference query workbook")

    def posting(owner, card, peer, direction, amount, voucher):
        return row(**{"查询帐号": owner, "查询卡号": card, "交易对方账卡号": peer, "借贷标志": direction,
                      "交易时间": "2046-02-01 10:20:30", "交易金额": amount, "币种": "CNY",
                      "交易流水号": "SYN-REFERENCE", "凭证号": voucher, "交易是否成功": "成功"})

    card_a, card_b = ("000000", "000000") if placeholder_cards else ("000301", "000302")
    a = posting("000201", card_a, card_b, "借", "30.00", "SYN-VOUCHER-A")
    b = posting("000202", card_b, card_a, "贷", "30.00", "SYN-VOUCHER-B")
    c = posting("000202", card_b, card_a, "贷", "20.00", "SYN-VOUCHER-C")
    ready = preview(client, case, workbook([a, b, c], headers=table_source_roles._TRANSACTION_QUERY_ONLY), "pairs.xlsx")
    assert complete(client, case, ready)["stage"] == "done"
    route = f"/api/v1/cases/{case}/imports/{ready['import_id']}/review"
    params = {"member_path": "worksheets/0001"}
    records = client.get(route, params=params).raise_for_status().json()["items"]
    first, second, third = [item["record_no"] for item in records]
    anchor = client.get(f"/api/context/{records[0]['flow_id']}", params={"case_id": case, "review": "true"}).raise_for_status().json()["anchor"]
    assert anchor["review_fields"]["counter_kind"] == "account_or_card"
    assert anchor["review_fields"]["counter_column"] == "交易对方账卡号"
    assert not anchor["review_fields"]["counter_card"]

    def compared(other):
        return client.post(route + "/compare", json={**params, "record_nos": [first, other]}).raise_for_status().json()

    same = compared(second)
    result = same["posting_comparison"]
    assert result["opposite_directions"] and result["reciprocal_card_text"] == (not placeholder_cards)
    assert result["same_recorded_clock"] and result["same_currency"] and result["same_amount"]
    assert result["different_vouchers"] and result["reference_occurrences"] == [3, 3]
    assert same["reference_competition"]["other_source_rows"] == [4]
    assert same["status"] == "pending" and "observation_correspondence" not in same
    different = compared(third)
    assert (different["status"], different["reason"], different["event_attribution"]) == (
        "excluded", "different_amount_currency", "unknown")
    assert different["reference_competition"]["other_source_rows"] == [3]


@pytest.mark.parametrize("alternate_annotations", [False, True])
def test_registered_query_only_ledger_can_exclude_same_side_with_original_and_bound_raw_marker(
        client, database, make_case, monkeypatch, alternate_annotations):
    from test_table_source_roles import TRANSACTION_QUERY_ONLY

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Exact registered query-only ledger")
    headers = list(TRANSACTION_QUERY_ONLY)
    if alternate_annotations:
        headers[:2] = ["企业名称(查询条件)", "证照(查询条件)"]
    base, review, scope, records, source, batch = registered_query_postings(client, cid, headers)
    assert all(records[number]["flow_id"] is not None for number in (2, 3, 4, 5))

    def compare(first, second):
        return client.post(review + "/compare", json={
            **scope, "record_nos": [records[number]["record_no"] for number in (first, second)],
        })

    original = compare(2, 3).raise_for_status().json()
    assert (original["status"], original["reason"], original["event_attribution"]) == (
        "excluded", "same_ledger_same_direction", "unknown")
    assert original["rows"] == [
        {"record_no": records[number]["record_no"], "source_row": number,
         "own_account_column": headers.index("查询帐号") + 1,
         "direction_column": headers.index("借贷标志") + 1, "direction": "out"}
        for number in (2, 3)
    ]

    for other, reason in ((4, "different_direction"), (5, "different_ledger"),
                          (6, "source_role_unproved"), (7, "source_role_unproved"),
                          (8, "source_role_unproved")):
        pending = compare(2, other).raise_for_status().json()
        assert (pending["status"], pending["reason"], pending["event_attribution"]) == ("pending", reason, "unknown")

    stored = records[2]["details"]
    evidence = json.loads(stored["source_ledger_evidence"])
    assert evidence["source_direction"] == "借"
    # Both literals mean an outward posting. Only the recorded raw marker matches the source.
    wrong = {**stored, "source_ledger_evidence": json.dumps({**evidence, "source_direction": "出"})}
    db_key = [cid, source, records[2]["record_no"]]
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [json.dumps(wrong), *db_key])
    assert compare(2, 3).raise_for_status().json()["reason"] == "source_role_unproved"
    database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
               [json.dumps(stored), *db_key])
    raw_receipt = database.q_raw("select result_json from import_sessions where case_id=? and import_id=?",
                                 [cid, source])[0]["result_json"]
    manual = json.loads(raw_receipt)
    for item in manual["table_summary"]["mapping_evidence"]:
        if item["field"] == "self_account":
            item["basis"] = "manual_override"
    database.x("update import_sessions set result_json=? where case_id=? and import_id=?",
               [json.dumps(manual), cid, source])
    assert compare(2, 3).raise_for_status().json()["reason"] == "source_role_unproved"
    database.x("update import_sessions set result_json=? where case_id=? and import_id=?",
               [raw_receipt, cid, source])
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert compare(2, 3).raise_for_status().json() == original
    assert client.get(base + "/flows").raise_for_status().json()["funds"]["confirmed_event_count"] == 0
    assert client.delete(base + f"/sources/{source}/operation", params={"expected_batch_id": batch}).status_code == 200
    assert compare(2, 3).status_code == 409


def test_same_query_label_outside_registered_workbook_cannot_certify_same_ledger(client, make_case, monkeypatch):
    from test_table_source_roles import TRANSACTION_QUERY_ONLY

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    headers = tuple("本方账号" if h == "交易对方帐卡号类型" else h for h in TRANSACTION_QUERY_ONLY)
    _, review, scope, records, _, _ = registered_query_postings(
        client, make_case("Conflicting unregistered owner shape"), headers, owner_field="本方账号")
    assert all(records[number]["flow_id"] for number in (2, 3))
    answer = client.post(review + "/compare", json={
        **scope, "record_nos": [records[number]["record_no"] for number in (2, 3)],
    })
    assert answer.raise_for_status().json()["reason"] == "source_role_unproved"


def test_repeated_query_annotations_never_authorize_selected_originals(client, make_case, monkeypatch):
    from test_table_source_roles import TRANSACTION_QUERY_ONLY

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    headers = [TRANSACTION_QUERY_ONLY[0], *TRANSACTION_QUERY_ONLY]
    _, review, scope, records, _, _ = registered_query_postings(
        client, make_case("Duplicated query context"), headers)
    answer = client.post(review + "/compare", json={
        **scope, "record_nos": [records[number]["record_no"] for number in (2, 3)],
    }).raise_for_status().json()
    assert (answer["status"], answer["reason"], answer["event_attribution"]) == (
        "pending", "source_role_unproved", "unknown")


def test_native_csv_comparison_lists_the_other_literal_reference_without_confirming_an_event(client, make_case, monkeypatch):
    from test_folder_evidence import preview as archive_preview, table, transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    records = [transaction(**{"交易账号": "00000000123", "交易流水号": "SYN-SHARED", "收付标志": direction,
                              "交易时间": f"2032-01-01 10:0{index}:00"})
               for index, direction in enumerate(("出", "进", "进"), 1)]
    member = "declared.csv"
    view = archive_preview(client, case, [(member, table("transaction", records))], zipped=True, scope_bank=None)
    done = complete(client, case, view)
    assert done["result"]["ok"] == 3
    url = f"{base}/imports/{view['import_id']}/review"
    rows = client.get(url, params={"member_path": member}).raise_for_status().json()["items"]
    body = {"member_path": member, "record_nos": [item["record_no"] for item in rows if item["source_row"] in {2, 3}]}
    answer = client.post(url + "/compare", json=body).raise_for_status().json()
    assert (answer["status"], answer["reason"], answer["event_attribution"]) == ("pending", "different_direction", "unknown")
    assert answer["reference_competition"] == {"column": 21, "first_row": 2, "last_row": 4,
                                               "other_source_rows": [4], "other_count": 1,
                                               "selected_differences": [{"column": 3, "heading": "交易时间"},
                                                                        {"column": 6, "heading": "收付标志"}]}
    assert client.get(base + "/flows").raise_for_status().json()["funds"]["confirmed_event_count"] == 0
    retire = client.delete(f"{base}/sources/{view['import_id']}/operation", params={"expected_batch_id": done["result"]["batch_id"]})
    assert retire.status_code == 200
    assert client.post(url + "/compare", json=body).status_code == 409


def test_standalone_closed31_csv_reuses_the_registered_posting_roles_only(client, database, make_case, monkeypatch):
    from test_feedback31_reference import COLUMNS as BANK31, payload, row as bank_row

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic registered v78 CSV")
    base = f"/api/v1/cases/{case}"

    def original(balance, **changes):
        return bank_row(**{"交易账号": "00070001", "交易卡号": "00087001", "收付标志": "出",
                           "交易流水号": "SYN-PAIRED-REF", "交易余额": balance,
                           "交易对手账卡号": "", "交易是否成功": "成功", **changes})

    rows = [original("100.00"), original("110.00"), original("120.00", **{"交易流水号": "SYN-OTHER-REF"}),
            original("130.00", **{"交易账号": "00070002"}), original("140.00", **{"收付标志": "入"}),
            original("150.00", **{"交易账号": ""}), original("160.00", **{"收付标志": "02"})]
    view = preview(client, case, payload(rows), "closed31.csv")
    assert view["table_summary"]["profile"] == "funds-trace-table-v78"
    finished = complete(client, case, view)
    assert finished["stage"] == "done" and finished["result"]["ok"] == 6
    sid = view["import_id"]
    path = f"{base}/imports/{sid}/review"
    scope = {"member_path": "table.csv"}
    items = client.get(path, params=scope).raise_for_status().json()["items"]
    record_nos = {item["source_row"]: item["record_no"] for item in items}

    def compare(other):
        return client.post(path + "/compare", json={**scope, "record_nos": [record_nos[2], record_nos[other]]})

    selected = compare(3).raise_for_status().json()
    assert (selected["status"], selected["reason"], selected["event_attribution"]) == (
        "excluded", "same_ledger_same_direction", "unknown")
    assert selected["rows"] == [
        {"record_no": record_nos[n], "source_row": n, "own_account_column": BANK31.index("交易账号") + 1,
         "direction_column": BANK31.index("收付标志") + 1, "direction": "out"} for n in (2, 3)
    ]
    assert selected["selected_reference_column"] == BANK31.index("交易流水号") + 1
    assert selected["reference_competition"]["other_source_rows"] == [5, 6, 7, 8]
    assert 4 not in selected["reference_competition"]["other_source_rows"]
    assert "reference_competition" not in compare(4).raise_for_status().json()
    for row_number, reason in ((5, "different_ledger"), (6, "different_direction"),
                               (7, "source_role_unproved"), (8, "source_role_unproved")):
        pending = compare(row_number).raise_for_status().json()
        assert (pending["status"], pending["reason"], pending["event_attribution"]) == ("pending", reason, "unknown")
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert compare(3).raise_for_status().json() == selected
    assert client.get(base + "/flows").raise_for_status().json()["funds"]["confirmed_event_count"] == 0
    assert client.delete(base + f"/sources/{sid}/operation",
                         params={"expected_batch_id": finished["result"]["batch_id"]}).status_code == 200
    assert compare(3).status_code == 409


def test_distinct_v78_csv_family_does_not_grant_closed31_receipt(client, make_case, monkeypatch):
    from test_feedback31_reference import payload
    from test_uniform37_reference import COLUMNS as UNIFORM37

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic alternate bank family")
    data = payload([row(**{"本方账号": "00070001", "交易流水号": f"SYN-X{n}"}) for n in (1, 2)], UNIFORM37)
    view = preview(client, case, data, "uniform37.csv")
    assert view["table_summary"]["profile"] == "funds-trace-table-v78"
    assert complete(client, case, view)["result"]["ok"] == 2
    path = f"/api/v1/cases/{case}/imports/{view['import_id']}/review"
    scope = {"member_path": "table.csv"}
    rows = client.get(path, params=scope).raise_for_status().json()["items"]
    response = client.post(path + "/compare", json={**scope, "record_nos": [r["record_no"] for r in rows]})
    verdict = response.raise_for_status().json()
    assert (verdict["status"], verdict["reason"]) == ("pending", "source_role_unproved")


@pytest.mark.parametrize("variant", ["no_reciprocal", "same_card", "literal_reciprocal", "masked_card", "changed_header"])
def test_closed28_csv_compares_same_account_opposite_observations_with_missing_primary_numbers(
        client, database, make_case, monkeypatch, variant):
    from app import feedback_import
    from test_feedback31_reference import payload
    from test_folder_evidence import transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic close original postings")
    account, card_a, card_b = "000007777", "000008881", "000008882"

    def posting(card, peer, side, clock, voucher, balance):
        return transaction(**{"交易账号": account, "交易卡号":card, "交易对手账卡号":peer, "收付标志":side,
            "交易时间":clock, "交易金额":"17.46", "交易余额":balance, "交易币种":"CNY",
            "交易流水号":"", "凭证号":voucher, "交易是否成功":"09"})

    peer_a, peer_b = "000008800", "000008801"
    if variant == "same_card":
        card_b = peer_a = peer_b = card_a
    if variant == "literal_reciprocal":
        peer_a, peer_b = card_b, card_a
    if variant == "masked_card":
        card_b = peer_a = "****8882"
        peer_b = card_a
    first = posting(card_a, peer_a, "进", "2044-05-01 10:00:21", "SYN-VOUCHER-A", "101.28")
    second = posting(card_b, peer_b, "出", "2044-05-01 10:00:29", "SYN-VOUCHER-B", "83.82")
    headings = list(feedback_import.COLUMNS)
    if variant == "changed_header":
        headings[7], headings[8] = headings[8], headings[7]
    raw = payload([first, second], headings)
    view = preview(client, case, raw, "synthetic-closed-feedback.csv")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    saved = complete(client, case, view)
    assert saved["stage"] == "done" and saved["result"]["ok"] == 2
    source = view["import_id"]
    base = f"/api/v1/cases/{case}"
    scope = {"member_path":"table.csv"}
    url = base + f"/imports/{source}/review"
    items = client.get(url, params=scope).raise_for_status().json()["items"]
    numbers = [item["record_no"] for item in items]
    request = {**scope,"record_nos":numbers}
    before = client.get(base + "/flows", params={"granularity":"observations"}).raise_for_status().json()
    answer = client.post(url + "/compare", json=request).raise_for_status().json()
    assert answer["status"] == "pending" and answer["event_attribution"] == "unknown"
    assert "observation_correspondence" not in answer
    if variant in {"masked_card", "changed_header"}:
        assert answer["reason"] == "source_role_unproved"
        assert "posting_comparison" not in answer
        return
    assert answer["reason"] == "different_direction"
    assert [(row["source_row"],row["own_account_column"],row["direction_column"],row["direction"])
            for row in answer["rows"]] == [(2,2,6,"in"),(3,2,6,"out")]
    assert answer["posting_comparison"] == {
        "opposite_directions":True, "reciprocal_card_text":variant == "literal_reciprocal",
        "same_recorded_clock":False, "same_currency":True, "same_amount":True, "same_reference":False,
        "reference_occurrences":[None,None], "different_vouchers":False,
        "columns":{"own_card":1,"clock":3,"amount":4,"direction":6,"peer":7,"currency":13,"reference":21},
    }
    backwards = client.post(url + "/compare", json={**scope,"record_nos":numbers[::-1]}).raise_for_status().json()
    assert backwards["rows"] == answer["rows"][::-1]
    assert backwards["posting_comparison"] == answer["posting_comparison"]
    assert client.post(url + "/compare", json={**request,"member_path":"another.csv"}).status_code == 422
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert client.post(url + "/compare", json=request).raise_for_status().json() == answer
    after = client.get(base + "/flows", params={"granularity":"observations"}).raise_for_status().json()
    assert after == before and after["funds"]["confirmed_event_count"] == 0
    assert client.get(base + f"/sources/{source}/file").content == raw
    assert client.delete(base + f"/sources/{source}/operation",
                         params={"expected_batch_id":saved["result"]["batch_id"]}).status_code == 200
    assert client.post(url + "/compare", json=request).status_code == 409


@pytest.mark.parametrize("variant", [
    "verified", "third_reference", "third_reference_unresolved", "ambiguous_card_owner", "card_present_as_account",
    "different_counterparty", "same_direction", "different_time", "date_only", "different_currency", "different_amount",
    "reordered_header",
])
def test_closed_feedback_original_correspondence_excludes_competition_and_preserves_event_boundary(
        client, database, make_case, monkeypatch, variant):
    from app import feedback_import
    from test_feedback31_reference import payload
    from test_folder_evidence import transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic reciprocal source postings")
    account_a, account_b, account_other = "000007101", "000007102", "000007103"
    card_a, card_b, card_other = "000009111", "000009112", "000009113"

    def posting(account, card, peer, side, balance, reference="SYN-FEEDBACK-REF"):
        return transaction(**{"交易卡号": card, "交易账号": account, "交易对手账卡号": peer,
            "收付标志": side, "交易金额": "18.73", "交易余额": balance,
            "交易时间": "2037-02-01 11:12:13", "交易流水号": reference, "凭证号": "SYN-VOUCHER",
            "交易币种": "CNY", "交易是否成功": "09", "查询反馈结果原因": "成功"})

    first = posting(account_a, card_a, card_b, "出", "60.01")
    second = posting(account_b, card_b, card_a, "入", "35.80")
    nearby = posting(account_other, card_other, card_a, "入", "88.37", reference="SYN-NEARBY-REF")
    nearby["交易时间"] = "2037-02-01 11:12:14"
    if variant in {"third_reference", "third_reference_unresolved"}:
        nearby["交易流水号"] = first["交易流水号"]
        if variant == "third_reference_unresolved":
            nearby["收付标志"] = "未释义"
    if variant == "ambiguous_card_owner":
        nearby["交易卡号"] = card_a
    if variant == "card_present_as_account":
        nearby["交易账号"] = card_b
    if variant == "date_only":
        first["交易时间"] = second["交易时间"] = "2037-02-01"
    second.update({
        "different_counterparty": {"交易对手账卡号": card_other},
        "same_direction": {"收付标志": "出"},
        "different_time": {"交易时间": "2037-02-01 11:12:15"},
        "different_currency": {"交易币种": "USD"},
        "different_amount": {"交易金额": "18.74"},
    }.get(variant, {}))
    headings = list(feedback_import.COLUMNS)
    if variant == "reordered_header":
        headings[21], headings[25] = headings[25], headings[21]
    raw = payload([first, second, nearby], headings)
    view = preview(client, cid, raw, "closed28.csv")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    finished = complete(client, cid, view)
    assert finished["stage"] == "done" and finished["result"]["ok"] >= 2
    base = f"/api/v1/cases/{cid}"
    source = view["import_id"]
    review = f"{base}/imports/{source}/review"
    scope = {"member_path": "table.csv"}
    items = client.get(review, params=scope).raise_for_status().json()["items"]
    by_row = {item["source_row"]: item for item in items}
    assert all(by_row[n]["disposition"] == "imported" for n in (2, 3))
    prior = client.get(base + "/flows", params={"granularity": "observations"}).raise_for_status().json()

    def compare(*numbers):
        return client.post(review + "/compare", json={**scope, "record_nos": [by_row[n]["record_no"] for n in numbers]})

    response = compare(2, 3).raise_for_status().json()
    assert response["event_attribution"] == "unknown"
    if variant in {"different_currency", "different_amount"}:
        assert response["status"] == "excluded" and response["reason"] == "different_amount_currency"
        assert "observation_correspondence" not in response
        proof = response["posting_comparison"]
        assert proof["same_currency"] == (variant == "different_amount")
        assert proof["same_amount"] == (variant == "different_currency")
        after = client.get(base + "/flows", params={"granularity": "observations"}).raise_for_status().json()
        assert after == prior and after["funds"]["confirmed_event_count"] == 0
        return
    assert response["status"] == "pending"
    if variant != "verified":
        assert "observation_correspondence" not in response
        return
    assert response["reason"] == "different_ledger"
    correspondence = {
        "own_card_column": 1, "counterparty_identifier_column": 7, "reference_column": 21, "status_column": 16,
        "time_column": 3, "amount_column": 4, "currency_column": 13,
    }
    assert response["observation_correspondence"] == correspondence
    assert response["rows"] == [
        {"record_no": by_row[pos]["record_no"], "source_row": pos,
         "own_account_column": 2, "direction_column": 6, "direction": side}
        for pos, side in ((2, "out"), (3, "in"))
    ]
    competition = response["reference_competition"]
    assert (competition["column"], competition["first_row"], competition["last_row"]) == (21, 2, 4)
    assert competition["other_count"] == 0 and competition["other_source_rows"] == []
    assert "observation_correspondence" not in compare(2, 4).raise_for_status().json()
    assert "observation_correspondence" not in compare(3, 4).raise_for_status().json()
    assert compare(3, 2).raise_for_status().json()["observation_correspondence"] == correspondence

    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert compare(2, 3).raise_for_status().json() == response
    after = client.get(base + "/flows", params={"granularity": "observations"}).raise_for_status().json()
    assert prior == after and after["funds"]["confirmed_event_count"] == 0
    assert client.get(base + f"/sources/{source}/file").content == raw
    assert client.delete(base + f"/sources/{source}/operation",
                         params={"expected_batch_id": finished["result"]["batch_id"]}).status_code == 200
    assert compare(2, 3).status_code == 409
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize("variant", [
    "valid", "duplicate_support", "contrary_support", "missing_before", "missing_after",
    "wrong_neighbor_book", "wrong_neighbor_currency", "unknown_intervening_currency",
    "unreconciled_selected", "unreconciled_after", "tied_clock", "different_reference",
    "third_reference", "ambiguous_card", "unbound_card", "independent_reference_conflict", "independent_reference_competition",
    "reversal", "distinct_typed_vouchers", "reversal_voucher_type",
])
def test_canvas_source_actions_need_two_original_local_balance_transitions(
        client, make_case, monkeypatch, variant):
    from decimal import Decimal
    from app import analysis_snapshot, feedback_import
    from app.services import trial_pair_comparison
    from test_feedback31_reference import payload
    from test_folder_evidence import transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic source action witnesses")
    base = f"/api/v1/cases/{case}"
    account_a, account_b = "000000011", "000000012"
    card_a, card_b = "000000021", "000000022"

    def row(account, card, counter, amount, balance, second, side, ref):
        return transaction(**{"交易账号": account, "交易卡号": card, "交易对手账卡号": counter,
            "交易金额": amount, "交易余额": balance, "对手交易余额": "999.00", "收付标志": side,
            "交易时间": f"2037-02-01 11:12:{second:02d}", "交易流水号": ref, "凭证号": "SYN-SHARED-VOUCHER",
            "交易币种": "CNY", "交易是否成功": "09", "查询反馈结果原因": "成功"})

    a = row(account_a, card_a, card_b, "18.73", "76.27", 20, "出", "SYN-MIRROR")
    b = row(account_b, card_b, card_a, "18.73", "58.73", 20, "进", "SYN-MIRROR")
    a["传票号"] = b["传票号"] = "SYN-SLIP"
    a_prior = row(account_a, card_a, "000000770", "18.73", "95.00", 18, "进", "SYN-SEPARATE")
    a_next = row(account_a, card_a, "000000771", "5.00", "71.27", 22, "出", "SYN-A-NEXT")
    b_prior = row(account_b, card_b, "000000772", "24.00", "40.00", 17, "进", "SYN-B-PRIOR")
    b_next = row(account_b, card_b, "000000773", "7.00", "65.73", 23, "进", "SYN-B-NEXT")
    rows = [a, b_next, a_prior, b, a_next, b_prior]
    if variant == "missing_before":
        rows.remove(a_prior)
    if variant == "missing_after":
        rows.remove(a_next)
    if variant == "wrong_neighbor_book":
        a_prior.update({"交易账号": "000000099", "交易卡号": "000000098"})
    if variant == "wrong_neighbor_currency":
        a_next["交易币种"] = "HKD"
    if variant == "unknown_intervening_currency":
        rows.append({**a_prior, "交易币种": "", "交易时间": "2037-02-01 11:12:19", "交易流水号": "SYN-NO-CURRENCY"})
    if variant == "unreconciled_selected":
        a["交易余额"] = "76.26"
    if variant == "unreconciled_after":
        a_next["交易余额"] = "71.26"
    if variant == "tied_clock":
        rows.append({**a_prior, "交易流水号": "SYN-TIED", "交易余额": "90.00"})
    if variant == "different_reference":
        b["交易流水号"] = "SYN-DIFFERENT"
    if variant == "third_reference":
        a_prior["交易流水号"] = "SYN-MIRROR"
    if variant == "ambiguous_card":
        rows.append({**a_prior, "交易账号": card_b, "交易卡号": "000000099", "交易流水号": "SYN-OTHER"})
    if variant == "unbound_card":
        rows.append({**a_prior, "交易账号": "", "交易流水号": "SYN-UNBOUND"})
    if variant == "independent_reference_conflict":
        b["传票号"] = "SYN-OTHER-SLIP"
    if variant == "independent_reference_competition":
        a_prior["传票号"] = "SYN-SLIP"
    if variant == "reversal":
        a["交易类型"] = "转账冲正"
        a["摘要说明"] = ""
    if variant == "distinct_typed_vouchers":
        a["凭证种类"] = b["凭证种类"] = "电子转账凭证"
        a["凭证号"], b["凭证号"] = "SYN-VOUCHER-PAYER", "SYN-VOUCHER-PAYEE"
    if variant == "reversal_voucher_type":
        a["凭证种类"] = b["凭证种类"] = "转账冲正凭证"
    raw = payload(rows, feedback_import.COLUMNS)
    view = preview(client, case, raw, "closed28-ledger.csv")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    job = complete(client, case, view)
    assert job["stage"] == "done"
    source = view["import_id"]
    records = client.get(f"{base}/imports/{source}/records", params={"member_path": "table.csv"}).raise_for_status().json()
    by_position = {record["source_row"]: record for record in records["items"]}
    numbers = tuple(rows.index(item) + 2 for item in (a, b))
    selected = tuple(int(by_position[number]["flow_id"]) for number in numbers)

    def pairs(*flows):
        with analysis_snapshot.case_read_snapshot(case):
            return trial_pair_comparison.canvas_ledger_pairs(case, flows)

    if variant not in {"valid", "duplicate_support", "contrary_support"}:
        assert pairs(*selected) == []
        if variant in {"missing_before", "missing_after", "unreconciled_after", "independent_reference_conflict",
                       "distinct_typed_vouchers", "reversal_voucher_type"}:
            answer = client.post(f"{base}/imports/{source}/review/compare", json={
                "member_path": "table.csv", "record_nos": [by_position[n]["record_no"] for n in numbers],
            }).raise_for_status().json()
            assert answer["observation_correspondence"]
        return
    unrelated = int(by_position[rows.index(a_prior) + 2]["flow_id"])
    expected = [{"flow_ids": selected, "source_rows": numbers, "amount": Decimal("18.73"), "currency": "CNY",
                 "source_id": source, "member_path": "table.csv", "witness_rows": ((4, 6), (7, 3))}]
    for chosen in ((selected[0],), (selected[1],), selected, (*selected, unrelated)):
        assert pairs(*chosen) == expected
    assert pairs(unrelated) == []
    if variant in {"duplicate_support", "contrary_support"}:
        second_rows = [dict(item) for item in rows]
        second_rows[-1]["备注"] = "Distinct synthetic document"
        if variant == "contrary_support":
            for item in second_rows:
                if item["交易账号"] == account_b:
                    item["交易账号"] = "000000019"
        second = preview(client, case, payload(second_rows, feedback_import.COLUMNS), "additional-source.csv")
        done = complete(client, case, second)
        assert done["stage"] == "done"
        other_source = second["import_id"]
        public = client.get(f"{base}/imports/{other_source}/records", params={"member_path": "table.csv"})
        linked = {record["source_row"]: int(record["flow_id"]) for record in public.raise_for_status().json()["items"]}
        assert linked[numbers[0]] == selected[0]
        if variant == "duplicate_support":
            assert linked[numbers[1]] == selected[1]
            [verified] = pairs(*selected)
            assert verified["source_id"] in {source, other_source}
            assert verified == {**expected[0], "source_id": verified["source_id"]}
        else:
            third = linked[numbers[1]]
            assert third != selected[1]
            for chosen in ((selected[0],), (selected[1],), (third,), selected):
                assert pairs(*chosen) == []
        assert client.delete(base + f"/sources/{other_source}/operation",
                             params={"expected_batch_id": done["result"]["batch_id"]}).status_code == 200
        assert pairs(*selected) == expected
    assert client.get(base + "/flows").raise_for_status().json()["funds"]["confirmed_event_count"] == 0
    assert client.get(base + f"/sources/{source}/file").content == raw
    assert client.delete(base + f"/sources/{source}/operation",
                         params={"expected_batch_id": job["result"]["batch_id"]}).status_code == 200
    assert pairs(*selected, unrelated) == []


@pytest.mark.parametrize("variant", [
    "text", "numeric_money", "numeric_clock", "numeric_identifier", "formula_reference", "multiple_sheets",
    "reordered_header", "expanded_limit", "changed_source",
])
def test_registered_xlsx_feedback_local_ledger_proves_only_exact_original_types(
        client, make_case, monkeypatch, variant):
    from datetime import datetime
    from decimal import Decimal
    from hashlib import sha256
    from app import analysis_snapshot, feedback_import, table_import
    from app.services import trial_pair_comparison
    from test_table_multisheet_append import encoded

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic registered workbook feedback")
    base = f"/api/v1/cases/{case}"
    columns = list(feedback_import.COLUMNS)
    card_a, card_b = "000081", "000082"
    account_a, account_b = "000011", "000012"

    def record(card, account, counter, clock, direction, amount, balance, reference, slip=""):
        return {"交易卡号": card, "交易账号": account, "交易对手账卡号": counter,
                "交易时间": f"2048-07-02 09:11:{clock:02d}", "收付标志": direction,
                "交易金额": amount, "交易余额": balance, "交易币种": "CNY",
                "交易流水号": reference, "传票号": slip, "查询反馈结果原因": "成功", "交易是否成功": "09"}

    rows = [
        record(card_a, account_a, card_b, 40, "出", "18.73", "76.27", "SYN-XLS-MIRROR", "SYN-XLS-SLIP"),
        record(card_b, account_b, card_a, 40, "进", "18.73", "58.73", "SYN-XLS-MIRROR", "SYN-XLS-SLIP"),
        record(card_a, account_a, "000099", 37, "进", "18.73", "95.00", "SYN-XLS-A-PRIOR"),
        record(card_a, account_a, "000099", 43, "出", "7.00", "69.27", "SYN-XLS-A-NEXT"),
        record(card_b, account_b, "000098", 38, "出", "22.00", "40.00", "SYN-XLS-B-PRIOR"),
        record(card_b, account_b, "000098", 45, "进", "16.00", "74.73", "SYN-XLS-B-NEXT"),
    ]
    if variant == "numeric_money":
        for row in rows:
            row["交易金额"], row["交易余额"] = map(Decimal, (row["交易金额"], row["交易余额"]))
    if variant == "numeric_clock":
        for row in rows:
            row["交易时间"] = datetime.fromisoformat(row["交易时间"])
    if variant == "numeric_identifier":
        rows[2]["交易卡号"] = int(card_a)
    if variant == "formula_reference":
        rows[2]["传票号"] = '="SYN-EXTERNAL"'
    if variant == "reordered_header":
        columns[7], columns[8] = columns[8], columns[7]
    name = "Independent worksheet identity"
    worksheets = [(name, [columns, *([row.get(column) for column in columns] for row in rows)])]
    if variant == "multiple_sheets":
        worksheets.append(("Separate evidence", [["材料说明"], ["另有已登记材料"]]))
    raw = encoded(worksheets, ".xlsx")
    view = preview(client, case, raw, "synthetic-unmodified-original.xlsx")
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    finished = complete(client, case, view)
    assert finished["stage"] == "done"
    source = view["import_id"]
    member = "worksheets/0001"
    route = base + f"/imports/{source}"
    records = client.get(route + "/records", params={"member_path": member}).raise_for_status().json()["items"]
    positions = {row["source_row"]: row for row in records}
    picked = tuple(int(positions[n]["flow_id"]) for n in (2, 3))
    compare = {"member_path": member, "record_nos": [positions[n]["record_no"] for n in (2, 3)]}
    if variant == "expanded_limit":
        monkeypatch.setattr(table_import, "MAX_LAYOUT_XLSX_BYTES", 1)
    if variant == "changed_source":
        imports._path(source, ".xlsx").write_bytes(bytes([raw[0] ^ 1]) + raw[1:])

    with analysis_snapshot.case_read_snapshot(case):
        original = trial_pair_comparison.read_registered_feedback_table(case, source)
        pairs = trial_pair_comparison.canvas_ledger_pairs(case, picked)
    response = client.post(route + "/review/compare", json=compare)
    if variant not in {"text", "numeric_money"}:
        assert original is None and pairs == []
        if variant == "changed_source":
            assert response.status_code == 409
        else:
            assert "observation_correspondence" not in response.raise_for_status().json()
        return
    source_sha = sha256(raw).hexdigest()
    assert original["source_sha256"] == source_sha
    assert original["member_sha256"] == table_import.xlsx_member_digest(source_sha, 1, name)
    assert original["member_path"] == member and original["headings"] == feedback_import.COLUMNS
    assert original["rows"][2][:3] == [card_a, account_a, "2048-07-02 09:11:40"]
    assert original["rows"][3][6] == card_a
    assert pairs == [{"source_id": source, "member_path": member, "flow_ids": picked, "source_rows": (2, 3),
                      "amount": Decimal("18.73"), "currency": "CNY", "witness_rows": ((4, 5), (6, 7))}]
    compared = response.raise_for_status().json()
    assert compared["observation_correspondence"]["reference_column"] == 21
    assert compared["observation_correspondence"]["status_column"] == 16
    assert compared["status"] == "pending" and compared["event_attribution"] == "unknown"
    assert client.get(base + "/flows").raise_for_status().json()["funds"]["confirmed_event_count"] == 0
    assert client.get(base + f"/sources/{source}/file").content == raw
    assert client.delete(base + f"/sources/{source}/operation",
                         params={"expected_batch_id": finished["result"]["batch_id"]}).status_code == 200
    assert client.post(route + "/review/compare", json=compare).status_code == 409
    with analysis_snapshot.case_read_snapshot(case):
        assert trial_pair_comparison.read_registered_feedback_table(case, source) is None
        assert trial_pair_comparison.canvas_ledger_pairs(case, picked) == []


@pytest.mark.parametrize("unproved", ["未知", "不详", "未提供", "0.00", "****1234", "XXXX1234"])
def test_placeholder_or_masked_text_does_not_establish_a_same_number_candidate(unproved):
    from app.services.trial_pair_comparison import _matching_reference

    assert not _matching_reference(unproved, unproved)


def _native32_short_competition(client, case, variant):
    from app import table_import
    from test_feedback31_reference import payload
    from test_folder_evidence import transaction

    account_a, account_b, card_a, card_b = "00000000121", "00000000122", "00000000221", "00000000222"

    def posting(book, minute, side, amount, balance, log, *, peer=None):
        account, card, counter = (account_a, card_a, card_b) if book == "A" else (account_b, card_b, card_a)
        return transaction(**{"交易账号": account, "交易卡号": card, "交易对手账卡号": counter if peer is None else peer,
            "交易金额": amount, "交易余额": balance, "交易时间": f"2037-03-02 11:00:{minute:02d}",
            "收付标志": side, "交易币种": "CNY", "交易是否成功": "1", "交易流水号": "0000",
            "日志号": log, "传票号": "SYN-REUSED-SLIP", "凭证号": "", "凭证种类": "", "查询反馈结果原因": ""})

    out_later = posting("A", 35, "出", "20.00", "72.00", "1002")
    out_earlier = posting("A", 9, "出", "20.00", "92.00", "1001")
    in_later = posting("B", 35, "进", "20.00", "80.00", "1002")
    in_earlier = posting("B", 9, "进", "20.00", "60.00", "1001")
    start_a = posting("A", 1, "进", "20.00", "100.00", "9001", peer="00000009001")
    bucket_a = posting("A", 5, "进", "10.00", "110.00", "9002", peer="00000009002")
    bucket_b = posting("A", 5, "出", "4.00", "106.00", "9003", peer="00000009003")
    bucket_c = posting("A", 5, "进", "6.00", "112.00", "9004", peer="00000009004")
    finish_a = posting("A", 42, "出", "5.00", "67.00", "9005", peer="00000009005")
    start_b = posting("B", 1, "进", "10.00", "40.00", "9006", peer="00000009006")
    finish_b = posting("B", 42, "进", "10.00", "90.00", "9007", peer="00000009007")
    rows = [out_later, bucket_c, in_earlier, bucket_b, finish_a, out_earlier, start_a, bucket_a,
            in_later, start_b, finish_b]
    if variant.startswith("recorded_destination"):
        finish_b.update({"收付标志": "出", "交易金额": "13.40", "交易余额": "66.60"})
        if variant.endswith("_currency"):
            finish_b["交易币种"] = "USD"
        elif variant.endswith("_account"):
            finish_b.update({"交易账号": "0000000870", "交易卡号": "0000000880"})
        elif variant.endswith("_tie"):
            finish_b["交易时间"] = in_later["交易时间"]
    if variant == "swap_logs":
        in_later["日志号"], in_earlier["日志号"] = in_earlier["日志号"], in_later["日志号"]
    elif variant in {"primary_competition", "slip_competition", "primary_competition_extra", "slip_competition_extra"}:
        column = "交易流水号" if variant.startswith("primary") else "传票号"
        out_later[column] = in_earlier[column] = "SYN-OPPOSITE-ONE"
        out_earlier[column] = in_later[column] = "SYN-OPPOSITE-TWO"
        if variant.endswith("_extra"):
            for index, ref in enumerate(("SYN-OPPOSITE-ONE", "SYN-OPPOSITE-TWO"), 1):
                rows.append({**posting("B", 29 + index, "进", "3.00", "12.00", f"999{index}", peer="00000009888"),
                    "交易卡号": f"0000000970{index}", "交易账号": f"0000000960{index}", column: ref})
    elif variant == "primary_reused":
        for item in rows:
            item["交易流水号"] = "SYN-BATCH-GROUP"
    elif variant == "wrong_side_reference":
        out_earlier["交易流水号"] = out_later["交易流水号"] = "SYN-ONLY-SAME-SIDE"
    elif variant == "cross_column_alias":
        out_earlier["交易流水号"] = out_later["传票号"] = in_later["传票号"] = "SYN-DIFFERENT-ROLES"
    elif variant == "different_money_reference":
        out_earlier["交易流水号"] = finish_b["交易流水号"] = "SYN-INCOMPATIBLE-AMOUNT"
        finish_b["交易对手账卡号"] = card_a
    elif variant == "duplicate_nearby":
        duplicated = posting("B", 55, "进", "20.00", "110.00", "9099")
        rows.extend((duplicated, duplicated.copy()))
    elif variant.startswith("rival_state_"):
        out_earlier["交易流水号"] = "SYN-ALTERNATIVE"
        rival_status = {"same": "1", "other_code": "01", "success": "成功", "failed": "失败"}
        rows.append({**posting("B", 55, "进", "20.00", "110.00", "9994"),
                     "交易流水号": "SYN-ALTERNATIVE", "交易是否成功": rival_status[variant.removeprefix("rival_state_")]})
    elif variant == "third_log":
        rows.append({**finish_a, "交易账号": "", "交易金额": "0.00", "日志号": in_earlier["日志号"]})
    elif variant == "empty_log":
        out_earlier["日志号"] = in_earlier["日志号"] = ""
    elif variant == "placeholder":
        out_earlier["日志号"] = in_earlier["日志号"] = "000"
    elif variant == "card_ambiguous":
        rows.append({**finish_a, "交易账号": "00000000888"})
    elif variant == "branch":
        bucket_b["交易余额"] = "96.00"  # Two different rows can now begin the group.
    elif variant == "missing":
        rows.remove(finish_a)
    elif variant == "status_disagrees":
        in_earlier["交易是否成功"] = "01"
    elif variant == "failed_opening":
        start_a["交易是否成功"] = "失败"
    elif variant == "negative_opening":
        start_a["交易金额"] = "-20.00"
    elif variant == "reversal_opening_voucher":
        start_a["凭证种类"] = "冲正凭证"
    elif variant == "reversal_after":
        finish_a["备注"] = "交易冲正"
    elif variant == "typed_voucher_conflict":
        out_earlier.update({"凭证种类": "转账凭证", "凭证号": "SYN-CERT-A"})
        in_earlier.update({"凭证种类": "转账凭证", "凭证号": "SYN-CERT-B"})
    headers = tuple(reversed(sorted(table_import.BANK_FEEDBACK_32_COLUMNS)))
    raw = payload(rows, headers)
    view = preview(client, case, raw, "synthetic-bank32.csv")
    assert view["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    done = complete(client, case, view)
    assert done["stage"] == "done"
    return raw, view["import_id"], done["result"]["batch_id"], headers


@pytest.mark.parametrize("variant", [
    "recorded_destination", "recorded_destination_currency", "recorded_destination_account",
    "recorded_destination_tie", "supported",
])
def test_recorded_destination_uses_receiving_book_and_one_shared_debit(client, make_case, monkeypatch, variant):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    raw, source, batch, headers = _native32_short_competition(client, case, variant)
    original = client.get(f"{base}/imports/{source}/review", params={"member_path": "table.csv"}).raise_for_status().json()
    ids = {row["source_row"]: row["flow_id"] for row in original["items"]}
    payer = ids[7]
    context = client.get(f"/api/context/{payer}", params={"case_id": case, "review": True, "x": 1})
    view = context.raise_for_status().json()
    destination = view["review"].get("recorded_destination")
    if variant != "recorded_destination":
        assert destination is None
        return
    assert destination["settlement"] == "unproved" and destination["actual_share"] is None
    assert destination["currency"] == "CNY" and destination["joint_upper"] == "13.40"
    assert destination["debit"] == {
        "source_row": 12, "id": ids[12], "amount": "13.40", "counterparty": "00000009007",
        "counterparty_column": headers.index("交易对手账卡号") + 1,
    }
    assert destination["bridges"] == [{"payer_id": payer, "payer_row": 7, "receiver_id": ids[4],
                                        "receiver_row": 4, "upper": "13.40"}]
    assert destination["other_credits"] == [{"id": ids[10], "source_row": 10}]
    token = view["review"]["token"]
    original_url = f"/api/context/{payer}/record/{ids[12]}"
    request = {"params": {"case_id": case}, "headers": {"X-FundsTrace-Review": token}}
    original_row = client.get(original_url, **request).raise_for_status().json()["originals"][0]
    assert original_row["source_row"] == 12
    raw_endpoint = next(cell for cell in original_row["cells"] if cell["header"] == "交易对手账卡号")
    assert raw_endpoint["value"] == "00000009007"
    assert client.get(f"/api/context/{ids[12]}", params={"case_id": case, "review": True}
                      ).raise_for_status().json()["review"].get("recorded_destination") is None

    chosen = [ids[2], ids[7]]
    assert client.post("/api/track", json={"case_id": case, "flow_ids": list(map(int, chosen))}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": case}).raise_for_status().json()
    [edge] = [item for item in graph["edges"] if set(map(str, item["ids"])) == set(chosen)]
    payload = {"case_id": case, "source": edge["source"], "target": edge["target"],
               "forward_ids": list(map(int, chosen)), "analysis_revision": graph["analysis_revision"],
               "membership_digest": graph["membership_digest"]}
    grouped = client.post("/api/context/edge", json=payload).raise_for_status().json()["review"]["recorded_destination"]
    assert [item["receiver_row"] for item in grouped["bridges"]] == [4, 10]
    assert [item["upper"] for item in grouped["bridges"]] == ["13.40", "13.40"]
    assert grouped["other_credits"] == [] and grouped["joint_upper"] == "13.40"
    assert grouped["actual_share"] is None and grouped["debit"] == destination["debit"]

    all_sides = [*chosen, ids[4], ids[10]]
    tracked = client.post("/api/track/bulk", json={"case_id": case, "flow_ids": list(map(int, all_sides)),
                                                  "direction": "target", "x": 1}).raise_for_status().json()
    assert tracked["recorded_destination"] == grouped
    assert not tracked["skipped_not_main"] and tracked["tracked"] == 5
    assert set(map(str, tracked["tracked_ids"])) == {*all_sides, ids[12]}
    graph = client.get("/api/graph", params={"case_id": case}).raise_for_status().json()
    assert graph["funds"]["confirmed_event_count"] == 0
    assert graph["ledger_summary"]["count"] == 3 and graph["ledger_summary"]["folded_pairs"] == 2
    assert client.delete(f"{base}/sources/{source}/operation", params={"expected_batch_id": batch}).status_code == 200
    assert client.get(original_url, **request).status_code == 409
    assert client.get(f"{base}/sources/{source}/file").content == raw


@pytest.mark.parametrize("variant", ["reciprocal_accounts", "broken_balance", "account_also_card", "other_account", "present_slip", "rival"])
def test_native32_empty_numbers_keep_a_reciprocal_account_pair_pending(client, make_case, monkeypatch, variant):
    from app import table_import
    from test_event_graph import graph, track
    from test_feedback31_reference import payload

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic account-to-account pair")
    base = f"/api/v1/cases/{case}"
    a, b, c = "000100111", "000100112", "000100113"
    card_a = "0007001120"

    def posting(own, card, counter, minute, marker, amount, balance):
        return {"交易账号": own, "交易卡号": card, "交易对手账卡号": counter, "交易时间": f"2041-07-08 10:{minute:02}:05",
                "交易金额": amount, "交易余额": balance, "收付标志": marker, "交易币种": "CNY",
                "交易是否成功": "1", "查询反馈结果原因": "", "对手开户银行": "Synthetic bank"}

    a0 = posting(a, card_a, c, 1, "进", "20.00", "100.00")
    a1 = posting(a, card_a, b, 2, "出", "17.25", "82.75")
    a2 = posting(a, card_a, c, 3, "出", "17.25", "65.50")
    b0 = posting(b, "", c, 1, "进", "3.00", "20.00")
    b1 = posting(b, "", a, 2, "进", "17.25", "37.25")
    b2 = posting(b, "", c, 3, "出", "2.00", "35.25")
    rows = [b1, a0, a2, b2, a1, b0]
    if variant == "broken_balance":
        b2["交易余额"] = "36.25"
    elif variant == "account_also_card":
        rows.append(posting(c, a, b, 4, "进", "3", "40"))
    elif variant == "other_account":
        b1["交易对手账卡号"] = c
    elif variant == "present_slip":
        a1["传票号"] = "SYN-SLIP"
    elif variant == "rival":
        rows.append(posting(a, card_a, b, 8, "出", "17.25", "48.25"))
    headers = tuple(sorted(table_import.BANK_FEEDBACK_32_COLUMNS))
    original = payload(rows, headers)
    ready = preview(client, case, original, "same-member.csv")
    assert ready["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    imported = complete(client, case, ready)
    assert imported["result"]["ok"] == len(rows)
    source = ready["import_id"]
    path = base + f"/imports/{source}/review"
    page = client.get(path, params={"member_path": "table.csv"}).raise_for_status().json()
    source_rows = {row["source_row"]: row for row in page["items"]}
    choice = {"member_path": "table.csv", "record_nos": [source_rows[n]["record_no"] for n in (6, 2)]}
    result = client.post(path + "/compare", json=choice).raise_for_status().json()
    assert [row["direction"] for row in result["rows"]] == ["out", "in"]
    assert all(row["own_account_column"] == headers.index("交易账号") + 1 for row in result["rows"])
    assert result["status"] == "pending" and result["event_attribution"] == "unknown"
    assert "observation_correspondence" not in result
    lead = result.get("account_pair_lead")
    if variant in {"reciprocal_accounts", "rival"}:
        assert lead["kind"] == "reciprocal_accounts" and result["reason"] == "source_account_text_pending"
        assert [row["source_row"] for row in lead["observations"]] == [2, 6]
        assert [row["balance_witness_rows"] for row in lead["observations"]] == [[5, 7], [3, 4]]
        assert [row["whole_member_candidate_count"] for row in lead["observations"]] == [1, 2 if variant == "rival" else 1]
        assert lead["empty_reference_columns"] == [headers.index(name) + 1 for name in ("传票号", "交易流水号", "日志号", "凭证号")]
    else:
        assert lead is None

    if variant != "reciprocal_accounts":
        return
    out_id, in_id, other_id = (source_rows[n]["flow_id"] for n in (6, 2, 4))
    for anchor, peer in ((out_id, in_id), (in_id, out_id)):
        context = client.get(f"/api/context/{anchor}", params={"case_id": case, "review": "true", "x": 20}).raise_for_status().json()
        pair = context["review"]["pair"]
        assert pair["account_pair_lead"] == lead and str(peer) in pair["candidate_ids"]
        token = context["review"]["token"]
        page = client.get(f"/api/context/{anchor}/record/{peer}", params={"case_id": case},
                          headers={"X-FundsTrace-Review": token}).raise_for_status().json()
        assert "按账号互指" in page["message"]
        reply = client.post(base + "/review-model-trial", json={"kind": "pair", "anchor_id": str(anchor),
            "canvas_id": "0", "review_token": token}).raise_for_status().json()
        assert (reply["state"], reply["reason"], reply["first_calls"], reply["recheck_calls"]) == (
            "pending", "source_account_text_pending", 0, 0)
    track(client, case, [{"id": out_id}, {"id": in_id}, {"id": other_id}])
    saved = graph(client, case)
    assert saved["transaction_count"] == 3 and "ledger_summary" not in saved
    assert sum(Decimal(edge["total"]) for edge in saved["edges"]) == Decimal("51.75")
    address = base + f"/sources/{source}"
    client.delete(address + "/operation", params={"expected_batch_id": imported["result"]["batch_id"]}).raise_for_status()
    assert client.post(path + "/compare", json=choice).status_code == 409
    assert graph(client, case)["transaction_count"] == 0


@pytest.mark.parametrize("variant", ["supported", "primary_reused", "wrong_side_reference", "cross_column_alias",
    "different_money_reference", "primary_competition", "slip_competition", "primary_competition_extra",
    "slip_competition_extra", "swap_logs", "third_log", "empty_log",
    "placeholder", "card_ambiguous", "branch", "missing", "status_disagrees", "failed_opening", "negative_opening",
    "reversal_opening_voucher", "reversal_after", "typed_voucher_conflict", "row_budget"])
def test_native32_log_proves_scoped_ledger_correspondence_not_settlement(client, make_case, monkeypatch, variant):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    raw, source, batch, headers = _native32_short_competition(client, case, variant)
    if variant == "row_budget":
        from app.services import trial_pair_comparison

        monkeypatch.setattr(trial_pair_comparison, "_BANK32_REFERENCE_ROWS", 11)
    url = f"{base}/imports/{source}/review"
    member = {"member_path": "table.csv"}
    originals = client.get(url, params=member).raise_for_status().json()["items"]
    by_row = {row["source_row"]: row for row in originals}
    def compare(first, second):
        selected = [by_row[row]["record_no"] for row in (first, second)]
        return client.post(url + "/compare", json={**member, "record_nos": selected})
    # All fixtures retain the same early selected coordinates except where
    # deleting a physical row intentionally moves the matching observation.
    early = (4, 6 if variant == "missing" else 7)
    verdict = compare(*early).raise_for_status().json()
    assert verdict["event_attribution"] == "unknown"
    if variant.startswith(("primary_competition", "slip_competition")):
        assert all(by_row[pos]["disposition"] == "imported" for pos in (2, 4, 7, 10))
        if variant.endswith("_extra"):
            assert all(by_row[pos]["disposition"] == "imported" for pos in (13, 14))
        for choice in ((2, 4), (4, 2), (2, 10), (10, 2), early, early[::-1], (7, 10), (10, 7)):
            result = compare(*choice).raise_for_status().json()
            assert "observation_correspondence" not in result
            assert "alternative_correspondences" not in result and result["event_attribution"] == "unknown"
        chosen = [int(by_row[row]["flow_id"]) for row in (2, 4, 7, 10)]
        assert client.post("/api/track", json={"case_id": case, "canvas_id": 0, "flow_ids": chosen}).status_code == 200
        graph = client.get("/api/graph", params={"case_id": case}).raise_for_status().json()
        assert graph.get("ledger_summary", {}).get("folded_pairs", 0) == 0
        assert (graph["funds"]["observation_count"], graph["funds"]["confirmed_event_count"]) == (4, 0)
        return

    if variant not in {"supported", "primary_reused", "wrong_side_reference", "cross_column_alias", "different_money_reference"}:
        denied = early if variant not in {"missing", "reversal_after"} else (2, 9 if variant == "missing" else 10)
        verdict = compare(*denied).raise_for_status().json()
        assert "observation_correspondence" not in verdict
        assert "alternative_correspondences" not in verdict
        return

    expected = {"source_reference_kind": "ledger_log",
                **{key: headers.index(name) + 1 for key, name in (
                    ("own_card_column", "交易卡号"), ("counterparty_identifier_column", "交易对手账卡号"),
                    ("reference_column", "日志号"), ("status_column", "交易是否成功"), ("time_column", "交易时间"),
                    ("amount_column", "交易金额"), ("currency_column", "交易币种"))}}
    late = (2, 10)
    assert verdict["observation_correspondence"] == expected
    assert compare(*late).raise_for_status().json()["observation_correspondence"] == expected
    assert compare(*early[::-1]).raise_for_status().json()["observation_correspondence"] == expected
    if variant in {"supported", "primary_reused"}:
        assert verdict["posting_comparison"]["reference_occurrences"] == [11, 11]
        assert verdict["posting_comparison"]["reference_text_matches"]
    else:
        assert not verdict["posting_comparison"]["reference_text_matches"]
    assert not verdict["posting_comparison"]["same_reference"] and "selected_reference_column" not in verdict
    alternatives = [list(late), list(early)]
    for left, right in ((late[0], early[0]), (early[1], late[1])):
        for first, second in ((left, right), (right, left)):
            rejected = compare(first, second).raise_for_status().json()
            assert (rejected["status"], rejected["reason"], rejected["event_attribution"]) == (
                "excluded", "other_ledger_correspondence", "unknown")
            assert rejected["alternative_correspondences"] == alternatives

    ids = {pos: by_row[pos]["flow_id"] for pos in (*early, *late)}
    for anchor, counterpart, possible in ((early[1], early[0], late[1]), (early[0], early[1], late[0])):
        ctx = client.get(f"/api/context/{ids[anchor]}", params={"case_id": case, "review": "true", "x": 20})
        pair = ctx.raise_for_status().json()["review"]["pair"]
        assert pair["candidate_ids"][:2] == [ids[counterpart], ids[possible]]
        assert pair["candidate_total"] == 2 and pair["candidate_total_exact"] and not pair["more"]
        assert pair["ledger_correspondence"]["source_rows"] == list(early)
        assert {pos for proof in pair["ledger_correspondence"]["witness_rows"] for pos in proof} >= {3, 5, 8, 9}
        answer = client.post(base + "/review-model-trial", json={"kind": "pair", "anchor_id": ids[anchor],
            "canvas_id": "0", "review_token": ctx.json()["review"]["token"]}).raise_for_status().json()
        assert (answer["state"], answer["reason"], answer["receipt"]["status"]) == (
            "rule", "ledger_observations_linked", "pending")
        assert answer["first_calls"] == answer["recheck_calls"] == 0
    added = client.post("/api/track", json={"case_id": case, "canvas_id": 0, "flow_ids": [int(key) for key in ids.values()]})
    assert added.status_code == 200
    graph = client.get("/api/graph", params={"case_id": case}).raise_for_status().json()
    assert set(map(str, graph["tracked_ids"])) == set(ids.values())
    assert (graph["funds"]["observation_count"], graph["funds"]["confirmed_event_count"]) == (4, 0)
    book = graph["ledger_summary"]
    assert (book["count"], book["folded_pairs"], book["amount_totals"]) == (2, 2, [{"currency": "CNY", "amount": "40.00"}])
    assert client.get(base + f"/sources/{source}/file").content == raw
    assert client.delete(base + f"/sources/{source}/operation", params={"expected_batch_id": batch}).status_code == 200
    assert compare(*early).status_code == 409


@pytest.mark.parametrize("state", ["same", "other_code", "success", "failed"])
def test_native32_reference_rival_is_not_dismissed_by_a_status_literal(client, make_case, monkeypatch, state):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    _, source, _, _ = _native32_short_competition(client, case, "rival_state_" + state)
    url = f"{base}/imports/{source}/review"
    member = {"member_path": "table.csv"}
    entries = {row["source_row"]: row for row in client.get(url, params=member).raise_for_status().json()["items"]}
    assert entries[13]["disposition"] == ("non_transaction" if state == "failed" else "imported")
    def comparison(positions):
        body = {**member, "record_nos": [entries[pos]["record_no"] for pos in positions]}
        return client.post(url + "/compare", json=body).raise_for_status().json()
    for order in ((4, 7), (7, 4)):
        result = comparison(order)
        assert bool(result.get("observation_correspondence")) == (state == "failed")
        assert result["status"] == "pending" and result["event_attribution"] == "unknown"
    assert comparison((2, 10))["observation_correspondence"]["source_reference_kind"] == "ledger_log"
    mixed = comparison((2, 4))
    assert (mixed["reason"] == "other_ledger_correspondence") == (state == "failed")
    selected = [int(entries[row]["flow_id"]) for row in (2, 4, 7, 10)]
    assert client.post("/api/track", json={"case_id": case, "canvas_id": 0, "flow_ids": selected}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": case}).raise_for_status().json()
    assert (graph["funds"]["observation_count"], graph["funds"]["confirmed_event_count"]) == (4, 0)
    assert graph["ledger_summary"]["folded_pairs"] == (2 if state == "failed" else 1)


def test_native32_pair_candidate_total_counts_linked_flows_not_source_positions(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    _, source, _, _ = _native32_short_competition(client, case, "duplicate_nearby")
    rows = client.get(base + f"/imports/{source}/records", params={
        "member_path": "table.csv"}).raise_for_status().json()["items"]
    by_row = {row["source_row"]: row for row in rows}
    assert by_row[13]["flow_id"] == by_row[14]["flow_id"]
    assert by_row[13]["disposition"] == "imported" and by_row[14]["disposition"] == "duplicate"
    ctx = client.get(f"/api/context/{by_row[7]['flow_id']}", params={
        "case_id": case, "review": "true", "x": 20}).raise_for_status().json()
    pair = ctx["review"]["pair"]
    assert pair["candidate_ids"] == [by_row[pos]["flow_id"] for pos in (4, 10, 13)]
    assert pair["candidate_total"] == 3 and pair["candidate_total_exact"] and not pair["more"]


@pytest.mark.parametrize("competition", [
    "none", "late_card", "account_in_mixed_column", "failed_nearby", "reversal_type", "reversal_with_original",
])
def test_selected_cross_source_card_pair_counts_once_only_with_complete_unique_originals(
        client, database, make_case, monkeypatch, competition):
    from app import table_import
    from app.services import desktop_model_trial
    from test_feedback31_reference import payload
    from test_folder_evidence import transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *_: pytest.fail("A rule reads the originals"))
    case = make_case("Scoped cross-source ledger")
    base = f"/api/v1/cases/{case}"
    card_a, card_b, other = "000001234567", "000001234568", "000001234569"
    account_a, account_b = "000004567891", "000004567892"

    def row(card, account, peer, tick, side, amount, balance, ref):
        return transaction(**{"交易卡号": card, "交易账号": account, "交易对手账卡号": peer,
            "交易金额": amount, "交易余额": balance, "交易时间": f"2041-03-02 08:{tick // 60:02d}:{tick % 60:02d}",
            "收付标志": side, "交易流水号": ref, "传票号": "", "日志号": "", "凭证号": "", "凭证种类": ""})

    a = [row(card_a, account_a, other, 0, "进", "5.00", "120.00", "SYN-A0"),
         row(card_a, account_a, card_b, 9, "出", "20.00", "100.00", "SYN-A1"),
         row(card_a, account_a, other, 27, "进", "3.00", "103.00", "SYN-A2")]
    b = [row(card_b, account_b, other, 5, "进", "5.00", "80.00", "SYN-B0"),
         row(card_b, account_b, card_a, 15, "进", "20.00", "100.00", "SYN-B1"),
         row(card_b, account_b, other, 25, "出", "20.00", "80.00", "SYN-B2")]
    if competition == "late_card":
        # No candidate-time cutoff can supply the missing unique raw identity.
        b.append(row(card_b, account_b, card_a, 140, "进", "20.00", "100.00", "SYN-B3"))
    if competition == "account_in_mixed_column":
        # The competing record points to the same opposite book by account.
        b.insert(2, row(card_b, account_b, account_a, 19, "进", "20.00", "120.00", "SYN-B3"))
        b[-1]["交易余额"] = "100.00"
    reversal = competition in {"reversal_type", "reversal_with_original"}
    contains_failed = reversal or competition == "failed_nearby"
    if contains_failed:
        # This original reports no posting. The next completed observation
        # still reconciles to the true incoming side's balance.
        failed = row(card_b, account_b, card_a, 19, "进", "20.00", "100.00", "SYN-B3")
        b.insert(2, {**failed, "交易是否成功": "转账失败（原报）"})
    if reversal:
        b[0].update(row(card_b, account_b, card_a, 5, "出", "20.00", "80.00", "SYN-B0"))
        b[1]["交易类型"] = "转账冲正"
        b[2]["交易时间"] = ""
    if competition == "reversal_with_original":
        b[1]["备注"] = "冲正原交易流水号：SYN-B0"
    competitive = competition in {"late_card", "account_in_mixed_column"}

    def register(rows):
        content = payload(rows, sorted(table_import.BANK_FEEDBACK_32_COLUMNS))
        view = preview(client, case, content, "synthetic.csv")
        done = complete(client, case, view)["result"]
        assert done["ok"] == len(rows) - (rows is b and contains_failed)
        job = base + f"/imports/{view['import_id']}"
        journal = client.get(job + "/records", params={"member_path": "table.csv"}).raise_for_status().json()
        if rows is b and contains_failed:
            failed = journal["items"][2]
            assert (failed["source_row"], failed["reason_code"], failed["flow_id"]) == (4, "failed_transaction", None)
        return done, [entry["flow_id"] for entry in journal["items"]]

    _, first = register(a)
    source_b, second = register(b)
    anchor, peer, near = first[1], second[1], second[-2 if competition == "late_card" else -1]
    ctx = client.get(f"/api/context/{anchor}", params={"case_id": case, "review": "true"}).raise_for_status().json()
    review = ctx["review"]
    pair = review["pair"]
    if not reversal:
        assert (pair["candidate_ids"], pair["candidate_total"], pair["candidate_total_exact"]) == ([peer], 1, True)
    assert ("ledger_correspondence" in pair) == (not competitive and not reversal)
    if not competitive and not reversal:
        assert [item["source_row"] for item in pair["ledger_correspondence"]["origins"]] == [3, 3]
        assert len({item["sha256"] for item in pair["ledger_correspondence"]["origins"]}) == 2
    if not reversal:
        native = client.post(base + "/review-model-trial", json={"kind": "pair", "anchor_id": anchor,
            "canvas_id": "0", "review_token": review["token"]}).raise_for_status().json()
        assert native["reason"] == ("source_card_text_pending" if competitive else "ledger_observations_linked")
        assert native["first_calls"] == native["recheck_calls"] == 0
        assert native["receipt"]["status"] == "pending"

    def track(*flows):
        return client.post("/api/track", json={"case_id": case, "canvas_id": 0,
                                             "flow_ids": list(map(int, flows))}).raise_for_status()

    def graph():
        return client.get("/api/graph", params={"case_id": case}).raise_for_status().json()

    track(anchor)
    assert graph()["transaction_count"] == 1
    track(peer, near)
    both = graph()
    assert both["funds"]["observation_count"] == 3 and both["funds"]["confirmed_event_count"] == 0
    assert both["transaction_count"] == 3
    if competitive or reversal:
        assert not both.get("ledger_summary")
    else:
        assert both["ledger_summary"] == {"policy": "selected-ledger-actions-v1", "count": 2, "observation_count": 3,
                                          "folded_pairs": 1, "amount_totals": [{"currency": "CNY", "amount": "40.00"}]}
        with database._lock:
            database._con.close()
            database._con = None
        assert graph()["ledger_summary"] == both["ledger_summary"]
    assert client.delete(base + f"/sources/{source_b['source_id']}/operation",
                         params={"expected_batch_id": source_b["batch_id"]}).status_code == 200
    assert (graph()["transaction_count"], graph().get("ledger_summary")) == (1, None)


@pytest.mark.parametrize("variant", ["mirror", "competition", "bounded_competition", "clock_boundary", "one_sided", "different_case"])
def test_registered_csv32_cross_source_cards_recall_only_reciprocal_observation_leads(
        client, make_case, monkeypatch, variant):
    from app import table_import
    from app.services import desktop_model_trial, trial_pair_comparison
    from test_feedback31_reference import payload
    from test_folder_evidence import transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (object(),), raising=False)
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *args: pytest.fail("textual clues must not dispatch models"))
    case = make_case("Synthetic cross-source card observations")
    other_case = make_case("Synthetic outside case") if variant == "different_case" else case
    card_a, card_b = "00000000123456", "00000000198765"
    account_a, account_b = "00000013131313", "00000017171717"

    def register(case_id, items):
        raw = payload(items, sorted(table_import.BANK_FEEDBACK_32_COLUMNS))
        view = preview(client, case_id, raw, "synthetic-bank32.csv")
        assert view["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
        done = complete(client, case_id, view)
        assert done["result"]["ok"] == len(items)
        base = f"/api/v1/cases/{case_id}"
        records = client.get(f"{base}/imports/{view['import_id']}/records", params={"member_path": "table.csv"}).raise_for_status().json()["items"]
        return records, f"{base}/sources/{view['import_id']}", done["result"]["batch_id"]

    def posting(card, account, peer, tick, direction, mark):
        return transaction(**{"交易卡号": card, "交易账号": account, "交易对手账卡号": peer,
            "交易金额": "20.00", "交易余额": "100.00", "交易时间": f"2037-03-02 11:{tick // 60:02d}:{tick % 60:02d}",
            "收付标志": direction, "交易流水号": f"SYN-ID-{mark}", "传票号": "", "日志号": "", "凭证号": "", "凭证种类": ""})

    [anchor], source_a, batch_a = register(case, [posting(card_a, account_a, card_b, 9, "出", "A")])
    ticks = (129, 130) if variant == "clock_boundary" else range(15, 25) if variant.endswith("competition") else (15,)
    counter = "00000000100007" if variant == "one_sided" else card_a
    others, source_b, batch_b = register(other_case, [posting(card_b, account_b, counter, tick, "进", f"B{offset}")
                                                     for offset, tick in enumerate(ticks)])
    if variant == "bounded_competition":
        monkeypatch.setattr(trial_pair_comparison, "_BANK32_CONTEXT_LIMIT", 4)
    flow = anchor["flow_id"]

    def context(key):
        return client.get(f"/api/context/{key}", params={"case_id": case, "review": "true", "x": 30}).raise_for_status().json()

    witness = context(flow)
    pair = witness["review"]["pair"]
    if variant in {"one_sided", "different_case"}:
        assert pair["candidate_ids"] == [] and pair["reason"] == "no_scoped_event_proof"
        return
    peers = [item["flow_id"] for item in others]
    if variant == "clock_boundary":
        assert context(peers[-1])["review"]["pair"]["candidate_ids"] == []
        peers = peers[:1]
    elif variant == "bounded_competition":
        peers = peers[:4]
    assert pair["candidate_ids"] == peers
    assert (pair["status"], pair["reason"]) == ("unknown", "source_card_text_pending")
    assert pair["candidate_total"] == len(peers)
    assert pair["source_card_clock_window_seconds"] == 120
    assert pair["candidate_total_exact"] == (variant != "bounded_competition")
    assert pair["more"] == (variant == "bounded_competition")
    assert "ledger_correspondence" not in pair
    for focus, opposing, view in ((flow, peers[0], witness), (peers[0], flow, context(peers[0]))):
        token = view["review"]["token"]
        original = client.get(f"/api/context/{focus}/record/{opposing}", params={"case_id": case},
                              headers={"X-FundsTrace-Review": token}).raise_for_status().json()
        assert len(original["originals"][0]["cells"]) == 32
        assert "原报时刻差 120 秒以内" in original["message"]
        if variant == "mirror":
            assert "原报时刻相差 6 秒" in original["message"]
        elif variant == "clock_boundary":
            assert "原报时刻相差 120 秒" in original["message"]
        model = client.post(f"/api/v1/cases/{case}/review-model-trial", json={"kind": "pair", "anchor_id": focus,
            "canvas_id": "0", "review_token": token}).raise_for_status().json()
        assert (model["state"], model["reason"], model["first_calls"], model["recheck_calls"]) == (
                "pending", "source_card_text_pending", 0, 0)
        assert model["receipt"]["status"] == "pending" and not model["receipt"].get("event_id")
        assert model["packet"]["facts"]["source_card_clock_window_seconds"] == 120
        if variant == "competition" and focus == flow:
            assert len(model["packet"]["candidates"]) == 8 and model["packet"]["facts"]["more_candidates"]
        if variant == "bounded_competition" and focus == flow:
            assert not model["packet"]["facts"]["candidate_total_exact"] and model["packet"]["facts"]["more_candidates"]
    assert client.post("/api/track", json={"case_id": case, "canvas_id": 0,
                       "flow_ids": [int(flow), int(peers[0])] }).status_code == 200
    graph = client.get("/api/graph", params={"case_id": case}).raise_for_status().json()
    assert (graph["funds"]["observation_count"], graph["funds"]["confirmed_event_count"]) == (2, 0)
    assert graph.get("ledger_summary", {}).get("folded_pairs", 0) == 0
    old_token = witness["review"]["token"]
    assert client.delete(source_b + "/operation", params={"expected_batch_id": batch_b}).status_code == 200
    assert client.get(f"/api/context/{flow}/record/{peers[0]}", params={"case_id": case},
                      headers={"X-FundsTrace-Review": old_token}).status_code == 409
    assert context(flow)["review"]["pair"]["candidate_ids"] == []
    assert client.delete(source_a + "/operation", params={"expected_batch_id": batch_a}).status_code == 200
