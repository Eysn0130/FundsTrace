"""The public review uses source-backed intervals and keeps read-only evidence separate."""
from itertools import product

import pytest

from app import db
from app.services import trace_group_evidence
from app.services.trace_group_evidence import _joint_share
from test_automatic_table_import import preview
from test_feedback_import_api import complete
from test_graph_owner_lifecycle import table_bytes
from test_principal_book_model import book_row, import_books
from test_reference_roles import payload
from test_table_mapping_versions import profile_preview
from test_trace_review import review


def evaluate_book(cid, ids):
    rows = db.q("""select * from effective_flows where case_id=? and id in (select unnest(?::bigint[]))""", [cid, ids])
    account = rows[0]["self_account"]
    incoming = [row for row in rows if row["payee_account"] == account]
    outgoing = [row for row in rows if row["payer_account"] == account]
    return trace_group_evidence.evidence(account, incoming, outgoing, trace_group_evidence.prepare(cid, account, rows))


@pytest.mark.parametrize("scenario", ["proved", "reversed", "other_source", "wrong_header", "changed_original"])
def test_v69_uniform35_reviews_the_observed_own_side_without_inventing_a_peer(client, make_case, monkeypatch, scenario):
    from app.services import imports
    from test_uniform35_reference import COLUMNS, row, workbook

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case("Synthetic uniform35 posting directions")
    base = f"/api/v1/cases/{case}"
    contents = []
    for reference, stamp, direction in (("DEBIT", "20460101080100", "出"), ("CREDIT", "20460101080500", "进")):
        contents.append(row(**{"本方账号": "SYN-OWN", "本方卡号": "", "查询账号": "SYN-OTHER-QUERY",
                               "交易对方账号": "", "交易对方卡号": "", "交易对方名称": "",
                               "交易时间": stamp, "借贷标志": direction, "交易流水号": reference,
                               "凭证号": reference, "交易是否成功": "成功"}))
    headers = tuple("其他日记" if col == "日志号" else col for col in COLUMNS) if scenario == "wrong_header" else COLUMNS
    sources = [[record] for record in contents] if scenario == "other_source" else [contents]
    jobs = []
    for source in sources:
        raw = workbook(source, headers=headers, stale=True, padding=("", ""))
        view = preview(client, case, raw, "source.xlsx")
        assert view["table_summary"]["profile"] == "funds-trace-table-v69"
        job = complete(client, case, view)
        assert job["stage"] == "done" and job["result"]["ok"] == len(source), job
        jobs.append((view["import_id"], job["result"]["batch_id"], raw))
    flows = {row["trade_no"]: row for row in client.get(base + "/flows?granularity=observations").json()["items"]}
    assert flows["CREDIT"]["raw_payer_account"] == flows["DEBIT"]["raw_payee_account"] == ""
    credit, debit = (flows[name]["id"] for name in ("CREDIT", "DEBIT"))
    if scenario == "reversed":
        credit, debit = debit, credit
    token = review(client, case, credit, x=20)["review"]["token"]
    current = {"kind": "trace_target", "anchor_id": credit, "canvas_id": "0", "review_token": token,
               "source_flow_ids": [credit], "target_flow_ids": [debit]}
    if scenario == "changed_original":
        source_id, _, raw = jobs[0]
        imports._path(source_id, ".xlsx").write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    response = client.post(base + "/review-model-trial", json=current)
    if scenario != "proved":
        assert (response.status_code, response.json()["error"]["code"]) == (422, "assistance_wrong_ledger_direction"), response.text
        return

    assert response.status_code == 200, response.text
    result = response.json()
    facts = result["packet"]["facts"]
    assert facts["checks"]["ledger_roles"] and "selected_debits_precede_all_credits" in facts["reasons"]
    assert facts["conditional_group_share"] is None and result["receipt"]["status"] == "pending"
    assert result["first_calls"] == result["recheck_calls"] == 0
    reverse = review(client, case, debit, x=20)["review"]["token"]
    other = client.post(base + "/review-model-trial", json={
        **current, "kind": "trace_source", "anchor_id": debit, "review_token": reverse,
    })
    assert other.status_code == 200 and other.json()["packet"]["facts"]["reasons"] == facts["reasons"]
    source_id, batch, raw = jobs[0]
    assert client.get(base + f"/sources/{source_id}/file").content == raw
    assert client.delete(base + f"/sources/{source_id}/operation", params={"expected_batch_id": batch}).status_code == 200
    assert client.post(base + "/review-model-trial", json=current).status_code == 409


