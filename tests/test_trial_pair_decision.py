"""Public source receipts and atomic publication of a supported cited pair."""
import json
from types import SimpleNamespace

import pytest

from app import db, event_resolution
from app.services import desktop_model_trial, trial_pair_decision
from test_event_graph import graph, track
from test_event_import import csv_rows, transaction
from test_folder_evidence import accepted
from test_source_ledger import add_source
from test_trace_review import review
from test_trial_delayed_pair import withdraw


ISSUER = "Synthetic correspondent"
CHANNEL = "Synthetic channel"
SCOPE = "Synthetic scope"
PROVIDER = {"d1-2b": ("127.0.0.1", 49001), "d3-lite": ("127.0.0.1", 49002), "qwen35-4b": ("127.0.0.1", 49003)}


def note(*, issuer=ISSUER, ambiguous=False):
    lines = [f"独立清算机构 {issuer} 的跨行对应说明。",
             f"本函仅适用于 {issuer} / {CHANNEL} / {SCOPE}、2026-01-01、付款账户 SYN-A 和收款账户 SYN-B；"
             "不适用于其他签发机构、通道、业务域、账户或日期。",
             "本域流水号仅在上述签发机构及业务域内解释。共享参考号可被不同支付复用，不能单靠相等参考号认定同笔。"
             "付款与入账记录的时刻分别为发起和入账，两个时钟不能直接比较。"]
    if ambiguous:
        lines.append("本函仅确认上述机构业务域存在两笔等额支付；现有资料未说明每个付款局部流水与哪个收款局部流水相对应。"
                     "不得把局部流水的排列位置当作配对约定。")
    else:
        lines.extend(["在上列适用范围内，付款账簿局部流水 SYN-A1 对应收款账簿局部流水 SYN-B2，二者是同一笔 7.20 CNY 已完成且未冲正支付。",
                      "在上列适用范围内，付款账簿局部流水 SYN-A2 对应收款账簿局部流水 SYN-B1，二者是另一笔 7.20 CNY 已完成且未冲正支付。",
                      "这些局部流水号各只归属所述一笔支付，不存在复用或该两笔的冲正。"])
    return ("\n".join(lines) + "\n").encode()


def single_note(payer="SYN-A1", payee="SYN-B2", *, scope=SCOPE, status="已完成且未冲正"):
    lines = note().decode().splitlines()[:3]
    lines[1] = lines[1].replace(SCOPE, scope)
    lines.extend([f"在上列适用范围内，付款账簿局部流水 {payer} 对应收款账簿局部流水 {payee}，二者是同一笔 7.20 CNY {status}支付。",
                  "本函仅确认上列这一笔支付。上述两个局部流水号仅归属该笔支付，且该笔未发生冲正。其余记录的对应关系未核定。"])
    return ("\n".join(lines) + "\n").encode()


def ledger(own, first, *, count=2):
    balances = ("30.00", "22.80", "15.60") if own == "A" else ("27.20", "34.40", "41.60")
    return csv_rows([transaction("SYN-" + own, **{
        "交易流水号": f"SYN-{own}{number}", "交易时间": f"2026-01-01 10:0{first + number - 1}:00",
        "参考号机构": ISSUER, "参考号渠道": CHANNEL, "参考号业务域": SCOPE, "交易金额": "7.20",
        "本笔交易后账面余额": balances[number - 1],
        "记账顺序号": str(number)}) for number in range(1, count + 1)])


def create_scope(client, make_case, monkeypatch, document, *, count=2):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic cited pair")
    accepted(client, case, [("payer.csv", ledger("A", 0, count=count)), ("payee.csv", ledger("B", 3, count=count))],
             scope_bank=None)
    source, job = add_source(client, case, document, "statement.txt")
    assert job["stage"] == "done", job
    api = f"/api/v1/cases/{case}"
    [attached] = client.get(f"{api}/imports/{source['import_id']}/intake").json()["items"]
    assert attached["role"] == "description" and attached["status"] == "registered"
    assert client.get(attached["original_url"]).content == document
    observations = client.get(api + "/flows", params={"granularity": "observations"}).json()
    anchor = next(row["id"] for row in observations["items"] if row["trade_no"] == "SYN-A1")
    opened = review(client, case, anchor, x=30)
    assert opened["review"]["pair"]["reason"] == "reference_reused"
    request = SimpleNamespace(anchor_id=anchor, canvas_id="0", review_token=opened["review"]["token"])
    offer = trial_pair_decision.prepare(case, request)
    assert offer and not offer["more_candidates"]
    if count == 2:
        assert offer["candidate_count"] == 2
    assert all(text not in json.dumps(offer) for text in (ISSUER, "SYN-A1", "SYN-B2"))
    return case, request, offer, source, job


