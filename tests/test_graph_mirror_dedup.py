"""Graph display pairs synthetic ledger mirrors without consuming later trades."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest


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
    return graph


@pytest.mark.parametrize("ledgers,expected_count", [
    (["SYN-A", "SYN-B"], 1),
    (["SYN-A", "SYN-A"], 2),
    (["SYN-A", "SYN-B", "SYN-A", "SYN-B"], 2),
    (["SYN-A", "SYN-A", "SYN-B", "SYN-B"], 2),
    (["SYN-A", "SYN-B", "SYN-B", "SYN-A"], 2),
    (["", None, "", None], 2),
    (["", "SYN-A", "SYN-A"], 2),
    ([None, "SYN-A", "SYN-A"], 2),
    (["SYN-A", "SYN-B", "SYN-C"], 2),
])
def test_each_record_can_only_mirror_one_other_record(client, database, make_case, ledgers, expected_count):
    graph = seed_graph(client, database, make_case, [{"self_account": ledger} for ledger in ledgers])
    edge = graph["edges"][0]
    expected_total = f"{expected_count * 100}.00"
    assert edge["cnt"] == expected_count
    assert edge["total"] == expected_total
    assert edge["amount_totals"] == [{"currency": "CNY", "amount": expected_total}]
    sender = next(node for node in graph["nodes"] if node["account"] == "SYN-A")
    recipient = next(node for node in graph["nodes"] if node["account"] == "SYN-B")
    assert (sender["out_cnt"], sender["out_amt"]) == (expected_count, expected_total)
    assert (recipient["in_cnt"], recipient["in_amt"]) == (expected_count, expected_total)


def test_pairing_uses_time_then_id_and_oldest_unmatched_record(client, database, make_case):
    # Import order differs from time order. FIFO pairs 0/120 and 110/220;
    # choosing the nearest record would leave the final mirror unpaired.
    graph = seed_graph(client, database, make_case, [
        {"self_account": "SYN-B", "second": 220},
        {"self_account": "SYN-A", "second": 110},
        {"self_account": "SYN-B", "second": 120},
        {"self_account": "SYN-A", "second": 0},
    ])
    assert graph["edges"][0]["cnt"] == 2
    assert graph["edges"][0]["total"] == "200.00"


@pytest.mark.parametrize("seconds,expected_count", [(120, 1), (120.9, 1), (121, 2)])
def test_existing_120_second_boundary_is_preserved(client, database, make_case, seconds, expected_count):
    graph = seed_graph(client, database, make_case, [
        {"self_account": "SYN-A", "second": 0},
        {"self_account": "SYN-B", "second": seconds},
    ])
    assert graph["edges"][0]["cnt"] == expected_count


@pytest.mark.parametrize("currencies,expected_count", [(["CNY", "USD"], 2), ([None, ""], 1)])
def test_same_amount_currency_matching_keeps_existing_normalization(client, database, make_case, currencies, expected_count):
    graph = seed_graph(client, database, make_case, [
        {"self_account": ledger, "currency": currency}
        for ledger, currency in zip(["SYN-A", "SYN-B"], currencies)
    ])
    assert graph["edges"][0]["cnt"] == expected_count


def test_equal_timestamps_and_null_times_are_handled_without_losing_records(client, database, make_case):
    graph = seed_graph(client, database, make_case, [
        {"self_account": ledger, "second": 0} for ledger in ["SYN-A", "SYN-B", "SYN-A", "SYN-B"]
    ] + [{"self_account": ledger, "trade_time": None} for ledger in ["SYN-A", "SYN-B"]])
    assert graph["edges"][0]["cnt"] == 4
    assert graph["edges"][0]["total"] == "400.00"


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
        assert graph["edges"][0]["cnt"] == expected_count
        assert graph["edges"][0]["total"] == f"{expected_count * 100}.00"


def test_currencies_and_decimal_cents_remain_separate(client, database, make_case):
    graph = seed_graph(client, database, make_case, [
        {"self_account": ledger, "currency": currency, "amount": Decimal(amount)}
        for currency, amount in [("CNY", "9999999999999999.99"), ("CNY", "9999999999999999.99"),
                                  ("USD", "0.01"), (None, "0.10"), ("", "0.20")]
        for ledger in ["SYN-A", "SYN-B"]
    ])
    edge = graph["edges"][0]
    assert edge["cnt"] == 5
    assert edge["total"] is None
    assert edge["amount_totals"] == [
        {"currency": "", "amount": "0.30"},
        {"currency": "CNY", "amount": "19999999999999999.98"},
        {"currency": "USD", "amount": "0.01"},
    ]


def test_different_counterparties_and_reverse_direction_never_pair(client, database, make_case):
    graph = seed_graph(client, database, make_case, [
        {"payer_account": source, "payee_account": target, "self_account": ledger}
        for source, target in [("SYN-A", "SYN-B"), ("SYN-A", "SYN-C"), ("SYN-B", "SYN-A")]
        for ledger in [source, target]
    ])
    assert {(edge["source"], edge["target"], edge["cnt"], edge["total"]) for edge in graph["edges"]} == {
        ("SYN-A", "SYN-B", 1, "100.00"),
        ("SYN-A", "SYN-C", 1, "100.00"),
        ("SYN-B", "SYN-A", 1, "100.00"),
    }
