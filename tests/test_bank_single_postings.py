"""Independent native31 booked movements, never settlement assertions."""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal, localcontext
import io
import json
import sys

from openpyxl import load_workbook
import pytest

from test_bank_posting_pairs import encoded, targets, tracked_graph
from test_source_contract_68 import HEADERS
from test_source_ledger import add_source


def entries():
    rows = []
    for index, (amount, balance) in enumerate((("0.00", "1000.00"), ("12.34", "987.66"), ("12.34", "975.32"))):
        row = dict.fromkeys(HEADERS, "")
        row.update({"交易账号": "SYN-SINGLE-A", "交易卡号": "SYN-SINGLE-CARD", "交易对手账卡号": "SYN-SINGLE-B",
            "对手开户银行": "Synthetic counter bank", "收付标志": "出", "交易币种": "CNY",
            "交易时间": f"2039-04-05 09:00:0{index}", "交易金额": amount, "交易余额": balance,
            "交易流水号": "SYN-PREV" if index == 0 else f"SYN-SINGLE-{index}",
            "凭证号": f"SYN-VOUCHER-{index}", "日志号": f"SYN-LOG-{index}", "传票号": f"SYN-TICKET-{index}"})
        rows.append(row)
    return rows


def assert_exact(client, cid):
    page = client.get(f"/api/v1/cases/{cid}/flows").json()
    selected = [row for row in page["items"] if not row["trade_no"].startswith("SYN-PREV")]
    assert len(selected) == 2
    assert {row["balance"] for row in selected} == {"987.66", "975.32"}
    graph = tracked_graph(client, cid, targets(client, cid))
    assert (graph["transaction_count"], graph["edges"][0]["total"]) == (2, "24.68")
    assert graph["funds"]["status"] == "exact"
    assert graph["funds"]["count"] == {"lower": 2, "upper": 2}
    assert graph["funds"]["totals"] == [{"currency": "CNY", "amount": "24.68", "lower": "24.68", "upper": "24.68"}]
    return selected


def test_distinct_independently_balanced_postings_are_exact_and_keep_all_source_references(client, database, make_case):
    cid = make_case("SYN independently balanced singles")
    raw = encoded(entries())
    view, _ = add_source(client, cid, raw)
    selected = assert_exact(client, cid)
    proofs = database.q_raw("select evidence_json from resolved_events where case_id=?", [cid])
    assert len(proofs) == 2
    for proof in proofs:
        proof = json.loads(proof["evidence_json"])
        assert proof["policy"] == "independent-native31-single-posting-v3"
        assert proof["balance_change"]["difference"] == "-12.34"
        assert proof["meaning"] == "local_booked_movement_only; external_settlement_unproved"
    for row in selected:
        source, = client.get(f"/api/v1/cases/{cid}/flows/{row['id']}/sources").json()["items"]
        assert "current_bank_single_posting_proof" in source["record_details"]
        assert "current_bank_posting_pair_proof" not in source["record_details"]
        fields = json.loads(source["record_details"]["source_field_evidence"])
        assert all(any(item["column"] == name and item["raw"].startswith("SYN-") for item in fields)
                   for name in ("凭证号", "日志号", "传票号"))
    exported = client.get("/api/export/tracked.xlsx", params={"case_id": cid})
    assert exported.status_code == 200
    book = load_workbook(io.BytesIO(exported.content), data_only=True)
    rows = list(book["资金统计"].values)
    result, = [dict(zip(rows[0], row)) for row in rows[1:]]
    assert result["精确事件金额"] == result["已确认下界"] == result["可能上界"] == "24.68"
    assert result["事件数下界"] == result["事件数上界"] == 2
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == raw
    report = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"]
    assert sum("current_bank_single_posting_proof" in row["details"] for row in report) == 2


