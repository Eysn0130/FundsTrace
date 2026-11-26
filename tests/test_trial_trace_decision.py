"""Public review adoption of a cited, scoped balance declaration."""
import json

import pytest

from app.services import desktop_model_trial, trial_trace_decision
from test_folder_evidence import complete
from test_trial_trace_lookup import _register


BOOK = "SYN-LOCAL-BOOK"
ISSUER = "SYN TRACE BANK"
DAY = "2032-02-10"
LEDGER_PATH = "export/ledger.csv"
NOTE_PATH = "export/basis.txt"
BALANCE = "本范围内每条明细的“余额”是该笔记账完成后的账面余额，不是可用余额，也不是授信额度。"
AVAILABLE = "表中“余额”是可用余额。"
LIMIT = "本账户在此期间没有透支；本说明不保证范围外的交易、现实资金最终归属或账户授信情况。"


def ledger(intermediate="17.20"):
    header = ("交易流水号,交易时间,付款账号,付款人开户行,收款账号,收款人开户行,本方账号,"
              "本方开户银行,交易金额,余额,交易币种,记账顺序号")
    rows = [
        ("SOURCE", "00", "SYN-EXT", BOOK, "11.15", "14.40"),
        ("OTHER-OUT", "01", BOOK, "SYN-ALT", "1.20", "13.20"),
        ("OTHER-IN", "02", "SYN-NONSELECTED", BOOK, "4.00", intermediate),
        ("TARGET1", "05", BOOK, "SYN-TO1", "6.10", "11.10"),
        ("TARGET2", "06", BOOK, "SYN-TO2", "9.00", "2.10"),
    ]
    return (header + "\n" + "\n".join(
        f"{number},{DAY} 09:{minute}:00,{payer},{ISSUER},{payee},{ISSUER},{BOOK},{ISSUER},{amount},{balance},CNY,{rank}"
        for rank, (number, minute, payer, payee, amount, balance) in enumerate(rows, 1)
    ) + "\n").encode()


def declaration(*, account=BOOK, issuer=ISSUER, currency="CNY", day=DAY, since="00:00", until="23:59:59", balance=BALANCE):
    return (f"这是合成机构 {issuer} 对所提供明细的用途说明。\n"
            f"本说明只适用于本机构账户 {account}、币种 {currency}、{day} {since} 至 {day} {until}；不适用于其他账户、币种或期间。\n"
            "在上述范围内，提供的明细是该时段记账后的完整逐笔入出记录；按明细时刻及本账记账顺序排列。\n"
            f"{balance}\n").encode()


def intake(client, case, materials):
    response = client.post(f"/api/v1/cases/{case}/imports/directory/preview",
                           files=[("files", item) for item in materials.items()])
    assert response.status_code == 200, response.text
    view = response.json()
    completed = complete(client, case, view)
    assert completed["stage"] == "done", completed
    return completed["result"]


def intake_note(client, case, raw):
    response = client.post(f"/api/v1/cases/{case}/imports/preview", files={"file": ("supplement.txt", raw)})
    assert response.status_code == 200, response.text
    completed = complete(client, case, response.json())
    assert completed["stage"] == "done" and completed["result"]["ok"] == 0, completed
    return completed["result"]


def selection(client, case, *, reverse=True, single=False):
    flows = client.get(f"/api/v1/cases/{case}/flows?granularity=observations").json()["items"]
    assert len(flows) == 5
    ids = {row["trade_no"]: row["id"] for row in flows}
    anchor = ids["TARGET1" if reverse else "SOURCE"]
    context = client.get(f"/api/context/{anchor}", params={"case_id": case, "review": True, "x": 12})
    assert context.status_code == 200, context.text
    return {"kind": "trace_source" if reverse else "trace_target", "canvas_id": "0", "anchor_id": anchor,
            "review_token": context.json()["review"]["token"], "source_flow_ids": [ids["SOURCE"]],
            "target_flow_ids": [ids["TARGET1"]] if single else [ids["TARGET1"], ids["TARGET2"]]}


def provider(client, monkeypatch, *, abstain=False):
    requests = []

    class FixedCitation:
        def __init__(self, _endpoints):
            pass

        def business_decision(self, tag, view):
            requests.append((tag, view))
            if abstain and tag == desktop_model_trial._FIRST[-1]:
                return {"choice": "DEFER", "witness": "NONE"}
            return {"choice": "B1", "witness": "B1"}

        def arbitrate_business(self, view, decisions):
            requests.append(("recheck", view))
            assert decisions[0] == {"choice": "B1", "witness": "B1"}
            assert decisions[1]["choice"] == "DEFER"
            return {"choice": "B1", "witness": "B1"}

    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", FixedCitation)
    origins = {"d1-2b": "http://127.0.0.1:49001", "d3-nano": "http://127.0.0.1:49002",
               "qwen35-4b": "http://127.0.0.1:49003"}
    monkeypatch.setattr(client.app.state, "desktop_model_trial",
                        (desktop_model_trial.parse_model_endpoints(origins),), raising=False)
    if abstain:
        monkeypatch.setattr(trial_trace_decision, "rule_balance_refs", lambda _packet: [])
    return requests


