"""Independent small-ledger oracles for exact conditional source allocation."""
from copy import deepcopy
import random

import pytest

from app.principal import allocate_ledger


def credit(entry_id, amount, post_balance=None):
    return {"id": entry_id, "direction": "in", "amount": amount, "post_balance": post_balance}


def debit(entry_id, amount, post_balance=None):
    return {"id": entry_id, "direction": "out", "amount": amount, "post_balance": post_balance}


def check_witness(opening, entries, sources, witness):
    assert witness["opening_balance"] == opening
    assert len(witness["entries"]) == len(entries)
    balance, source_balance = opening, 0
    allocation = {}
    for expected, actual in zip(entries, witness["entries"]):
        for field in ("id", "direction", "amount"):
            assert actual[field] == expected[field]
        source, other = actual["source_amount"], actual["other_amount"]
        assert type(source) is type(other) is int
        assert source >= 0 and other >= 0
        assert source + other == expected["amount"]
        if expected["direction"] == "in":
            assert source == (expected["amount"] if expected["id"] in sources else 0)
            balance += expected["amount"]
            source_balance += source
        else:
            assert source <= source_balance
            assert other <= balance - source_balance
            balance -= expected["amount"]
            source_balance -= source
            allocation[expected["id"]] = source
        assert 0 <= source_balance <= balance
        assert actual["balance"] == balance
        assert actual["source_balance"] == source_balance
        if expected.get("post_balance") is not None:
            assert expected["post_balance"] == balance
    assert witness["closing_balance"] == balance
    assert witness["closing_source_balance"] == source_balance
    return allocation


def check_result(opening, entries, sources, result):
    assert result["status"] == "ok"
    fifo = check_witness(opening, entries, sources, result["fifo_witness"])
    assert result["closing_balance"] == result["fifo_witness"]["closing_balance"]
    for target in result["targets"]:
        assert 0 <= target["min"] <= target["fifo"] <= target["max"] <= target["amount"]
        assert fifo[target["id"]] == target["fifo"]
        for extreme in ("min", "max"):
            allocation = check_witness(opening, entries, sources, target[f"{extreme}_witness"])
            assert allocation[target["id"]] == target[extreme]


@pytest.mark.parametrize(("opening", "entries", "expected"), [
    (0, [credit("A", 100), debit("C", 100)], (100, 100, 100)),
    (1000, [credit("A", 100), debit("C", 100)], (0, 100, 0)),
    (40, [credit("A", 100), debit("D", 50), debit("C", 90)], (50, 90, 90)),
    (0, [credit("A", 100), debit("D", 100), credit("O", 100), debit("C", 100)], (0, 0, 0)),
])
def test_independently_derived_source_ranges_and_fifo(opening, entries, expected):
    result = allocate_ledger(opening, entries, {"A"}, ["C"])
    check_result(opening, entries, {"A"}, result)
    target = result["targets"][0]
    assert (target["min"], target["max"], target["fifo"]) == expected


def test_arbitrarily_large_integer_amounts_remain_exact():
    amount = 10**40 + 1
    entries = [credit("A", amount), debit("C", amount + 4)]
    result = allocate_ledger(7, entries, {"A"}, ["C"])
    check_result(7, entries, {"A"}, result)
    target = result["targets"][0]
    assert (target["min"], target["max"], target["fifo"]) == (amount - 3, amount, amount - 3)


def test_separately_booked_fee_consumes_source_capacity():
    entries = [credit("A", 100), debit("fee", 3), debit("C", 97)]
    result = allocate_ledger(0, entries, {"A"}, ["C", "fee"])
    check_result(0, entries, {"A"}, result)
    assert [(t["id"], t["min"], t["max"], t["fifo"]) for t in result["targets"]] == [
        ("C", 97, 97, 97), ("fee", 3, 3, 3)]