def test_public_rebuild_retains_utf8_single_proofs_and_writes_exact_text(client, database, make_case, monkeypatch):
    from app import bank_posting_pairs, bank_single_postings, event_resolution
    from test_automatic_table_import import preview
    from test_bank_pair_scope_payload import legacy_projection
    from test_folder_evidence import complete

    cid = make_case("SYN retained single proof bytes")
    rows = entries()
    for row in rows[1:]:
        row["交易流水号"] += "-合成"
    raw = encoded(rows)
    published, singleton_facts = [], []
    write = event_resolution._write_projection
    single, facts = bank_single_postings.project, bank_posting_pairs._facts

    def observe_publication(case_id, events, memberships):
        published.append({event["event_id"]: event["evidence_json"] for event in events})
        return write(case_id, events, memberships)

    def observe_facts(row):
        singleton_facts.append(row["reference"])
        return facts(row)

    def observe_single(*args, **kwargs):
        with monkeypatch.context() as observer:
            observer.setattr(bank_posting_pairs, "_facts", observe_facts)
            return single(*args, **kwargs)

    monkeypatch.setattr(event_resolution, "_write_projection", observe_publication)
    with monkeypatch.context() as observer:
        observer.setattr(bank_single_postings, "project", observe_single)
        add_source(client, cid, raw)
    expected, expected_members, _ = legacy_projection(database, cid, monkeypatch)
    proofs = {event["event_id"]: event["evidence_json"] for event in expected}
    assert len(proofs) == 2 and all("合成" in proof for proof in proofs.values())
    assert [tuple(type(proof) for proof in batch.values()) for batch in published] == [(bytes, bytes)]
    assert published[0] == {event_id: proof.encode("utf-8") for event_id, proof in proofs.items()}
    assert singleton_facts == []
    current, members, _ = bank_posting_pairs.project(cid)
    assert (current, members) == (expected, expected_members)
    stored = database.q_raw("select event_id,evidence_json from resolved_events where case_id=?", [cid])
    assert {row["event_id"]: row["evidence_json"] for row in stored} == proofs
    assert database.q_raw("""select cast(flow_id as varchar) flow_id,event_id,status,reason,pending_group
        from event_members where case_id=? order by cast(flow_id as varchar)""", [cid]) == expected_members

    tables = ("flows", "flow_sources", "batches", "import_sources", "import_source_operations", "import_records",
              "resolved_events", "event_members", "event_state")

    def state():
        return {table: database.q_raw(f"select * from {table} where case_id=? order by all", [cid]) for table in tables}

    before = state()
    repeat = preview(client, cid, raw, "repeat.csv")
    insert, failures = database.insert_dataframe, []

    def fail_after_members(table, frame, columns):
        result = insert(table, frame, columns)
        if table == "event_members":
            failures.append(len(frame))
            raise RuntimeError("Synthetic rollback after writing single proof membership")
        return result

    monkeypatch.setattr(database, "insert_dataframe", fail_after_members)
    failed = complete(client, cid, repeat)
    assert failed["stage"] == "error" and failed["result"] is None
    assert len(failures) == 1 and failures[0] > 0
    assert state() == before


def test_public_single_proof_does_not_hold_self_only_keys_beside_prepared_books(client, make_case, monkeypatch):
    from app import bank_single_postings

    original, money = bank_single_postings.project, bank_single_postings.money_decimal
    observed = {"calls": 0, "resident": 0, "decimal_calls": []}
    active = None

    def counted(value):
        if active is not None:
            active[0] += 1
        return money(value)

    def trace(frame, event, _):
        if frame.f_code is not original.__code__:
            return None
        local = frame.f_locals
        if "prepared" in local and "cross" in local:
            seen, resident = set(), 0
            for value in local.values():
                if isinstance(value, dict) and id(value) not in seen:
                    seen.add(id(value))
                    resident += sum(isinstance(key, tuple) and len(key) == 4 and isinstance(key[3], Decimal)
                                    for key in value)
            observed["resident"] = max(observed["resident"], resident)
        return trace

    def project(*args, **kwargs):
        nonlocal active
        previous = sys.gettrace()
        active = [0]
        try:
            sys.settrace(trace)
            return original(*args, **kwargs)
        finally:
            sys.settrace(previous)
            observed["calls"] += 1
            observed["decimal_calls"].append(active[0])
            active = None

    monkeypatch.setattr(bank_single_postings, "project", project)
    monkeypatch.setattr(bank_single_postings, "money_decimal", counted)
    cid = make_case("SYN sparse single proof residency")
    add_source(client, cid, encoded(entries()))
    assert_exact(client, cid)
    assert observed["calls"] > 0
    assert observed["resident"] == 0
    assert max(observed["decimal_calls"]) <= 13


