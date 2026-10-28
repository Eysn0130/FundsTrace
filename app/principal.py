"""Conditional source attribution for an ordered, single-currency ledger model.

The caller establishes the model's account/subaccount, included entries, opening
balance, order, and post-balance meaning. Book balances support a recorded book
model, not historical availability. Every modeled fee and competing transaction
participates. Nonnegative capacity is a model constraint, not evidence of credit
terms or freezes. No missing entries, FX, unknown openings or unresolved reversal
semantics are inferred here. Opening funds belong to other sources. Selected
incoming entries form one source pool.

Amounts are exact nonnegative Python integers in one caller-supplied scale
(FundsTrace uses cents, scale 2). This module does not read a database or apply
display filters. A feasible result is conditional on the stated ledger model,
not evidence that those premises hold in the source documents.
"""
from collections import deque


RULE_VERSION = "principal-ledger.v1"


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def _validate(opening, entries, source_ids, target_ids):
    _integer(opening, "opening")
    if not isinstance(entries, list):
        raise ValueError("entries must be a list")
    if not isinstance(source_ids, (set, frozenset)):
        raise ValueError("source_ids must be a set")
    if not isinstance(target_ids, list):
        raise ValueError("target_ids must be a list")
    by_id = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each entry must be a dictionary")
        entry_id = entry.get("id")
        _identifier(entry_id, "entry id")
        if entry_id in by_id:
            raise ValueError("entry ids must be unique")
        if entry.get("direction") not in ("in", "out"):
            raise ValueError("entry direction must be in or out")
        _integer(entry.get("amount"), "entry amount")
        if entry.get("post_balance") is not None:
            _integer(entry["post_balance"], "post_balance")
        by_id[entry_id] = entry
    seen_targets = set()
    for ids, direction, label in ((source_ids, "in", "source"), (target_ids, "out", "target")):
        for entry_id in ids:
            _identifier(entry_id, f"{label} id")
            if entry_id not in by_id or by_id[entry_id]["direction"] != direction:
                raise ValueError(f"each {label} id must identify an {direction} entry")
            if label == "target":
                if entry_id in seen_targets:
                    raise ValueError("target ids must be unique")
                seen_targets.add(entry_id)


def _witness(opening, entries, source_ids, outgoing):
    """Materialize an aggregate source/other allocation for the whole interval."""
    balance, source_balance = opening, 0
    records = []
    for index, entry in enumerate(entries):
        amount = entry["amount"]
        incoming = entry["direction"] == "in"
        source_amount = (amount if entry["id"] in source_ids else 0) if incoming else outgoing[index]
        sign = 1 if incoming else -1
        balance += sign * amount
        source_balance += sign * source_amount
        records.append({"id": entry["id"], "direction": entry["direction"], "amount": amount,
                        "source_amount": source_amount, "other_amount": amount - source_amount,
                        "balance": balance, "source_balance": source_balance})
    return {"opening_balance": opening, "entries": records, "closing_balance": balance,
            "closing_source_balance": source_balance}


def _extreme_witness(opening, entries, source_ids, states, target_index, target_amount):
    """Backtrack a feasible prefix, then extend it through every later entry.

    A state is (balance before, minimum source before, maximum source before).
    Reachable source balances form an integer interval. At a debit of d, a
    chosen remaining source x can have any predecessor in [x, x+d] intersected
    with the prior interval. At the target, choose a predecessor capable of
    funding its requested extreme; no later entry can require a specific source
    because only total balances constrain this model.
    """
    outgoing = [0] * len(entries)
    outgoing[target_index] = target_amount
    _, lower, _ = states[target_index]
    source_before = max(lower, target_amount)
    source_after = source_before - target_amount
    remaining = source_before
    for index in range(target_index - 1, -1, -1):
        entry = entries[index]
        if entry["direction"] == "in":
            if entry["id"] in source_ids:
                remaining -= entry["amount"]
        else:
            predecessor = max(states[index][1], remaining)
            outgoing[index] = predecessor - remaining
            remaining = predecessor
    remaining = source_after
    for index in range(target_index + 1, len(entries)):
        entry = entries[index]
        if entry["direction"] == "in":
            if entry["id"] in source_ids:
                remaining += entry["amount"]
        else:
            balance_after = states[index][0] - entry["amount"]
            outgoing[index] = max(0, remaining - balance_after)
            remaining -= outgoing[index]
    return _witness(opening, entries, source_ids, outgoing)