def submit(client, case, request):
    response = client.post(f"/api/v1/cases/{case}/review-model-trial", json={**vars(request), "kind": "pair"})
    assert response.status_code == 200, response.text
    return response.json()


def mock_decision(client, monkeypatch, choice, witness):
    class Reply:
        def business_decision(self, _tag, _view):
            return {"choice": choice, "witness": witness}

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (PROVIDER,))
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda _: Reply())


def valid_claim(case, request, offer):
    [pair] = [(choice, witness) for choice in offer["choices"] for witness in offer["witnesses"]
              if trial_pair_decision.verify(case, request, offer["scope_sha256"], choice, (witness,))]
    return pair


def test_explicit_decision_changes_economic_events_with_readback_and_retraction(client, make_case, monkeypatch):
    case, request, offer, source, job = create_scope(client, make_case, monkeypatch, note())
    api = f"/api/v1/cases/{case}"
    unrelated = {"scope_id": "a" * 64, "lookup": {
        "kind": "name", "value": "Synthetic unrelated book", "start": "2026-01-01", "end": "2026-01-01"}}
    rejected = client.post(api + "/review-model-trial", json={
        **vars(request), "kind": "pair", "ledger_binding": unrelated})
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "invalid_trace_binding"
    assert event_resolution.summary(case)["confirmed_events"] == 0
    calls = []
    active_notices = trial_pair_decision._active_notices

    def counted(case_id):
        calls.append(case_id)
        return active_notices(case_id)

    with monkeypatch.context() as instrumentation:
        instrumentation.setattr(trial_pair_decision, "_active_notices", counted)
        claim = trial_pair_decision.explicit_claim(case, request, offer["scope_sha256"])
    assert calls == [case]
    assert claim == valid_claim(case, request, offer)
    assert offer["witnesses"][claim[1]]["source_row"] == 4
    assert event_resolution.summary(case)["confirmed_events"] == 0
    product = submit(client, case, request)
    assert (product["state"], product["first_calls"], product["recheck_calls"]) == ("rule", 0, 0)
    receipt = product["receipt"]
    assert (receipt["status"], receipt["observations"], receipt["amount"]) == ("accepted", 2, "7.20")
    assert event_resolution.summary(case)["confirmed_events"] == 2
    assert event_resolution.summary(case)["confirmed_amount_totals"] == [{"currency": "CNY", "amount": "14.40"}]
    observed = client.get(api + "/flows", params={"granularity": "observations"}).json()["items"]
    trades = {row["id"]: row["trade_no"] for row in observed}
    events = client.get(api + "/flows", params={"granularity": "events"}).json()["items"]
    assert {frozenset(trades[item] for item in event["observation_ids"]) for event in events} == {
        frozenset(("SYN-A1", "SYN-B2")), frozenset(("SYN-A2", "SYN-B1"))}
    assert sorted(row["balance"] for row in observed) == ["22.80", "27.20", "30.00", "34.40"]
    path = f"{api}/model-trial/receipts/{receipt['receipt_id']}"
    history = client.get(path).json()
    assert history["authority"] == "historical" and history["receipt"] == receipt
    assert {origin["source_row"] for origin in history["originals"] if origin["source_id"] == source["import_id"]} == {2, 4, 5}
    with db._lock:
        db._con.close()
        db._con = None
    assert client.get(path).json() == history
    reopened = review(client, case, request.anchor_id, x=30)
    assert reopened["review"]["pair"]["status"] == "confirmed"
    fresh = SimpleNamespace(anchor_id=request.anchor_id, canvas_id="0", review_token=reopened["review"]["token"])
    assert trial_pair_decision.prepare(case, fresh) is None
    withdraw(client, case, source["import_id"], job["result"]["batch_id"])
    assert review(client, case, request.anchor_id, x=30)["review"]["pair"]["reason"] == "reference_reused"
    assert client.get(api + "/flows", params={"granularity": "events"}).json()["total"] == 4
    assert client.get(path).json() == history