def test_duplicate_support_late_primary_conflict_and_withdrawal_recompute_singles(client, make_case):
    cid = make_case("SYN reversible single proof")
    raw = encoded(entries());view, _ = add_source(client, cid, raw)
    receipt_url = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    receipt = client.get(receipt_url).json()
    assert_exact(client, cid)
    _, duplicate = add_source(client, cid, raw)
    assert_exact(client, cid)
    late = dict(entries()[1], **{"交易时间": "2039-04-05 09:00:03", "交易余额": "962.98"})
    _, conflict = add_source(client, cid, encoded([late]), "late.csv")
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["funds"]["confirmed_event_count"] == 1
    assert graph["funds"]["totals"][0]["amount"] is None
    assert client.delete(f"/api/batches/{conflict['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)
    assert client.delete(f"/api/batches/{duplicate['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)
    assert client.get(receipt_url).json() == receipt
    package = client.get(f"/api/cases/{cid}/export")
    restored = client.post("/api/cases/import", files={"file": ("singles.fundcase", package.content)})
    assert restored.status_code == 200, restored.text
    assert_exact(client, restored.json()["case_id"])


@pytest.mark.parametrize("precision", [3, 28, 50])
@pytest.mark.parametrize("conflict_field", [None, "state", "card_state"])
def test_single_conflict_lookup_streams_case_ids_and_preserves_complete_proofs(
        client, database, make_case, monkeypatch, conflict_field, precision):
    from app import bank_posting_pairs, bank_single_postings
    from test_bank_pair_scope_payload import exact_value, legacy_single_projection

    cid, other = make_case("SYN streamed single conflicts"), make_case("SYN foreign single conflicts")
    raw = encoded(entries())
    add_source(client, cid, raw)
    add_source(client, cid, raw)
    ids = {row["trade_no"]: row["id"] for row in database.q_raw("select id,trade_no from flows where case_id=?", [cid])}
    target, unaffected = ids["SYN-SINGLE-1"], ids["SYN-SINGLE-2"]
    if conflict_field:
        for role in ("payer", "payee"):
            database.x("""insert or replace into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason,card_state)
                values (?,?,?,'SYN-CONFLICT',?,'synthetic',?)""", [cid, target, role,
                "conflict" if conflict_field == "state" else "missing",
                "conflict" if conflict_field == "card_state" else "missing"])
    # A local endpoint outside the effective map and an identical foreign ID
    # must not revoke either valid local proof. Multiple roles may conflict.
    outside = 9_000_000_001
    database.x("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason,card_state)
        values (?,?,'payer','SYN-OUTSIDE','conflict','synthetic','missing'),
               (?,?,'payer','SYN-FOREIGN','resolved','synthetic','conflict')""", [cid, outside, other, unaffected])
    original_project, original_query, original_rows = bank_single_postings.project, database.q_raw, database.raw_rows
    observed = {"bindings": [], "bulk": 0, "streamed": []}

    def conflict_query(query):
        return "from resolved_endpoints" in query and "card_state='conflict'" in query

    def query(sql, params=None):
        if conflict_query(sql):
            observed["bindings"].append(params)
            observed["bulk"] += 1
        return original_query(sql, params)

    @contextmanager
    def rows(sql, params=None):
        with original_rows(sql, params) as stream:
            if conflict_query(sql):
                observed["bindings"].append(params)
                def selected():
                    for row in stream:
                        assert set(row) == {"flow_id"}
                        observed["streamed"].append(row["flow_id"])
                        yield row
                yield selected()
            else:
                yield stream

    def compare(case_id, candidates, books, unlocated, effective, current, memberships, excluded):
        assert outside not in {int(fid) for fid in effective}
        assert sum(row["reference"] == "SYN-SINGLE-1" for row in candidates) == 2
        before = exact_value(candidates)
        expected_memberships = deepcopy(memberships)
        with localcontext() as context:
            context.prec = precision
            expected = legacy_single_projection(case_id, candidates, books, unlocated, effective,
                                                current, expected_memberships, excluded)
            with monkeypatch.context() as lookup:
                lookup.setattr(database, "q_raw", query)
                lookup.setattr(database, "raw_rows", rows)
                actual = original_project(case_id, candidates, books, unlocated, effective, current, memberships, excluded)
        assert exact_value((actual, memberships)) == exact_value((expected, expected_memberships))
        assert exact_value(candidates) == before
        assert len(actual) == (1 if conflict_field else 2)
        assert memberships[str(target)]["reason"] == ("single_posting_identity_conflict" if conflict_field
                                                     else "single_posting_independent_balance_proof")
        return actual

    monkeypatch.setattr(bank_single_postings, "project", compare)
    bank_posting_pairs.project(cid)
    assert observed["bindings"] == [[cid]]
    assert observed["bulk"] == 0
    assert sorted(observed["streamed"]) == sorted([outside, *([target, target] if conflict_field else [])])


@pytest.mark.parametrize("owners,observed,expected", [
    (1, (), 0), (1, (1,), 0), (1, (2,), 2), (2, (2,), 0),
    (2, (1,), 1), (3, (1,), 1), (3, (1, 2, 1), 3)])
def test_sparse_cross_index_keeps_only_actual_opposite_owners(database, make_case, owners, observed, expected):
    from app import bank_single_postings

    cid = make_case("SYN sparse owner masks")
    key = ('SYN-A', 'SYN-B', 'USD', Decimal('7.53'))
    wanted = {key: owners}
    books = {('SYN-book',): [
        {'evidence': {'row': {'payer_account': 'SYN-A', 'payee_account': 'SYN-B',
                              'self_account': 'SYN-A' if owner == 1 else 'SYN-B', 'currency': 'USD', 'amount': '7.53'},
                      'signed_amount': '7.53'}} for owner in observed]}
    result = bank_single_postings._cross_index(cid, books, {}, wanted)
    assert result == ({key: expected} if expected else {})
    assert wanted == {key: owners} and next(iter(wanted)) is key


def test_sparse_cross_index_keeps_legacy_case_currency_amount_and_current_row_scope(
        database, make_case, flow_row):
    from app import bank_single_postings, importer

    cid, other = make_case("SYN sparse legacy scope"), make_case("SYN foreign sparse legacy scope")
    key = ('SYN-A', 'SYN-B', 'USD', Decimal('7.53'))
    wanted = {key: 1}
    def legacy(case_id, number, amount='7.53', currency='USD'):
        importer.commit_rows('synthetic-legacy.csv', 'A', [flow_row(trade_no=number,
            payer_account='SYN-A', payee_account='SYN-B', self_account='SYN-B',
            amount=Decimal(amount), balance=None, currency=currency)], case_id=case_id)
    legacy(other, 'SYN-FOREIGN')
    legacy(cid, 'SYN-CURRENCY', currency='CNY')
    legacy(cid, 'SYN-AMOUNT', amount='7.54')
    assert bank_single_postings._cross_index(cid, {}, {}, wanted) == {}
    legacy(cid, 'SYN-MATCH')
    assert bank_single_postings._cross_index(cid, {}, {}, wanted) == {key: 2}
    fid = database.q_raw("select id from flows where case_id=? and trade_no='SYN-MATCH'", [cid])[0]['id']
    assert bank_single_postings._cross_index(cid, {}, {str(fid): {}}, wanted) == {}


@pytest.mark.parametrize("change", ["missing_clock", "missing_amount", "same_target_time", "wrong_balance", "same_primary", "missing_bank"])
def test_incomplete_book_or_target_proof_stays_unresolved(client, database, make_case, change):
    rows = entries()
    if change == "missing_clock": rows[0]["交易时间"] = ""
    elif change == "missing_amount": rows[0]["交易金额"] = ""
    elif change == "same_target_time": rows[1]["交易时间"] = rows[2]["交易时间"]
    elif change == "wrong_balance": rows[1]["交易余额"] = "987.65"
    elif change == "same_primary": rows[1]["交易流水号"] = rows[2]["交易流水号"]
    else:
        for row in rows: row["对手开户银行"] = ""
    cid = make_case("SYN missing independent proof")
    add_source(client, cid, encoded(rows))
    assert not database.q_raw("select * from resolved_events where case_id=?", [cid])


@pytest.mark.parametrize("prior_kind", ["negative", "unknown_direction"])
def test_negative_and_unoriented_rows_remain_available_as_previous_balance_observations(client, make_case, prior_kind):
    rows = entries()
    if prior_kind == "negative": rows[0]["交易金额"] = "-1.00"
    else:
        rows[0]["收付标志"] = ""
        rows[0]["交易对手账卡号"] = rows[0]["对手开户银行"] = ""
        rows[0]["交易是否成功"] = "1"
    cid = make_case("SYN retained source history")
    add_source(client, cid, encoded(rows))
    assert_exact(client, cid)


def test_empty_direction_and_empty_status_remains_unadmitted_and_traceable(client, database, make_case):
    rows = entries();rows[0]["收付标志"] = "";rows[0]["交易对手账卡号"] = rows[0]["对手开户银行"] = ""
    raw = encoded(rows);cid = make_case("SYN unknown status source boundary")
    view, _ = add_source(client, cid, raw)
    records = client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/report").json()["items"]
    first = next(row for row in records if row["source_row"] == 2)
    assert first["reason_code"] == "unknown_status" and first["flow_id"] is None
    assert "source_ledger_evidence" not in first["details"]
    assert not database.q_raw("select * from resolved_events where case_id=?", [cid])
    assert client.get(f"/api/v1/cases/{cid}/sources/{view['import_id']}/file").content == raw


@pytest.mark.parametrize("clock", ["2039-04-05 09:00:01", "2039-04-05 09:00:02", "2041-01-01 00:00:00", "", "2039-04-05T09:00:01+08:00"])
def test_late_different_primary_counterbook_candidate_revokes_without_merging(client, database, make_case, clock):
    cid = make_case("SYN cross-book uncertainty")
    add_source(client, cid, encoded(entries()))
    assert_exact(client, cid)
    before = dict(entries()[0], **{"交易账号": "SYN-SINGLE-B", "交易卡号": "SYN-OTHER-CARD",
        "交易对手账卡号": "SYN-SINGLE-A", "收付标志": "进", "交易余额": "500.00", "交易流水号": "SYN-PREV-OTHER"})
    opposite = dict(before, **{"交易流水号": "SYN-DIFFERENT-PRIMARY", "交易时间": clock,
        "交易金额": "12.34", "交易余额": "512.34"})
    _, late = add_source(client, cid, encoded([before, opposite]), "opposite.csv")
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["transaction_count"] == 2
    assert graph["edges"][0]["total"] == '24.68'
    assert graph["funds"]["totals"][0]["amount"] is None
    assert not database.q_raw("select * from resolved_events where case_id=? and amount=12.34 and evidence_json like '%SYN-SINGLE-1%'", [cid])
    assert all(len(json.loads(row["evidence_json"])["source_rows"]) == 1 for row in
               database.q_raw("select evidence_json from resolved_events where case_id=?", [cid]))
    assert client.delete(f"/api/batches/{late['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)


def test_primary_reuse_across_counterparty_banks_cannot_confirm_singletons(client, database, make_case, monkeypatch):
    from app import bank_single_postings

    cid = make_case("SYN global primary competition")
    rows = entries();rows[2]["交易流水号"] = rows[1]["交易流水号"];rows[2]["对手开户银行"] = "Synthetic different counter bank"
    original, requested = bank_single_postings._cross_index, []
    def observe(case_id, books, current, wanted):
        requested.append(len(wanted))
        return original(case_id, books, current, wanted)
    monkeypatch.setattr(bank_single_postings, '_cross_index', observe)
    add_source(client, cid, encoded(rows))
    assert not database.q_raw("select * from resolved_events where case_id=?", [cid])
    assert requested and requested == [0] * len(requested)
    assert {row['reason'] for row in database.q_raw("""select m.reason from event_members m
        join flows f on f.case_id=m.case_id and f.id=m.flow_id
        where m.case_id=? and f.trade_no='SYN-SINGLE-1'""", [cid])} == {'single_posting_primary_reused'}


def test_named_primary_with_tab_crlf_in_nonfinancial_other_family_revokes_singleton(client, make_case):
    from app.feedback_import import COLUMNS
    cid = make_case("SYN literal primary whitespace")
    add_source(client, cid, encoded(entries()))
    assert_exact(client, cid)
    row = dict(entries()[1], **{"交易账号": "SYN-UNRELATED", "交易卡号": "SYN-UNRELATED-CARD",
        "交易流水号": "SYN-SINGLE-1\t\r\n", "交易金额": "", "对手开户银行": "Synthetic unrelated bank"})
    _, late = add_source(client, cid, encoded([row], headers=COLUMNS), "other-family.csv")
    graph = client.get("/api/graph", params={"case_id": cid}).json()
    assert graph["funds"]["confirmed_event_count"] == 1
    assert client.delete(f"/api/batches/{late['result']['batch_id']}", params={"case_id": cid}).status_code == 200
    assert_exact(client, cid)


def test_earlier_projection_rebuild_keeps_source_journal_and_receipt_unchanged(client, database, make_case):
    from app import event_resolution
    cid = make_case("SYN v4 derived projection compatibility")
    view, _ = add_source(client, cid, encoded(entries()))
    before = database.q_raw("select * from import_records where case_id=? order by record_no", [cid])
    receipt_url = f"/api/v1/cases/{cid}/imports/{view['import_id']}"
    receipt = client.get(receipt_url).json()
    summary = event_resolution.summary(cid);summary["policy"] = "scoped-source-postings-v4"
    with database.transaction():
        database.x("delete from resolved_events where case_id=?", [cid])
        database.x("update event_members set status='unknown',event_id=null where case_id=?", [cid])
        database.x("update event_state set summary_json=? where case_id=?", [json.dumps(summary), cid])
    with database._lock:
        database.con().execute("checkpoint")
        database._con.close();database._con = None
        database.con()
    assert_exact(client, cid)
    assert database.q_raw("select * from import_records where case_id=? order by record_no", [cid]) == before
    assert client.get(receipt_url).json() == receipt
