"""Unique event pages fetch payloads and selected member IDs after paging."""
from contextlib import nullcontext
from decimal import Decimal
import threading

import pytest

from app import funds_projection as funds, transaction_time
from test_funds_projection import event, flow


@pytest.mark.parametrize("observations", [False, True])
@pytest.mark.parametrize("offset", [0, 995, 2000])
def test_unique_event_page_evaluates_wide_payload_only_for_page_representatives(database, make_case, observations, offset):
    cid = make_case("Synthetic bounded event payload")
    database.x("""insert into flows(id,case_id,trade_time,payer_account,payee_account,amount,currency,balance)
        select n,?,timestamp '2026-01-01' + n * interval '1 second','SYN-A','SYN-B',1.01,'CNY',null
        from range(1,1001) r(n)""", [cid])
    event(database, cid, "SYN-PAGE-EVENT", [999, 1000], "100.03")
    calls = []

    def payload(number):
        calls.append(number)
        return "Synthetic wide payload " * 256 + str(number)

    connection = database.con()
    connection.create_function("synthetic_page_payload", payload, ["BIGINT"], "VARCHAR")
    try:
        source = """select f.* replace (synthetic_page_payload(id) as summary)
            from flows f where case_id=?"""
        rows, summary = funds.page_with_summary(source, [cid], 5, offset, observations=observations, unique_source=True)
        ids = list(range(1000 if observations else 999, 0, -1))[offset:offset + 5]
        assert [row["id"] for row in rows] == ids
        if not observations and offset == 0:
            assert rows[0]["observation_ids"] == ["999", "1000"]
            assert rows[0]["amount"] == Decimal("100.03") and rows[0]["balance"] is None
            assert rows[0]["observation_amount"] == Decimal("1.01")
        if observations:
            assert all("observation_ids" not in row and row["amount"] == Decimal("1.01") for row in rows)
        assert summary["observation_count"] == 1000
        assert summary["confirmed_event_count"] == 1
        assert len(calls) == len(rows) and set(calls) == set(ids)
    finally:
        connection.remove_function("synthetic_page_payload")


HIGH = 9007199254740993


@pytest.mark.parametrize("observations", [False, True])
def test_sparse_page_keys_do_not_evaluate_payload_between_representatives(database, make_case, observations):
    cid = make_case("Synthetic sparse payload keys")
    database.x("""insert into flows(id,case_id,trade_time,amount,currency)
        select n,?,timestamp '2026-01-01' + (n%10) * interval '1 second',1.01,'CNY'
        from range(1,1001) r(n)""", [cid])
    calls = []

    def payload(number):
        calls.append(number)
        return "Synthetic sparse payload " * 256 + str(number)

    connection = database.con()
    connection.create_function("synthetic_sparse_payload", payload, ["BIGINT"], "VARCHAR")
    try:
        source = "select f.* replace(synthetic_sparse_payload(id) as summary) from flows f where case_id=?"
        rows, _ = funds.page_with_summary(source, [cid], 5, 0, observations=observations, unique_source=True)
        assert [row["id"] for row in rows] == [999, 989, 979, 969, 959]
        assert sorted(calls) == [959, 969, 979, 989, 999]
    finally:
        connection.remove_function("synthetic_sparse_payload")