def post_review(client, case, body):
    response = client.post(f"/api/v1/cases/{case}/review-model-trial", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_cited_post_book_range_is_joint_and_current_only_while_its_original_is_live(client, make_case, monkeypatch):
    requests = provider(client, monkeypatch, abstain=True)
    case = make_case()
    raw = ledger()
    intake(client, case, {LEDGER_PATH: raw})
    receipt = intake(client, case, {LEDGER_PATH: raw, NOTE_PATH: declaration()})
    base = f"/api/v1/cases/{case}"
    reverse = selection(client, case)
    answer = post_review(client, case, reverse)
    assert (answer["state"], answer["first_calls"], answer["recheck_calls"]) == ("proposal", 2, 1)
    accepted = answer["receipt"]
    assert (accepted["status"], accepted["lower"], accepted["upper"], accepted["ledger_rows"]) == (
        "accepted", "7.85", "11.15", 5)
    facts = answer["packet"]["facts"]
    assert facts["trace_balance_proof"]["column"] == 10
    assert facts["trace_balance_proof"]["declared_ledger_rows"] == 5
    assert (facts["opening_balance"], facts["closing_balance"], facts["attribution_status"]) == (
        "3.25", "2.10", "unknown")
    note = next(row for row in answer["packet"]["records"] if row["id"] == "B1")
    assert note["locator"]["source_row"] == 4
    assert client.get(note["locator"]["file_url"]).content == declaration()
    material = json.dumps(requests, ensure_ascii=False)
    assert all(item not in material for item in (BOOK, ISSUER, DAY, LEDGER_PATH, NOTE_PATH))
    assert BALANCE in material and "第1日 09:06:00" in material
    history_url = base + f"/model-trial/receipts/{accepted['receipt_id']}"
    history = client.get(history_url).json()
    assert history["receipt"] == accepted
    assert any(item["member_path"] == NOTE_PATH and item["source_row"] == 4 for item in history["originals"])
    assert {item["source_row"] for item in history["originals"] if item["member_path"] == LEDGER_PATH} == set(range(2, 7))
    forward = post_review(client, case, selection(client, case, reverse=False))["receipt"]
    assert (forward["lower"], forward["upper"], forward["receipt_id"]) == ("7.85", "11.15", accepted["receipt_id"])
    single = post_review(client, case, selection(client, case, single=True))["receipt"]
    assert (single["lower"], single["upper"]) == ("0.00", "6.10")
    removed = client.delete(base + f"/sources/{receipt['source_id']}/operation",
                            params={"expected_batch_id": receipt["batch_id"]})
    assert removed.status_code == 200, removed.text
    assert client.post(base + "/review-model-trial", json=reverse).status_code == 409
    after = post_review(client, case, selection(client, case))
    assert after["receipt"]["status"] == "pending" and after["first_calls"] == 0
    assert client.get(history_url).json() == history


@pytest.mark.parametrize("note_row", [4, 5])
def test_explicit_scoped_balance_is_admitted_by_rule_with_models_off(client, make_case, monkeypatch, note_row):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    lines = declaration().decode().splitlines()
    lines.insert(4 if note_row == 4 else 3, LIMIT)
    raw = ("\n".join(lines) + "\n").encode()
    intake(client, case, {LEDGER_PATH: ledger(), NOTE_PATH: raw})
    result = post_review(client, case, selection(client, case))
    packet = result["packet"]
    note = next(row for row in packet["records"] if row["id"] == "B1")
    assert note["locator"]["source_row"] == note_row
    assert client.get(note["locator"]["file_url"]).content == raw
    assert (result["state"], result["first_calls"], result["recheck_calls"]) == ("rule", 0, 0)
    receipt = result["receipt"]
    assert (receipt["status"], receipt["lower"], receipt["upper"]) == ("accepted", "7.85", "11.15")
    original = client.get(f"/api/v1/cases/{case}/model-trial/receipts/{receipt['receipt_id']}").json()
    assert any(item["member_path"] == NOTE_PATH and item["source_row"] == note_row for item in original["originals"])
    assert trial_trace_decision.verify_and_recompute(case, packet, "B1", ("B1",)) is not None
    note["locator"]["source_row"] = 9 - note_row
    assert trial_trace_decision.verify_and_recompute(case, packet, "B1", ("B1",)) is None


def test_time_exclusion_preserves_amount_range_with_a_live_balance_declaration(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}"
    raw = ledger()
    intake(client, case, {LEDGER_PATH: raw})

    def review_late_credit():
        amounts = client.get(base + "/flows?granularity=observations").raise_for_status().json()["items"]
        positions = {row["trade_no"]: row["id"] for row in amounts}
        debit, credit = (positions[key] for key in ("OTHER-OUT", "OTHER-IN"))
        context = client.get(f"/api/context/{debit}", params={"case_id": case, "review": True, "x": 12})
        context.raise_for_status()
        return post_review(client, case, {
            "kind": "trace_source", "canvas_id": "0", "anchor_id": debit,
            "review_token": context.json()["review"]["token"],
            "source_flow_ids": [credit], "target_flow_ids": [debit],
        })

    pending = review_late_credit()
    assert pending["reason"] == "trace_time_exclusion" and pending["receipt"]["upper"] is None
    filed = intake(client, case, {LEDGER_PATH: raw, NOTE_PATH: declaration()})
    accepted = review_late_credit()
    assert (accepted["state"], accepted["first_calls"], accepted["recheck_calls"]) == ("rule", 0, 0)
    receipt = accepted["receipt"]
    assert (receipt["status"], receipt["lower"], receipt["upper"]) == ("accepted", "0.00", "0.00")
    history = f"{base}/model-trial/receipts/{receipt['receipt_id']}"
    assert client.get(history).raise_for_status().json()["receipt"] == receipt

    client.delete(base + f"/sources/{filed['source_id']}/operation",
                  params={"expected_batch_id": filed["batch_id"]}).raise_for_status()
    back = review_late_credit()
    assert back["reason"] == "trace_time_exclusion" and back["receipt"]["upper"] is None
    assert client.get(history).raise_for_status().json()["receipt"] == receipt


@pytest.mark.parametrize(("note", "intermediate"), [
    (declaration(account="SYN-DIFFERENT-BOOK"), "17.20"),
    (declaration(issuer="SYN DIFFERENT BANK"), "17.20"),
    (declaration(day="2032-02-11"), "17.20"),
    (declaration(balance=AVAILABLE), "17.20"),
    (declaration(balance="表中“余额”可能指账面余额或可用余额，本资料未说明采用哪一种。"), "17.20"),
    (declaration(), "17.21"),
])
def test_model_agreement_cannot_admit_a_wrong_scope_or_unreconciled_balance(client, make_case, monkeypatch, note, intermediate):
    requests = provider(client, monkeypatch)
    case = make_case()
    intake(client, case, {LEDGER_PATH: ledger(intermediate), NOTE_PATH: note})
    answer = post_review(client, case, selection(client, case))
    assert len(requests) == answer["first_calls"] == 2
    assert answer["receipt"]["status"] == "pending"
    assert answer["packet"]["facts"]["conditional_group_share"] is None
    history_url = f"/api/v1/cases/{case}/model-trial/receipts/{answer['receipt']['receipt_id']}"
    assert client.get(history_url).status_code == 404


def test_conflicting_balance_basis_in_another_active_original_stays_pending(client, make_case, monkeypatch):
    requests = provider(client, monkeypatch)
    case = make_case()
    raw = ledger()
    for note in (declaration(), declaration(balance=AVAILABLE)):
        intake(client, case, {LEDGER_PATH: raw, NOTE_PATH: note})
    answer = post_review(client, case, selection(client, case))
    assert len(requests) == 2
    assert {key for key in requests[0][1]["witnesses"]} == {"B1", "B2"}
    assert answer["receipt"]["status"] == "pending"
    assert answer["packet"]["facts"]["conditional_group_share"] is None


def test_conflicting_balance_basis_in_a_different_directory_of_the_same_original_stays_pending(client, make_case, monkeypatch):
    provider(client, monkeypatch)
    case = make_case()
    intake(client, case, {LEDGER_PATH: ledger(), NOTE_PATH: declaration(),
                          "supplement/other.txt": declaration(since="09:04", until="09:05", balance=AVAILABLE)})
    answer = post_review(client, case, selection(client, case))
    assert answer["receipt"]["status"] == "pending"
    assert answer["packet"]["facts"]["conditional_group_share"] is None


@pytest.mark.parametrize("direct", [True, False], ids=["standalone-text", "separate-archive"])
def test_an_active_separate_conflict_blocks_acceptance_until_withdrawn(client, make_case, monkeypatch, direct):
    provider(client, monkeypatch)
    case = make_case()
    intake(client, case, {LEDGER_PATH: ledger(), NOTE_PATH: declaration()})
    base = f"/api/v1/cases/{case}"
    initial = post_review(client, case, selection(client, case))["receipt"]
    assert initial["status"] == "accepted"
    historical = base + f"/model-trial/receipts/{initial['receipt_id']}"
    contradiction = declaration(balance=AVAILABLE)
    stored = (intake_note(client, case, contradiction) if direct
              else intake(client, case, {"supplement/other.txt": contradiction}))
    blocked = post_review(client, case, selection(client, case))["receipt"]
    assert blocked["status"] == "pending" and blocked["lower"] is None
    assert client.get(historical).json()["receipt"] == initial
    withdrawn = client.delete(base + f"/sources/{stored['source_id']}/operation",
                              params={"expected_batch_id": stored["batch_id"]})
    assert withdrawn.status_code == 200, withdrawn.text
    resumed = post_review(client, case, selection(client, case))["receipt"]
    assert (resumed["status"], resumed["lower"], resumed["upper"]) == ("accepted", "7.85", "11.15")


def test_separate_description_for_a_different_ledger_scope_does_not_block_acceptance(client, make_case, monkeypatch):
    provider(client, monkeypatch)
    case, elsewhere = make_case(), make_case("Another synthetic case")
    intake(client, case, {LEDGER_PATH: ledger(), NOTE_PATH: declaration()})
    intake(client, case, {
        "supplement/other-account.txt": declaration(account="OTHER", balance=AVAILABLE),
        "supplement/other-issuer.txt": declaration(issuer="OTHER BANK", balance=AVAILABLE),
        "supplement/other-currency.txt": declaration(currency="USD", balance=AVAILABLE),
        "supplement/other-period.txt": declaration(since="07:00", until="08:59:59", balance=AVAILABLE),
    })
    intake_note(client, elsewhere, declaration(balance=AVAILABLE))
    answer = post_review(client, case, selection(client, case))["receipt"]
    assert (answer["status"], answer["lower"], answer["upper"]) == ("accepted", "7.85", "11.15")


def test_incomplete_case_description_scan_cannot_admit_a_positive_citation(client, make_case, monkeypatch):
    requests = provider(client, monkeypatch)
    monkeypatch.setattr(trial_trace_decision, "_MAX_CASE_NOTES", 1)
    case = make_case()
    intake(client, case, {LEDGER_PATH: ledger(), NOTE_PATH: declaration()})
    intake_note(client, case, declaration(account="OTHER", balance=AVAILABLE))
    answer = post_review(client, case, selection(client, case))
    assert answer["packet"]["facts"]["trace_balance_scan_complete"] is False
    assert answer["receipt"]["status"] == "pending"
    assert requests == []


def test_unbound_positive_statement_cannot_supply_a_ledger_balance_basis(client, make_case, monkeypatch):
    requests = provider(client, monkeypatch)
    case = make_case()
    intake(client, case, {LEDGER_PATH: ledger()})
    intake_note(client, case, declaration())
    answer = post_review(client, case, selection(client, case))
    assert answer["receipt"]["status"] == "pending"
    assert requests == []


@pytest.mark.parametrize(("kind", "value"), [("card", "00000001"), ("name", "合成账簿持有人")])
def test_dated_card_or_name_lookup_keeps_its_binding_in_the_cited_range_receipt(client, make_case, monkeypatch, kind, value):
    provider(client, monkeypatch)
    case = make_case()
    intake(client, case, {LEDGER_PATH: ledger(), NOTE_PATH: declaration()})
    registration = _register(client, case, [f"SYN-OWNER,合成账簿持有人,{BOOK},00000001,2032-01-01,2032-12-31,{ISSUER}"])
    base = f"/api/v1/cases/{case}"
    query = {"kind": kind, "value": value, "start": DAY, "end": DAY}
    lookup = client.post(base + "/trace-ledgers", json=query)
    assert lookup.status_code == 200, lookup.text
    found = lookup.json()
    assert found["status"] == "found"
    [choice] = found["choices"]
    response = post_review(client, case, {**selection(client, case),
                           "ledger_binding": {"lookup": query, "scope_id": choice["scope_id"]}})
    receipt = response["receipt"]
    assert (receipt["status"], receipt["lower"], receipt["upper"]) == ("accepted", "7.85", "11.15")
    stored = client.get(base + f"/model-trial/receipts/{receipt['receipt_id']}").json()
    assert any(item["source_id"] == registration["source_id"] and item["source_row"] == 2
               for item in stored["originals"])
    assert any(item["member_path"] == NOTE_PATH and item["source_row"] == 4 for item in stored["originals"])


@pytest.mark.parametrize("proof", ["active", "account_inquiry", "changed", "ordinary", "archive", "archive_inquiry",
                                   "archive_changed", "archive_bad_digest", "archive_wrong_profile", "archive_wrong_sheet"])
def test_joint_trace_requires_physical_ledger_direction_for_a_blank_counterparty(
        client, database, make_case, monkeypatch, proof):
    from app.services import imports
    from test_automatic_table_import import account_inquiry_rows
    from test_table_multisheet_append import encoded
    from test_three_workflow_intake import trial_directory

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic split ledger with unnamed party")
    base = f"/api/v1/cases/{case}"
    ordinary = proof == "ordinary"
    archived = proof.startswith("archive")
    inquiry = proof == "account_inquiry" or archived and proof != "archive"
    day = DAY if ordinary else DAY.replace("-", "")
    own_heading = "本方账号" if ordinary else "账户号"
    rows = [(own_heading, "对方账号", "收入", "支出", "余额", "交易日期", "交易时间", "币种"),
            (BOOK, "", "3.25", "", "10.25", day, day + " 09:00:00", "CNY"),
            (BOOK, "SYN-FROM", "4.50", "", "14.75", day, day + " 09:01:00", "CNY"),
            (BOOK, "SYN-TO", "", "6.35", "8.40", day, day + " 09:02:00", "CNY")]
    sheets = [("SyntheticLedger", rows)]
    if inquiry:
        sheets.insert(0, ("SyntheticInquiry", account_inquiry_rows(1)))
    content = encoded(sheets, ".xlsx")
    bundle = "book/synthetic.xlsx"
    if archived:
        view = trial_directory(client, case, [(bundle, content)])
        assert view["table_summary"]["profile"] == "funds-trace-table-archive-v69"
    else:
        preview = client.post(base + "/imports/preview", files={"file": ("synthetic.xlsx", content)})
        assert preview.status_code == 200, preview.text
        view = preview.json()
        expected_profile = 69 if ordinary else 97 if inquiry else 96
        assert view["table_summary"]["profile"] == f"funds-trace-table-v{expected_profile}"
    completed = complete(client, case, view)
    assert completed["stage"] == "done" and completed["result"]["ok"] == 3, completed
    flows = client.get(base + "/flows?granularity=observations").json()["items"]
    by_amount = {row["amount"]: row for row in flows}
    missing, incoming, outgoing = (by_amount[key]["id"] for key in ("3.25", "4.50", "6.35"))
    assert by_amount["3.25"]["raw_payer_account"] == "" and by_amount["3.25"]["raw_payee_account"] == BOOK
    source = view["import_id"]
    member = f"worksheets/{len(sheets):04d}"
    if archived:
        member = f"{bundle}/{member}"
    if proof in {"changed", "archive_changed"}:
        stored = imports._path(source, ".zip" if archived else ".xlsx")
        raw = stored.read_bytes()
        stored.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    if proof == "archive_bad_digest":
        database.x("update import_members set sha256=? where case_id=? and source_id=? and member_path=?",
                   ["1" * 64, case, source, member])
    if proof == "archive_wrong_profile":
        database.x("""update import_records set details_json=json_merge_patch(details_json, ?)
            where case_id=? and source_id=? and member_path=? and source_row=2""",
                   ['{"trial_member_parser_version":"funds-trace-table-v95"}', case, source, member])
    if proof == "archive_wrong_sheet":
        database.x("""update import_records set details_json=json_merge_patch(details_json, ?)
            where case_id=? and source_id=? and member_path=? and source_row=2""",
                   ['{"sheet":"SyntheticInquiry"}', case, source, member])
    if proof == "archive_inquiry":
        with database._lock:
            database._con.close()
            database._con = None
    if archived:
        monkeypatch.setattr(client.app.state, "desktop_model_trial", (object(),))
        monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *_: pytest.fail("an observed posting is not a balance proof"))

    for reverse in (False, True):
        anchor = outgoing if reverse else incoming
        context = client.get(f"/api/context/{anchor}", params={"case_id": case, "review": True, "x": 12,
                             "side": "payer" if reverse else "payee"})
        assert context.status_code == 200, context.text
        body = {"kind": "trace_source" if reverse else "trace_target", "canvas_id": "0", "anchor_id": anchor,
                "review_token": context.json()["review"]["token"], "source_flow_ids": [missing, incoming],
                "target_flow_ids": [outgoing]}
        response = client.post(base + "/review-model-trial", json=body)
        if proof not in {"active", "account_inquiry", "archive", "archive_inquiry"}:
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "assistance_wrong_ledger_direction"
            continue
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
            "pending", "ledger_evidence_missing", 0, 0)
        facts = result["packet"]["facts"]
        assert facts["checks"]["ledger_roles"] is True and facts["checks"]["own_balance"] is False
        assert facts["checks"]["currency_amount"] is True and facts["currency"] == "CNY"
        assert (facts["source_count"], facts["target_count"], facts["conditional_group_share"]) == (2, 1, None)
        assert result["receipt"]["status"] == "pending" and result["receipt"]["lower"] is None
        record = next(item for item in result["packet"]["records"] if item["id"] == f"F{missing}")
        assert record["raw"]["raw_payer_account"] == "" and record["locator"]["source_row"] == 2
        assert record["raw"]["amount"] == "3.25" and record["structured"]["book_direction"] == "in"
        assert record["locator"]["member_path"] == member
        history = base + f"/model-trial/receipts/{result['receipt']['receipt_id']}"
        assert client.get(history).status_code == 404

        if not reverse:
            wrong = client.post(base + "/review-model-trial", json={
                **body, "source_flow_ids": [incoming], "target_flow_ids": [missing, outgoing]})
            assert wrong.status_code == 422, wrong.text
            assert wrong.json()["error"]["code"] == "assistance_wrong_ledger_direction"
    if proof == "archive_inquiry":
        origin = client.get(f"/api/context/{anchor}/record/{missing}", params={"case_id": case},
                            headers={"X-FundsTrace-Review": body["review_token"]})
        assert origin.status_code == 200, origin.text
        [row] = origin.json()["originals"]
        assert (row["member_path"], row["source_row"]) == (member, 2)
        assert next(cell["value"] for cell in row["cells"] if cell["header"] == "对方账号") == ""
        removed = client.delete(base + f"/sources/{source}/operation",
                                params={"expected_batch_id": completed["result"]["batch_id"]})
        assert removed.status_code == 200, removed.text
        assert client.post(base + "/review-model-trial", json=body).status_code == 409
        assert client.get(history).status_code == 404

