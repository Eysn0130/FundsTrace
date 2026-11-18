"""Independent synthetic references; ordinary matching values confer no identity."""
from datetime import datetime
from decimal import Decimal
import json

import pytest

from app import event_resolution as events


def observed(flow_id, own, **changes):
    result = {"flow_id": flow_id, "payer_account": "SYN-A", "payee_account": "SYN-B",
              "self_account": own, "amount": Decimal("100.00"), "currency": "CNY",
              "trade_time": datetime(2026, 1, 1, 12), "effective": True, "typed_accounts": True,
              "evidence": json.dumps({"policy": events.POLICY, "issuer": "Synthetic issuer",
                  "channel": "Synthetic clearing", "business_scope": "Synthetic transfers 2026", "reference": "SYN-E1"})}
    result.update(changes)
    return result


def test_opposite_books_form_one_event_without_merging_balances_or_duplicate_links():
    a, b = observed("1", "SYN-A"), observed("2", "SYN-B")
    resolved, members = events.resolve([a, b, a.copy(), b.copy()])
    assert len(resolved) == 1 and resolved[0]["amount"] == Decimal("100.00")
    assert resolved[0]["flow_ids"] == ["1", "2"]
    assert all(member["status"] == "confirmed" for member in members)
    assert events.resolve([b, a]) == (resolved, members)


@pytest.mark.parametrize("changes,reason", [
    ({"amount": Decimal("100.01")}, "conflicting_event_facts"),
    ({"currency": "USD"}, "conflicting_event_facts"),
    ({"trade_time": datetime(2026, 1, 1, 12, 0, 1)}, "conflicting_event_facts"),
    ({"payee_account": "SYN-C", "self_account": "SYN-C"}, "conflicting_event_facts"),
    ({"self_account": "SYN-A"}, "multiple_entries_on_one_side"),
    ({"typed_accounts": False}, "incomplete_ledger_identity"),
    ({"effective": False}, "unresolved_or_excluded_observation"),
])
def test_conflicts_revoke_instead_of_choosing_a_pair(changes, reason):
    resolved, members = events.resolve([observed("1", "SYN-A"), observed("2", "SYN-B", **changes)])
    assert resolved == []
    assert all(row["reason"] == reason for row in members)


def test_reference_reuse_and_distinct_scopes_do_not_swallow_same_time_amount_events():
    a, b = observed("1", "SYN-A"), observed("2", "SYN-B")
    resolved, members = events.resolve([a, b, observed("3", "SYN-A")])
    assert not resolved and {row["reason"] for row in members} == {"reference_reused"}
    other = json.loads(a["evidence"])
    other["business_scope"] = "Independent settlement domain"
    resolved, members = events.resolve([a, b, observed("3", "SYN-A", evidence=json.dumps(other)),
                                        observed("4", "SYN-B", evidence=json.dumps(other))])
    assert len(resolved) == 2 and len({row["event_id"] for row in resolved}) == 2


def test_one_flow_with_conflicting_references_invalidates_both_groups():
    a, b = observed("1", "SYN-A"), observed("2", "SYN-B")
    second = json.loads(a["evidence"])
    second["reference"] = "SYN-E2"
    resolved, members = events.resolve([a, b, observed("1", "SYN-A", evidence=json.dumps(second)),
                                        observed("3", "SYN-B", evidence=json.dumps(second))])
    assert not resolved and all(row["status"] == "conflict" for row in members)


def test_parser_requires_closed_explicit_reference_headers_and_valid_order():
    headers = list(events.HEADERS.values()) + ["本笔交易后账面余额", "记账顺序号"]
    values = ["Synthetic bank", "Synthetic channel", "Synthetic payments", "SYN-REF", "99.00", "9007199254740993"]
    result = events.parse_fields(headers, values, accepted=True, posting_scope=3)
    assert events.validate_reference(result[events.DETAIL_FIELD])["reference"] == "SYN-REF"
    assert events.validate_ledger(result[events.LEDGER_FIELD]) == {
        "policy": events.LEDGER_POLICY, "balance_kind": "post_book", "posting_order": "9007199254740993", "posting_scope": "3"}
    assert events.DETAIL_FIELD not in events.parse_fields(headers[:-3], values[:-3], accepted=True)
    assert events.DETAIL_FIELD not in events.parse_fields(headers + [headers[0]], values + [values[0]], accepted=True)
    assert events.parse_fields(["交易流水号", "交易类型", "交易余额"], ["SYN-REF", "Transfer", "99"], accepted=True) == {}
    assert events.LEDGER_FIELD not in events.parse_fields(headers, values, accepted=False)
    for invalid in ["0", "-1", "1.0", "1e3", " 1", "1" * 39]:
        result = events.parse_fields(["记账顺序号"], [invalid], accepted=True, posting_scope=1)
        assert result == {}