def test_explicit_single_correspondence_does_not_assign_other_postings(client, make_case, monkeypatch):
    case, request, offer, source, job = create_scope(client, make_case, monkeypatch, single_note(), count=3)
    api = f"/api/v1/cases/{case}"
    result = submit(client, case, request)
    assert (result["state"], result["first_calls"], result["recheck_calls"]) == ("rule", 0, 0)
    receipt = result["receipt"]
    assert (receipt["status"], receipt["observations"], receipt["amount"]) == ("accepted", 2, "7.20")
    assert offer["candidate_count"] == 3 and all(len(choice["pairs"]) == 1 for choice in offer["choices"].values())
    observed = client.get(api + "/flows", params={"granularity": "observations"}).json()["items"]
    by_trade = {row["trade_no"]: row for row in observed}
    payer, payee = (by_trade[trade] for trade in ("SYN-A1", "SYN-B2"))
    assert (payer["balance"], payee["balance"]) == ("30.00", "34.40")
    assert len(observed) == 6
    events = client.get(api + "/flows", params={"granularity": "events"}).json()["items"]
    assert len(events) == 5
    [proved] = [row for row in events if len(row["observation_ids"]) == 2]
    assert set(proved["observation_ids"]) == {payer["id"], payee["id"]}
    assert proved["amount"] == "7.20"
    other = [row for row in observed if row not in (payer, payee)]
    assert event_resolution.summary(case)["conflict_entries"] == 4

    for rows, count, total, unresolved in [
        ([payer], 1, "7.20", 0),
        ([payee], 1, "7.20", 0),
        (other, 5, "36.00", 4),
    ]:
        track(client, case, rows)
        canvas = graph(client, case)
        assert canvas["transaction_count"] == count
        assert canvas["edges"][0]["amount_totals"] == [{"currency": "CNY", "amount": total}]
        assert canvas["funds"]["confirmed_event_count"] == 1
        assert canvas["funds"]["unresolved_observation_count"] == unresolved

    history_path = f"{api}/model-trial/receipts/{receipt['receipt_id']}"
    history = client.get(history_path).json()
    assert history["receipt"] == receipt
    assert {row["source_row"] for row in history["originals"] if row["source_id"] == source["import_id"]} == {2, 4}
    with db._lock:
        db._con.close()
        db._con = None
    assert client.get(history_path).json() == history
    assert graph(client, case)["transaction_count"] == 5
    assert review(client, case, payee["id"], x=30)["review"]["pair"]["status"] == "confirmed"
    for row in other:
        assert review(client, case, row["id"], x=30)["review"]["pair"]["reason"] == "reference_reused"

    withdraw(client, case, source["import_id"], job["result"]["batch_id"])
    old = client.post(api + "/review-model-trial", json={**vars(request), "kind": "pair"})
    assert old.status_code == 409
    withdrawn = graph(client, case)
    assert withdrawn["transaction_count"] == 6
    assert withdrawn["funds"]["confirmed_event_count"] == 0
    assert withdrawn["edges"][0]["amount_totals"] == [{"currency": "CNY", "amount": "43.20"}]
    assert len(client.get(api + "/flows", params={"granularity": "observations"}).json()["items"]) == 6
    assert client.get(history_path).json() == history


@pytest.mark.parametrize("document", [single_note(scope="Synthetic other scope"), single_note(status="已冲正")],
                         ids=["other-business-scope", "reversed"])
def test_single_correspondence_with_wrong_scope_or_reversal_stays_pending(client, make_case, monkeypatch, document):
    case, request, _, _, _ = create_scope(client, make_case, monkeypatch, document, count=3)
    result = submit(client, case, request)
    assert result["receipt"]["status"] == "pending"
    assert event_resolution.summary(case)["confirmed_events"] == 0


