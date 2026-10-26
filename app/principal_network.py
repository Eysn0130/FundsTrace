"""Exact conditional multi-account attribution in an ordered available ledger.

The caller proves one currency/scale, complete accounting-account coverage,
opening available balances, event identity, and a real total event order. None
denotes an external boundary, not an unknown account. Refunds and fees are
ordinary transfers; unresolved reversals, FX, credit, freezes, or tied events
without a proven order must be rejected by the caller. Integer amounts use one
caller-supplied scale (FundsTrace: scale 2). No source documents are read here.

Only the selected event introduces source funds, at its recipient. All earlier
funds are other funds, including the selected event's payer funds before that
event. Every later event and every competing transaction participates, whether
or not it is a target. The time-expanded network has no static recycling of
capacity. Intermediate transfer shares are not additive final principal.
"""
from collections import deque


RULE_VERSION = "principal-network.v1"


class _Limited(Exception):
    pass


class _Budget:
    def __init__(self, limit):
        self.limit = limit
        self.steps = 0

    def use(self, count=1):
        self.steps += count
        if self.steps > self.limit:
            raise _Limited


def _integer(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _validate(opening, events, source_event_id, target_ids, budget):
    for account, balance in opening.items():
        budget.use()
        _identifier(account, "account")
        _integer(balance, "opening balance")
    indices = {}
    for index, event in enumerate(events):
        budget.use()
        if not isinstance(event, dict):
            raise ValueError("each event must be a dictionary")
        event_id = event.get("id")
        _identifier(event_id, "event id")
        if event_id in indices:
            raise ValueError("event ids must be unique")
        indices[event_id] = index
        _integer(event.get("amount"), "event amount")
        if "payer" not in event or "payee" not in event:
            raise ValueError("payer and payee are required; use None for an external boundary")
        if event["payer"] is None and event["payee"] is None:
            raise ValueError("an event must touch an internal account")
        for party in ("payer", "payee"):
            account = event[party]
            if account is not None:
                _identifier(account, party)
                if account not in opening:
                    raise ValueError("every internal account needs an explicit opening balance")
            post_balance = event.get(f"{party}_post_balance")
            if post_balance is not None:
                _integer(post_balance, f"{party}_post_balance")
                if account is None:
                    raise ValueError("an external boundary has no modeled post-balance")
    _identifier(source_event_id, "source event id")
    if source_event_id not in indices:
        raise ValueError("source event id must identify an event")
    source = events[indices[source_event_id]]
    if source["payee"] is None or source["payer"] == source["payee"]:
        raise ValueError("source event must enter an internal account from another account or boundary")
    seen = set()
    for event_id in target_ids:
        budget.use()
        _identifier(event_id, "target event id")
        if event_id in seen:
            raise ValueError("target ids must be unique")
        seen.add(event_id)
        if event_id not in indices or indices[event_id] <= indices[source_event_id]:
            raise ValueError("each target must occur after the source event")
        if events[indices[event_id]]["payer"] is None:
            raise ValueError("each target must have an internal payer")
    return indices


def _ledger_balances(opening, events, budget):
    balances, before = dict(opening), []
    for event in events:
        budget.use()
        payer, payee, amount = event["payer"], event["payee"], event["amount"]
        before.append((balances.get(payer, 0), balances.get(payee, 0)))
        if payer is not None:
            if amount > balances[payer]:
                return None, None, {"reason": "insufficient_funds", "event_id": event["id"], "account": payer}
            balances[payer] -= amount
        if payee is not None:
            balances[payee] += amount
        for party in ("payer", "payee"):
            post_balance = event.get(f"{party}_post_balance")
            if post_balance is not None and post_balance != balances[event[party]]:
                return None, None, {"reason": "post_balance_mismatch", "event_id": event["id"], "account": event[party]}
    return before, balances, None


class _Network:
    """Immutable topology with separate residual-capacity arrays for each solve."""
    def __init__(self):
        self.adj, self.to, self.initial, self.edges = [[], []], [], [], []
        self.source, self.sink = 0, 1

    def node(self):
        self.adj.append([])
        return len(self.adj) - 1

    def edge(self, start, end, capacity, kind, *, event_id=None, account=None):
        arc = len(self.to)
        self.adj[start].append(arc)
        self.adj[end].append(arc + 1)
        self.to.extend((end, start))
        self.initial.extend((capacity, 0))
        self.edges.append({"edge_id": str(arc // 2), "from": str(start), "to": str(end),
                           "capacity": capacity, "kind": kind,
                           **({"event_id": event_id} if event_id is not None else {}),
                           **({"account": account} if account is not None else {})})
        return arc

    def augment(self, residual, start, end, limit, budget):
        """Iterative breadth-first augmenting paths; bounded by examined arcs."""
        total = 0
        while total < limit:
            parent = {start: None}
            queue = deque([start])
            while queue and end not in parent:
                node = queue.popleft()
                for arc in self.adj[node]:
                    budget.use()
                    neighbor = self.to[arc]
                    if residual[arc] and neighbor not in parent:
                        parent[neighbor] = arc
                        if neighbor == end:
                            break
                        queue.append(neighbor)
            if end not in parent:
                break
            delta, node = limit - total, end
            while node != start:
                budget.use()
                arc = parent[node]
                delta = min(delta, residual[arc])
                node = self.to[arc ^ 1]
            node = end
            while node != start:
                budget.use()
                arc = parent[node]
                residual[arc] -= delta
                residual[arc ^ 1] += delta
                node = self.to[arc ^ 1]
            total += delta
        return total

    def witness(self, residual, source_amount, budget):
        edge_flows, event_shares, closing_shares = [], {}, {}
        external, retained = 0, 0
        for index, edge in enumerate(self.edges):
            budget.use()
            amount = edge["capacity"] - residual[2 * index]
            if not amount:
                continue
            edge_flows.append({**edge, "amount": amount})
            if edge["kind"] in ("transfer", "external", "injection"):
                event_shares[edge["event_id"]] = amount
            if edge["kind"] == "external":
                external += amount
            elif edge["kind"] == "terminal":
                retained += amount
                closing_shares[edge["account"]] = amount
        return {"source_node": str(self.source), "sink_node": str(self.sink), "source_amount": source_amount,
                "edge_flows": edge_flows, "event_shares": event_shares, "closing_shares": closing_shares,
                "external_total": external, "retained_total": retained}


def _build(opening, events, before, closing, source_event_id, budget):
    network = _Network()
    current = {account: network.node() for account in opening}
    event_arcs, terminal_arcs = {}, {}
    for event, (payer_before, payee_before) in zip(events, before):
        budget.use()
        event_id, payer, payee, amount = event["id"], event["payer"], event["payee"], event["amount"]
        arcs = {}
        payer_old = current.get(payer)
        if payer is not None:
            current[payer] = network.node()
            arcs["payer_hold"] = network.edge(payer_old, current[payer], payer_before - amount,
                                               "hold", event_id=event_id, account=payer)
        if payee is not None and payee != payer:
            payee_old = current[payee]
            current[payee] = network.node()
            arcs["payee_hold"] = network.edge(payee_old, current[payee], payee_before,
                                               "hold", event_id=event_id, account=payee)
        if event_id == source_event_id:
            arcs["injection"] = network.edge(network.source, current[payee], amount,
                                              "injection", event_id=event_id, account=payee)
        elif payer is not None:
            arcs["transfer"] = network.edge(payer_old, network.sink if payee is None else current[payee],
                                             amount, "external" if payee is None else "transfer", event_id=event_id)
        event_arcs[event_id] = arcs
    for account, node in current.items():
        budget.use()
        terminal_arcs[account] = network.edge(node, network.sink, closing[account], "terminal", account=account)
    return network, event_arcs, terminal_arcs


def _extreme(network, baseline, arc, maximize, budget):
    old = baseline[arc ^ 1]
    available = baseline[arc] if maximize else old
    if not available:
        return old, baseline
    budget.use(len(baseline))
    residual = baseline.copy()
    # A residual cycle must use other arcs; otherwise the opposite arc could
    # immediately cancel the requested change and falsely improve the bound.
    residual[arc] = residual[arc ^ 1] = 0
    start, end = network.to[arc], network.to[arc ^ 1]
    if not maximize:
        start, end = end, start
    change = network.augment(residual, start, end, available, budget)
    result = old + change if maximize else old - change
    residual[arc] = network.initial[arc] - result
    residual[arc ^ 1] = result
    return result, residual


def _fifo(network, opening, events, source_event_id, event_arcs, terminal_arcs, budget):
    queues = {account: deque([[False, amount]]) if amount else deque() for account, amount in opening.items()}
    selected = {account: 0 for account in opening}
    budget.use(len(network.initial))
    residual = network.initial.copy()

    def assign(arc, amount):
        if not 0 <= amount <= network.initial[arc]:
            raise RuntimeError("FIFO allocation violated a ledger capacity")
        residual[arc], residual[arc ^ 1] = network.initial[arc] - amount, amount

    for event in events:
        budget.use()
        payer, payee, amount = event["payer"], event["payee"], event["amount"]
        arcs, chunks = event_arcs[event["id"]], []
        payer_source, payee_source = selected.get(payer, 0), selected.get(payee, 0)
        taken_source, left = 0, amount
        if payer is not None:
            while left:
                budget.use()
                is_source, available = queues[payer][0]
                taken = min(left, available)
                chunks.append((is_source, taken))
                if is_source:
                    taken_source += taken
                left -= taken
                if taken == available:
                    queues[payer].popleft()
                else:
                    queues[payer][0][1] -= taken
            selected[payer] -= taken_source
            assign(arcs["payer_hold"], payer_source - taken_source)
        else:
            chunks = [(False, amount)] if amount else []
        if "payee_hold" in arcs:
            assign(arcs["payee_hold"], payee_source)
        delivered = taken_source
        if event["id"] == source_event_id:
            chunks = [(True, amount)] if amount else []
            delivered = amount
            assign(arcs["injection"], amount)
        elif "transfer" in arcs:
            assign(arcs["transfer"], taken_source)
        if payee is not None:
            selected[payee] += delivered
            for is_source, fragment in chunks:
                budget.use()
                if queues[payee] and queues[payee][-1][0] == is_source:
                    queues[payee][-1][1] += fragment
                else:
                    queues[payee].append([is_source, fragment])
    for account, arc in terminal_arcs.items():
        budget.use()
        assign(arc, selected[account])
    return residual


def allocate_network(opening: dict[str, int], events: list[dict], source_event_id: str,
                     target_ids: list[str], *, max_events=20000, max_targets=20,
                     max_accounts=20000, max_steps=2000000):
    """Return conditional multi-hop ranges and independent conserving witnesses.

    Events contain id, payer, payee, amount; optional payer_post_balance and
    payee_post_balance mean available balances after the entire event. Internal
    accounts require explicit openings. The selected event's recipient receives
    its full amount as the selected source. Targets occur later and have an
    internal payer. Source and target IDs must be unique, exact strings.

    Malformed inputs raise ValueError. A negative total balance or contradictory
    observed post-balance returns inconsistency, before a flow solve. Budget or
    size exhaustion returns limited with no partial bounds. Each min/max witness
    is a complete feasible allocation of P, with P = external_total +
    retained_total. Different extrema cannot be added; event_shares count each
    intermediate traversal. FIFO separately moves compact ordered fragments.

    Iterative augmenting paths avoid recursive stack limits. Their worst-case
    complexity is not constant or linear; examined residual arcs, path updates,
    cloned residual entries, FIFO fragments, and witness edges share max_steps.
    Bounds are exact only when status is ok. The caller must reject incomplete
    ledgers, unknown order or semantics rather than clipping inputs to fit.
    """
    if not isinstance(opening, dict) or not isinstance(events, list) or not isinstance(target_ids, list):
        raise ValueError("opening must be a dictionary; events and target_ids must be lists")
    for name, limit in (("max_events", max_events), ("max_targets", max_targets),
                        ("max_accounts", max_accounts), ("max_steps", max_steps)):
        _integer(limit, name)
        if limit == 0:
            raise ValueError(f"{name} must be positive")
    base = {"rule_version": RULE_VERSION, "targets": []}
    for size, limit, reason in ((len(events), max_events, "event_limit"),
                                (len(target_ids), max_targets, "target_limit"),
                                (len(opening), max_accounts, "account_limit")):
        if size > limit:
            return {**base, "status": "limited", "reason": reason, "steps": 0}
    budget = _Budget(max_steps)
    try:
        indices = _validate(opening, events, source_event_id, target_ids, budget)
        before, closing, error = _ledger_balances(opening, events, budget)
        if error:
            return {**base, "status": "inconsistency", "steps": budget.steps, **error}
        network, event_arcs, terminal_arcs = _build(opening, events, before, closing, source_event_id, budget)
        source_amount = events[indices[source_event_id]]["amount"]
        budget.use(len(network.initial))
        baseline = network.initial.copy()
        if network.augment(baseline, network.source, network.sink, source_amount, budget) != source_amount:
            return {**base, "status": "inconsistency", "reason": "source_capacity_inconsistent", "steps": budget.steps}
        fifo_residual = _fifo(network, opening, events, source_event_id, event_arcs, terminal_arcs, budget)
        fifo_witness = network.witness(fifo_residual, source_amount, budget)
        targets = []
        for event_id in target_ids:
            arc = event_arcs[event_id]["transfer"]
            minimum, min_residual = _extreme(network, baseline, arc, False, budget)
            maximum, max_residual = _extreme(network, baseline, arc, True, budget)
            targets.append({"id": event_id, "amount": events[indices[event_id]]["amount"],
                            "min": minimum, "max": maximum, "fifo": fifo_residual[arc ^ 1],
                            "min_witness": network.witness(min_residual, source_amount, budget),
                            "max_witness": network.witness(max_residual, source_amount, budget)})
        return {"status": "ok", "rule_version": RULE_VERSION, "source_amount": source_amount,
                "targets": targets, "fifo_witness": fifo_witness, "steps": budget.steps}
    except _Limited:
        return {**base, "status": "limited", "reason": "step_budget", "steps": budget.steps}
