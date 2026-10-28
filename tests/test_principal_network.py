"""Small independent multi-ledger enumeration and conserving-witness checks."""
from collections import defaultdict
from copy import deepcopy
import random

import pytest

from app.principal_network import allocate_network


def event(event_id, payer, payee, amount, **balances):
    return {"id": event_id, "payer": payer, "payee": payee, "amount": amount, **balances}


def check_witness(opening, events, source_id, witness):
    by_id = {item["id"]: item for item in events}
    principal = by_id[source_id]["amount"]
    assert witness["source_amount"] == principal
    mass = defaultdict(int)
    edge_ids = set()
    for edge in witness["edge_flows"]:
        assert edge["edge_id"] not in edge_ids
        edge_ids.add(edge["edge_id"])
        assert type(edge["amount"]) is type(edge["capacity"]) is int
        assert 0 < edge["amount"] <= edge["capacity"]
        mass[edge["from"]] -= edge["amount"]
        mass[edge["to"]] += edge["amount"]
        assert edge["to"] == witness["sink_node"] or int(edge["from"]) < int(edge["to"])
        if edge["kind"] in ("transfer", "external", "injection"):
            assert edge["capacity"] == by_id[edge["event_id"]]["amount"]
    assert mass[witness["source_node"]] == -principal
    assert mass[witness["sink_node"]] == principal
    assert all(value == 0 for node, value in mass.items()
               if node not in (witness["source_node"], witness["sink_node"]))

    balances, selected = dict(opening), {account: 0 for account in opening}
    external = 0
    assert set(witness["event_shares"]) <= by_id.keys()
    for item in events:
        payer, payee, amount = item["payer"], item["payee"], item["amount"]
        share = witness["event_shares"].get(item["id"], 0)
        assert type(share) is int and 0 <= share <= amount
        if item["id"] == source_id:
            assert share == principal
            assert not any(selected.values())
            if payer is not None:
                assert amount <= balances[payer]
                balances[payer] -= amount
        elif payer is not None:
            assert share <= selected[payer]
            assert amount - share <= balances[payer] - selected[payer]
            balances[payer] -= amount
            selected[payer] -= share
        else:
            assert share == 0
        if payee is not None:
            balances[payee] += amount
            selected[payee] += share
        else:
            external += share
        for account in opening:
            assert 0 <= selected[account] <= balances[account]
        for party in ("payer", "payee"):
            if item.get(f"{party}_post_balance") is not None:
                assert balances[item[party]] == item[f"{party}_post_balance"]
    assert witness["closing_shares"] == {account: share for account, share in selected.items() if share}
    assert witness["external_total"] == external
    assert witness["retained_total"] == sum(selected.values())
    assert witness["external_total"] + witness["retained_total"] == principal
    return witness["event_shares"]


def check_result(opening, events, source_id, result):
    assert result["status"] == "ok", result
    fifo = check_witness(opening, events, source_id, result["fifo_witness"])
    for target in result["targets"]:
        assert 0 <= target["min"] <= target["fifo"] <= target["max"] <= target["amount"]
        assert target["fifo"] == fifo.get(target["id"], 0)
        for extreme in ("min", "max"):
            witness = check_witness(opening, events, source_id, target[f"{extreme}_witness"])
            assert witness.get(target["id"], 0) == target[extreme]


def test_mixed_diamond_split_and_merge_has_exact_joint_capacity():
    opening = {"A": 0, "B": 6, "C": 0, "D": 0}
    events = [event("source", None, "A", 10), event("AB", "A", "B", 6),
              event("AC", "A", "C", 4), event("BD", "B", "D", 6),
              event("CD", "C", "D", 4), event("exit", "D", None, 10)]
    result = allocate_network(opening, events, "source", ["BD", "exit"])
    check_result(opening, events, "source", result)
    assert [(t["min"], t["max"], t["fifo"]) for t in result["targets"]] == [(0, 6, 0), (4, 10, 4)]