def mixed_case(database, make_case):
    cid, other = make_case("Synthetic event payload scope"), make_case("Synthetic foreign event payload")
    for number in (2, 10, HIGH):
        flow(database, cid, number, "100.03")
    event(database, cid, "SYN-SHARED", [2, 10, HIGH], "100.03")
    for number in (HIGH + 1, HIGH + 2):
        flow(database, cid, number, "0.10", "USD")
    event(database, cid, "SYN-USD", [HIGH + 1, HIGH + 2], "0.10", "USD")
    flow(database, cid, HIGH + 3, None, None)
    flow(database, cid, HIGH + 4, "-1.01")
    flow(database, cid, HIGH + 5, "0.03", "USD", balance="123.45")
    event(database, cid, "SYN-SINGLE", [HIGH + 5], "0.03", "USD")
    database.x("update resolved_events set evidence_json=? where case_id=? and event_id='SYN-SINGLE'",
               ['{"policy":"' + funds.SINGLE_POSTING_POLICY + '"}', cid])
    flow(database, cid, HIGH + 6, "7.01")
    flow(database, cid, HIGH + 7, "2.02", "USD")
    database.x("""insert into event_members(case_id,flow_id,event_id,status,reason)
        values (?,?,'SYN-SHARED','unknown','synthetic stale event')""", [cid, HIGH + 4])
    database.x("""insert into event_members(case_id,flow_id,status,reason)
        values (?,?,'unknown','time_basis_incomparable')""", [cid, HIGH + 6])
    database.x("""insert into event_members(case_id,flow_id,status,reason,pending_group)
        values (?,?,'unknown','synthetic pending pair','SYN-PENDING')""", [cid, HIGH + 7])
    database.x("""update flows set summary='Synthetic original ' || cast(id as varchar),trade_no='SYN-' || cast(id as varchar)
        where case_id=?""", [cid])
    database.x("update flows set balance=null where case_id=? and id in (?,?,?)", [cid, HIGH + 3, HIGH + 4, HIGH + 6])
    database.x("update flows set trade_time=null where case_id=? and id=?", [cid, HIGH + 3])
    database.x("""update flows set trade_time=timestamp '2026-01-01 09:00:00.123456',
        trade_time_utc=timestamp '2026-01-01 01:00:00.123456',
        trade_time_original='2026-01-01T09:00:00.123456+08:00',trade_time_offset='+08:00'
        where case_id=? and id in (2,10,?)""", [cid, HIGH])
    database.x("""update flows set trade_time=timestamp '2026-01-01 10:00:00.123456',
        trade_time_utc=timestamp '2026-01-01 02:00:00.123456',
        trade_time_original='2026-01-01T10:00:00.123456+08:00',trade_time_offset='+08:00'
        where case_id=? and id=?""", [cid, HIGH + 4])
    for numbers, stamp in (([HIGH + 1, HIGH + 2], "2026-01-02"), ([HIGH + 5], "2026-01-03"), ([HIGH + 7], "2026-01-04")):
        database.x("update flows set trade_time=cast(? as timestamp) where case_id=? and id in (select unnest(?::bigint[]))", [stamp, cid, numbers])
    for number in (HIGH + 8, HIGH + 9):
        flow(database, other, number, "999.99")
    event(database, other, "SYN-SHARED", [HIGH + 8, HIGH + 9], "999.99")
    return cid, other


def wide_reference(database, source, params, size, offset, *, observations=False, unique_source=True):
    relation = "selected_observations" if observations else "selected_facts"
    order = transaction_time.sql_order() if unique_source else "trade_time desc nulls last,id desc"
    rows = database.q_raw(funds.cte(source, include_ids=not observations) +
                         f"select * from {relation} order by {order} limit ? offset ?", [*params, size, offset])
    return rows, funds.summary(source, params)