def test_unallocated_later_target_original_explains_the_principal_boundary(
        client, make_case, monkeypatch):
    from app.services import imports
    from test_event_graph import graph, track
    from test_event_import import csv_rows

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    cid = make_case("Synthetic limited target principal review")
    canvas = client.post("/api/canvases/save", json={
        "case_id": cid, "name": "Synthetic principal canvas", "snapshot": {},
    }).raise_for_status().json()["canvas_id"]
    rows = [
        book_row("B", "SYN-INITIAL-PAYER", "B", "40.00", "40.00", 0, 1, "ANCHOR"),
        book_row("B", "B", "SYN-FUNDED", "40.00", "0.00", 1, 2, "FUNDED"),
        book_row("B", "SYN-NEXT-PAYER", "B", "1000.00", "1000.00", 2, 3, "OTHER-CREDIT"),
    ]
    for index in range(21):
        balance = f"{1000 - 10 * (index + 1)}.00" if index < 11 else ""
        rows.append(book_row("B", "B", f"SYN-RECIPIENT-{index}", "10.00",
                             balance, index + 3, index + 4, f"TARGET-{index}"))
    raw = csv_rows(rows)
    view = preview(client, cid, raw)
    job = complete(client, cid, view)
    assert job["stage"] == "done" and job["result"]["ok"] == len(rows)
    base = f"/api/v1/cases/{cid}"
    flows = client.get(base + "/flows?granularity=observations").raise_for_status().json()["items"]
    positions = {row["trade_no"]: row for row in flows}
    anchor, funded, other, zero, hidden, pending = (
        positions[f"SYN-B-{key}"] for key in ("ANCHOR", "FUNDED", "OTHER-CREDIT", "TARGET-0", "TARGET-10", "TARGET-11")
    )

    opened = review(client, cid, anchor["id"], canvas=canvas, x=50)
    token = opened["review"]["token"]
    limited = opened["review"]["principal"]["target"]
    assert limited["status"] == "limited" and not limited["targets"]
    assert limited["assessment"][0]["kind"] == "resource_limit"
    roles = {row["id"]: row["review_roles"] for row in opened["rows"]}
    assert roles[pending["id"]] == roles[zero["id"]] == roles[hidden["id"]] == ["target"]
    assert roles[other["id"]] == []
    clues = {item["ids"][0]: item["evidence"]["conditional_group_share"] for item in opened["suggestions"]["targets"]}
    assert clues[funded["id"]] == {"min": "40.00", "max": "40.00"}
    assert clues[zero["id"]] == {"min": "0.00", "max": "0.00"}
    assert hidden["id"] not in clues
    assert evaluate_book(cid, [int(anchor["id"]), int(hidden["id"])])["conditional_group_share"] == {
        "min": "0.00", "max": "0.00"}

    message = "该原行可以回查已记录的往来。先后顺序不足以确定本金归属，本次未核定资金份额。"

    def original(row, review_token=token, *, anchor_id=anchor["id"], canvas_id=canvas):
        return client.get(f"/api/context/{anchor_id}/record/{row['id']}",
                          params={"case_id": cid, "canvas_id": canvas_id},
                          headers={"X-FundsTrace-Review": review_token})

    card = original(pending).raise_for_status().json()
    assert card["message"] == message
    [witness] = card["originals"]
    assert witness["source_row"] == 16
    assert witness["cells"][0]["value"] == "SYN-B-TARGET-11"
    for excluded in (anchor, funded, other, zero, hidden):
        assert message not in original(excluded).raise_for_status().json()["message"]
    assert original(pending, canvas_id=0).status_code == 409
    assert original(pending, anchor_id=other["id"]).status_code == 409

    # The default view and the unanchored edge do not issue this finding.
    with monkeypatch.context() as normal:
        normal.setattr(client.app.state, "desktop_model_trial", None)
        default_token = review(client, cid, anchor["id"], canvas=canvas, x=50)["review"]["token"]
        assert message not in original(pending).raise_for_status().json()["message"]
    assert message not in original(pending, default_token).raise_for_status().json()["message"]
    track(client, cid, [anchor])
    current = graph(client, cid)
    [edge] = current["edges"]
    edge_view = client.post("/api/context/edge", json={
        "case_id": cid, "source": edge["source"], "target": edge["target"], "forward_ids": edge["ids"],
        "analysis_revision": current["analysis_revision"], "membership_digest": current["membership_digest"],
    }).raise_for_status().json()
    assert message not in original(pending, edge_view["review"]["token"], anchor_id=0,
                                   canvas_id=0).raise_for_status().json()["message"]

    # Source bytes and the active revision are still required after issuing the review.
    source_id = view["import_id"]
    original_path = imports._path(source_id, ".csv")
    original_path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    unavailable = original(pending).raise_for_status().json()
    assert message not in unavailable["message"] and unavailable["originals"][0]["cells"] == []
    original_path.write_bytes(raw)
    assert client.delete(base + f"/sources/{source_id}/operation",
                         params={"expected_batch_id": job["result"]["batch_id"]}).status_code == 200
    assert original(pending).status_code == 409
    assert client.get(base + "/flows").raise_for_status().json()["total"] == 0


