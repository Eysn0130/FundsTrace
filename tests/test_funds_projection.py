"""Independent synthetic bounds distinguish observations from economic events."""
from decimal import Decimal

import pytest

from app import funds_projection as funds


def flow(database, cid, number, amount="100.00", currency="CNY", *, payer="SYN-A", payee="SYN-B", balance="150.00"):
    database.x("insert into flows(id,case_id,payer_account,payee_account,self_account,amount,currency,balance,trade_time) "
               "values (?,?,?,?,?,?,?,?,timestamp '2026-01-01 12:00:00')",
               [number, cid, payer, payee, payer if number % 2 else payee,
                Decimal(amount) if amount is not None else None, currency, Decimal(balance)])


def event(database, cid, name, ids, amount="100.00", currency="CNY", *, payer="SYN-A", payee="SYN-B"):
    database.x("insert into resolved_events(case_id,event_id,payer_account,payee_account,amount,currency) values (?,?,?,?,?,?)",
               [cid, name, payer, payee, Decimal(amount), currency])
    for number in ids:
        database.x("insert into event_members values (?,?,?,'confirmed','synthetic scoped proof')", [cid, number, name])


def selected(cid, ids):
    return "select * from flows where case_id=? and id in (select unnest(?::bigint[]))", [cid, ids]


def expected(*, observations, confirmed, unresolved, totals, status="bounded"):
    return {"policy": funds.POLICY, "status": status, "observation_count": observations,
            "confirmed_event_count": confirmed, "unresolved_observation_count": unresolved,
            "count": {"lower": confirmed, "upper": confirmed + unresolved}, "totals": totals}


def total(currency, lower, upper, amount=None):
    return {"currency": currency, "amount": amount, "lower": lower, "upper": upper}


def all_paths(database, cid, ids):
    sql, params = selected(cid, ids)
    original = database.q_raw(sql + " order by id", params)
    projected, result = funds.from_rows(cid, list(reversed(original)))
    assert funds.summary(sql, params) == result == funds.summarize_rows(projected)
    sql_rows = database.q_raw(funds.cte(sql) + "select * from selected_facts order by id", params)
    assert sql_rows == projected
    assert database.q_raw(sql + " order by id", params) == original
    return projected, result


@pytest.mark.parametrize("ids", [[1], [2], [1, 2], [2, 1, 1]])
def test_confirmed_event_counts_once_even_if_one_side_selected(database, make_case, ids):
    cid = make_case("Synthetic one economic event")
    flow(database, cid, 1, balance="150.00")
    flow(database, cid, 2, balance="900.00")
    event(database, cid, "SYN-E1", [1, 2])
    rows, result = all_paths(database, cid, ids)
    assert result == expected(observations=len(set(ids)), confirmed=1, unresolved=0, status="exact",
                              totals=[total("CNY", "100.00", "100.00", "100.00")])
    assert len(rows) == 1 and rows[0]["id"] == min(ids)
    assert rows[0]["balance"] is None
    assert rows[0]["observation_amount"] == Decimal("100.00")
    assert rows[0]["observation_balance"] == (Decimal("150.00") if min(ids) == 1 else Decimal("900.00"))
    assert funds.public_fields(result) == {"cnt": 1, "total": "100.00", "amount_totals": [{"currency": "CNY", "amount": "100.00"}]}


def test_ordinary_same_time_same_amount_rows_remain_bounded_observations(database, make_case):
    cid = make_case("Synthetic ordinary observations")
    flow(database, cid, 1)
    flow(database, cid, 2)
    rows, result = all_paths(database, cid, [1, 2])
    assert result == expected(observations=2, confirmed=0, unresolved=2,
                              totals=[total("CNY", "0.00", "200.00")])
    assert len(rows) == 2 and {row["fact_status"] for row in rows} == {"unresolved"}
    assert all(row["balance"] == Decimal("150.00") for row in rows)
    assert funds.public_fields(result) == {"cnt": None, "total": None, "amount_totals": [{"currency": "CNY", "amount": None}]}