def test_native_archive_checks_book_direction_on_the_registered_csv(client, make_case, monkeypatch):
    from test_folder_evidence import preview, table, transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic archive bank posting")
    url = f"/api/v1/cases/{case}"
    rows = []
    for tag, minute, side, amount, peer in (
        ("IN1", "00", "进", "3.25", ""), ("IN2", "01", "进", "4.50", "SYN-PAYER"),
        ("OUT", "02", "出", "6.35", "SYN-PAYEE"),
    ):
        rows.append(transaction(**{"交易流水号": f"SYN-{tag}", "交易时间": f"{DAY} 09:{minute}:00",
                                   "收付标志": side, "交易金额": amount, "交易对手账卡号": peer,
                                   "对手户名": "" if not peer else "Synthetic counterparty"}))
    view = preview(client, case, [("signed-bank.csv", table("transaction", rows))], zipped=True, scope_bank=None)
    assert view["table_summary"]["profile"] == "funds-trace-table-archive-v67"
    assert complete(client, case, view)["result"]["ok"] == 3
    wallet = {r["amount"]: r for r in client.get(url + "/flows?granularity=observations").raise_for_status().json()["items"]}
    missing, incoming, outgoing = (wallet[amount]["id"] for amount in ("3.25", "4.50", "6.35"))
    assert not wallet["3.25"]["raw_payer_account"]

    ctx = client.get(f"/api/context/{outgoing}", params={"case_id": case, "review": True, "x": 12, "side": "payer"})
    assert ctx.status_code == 200
    request = {"kind": "trace_source", "canvas_id": "0", "anchor_id": outgoing, "review_token": ctx.json()["review"]["token"],
               "source_flow_ids": [missing, incoming], "target_flow_ids": [outgoing]}
    reply = client.post(url + "/review-model-trial", json=request).raise_for_status().json()
    assert reply["packet"]["facts"]["checks"]["ledger_roles"] is True
    assert reply["packet"]["facts"]["conditional_group_share"] is None
    assert reply["state"] == "pending" and reply["first_calls"] == reply["recheck_calls"] == 0
    original = next(item for item in reply["packet"]["records"] if item["id"] == f"F{missing}")
    assert original["locator"]["source_row"] == 2 and original["structured"]["book_direction"] == "in"