@pytest.mark.parametrize("credits,debits,pattern", [
    ([100], [90], "整进整出"),
    ([100], [40, 50], "整进散出"),
    ([40, 60], [90], "散进整出"),
    ([40, 60], [40, 50], "散进散出"),
])
def test_existing_source_card_uses_same_book_and_untracked_other_entries(client, database, make_case, credits, debits, pattern):
    from test_event_graph import graph, track

    cid = make_case("Synthetic group review")
    balance, rows, names, mirrors = 40, [], [], []

    def append(direction, amount, key, selected):
        nonlocal balance
        balance += amount if direction == "in" else -amount
        payer, payee = ("SYN-PREV", "B") if direction == "in" else ("B", "SYN-NEXT")
        if not selected:
            payer, payee = ("SYN-COMPETING-PAYER", "B") if direction == "in" else ("B", "SYN-COMPETING-PAYEE")
        rows.append(book_row("B", payer, payee, f"{amount}.00", f"{balance}.00", len(rows), len(rows) + 1, key))
        if selected:
            names.append(f"SYN-B-{key}")

    for i, value in enumerate(credits):
        append("in", value, f"CREDIT{i}", True)
    append("in", 20, "OTHER-CREDIT", False)
    append("out", 50, "OTHER-DEBIT", False)
    received = 0
    for i, value in enumerate(debits):
        received += value
        mirrors.append(book_row("SYN-NEXT", "B", "SYN-NEXT", f"{value}.00", f"{received}.00",
                                len(rows), i + 1, f"DEBIT{i}"))
        append("out", value, f"DEBIT{i}", True)
    observed = import_books(client, cid, rows + mirrors)
    debits_tracked = [observed[f"SYN-B-DEBIT{i}"] for i in range(len(debits))]
    track(client, cid, debits_tracked)
    current = graph(client, cid)
    edge, = current["edges"]
    response = client.post("/api/context/edge", json={
        "case_id": cid, "source": edge["source"], "target": edge["target"], "forward_ids": edge["ids"],
        "analysis_revision": current["analysis_revision"], "membership_digest": current["membership_digest"],
    })
    assert response.status_code == 200, response.text
    ids = {observed[name]["id"] for name in names[:len(credits)]}
    clue, = [item for item in response.json()["suggestions"]["sources"] if set(item["ids"]) == ids]
    assert set(clue["anchor_ids"]) == {row["id"] for row in debits_tracked}
    data = clue["evidence"]
    assert data["pattern"] == pattern
    assert (data["observed_in"], data["observed_out"], data["opening_balance"], data["closing_balance"]) == (
        "100.00", "90.00", "40.00", "20.00")
    assert data["other_entries"] == {"in": {"count": 1, "amount": "20.00"}, "out": {"count": 1, "amount": "50.00"}}
    assert data["conditional_group_share"] == {"min": "30.00", "max": "90.00"}
    assert data["attribution_status"] == "unknown" and data["checks"]["own_balance"]
    assert "30.00–90.00" in data["judgment"] and data["judgment"].count("。") == 1
    assert all(item["source_row"] for item in data["locations"])
    assert database.q_raw("select count(*) n from tracked_flows where case_id=?", [cid])[0]["n"] == len(debits)


