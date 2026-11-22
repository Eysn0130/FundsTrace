"""Public review reads and case/canvas/revision-bound manual graph writes."""
from test_event_graph import imported, pair


def review(client, case, anchor, canvas=0, x=1):
    response = client.get(f"/api/context/{anchor}", params={
        "case_id": case, "canvas_id": canvas, "x": x, "review": "true",
    })
    assert response.status_code == 200, response.text
    return response.json()


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
    assert row["review_fields"]["counter_kind"] == "account_or_card"
    assert row["review_fields"]["counter_card"] == ""
    assert row["review_fields"]["counter_account"] == "SYN-SINGLE-B"
    assert row["review_fields"]["remark"] == "合成备注与摘要分开"
    assert row["summary"] == "合成摘要"


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
               "analysis_revision": current["analysis_revision"]}
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
    stale = client.post("/api/context/summary", json={"case_id": cid, "flow_ids": []}, headers=headers)
    assert stale.status_code == 409
