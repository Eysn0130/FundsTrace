"""Public delayed correspondence, checked against independent synthetic material."""
from datetime import datetime, timedelta
import csv
import hashlib
import io

import pytest

from app.services import imports
from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted
from test_source_ledger import add_source
from test_trace_review import review


def pair_sources(payer_time="2026-06-02 10:00:00", payee_time="2026-06-02 10:03:00"):
    details = {"付款人开户行": "Synthetic Bank A", "收款人开户行": "Synthetic Bank B",
               "交易金额": "37.41", "参考号机构": "Synthetic Clearing", "参考号渠道": "Synthetic Payment",
               "参考号业务域": "Synthetic 2026 Transfers", "跨账簿事件参考号": "SYN-X1"}
    payer = transaction("SYN-A", **details)
    payer.update({"本方开户银行": "Synthetic Bank A", "交易时间": payer_time, "交易流水号": "SYN-A1",
                  "本笔交易后账面余额": "962.59"})
    payee = transaction("SYN-B", **details)
    payee.update({"本方开户银行": "Synthetic Bank B", "交易时间": payee_time, "交易流水号": "SYN-B1",
                  "本笔交易后账面余额": "2037.41"})
    later = {**payee, "交易时间": "2026-06-02 10:04:00", "交易流水号": "SYN-B2", "记账顺序号": "2",
             "本笔交易后账面余额": "2074.82", "跨账簿事件参考号": "SYN-X2"}
    return [("payer.csv", csv_rows([payer])), ("payee.csv", csv_rows([payee, later]))]


def correspondence(payee_reference="SYN-B1", status="已完成且未冲正"):
    row = {"签发方": "Synthetic Clearing", "通道": "Synthetic Payment", "业务域": "Synthetic 2026 Transfers",
           "唯一跨账簿参考": "SYN-X1", "付款侧流水": "SYN-A1", "收款侧流水": payee_reference,
           "付款账号": "SYN-A", "收款账号": "SYN-B", "金额": "37.41", "币种": "CNY",
           "状态": status, "时钟口径": "付款发起时刻和收款入账时刻"}
    return csv_rows([row, {**row, "唯一跨账簿参考": "SYN-X2", "付款侧流水": "SYN-NOT-PROVIDED",
                          "收款侧流水": "SYN-B2", "状态": "已完成且未冲正"}])


def observations(client, case):
    payload = client.get(f"/api/v1/cases/{case}/flows", params={"granularity": "observations"}).json()
    assert payload["total"] == 3
    return {row["trade_no"]: row for row in payload["items"]}


def withdraw(client, case, source, batch):
    res = client.delete(f"/api/v1/cases/{case}/sources/{source}/operation", params={"expected_batch_id": batch})
    assert res.status_code == 200, res.text


def separate_correspondence(payee_reference="SYN-B1", status="已完成且未冲正", second_reference="SYN-X2"):
    row = {"签发方": "Synthetic Clearing", "通道": "Synthetic Payment", "业务域": "Synthetic 2026 Transfers",
           "跨账簿参考号": "SYN-X1", "付款流水": "SYN-A1", "收款流水": payee_reference,
           "付款账号": "SYN-A", "收款账号": "SYN-B", "金额": "37.41", "币种": "CNY", "状态": status}
    return csv_rows([row, {**row, "跨账簿参考号": second_reference, "付款流水": "SYN-NOT-PROVIDED",
                          "收款流水": "SYN-B2", "状态": "已完成且未冲正"}])


def split_scope():
    return ("独立清算机构 Synthetic Clearing 的资料。\n"
            "Synthetic Payment / Synthetic 2026 Transfers 参考号可在不同支付间复用，不能仅凭参考号相等断定同笔。\n"
            "每行完成且未冲正的清算记录明确绑定付款侧与收款侧流水；该范围内两侧各自的流水号各只属于一笔完成且未冲正的支付。\n"
            "银行文件中的付款侧交易时间是发起时间，收款侧交易时间是入账时间，二者并非同一时钟。\n").encode()


def reusable_reference(*, clock=False):
    suffix = "；两侧记载交易发生时刻" if clock else ""
    return ("Synthetic Clearing: Synthetic Payment / Synthetic 2026 Transfers 中跨账簿参考号"
            f"每个值可对应多笔支付，不能凭参考号认定同笔{suffix}。\n").encode()


