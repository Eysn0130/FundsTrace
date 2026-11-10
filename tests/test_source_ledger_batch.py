"""Independent nonempty source-ledger scenarios and bounded writer contracts."""
import csv
import io
import json
import zipfile

import pytest


BANK_HEADERS = "交易卡号 交易账号 交易方户名 交易方证件号码 交易时间 交易金额 交易余额 收付标志 交易对手账卡号 现金标志 对手户名 对手身份证号 对手开户银行 摘要说明 交易币种 交易网点名称 交易发生地 交易是否成功 传票号 IP地址 MAC地址 对手交易余额 交易流水号 日志号 凭证种类 凭证号 交易柜员号 备注 商户名称 交易类型 查询反馈结果原因".split()


def complex_source(scale=2, *, ledger_amount="-0.01"):
    """Fixed source expectations, independent of every product projection."""
    bank, neutral = [], []
    def posting(own, counter, trade, direction, stamp, amount, balance, currency="CNY"):
        return {"交易卡号": own, "交易账号": own, "交易方户名": "SYN owner",
                "交易对手账卡号": counter, "对手户名": "SYN counterparty", "对手开户银行": "SYN Bank",
                "交易流水号": trade, "收付标志": direction, "交易时间": "2038-02-04 " + stamp,
                "交易金额": amount, "交易余额": balance, "交易币种": currency,
                "交易是否成功": "01", "查询反馈结果原因": "SYN feedback", "日志号": "0", "传票号": "-", "凭证号": "-"}
    bank += [
        posting("000SYN-PAIR-A", "000SYN-PREV-X", "SYN-PREV-A", "出", "09:00:00", "1.00", "1000.00"),
        posting("000SYN-PAIR-A", "000SYN-PAIR-B", "SYN-PAIR", "出", "09:00:01", "37.41", "962.59"),
        posting("000SYN-PAIR-B", "000SYN-PREV-Y", "SYN-PREV-B", "进", "09:00:00", "1.00", "2000.00"),
        posting("000SYN-PAIR-B", "000SYN-PAIR-A", "SYN-PAIR", "进", "09:00:01", "37.41", "2037.41"),
        posting("000SYN-LEG-A", "000SYN-LEG-X", "SYN-LEG-PREV", "出", "10:00:00", "1.00", "1.00", "USD"),
        posting("000SYN-LEG-A", "000SYN-LEG-B", "SYN-LEG-1", "出", "10:00:01", "0.10", "0.90", "USD"),
        posting("000SYN-LEG-A", "000SYN-LEG-B", "SYN-LEG-2", "出", "10:00:02", "0.10", "0.80", "USD"),
    ]
    for n in range(scale):
        currency = "USD" if n % 2 else "CNY"
        bank += [posting(f"000SYN-NEG-{n:05d}", "000SYN-NEG-COUNTER", f"SYN-NEG-{n:05d}", "出", "11:00:00", ledger_amount if n == 0 else "-0.01", "10.01", currency),
                 posting(f"000SYN-ZERO-{n:05d}", "", f"SYN-ZERO-{n:05d}", "出", "11:00:01", "0.00", "10.01", currency)]
        neutral.append(posting(f"000SYN-UNKNOWN-{n:05d}", f"000SYN-NEUTRAL-{n:05d}", f"SYN-UNKNOWN-{n:05d}",
                               "待核", "12:00:00", "90071992547409.91" if n == 0 else "0.03", "20.00", currency))
    members, facts = [], []
    for path, headers, rows in (("bank.csv", BANK_HEADERS, bank), ("neutral.csv", BANK_HEADERS, neutral)):
        stream = io.StringIO(newline="")
        writer = csv.writer(stream); writer.writerow(headers)
        for position, row in enumerate(rows, 2):
            writer.writerow([row.get(header, "") for header in headers])
            facts.append({"trade_no": row["交易流水号"], "amount": row["交易金额"], "currency": row["交易币种"],
                          "self_account": row["交易账号"], "trade_time": row["交易时间"], "balance": row["交易余额"],
                          "source_direction": row["收付标志"], "member_path": path, "source_row": position})
        members.append((path, stream.getvalue().encode("utf-8-sig")))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, raw in members:
            item = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0)); item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, raw)
    return output.getvalue(), {"scale": scale, "source_rows": 7 + 3 * scale, "ledger_rows": 3 * scale,
                               "financial_observations": 7, "canonical_financial_count": 6, "facts": facts}