def test_disjoint_single_notices_remain_independent_of_a_competing_binding(client, make_case, monkeypatch):
    case, request, offer, _, _ = create_scope(client, make_case, monkeypatch, single_note(), count=3)
    api = f"/api/v1/cases/{case}"
    selected, witness = valid_claim(case, request, offer)
    assert offer["witnesses"][witness]["source_row"] == 4
    wrong_line = next(key for key, row in offer["witnesses"].items() if row["source_row"] == 5)
    assert trial_pair_decision.verify(case, request, offer["scope_sha256"], selected, (wrong_line,)) is None
    assert submit(client, case, request)["receipt"]["status"] == "accepted"
    rows = client.get(api + "/flows", params={"granularity": "observations"}).json()["items"]
    ids = {row["trade_no"]: row["id"] for row in rows}
    _, unrelated = add_source(client, case, single_note("SYN-A2", "SYN-B1"), "second.txt")
    assert unrelated["stage"] == "done"
    assert event_resolution.summary(case)["confirmed_events"] == 1

    def opened(trade):
        anchor = ids[trade]
        token = review(client, case, anchor, x=30)["review"]["token"]
        return SimpleNamespace(anchor_id=anchor, canvas_id="0", review_token=token)

    second = submit(client, case, opened("SYN-A2"))
    assert second["receipt"]["status"] == "accepted"
    assert event_resolution.summary(case)["confirmed_events"] == 2
    assert submit(client, case, opened("SYN-A3"))["receipt"]["status"] == "pending"
    _, contrary = add_source(client, case, single_note("SYN-A1", "SYN-B3"), "contrary-single.txt")
    assert contrary["stage"] == "done"
    assert event_resolution.summary(case)["confirmed_events"] == 1
    assert submit(client, case, opened("SYN-A1"))["receipt"]["status"] == "pending"
    for trade in ("SYN-A2", "SYN-B1"):
        pair = review(client, case, ids[trade], x=30)["review"]["pair"]
        assert pair["status"] == "confirmed" and set(pair["candidate_ids"]) == {ids["SYN-A2"], ids["SYN-B1"]}


@pytest.mark.parametrize("corroboration_first", [True, False], ids=["before-review", "after-review"])
def test_consistent_single_corroboration_preserves_all_supported_pairs(client, make_case, monkeypatch, corroboration_first):
    case, request, _, complete, filed = create_scope(client, make_case, monkeypatch, note())
    if corroboration_first:
        source, corrob = add_source(client, case, single_note(), "corroboration.txt")
        assert corrob["stage"] == "done"
        request.review_token = review(client, case, request.anchor_id, x=30)["review"]["token"]
    result = submit(client, case, request)
    assert (result["state"], result["first_calls"], result["recheck_calls"]) == ("rule", 0, 0)
    assert result["receipt"]["status"] == "accepted"
    if not corroboration_first:
        source, corrob = add_source(client, case, single_note(), "corroboration.txt")
        assert corrob["stage"] == "done"

    api = f"/api/v1/cases/{case}"
    history_path = f"{api}/model-trial/receipts/{result['receipt']['receipt_id']}"
    historical = client.get(history_path).raise_for_status().json()
    observed = client.get(api + "/flows", params={"granularity": "observations"}).json()["items"]
    assert len(observed) == 4
    assert sorted(row["balance"] for row in observed) == ["22.80", "27.20", "30.00", "34.40"]
    by_id = {row["id"]: row["trade_no"] for row in observed}

    def confirmed_pairs():
        events = client.get(api + "/flows", params={"granularity": "events"}).json()["items"]
        return {frozenset(by_id[key] for key in event["observation_ids"]) for event in events
                if len(event["observation_ids"]) == 2}

    primary = frozenset(("SYN-A1", "SYN-B2"))
    other = frozenset(("SYN-A2", "SYN-B1"))
    assert confirmed_pairs() == {primary, other}
    track(client, case, observed)
    canvas = graph(client, case)
    assert canvas["transaction_count"] == 2 and canvas["edges"][0]["total"] == "14.40"
    assert canvas["funds"]["confirmed_event_count"] == 2 and canvas["funds"]["unresolved_observation_count"] == 0
    with db._lock:
        db._con.close()
        db._con = None
    assert graph(client, case)["edges"][0]["total"] == "14.40"
    assert confirmed_pairs() == {primary, other}
    assert client.get(history_path).json() == historical

    withdraw(client, case, complete["import_id"], filed["result"]["batch_id"])
    assert confirmed_pairs() == {primary}
    assert review(client, case, request.anchor_id, x=30)["review"]["pair"]["proof"]["witness"]["source_id"] == source["import_id"]
    kept = graph(client, case)
    assert kept["transaction_count"] == 3 and kept["funds"]["unresolved_observation_count"] == 2
    withdraw(client, case, source["import_id"], corrob["result"]["batch_id"])
    assert confirmed_pairs() == set() and graph(client, case)["transaction_count"] == 4
    assert client.get(api + "/flows", params={"granularity": "observations"}).json()["total"] == 4
    assert client.get(history_path).json() == historical