def test_distinct_confirmed_steps_are_two_events_not_one_net_transfer(database, make_case):
    cid = make_case("Synthetic two economic steps")
    for number in (1, 2):
        flow(database, cid, number)
    for number in (3, 4):
        flow(database, cid, number, payer="SYN-B", payee="SYN-C")
    event(database, cid, "SYN-AB", [1, 2])
    event(database, cid, "SYN-BC", [3, 4], payer="SYN-B", payee="SYN-C")
    rows, result = all_paths(database, cid, [1, 2, 3, 4])
    assert len(rows) == 2
    assert result == expected(observations=4, confirmed=2, unresolved=0, status="exact",
                              totals=[total("CNY", "200.00", "200.00", "200.00")])
    groups = funds.grouped_summary(*selected(cid, [1, 2, 3, 4]), group_by=("payer_account", "payee_account"))
    assert set(groups) == {("SYN-A", "SYN-B"), ("SYN-B", "SYN-C")}
    assert all(value["confirmed_event_count"] == 1 and value["totals"][0]["amount"] == "100.00" for value in groups.values())


def test_mixed_confirmed_unknown_and_conflict_bounds_keep_all_observations(database, make_case):
    cid = make_case("Synthetic bounded mixed evidence")
    for number, amount in enumerate(("100.00", "100.00", "7.00", "8.00", "9.00"), 1):
        flow(database, cid, number, amount)
    event(database, cid, "SYN-E1", [1, 2])
    for number in (4, 5):
        # A stale event_id on a conflict must never confer event authority.
        database.x("insert into event_members values (?,?,?,'conflict','synthetic conflicting proof')", [cid, number, "SYN-E1"])
    rows, result = all_paths(database, cid, [1, 2, 3, 4, 5])
    assert len(rows) == 4
    assert result == expected(observations=5, confirmed=1, unresolved=3,
                              totals=[total("CNY", "100.00", "124.00")])


def test_multicurrency_and_large_cents_never_mix_or_round(database, make_case):
    cid = make_case("Synthetic exact per currency")
    for number in (1, 2):
        flow(database, cid, number, "90071992547409.91")
    for number in (3, 4):
        flow(database, cid, number, "0.10", "USD")
    flow(database, cid, 5, "0.03", "USD")
    event(database, cid, "SYN-CNY", [1, 2], "90071992547409.91")
    event(database, cid, "SYN-USD", [3, 4], "0.10", "USD")
    _, result = all_paths(database, cid, [1, 2, 3, 4, 5])
    assert result == expected(observations=5, confirmed=2, unresolved=1,
        totals=[total("CNY", "90071992547409.91", "90071992547409.91", "90071992547409.91"), total("USD", "0.10", "0.13")])
    assert funds.public_fields(result)["total"] is None


@pytest.mark.parametrize("amount", [None, "-1.00"])
def test_unknown_or_negative_amount_has_no_false_upper_bound(database, make_case, amount):
    cid = make_case("Synthetic unknown amount")
    flow(database, cid, 1)
    flow(database, cid, 2)
    flow(database, cid, 3, amount)
    event(database, cid, "SYN-E1", [1, 2])
    _, result = all_paths(database, cid, [1, 2, 3])
    assert result == expected(observations=3, confirmed=1, unresolved=1, status="unknown",
                              totals=[total("CNY", "100.00", None)])


@pytest.mark.parametrize("currency", [None, "", "unknown", "XXX"])
def test_unknown_currency_cannot_be_assigned_zero_or_excluded_from_known_upper(database, make_case, currency):
    cid = make_case("Synthetic unknown currency")
    flow(database, cid, 1)
    flow(database, cid, 2)
    flow(database, cid, 3, "12.00", currency)
    event(database, cid, "SYN-E1", [1, 2])
    _, result = all_paths(database, cid, [1, 2, 3])
    assert result == expected(observations=3, confirmed=1, unresolved=1, status="unknown",
                              totals=[total("CNY", "100.00", None), total(None, "0.00", None)])