@pytest.mark.parametrize("observations", [False, True])
@pytest.mark.parametrize("selection", ["all", "second", "partial", "confirmed", "single", "pending", "empty"])
def test_unique_page_matches_complete_wide_rows_money_ids_and_time_scope(database, make_case, observations, selection):
    cid, _ = mixed_case(database, make_case)
    selected = {"all": [2, 10, *range(HIGH, HIGH + 8)], "second": [10], "partial": [10, HIGH],
                "confirmed": [2, 10, HIGH], "single": [HIGH + 5],
                "pending": [HIGH + 4, HIGH + 6, HIGH + 7], "empty": []}[selection]
    source = """select f.* replace (? || cast(id as varchar) as summary), 'Synthetic extra display' as extra_display
        from flows f where case_id=? and id in (select unnest(?::bigint[]))"""
    params = ["Synthetic projected ", cid, selected]
    for offset in (0, 1, 99):
        actual = funds.page_with_summary(source, params, 2, offset, observations=observations, unique_source=True)
        reference = wide_reference(database, source, params, 2, offset, observations=observations)
        assert actual == reference
        for row, expected in zip(actual[0], reference[0]):
            assert list(row) == list(expected)
            assert row["summary"] == "Synthetic projected " + str(row["id"])
            assert row["extra_display"] == "Synthetic extra display"
            assert row["amount"] is None or isinstance(row["amount"], Decimal)
            if not observations:
                assert row["observation_ids"] == [str(number) for number in sorted(map(int, row["observation_ids"]))]
                if row["fact_status"] != "confirmed":
                    assert row["observation_ids"] == [str(row["id"])]
                elif row["fact_event_id"] == "SYN-SHARED":
                    assert row["id"] == min(set(selected) & {2, 10, HIGH})
                    assert row["amount"] == Decimal("100.03") and row["balance"] is None
                    assert row["observation_ids"] == [str(number) for number in sorted(set(selected) & {2, 10, HIGH})]
                elif row["fact_event_id"] == "SYN-SINGLE":
                    assert row["amount"] == Decimal("0.03") and row["balance"] == Decimal("123.45")
    if selection == "all":
        rows, _ = funds.page_with_summary(source, params, 50, 0, observations=observations, unique_source=True)
        ids = [HIGH + 4, HIGH, 10, 2, HIGH + 7, HIGH + 5, HIGH + 2, HIGH + 1, HIGH + 6, HIGH + 3] if observations else [HIGH + 4, 2, HIGH + 7, HIGH + 5, HIGH + 1, HIGH + 6, HIGH + 3]
        assert [row["id"] for row in rows] == ids


@pytest.mark.parametrize("observations", [False, True])
def test_generic_joined_source_stays_deduplicated_on_original_path(database, make_case, observations):
    cid, _ = mixed_case(database, make_case)
    source = """select f.* from flows f cross join (values (1),(2)) copies(n)
        where case_id=?"""
    for offset in (0, 3, 99):
        assert funds.page_with_summary(source, [cid], 3, offset, observations=observations, unique_source=False) == wide_reference(
            database, source, [cid], 3, offset, observations=observations, unique_source=False)


def test_unique_page_rejects_multiple_case_scope(database, make_case):
    mixed_case(database, make_case)
    with pytest.raises(ValueError, match="one case scope"):
        funds.page_with_summary("select * from flows", [], 2, 0, unique_source=True)


@pytest.mark.parametrize("granularity", ["events", "observations"])
def test_public_unique_pages_match_prior_payload_and_wire_contract(client, database, make_case, monkeypatch, granularity):
    cid, _ = mixed_case(database, make_case)

    def prior(source, params, size, offset, **options):
        return wide_reference(database, source, params, size, offset, **options)

    for filters in ({}, {"page": 2}, {"page": 99}, {"account": "SYN-A"}, {"kw": "SYN-10"}, {"kw": "SYN-MISSING"}):
        params = {"granularity": granularity, "page_size": 2, **filters}
        actual = client.get(f"/api/v1/cases/{cid}/flows", params=params)
        assert actual.status_code == 200, (filters, actual.text)
        with monkeypatch.context() as baseline:
            baseline.setattr(funds, "page_with_summary", prior)
            expected = client.get(f"/api/v1/cases/{cid}/flows", params=params)
        assert expected.status_code == 200 and actual.json() == expected.json()
        for row in actual.json()["items"]:
            assert isinstance(row["id"], str) and all(isinstance(number, str) for number in row["observation_ids"])
            assert row["balance"] is None or isinstance(row["balance"], str)
            assert row["amount"] is None or isinstance(row["amount"], str)