def _selected_order_source(client, case, *, variant="verified", compact=False):
    from app import table_import
    from test_feedback31_reference import payload

    owner, counter = "00000210001", "00000220001"
    records = []
    for ref, side, amount, minute in (
        ("LATE-OUT", "出", "50.03", "03"), ("LATE-IN", "进", "10.25", "02"),
        ("EARLY-IN", "进", "60.75", "00"), ("EARLY-OUT", "出", "63.25", "01"),
    ):
        records.append({"交易账号": owner, "交易对手账卡号": counter, "交易时间": f"{DAY} 09:{minute}:00",
                        "交易金额": amount, "交易余额": "120.00", "收付标志": side, "交易币种": "CNY",
                        "交易流水号": ref, "交易是否成功": "1", "查询反馈结果原因": "成功"})
    if variant == "same_clock":
        records[1]["交易时间"] = records[3]["交易时间"]
    elif variant == "tied_credits":
        records[1]["交易时间"] = records[2]["交易时间"]
    elif variant == "tied_debits":
        records[0]["交易时间"] = records[3]["交易时间"]
    elif variant == "minute_only":
        records[1]["交易时间"] = records[1]["交易时间"][:-3]
    elif variant == "date_only":
        records[1]["交易时间"] = DAY
    elif variant == "mixed_currency":
        records[0]["交易币种"] = "USD"
    elif variant == "mixed_account":
        records[0].update({"交易账号": counter, "交易对手账卡号": owner})
    elif variant == "different_targets":
        records[0]["交易对手账卡号"] = "00000220002"
    if compact:
        renamed = {"交易账号": "本方账号", "交易对手账卡号": "对方账号", "交易时间": "交易时间",
                   "交易金额": "交易金额", "交易余额": "余额", "收付标志": "收支标志",
                   "交易币种": "币种", "交易流水号": "交易流水号"}
        raw = payload([{target: record[origin] for origin, target in renamed.items()} for record in records],
                      tuple(renamed.values()))
    else:
        raw = payload(records, tuple(reversed(sorted(table_import.BANK_FEEDBACK_32_COLUMNS))))
    base = f"/api/v1/cases/{case}"
    view = client.post(base + "/imports/preview", files={"file": ("synthetic.csv", raw)}).raise_for_status().json()
    assert view["table_summary"]["profile"] == table_import.VOUCHER_REFERENCE_PROFILE
    saved = complete(client, case, view)
    assert saved["stage"] == "done" and saved["result"]["ok"] == 4
    items = client.get(base + "/flows?granularity=observations").raise_for_status().json()["items"]
    ids = {row["trade_no"]: row["id"] for row in items}
    return base, ids, raw, saved["result"]