@pytest.mark.parametrize("scope", [None, "1", 0, -1, True, 10 ** 38])
def test_posting_order_requires_an_actual_positive_header_region(scope):
    assert events.parse_fields(["记账顺序号"], ["1"], accepted=True, posting_scope=scope) == {}


@pytest.mark.parametrize("change", [{"posting_scope": ""}, {"posting_scope": 1}, {"posting_scope": "0"},
                                      {"posting_scope": "1.0"}, {"posting_scope": "1" * 39}, {"extra": "scope"}])
def test_invalid_posting_scope_is_rejected(change):
    value = {"policy": events.LEDGER_POLICY, "balance_kind": "unknown", "posting_order": "1", "posting_scope": "1", **change}
    with pytest.raises(ValueError):
        events.validate_ledger(json.dumps(value))


@pytest.mark.parametrize("field,value", [("reference", ""), ("issuer", " x"), ("channel", "x\n"), ("business_scope", "x" * 129), ("reference", 1), ("reference", "NULL"), ("reference", "--")])
def test_invalid_reference_values_rejected(field, value):
    data = json.loads(observed("1", "SYN-A")["evidence"])
    data[field] = value
    with pytest.raises(ValueError):
        events.validate_reference(json.dumps(data))


def test_excluded_reference_entries_do_not_make_effective_denominator_negative(database, make_case, monkeypatch):
    cid = make_case("Synthetic excluded reference")
    database.x("insert into flows(id,case_id,payer_account,payee_account,self_account,amount,currency) values (1,?,'SYN-A','SYN-B','SYN-A',100,'CNY')", [cid])
    monkeypatch.setattr(events, "_observations", lambda _: [observed("1", "SYN-A"), observed("2", "SYN-B", effective=False)])
    with database.transaction():
        result = events.rebuild(cid)
    assert (result["observed_entries"], result["conflict_entries"], result["unknown_entries"], result["excluded_reference_entries"]) == (1, 1, 0, 1)
    assert result["confirmed_events"] == 0


def test_rebuild_releases_proof_inputs_before_writing_projection(database, make_case, monkeypatch):
    from weakref import ref
    from app import bank_posting_pairs

    class Observation(dict):
        pass

    cid = make_case("Synthetic projection lifetime")
    database.x("insert into flows(id,case_id,amount,currency) values (1,?,100,'CNY')", [cid])
    retained = []

    def bank_rows(*args, **kwargs):
        rows = [Observation(flow_id=1, effective=True), Observation(flow_id="1", effective=False),
                Observation(flow_id=2, effective=False), Observation(flow_id=2, effective=False),
                Observation(flow_id=None, effective=False)]
        retained.extend(ref(row) for row in rows)
        return [], [{"flow_id": "1", "event_id": None, "status": "conflict", "reason": "synthetic"},
                    {"flow_id": "2", "event_id": None, "status": "conflict", "reason": "synthetic"}], rows

    write = events._write_projection

    def observed_write(*args):
        assert len(retained) == 5 and all(item() is None for item in retained)
        return write(*args)

    monkeypatch.setattr(bank_posting_pairs, "has_sources", lambda case: True)
    monkeypatch.setattr(bank_posting_pairs, "project", bank_rows)
    monkeypatch.setattr(events, "_write_projection", observed_write)
    with database.transaction():
        result = events.rebuild(cid)
    assert (result["reference_observations"], result["excluded_reference_entries"],
            result["unresolved_reference_observations"], result["conflict_entries"], result["unknown_entries"]) == (5, 1, 1, 1, 0)