def test_voucher_collision_is_observed_without_merged_or_more_permissive_tracking(client, make_case):
    cid = make_case("Synthetic voucher collision")
    left = payload()
    right = payload(changes={"交易流水号": "SYN-OTHER", "交易金额": "10.56", "交易时间": "2026-01-01 10:04:00"}, missing_peer=True)
    raw = left + right.splitlines(keepends=True)[1]
    assert complete(client, cid, preview(client, cid, raw, "synthetic-vouchers.csv"))["stage"] == "done"
    data = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    ids = {row["id"] for row in data["items"]}
    assert len(ids) == 2
    anchor, counter = sorted(ids)
    value = review(client, cid, anchor)
    proof = value["review"]["pair"]
    assert proof["status"] == "unknown" and proof["reason"] == "voucher_candidate_only"
    assert proof["voucher_candidate_ids"] == [counter]
    assert value["review"]["directional_funds"]["forward"]["count"] == {"lower": 0, "upper": 1}


@pytest.mark.parametrize("placeholder", ["", "0", "--"])
def test_voucher_placeholders_with_source_tabs_do_not_find_a_candidate(client, make_case, placeholder):
    cid = make_case("Synthetic missing voucher")
    left = payload(changes={"凭证号": placeholder})
    right = payload(changes={"凭证号": placeholder, "交易流水号": "SYN-OTHER",
                             "交易金额": "15.29", "交易时间": "2026-01-01 10:04:00"})
    raw = left + right.splitlines(keepends=True)[1]
    assert complete(client, cid, preview(client, cid, raw, "synthetic-empty-vouchers.csv"))["stage"] == "done"
    rows = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()["items"]
    value = review(client, cid, rows[0]["id"])
    assert value["review"]["pair"]["candidate_ids"] == []


@pytest.mark.parametrize("complication,reason", [
    ("mixed", "mixed_currency"), ("future", "selected_debits_precede_all_credits"),
    ("boundary-date", "boundary_date_precision_unknown"),
])
def test_evidence_reports_conflicts_and_date_only_boundary(client, make_case, complication, reason):
    cid = make_case("Synthetic conservative group")
    credit_minute, debit_minute = (3, 1) if complication == "future" else (1, 3)
    credit = book_row("B", "A", "B", "20.00", "70.00", credit_minute, 2, "CREDIT")
    debit = book_row("B", "B", "C", "10.00", "60.00", debit_minute, 3, "DEBIT")
    rows = [credit, debit]
    if complication == "mixed":
        credit["交易币种"] = "USD"
    if complication == "boundary-date":
        boundary = book_row("B", "B", "D", "5.00", "50.00", 0, 1, "BOUNDARY-OTHER")
        boundary["交易时间"] = "2026-01-01"
        rows.append(boundary)
    observed = import_books(client, cid, rows)
    ids = [observed[key]["id"] for key in ("SYN-B-CREDIT", "SYN-B-DEBIT")]
    details = evaluate_book(cid, ids)
    assert reason in details["reasons"]
    assert not details["checks"]["order"] or complication == "mixed"
    if complication != "boundary-date":
        assert details["score"] is None
    else:
        assert not details["checks"]["interval_records"] and details["conditional_group_share"] is None