def test_complete_original_can_extend_a_previously_confirmed_pair_without_recounting_it(client, make_case, monkeypatch):
    case, request, _, _, _ = create_scope(client, make_case, monkeypatch, single_note())
    first = submit(client, case, request)
    assert first["receipt"]["status"] == "accepted"
    source, added = add_source(client, case, note(), "complete.txt")
    assert added["stage"] == "done"
    assert event_resolution.summary(case)["confirmed_events"] == 1

    api = f"/api/v1/cases/{case}"
    items = client.get(api + "/flows", params={"granularity": "observations"}).json()["items"]
    peer = next(row["id"] for row in items if row["trade_no"] == "SYN-A2")
    opened = review(client, case, peer, x=30)
    remaining = SimpleNamespace(anchor_id=peer, canvas_id="0", review_token=opened["review"]["token"])
    result = submit(client, case, remaining)
    assert result["receipt"]["status"] == "accepted"
    assert first["receipt"]["event_id"] in result["packet"]["facts"]["event_ids"]
    assert event_resolution.summary(case)["confirmed_events"] == 2
    assert event_resolution.summary(case)["confirmed_amount_totals"] == [{"currency": "CNY", "amount": "14.40"}]
    withdraw(client, case, source["import_id"], added["result"]["batch_id"])
    assert event_resolution.summary(case)["confirmed_events"] == 1
    assert review(client, case, request.anchor_id, x=30)["anchor"]["event_id"] == first["receipt"]["event_id"]


@pytest.mark.parametrize("counterevidence_first", [True, False], ids=["before-approval", "during-replay"])
def test_scoped_reversal_blocks_only_its_pair(client, make_case, monkeypatch, counterevidence_first):
    case, request, _, _, _ = create_scope(client, make_case, monkeypatch, note())
    if not counterevidence_first:
        assert submit(client, case, request)["receipt"]["status"] == "accepted"
    _, result = add_source(client, case, single_note(status="已冲正"), "scoped-reversal.txt")
    assert result["stage"] == "done"
    api = f"/api/v1/cases/{case}"
    observations = client.get(api + "/flows", params={"granularity": "observations"}).json()["items"]
    ids = {row["trade_no"]: row["id"] for row in observations}
    if counterevidence_first:
        unaffected = ids["SYN-A2"]
        current = SimpleNamespace(anchor_id=unaffected, canvas_id="0",
                                  review_token=review(client, case, unaffected, x=30)["review"]["token"])
        assert submit(client, case, current)["receipt"]["status"] == "accepted"
    assert event_resolution.summary(case)["confirmed_events"] == 1
    assert review(client, case, ids["SYN-A1"], x=30)["review"]["pair"]["status"] == "conflict"
    continued = review(client, case, ids["SYN-A2"], x=30)["review"]["pair"]
    assert continued["status"] == "confirmed" and set(continued["candidate_ids"]) == {ids["SYN-A2"], ids["SYN-B1"]}


def test_model_path_rejects_swap_and_adopts_only_verified_citation_in_controlled_ablation(client, make_case, monkeypatch):
    case, request, offer, _, _ = create_scope(client, make_case, monkeypatch, note())
    choice, witness = trial_pair_decision.explicit_claim(case, request, offer["scope_sha256"])
    # Disable only direct selection for this test of the unchanged model adoption gate.
    monkeypatch.setattr(trial_pair_decision, "explicit_claim", lambda *_: None)
    wrong = next(option for option in offer["choices"] if option != choice)
    mock_decision(client, monkeypatch, wrong, witness)
    assert submit(client, case, request)["receipt"]["status"] == "pending"
    assert event_resolution.summary(case)["confirmed_events"] == 0
    mock_decision(client, monkeypatch, choice, witness)
    result = submit(client, case, request)
    assert (result["state"], result["first_calls"], result["recheck_calls"]) == ("proposal", 2, 0)
    assert result["receipt"]["status"] == "accepted"
    assert event_resolution.summary(case)["confirmed_events"] == 2