def _selected_order_request(client, case, ids, *, reverse=False, reorder=False):
    source_ids = [ids["EARLY-IN"], ids["LATE-IN"]]
    target_ids = [ids["EARLY-OUT"], ids["LATE-OUT"]]
    anchor = target_ids[0] if reverse else source_ids[0]
    opened = client.get(f"/api/context/{anchor}", params={"case_id": case, "review": True, "x": 20,
                        "side": "payer" if reverse else "payee"}).raise_for_status().json()
    if reorder:
        source_ids.reverse()
        target_ids.reverse()
    return {"kind": "trace_source" if reverse else "trace_target", "anchor_id": anchor, "canvas_id": "0",
            "review_token": opened["review"]["token"], "source_flow_ids": source_ids, "target_flow_ids": target_ids}


@pytest.mark.parametrize("control", ["exact", "minute_only", "unbound_query"])
def test_selected_order_rechecks_explicit_workbook_postings(client, make_case, monkeypatch, control):
    from app.table_source_roles import _TRANSACTION_EXPLICIT
    from test_feedback31_reference import payload

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic explicit-account workbook")
    base = f"/api/v1/cases/{case}"
    owner = "000005230001"
    rows = [
        {"交易流水号": "SYN-IN", "交易时间": "20320210090000", "交易金额": "8.25", "借贷标志": "进"},
        {"交易流水号": "SYN-NONSELECTED", "交易时间": "20320210090100", "交易金额": "4.20", "借贷标志": "进"},
        {"交易流水号": "SYN-OUT", "交易时间": "20320210090200", "交易金额": "9.15", "借贷标志": "出",
         "交易对方账号": "000005230002"},
    ]
    for row in rows:
        row.update({"本方账号": owner, "查询帐号": owner, "币种": "CNY", "交易是否成功": "1"})
    if control == "minute_only":
        rows[0]["交易时间"] = "2032-02-10 09:00"
    elif control == "unbound_query":
        rows[0]["查询帐号"] = ""
    raw = payload(rows, _TRANSACTION_EXPLICIT, suffix="xlsx")
    view = client.post(base + "/imports/preview", files={"file": ("synthetic.xlsx", raw)}).raise_for_status().json()
    assert view["table_summary"]["profile"] == "funds-trace-table-v69"
    saved = complete(client, case, view)["result"]
    assert saved["ok"] == 3
    flows = client.get(base + "/flows?granularity=observations").raise_for_status().json()["items"]
    by_reference = {flow["trade_no"]: flow for flow in flows}
    source, target = by_reference["SYN-IN"], by_reference["SYN-OUT"]
    assert not source["raw_payer_account"] and source["raw_self_account"] == owner
    context = client.get(f"/api/context/{source['id']}", params={"case_id": case, "review": True,
                         "x": 12, "side": "payee"}).raise_for_status().json()
    request = {"kind": "trace_target", "canvas_id": "0", "anchor_id": source["id"],
               "review_token": context["review"]["token"], "source_flow_ids": [source["id"]],
               "target_flow_ids": [target["id"]]}
    reply = client.post(base + "/review-model-trial", json=request)
    if control != "exact":
        assert reply.status_code == 422
        assert reply.json()["error"]["code"] == "assistance_wrong_ledger_direction"
        return
    facts = reply.raise_for_status().json()["packet"]["facts"]
    bound = facts["selected_order_upper_bound"]
    assert bound["joint_upper"] == "8.25"
    assert [(target["source_row"], target["upper"]) for target in bound["targets"]] == [(4, "8.25")]
    assert facts["conditional_group_share"] is None
    removed = client.delete(base + f"/sources/{view['import_id']}/operation",
                            params={"expected_batch_id": saved["batch_id"]})
    assert removed.status_code == 200
    assert client.post(base + "/review-model-trial", json=request).status_code == 409