@pytest.mark.parametrize(("claimed", "reason"), [
    (("37.41", "USD"), "settlement_bridge_currency_conflict_payer"),
    (("37.42", "CNY"), "settlement_bridge_amount_conflict_payer"),
])
def test_clearance_financial_conflict_keeps_both_postings_visible(client, make_case, monkeypatch, claimed, reason):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    accepted(client, case, pair_sources(), scope_bank=None)
    declarations = list(csv.DictReader(io.StringIO(separate_correspondence().decode("utf-8-sig"))))
    declarations[0]["金额"], declarations[0]["币种"] = claimed
    add_source(client, case, csv_rows(declarations), name="correspondence.csv")
    add_source(client, case, split_scope(), name="scope.txt")
    rows = observations(client, case)
    anchor = rows["SYN-A1"]["id"]
    current = review(client, case, anchor)["review"]
    assert current["pair"]["reason"] == reason
    result = client.post(f"/api/v1/cases/{case}/review-model-trial",
                         json={"kind": "pair", "anchor_id": anchor, "review_token": current["token"]}).json()
    assert (result["receipt"]["status"], result["receipt"]["reason"], result["first_calls"]) == ("pending", reason, 0)
    assert rows["SYN-A1"]["event_id"] is None and rows["SYN-B1"]["event_id"] is None