def test_unselected_and_foreign_events_do_not_leak_and_mixed_case_sql_fails(database, make_case):
    cid, other = make_case("Synthetic selected scope"), make_case("Synthetic foreign scope")
    for number in (1, 2, 3, 4):
        flow(database, cid, number)
    for number in (5, 6):
        flow(database, other, number, "999.00")
    event(database, cid, "SYN-SAME-ID", [1, 2])
    event(database, cid, "SYN-UNSELECTED", [3, 4])
    event(database, other, "SYN-SAME-ID", [5, 6], "999.00")
    loaded = database.q_raw("select * from flows where id in (2,5,6) order by id")
    projected, result = funds.from_rows(cid, loaded)
    assert [row["id"] for row in projected] == [2]
    assert result["observation_count"] == result["confirmed_event_count"] == 1
    assert result["totals"] == [total("CNY", "100.00", "100.00", "100.00")]
    assert result == funds.summary(*selected(cid, [2, 5, 6]))
    with pytest.raises(ValueError, match="one case scope"):
        funds.summary("select * from flows")
    with pytest.raises(ValueError, match="one case scope"):
        funds.grouped_summary("select * from flows", group_by=("self_account",))


def test_repeated_source_join_is_one_observation_and_empty_selection_is_exact(database, make_case):
    cid = make_case("Synthetic repeated selection")
    flow(database, cid, 1)
    sql = "select f.* from flows f cross join (values (1),(2)) copies(n) where f.case_id=?"
    assert funds.summary(sql, [cid]) == expected(observations=1, confirmed=0, unresolved=1,
                                                totals=[total("CNY", "0.00", "100.00")])
    projected, result = all_paths(database, cid, [])
    assert projected == []
    assert result == expected(observations=0, confirmed=0, unresolved=0, status="exact", totals=[])


def test_from_rows_is_chunked_preserves_wire_money_and_detects_stale_money(database, make_case, monkeypatch):
    cid = make_case("Synthetic chunked selected rows")
    for number in (1, 2, 3, 4):
        flow(database, cid, number)
    event(database, cid, "SYN-E1", [1, 4])
    monkeypatch.setattr(funds, "CHUNK", 2)
    original = database.q_raw("select * from flows where case_id=? order by id", [cid])
    wire = [{**row, "id": str(row["id"]), "amount": format(row["amount"], ".2f"), "balance": format(row["balance"], ".2f")} for row in original]
    rows, result = funds.from_rows(cid, wire)
    assert len(rows) == 3 and rows[0]["observation_count"] == 2 and rows[0]["amount"] == "100.00"
    assert funds.summarize_rows(rows) == result == funds.summary(*selected(cid, [1, 2, 3, 4]))
    wire[0]["amount"] = "100.01"
    with pytest.raises(ValueError, match="snapshot"):
        funds.from_rows(cid, wire)


def test_public_event_rebuild_withdraws_default_projection_after_conflict(client, database, make_case):
    from test_event_import import csv_rows, transaction
    from test_folder_evidence import accepted
    cid = make_case("Synthetic public projection withdrawal")
    rows = [transaction(own) for own in ("SYN-A", "SYN-B")]
    accepted(client, cid, [("events.csv", csv_rows(rows))], scope_bank=None)
    ids = [row["id"] for row in database.q_raw("select id from flows where case_id=? order by id", [cid])]
    assert funds.summary(*selected(cid, ids))["totals"] == [total("CNY", "100.00", "100.00", "100.00")]
    _, conflict = accepted(client, cid, [("conflict.csv", csv_rows([transaction("SYN-A", **{"交易金额": "101.00"})]))], scope_bank=None)
    assert funds.summary(*selected(cid, ids)) == expected(observations=2, confirmed=0, unresolved=2,
        totals=[total("CNY", "0.00", "200.00")])
    assert client.delete(f"/api/batches/{conflict['batch_id']}", params={"case_id": cid}).status_code == 200
    assert funds.summary(*selected(cid, ids))["status"] == "exact"