def test_all_source_diamond_never_duplicates_principal():
    opening = dict.fromkeys("ABCD", 0)
    events = [event("source", None, "A", 10), event("AB", "A", "B", 6),
              event("AC", "A", "C", 4), event("BD", "B", "D", 6),
              event("CD", "C", "D", 4), event("exit", "D", None, 10)]
    result = allocate_network(opening, events, "source", ["AB", "BD", "CD", "exit"])
    check_result(opening, events, "source", result)
    assert [(t["min"], t["max"], t["fifo"]) for t in result["targets"]] == [
        (6, 6, 6), (6, 6, 6), (4, 4, 4), (10, 10, 10)]
    assert result["fifo_witness"]["external_total"] == 10
    assert result["fifo_witness"]["retained_total"] == 0


def test_independent_exit_upper_bounds_cannot_be_summed():
    opening = {"A": 10, "B": 0, "C": 0}
    events = [event("source", None, "A", 10), event("AB", "A", "B", 10),
              event("AC", "A", "C", 10), event("B-exit", "B", None, 10), event("C-exit", "C", None, 10)]
    result = allocate_network(opening, events, "source", ["B-exit", "C-exit"])
    check_result(opening, events, "source", result)
    assert [(t["min"], t["max"], t["fifo"]) for t in result["targets"]] == [(0, 10, 0), (0, 10, 10)]
    assert sum(t["max"] for t in result["targets"]) == 20
    for target in result["targets"]:
        for key in ("min_witness", "max_witness"):
            assert target[key]["external_total"] == 10
            assert target[key]["retained_total"] == 0


def test_time_ordered_return_and_repayment_preserve_prior_source_use():
    opening = {"A": 0, "B": 0, "C": 0}
    events = [event("source", None, "A", 10), event("AB", "A", "B", 10),
              event("return", "B", "A", 10), event("AC", "A", "C", 10),
              event("other", None, "A", 10), event("refund", "A", None, 10)]
    result = allocate_network(opening, events, "source", ["AB", "return", "AC", "refund"])
    check_result(opening, events, "source", result)
    assert [(t["min"], t["max"]) for t in result["targets"]] == [(10, 10), (10, 10), (10, 10), (0, 0)]
    assert result["fifo_witness"]["closing_shares"] == {"C": 10}


def test_fee_and_later_unrelated_funds_do_not_recreate_exhausted_source():
    opening = {"A": 0, "B": 0}
    events = [event("source", None, "A", 100), event("fee", "A", None, 3),
              event("exit", "A", None, 97), event("other", None, "A", 100), event("AB", "A", "B", 100)]
    result = allocate_network(opening, events, "source", ["fee", "exit", "AB"])
    check_result(opening, events, "source", result)
    assert [(t["min"], t["max"], t["fifo"]) for t in result["targets"]] == [(3, 3, 3), (97, 97, 97), (0, 0, 0)]


def test_internal_source_event_and_self_transfer_use_real_available_balance():
    opening = {"A": 7, "B": 0}
    events = [event("source", "A", "B", 5, payer_post_balance=2, payee_post_balance=5),
              event("BA", "B", "A", 5), event("self", "A", "A", 6, payer_post_balance=7, payee_post_balance=7),
              event("exit", "A", None, 7)]
    result = allocate_network(opening, events, "source", ["self", "exit"])
    check_result(opening, events, "source", result)
    assert [(t["min"], t["max"], t["fifo"]) for t in result["targets"]] == [(4, 5, 4), (5, 5, 5)]


def test_source_cannot_retroactively_fund_an_earlier_transfer():
    opening = {"A": 10, "B": 0, "C": 0}
    events = [event("before", "A", "B", 10), event("source", None, "A", 10),
              event("other-BC", "B", "C", 10), event("AB", "A", "B", 10), event("source-BC", "B", "C", 10)]
    result = allocate_network(opening, events, "source", ["other-BC", "source-BC"])
    check_result(opening, events, "source", result)
    assert [(t["min"], t["max"], t["fifo"]) for t in result["targets"]] == [(0, 0, 0), (10, 10, 10)]