def _fifo(opening, entries, source_ids):
    """Consume compact source/other lots; each lot is removed at most once."""
    lots = deque([[False, opening]]) if opening else deque()
    outgoing = [0] * len(entries)
    for index, entry in enumerate(entries):
        amount = entry["amount"]
        if entry["direction"] == "in":
            if not amount:
                continue
            selected = entry["id"] in source_ids
            if lots and lots[-1][0] == selected:
                lots[-1][1] += amount
            else:
                lots.append([selected, amount])
        else:
            while amount:
                selected, available = lots[0]
                taken = min(amount, available)
                if selected:
                    outgoing[index] += taken
                amount -= taken
                if taken == available:
                    lots.popleft()
                else:
                    lots[0][1] -= taken
    return outgoing, _witness(opening, entries, source_ids, outgoing)


def allocate_ledger(opening: int, entries: list[dict], source_ids: set[str], target_ids: list[str]):
    """Return exact conditional target ranges, FIFO amounts, and full witnesses.

    Malformed inputs raise ValueError. Impossible total balances return status
    ``inconsistency`` without attribution amounts. Otherwise status is ``ok``;
    min == max is a determined share under this ledger's premises, while FIFO
    remains a separately identified allocation rule. Each target's extremes
    have their own complete joint witness: independent maxima cannot be added.

    The scan and FIFO take O(n) time and space. Producing both complete witnesses
    for each of t requested targets takes O(n*t), including output size. There
    is no loop over monetary units or enumeration of source combinations. The
    caller must bound requested entries/targets and must not truncate a ledger
    then describe the resulting bounds as complete.
    """
    _validate(opening, entries, source_ids, target_ids)
    balance, lower, upper = opening, 0, 0
    states, bounds = [], {}
    target_set = set(target_ids)
    for index, entry in enumerate(entries):
        states.append((balance, lower, upper))
        amount = entry["amount"]
        if entry["direction"] == "in":
            balance += amount
            if entry["id"] in source_ids:
                lower += amount
                upper += amount
        else:
            if amount > balance:
                return {"status": "inconsistency", "rule_version": RULE_VERSION,
                        "reason": "insufficient_funds", "entry_id": entry["id"], "targets": []}
            if entry["id"] in target_set:
                bounds[entry["id"]] = (index, max(0, lower - (balance - amount)), min(amount, upper))
            balance -= amount
            lower, upper = max(0, lower - amount), min(upper, balance)
        if entry.get("post_balance") is not None and entry["post_balance"] != balance:
            return {"status": "inconsistency", "rule_version": RULE_VERSION,
                    "reason": "post_balance_mismatch", "entry_id": entry["id"], "targets": []}
    fifo_amounts, fifo_witness = _fifo(opening, entries, source_ids)
    targets = []
    for entry_id in target_ids:
        index, minimum, maximum = bounds[entry_id]
        targets.append({"id": entry_id, "amount": entries[index]["amount"],
                        "min": minimum, "max": maximum, "fifo": fifo_amounts[index],
                        "min_witness": _extreme_witness(opening, entries, source_ids, states, index, minimum),
                        "max_witness": _extreme_witness(opening, entries, source_ids, states, index, maximum)})
    return {"status": "ok", "rule_version": RULE_VERSION, "closing_balance": balance,
            "targets": targets, "fifo_witness": fifo_witness}