@pytest.mark.parametrize("private_snapshot", [False, True])
def test_two_phase_page_keeps_owner_lock_or_existing_private_snapshot(database, make_case, monkeypatch, private_snapshot):
    cid = make_case("Synthetic two phase page consistency")
    flow(database, cid, HIGH, "10.25")
    database.x("update flows set summary='Synthetic before' where case_id=?", [cid])
    started, acquired, completed = threading.Event(), threading.Event(), threading.Event()
    original, workers, parameters = database.q_raw, [], []
    active_snapshot = False

    def update():
        started.set()
        with database.transaction():
            acquired.set()
            database.x("update flows set amount=20.50,balance=77.77,summary='Synthetic after' where case_id=?", [cid])
        completed.set()

    def between_phases(sql, params=None):
        if sql.startswith("select s.* from ("):
            parameters.append(params)
            worker = threading.Thread(target=update)
            workers.append(worker)
            worker.start()
            assert started.wait(2)
            if active_snapshot:
                assert acquired.wait(2) and completed.wait(2)
            else:
                assert not acquired.wait(.05)
        return original(sql, params)

    try:
        with monkeypatch.context() as interleaved:
            interleaved.setattr(database, "q_raw", between_phases)
            context = database.read_snapshot() if private_snapshot else nullcontext(False)
            with context as active_snapshot:
                rows, summary = funds.page_with_summary("select * from flows where case_id=?", [cid], 1, 0, unique_source=True)
    finally:
        for worker in workers:
            worker.join(2)
    assert len(workers) == 1 and all(not worker.is_alive() for worker in workers)
    assert acquired.is_set() and completed.is_set()
    assert parameters == [[cid, cid, HIGH]]
    assert all(type(value) is int for value in parameters[0])
    assert rows[0]["id"] == HIGH and rows[0]["observation_ids"] == [str(HIGH)]
    assert (rows[0]["amount"], rows[0]["balance"], rows[0]["summary"]) == (Decimal("10.25"), Decimal("150.00"), "Synthetic before")
    assert summary["totals"][0]["upper"] == "10.25"
    fresh, _ = funds.page_with_summary("select * from flows where case_id=?", [cid], 1, 0, unique_source=True)
    assert (fresh[0]["amount"], fresh[0]["balance"], fresh[0]["summary"]) == (Decimal("20.50"), Decimal("77.77"), "Synthetic after")


@pytest.mark.parametrize("damage", ["missing", "duplicate", "wrong-key"])
def test_page_payload_mismatch_fails_closed(database, make_case, monkeypatch, damage):
    cid = make_case("Synthetic unavailable page payload")
    flow(database, cid, HIGH, "0.01")
    original = database.q_raw

    def unavailable(sql, params=None):
        rows = original(sql, params)
        if sql.startswith("select s.* from ("):
            if damage == "missing":
                return []
            if damage == "duplicate":
                return [*rows, rows[0]]
            return [{**rows[0], "id": HIGH + 1}]
        return rows

    monkeypatch.setattr(database, "q_raw", unavailable)
    with pytest.raises(ValueError, match="payload does not match selected keys"):
        funds.page_with_summary("select * from flows where case_id=?", [cid], 1, 0, unique_source=True)


@pytest.mark.parametrize("observations", [False, True])
def test_unique_joined_between_source_keeps_existing_window_boundary(database, make_case, observations):
    cid, _ = mixed_case(database, make_case)
    database.x("""insert into resolved_endpoints(case_id,flow_id,role,entity_id,state,reason)
        values (?,2,'payer','SYN-M','resolved','synthetic')""", [cid])
    source = """select f.* from flows f left join resolved_endpoints e
        on e.case_id=f.case_id and e.flow_id=f.id and e.role='payer'
        where f.case_id=? and coalesce(e.entity_id,f.payer_account) between 'SYN-A' and 'SYN-Z'"""
    for offset in (0, 99):
        assert funds.page_with_summary(source, [cid], 2, offset, observations=observations, unique_source=True) == wide_reference(
            database, source, [cid], 2, offset, observations=observations)