@pytest.mark.parametrize("compact", [False, True])
def test_selected_trace_order_bounds_use_source_clocks_and_do_not_double_count_credit(client, make_case, monkeypatch, compact):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (object(),), raising=False)
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *_: pytest.fail("amount capacity is a rule"))
    case = make_case()
    base, ids, raw, saved = _selected_order_source(client, case, compact=compact)
    original = base + "/sources/" + saved["source_id"]
    expected = {"currency": "CNY", "joint_upper": "71.00", "targets": [
        {"ref": "F" + ids["EARLY-OUT"], "source_row": 5, "upper": "60.75", "later_source_rows": [3]},
        {"ref": "F" + ids["LATE-OUT"], "source_row": 2, "upper": "50.03", "later_source_rows": []},
    ]}
    for reverse, reorder in ((False, False), (False, True), (True, True)):
        body = _selected_order_request(client, case, ids, reverse=reverse, reorder=reorder)
        result = post_review(client, case, body)
        facts = result["packet"]["facts"]
        assert facts["selected_order_upper_bound"] == expected
        assert facts["conditional_group_share"] is None and facts["attribution_status"] == "unknown"
        assert result["receipt"]["status"] == "pending" and result["receipt"]["lower"] is None
        assert result["receipt"]["upper"] is None and result["first_calls"] == result["recheck_calls"] == 0
        wrong = client.post(base + "/review-model-trial", json={
            **body, "source_flow_ids": body["target_flow_ids"], "target_flow_ids": body["source_flow_ids"],
            "anchor_id": body["anchor_id"],
        })
        assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "assistance_invalid_joint_group"

    assert client.get(original + "/file").content == raw
    assert client.get(base + "/flows?granularity=observations").raise_for_status().json()["total"] == 4
    retired = client.delete(original + "/operation", params={"expected_batch_id": saved["batch_id"]})
    assert retired.status_code == 200
    assert client.post(base + "/review-model-trial", json=body).status_code == 409
    assert client.get(base + "/flows?granularity=observations").raise_for_status().json()["total"] == 0


