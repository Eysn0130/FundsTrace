"""Bounded raw reads retain transaction, snapshot and value contracts."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading

import pytest


def observe_connection(database, monkeypatch, connection, sql, *, cleanup_fails=False):
    state = {"executions": [], "sizes": [], "lengths": [], "closed": False}

    class Cursor:
        @property
        def description(self):
            return connection.description

        def fetchall(self):
            raise AssertionError("raw_rows must never fetchall")

        def fetchmany(self, size):
            state["sizes"].append(size)
            rows = connection.fetchmany(size)
            state["lengths"].append(len(rows))
            return rows

    class Connection:
        def execute(self, query, params=None):
            state["executions"].append(query)
            if cleanup_fails and query == "select 1":
                raise OSError("synthetic cleanup failure")
            result = connection.execute(query, params or [])
            return Cursor() if query == sql else result

        def cursor(self):
            raise AssertionError("raw_rows must use the current transaction connection")

        def close(self):
            state["closed"] = True
            raise AssertionError("raw_rows must not close its borrowed connection")

        def __getattr__(self, name):
            return getattr(connection, name)

    def no_connection(*args, **kwargs):
        raise AssertionError("raw_rows must not open another connection")

    monkeypatch.setattr(database.duckdb, "connect", no_connection)
    return Connection(), state


def test_raw_rows_fetches_fixed_batches_lazily_and_preserves_raw_values(database, monkeypatch):
    sql = """select (9007199254740993+n)::bigint id, ?::decimal(18,2) amount,
        null::decimal(18,2) balance, n::integer source_order
        from range(2053) as source(n) order by n desc"""
    connection, state = observe_connection(database, monkeypatch, database.con(), sql)
    monkeypatch.setattr(database, "con", lambda: connection)
    with database.raw_rows(sql, ["9007199254740991.91"]) as rows:
        assert state["sizes"] == []
        first = next(rows)
        assert state["sizes"] == [1024]
        result = [first, *rows]
    assert [row["source_order"] for row in result] == list(reversed(range(2053)))
    assert all(type(row["id"]) is int and row["id"] == 9007199254740993 + row["source_order"] for row in result)
    assert all(type(row["amount"]) is Decimal and row["amount"] == Decimal("9007199254740991.91") for row in result)
    assert all(row["balance"] is None for row in result)
    assert state["sizes"] == [1024] * 4 and state["lengths"] == [1024, 1024, 5, 0]
    assert state["executions"] == [sql] and not state["closed"]
    assert list(rows) == []


@pytest.mark.parametrize("exit_kind", ["unstarted", "early", "validation"])
def test_raw_rows_abandoned_results_release_iterator_connection_and_lock(database, monkeypatch, exit_kind):
    sql = "select n from range(2049) as source(n)"
    connection, state = observe_connection(database, monkeypatch, database.con(), sql)
    monkeypatch.setattr(database, "con", lambda: connection)

    def consume():
        with database.raw_rows(sql) as rows:
            assert database._lock._is_owned()
            if exit_kind != "unstarted":
                assert next(rows) == {"n": 0}
            if exit_kind == "validation":
                raise ValueError("synthetic validation failure")
        return rows

    if exit_kind == "validation":
        with pytest.raises(ValueError, match="synthetic validation failure"):
            consume()
    else:
        assert list(consume()) == []
    assert state["executions"] == [sql, "select 1"] and not state["closed"]
    assert not database._lock._is_owned()
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(database.q_raw, "select 42 n").result(3) == [{"n": 42}]


@pytest.mark.parametrize("body_fails", [False, True])
def test_raw_rows_cleanup_failure_does_not_hide_validation_failure(database, monkeypatch, body_fails):
    sql = "select n from range(2049) as source(n)"
    connection, state = observe_connection(database, monkeypatch, database.con(), sql, cleanup_fails=True)
    monkeypatch.setattr(database, "con", lambda: connection)
    error, message = (ValueError, "synthetic validation failure") if body_fails else (OSError, "synthetic cleanup failure")
    with pytest.raises(error, match=message):
        with database.raw_rows(sql) as rows:
            assert next(rows) == {"n": 0}
            if body_fails:
                raise ValueError("synthetic validation failure")
    assert state["executions"] == [sql, "select 1"] and not state["closed"]
    assert list(rows) == [] and not database._lock._is_owned()
    assert database.q_raw("select 42 n") == [{"n": 42}]


def test_raw_rows_uses_uncommitted_transaction_and_preserves_rollback(database):
    database.x("create table synthetic_raw_rows (id bigint, amount decimal(18,2), balance decimal(18,2))")
    with pytest.raises(ValueError, match="synthetic rollback"):
        with database.transaction():
            # q_raw's existing write/RETURNING list contract remains available.
            returned = database.q_raw("insert into synthetic_raw_rows values (?, ?, null) returning id", [9007199254740993, "9007199254740991.91"])
            assert returned == [{"id": 9007199254740993}]
            with database.raw_rows("select * from synthetic_raw_rows") as rows:
                assert list(rows) == [{"id": 9007199254740993, "amount": Decimal("9007199254740991.91"), "balance": None}]
            database.x("insert into synthetic_raw_rows values (2, 0.01, null)")
            raise ValueError("synthetic rollback")
    assert database.q_raw("select count(*) n from synthetic_raw_rows") == [{"n": 0}]
    with database.transaction():
        database.x("insert into synthetic_raw_rows values (3, 0.02, null)")
        with database.raw_rows("select id from synthetic_raw_rows") as rows:
            assert next(rows) == {"id": 3}
        database.x("insert into synthetic_raw_rows values (4, 0.03, null)")
    assert database.q_raw("select id from synthetic_raw_rows order by id") == [{"id": 3}, {"id": 4}]


def test_raw_rows_keeps_shared_lock_until_context_exit_even_after_exhaustion(database):
    started, finished = threading.Event(), threading.Event()

    def query():
        started.set()
        result = database.q_raw("select 42 n")
        finished.set()
        return result

    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.raw_rows("select 1 n") as rows:
            assert list(rows) == [{"n": 1}] and database._lock._is_owned()
            pending = pool.submit(query)
            assert started.wait(3)
            assert not finished.wait(.05)
        assert pending.result(3) == [{"n": 42}]


def test_raw_rows_uses_snapshot_reader_and_same_select_guard_as_q_raw(database, monkeypatch):
    sql = "with synthetic_values as (select ?::decimal(18,2) amount) select * from synthetic_values"
    with database.read_snapshot() as active:
        assert active
        reader = database._read_state.connection
        with monkeypatch.context() as patch:
            connection, state = observe_connection(database, patch, reader, sql)
            patch.setattr(database._read_state, "connection", connection)

            def no_shared_connection():
                raise AssertionError("private snapshot must not use the shared connection")

            patch.setattr(database, "con", no_shared_connection)
            with database.raw_rows(sql, ["9007199254740991.91"]) as rows:
                assert list(rows) == [{"amount": Decimal("9007199254740991.91")}]
                assert not database._lock._is_owned()
            assert database.q_raw("select 7 n") == [{"n": 7}]
            for invalid in ("commit", "select 1; commit", "select 1; select 2", "update cases set name='Synthetic changed'", "insert into cases(name) values ('Synthetic forbidden') returning id"):
                with pytest.raises(RuntimeError, match="Only one SELECT"):
                    with database.raw_rows(invalid):
                        pytest.fail("non-SELECT entered a read snapshot")
                with pytest.raises(RuntimeError, match="Only one SELECT"):
                    database.q_raw(invalid)
            assert state["executions"] == [sql, "select 7 n"] and not state["closed"]
    assert not database.in_read_snapshot()
    assert database.q_raw("select count(*) n from cases") == [{"n": 1}]


def test_raw_rows_snapshot_stays_committed_while_shared_writer_proceeds(database):
    database.x("create table synthetic_raw_rows (id integer)")
    database.x("insert into synthetic_raw_rows values (1)")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.read_snapshot() as active:
            assert active
            with database.raw_rows("select id from synthetic_raw_rows order by id") as rows:
                assert not database._lock._is_owned()
                pool.submit(database.x, "insert into synthetic_raw_rows values (2)").result(3)
                assert list(rows) == [{"id": 1}]
            assert database.q_raw("select id from synthetic_raw_rows order by id") == [{"id": 1}]
    assert database.q_raw("select id from synthetic_raw_rows order by id") == [{"id": 1}, {"id": 2}]