@pytest.mark.parametrize("competing_balances", [("", ""), ("200.00", "100.00")])
def test_end_day_date_only_models_no_range_with_other_same_day_entries(client, make_case, competing_balances):
    cid = make_case("Synthetic uncertain end date")
    credit = book_row("B", "A", "B", "100.00", "100.00", 1, 1, "CREDIT")
    debit = book_row("B", "B", "C", "100.00", "0.00", 0, 2, "DEBIT")
    debit["交易时间"] = "2026-01-02"
    other_in = book_row("B", "D", "B", "100.00", competing_balances[0], 1, 3, "OTHER-IN")
    other_out = book_row("B", "B", "E", "100.00", competing_balances[1], 2, 4, "OTHER-OUT")
    other_in["交易时间"], other_out["交易时间"] = "2026-01-02 09:00:00", "2026-01-02 10:00:00"
    observed = import_books(client, cid, [credit, debit, other_in, other_out])
    ids = [observed[key]["id"] for key in ("SYN-B-CREDIT", "SYN-B-DEBIT")]
    details = evaluate_book(cid, ids)
    assert "boundary_date_precision_unknown" in details["reasons"]
    assert details["conditional_group_share"] is None and not details["checks"]["interval_records"]
    legacy = review(client, cid, ids[1])["review"]["principal"]["source"]
    assert legacy["status"] == "unknown" and legacy["source_candidates"]
    assert all(not item["targets"] for item in legacy["source_candidates"])
    assert "boundary_date_precision_unknown" in legacy["source_candidates"][0]["reasons"]


@pytest.mark.parametrize("competitor", ["inside", "date_only_competitor", "uncertain_end", "outside"])
def test_known_default_currency_competitor_is_scoped_to_its_possible_time(client, database, make_case, competitor):
    cid = make_case("Synthetic uncertain currency in the book")
    credit = book_row("B", "A", "B", "100.00", "100.00", 1, 1, "CREDIT")
    debit = book_row("B", "B", "C", "100.00", "0.00", 3, 3, "DEBIT")
    credit["交易币种"] = debit["交易币种"] = "USD"
    assumed = book_row("B", "D", "B", "100.00", "100.00", 2, 2, "ASSUMED")
    if competitor == "date_only_competitor":
        assumed["交易时间"] = "2026-01-01"
    elif competitor == "uncertain_end":
        debit["交易时间"] = "2026-01-02"
        assumed["交易时间"] = "2026-01-02 09:00:00"
    elif competitor == "outside":
        assumed["交易时间"] = "2026-01-01 17:00:00"
    observed = import_books(client, cid, [credit, assumed, debit])
    # This is the historical fallback marker, paired with a stored CNY
    # observation. It is independent of the USD filter of the review.
    database.x("""update import_records r set details_json=json_merge_patch(r.details_json,?)
        where r.case_id=? and exists (select 1 from flow_sources f
          where f.case_id=r.case_id and f.source_id=r.source_id and f.source_row=r.record_no and f.flow_id=?)""",
        ['{"currency_basis":"沿用普通表格历史默认币种CNY；来源未提供币种"}', cid, observed["SYN-B-ASSUMED"]["id"]])
    ids = [observed[key]["id"] for key in ("SYN-B-CREDIT", "SYN-B-DEBIT")]
    view = review(client, cid, ids[1])
    item, = view["suggestions"]["sources"]
    details = item["evidence"]
    principal = view["review"]["principal"]["source"]
    assert principal["source_candidates"]
    if competitor == "outside":
        assert "source_currency_unproved" not in details["reasons"]
        assert details["checks"]["interval_records"]
        assert details["conditional_group_share"] == {"min": "100.00", "max": "100.00"}
        assert principal["status"] == "ok"
    else:
        assert "source_currency_unproved" in details["reasons"]
        assert details["conditional_group_share"] is None and not details["checks"]["interval_records"]
        assert principal["status"] == "unknown"
        assert all(not candidate["targets"] for candidate in principal["source_candidates"])