def test_complex_public_source_preserves_one_pair_two_equal_transfers_and_neutral_observations(client, database, make_case, monkeypatch):
    from app import source_ledger
    from test_source_ledger import add_source
    counts, commit = [], source_ledger.commit
    def observed(*args):
        result = commit(*args)
        counts.append(result)
        return result
    monkeypatch.setattr(source_ledger, "commit", observed)
    cid = make_case("SYN independently fixed source batch")
    raw, expected = complex_source()
    _, first = add_source(client, cid, raw, "source.zip")
    assert counts == [{"added": 6, "duplicates": 0}], first
    assert first["result"]["ok"] == 7 and first["result"]["rows_dup"] == 0
    observed = client.get(f"/api/v1/cases/{cid}/flows", params={"granularity": "observations"}).json()
    assert observed["total"] == expected["source_rows"] == 13
    legacy = client.get("/api/flows", params={"case_id": cid}).json()
    assert legacy["total"] == expected["canonical_financial_count"] + expected["ledger_rows"] == 12
    original = database.q_raw("select id,trade_no,amount,currency,self_account from flows where case_id=? order by id", [cid])
    assert len(original) == 13
    assert [row["id"] for row in original] == list(range(1, 14))
    sequence = database.q_raw("select last_value from duckdb_sequences() where sequence_name='flow_seq'")[0]["last_value"]
    assert sequence == 13
    assert sorted((row["trade_no"], format(row["amount"], ".2f"), row["currency"], row["self_account"]) for row in original) == sorted(
        (row["trade_no"], row["amount"], row["currency"], row["self_account"]) for row in expected["facts"])
    selected = [row for row in observed["items"] if row["trade_no"] in {"SYN-PAIR", "SYN-LEG-1", "SYN-LEG-2", "SYN-UNKNOWN-00000", "SYN-NEG-00000", "SYN-ZERO-00000"}]
    unknown, = [row for row in selected if row["trade_no"] == "SYN-UNKNOWN-00000"]
    assert unknown["payer_account"] == unknown["payee_account"] == ""
    assert unknown["amount"] == "90071992547409.91" and unknown["source_ledger"]["direction"] == "unknown"
    assert client.post("/api/track", json={"case_id": cid, "flow_ids": [row["id"] for row in selected]}).status_code == 200
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    money_edges = [edge for edge in graph["edges"] if edge["cnt"]]
    assert sorted((edge["cnt"], edge["amount_totals"]) for edge in money_edges) == [
        (1, [{"currency": "CNY", "amount": "37.41"}]), (2, [{"currency": "USD", "amount": "0.20"}])]
    assert graph["transaction_count"] == graph["funds"]["confirmed_event_count"] == 3
    assert graph["funds"]["observation_count"] == 4 and graph["funds"]["status"] == "exact"
    assert graph["source_ledger"] == {"count": 3, "unknown_effect_count": 2}
    _, repeated = add_source(client, cid, raw, "source.zip")
    assert counts[-1] == {"added": 0, "duplicates": 6}
    assert repeated["result"]["ok"] == 0 and repeated["result"]["rows_dup"] == 7
    assert database.q_raw("select id,trade_no,amount,currency,self_account from flows where case_id=? order by id", [cid]) == original
    assert database.q_raw("select last_value from duckdb_sequences() where sequence_name='flow_seq'")[0]["last_value"] == sequence


@pytest.mark.parametrize("amount,accepted", [("-9999999999999999.99", True),
                                            ("-999999999999999999999999999999999999.99", False)],
                         ids=["actual-storage-bound", "generic-money-bound-exceeds-storage"])
def test_public_ledger_amount_preserves_actual_import_storage_bound(client, database, make_case, monkeypatch, amount, accepted):
    import decimal
    import traceback
    from app import source_ledger
    from app.money import money_decimal
    from test_automatic_table_import import preview
    from test_folder_evidence import complete
    assert format(money_decimal(amount), ".2f") == amount
    cid = make_case("SYN maximum exact source ledger")
    raw, _ = complex_source(1, ledger_amount=amount)
    view = preview(client, cid, raw, "source.zip")
    commit, captured = source_ledger.commit, []
    def observed(*args):
        try:
            return commit(*args)
        except BaseException as error:
            captured.append({"precision": decimal.getcontext().prec, "error": repr(error), "traceback": traceback.format_exc()})
            raise
    monkeypatch.setattr(source_ledger, "commit", observed)
    result = complete(client, cid, view)
    assert result["stage"] == "done", {"result": result, "source_commit_error": captured}
    assert not captured
    matched = database.q_raw("select amount,self_account from flows where case_id=? and trade_no='SYN-NEG-00000'", [cid])
    if not accepted:
        assert matched == []
        record, = database.q_raw("select disposition,details_json from import_records where case_id=? and member_path='bank.csv' and source_row=9", [cid])
        assert record["disposition"] == "unresolved" and "amount_overflow" in record["details_json"]
        return
    row, = matched
    assert format(row["amount"], ".2f") == amount and row["self_account"] == "000SYN-NEG-00000"
    page = client.get(f"/api/v1/cases/{cid}/flows", params={"kw": "SYN-NEG-00000"})
    assert page.status_code == 200 and page.json()["items"][0]["amount"] == amount