def test_clearance_lists_every_missing_opposite_posting_on_the_current_card(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    payer, payee = pair_sources()
    rows = list(csv.DictReader(io.StringIO(payee[1].decode("utf-8-sig"))))
    rows.append({**rows[-1], "交易时间": "2026-06-02 10:05:00", "交易流水号": "SYN-B3",
                 "记账顺序号": "3", "本笔交易后账面余额": "2112.23", "跨账簿事件参考号": "SYN-X3"})
    accepted(client, case, [payer, (payee[0], csv_rows(rows))], scope_bank=None)
    declarations = list(csv.DictReader(io.StringIO(separate_correspondence().decode("utf-8-sig"))))
    declarations.append({**declarations[-1], "跨账簿参考号": "SYN-X3", "付款流水": "SYN-MISSING-A3", "收款流水": "SYN-B3"})
    add_source(client, case, csv_rows(declarations), name="correspondence.csv")
    add_source(client, case, split_scope(), name="scope.txt")
    api = f"/api/v1/cases/{case}"
    rows = {row["trade_no"]: row for row in client.get(api + "/flows?granularity=observations").json()["items"]}
    anchor = rows["SYN-A1"]["id"]
    token = review(client, case, anchor, x=10)["review"]["token"]
    result = client.post(api + "/review-model-trial", json={"kind": "pair", "anchor_id": anchor, "review_token": token}).json()
    assert result["receipt"]["status"] == "accepted" and result["receipt"]["observations"] == 2
    facts = result["packet"]["facts"]
    assert not facts["more_open_book_postings"]
    assert {entry["ref"]: entry["reason"] for entry in facts["open_book_postings"]} == {
        f"F{rows[name]['id']}": "settlement_bridge_missing_posting_payer" for name in ("SYN-B2", "SYN-B3")
    }


def test_separate_clearance_scope_registers_accepts_and_withdraws_with_history(client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic separately declared clearing scope")
    api = f"/api/v1/cases/{case}"
    accepted(client, case, pair_sources(), scope_bank=None)
    rows = observations(client, case)
    payer, payee, deferred = (rows[key]["id"] for key in ("SYN-A1", "SYN-B1", "SYN-B2"))
    no_bridge = review(client, case, payer)["review"]["pair"]
    assert no_bridge["status"] == "conflict"
    assert no_bridge["reason"] == "settlement_bridge_missing_correspondence"
    accepted(client, case, [("transfer-material.csv", separate_correspondence())], scope_bank=None)
    no_scope = review(client, case, payer)["review"]["pair"]
    assert no_scope["status"] == "conflict"
    assert no_scope["reason"] == "settlement_bridge_missing_scope_statement"

    view, final = add_source(client, case, split_scope(), name="independent-material.txt")
    [note] = client.get(f"{api}/imports/{view['import_id']}/intake").json()["items"]
    assert (note["role"], note["status"], note["unresolved"]) == ("settlement_scope", "registered", 0)
    assert client.get(note["original_url"]).content == split_scope()
    first = review(client, case, payer)
    pair = first["review"]["pair"]
    assert pair["status"] == "confirmed", pair
    assert set(pair["candidate_ids"]) == {payer, payee}
    assert pair["proof"]["clock_basis"] == "incomparable"
    proof = pair["proof"]
    assert (proof["issuer"], proof["channel"], proof["business_scope"]) == (
        "Synthetic Clearing", "Synthetic Payment", "Synthetic 2026 Transfers")
    for location in (proof["witness"], proof["scope_witness"], *proof["sides"]):
        [original] = client.get(location["record_url"]).json()["items"]
        assert original["source_row"] == location["source_row"]
        original_file = client.get(location["file_url"])
        assert original_file.status_code == 200
        assert hashlib.sha256(original_file.content).hexdigest() == location["member_sha256"]
    assert proof["scope_witness"]["source_id"] == view["import_id"]
    assert client.get(proof["scope_witness"]["file_url"]).content == split_scope()
    assert review(client, case, deferred)["review"]["pair"]["reason"] == "settlement_bridge_missing_posting_payer"
    assert review(client, case, deferred)["anchor"].get("event_id") is None
    adjudicate = f"{api}/review-model-trial"
    body = {"kind": "pair", "anchor_id": payer, "review_token": first["review"]["token"]}
    response = client.post(adjudicate, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["first_calls"], result["recheck_calls"]) == ("rule", 0, 0)
    assert result["receipt"]["status"] == "accepted"
    history_url = f"{api}/model-trial/receipts/{result['receipt']['receipt_id']}"
    history = client.get(history_url).json()
    assert history["authority"] == "historical"
    assert {(location["member_path"], location["source_row"]) for location in history["originals"]} == {
        ("payer.csv", 2), ("payee.csv", 2), ("transfer-material.csv", 2), (proof["scope_witness"]["member_path"], 1)}

    path = imports._path(view["import_id"], ".txt")
    path.write_bytes(split_scope().replace(b"Synthetic 2026 Transfers", b"Synthetic 2027 Transfers"))
    stale = review(client, case, payer)
    assert stale["review"]["pair"]["reason"] == "settlement_bridge_original_unavailable"
    request = {**body, "review_token": stale["review"]["token"]}
    assert client.post(adjudicate, json=request).json()["receipt"]["status"] == "pending"
    path.write_bytes(split_scope())
    assert review(client, case, payer)["review"]["pair"]["status"] == "confirmed"

    withdraw(client, case, view["import_id"], final["result"]["batch_id"])
    assert client.post(adjudicate, json=body).status_code == 409
    pending = review(client, case, payer)["review"]["pair"]
    assert pending["status"] == "conflict" and pending["reason"] == "settlement_bridge_missing_scope_statement"
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(history_url).json() == history
    assert {key: row["balance"] for key, row in observations(client, case).items()} == {
        "SYN-A1": "962.59", "SYN-B1": "2037.41", "SYN-B2": "2074.82",
    }


@pytest.mark.parametrize(("issue", "reason"), [
    ("wrong_scope", "settlement_bridge_missing_scope_statement"),
    ("wrong_row", "settlement_bridge_reference_reused"),
    ("reversal", "settlement_bridge_declaration_unverified"),
    ("exception", "settlement_bridge_declaration_unverified"),
    ("reversed_clock", "settlement_bridge_clock_conflict"),
])
def test_split_scope_refuses_conflicting_originals(client, make_case, monkeypatch, issue, reason):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic disputed separate clearing scope")
    ledgers = (pair_sources("2026-06-02T10:00:00Z", "2026-06-02T09:59:59Z")
               if issue == "reversed_clock" else pair_sources())
    note = split_scope()
    if issue == "wrong_scope":
        note = note.replace(b"Synthetic 2026 Transfers", b"Synthetic 2027 Transfers")
    elif issue == "exception":
        note += "例外：本通道不保证付款流水在各自侧唯一。\n".encode()
    clearing = separate_correspondence(payee_reference="SYN-B2" if issue == "wrong_row" else "SYN-B1",
                                       status="已冲正" if issue == "reversal" else "已完成且未冲正")
    accepted(client, case, [*ledgers, ("cleared.csv", clearing), ("authority.txt", note)], scope_bank=None)
    payer = observations(client, case)["SYN-A1"]["id"]
    current = review(client, case, payer)
    assert current["review"]["pair"]["reason"] == reason
    assert current["review"]["pair"]["status"] != "confirmed"
    assert current["anchor"].get("event_id") is None


def test_split_scope_accepts_postings_in_same_explicit_utc_second(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic equal UTC second for distinct posting clocks")
    same_second = "2026-06-02T10:00:00Z"
    accepted(client, case, [*pair_sources(same_second, same_second),
                            ("cleared.csv", separate_correspondence()), ("authority.txt", split_scope()),
                            ("reference-reuse.txt", reusable_reference())],
             scope_bank=None)
    rows = observations(client, case)
    payer, payee = (rows[key]["id"] for key in ("SYN-A1", "SYN-B1"))
    card = review(client, case, payer)
    pair = card["review"]["pair"]
    assert pair["status"] == "confirmed"
    assert set(pair["candidate_ids"]) == {payer, payee}
    proof = pair["proof"]
    assert proof["clock_basis"] == "utc"
    assert [(side["clock_kind"], side["utc"]) for side in proof["sides"]] == [
        ("initiation", "2026-06-02T10:00:00.000000Z"),
        ("credit_posting", "2026-06-02T10:00:00.000000Z"),
    ]
    assert proof["scope_witness"]["source_row"] == 1
    result = client.post(f"/api/v1/cases/{case}/review-model-trial",
                         json={"kind": "pair", "anchor_id": payer, "review_token": card["review"]["token"]})
    assert result.status_code == 200, result.text
    assert result.json()["receipt"]["status"] == "accepted"


def test_split_scope_reuses_cross_reference_and_walks_local_competition_outside_window(
        client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic reused clearing reference")
    api = f"/api/v1/cases/{case}"
    ledgers = [(name, data.replace(b"SYN-X2", b"SYN-X1")) for name, data in pair_sources()]
    bridge = separate_correspondence(second_reference="SYN-X1")
    initial_source, _ = accepted(client, case, [
        *ledgers, ("clearing.csv", bridge), ("copies/clearing.csv", bridge),
        ("scope.txt", split_scope()), ("copies/scope.txt", split_scope()),
    ], zipped=True, scope_bank=None)
    rows = observations(client, case)
    payer, payee, deferred = (rows[key]["id"] for key in ("SYN-A1", "SYN-B1", "SYN-B2"))
    positive = review(client, case, payer)
    assert set(positive["review"]["pair"]["candidate_ids"]) == {payer, payee}
    assert positive["review"]["pair"]["status"] == "confirmed"
    proof = positive["review"]["pair"]["proof"]
    assert proof["scope_witness"]["source_id"] == initial_source["import_id"]
    assert proof["witness"]["source_id"] == initial_source["import_id"]
    assert client.get(proof["scope_witness"]["file_url"]).content == split_scope()
    assert client.get(proof["witness"]["file_url"]).content == bridge
    assert review(client, case, deferred)["review"]["pair"]["reason"] == "settlement_bridge_missing_posting_payer"
    assert review(client, case, deferred)["anchor"].get("event_id") is None

    _, later = ledgers[1]
    item = next(csv.DictReader(io.StringIO(later.decode("utf-8-sig"))))
    # A same-side local ID in the complete case can compete across accounts
    # within the declared scope and need never enter the review time window.
    item.update({"交易时间": "2026-06-10 10:05:00", "收款账号": "SYN-C", "本方账号": "SYN-C",
                 "跨账簿事件参考号": "SYN-X3"})
    source, result = accepted(client, case, [("outside-window.csv", csv_rows([item]))], scope_bank=None)
    current = review(client, case, payer)
    assert current["review"]["pair"]["status"] == "conflict"
    assert current["review"]["pair"]["reason"] == "settlement_bridge_reference_reused_payee"
    assert current["anchor"].get("event_id") is None
    withdraw(client, case, source["import_id"], result["batch_id"])
    assert review(client, case, payer)["anchor"]["event_id"] == positive["anchor"]["event_id"]
    kept = imports.cleanup_uploads(case, now=datetime.now() + timedelta(days=40), dry_run=True)
    assert kept["eligible"] == 1 and kept["protected"] == 1

    exported = client.get(f"/api/cases/{case}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    archive_case = restored.json()["case_id"]
    restored_payer = observations(client, archive_case)["SYN-A1"]["id"]
    assert review(client, archive_case, restored_payer)["review"]["pair"]["status"] != "confirmed"


def test_same_instant_reference_scope_matches_current_registered_original(client, make_case, monkeypatch):
    from app.services.trial_delayed_pair import same_instant_scope_witness

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic same-instant scope material")
    statement = ("Synthetic Clearing: Synthetic Payment / Synthetic Network 中跨账簿参考号"
                 "每个值仅对应一笔支付；两侧记载交易发生时刻。\n").encode()
    view, result = accepted(client, case, [("primary/readme.txt", statement), ("copy/text.txt", statement)],
                            zipped=True, scope_bank=None)
    materials = client.get(f"/api/v1/cases/{case}/imports/{view['import_id']}/intake").json()["items"]
    assert {(item["role"], item["status"], item["unresolved"]) for item in materials} == {
        ("event_reference_scope", "registered", 0)}
    scope = {"issuer": "Synthetic Clearing", "channel": "Synthetic Payment", "business_scope": "Synthetic Network"}
    assert same_instant_scope_witness(case, {**scope, "business_scope": "Other Network"}) is None
    witness = same_instant_scope_witness(case, scope)
    assert client.get(witness["file_url"]).content == statement
    assert client.get(witness["record_url"]).json()["items"][0]["source_row"] == 1
    assert imports.cleanup_uploads(case, now=datetime.now() + timedelta(days=40), dry_run=True)["protected"] == 1
    competing_note = split_scope().replace(b"Synthetic 2026 Transfers", b"Synthetic Network")
    conflicting, completed = add_source(client, case, competing_note, name="alternative.txt")
    assert same_instant_scope_witness(case, scope) is None
    withdraw(client, case, conflicting["import_id"], completed["result"]["batch_id"])
    assert same_instant_scope_witness(case, scope) is not None
    withdraw(client, case, view["import_id"], result["batch_id"])
    assert same_instant_scope_witness(case, scope) is None


def test_active_nonunique_reference_challenges_only_its_registered_scope(client, database, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic same-instant scope counterevidence")
    api = f"/api/v1/cases/{case}"
    stamp = "2026-06-02T10:00:00Z"
    accepted(client, case, pair_sources(stamp, stamp), scope_bank=None)
    before = observations(client, case)
    payer, payee = (before[key]["id"] for key in ("SYN-A1", "SYN-B1"))
    first = review(client, case, payer)
    event_id = first["anchor"]["event_id"]
    assert first["review"]["pair"]["status"] == "confirmed"

    prefix = "Synthetic Clearing: Synthetic Payment / Synthetic 2026 Transfers 中跨账簿参考号"
    positive = (prefix + "每个值仅对应一笔支付；两侧记载交易发生时刻。\n").encode()
    negative = reusable_reference(clock=True)
    add_source(client, case, negative.replace(b"Synthetic 2026 Transfers", b"Another Domain"),
               name="other-network.txt")
    assert review(client, case, payer)["anchor"]["event_id"] == event_id
    affirm, finished_affirm = add_source(client, case, positive, name="scope.txt")
    with_support = review(client, case, payer)
    assert with_support["anchor"]["event_id"] == event_id
    body = {"kind": "pair", "anchor_id": payer, "review_token": with_support["review"]["token"]}
    adjudicate = f"{api}/review-model-trial"
    accepted_response = client.post(adjudicate, json=body)
    assert accepted_response.status_code == 200, accepted_response.text
    receipt = accepted_response.json()["receipt"]
    assert receipt["status"] == "accepted"
    history_url = f"{api}/model-trial/receipts/{receipt['receipt_id']}"
    history = client.get(history_url).json()
    assert history["authority"] == "historical"

    preview, completion = add_source(client, case, negative, name="separate-attachment.txt")
    [material] = client.get(f"{api}/imports/{preview['import_id']}/intake").json()["items"]
    assert material["role"] == "event_reference_scope"
    assert client.get(material["original_url"]).content == negative
    now = review(client, case, payer)
    assert now["review"]["pair"]["status"] == "conflict"
    assert now["review"]["pair"]["reason"] == "source_reference_scope_nonunique"
    assert now["anchor"].get("event_id") is None
    for raw in observations(client, case).values():
        if raw["id"] in {payer, payee}:
            assert (raw["event_state"], raw["event_id"]) == ("conflict", None)
    assert client.get(f"{api}/stats").json()["event_summary"]["confirmed_events"] == 0
    assert client.post(adjudicate, json=body).status_code == 409
    pending = client.post(adjudicate, json={**body, "review_token": now["review"]["token"]})
    assert pending.status_code == 200 and pending.json()["receipt"]["status"] == "pending"
    assert client.get(history_url).json() == history

    original_path = imports._path(preview["import_id"], ".txt")
    original_path.write_bytes(negative.replace(b"Synthetic 2026 Transfers", b"Synthetic 2027 Transfers"))
    add_source(client, case, positive.replace(b"Synthetic 2026 Transfers", b"Unrelated Domain"),
               name="another-network.txt")
    unavailable = review(client, case, payer)
    assert unavailable["review"]["pair"]["reason"] == "source_reference_scope_unavailable"
    assert unavailable["anchor"].get("event_id") is None
    original_path.write_bytes(negative)

    withdraw(client, case, affirm["import_id"], finished_affirm["result"]["batch_id"])
    assert review(client, case, payer)["review"]["pair"]["reason"] == "source_reference_scope_nonunique"
    withdraw(client, case, preview["import_id"], completion["result"]["batch_id"])
    restored = review(client, case, payer)
    assert restored["review"]["pair"]["status"] == "confirmed"
    assert restored["anchor"]["event_id"] == event_id
    paraphrase = negative.replace("可对应".encode(), "可以对应".encode())
    add_source(client, case, paraphrase.replace(b"Synthetic 2026 Transfers", b"Another Domain"),
               name="other-unreviewed-scope.txt")
    assert review(client, case, payer)["anchor"]["event_id"] == event_id
    unreviewed, finished_unreviewed = add_source(client, case, paraphrase, name="unreviewed-scope.txt")
    current = review(client, case, payer)
    assert current["review"]["pair"]["reason"] == "source_reference_scope_unreviewed"
    pending = client.post(adjudicate, json={**body, "review_token": current["review"]["token"]}).json()
    assert (pending["receipt"]["status"], pending["receipt"]["reason"], pending["first_calls"]) == (
        "pending", "source_reference_scope_unreviewed", 0)
    assert client.get(history_url).json() == history
    withdraw(client, case, unreviewed["import_id"], finished_unreviewed["result"]["batch_id"])
    assert review(client, case, payer)["anchor"]["event_id"] == event_id
    assert {row["id"]: row["event_id"] for row in observations(client, case).values()
            if row["id"] in {payer, payee}} == {payer: event_id, payee: event_id}
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(history_url).json() == history


@pytest.mark.parametrize("reference_header", ["唯一跨账簿参考", "跨账簿参考"])
def test_delayed_pair_from_uploaded_originals_preserves_balances_and_revokes(
        client, database, make_case, monkeypatch, reference_header):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic independent clearing correspondence")
    ledger, _ = accepted(client, case, pair_sources(), scope_bank=None)
    rows = observations(client, case)
    payer, payee, later = (rows[key]["id"] for key in ("SYN-A1", "SYN-B1", "SYN-B2"))
    assert review(client, case, payer)["review"]["pair"]["status"] == "conflict"
    bridge = correspondence().replace("唯一跨账簿参考".encode(), reference_header.encode(), 1)
    preview, completion = add_source(client, case, bridge, name="clearing.csv")
    src = preview["import_id"]
    assert completion["result"]["ok"] == 0, completion
    [material] = client.get(f"/api/v1/cases/{case}/imports/{src}/intake").json()["items"]
    assert (material["role"], material["status"]) == ("settlement_bridge", "registered")
    assert client.get(material["original_url"]).content == bridge

    left, right = (review(client, case, key) for key in (payer, payee))
    for context in (left, right):
        pair = context["review"]["pair"]
        assert pair["status"] == "confirmed", pair
        assert set(pair["candidate_ids"]) == {payer, payee}
        proof = pair["proof"]
        assert proof["clock_basis"] == "incomparable"
        assert proof["reference"] == "SYN-X1"
        assert proof["witness"]["source_id"] == src
        assert proof["witness"]["member_sha256"] == hashlib.sha256(bridge).hexdigest()
        assert context["review"]["directional_funds"]["forward"]["totals"] == [
            {"currency": "CNY", "amount": "37.41", "lower": "37.41", "upper": "37.41"}]
        assert [side["raw_time"] for side in proof["sides"]] == ["2026-06-02 10:00:00", "2026-06-02 10:03:00"]
        assert [(side["clock_kind"], side["utc"]) for side in proof["sides"]] == [
            ("initiation", None), ("credit_posting", None)]
        assert [side["source_id"] for side in proof["sides"]] == [ledger["import_id"]] * 2
        assert [side["member_path"] for side in proof["sides"]] == ["payer.csv", "payee.csv"]
        for original in (proof["witness"], *proof["sides"]):
            [record] = client.get(original["record_url"]).json()["items"]
            assert record["source_row"] == original["source_row"] == 2
            assert client.get(original["file_url"]).status_code == 200
    assert left["anchor"]["event_id"] == right["anchor"]["event_id"]
    request = {"kind": "pair", "anchor_id": payer, "review_token": left["review"]["token"]}
    adjudicate = f"/api/v1/cases/{case}/review-model-trial"
    response = client.post(adjudicate, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["state"], result["first_calls"], result["recheck_calls"]) == ("rule", 0, 0)
    receipt = result["receipt"]
    assert receipt["status"] == "accepted" and receipt["event_id"] == left["anchor"]["event_id"]
    history_url = f"/api/v1/cases/{case}/model-trial/receipts/{receipt['receipt_id']}"
    history = client.get(history_url).json()
    assert history["authority"] == "historical"
    assert {original["member_path"] for original in history["originals"]} == {"payer.csv", "payee.csv", "table.csv"}
    assert review(client, case, later)["review"]["pair"]["reason"] == "settlement_bridge_missing_posting_payer"
    assert {key: row["balance"] for key, row in observations(client, case).items()} == {
        "SYN-A1": "962.59", "SYN-B1": "2037.41", "SYN-B2": "2074.82",
    }
    kept = imports.cleanup_uploads(case, now=datetime.now() + timedelta(days=40), dry_run=True)
    assert kept["eligible"] == 0 and kept["protected"] == 2

    exported = client.get(f"/api/cases/{case}/export")
    restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
    assert restored.status_code == 200, restored.text
    reopened = restored.json()["case_id"]
    restored_payer = observations(client, reopened)["SYN-A1"]["id"]
    assert review(client, reopened, restored_payer)["review"]["pair"]["status"] == "conflict"

    withdraw(client, case, src, completion["result"]["batch_id"])
    assert review(client, case, payer)["review"]["pair"]["status"] == "conflict"
    assert client.post(adjudicate, json=request).status_code == 409
    with database._lock:
        database._con.close()
        database._con = None
    assert client.get(history_url).json() == history
    assert imports.cleanup_uploads(case, now=datetime.now() + timedelta(days=40), dry_run=True)["eligible"] == 1
    assert observations(client, case)["SYN-A1"]["balance"] == "962.59"


@pytest.mark.parametrize("problem", ["wrong_reference", "reversed_utc", "cancelled"])
def test_delayed_pair_rejects_incompatible_independent_original(client, make_case, monkeypatch, problem):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic independent clearing mismatch")
    ledgers = (pair_sources("2026-06-02T10:00:00Z", "2026-06-02T09:58:00Z")
               if problem == "reversed_utc" else pair_sources())
    clearing = correspondence(payee_reference="SYN-WRONG" if problem == "wrong_reference" else "SYN-B1",
                              status="已冲正" if problem == "cancelled" else "已完成且未冲正")
    accepted(client, case, [*ledgers, ("clearing.csv", clearing)], scope_bank=None)
    rows = observations(client, case)
    card = review(client, case, rows["SYN-A1"]["id"])["review"]["pair"]
    assert card["status"] == "conflict"
    assert card["reason"] == {"wrong_reference": "settlement_bridge_missing_posting_payee",
                              "reversed_utc": "settlement_bridge_clock_conflict",
                              "cancelled": "settlement_bridge_declaration_unverified"}[problem]


def test_delayed_pair_rejects_new_competing_declaration_and_recovers(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic independent clearing reuse")
    ledgers = pair_sources("2026-06-02T12:00:00+02:00", "2026-06-02T10:03:00Z")
    accepted(client, case, [*ledgers, ("clearing.csv", correspondence())], scope_bank=None)
    payer = observations(client, case)["SYN-A1"]["id"]
    first = review(client, case, payer)
    proof = first["review"]["pair"]["proof"]
    assert proof["clock_basis"] == "utc"
    assert [side["utc"] for side in proof["sides"]] == ["2026-06-02T10:00:00.000000Z", "2026-06-02T10:03:00.000000Z"]
    contradiction, final = add_source(client, case, correspondence(payee_reference="SYN-B2"), name="disputed.csv")
    disputed = review(client, case, payer)
    assert disputed["review"]["pair"]["reason"] == "settlement_bridge_reference_reused"
    assert disputed["review"]["pair"]["status"] == "conflict"
    withdraw(client, case, contradiction["import_id"], final["result"]["batch_id"])
    restored = review(client, case, payer)
    assert restored["review"]["pair"]["status"] == "confirmed"
    assert restored["anchor"]["event_id"] == first["anchor"]["event_id"]


def test_reused_local_reference_in_another_active_scope_prevents_approval(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic same-ledger reference reuse")
    accepted(client, case, [*pair_sources(), ("clearing.csv", correspondence())], scope_bank=None)
    payer = observations(client, case)["SYN-A1"]["id"]
    assert review(client, case, payer)["review"]["pair"]["status"] == "confirmed"

    additional = transaction("SYN-B", **{
        "付款人开户行": "Synthetic Bank A", "收款人开户行": "Synthetic Bank B", "本方开户银行": "Synthetic Bank B",
        "交易时间": "2026-06-02 10:06:00", "交易流水号": "SYN-B1", "交易金额": "37.41",
        "本笔交易后账面余额": "2112.23", "参考号机构": "Synthetic Clearing", "参考号渠道": "Synthetic Payment",
        "参考号业务域": "Synthetic 2026 Transfers", "跨账簿事件参考号": "SYN-OTHER-SCOPE",
    })
    source, receipt = accepted(client, case, [("extra.csv", csv_rows([additional]))], scope_bank=None)
    pair = review(client, case, payer)["review"]["pair"]
    assert pair["status"] == "conflict" and pair["reason"] == "settlement_bridge_reference_reused_payee"
    withdraw(client, case, source["import_id"], receipt["batch_id"])
    assert review(client, case, payer)["review"]["pair"]["status"] == "confirmed"


@pytest.mark.parametrize("issue", ["excess_rows", "invalid_utf8", "excess_bytes"])
def test_recognized_clearance_with_unread_body_revokes_current_pair(client, make_case, monkeypatch, issue):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic incomplete clearance")
    accepted(client, case, [*pair_sources(), ("original-clearance.csv", correspondence())], scope_bank=None)
    payer = observations(client, case)["SYN-A1"]["id"]
    first = review(client, case, payer)
    assert first["review"]["pair"]["status"] == "confirmed"
    raw = correspondence(payee_reference="SYN-B2")
    if issue == "excess_rows":
        bad = next(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        unrelated = [{**bad, "唯一跨账簿参考": f"SYN-EXTRA-{index}"} for index in range(200)]
        payload = csv_rows([*unrelated, bad])
        view, result = accepted(client, case, [("competing-clearance.csv", payload)], scope_bank=None)
    else:
        alias = raw.replace("唯一跨账簿参考".encode(), "跨账簿参考".encode(), 1)
        payload = alias + (b"\xff" if issue == "invalid_utf8" else b"*" * (256 * 1024))
        view, finished = add_source(client, case, payload, name="competing-clearance.csv")
        result = finished["result"]
    assert result["ok"] == 0
    source = view["import_id"]
    api = f"/api/v1/cases/{case}"
    [material] = client.get(f"{api}/imports/{source}/intake").json()["items"]
    assert (material["role"], material["reason"], material["unresolved"]) == (
        "settlement_bridge", "settlement_bridge_coverage_incomplete", 1)
    assert material["header_row"] == material["last_row"] == 1
    assert client.get(material["original_url"]).content == payload
    current = review(client, case, payer)
    assert current["review"]["pair"]["reason"] == "settlement_bridge_coverage_incomplete"
    assert current["anchor"].get("event_id") is None
    assert current["review"]["pair"]["status"] != "confirmed"
    if issue == "excess_rows":
        exported = client.get(f"/api/cases/{case}/export")
        restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
        assert restored.status_code == 200, restored.text
    withdraw(client, case, source, result["batch_id"])
    recovered = review(client, case, payer)
    assert recovered["review"]["pair"]["status"] == "confirmed"
    assert recovered["anchor"]["event_id"] == first["anchor"]["event_id"]


@pytest.mark.parametrize("status,local_reference,reason", [
    ("已完成且未冲正", "SYN-B2", "settlement_bridge_scoped_conflict"),
    ("已冲正", "SYN-B1", "settlement_bridge_declaration_unverified"),
])
def test_conflicting_clearance_rechecks_legacy_pair_and_withdrawal_restores_it(
        client, make_case, monkeypatch, status, local_reference, reason):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic independent challenge to same-instant pair")
    time = "2026-06-02T10:00:00Z"
    accepted(client, case, pair_sources(time, time), scope_bank=None)
    payer = observations(client, case)["SYN-A1"]["id"]
    original = review(client, case, payer)
    assert original["review"]["pair"]["reason"] == "scoped_reference_and_opposite_ledgers"

    api = f"/api/v1/cases/{case}"
    body = {"kind": "pair", "anchor_id": payer, "review_token": original["review"]["token"]}
    accepted_receipt = client.post(f"{api}/review-model-trial", json=body).json()["receipt"]
    history_url = f"{api}/model-trial/receipts/{accepted_receipt['receipt_id']}"
    assert accepted_receipt["status"] == "accepted"
    bad = next(csv.DictReader(io.StringIO(correspondence(local_reference, status).decode("utf-8-sig"))))
    source, finished = add_source(client, case, csv_rows([bad]), name="new-correspondence.csv")
    withheld = review(client, case, payer)
    assert withheld["review"]["pair"]["reason"] == reason
    assert withheld["review"]["pair"]["status"] == "conflict"
    assert withheld["anchor"].get("event_id") is None
    assert client.get(history_url).json()["receipt"] == accepted_receipt
    if status == "已完成且未冲正":
        exported = client.get(f"/api/cases/{case}/export")
        restored = client.post("/api/cases/import", files={"file": ("synthetic.fundcase", exported.content)})
        assert restored.status_code == 200, restored.text
        restored_case = restored.json()["case_id"]
        restored_payer = observations(client, restored_case)["SYN-A1"]["id"]
        restored_pair = review(client, restored_case, restored_payer)["review"]["pair"]
        assert (restored_pair["status"], restored_pair["reason"]) == ("unknown", "settlement_bridge_original_unavailable")

    withdraw(client, case, source["import_id"], finished["result"]["batch_id"])
    add_source(client, case, correspondence(), name="compatible-correspondence.csv")
    recovered = review(client, case, payer)
    assert recovered["review"]["pair"]["reason"] == "scoped_reference_and_opposite_ledgers"
    assert recovered["anchor"]["event_id"] == original["anchor"]["event_id"]