def test_explicit_rule_requires_consensus_across_active_same_scope_notices(client, make_case, monkeypatch):
    case, request, _, _, _ = create_scope(client, make_case, monkeypatch, note())
    contrary = note().replace(b"SYN-B1", b"SYN-TMP").replace(b"SYN-B2", b"SYN-B1").replace(b"SYN-TMP", b"SYN-B2")
    source, job = add_source(client, case, contrary, "contrary.txt")
    assert job["stage"] == "done", job
    [part] = client.get(f"/api/v1/cases/{case}/imports/{source['import_id']}/intake").json()["items"]
    assert part["role"] == "description" and part["status"] == "registered"
    opened = review(client, case, request.anchor_id, x=30)
    current = SimpleNamespace(anchor_id=request.anchor_id, canvas_id="0", review_token=opened["review"]["token"])
    offer = trial_pair_decision.prepare(case, current)
    assert offer and trial_pair_decision.explicit_claim(case, current, offer["scope_sha256"]) is None
    result = submit(client, case, current)
    assert result["receipt"]["status"] == "pending" and result["first_calls"] == 0
    assert event_resolution.summary(case)["confirmed_events"] == 0


@pytest.mark.parametrize("document", [note(issuer="Synthetic other correspondent"), note(ambiguous=True)])
def test_wrong_scope_and_exchangeable_pairings_remain_pending(client, make_case, monkeypatch, document):
    case, request, offer, _, _ = create_scope(client, make_case, monkeypatch, document)
    assert trial_pair_decision.explicit_claim(case, request, offer["scope_sha256"]) is None
    choice = next(iter(offer["choices"]))
    witness = next(key for key, row in offer["witnesses"].items() if row["source_row"] == 4)
    mock_decision(client, monkeypatch, choice, witness)
    assert submit(client, case, request)["receipt"]["status"] == "pending"
    assert event_resolution.summary(case)["confirmed_events"] == 0


def test_disagreement_without_applicable_original_never_triggers_review(client, make_case, monkeypatch):
    case, request, offer, _, _ = create_scope(client, make_case, monkeypatch, note(ambiguous=True))
    choices = iter(offer["choices"])
    witness = next(key for key, row in offer["witnesses"].items() if row["source_row"] == 4)

    class Reply:
        def business_decision(self, _tag, _view):
            return {"choice": next(choices), "witness": witness}

        def arbitrate_business(self, _view, _first):
            pytest.fail("Disagreement without source support should stay pending")

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (PROVIDER,))
    monkeypatch.setattr(desktop_model_trial, "_LoopbackProvider", lambda _: Reply())
    result = submit(client, case, request)
    assert (result["state"], result["reason"], result["first_calls"], result["recheck_calls"]) == (
        "pending", "pair_evidence_missing", 2, 0)
    assert result["receipt"]["status"] == "pending"
    assert event_resolution.summary(case)["confirmed_events"] == 0


def test_pair_event_rolls_back_if_historical_publication_fails(client, make_case, monkeypatch):
    case, request, offer, _, _ = create_scope(client, make_case, monkeypatch, note())
    choice, witness = valid_claim(case, request, offer)
    old = review(client, case, request.anchor_id, x=30)["analysis_revision"]

    def fail(_proof):
        assert event_resolution.summary(case)["confirmed_events"] == 2
        raise RuntimeError("synthetic receipt failure")

    with pytest.raises(RuntimeError, match="synthetic receipt failure"):
        trial_pair_decision.adopt(case, request, offer["scope_sha256"], choice, (witness,), on_accept=fail)
    assert event_resolution.summary(case)["confirmed_events"] == 0
    assert review(client, case, request.anchor_id, x=30)["analysis_revision"] == old


def test_new_competing_original_invalidates_live_group_but_preserves_history(client, make_case, monkeypatch):
    case, request, _, _, _ = create_scope(client, make_case, monkeypatch, note())
    result = submit(client, case, request)
    receipt = result["receipt"]
    assert result["state"] == "rule" and receipt["status"] == "accepted"
    history_path = f"/api/v1/cases/{case}/model-trial/receipts/{receipt['receipt_id']}"
    history = client.get(history_path).json()
    third = transaction("SYN-A", **{
        "交易流水号": "SYN-A3", "交易时间": "2026-01-01 10:02:00", "参考号机构": ISSUER,
        "参考号渠道": CHANNEL, "参考号业务域": SCOPE, "交易金额": "7.20",
        "本笔交易后账面余额": "15.60", "记账顺序号": "3"})
    _, job = add_source(client, case, csv_rows([third]), "additional-payer.csv")
    assert job["stage"] == "done" and job["result"]["ok"] == 1
    assert event_resolution.summary(case)["confirmed_events"] == 0
    assert review(client, case, request.anchor_id, x=30)["review"]["pair"]["reason"] == "reference_reused"
    assert client.get(history_path).json() == history