def projection_fixture(literals):
    observations = []
    stamp = datetime(2036, 2, 3, 4, 5, 6, 123456)
    for index, literal in enumerate(literals):
        currency = ("CNY", "USD", "EUR")[index % 3]
        evidence = json.loads(observed("1", "SYN-A")["evidence"])
        evidence["reference"] = f"SYN-PROJECTION-{index:06d}"
        for side, own in enumerate(("SYN-A", "SYN-B")):
            fid = 9007199254740993 + index * 2 + side
            observations.append(observed(str(fid), own, amount=Decimal(literal), currency=currency,
                trade_time=stamp, evidence=json.dumps(evidence)))
    resolved, memberships = events.resolve(observations)
    return resolved, memberships, stamp


def test_event_projection_default_decimal_scan_keeps_large_tail_exact(database, make_case):
    from app import evidence_resolution
    cid = make_case("SYN exact event Decimal scanner boundary")
    literals = ["123456789.12"] * 4997 + ["1234567890.13", "9007199254740993.13", "9999999999999999.99"]
    candidates, memberships, stamp = projection_fixture(literals)
    sample = database.q_raw("select current_setting('pandas_analyze_sample') sample_size")[0]["sample_size"]
    assert sample == 1000
    assert len(literals) < evidence_resolution.WRITE_CHUNK
    amounts = [(id(row["amount"]), row["amount"].as_tuple()) for row in candidates]
    with database.transaction():
        events._write_projection(cid, candidates, memberships)
    stored = database.q_raw("select * from resolved_events where case_id=?", [cid])
    assert len(stored) == len(literals)
    expected_events = {row["event_id"]: row for row in candidates}
    for row in stored:
        candidate = expected_events[row["event_id"]]
        reference = json.loads(row["evidence_json"])["reference"]
        index = int(reference.rsplit("-", 1)[1])
        assert type(row["amount"]) is Decimal
        assert row["amount"].as_tuple() == Decimal(literals[index]).as_tuple()
        assert row["currency"] == ("CNY", "USD", "EUR")[index % 3]
        assert row["trade_time"] == stamp
        assert row["payer_account"] == candidate["payer_account"] == "SYN-A"
        assert row["payee_account"] == candidate["payee_account"] == "SYN-B"
        assert row["evidence_json"] == candidate["evidence_json"]
    members = database.q_raw("select flow_id,event_id,status from event_members where case_id=? order by flow_id", [cid])
    assert [row["flow_id"] for row in members] == [9007199254740993 + index for index in range(len(literals) * 2)]
    assert all(row["event_id"] in expected_events and row["status"] == "confirmed" for row in members)
    assert [(id(row["amount"]), row["amount"].as_tuple()) for row in candidates] == amounts
    assert all(type(row["amount"]) is Decimal for row in candidates)
    assert database.q_raw("select current_setting('pandas_analyze_sample') sample_size") == [{"sample_size": sample}]


def test_late_event_member_batch_failure_restores_previous_projection(database, make_case, monkeypatch):
    from app import evidence_resolution
    cid = make_case("SYN event projection late batch rollback")
    literals = ["0.13", "1234567890.13", "9007199254740993.13", "9999999999999999.99", "7.53"]
    candidates, memberships, _ = projection_fixture(literals)
    old_candidates, old_memberships, _ = projection_fixture(["100.00"])
    with database.transaction():
        events._write_projection(cid, old_candidates, old_memberships)
    before = {table: database.q_raw(f"select * from {table} where case_id=? order by 1,2", [cid])
              for table in ("resolved_events", "event_members")}
    # A small write chunk forces a late failure; scanner sampling is untouched.
    monkeypatch.setattr(evidence_resolution, "WRITE_CHUNK", 2)
    original_insert, batches = database.insert_dataframe, []

    def fail_late(table, frame, columns):
        batches.append((table, len(frame)))
        if table == "event_members" and sum(name == table for name, _ in batches) == 2:
            raise RuntimeError("SYN late event member batch")
        return original_insert(table, frame, columns)

    monkeypatch.setattr(database, "insert_dataframe", fail_late)
    with pytest.raises(RuntimeError, match="SYN late event member batch"):
        with database.transaction():
            database.x("delete from event_members where case_id=?", [cid])
            database.x("delete from resolved_events where case_id=?", [cid])
            events._write_projection(cid, candidates, memberships)
    assert batches == [("resolved_events", 2), ("resolved_events", 2), ("resolved_events", 1),
                       ("event_members", 2), ("event_members", 2)]
    assert {table: database.q_raw(f"select * from {table} where case_id=? order by 1,2", [cid]) for table in before} == before