def test_ordinary_single_target_can_report_same_order_limit_as_explicit_selection(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base, ids, _, saved = _selected_order_source(client, case, compact=True, variant="different_targets")
    source, first = ids["EARLY-IN"], ids["EARLY-OUT"]
    opened = client.get(f"/api/context/{source}", params={"case_id": case, "review": True, "x": 20,
                        "side": "payee"}).raise_for_status().json()
    body = {"kind": "trace_target", "anchor_id": source, "canvas_id": "0",
            "review_token": opened["review"]["token"]}
    one = next(clue for clue in opened["suggestions"]["targets"] if first in clue["ids"])
    assert one["ids"] == [first]
    direct = post_review(client, case, {**body, "group_flow_id": first})
    explicit = post_review(client, case, {**body, "source_flow_ids": [source], "target_flow_ids": [first]})
    facts = direct["packet"]["facts"]
    assert facts["selected_order_upper_bound"] == explicit["packet"]["facts"]["selected_order_upper_bound"]
    assert facts["selected_order_upper_bound"]["joint_upper"] == "60.75"
    assert facts["conditional_group_share"] is None
    assert direct["receipt"]["status"] == "pending"
    assert (direct["first_calls"], direct["recheck_calls"], direct["explanation_calls"]) == (0, 0, 0)
    original = base + "/sources/" + saved["source_id"]
    assert client.delete(original + "/operation", params={"expected_batch_id": saved["batch_id"]}).status_code == 200
    assert client.post(base + "/review-model-trial", json={**body, "group_flow_id": first}).status_code == 409


@pytest.mark.parametrize("change", ["none", "wrong_amount", "wrong_target", "bad_reference", "omit_assumptions",
                                    "double_count", "claim_attainable"])
def test_selected_order_explanation_is_limited_to_verified_positions_and_upper_bounds(
        client, make_case, monkeypatch, change):
    from test_desktop_model_trial import explanation_transport

    case = make_case()
    sentence = ("若所选记录有效，且原交易时钟反映资金可用顺序。原行5这笔出账单独核算时至多可用CNY60.75，"
                "原行2这笔出账单独核算时至多可用CNY50.03。所选目标合计至多可用CNY71.00。"
                "同一份入账资金不能在多个目标中重复计算。这些上限只按所选流水和时序计算，不保证实际可达，实际本金归属待核。")
    text = {
        "none": sentence,
        "wrong_amount": sentence.replace("71.00", "110.78"),
        "wrong_target": sentence.replace("原行5", "原行4"),
        "bad_reference": sentence,
        "omit_assumptions": sentence.split("。", 1)[1],
        "double_count": sentence.replace("不能在多个目标中重复计算", "可以在多个目标中重复计算"),
        "claim_attainable": sentence.replace("不保证实际可达", "保证实际可达"),
    }[change]
    refs = ["R1", "R2", "R3", "R4"] if change != "bad_reference" else ["R1", "R2", "R3", "R9"]
    calls = explanation_transport(client, monkeypatch, {"text": text, "refs": refs})
    base, ids, raw, saved = _selected_order_source(client, case, compact=True)
    body = _selected_order_request(client, case, ids)
    url = base + "/review-model-trial"
    plain = client.post(url, json=body).raise_for_status().json()
    assert plain["explanation_calls"] == 0 and not calls
    result = client.post(url, json={**body, "explain": True}).raise_for_status().json()
    assert result["first_calls"] == result["recheck_calls"] == 0 and result["explanation_calls"] == 1
    assert result["receipt"]["status"] == "pending" and result["receipt"]["upper"] is None
    assert result["packet"]["facts"]["selected_order_upper_bound"] == plain["packet"]["facts"]["selected_order_upper_bound"]
    facts = json.loads(calls[0][1]["messages"][0]["content"].splitlines()[-1])
    assert [item["原行"] for item in facts["目标"]] == [5, 2]
    assert [item["单独上界"] for item in facts["目标"]] == ["60.75", "50.03"]
    assert facts["合计上界"] == "71.00" and facts["币种"] == "CNY"
    assert calls[2][1]["json_schema"]["properties"]["refs"]["enum"] == [["R1", "R2", "R3", "R4"]]
    assert not any(identifier in str(facts) for identifier in ("EARLY-IN", "LATE-OUT", "file_url"))
    assert (result["explanation"], result["explanation_model"]) == (
        (sentence, "qwen35-4b") if change == "none" else (None, None))
    original = base + "/sources/" + saved["source_id"]
    assert client.delete(original + "/operation", params={"expected_batch_id": saved["batch_id"]}).status_code == 200
    assert client.post(url, json={**body, "explain": True}).status_code == 409
    assert client.get(original + "/file").content == raw
    assert [route for route, _ in calls] == ["/apply-template", "/tokenize", "/completion"]


@pytest.mark.parametrize("variant", ["same_clock", "minute_only", "date_only", "mixed_currency", "mixed_account",
                                    "changed", "stored_clock_conflict"])
def test_selected_trace_order_bounds_need_active_matching_comparable_originals(
        client, database, make_case, monkeypatch, variant):
    from app.services import imports

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base, ids, raw, saved = _selected_order_source(client, case, variant=variant)
    if variant == "stored_clock_conflict":
        database.x("update flows set trade_time=? where case_id=? and id=?",
                   [DAY + " 09:04:00", case, int(ids["LATE-IN"])])
    body = _selected_order_request(client, case, ids)
    if variant == "changed":
        path = imports._path(saved["source_id"], ".csv")
        modified = raw.replace(b"09:02:00", b"09:04:00", 1)
        assert modified != raw and len(modified) == len(raw)
        path.write_bytes(modified)
    response = client.post(base + "/review-model-trial", json=body)
    if variant == "mixed_account":
        assert response.status_code == 422 and response.json()["error"]["code"] == "assistance_group_outside_card"
        return
    facts = response.raise_for_status().json()["packet"]["facts"]
    assert "selected_order_upper_bound" not in facts
    assert facts["conditional_group_share"] is None and facts["attribution_status"] == "unknown"


@pytest.mark.parametrize("variant", ["tied_credits", "tied_debits"])
def test_selected_trace_order_bounds_keep_same_direction_ties_as_separate_postings(
        client, make_case, monkeypatch, variant):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (object(),), raising=False)
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda *_: pytest.fail("a same-direction tie is a rule"))
    case = make_case()
    base, ids, raw, saved = _selected_order_source(client, case, variant=variant)
    original = base + "/sources/" + saved["source_id"]
    if variant == "tied_credits":
        tied = (("EARLY-OUT", 5, "63.25"), ("LATE-OUT", 2, "50.03"))
        joint, later = "71.00", []
    else:
        tied = (("LATE-OUT", 2, "50.03"), ("EARLY-OUT", 5, "60.75"))
        joint, later = "60.75", [3]
    expected = {"currency": "CNY", "joint_upper": joint, "same_direction_ties": True,
                "targets": [{"ref": "F" + ids[role], "source_row": position, "upper": upper,
                             "later_source_rows": later} for role, position, upper in tied]}
    flows = base + "/flows?granularity=observations"
    before = client.get(flows).raise_for_status().json()
    for reverse, reorder in ((False, False), (False, True), (True, True)):
        request = _selected_order_request(client, case, ids, reverse=reverse, reorder=reorder)
        result = post_review(client, case, request)
        facts = result["packet"]["facts"]
        assert facts["selected_order_upper_bound"] == expected
        assert facts["conditional_group_share"] is None and facts["attribution_status"] == "unknown"
        assert result["receipt"]["status"] == "pending"
        assert result["receipt"]["lower"] is None and result["receipt"]["upper"] is None
        assert result["first_calls"] == result["recheck_calls"] == 0
        selected = set(facts["source_refs"] + facts["target_refs"])
        assert len(selected) == 4 and {record["locator"]["source_row"] for record in result["packet"]["records"]
                                       if record["id"] in selected} == {2, 3, 4, 5}
    assert client.get(flows).raise_for_status().json() == before
    assert before["total"] == 4 and before["funds"]["confirmed_event_count"] == 0
    assert client.get(original + "/file").content == raw
    assert client.delete(original + "/operation", params={"expected_batch_id": saved["batch_id"]}).status_code == 200
    assert client.post(base + "/review-model-trial", json=request).status_code == 409
    assert client.get(flows).raise_for_status().json()["total"] == 0