def test_nonempty_public_ledger_import_batches_database_round_trips(client, database, make_case, monkeypatch):
    from app import source_ledger
    from test_source_ledger import add_source
    cid = make_case("SYN ledger query budget")
    raw, _ = complex_source(8)
    query, commit = database.q_raw, source_ledger.commit
    active, calls, results = False, [], []
    def counted(sql, params=None):
        if active:
            calls.append(sql)
        return query(sql, params)
    def observed(*args):
        nonlocal active
        start = len(calls)
        active = True
        try:
            result = commit(*args)
        finally:
            active = False
        results.append((result, len(calls) - start))
        return result
    monkeypatch.setattr(database, "q_raw", counted)
    monkeypatch.setattr(source_ledger, "commit", observed)
    add_source(client, cid, raw, "source.zip")
    add_source(client, cid, raw, "source.zip")
    assert [result for result, _ in results] == [{"added": 24, "duplicates": 0}, {"added": 0, "duplicates": 24}]
    assert all(count < 10 for _, count in results), results


def test_history_keeps_lowest_matching_id_after_many_changed_candidates(client, database, make_case, monkeypatch):
    from app import source_ledger
    from test_source_ledger import add_source
    cid = make_case("SYN ordered ledger history")
    raw, _ = complex_source()
    view, _ = add_source(client, cid, raw, "source.zip")
    target, = database.q_raw("""select s.flow_id,r.record_no,r.member_path from import_records r
        join flow_sources s on s.case_id=r.case_id and s.source_id=r.source_id and s.source_row=r.record_no
        where r.case_id=? and r.source_id=? and r.source_row=9 and r.member_path='bank.csv'""", [cid, view["import_id"]])
    database.x("update flows set amount=-0.02 where id=?", [target["flow_id"]])
    matching = []
    with database.import_transaction():
        for n in range(130):
            sid = f"{n:032x}"
            fid = database.q_raw("select nextval('flow_seq') id")[0]["id"]
            amount = "-0.01" if n >= 128 else "-0.02"
            columns = ["id", "case_id", "batch_id", *source_ledger.FLOW_FIELDS]
            copied = database.q_raw("select * from flows where id=?", [target["flow_id"]])[0]
            copied.update(id=fid, amount=amount)
            database.x(f"insert into flows({','.join(columns)}) values ({','.join('?' for _ in columns)})",
                       [copied[column] for column in columns])
            for table, extra in (("import_sources", ""), ("import_members", " and member_path='bank.csv'"),
                                 ("import_records", " and record_no=?")):
                params = [sid, cid, view["import_id"]] + ([target["record_no"]] if table == "import_records" else [])
                database.x(f"insert into {table} select * replace (? as source_id) from {table} where case_id=? and source_id=?" + extra, params)
            database.x("insert into flow_sources values (?,?,?,?,?)", [cid, sid, target["record_no"], fid, "imported"])
            if n >= 128:
                matching.append(fid)
    counts, commit = [], source_ledger.commit
    def observed(*args):
        result = commit(*args); counts.append(result); return result
    monkeypatch.setattr(source_ledger, "commit", observed)
    repeated, _ = add_source(client, cid, raw, "source.zip")
    chosen, = database.q_raw("select flow_id from flow_sources where case_id=? and source_id=? and source_row=?",
                            [cid, repeated["import_id"], target["record_no"]])
    assert chosen["flow_id"] == min(matching)
    assert counts == [{"added": 0, "duplicates": 6}]
    assert str(database.q_raw("select amount from flows where id=?", [target["flow_id"]])[0]["amount"]) == "-0.02"


@pytest.mark.parametrize("changed_digest", [False, True], ids=["same-digest", "different-digest"])
def test_same_source_position_sees_earlier_writes_without_comparing_extra_digest_fields(client, database, make_case, monkeypatch, changed_digest):
    from app import source_ledger
    from test_source_ledger import add_source
    cid = make_case("SYN same operation ledger support")
    raw, _ = complex_source()
    commit, counts = source_ledger.commit, []
    def duplicate_position(case_id, source_id, batch_id):
        record, = database.q_raw("select * from import_records where case_id=? and source_id=? and record_no=8", [case_id, source_id])
        details = json.loads(record["details_json"])
        evidence = json.loads(details[source_ledger.FIELD])
        evidence["row"]["self_name"] = "SYN source metadata outside established flow digest"
        if changed_digest:
            evidence["row"]["amount"] = evidence["signed_amount"] = "-0.02"
        details[source_ledger.FIELD] = json.dumps(evidence, ensure_ascii=False)
        database.x("""insert into import_records select * replace (14 as record_no,? as details_json)
            from import_records where case_id=? and source_id=? and record_no=8""",
                   [json.dumps(details, ensure_ascii=False), case_id, source_id])
        result = commit(case_id, source_id, batch_id); counts.append(result); return result
    monkeypatch.setattr(source_ledger, "commit", duplicate_position)
    view, _ = add_source(client, cid, raw, "source.zip")
    supports = database.q_raw("""select source_row,flow_id,disposition from flow_sources
        where case_id=? and source_id=? and source_row in (8,14) order by source_row""", [cid, view["import_id"]])
    assert counts == [{"added": 7, "duplicates": 0} if changed_digest else {"added": 6, "duplicates": 1}]
    assert [row["flow_id"] for row in supports] == [8, 14 if changed_digest else 8]
    assert [row["disposition"] for row in supports] == ["imported", "imported" if changed_digest else "duplicate"]