def test_ordinary_refund_is_an_outgoing_payment_not_source_cancellation():
    entries = [credit("A", 100), debit("refund", 100), debit("C", 10)]
    result = allocate_ledger(10, entries, {"A"}, ["C"])
    check_result(10, entries, {"A"}, result)
    target = result["targets"][0]
    assert (target["min"], target["max"], target["fifo"]) == (0, 10, 10)
    used_then_refunded = [credit("A", 100), debit("C", 100), credit("O", 100), debit("refund", 100)]
    later = allocate_ledger(0, used_then_refunded, {"A"}, ["C"])
    check_result(0, used_then_refunded, {"A"}, later)
    assert (later["targets"][0]["min"], later["targets"][0]["max"]) == (100, 100)


def test_independent_target_maxima_are_not_a_joint_allocation():
    entries = [credit("A", 100), debit("D", 100), debit("C", 100)]
    result = allocate_ledger(100, entries, {"A"}, ["D", "C"])
    check_result(100, entries, {"A"}, result)
    assert [(t["min"], t["max"]) for t in result["targets"]] == [(0, 100), (0, 100)]
    assert sum(t["max"] for t in result["targets"]) == 200
    for target in result["targets"]:
        for extreme in ("min", "max"):
            allocation = check_witness(100, entries, {"A"}, target[f"{extreme}_witness"])
            assert sum(allocation.values()) == 100


def test_multiple_incoming_sources_are_one_selected_pool_without_mutating_inputs():
    entries = [credit("A", 10), credit("O", 5), credit("B", 20), debit("C", 30, 5)]
    original = deepcopy(entries)
    sources, targets = {"A", "B"}, ["C"]
    result = allocate_ledger(0, entries, sources, targets)
    check_result(0, entries, sources, result)
    assert (result["targets"][0]["min"], result["targets"][0]["max"], result["targets"][0]["fifo"]) == (25, 30, 25)
    assert entries == original and sources == {"A", "B"} and targets == ["C"]


def test_no_source_can_fund_earlier_entries_and_zero_entries_are_preserved():
    entries = [debit("before", 5), credit("A", 7), credit("zero", 0), debit("zero-out", 0), debit("C", 7)]
    result = allocate_ledger(5, entries, {"A", "zero"}, ["before", "zero-out", "C"])
    check_result(5, entries, {"A", "zero"}, result)
    assert [(t["min"], t["max"]) for t in result["targets"]] == [(0, 0), (0, 0), (7, 7)]


@pytest.mark.parametrize(("entries", "reason", "entry_id"), [
    ([credit("A", 100, 100), debit("C", 100, 50)], "post_balance_mismatch", "C"),
    ([debit("D", 1), credit("A", 100), debit("C", 99)], "insufficient_funds", "D"),
    ([credit("A", 100), debit("C", 100), debit("later", 1)], "insufficient_funds", "later"),
    ([credit("A", 100), debit("C", 100), credit("later", 1, 2)], "post_balance_mismatch", "later"),
])
def test_inconsistent_ledger_never_returns_zero_attribution_even_after_target(entries, reason, entry_id):
    result = allocate_ledger(0, entries, {"A"}, ["C"])
    assert result["status"] == "inconsistency"
    assert result["reason"] == reason and result["entry_id"] == entry_id
    assert result["targets"] == []
    assert "fifo_witness" not in result


@pytest.mark.parametrize(("field", "invalid"), [
    (field, invalid)
    for field in ("opening", "amount", "post_balance")
    for invalid in (True, False, -1, 1.0, "1", None)
    if not (field == "post_balance" and invalid is None)
])
def test_rejects_non_integer_and_negative_money(field, invalid):
    opening, entries = 0, [credit("A", 1), debit("C", 1)]
    if field == "opening":
        opening = invalid
    else:
        entries[0][field] = invalid
    with pytest.raises(ValueError):
        allocate_ledger(opening, entries, {"A"}, ["C"])