def test_large_amounts_are_exact_and_inputs_are_not_mutated():
    amount = 10**40 + 7
    opening = {"A": 3, "B": 0}
    events = [event("source", None, "A", amount), event("AB", "A", "B", amount)]
    original = deepcopy((opening, events))
    result = allocate_network(opening, events, "source", ["AB"])
    check_result(opening, events, "source", result)
    target = result["targets"][0]
    assert (target["min"], target["max"], target["fifo"]) == (amount - 3, amount, amount - 3)
    assert (opening, events) == original


@pytest.mark.parametrize(("events", "bad_id", "reason"), [
    ([event("source", None, "B", 5), event("AB", "A", "B", 5), event("BA", "B", "A", 5)], "AB", "insufficient_funds"),
    ([event("source", "A", "B", 5), event("BA", "B", "A", 5)], "source", "insufficient_funds"),
    ([event("source", None, "A", 5), event("AB", "A", "B", 5), event("late", "B", None, 6)], "late", "insufficient_funds"),
    ([event("source", None, "A", 5), event("AB", "A", "B", 5, payee_post_balance=4)], "AB", "post_balance_mismatch"),
    ([event("source", None, "A", 5), event("AB", "A", "B", 5), event("late", None, "B", 1, payee_post_balance=7)], "late", "post_balance_mismatch"),
])
def test_total_ledger_inconsistency_is_checked_before_source_allocation(events, bad_id, reason):
    result = allocate_network({"A": 0, "B": 0}, events, "source", [])
    assert result["status"] == "inconsistency"
    assert result["event_id"] == bad_id and result["reason"] == reason
    assert result["targets"] == [] and "fifo_witness" not in result


def test_zero_source_returns_zero_shares_without_inventing_money():
    opening = {"A": 5}
    events = [event("source", None, "A", 0), event("exit", "A", None, 5)]
    result = allocate_network(opening, events, "source", ["exit"])
    check_result(opening, events, "source", result)
    assert result["targets"][0]["min"] == result["targets"][0]["max"] == 0


def test_size_and_step_exhaustion_return_no_partial_exact_result():
    opening = {"A": 5, "B": 0}
    events = [event("source", None, "A", 5), event("AB", "A", "B", 5), event("exit", "B", None, 5)]
    targets = ["AB", "exit"]
    full = allocate_network(opening, events, "source", targets)
    check_result(opening, events, "source", full)
    for options, reason in [({"max_events": 1}, "event_limit"), ({"max_targets": 1}, "target_limit"),
                            ({"max_accounts": 1}, "account_limit"), ({"max_steps": 1}, "step_budget"),
                            ({"max_steps": full["steps"] - 1}, "step_budget")]:
        result = allocate_network(opening, events, "source", targets, **options)
        assert result["status"] == "limited" and result["reason"] == reason
        assert result["targets"] == [] and "fifo_witness" not in result


@pytest.mark.parametrize("field", ["opening", "amount", "payer_post_balance", "payee_post_balance"])
@pytest.mark.parametrize("value", [True, -1, 1.0, "1"])
def test_invalid_money_is_rejected(field, value):
    opening = {"A": 5, "B": 0}
    events = [event("source", "A", "B", 5)]
    if field == "opening":
        opening["A"] = value
    else:
        events[0][field] = value
    with pytest.raises(ValueError):
        allocate_network(opening, events, "source", [])


@pytest.mark.parametrize(("opening", "events", "source", "targets"), [
    ({"A": 0}, [event("source", None, "B", 1)], "source", []),
    ({"A": 0}, [event("source", None, "A", 1), event("source", "A", None, 1)], "source", []),
    ({"A": 1}, [event("source", "A", "A", 1)], "source", []),
    ({"A": 1}, [event("source", "A", None, 1)], "source", []),
    ({"A": 0}, [event("source", None, None, 1)], "source", []),
    ({"A": 0}, [{"id": "source", "payee": "A", "amount": 1}], "source", []),
    ({"A": 0}, [event("source", None, "A", 1, payer_post_balance=0)], "source", []),
    ({"A": 0}, [event("source", None, "A", 1)], "missing", []),
    ({"A": 0}, [event("source", None, "A", 1)], "source", ["source"]),
    ({"A": 0}, [event("source", None, "A", 1), event("later", None, "A", 1)], "source", ["later"]),
    ({"A": 0}, [event("source", None, "A", 1), event("exit", "A", None, 1)], "source", ["exit", "exit"]),
])
def test_invalid_accounts_event_ids_and_target_roles_fail_closed(opening, events, source, targets):
    with pytest.raises(ValueError):
        allocate_network(opening, events, source, targets)


