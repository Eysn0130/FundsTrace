"""Legacy mirror candidates remain clues; public graph funds require event evidence."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.canvas_investigation import mirror_groups
from funds_assertions import unresolved_funds


def seed_graph(client, database, make_case, rows):
    case_id = make_case("Synthetic graph mirrors")
    ids = []
    for index, overrides in enumerate(rows):
        row = {
            "trade_no": f"SYN-MIRROR-{index}",
            "trade_time": datetime(2026, 1, 1) + timedelta(seconds=overrides.get("second", index)),
            "payer_account": "SYN-A", "payee_account": "SYN-B",
            "payer_name": "Synthetic sender", "payee_name": "Synthetic recipient",
            "amount": Decimal("100.00"), "currency": "CNY", "self_account": "SYN-A",
        }
        row.update({key: value for key, value in overrides.items() if key != "second"})
        cols = ",".join(row)
        marks = ",".join("?" for _ in row)
        ids.append(database.q(f"insert into flows(case_id,{cols}) values (?,{marks}) returning id",
                              [case_id, *row.values()])[0]["id"])
    before = database.q_raw("select * from flows where case_id=? order by id", [case_id])
    response = client.post("/api/track", json={"case_id": case_id, "flow_ids": list(reversed(ids))})
    assert response.status_code == 200, response.text
    response = client.get("/api/graph", params={"case_id": case_id})
    assert response.status_code == 200, response.text
    graph = response.json()
    assert sorted(graph["tracked_ids"]) == ids
    assert sorted(flow_id for edge in graph["edges"] for flow_id in edge["ids"]) == ids
    assert database.q_raw("select * from flows where case_id=? order by id", [case_id]) == before
    raw = client.get("/api/flows", params={"case_id": case_id}).json()
    assert raw["total"] == len(ids)
    assert sorted(row["id"] for row in raw["rows"]) == ids
    assert_unresolved_graph(graph, before)
    return graph, mirror_candidates(before)


def mirror_candidates(rows):
    ordered = sorted(rows, key=lambda row: (row["trade_time"] is None, row["trade_time"] or datetime.max, row["id"]))
    groups = mirror_groups(ordered)
    duplicates = {flow_id for group in groups.values() for flow_id in group}
    return [row for row in ordered if row["id"] not in duplicates]


def expected_observations(rows):
    # Independent sums of the synthetic ledger facts, never projection output.
    totals = {}
    for row in rows:
        currency = row["currency"] or None
        totals[currency] = totals.get(currency, Decimal(0)) + row["amount"]
    return unresolved_funds(len(rows), *[(currency, None if None in totals else format(total, ".2f"))
        for currency, total in sorted(totals.items(), key=lambda item: (item[0] is None, item[0] or ""))])


def assert_unresolved_graph(graph, rows):
    assert graph["funds"] == expected_observations(rows)
    pairs = {(row["payer_account"], row["payee_account"]) for row in rows}
    assert {(edge["source"], edge["target"]) for edge in graph["edges"]} == pairs
    for edge in graph["edges"]:
        selected = [row for row in rows if (row["payer_account"], row["payee_account"]) == (edge["source"], edge["target"])]
        expected = expected_observations(selected)
        assert (edge["cnt"], edge["total"]) == (None, None)
        assert edge["funds"] == expected
        assert edge["amount_totals"] == [{"currency": item["currency"], "amount": None} for item in expected["totals"]]
    for node in graph["nodes"]:
        for direction, party in (("in", "payee_account"), ("out", "payer_account")):
            selected = [row for row in rows if row[party] == node["account"]]
            assert node[direction + "_funds"] == expected_observations(selected)
            assert node[direction + "_observation_count"] == len(selected)
            assert (node[direction + "_cnt"], node[direction + "_amt"]) == ((None, None) if selected else (0, "0.00"))


@pytest.mark.parametrize("ledgers,expected_count", [
    (["SYN-A", "SYN-B"], 1),
    (["SYN-A", "SYN-A"], 2),
    (["SYN-A", "SYN-B", "SYN-A", "SYN-B"], 2),
    (["SYN-A", "SYN-A", "SYN-B", "SYN-B"], 2),
    (["SYN-A", "SYN-B", "SYN-B", "SYN-A"], 2),
    # Undeclared ledgers were previously paired; approved G01 keeps all of
    # these independent facts because no opposite ledger owner is established.
    (["", None, "", None], 4),
    (["", "SYN-A", "SYN-A"], 3),
    ([None, "SYN-A", "SYN-A"], 3),
    (["SYN-A", "SYN-B", "SYN-C"], 2),
])
def test_each_record_can_only_mirror_one_other_record(client, database, make_case, ledgers, expected_count):
    graph, candidates = seed_graph(client, database, make_case, [{"self_account": ledger} for ledger in ledgers])
    # The original FIFO heuristic is tested directly; it does not certify these events.
    assert len(candidates) == expected_count
    assert sum(row["amount"] for row in candidates) == Decimal(expected_count * 100)


@pytest.mark.parametrize("ledgers", [
    ["", None],
    [None, "SYN-A"],
    ["SYN-A", ""],
    ["SYN-C", "SYN-A"],
    ["SYN-B", "SYN-C"],
    ["SYN-C", "SYN-D"],
    [" SYN-A", "SYN-B"],
])
def test_missing_or_third_party_owner_never_justifies_a_mirror(client, database, make_case, ledgers):
    graph, candidates = seed_graph(client, database, make_case, [{"self_account": ledger} for ledger in ledgers])
    assert len(candidates) == 2
    assert sum(row["amount"] for row in candidates) == Decimal("200.00")


def test_unproven_owners_do_not_consume_the_valid_fifo(client, database, make_case):
    # The two established A/B mirrors still pair 0/120 and 110/220.
    # Unknown/third-party rows are independently retained, never FIFO entries.
    graph, candidates = seed_graph(client, database, make_case, [
        {"self_account": "SYN-A", "second": 0},
        {"self_account": "", "second": 1},
        {"self_account": "SYN-C", "second": 2},
        {"self_account": "SYN-A", "second": 110},
        {"self_account": None, "second": 119},
        {"self_account": "SYN-B", "second": 120},
        {"self_account": "SYN-B", "second": 220},
    ])
    assert len(candidates) == 5
    assert sum(row["amount"] for row in candidates) == Decimal("500.00")


def test_pairing_uses_time_then_id_and_oldest_unmatched_record(client, database, make_case):
    # Import order differs from time order. FIFO pairs 0/120 and 110/220;
    # choosing the nearest record would leave the final mirror unpaired.
    graph, candidates = seed_graph(client, database, make_case, [
        {"self_account": "SYN-B", "second": 220},
        {"self_account": "SYN-A", "second": 110},
        {"self_account": "SYN-B", "second": 120},
        {"self_account": "SYN-A", "second": 0},
    ])
    assert len(candidates) == 2
    assert sum(row["amount"] for row in candidates) == Decimal("200.00")


@pytest.mark.parametrize("seconds,expected_count", [(120, 1), (120.9, 1), (121, 2)])
def test_existing_120_second_boundary_is_preserved(client, database, make_case, seconds, expected_count):
    graph, candidates = seed_graph(client, database, make_case, [
        {"self_account": "SYN-A", "second": 0},
        {"self_account": "SYN-B", "second": seconds},
    ])
    assert len(candidates) == expected_count


@pytest.mark.parametrize("currencies,expected_count", [(["CNY", "USD"], 2), ([None, ""], 1)])
def test_same_amount_currency_matching_keeps_existing_normalization(client, database, make_case, currencies, expected_count):
    graph, candidates = seed_graph(client, database, make_case, [
        {"self_account": ledger, "currency": currency}
        for ledger, currency in zip(["SYN-A", "SYN-B"], currencies)
    ])
    assert len(candidates) == expected_count


def test_equal_timestamps_and_null_times_are_handled_without_losing_records(client, database, make_case):
    graph, candidates = seed_graph(client, database, make_case, [
        {"self_account": ledger, "second": 0} for ledger in ["SYN-A", "SYN-B", "SYN-A", "SYN-B"]
    ] + [{"self_account": ledger, "trade_time": None} for ledger in ["SYN-A", "SYN-B"]])
    assert len(candidates) == 4
    assert sum(row["amount"] for row in candidates) == Decimal("400.00")


def test_adding_later_real_transaction_then_its_mirror_updates_graph(client, database, make_case):
    case_id = make_case("Synthetic incrementally tracked mirrors")
    ids = [database.q("""
        insert into flows(case_id, trade_time, payer_account, payee_account, amount, self_account)
        values (?, ?, 'SYN-A', 'SYN-B', 100.00, ?) returning id
    """, [case_id, datetime(2026, 1, 1) + timedelta(seconds=second), ledger])[0]["id"]
        for second, ledger in enumerate(["SYN-A", "SYN-B", "SYN-A", "SYN-B"])]
    for index, expected_count in enumerate([1, 1, 2, 2]):
        tracked = client.post("/api/track", json={"case_id": case_id, "flow_ids": [ids[index]]})
        assert tracked.status_code == 200, tracked.text
        response = client.get("/api/graph", params={"case_id": case_id})
        assert response.status_code == 200, response.text
        graph = response.json()
        assert sorted(graph["tracked_ids"]) == ids[:index + 1]
        assert sorted(graph["edges"][0]["ids"]) == ids[:index + 1]
        selected = database.q_raw("select * from flows where id in (select unnest(?::bigint[])) order by id", [ids[:index + 1]])
        assert_unresolved_graph(graph, selected)
        candidates = mirror_candidates(selected)
        assert len(candidates) == expected_count
        assert sum(row["amount"] for row in candidates) == Decimal(expected_count * 100)


def test_currencies_and_decimal_cents_remain_separate(client, database, make_case):
    graph, candidates = seed_graph(client, database, make_case, [
        {"self_account": ledger, "currency": currency, "amount": Decimal(amount)}
        for currency, amount in [("CNY", "9999999999999999.99"), ("CNY", "9999999999999999.99"),
                                  ("USD", "0.01"), (None, "0.10"), ("", "0.20")]
        for ledger in ["SYN-A", "SYN-B"]
    ])
    assert len(candidates) == 5
    candidate_totals = {}
    for row in candidates:
        currency = row["currency"] or ""
        candidate_totals[currency] = candidate_totals.get(currency, Decimal(0)) + row["amount"]
    assert candidate_totals == {"": Decimal("0.30"), "CNY": Decimal("19999999999999999.98"), "USD": Decimal("0.01")}
    assert graph["edges"][0]["funds"] == unresolved_funds(10, ("CNY", None), ("USD", None), (None, None))


def test_different_counterparties_and_reverse_direction_never_pair(client, database, make_case):
    graph, candidates = seed_graph(client, database, make_case, [
        {"payer_account": source, "payee_account": target, "self_account": ledger}
        for source, target in [("SYN-A", "SYN-B"), ("SYN-A", "SYN-C"), ("SYN-B", "SYN-A")]
        for ledger in [source, target]
    ])
    assert {(row["payer_account"], row["payee_account"], row["amount"]) for row in candidates} == {
        ("SYN-A", "SYN-B", Decimal("100.00")),
        ("SYN-A", "SYN-C", Decimal("100.00")),
        ("SYN-B", "SYN-A", Decimal("100.00")),
    }
    assert len(candidates) == 3


@pytest.mark.parametrize("ledgers", [["SYN-A", "SYN-A"], ["SYN-A", "SYN-B"], ["", None]])
def test_self_transfer_cannot_establish_two_distinct_ledger_owners(client, database, make_case, ledgers):
    graph, candidates = seed_graph(client, database, make_case, [
        {"payer_account": "SYN-A", "payee_account": "SYN-A", "self_account": ledger}
        for ledger in ledgers
    ])
    assert len(candidates) == 2
    assert sum(row["amount"] for row in candidates) == Decimal("200.00")


@pytest.mark.parametrize("source,target", [(None, "SYN-B"), ("SYN-A", ""), ("", "")])
def test_missing_party_facts_remain_ungraphable_and_historical_tracking_preserves_them(
        client, database, make_case, source, target):
    case_id = make_case("Synthetic incomplete historic graph identity")
    ids = [database.q("""
        insert into flows(case_id,trade_time,payer_account,payee_account,amount,self_account)
        values (?, '2026-01-01 10:00:00', ?, ?, 100.00, ?) returning id
    """, [case_id, source, target, ledger])[0]["id"] for ledger in ["SYN-A", "SYN-B"]]
    before = database.q_raw("select * from flows where case_id=? order by id", [case_id])
    rejected = client.post("/api/track", json={"case_id": case_id, "flow_ids": ids})
    assert rejected.status_code == 422, rejected.text
    assert database.q_raw("select * from flows where case_id=? order by id", [case_id]) == before
    # A historical membership can predate current graphability validation.
    # It retains its facts/IDs while still adding no anonymous graph edge.
    for flow_id in ids:
        database.x("insert into tracked_flows(case_id,flow_id) values (?,?)", [case_id, flow_id])
    response = client.get("/api/graph", params={"case_id": case_id})
    assert response.status_code == 200, response.text
    assert response.json()["nodes"] == response.json()["edges"] == []
    assert sorted(response.json()["tracked_ids"]) == ids
    stats = client.get("/api/stats", params={"case_id": case_id}).json()
    assert stats["flows"] is None
    assert stats["funds"] == unresolved_funds(2, ("CNY", "200.00"))
    assert database.q_raw("select * from flows where case_id=? order by id", [case_id]) == before