@pytest.mark.parametrize(("entries", "sources", "targets"), [
    ([credit("A", 1), debit("A", 1)], {"A"}, []),
    ([credit("A", 1), debit("C", 1)], {"C"}, ["C"]),
    ([credit("A", 1), debit("C", 1)], {"A"}, ["A"]),
    ([credit("A", 1), debit("C", 1)], {"missing"}, ["C"]),
    ([credit("A", 1), debit("C", 1)], {"A"}, ["missing"]),
    ([credit("A", 1), debit("C", 1)], {"A"}, ["C", "C"]),
    ([credit("", 1)], set(), []),
    ([credit(True, 1)], set(), []),
    ([{"id": "A", "direction": "refund", "amount": 1}], set(), []),
    ([{"id": "A", "direction": "in"}], set(), []),
    ([credit("A", 1)], ["A"], []),
    ([credit("A", 1)], {"A"}, set()),
])
def test_rejects_invalid_entries_or_source_target_ids(entries, sources, targets):
    with pytest.raises(ValueError):
        allocate_ledger(0, entries, sources, targets)


def test_empty_ledger_and_empty_selected_source_pool():
    empty = allocate_ledger(7, [], set(), [])
    check_result(7, [], set(), empty)
    assert empty["closing_balance"] == 7
    entries = [credit("A", 10), debit("C", 10)]
    result = allocate_ledger(0, entries, set(), ["C"])
    check_result(0, entries, set(), result)
    assert (result["targets"][0]["min"], result["targets"][0]["max"]) == (0, 0)


def enumerate_small_ledger(opening, entries, sources):
    """Test-only exhaustive monetary-unit choices; never used by production."""
    states = [(0, {})]
    balance = opening
    for entry in entries:
        amount = entry["amount"]
        if entry["direction"] == "in":
            balance += amount
            added = amount if entry["id"] in sources else 0
            states = [(source + added, allocations) for source, allocations in states]
        else:
            next_states = []
            for source, allocations in states:
                for share in range(amount + 1):
                    if share <= source and amount - share <= balance - source:
                        next_states.append((source - share, {**allocations, entry["id"]: share}))
            states = next_states
            balance -= amount
        if entry.get("post_balance") is not None and entry["post_balance"] != balance:
            return []
    return states


def fifo_small_units(opening, entries, sources):
    """A deliberately different test oracle: a queue of individual tiny units."""
    units, allocated = [False] * opening, {}
    for entry in entries:
        if entry["direction"] == "in":
            units.extend([entry["id"] in sources] * entry["amount"])
        else:
            allocated[entry["id"]] = sum(units[:entry["amount"]])
            del units[:entry["amount"]]
    return allocated


def test_random_small_ledgers_match_all_feasible_allocations_and_fifo_unit_oracle():
    rng = random.Random(20261026)
    for _ in range(180):
        opening = rng.randrange(5)
        balance, entries, sources, targets = opening, [], set(), []
        for index in range(rng.randrange(1, 8)):
            entry_id = str(index)
            if rng.random() < 0.5:
                amount = rng.randrange(5)
                entries.append(credit(entry_id, amount))
                balance += amount
                if rng.random() < 0.5:
                    sources.add(entry_id)
            else:
                amount = rng.randrange(min(balance, 4) + 1)
                entries.append(debit(entry_id, amount))
                balance -= amount
                targets.append(entry_id)
            if rng.random() < 0.4:
                entries[-1]["post_balance"] = balance
        feasible = enumerate_small_ledger(opening, entries, sources)
        assert feasible
        result = allocate_ledger(opening, entries, sources, targets)
        check_result(opening, entries, sources, result)
        fifo = fifo_small_units(opening, entries, sources)
        for target in result["targets"]:
            values = [allocation[target["id"]] for _, allocation in feasible]
            assert (target["min"], target["max"], target["fifo"]) == (
                min(values), max(values), fifo[target["id"]])