def enumerate_small_network(opening, events, source_id):
    """Exhaustive test-only choices of source units at each outgoing payment."""
    balances = dict(opening)
    states = [({account: 0 for account in opening}, {})]
    for item in events:
        payer, payee, amount = item["payer"], item["payee"], item["amount"]
        next_states = []
        for selected, history in states:
            if item["id"] == source_id:
                choices = [amount]
            elif payer is None:
                choices = [0]
            else:
                choices = [share for share in range(amount + 1)
                           if share <= selected[payer] and amount - share <= balances[payer] - selected[payer]]
            for share in choices:
                updated = dict(selected)
                if payer is not None and item["id"] != source_id:
                    updated[payer] -= share
                if payee is not None:
                    updated[payee] += share
                next_states.append((updated, {**history, item["id"]: share}))
        states = next_states
        if payer is not None:
            balances[payer] -= amount
        if payee is not None:
            balances[payee] += amount
    return states


def fifo_small_units(opening, events, source_id):
    queues, shares = {account: [False] * amount for account, amount in opening.items()}, {}
    for item in events:
        payer, payee, amount = item["payer"], item["payee"], item["amount"]
        if payer is None:
            moved = [False] * amount
        else:
            moved = queues[payer][:amount]
            del queues[payer][:amount]
        if item["id"] == source_id:
            moved = [True] * amount
        shares[item["id"]] = sum(moved)
        if payee is not None:
            queues[payee].extend(moved)
    return shares


def test_random_small_networks_match_independent_allocation_and_fifo_oracles():
    rng = random.Random(20261027)
    for _ in range(140):
        opening = {account: rng.randrange(4) for account in "ABC"}
        balances = dict(opening)
        source_payer = rng.choice([None, "B"])
        amount = rng.randrange(4 if source_payer is None else min(3, balances[source_payer]) + 1)
        events = [event("source", source_payer, "A", amount)]
        balances["A"] += amount
        if source_payer is not None:
            balances[source_payer] -= amount
        targets = []
        for index in range(rng.randrange(1, 7)):
            payer = rng.choice([None, "A", "B", "C"])
            payee = rng.choice(list("ABC") if payer is None else [None, "A", "B", "C"])
            amount = rng.randrange(4 if payer is None else min(3, balances[payer]) + 1)
            item = event(str(index), payer, payee, amount)
            if payer is not None:
                balances[payer] -= amount
                targets.append(str(index))
            if payee is not None:
                balances[payee] += amount
            if payer is not None and rng.random() < 0.3:
                item["payer_post_balance"] = balances[payer]
            if payee is not None and rng.random() < 0.3:
                item["payee_post_balance"] = balances[payee]
            events.append(item)
        feasible = enumerate_small_network(opening, events, "source")
        assert feasible
        result = allocate_network(opening, events, "source", targets)
        check_result(opening, events, "source", result)
        fifo = fifo_small_units(opening, events, "source")
        for target in result["targets"]:
            shares = [history[target["id"]] for _, history in feasible]
            assert (target["min"], target["max"], target["fifo"]) == (
                min(shares), max(shares), fifo[target["id"]])


def test_long_time_ordered_chain_does_not_depend_on_python_recursion_depth():
    opening = {"A": 0, "B": 0}
    events = [event("source", None, "A", 1)]
    for index in range(2500):
        payer, payee = ("A", "B") if index % 2 == 0 else ("B", "A")
        events.append(event(str(index), payer, payee, 1))
    result = allocate_network(opening, events, "source", ["2499"])
    check_result(opening, events, "source", result)
    target = result["targets"][0]
    assert (target["min"], target["max"], target["fifo"]) == (1, 1, 1)