def test_historical_default_currency_has_no_evidence_weight_and_can_be_withdrawn(client, database, make_case, monkeypatch):
    cid = make_case("Synthetic historical currency evidence")
    old = "funds-trace-table-v17"
    source = profile_preview(client, cid, table_bytes([
        ["2026-01-01 10:01:00", "SYN-A", "SYN-B", "100.00", "", "SYN-B", "SYN-OLD-CREDIT"],
    ]), monkeypatch, old)
    saved = complete(client, cid, source)
    assert saved["stage"] == "done" and saved["result"]["ok"] == 1
    target = profile_preview(client, cid, table_bytes([
        ["2026-01-01 10:03:00", "SYN-B", "SYN-C", "100.00", "CNY", "SYN-B", "SYN-OLD-DEBIT"],
    ]), monkeypatch, old)
    assert complete(client, cid, target)["stage"] == "done"
    flows = client.get(f"/api/v1/cases/{cid}/flows").json()["items"]
    anchor = next(row["id"] for row in flows if row["trade_no"] == "SYN-OLD-DEBIT")
    credit = next(row for row in flows if row["trade_no"] == "SYN-OLD-CREDIT")
    assert credit["currency"] == "CNY"
    assert not database.q_raw("select 1 from evidence_sources where case_id=? and source_id=?", [cid, source["import_id"]])
    original = client.get(f"/api/v1/cases/{cid}/imports/{source['import_id']}/records").json()["items"][0]
    assert "来源未提供币种" in original["details"]["currency_basis"]
    view = review(client, cid, anchor)
    candidate, = view["suggestions"]["sources"]
    assert candidate["ids"] == [credit["id"]]
    assert candidate["total"] is None and not candidate["evidence"]["checks"]["currency_amount"]
    assert "source_currency_unproved" in candidate["evidence"]["reasons"]

    removed = client.delete(f"/api/batches/{saved['result']['batch_id']}", params={"case_id": cid})
    assert removed.status_code == 200, removed.text
    assert review(client, cid, anchor)["suggestions"]["sources"] == []


def test_joint_share_matches_independent_exhaustive_unit_allocation():
    # Inbound group and other credits, interleaved targets and other debits.
    for sequence in product(("source", "credit", "target", "debit"), repeat=5):
        if "source" not in sequence or "target" not in sequence:
            continue
        rows, states = [], {(0, 2, 0)}
        for i, role in enumerate(sequence):
            direction = "in" if role in {"source", "credit"} else "out"
            rows.append({"id": str(i), "direction": direction, "amount": 1})
            if direction == "in":
                states = {(a + (role == "source"), b + (role == "credit"), value) for a, b, value in states}
            else:
                states = {(a - spend, b - 1 + spend, value + (spend if role == "target" else 0))
                          for a, b, value in states for spend in (0, 1) if spend <= a and 1 - spend <= b}
        if not states:
            continue
        sources = {str(i) for i, role in enumerate(sequence) if role == "source"}
        targets = {str(i) for i, role in enumerate(sequence) if role == "target"}
        assert _joint_share(rows, 2, sources, targets, maximize=False) == min(state[2] for state in states)
        assert _joint_share(rows, 2, sources, targets, maximize=True) == max(state[2] for state in states)
