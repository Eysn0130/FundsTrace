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
