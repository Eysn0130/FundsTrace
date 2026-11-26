"""Dated registration evidence feeds a current, case-bound trace receipt."""
from test_api_v1 import poll
from test_principal_book_model import book_row, import_books


REGISTRATION = "登记主体标识,开户名称,开户账号,卡号,关系有效起日,关系有效止日,签发方\n"
QUERY = {"kind": "card", "value": "00000001", "start": "2026-01-01", "end": "2026-01-01"}


def _register(client, case, claims):
    base = f"/api/v1/cases/{case}/imports"
    raw = (REGISTRATION + "\n".join(claims) + "\n").encode()
    response = client.post(base + "/preview", files={"file": ("dated-accounts.csv", raw)})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view["table_summary"]["accepted"] == 0
    assert view["table_summary"]["auxiliary"] == len(claims)
    body = {key: view[key] for key in ("mode", "mapping")}
    start = client.post(f"{base}/{view['import_id']}/commit", json={**body, "mark_accounts": []})
    assert start.status_code == 200, start.text
    return poll(client, case, view["import_id"])["result"]


def test_dated_card_lookup_binds_raw_registration_and_then_existing_trace(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    rows = [
        book_row("B", "EXT", "B", "100.00", "140.00", 0, 1, "SOURCE"),
        book_row("B", "B", "D", "50.00", "90.00", 1, 2, "MIX-OUT"),
        book_row("B", "E", "B", "20.00", "110.00", 2, 3, "MIX-IN"),
        book_row("B", "B", "C", "90.00", "20.00", 3, 4, "TARGET"),
    ]
    bank = rows[0]["本方开户银行"]
    flows = import_books(client, case, rows)
    stored = _register(client, case, [f"SYN-HOLDER,同名合成户,B,00000001,2026-01-01,2026-12-31,{bank}"])
    base = f"/api/v1/cases/{case}"
    result = client.post(base + "/trace-ledgers", json=QUERY)
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["status"] == "found" and value["coverage_complete"]
    [choice] = value["choices"]
    assert choice["ledger_id"] == flows["SYN-B-SOURCE"]["self_account"] and len(choice["rows"]) == 4
    [original] = choice["proofs"]
    assert original["source_row"] == 2
    ledger = flows["SYN-B-TARGET"]["id"]
    opened = client.get(f"/api/context/{ledger}", params={"case_id": case, "review": "true", "x": 10}).json()
    body = {"kind": "trace_source", "anchor_id": ledger, "canvas_id": "0", "group_flow_id": flows["SYN-B-SOURCE"]["id"],
            "ledger_binding": {"lookup": QUERY, "scope_id": choice["scope_id"]}, "review_token": opened["review"]["token"]}
    answer = client.post(base + "/review-model-trial", json=body)
    assert answer.status_code == 200, answer.text
    value = answer.json()
    receipt = value["receipt"]
    assert (receipt["status"], receipt["lower"], receipt["upper"], value["first_calls"]) == ("accepted", "30.00", "90.00", 0)
    history = client.get(base + f"/model-trial/receipts/{receipt['receipt_id']}").json()
    assert any(row["member_path"] == "table.csv" and row["source_id"] == stored["source_id"]
               and row["source_row"] == 2 for row in history["originals"])
    masked = {"lookup": {**QUERY, "value": "****0001"}, "scope_id": choice["scope_id"]}
    assert client.post(base + "/review-model-trial", json={**body, "ledger_binding": masked}).status_code == 409
    removed = client.delete(base + f"/sources/{stored['source_id']}/operation", params={"expected_batch_id": stored["batch_id"]})
    assert removed.status_code == 200, removed.text
    assert client.post(base + "/trace-ledgers", json=QUERY).json()["status"] == "evidence_missing"
    assert client.post(base + "/review-model-trial", json=body).status_code == 409
    assert client.get(base + f"/model-trial/receipts/{receipt['receipt_id']}").json()["authority"] == "historical"


def test_multiple_or_only_partly_effective_dated_records_do_not_resolve_a_unique_scope(client, make_case, monkeypatch):
    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    base = f"/api/v1/cases/{case}/trace-ledgers"
    _register(client, case, ["ONE,同名合成户,BOOK-A,00000001,2025-12-01,2026-01-01,SYN BANK",
                             "TWO,同名合成户,BOOK-B,00000002,2025-12-01,2026-12-31,SYN BANK"])
    disputed = client.post(base, json={**QUERY, "kind": "name", "value": "同名合成户"}).json()
    assert disputed["status"] == "multiple_ledgers" and len(disputed["choices"]) == 2
    partial = client.post(base, json={**QUERY, "end": "2026-02-01"}).json()
    assert partial["status"] == "period_unproved" and not partial["choices"][0]["covers_period"]
    assert client.post(base, json={**QUERY, "value": "1"}).json()["status"] == "partial_card"
    assert client.post(base, json={**QUERY, "value": "****0001"}).json()["status"] == "partial_card"
    assert client.post(base, json={**QUERY, "value": "9999????0001"}).json()["status"] == "partial_card"
    assert client.post(base, json={**QUERY, "value": "银行卡末四位0001"}).json()["status"] == "partial_card"


def test_source_observations_keep_query_name_and_card_evidence_separate_from_registration(client, make_case, monkeypatch):
    from app import table_source_roles
    from test_automatic_table_import import preview
    from test_feedback_import_api import complete
    from test_uniform35_reference import row, workbook

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    records = [row(**{"姓名(查询条件)": "合成查询姓名", "本方卡号": f"00000000000{suffix:05d}",
                     "查询卡号": f"00000000000{suffix:05d}",
                     "本方账号": f"00000000{suffix}", "交易流水号": f"SYN-OBS-{suffix}", "凭证号": f"SYN-OBS-{suffix}"})
               for suffix in (1001, 1002)]
    records += [row(**{"姓名(查询条件)": "合成查询姓名", "本方卡号": card, "查询卡号": card,
                       "本方账号": f"00000000{suffix}", "交易流水号": f"SYN-OBS-{suffix}", "凭证号": f"SYN-OBS-{suffix}"})
                for suffix, card in ((1003, ""), (1004, "****1004"), (1005, "0000000000000000"))]
    view = preview(client, case, workbook(records, headers=table_source_roles._TRANSACTION_EXPLICIT), "native.xlsx")
    commit = complete(client, case, view)
    assert commit["stage"] == "done" and commit["result"]["ok"] >= 2
    base = f"/api/v1/cases/{case}"
    url = base + "/trace-ledgers"
    name_search = {**QUERY, "kind": "name", "value": "合成查询姓名"}
    answer = client.post(url, json=name_search).raise_for_status().json()
    assert answer["status"] == "evidence_missing" and answer["choices"] == []
    [match] = answer["source_observations"]["matches"]
    assert (match["role"], match["match_column"], match["card_column"]) == ("query_name", 2, 1)
    assert (match["matching_rows"], match["distinct_cards"], match["rows_without_full_card"]) == (5, 2, 3)
    assert [(item["source_row"], item["card_suffix"]) for item in match["examples"]] == [
        (2, "1001"), (3, "1002"), (4, None), (5, None), (6, None)]
    result = client.post(url, json={**QUERY, "value": records[0]["本方卡号"]}).raise_for_status().json()
    [match] = result["source_observations"]["matches"]
    assert result["choices"] == [] and (match["matching_rows"], match["match_column"], match["card_column"]) == (1, 1, 1)
    assert match["examples"][0]["flow_id"]
    assert client.post(url, json={**QUERY, "value": "00000000"}).json()["status"] == "partial_card"
    other = make_case("Other isolated context")
    outside = client.post(f"/api/v1/cases/{other}/trace-ledgers", json=name_search).raise_for_status().json()
    assert "source_observations" not in outside
    retire = client.delete(f"{base}/sources/{view['import_id']}/operation", params={"expected_batch_id": commit["result"]["batch_id"]})
    assert retire.status_code == 200
    assert "source_observations" not in client.post(url, json=name_search).raise_for_status().json()


def _upload_feedback_csv(client, case, content, *, profile):
    from test_automatic_table_import import preview
    from test_feedback_import_api import complete

    view = preview(client, case, content, "synthetic-feedback.csv")
    assert view["table_summary"]["profile"] == profile
    saved = complete(client, case, view)
    assert saved["stage"] == "done"
    return view["import_id"], saved["result"]


def test_feedback_csv_lookup_uses_both_reported_card_and_account_for_a_name(client, database, make_case, monkeypatch):
    from test_folder_evidence import account, table, transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    root = f"/api/v1/cases/{case}"
    wanted = "Synthetic shared owner"
    card_a, card_b = "000008900001", "000008900002"
    acct_a1, acct_a2, acct_b1 = "000003300001", "000003300002", "000003300003"
    aux_raw = table("account", [
        account(acct_a1, card_a, **{"账户开户名称": wanted}),
        account(acct_b1, card_b, **{"账户开户名称": wanted}),
        account(acct_a2, card_a, **{"账户开户名称": "Other synthetic owner"}),
        account(acct_a1, "****0001", **{"账户开户名称": wanted}),
        account("000000000000", card_a, **{"账户开户名称": wanted}),
        account("****0005", card_a, **{"账户开户名称": wanted}),
    ])

    def posting(card, own, side, second, *, day="2026-01-01", peer="000008900099"):
        return transaction(**{"交易卡号": card, "交易账号": own, "收付标志": side,
                              "交易流水号": f"SYN-POST-{second:02d}", "交易时间": f"{day} 10:00:{second:02d}",
                              "交易对手账卡号": peer, "对手户名": wanted})

    ledger_raw = table("transaction", [
        *(posting(card_a, acct_a1, "出", second) for second in range(1, 9)),
        posting(card_a, acct_a2, "出", 9),
        posting(card_a, acct_a1, "进", 10),
        posting(card_b, acct_b1, "进", 11),
        posting(card_b, acct_a1, "进", 12),  # each component exists, but not as a recorded pair
        posting("000008900098", acct_a1, "出", 13, peer=card_a),  # matching the mixed peer is insufficient
        posting(card_a, acct_a1, "进", 14, day="2026-01-02"),
    ])
    ledger, posted = _upload_feedback_csv(client, case, ledger_raw, profile="funds-trace-table-v69")
    assert posted["ok"] == 14
    query_name = {**QUERY, "kind": "name", "value": wanted}
    query_card = {**QUERY, "value": card_a}

    def find(body):
        return client.post(root + "/trace-ledgers", json=body).raise_for_status().json()

    assert "source_observations" not in find(query_name)  # no name fallback to the transaction counterpart
    card_only = find(query_card)
    [trade_card] = card_only["source_observations"]["matches"]
    assert trade_card["source_id"] == ledger and trade_card["role"] == "own_card"
    assert (trade_card["matching_rows"], trade_card["distinct_accounts"], trade_card["distinct_cards"]) == (10, 2, 1)
    assert [entry["source_row"] for entry in trade_card["examples"]] == sorted(
        entry["source_row"] for entry in trade_card["examples"])
    assert len(trade_card["examples"]) == 8
    samples = {(x["account"], x["direction"]) for x in trade_card["examples"]}
    assert samples == {(acct_a1, "in"), (acct_a1, "out"), (acct_a2, "out")}
    assert all(x["flow_id"] and x["clock"].startswith("2026-01-01 10:00:") for x in trade_card["examples"])
    card_flow = trade_card["examples"][0]["flow_id"]
    context = {"case_id": case, "review": "true", "x": 10}
    assert client.get(f"/api/context/{card_flow}", params=context).raise_for_status().json()["anchor"]["id"] == card_flow

    auxiliary, stored = _upload_feedback_csv(client, case, aux_raw, profile="funds-trace-table-v71")
    assert stored["ok"] == 0
    before = client.get(root + "/flows", params={"granularity": "observations"}).raise_for_status().json()
    answer = find(query_name)
    assert answer["status"] == "evidence_missing" and answer["coverage_complete"] and answer["choices"] == []
    evidence = answer["source_observations"]
    assert evidence["complete"] and evidence["checked_sources"] == 2
    by_role = {row["role"]: row for row in evidence["matches"]}
    assert set(by_role) == {"account_snapshot", "matching_card_account"}
    snapshot, trade_name = by_role["account_snapshot"], by_role["matching_card_account"]
    assert (snapshot["source_id"], snapshot["member_path"], snapshot["header_row"]) == (auxiliary, "table.csv", 1)
    assert (snapshot["match_column"], snapshot["card_column"], snapshot["account_column"]) == (1, 3, 4)
    assert (snapshot["matching_rows"], snapshot["distinct_accounts"], snapshot["distinct_cards"]) == (2, 2, 2)
    assert [(x["source_row"], x["account"]) for x in snapshot["examples"]] == [(2, acct_a1), (3, acct_b1)]
    assert all(not any(x.get(key) for key in ("clock", "direction", "flow_id")) for x in snapshot["examples"])
    assert (trade_name["source_id"], trade_name["member_path"], trade_name["header_row"]) == (ledger, "table.csv", 1)
    assert (trade_name["match_column"], trade_name["card_column"], trade_name["account_column"]) == (1, 1, 2)
    assert (trade_name["matching_rows"], trade_name["distinct_accounts"], trade_name["distinct_cards"]) == (10, 2, 2)
    assert {(x["account"], x["direction"]) for x in trade_name["examples"]} == {
        (acct_a1, "in"), (acct_a1, "out"), (acct_b1, "in"),
    }
    assert {2, 11, 12} <= {x["source_row"] for x in trade_name["examples"]}
    for match in evidence["matches"]:
        records = client.get(f"{root}/imports/{match['source_id']}/records", params={
            "member_path": match["member_path"], "page_size": 20,
        }).raise_for_status().json()["items"]
        by_row = {item["source_row"]: item["flow_id"] for item in records}
        assert all(x["flow_id"] == by_row[x["source_row"]] for x in match["examples"])
    assert "source_observations" not in find({**query_name, "value": wanted.upper()})
    assert find({**query_card, "value": "****0001"})["status"] == "partial_card"
    assert find({**query_card, "value": card_a[-4:]})["status"] == "partial_card"
    outside = find({**query_name, "start": "2026-02-01", "end": "2026-02-02"})
    assert outside["choices"] == [] and [item["role"] for item in outside["source_observations"]["matches"]] == ["account_snapshot"]
    include_next = find({**query_card, "start": "2026-01-02", "end": "2026-01-02"})
    [last] = [item for item in include_next["source_observations"]["matches"] if item["role"] == "own_card"]
    assert last["matching_rows"] == 1 and last["examples"][0]["source_row"] == 15
    assert include_next["choices"] == []
    other = make_case("Other case with the same original search")
    for query in (query_name, query_card):
        crossed = client.post(f"/api/v1/cases/{other}/trace-ledgers", json=query).raise_for_status().json()
        assert crossed["choices"] == [] and "source_observations" not in crossed
    after = client.get(root + "/flows", params={"granularity": "observations"}).raise_for_status().json()
    assert after == before and after["funds"]["observation_count"] == 14
    with database._lock:
        database._con.close()
        database._con = None
        database.con()
    assert find(query_name) == answer

    assert client.delete(f"{root}/sources/{auxiliary}/operation",
                         params={"expected_batch_id": stored["batch_id"]}).status_code == 200
    assert "source_observations" not in find(query_name)
    assert find(query_card)["source_observations"] == card_only["source_observations"]
    assert client.delete(f"{root}/sources/{ledger}/operation",
                         params={"expected_batch_id": posted["batch_id"]}).status_code == 200
    retired = find(query_card)
    assert retired["revision"] != answer["revision"] and "source_observations" not in retired
    assert client.get(f"/api/context/{card_flow}", params=context).status_code == 404


def test_feedback_csv_lookup_refuses_changed_header_and_revoked_snapshot(client, make_case, monkeypatch):
    from app import feedback_import
    from test_feedback31_reference import payload
    from test_folder_evidence import account, table, transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    root = f"/api/v1/cases/{case}"
    card, book = "000007000001", "000001000001"
    heading = list(feedback_import.COLUMNS)
    heading[7], heading[8] = heading[8], heading[7]
    near = payload([transaction(**{"交易卡号": card, "交易账号": book})], heading)
    _, posted = _upload_feedback_csv(client, case, near, profile="funds-trace-table-v69")
    assert posted["ok"] == 1
    name = "Synthetic only snapshot"
    source, result = _upload_feedback_csv(client, case, table("account", [
        account(book, card, **{"账户开户名称": name}),
    ]), profile="funds-trace-table-v71")
    query = {**QUERY, "kind": "name", "value": name}
    response = client.post(root + "/trace-ledgers", json=query).raise_for_status().json()
    assert response["choices"] == []
    [observed] = response["source_observations"]["matches"]
    assert (observed["source_id"], observed["role"], observed["examples"][0]["account"]) == (source, "account_snapshot", book)
    assert client.delete(f"{root}/sources/{source}/operation",
                         params={"expected_batch_id": result["batch_id"]}).status_code == 200
    assert "source_observations" not in client.post(root + "/trace-ledgers", json=query).raise_for_status().json()


def test_feedback_csv_lookup_requires_retained_bytes_and_preserves_unknown_clock_and_direction(
        client, database, make_case, monkeypatch):
    from test_folder_evidence import account, table, transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    root = f"/api/v1/cases/{case}"
    card, book, name = "000007000021", "000001000021", "Synthetic retained snapshot"
    aux_raw = table("account", [account(book, card, **{"账户开户名称": name})])
    ledger_raw = table("transaction", [
        transaction(**{"交易卡号": card, "交易账号": book, "交易时间": "2026-01-01",
                       "收付标志": "", "交易流水号": "SYN-ONLY-DATE"}),
        transaction(**{"交易卡号": card, "交易账号": book, "交易时间": "date unavailable",
                       "交易流水号": "SYN-NO-CLOCK"}),
    ])
    auxiliary, _ = _upload_feedback_csv(client, case, aux_raw, profile="funds-trace-table-v71")
    ledger, _ = _upload_feedback_csv(client, case, ledger_raw, profile="funds-trace-table-v69")
    query = {**QUERY, "kind": "name", "value": name}

    def search(body):
        return client.post(root + "/trace-ledgers", json=body).raise_for_status().json()

    found_result = search(query)
    found = found_result["source_observations"]
    assert not found["complete"]
    [trades] = [row for row in found["matches"] if row["role"] == "matching_card_account"]
    [observation] = trades["examples"]
    assert trades["matching_rows"] == 1 and observation["source_row"] == 2
    assert observation["account"] == book
    assert all(observation.get(key) is None for key in ("clock", "direction", "flow_id"))

    aux_path = database.DATA_DIR / "uploads" / f"{auxiliary}.csv"
    aux_path.write_bytes(aux_raw.replace(name.encode(), name.upper().encode(), 1))
    unread = search(query)
    assert unread["choices"] == [] and unread["status"] == found_result["status"]
    assert unread["coverage_complete"] == found_result["coverage_complete"]
    assert unread["source_observations"] == {"checked_sources": 2, "complete": False, "matches": []}
    card_query = search({**QUERY, "value": card})
    assert card_query["choices"] == [] and not card_query["source_observations"]["complete"]
    assert [item["role"] for item in card_query["source_observations"]["matches"]] == ["own_card"]
    aux_path.write_bytes(aux_raw)

    ledger_path = database.DATA_DIR / "uploads" / f"{ledger}.csv"
    ledger_path.write_bytes(ledger_raw.replace(b"SYN-ONLY-DATE", b"SYN-ONLY-LATE", 1))
    report = search(query)
    assert report["choices"] == [] and not report["source_observations"]["complete"]
    [snapshot] = report["source_observations"]["matches"]
    assert snapshot["role"] == "account_snapshot" and snapshot["source_id"] == auxiliary


def test_native32_registration_lookup_and_selected_rows_keep_original_roles_and_dates(client, make_case, monkeypatch):
    from app import feedback_import, table_import
    from test_event_graph import graph, track
    from test_feedback31_reference import payload
    from test_folder_evidence import account

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    root = f"/api/v1/cases/{case}"
    a, b, other, card_a = "000100211", "000100212", "000100213", "0007002120"

    def txn(own, card, counter, minute, direction, amount, balance):
        return {"交易账号": own, "交易卡号": card, "交易对手账卡号": counter,
                "交易时间": f"2026-01-01 10:0{minute}:00", "交易金额": amount, "交易余额": balance,
                "收付标志": direction, "交易币种": "CNY", "交易是否成功": "1"}

    postings = [
        txn(a, card_a, other, 0, "进", "3.00", "30.00"),
        txn(a, card_a, b, 1, "出", "5.00", "25.00"),
        txn(a, card_a, other, 2, "出", "1.00", "24.00"),
        txn(b, "", other, 0, "进", "3.00", "8.00"),
        txn(b, "", a, 1, "进", "5.00", "13.00"),
        txn(b, "", other, 2, "出", "1.00", "12.00"),
    ]
    transaction_heads = tuple(reversed(sorted(table_import.BANK_FEEDBACK_32_COLUMNS)))
    tx, transaction = _upload_feedback_csv(client, case, payload(postings, transaction_heads), profile="funds-trace-table-v69")
    assert transaction["ok"] == 6
    owner_a, owner_b, unrelated_name, other_currency = "SYN Owner A", "SYN Owner B", "SYN Other", "SYN Other Currency"
    scope = {"start": "2026-01-01", "end": "2026-01-01"}

    def search(kind, value):
        query = {**scope, "kind": kind, "value": value}
        return client.post(root + "/trace-ledgers", json=query).raise_for_status().json()

    prior = search("name", owner_b)
    assert prior["choices"] == [] and "source_observations" not in prior
    registry_heads = tuple(reversed(feedback_import.SCHEMAS["account"]))
    raw_registry = payload([
        account(a, card_a, **{"账户开户名称": owner_a, "开户人证件号码": "SYN-DOC-A", "币种": "CNY",
                              "账号开户时间": "2025-12-01", "最后交易时间": "2026-01-02 09:30:00"}),
        account(b, b, **{"账户开户名称": owner_b, "开户人证件号码": "SYN-DOC-B", "币种": "人民币",
                         "账号开户时间": "2025-12-01", "最后交易时间": "2026-01-02 09:30:00"}),
        account(other, b, **{"账户开户名称": unrelated_name, "币种": "CNY"}),
        account(b, "0007002121", **{"账户开户名称": other_currency, "币种": "HKD"}),
    ], registry_heads)
    reg, registry = _upload_feedback_csv(client, case, raw_registry, profile="funds-trace-table-v71")
    assert registry["ok"] == 0
    for kind, value in (("name", owner_b), ("card", b)):
        found = search(kind, value)
        assert found["choices"] == []
        observed = {item["role"]: item for item in found["source_observations"]["matches"]}
        # A card-only match to another account has its own snapshot row. It
        # does not change the account column of the ledger observations.
        snap = observed["account_snapshot"]
        assert snap["source_id"] == reg
        assert snap["registration_columns"]["last_transaction"] == registry_heads.index("最后交易时间") + 1
        row = next(item for item in snap["examples"] if item["source_row"] == 3)
        assert row["card_equals_account"] and row["flow_id"] is None
        matched = observed["matching_registered_account"]
        assert matched["source_id"] == tx and matched["rows_without_full_card"] == 3
        assert matched["match_column"] == transaction_heads.index("交易账号") + 1
        assert all(row["card_suffix"] is None and row["account"] == b for row in matched["examples"])
        assert "own_card" not in observed
    for name in (unrelated_name, other_currency):
        assert [entry["role"] for entry in search("name", name)["source_observations"]["matches"]] == ["account_snapshot"]

    path = root + f"/imports/{tx}/review"
    records = client.get(path, params={"member_path": "table.csv"}).raise_for_status().json()["items"]
    source = {row["source_row"]: row for row in records}
    selection = {"member_path": "table.csv", "record_nos": [source[3]["record_no"], source[6]["record_no"]]}
    result = client.post(path + "/compare", json=selection).raise_for_status().json()
    assert result["reason"] == "source_account_text_pending" and result["event_attribution"] == "unknown"
    proof = result["account_pair_lead"]["registration_context"]
    assert proof["different_name"] and proof["different_document"] and proof["source_role_only"]
    linked = {row["posting_source_row"]: row for row in proof["matches"]}
    assert [(row, item["source_row"]) for row, item in linked.items()] == [(3, 2), (6, 3)]
    assert all(item["source_id"] == reg for item in linked.values())
    assert linked[3]["card_matches"] is True and linked[6]["card_matches"] is None
    assert linked[6]["card_equals_account"] and not linked[6]["posting_card_present"]

    ids = [source[i]["flow_id"] for i in (3, 6)]
    track(client, case, [{"id": ids[0]}])
    assert "source_observation_notice" not in graph(client, case)
    track(client, case, [{"id": ids[1]}])
    canvas = graph(client, case)
    assert canvas["source_observation_notice"] == {"observation_count": 2}
    assert canvas["transaction_count"] == 2 and canvas["funds"]["confirmed_event_count"] == 0
    assert canvas["edges"][0]["total"] == "10.00"
    assert client.delete(f"{root}/sources/{reg}/operation",
                         params={"expected_batch_id": registry["batch_id"]}).status_code == 200
    after = client.post(path + "/compare", json=selection).raise_for_status().json()
    assert after["reason"] == result["reason"] and "registration_context" not in after["account_pair_lead"]
    withdrawn = search("name", owner_b)
    assert withdrawn["status"] == prior["status"] and withdrawn["choices"] == []
    assert "source_observations" not in withdrawn
    assert graph(client, case)["source_observation_notice"] == {"observation_count": 2}

    # A repeated heading must not let a row from another physical table
    # acquire the scope of the first table.
    misplaced = payload([
        account(other, "0007002122", **{"账户开户名称": unrelated_name, "币种": "CNY"}),
        {header: header for header in registry_heads},
        account(b, b, **{"账户开户名称": owner_b, "币种": "CNY"}),
    ], registry_heads)
    misplaced_source, misplaced_receipt = _upload_feedback_csv(client, case, misplaced, profile="funds-trace-table-v71")
    observed = search("name", owner_b)["source_observations"]
    assert observed["matches"] == [] and not observed["complete"]
    assert "registration_context" not in client.post(path + "/compare", json=selection).raise_for_status().json()["account_pair_lead"]
    assert client.delete(f"{root}/sources/{misplaced_source}/operation",
                         params={"expected_batch_id": misplaced_receipt["batch_id"]}).status_code == 200

    _upload_feedback_csv(client, case, raw_registry, profile="funds-trace-table-v71")
    assert client.delete(f"{root}/sources/{tx}/operation",
                         params={"expected_batch_id": transaction["batch_id"]}).status_code == 200
    assert [entry["role"] for entry in search("name", owner_b)["source_observations"]["matches"]] == ["account_snapshot"]
    assert graph(client, case)["transaction_count"] == 0


def test_feedback_csv_lookup_preserves_original_clock_and_reports_an_unfinished_empty_search(
        client, make_case, monkeypatch):
    from app.services import trial_trace_lookup
    from test_folder_evidence import account, table, transaction

    monkeypatch.setattr(client.app.state, "desktop_model_trial", (None,), raising=False)
    case = make_case()
    url = f"/api/v1/cases/{case}/trace-ledgers"
    card, unknown_card, absent_card = "000007000031", "000007000032", "000007000033"
    book = "000001000031"
    clocks = (" 2026/01/01 10:03\t", "2026-01-01 10:04:00", "2026-01-02 10:03", "clock unavailable")
    _upload_feedback_csv(client, case, table("transaction", [
        transaction(**{"交易卡号": own_card, "交易账号": book, "交易时间": clock,
                       "交易流水号": f"SYN-CLOCK-{index}"})
        for index, (own_card, clock) in enumerate(zip((card, card, card, unknown_card), clocks, strict=True), 1)
    ]), profile="funds-trace-table-v69")

    def lookup(value):
        return client.post(url, json={**QUERY, "value": value}).raise_for_status().json()

    shown = lookup(card)
    [observed] = shown["source_observations"]["matches"]
    assert shown["source_observations"]["complete"] and observed["matching_rows"] == 2
    assert [(item["source_row"], item["clock"]) for item in observed["examples"]] == [(2, clocks[0]), (3, clocks[1])]
    no_clock = lookup(unknown_card)
    assert no_clock["source_observations"] == {"checked_sources": 1, "complete": False, "matches": []}
    complete_miss = lookup(absent_card)
    assert "source_observations" not in complete_miss
    assert no_clock == {**complete_miss, "source_observations": no_clock["source_observations"]}

    _upload_feedback_csv(client, case, table("account", [account(book, card)]), profile="funds-trace-table-v71")
    assert "source_observations" not in lookup(absent_card)
    with monkeypatch.context() as limits:
        limits.setattr(trial_trace_lookup, "_OBSERVATION_MEMBERS", 1)
        assert lookup(absent_card)["source_observations"] == {"checked_sources": 1, "complete": False, "matches": []}