def test_existing_support_skips_publication_after_validating_the_complete_source(client, database, make_case, monkeypatch):
    from app import source_ledger
    from test_source_ledger import add_source
    cid = make_case("SYN already supported source")
    raw, _ = complex_source()
    view, job = add_source(client, cid, raw, "source.zip")
    before = database.q_raw("select * from flows where case_id=? order by id", [cid])
    validated, validate = [], source_ledger.validate
    def observed(encoded):
        validated.append(encoded)
        return validate(encoded)
    monkeypatch.setattr(source_ledger, "validate", observed)
    with database.import_transaction():
        assert source_ledger.commit(cid, view["import_id"], job["result"]["batch_id"]) == {"added": 0, "duplicates": 0}
    assert len(validated) == 13
    assert database.q_raw("select * from flows where case_id=? order by id", [cid]) == before


@pytest.mark.parametrize("failure", ["late-ledger-validation", "supported-financial-validation", "supported-ledger-validation", "final-refresh"])
def test_publication_failure_rolls_back_the_complete_source_and_preserves_other_case(client, database, make_case, monkeypatch, failure):
    from app import source_ledger
    from test_automatic_table_import import preview
    from test_folder_evidence import complete
    from test_source_ledger import add_source
    raw, expected = complex_source(90 if failure == "late-ledger-validation" else 2)
    other = make_case("SYN retained other case")
    add_source(client, other, raw, "source.zip")
    retained = database.q_raw("select * from flows where case_id=? order by id", [other])
    initial_sequence = database.q_raw("select last_value from duckdb_sequences() where sequence_name='flow_seq'")[0]["last_value"]
    cid = make_case("SYN atomic ledger failure")
    view = preview(client, cid, raw, "source.zip")
    commit, refresh = source_ledger.commit, source_ledger.refresh_index
    inside = False
    def fail_refresh(*args):
        if inside and failure == "final-refresh":
            raise ValueError("SYN final source ledger refresh failure")
        return refresh(*args)
    def fail_commit(case_id, source_id, batch_id):
        nonlocal inside
        if failure != "final-refresh":
            number = 1 if failure == "supported-financial-validation" else 8 if failure == "supported-ledger-validation" else expected["source_rows"]
            if failure == "supported-ledger-validation":
                first, = database.q_raw("select flow_id from flow_sources where case_id=? and source_id=? and source_row=1", [case_id, source_id])
                database.x("insert into flow_sources values (?,?,?,?,?)", [case_id, source_id, number, first["flow_id"], "duplicate"])
            record, = database.q_raw("select details_json from import_records where case_id=? and source_id=? and record_no=?", [case_id, source_id, number])
            details = json.loads(record["details_json"])
            evidence = json.loads(details[source_ledger.FIELD]); evidence["header_scope"] = 0
            details[source_ledger.FIELD] = json.dumps(evidence)
            database.x("update import_records set details_json=? where case_id=? and source_id=? and record_no=?",
                       [json.dumps(details), case_id, source_id, number])
        inside = True
        try:
            return commit(case_id, source_id, batch_id)
        finally:
            inside = False
    monkeypatch.setattr(source_ledger, "commit", fail_commit)
    monkeypatch.setattr(source_ledger, "refresh_index", fail_refresh)
    result = complete(client, cid, view)
    assert result["stage"] == "error", result
    assert client.get(f"/api/v1/cases/{cid}/imports/{view['import_id']}").json()["stage"] == "error"
    for table in ("flows", "flow_sources", "import_sources", "import_members", "import_records", "batches", "source_ledger_index"):
        assert database.q_raw(f"select count(*) n from {table} where case_id=?", [cid])[0]["n"] == 0
    assert database.q_raw("select * from flows where case_id=? order by id", [other]) == retained
    ledger_prefix = {"late-ledger-validation": 269, "supported-financial-validation": 0, "supported-ledger-validation": 0, "final-refresh": 6}[failure]
    assert database.q_raw("select last_value from duckdb_sequences() where sequence_name='flow_seq'")[0]["last_value"] == initial_sequence + 7 + ledger_prefix
