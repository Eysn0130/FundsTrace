"""Optimizer policy mutation is fenced from private reader lifetimes.

SET is simulated in the concurrent checks: a regression must fail safely rather
than exercise DuckDB's native shared-configuration race.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading

import duckdb
import pytest

from app import analysis_snapshot


def simulated_policy(database, monkeypatch):
    connection = database.con()
    state = {"policy": "", "sets": [], "ready": threading.Event(),
             "set": threading.Event(), "restore": threading.Event(), "committed": threading.Event()}

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return (self.value,)

    class Connection:
        def execute(self, sql, params=None):
            if sql == "select current_setting('disabled_optimizers')":
                return Result(state["policy"])
            if sql == "set disabled_optimizers=?":
                state["policy"] = params[0]
                state["sets"].append(params[0])
                state["set" if len(state["sets"]) == 1 else "restore"].set()
                return Result(None)
            result = connection.execute(sql, params or [])
            if sql == "commit":
                state["committed"].set()
            return result

        def __getattr__(self, name):
            return getattr(connection, name)

    proxy = Connection()

    def shared():
        state["ready"].set()
        return proxy

    monkeypatch.setattr(database, "con", shared)
    return state


@pytest.mark.parametrize("phase", ["begin", "body", "rollback", "close"])
def test_import_set_waits_for_entire_private_reader_lifetime(database, monkeypatch, phase):
    state = simulated_policy(database, monkeypatch)
    connect = database.duckdb.connect
    entered, release = threading.Event(), threading.Event()

    def pause():
        entered.set()
        assert release.wait(4)

    class Reader:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            if (phase == "begin" and sql == "begin transaction read only") or (phase == "rollback" and sql == "rollback"):
                pause()
            return self.connection.execute(sql, *args)

        def close(self):
            if phase == "close":
                pause()
            self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(database.duckdb, "connect", lambda *args, **kwargs: Reader(connect(*args, **kwargs)))

    def read():
        with database.read_snapshot() as active:
            assert active
            result = database.q_raw("select 7 n")
            if phase == "body":
                pause()
            return result

    def write():
        with database.import_transaction():
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(read)
        try:
            assert entered.wait(3)
            writer = pool.submit(write)
            assert state["ready"].wait(3)
            assert not state["set"].wait(.05)
        finally:
            release.set()
        assert reader.result(4) == [{"n": 7}]
        writer.result(4)
    assert state["sets"] == ["join_order,build_side_probe_side", ""]


def test_writer_body_allows_committed_progress_reader_but_restore_waits(database, monkeypatch):
    database.x("create table synthetic_optimizer_fence (id integer)")
    database.x("insert into synthetic_optimizer_fence values (1)")
    state = simulated_policy(database, monkeypatch)
    body, finish_body = threading.Event(), threading.Event()
    reading, finish_read = threading.Event(), threading.Event()

    def write():
        with database.import_transaction():
            database.x("insert into synthetic_optimizer_fence values (2)")
            body.set()
            assert finish_body.wait(4)

    def progress():
        with database.read_snapshot() as active:
            assert active
            result = database.q_raw("select id from synthetic_optimizer_fence order by id")
            reading.set()
            assert finish_read.wait(4)
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        try:
            assert body.wait(3)
            reader = pool.submit(progress)
            assert reading.wait(3), "import body must not retain reader admission"
            finish_body.set()
            assert state["committed"].wait(3)
            assert not state["restore"].wait(.05)
        finally:
            finish_body.set()
            finish_read.set()
        writer.result(4)
        assert reader.result(4) == [{"id": 1}]
    assert state["policy"] == ""
    assert database.q_raw("select id from synthetic_optimizer_fence order by id") == [{"id": 1}, {"id": 2}]


def forbid_inverted_writer_lock(database, monkeypatch):
    lock, admission = database._lock, database._read_admission

    class Lock:
        def __enter__(self):
            assert not admission._is_owned(), "reader admission must be released before waiting for writer lock"
            return lock.__enter__()

        def __exit__(self, *args):
            return lock.__exit__(*args)

        def __getattr__(self, name):
            return getattr(lock, name)

    monkeypatch.setattr(database, "_lock", Lock())


@contextmanager
def snapshot_entry(database, case_id, entry):
    manager = database.read_snapshot() if entry == "raw" else analysis_snapshot.case_read_snapshot(case_id)
    with manager as active:
        yield active


@pytest.mark.parametrize("entry", ["raw", "case"])
def test_fresh_snapshot_initializes_before_reader_admission(database, make_case, monkeypatch, entry):
    cid = make_case("Synthetic optimizer fence initialization")
    with database._lock:
        database._con.close()
        database._con = None
    monkeypatch.setattr(database, "_read_ready", None)
    forbid_inverted_writer_lock(database, monkeypatch)
    with snapshot_entry(database, cid, entry):
        assert database.q_raw("select 7 n") == [{"n": 7}]
    assert database._read_ready[0] is database._con


@pytest.mark.parametrize("entry", ["raw", "case"])
def test_unsupported_snapshot_closes_reader_before_unfenced_locked_fallback(database, make_case, monkeypatch, entry):
    cid = make_case("Synthetic optimizer fence fallback")
    connect, opened = database.duckdb.connect, []

    class Reader:
        def __init__(self, connection):
            self.connection, self.closed = connection, False

        def execute(self, sql, *args):
            assert sql == "begin transaction read only"
            raise duckdb.ParserException("synthetic unsupported READ ONLY")

        def close(self):
            self.connection.close()
            self.closed = True

    def private(*args, **kwargs):
        reader = Reader(connect(*args, **kwargs))
        opened.append(reader)
        return reader

    monkeypatch.setattr(database.duckdb, "connect", private)
    forbid_inverted_writer_lock(database, monkeypatch)
    with snapshot_entry(database, cid, entry) as active:
        assert not active and not database.in_read_snapshot()
        assert database._lock._is_owned() and opened[0].closed
        assert database.q_raw("select 7 n") == [{"n": 7}]
    assert len(opened) == 1 and opened[0].closed


def test_missing_case_generation_releases_reader_admission_before_initialization(database, make_case, monkeypatch):
    cid = make_case("Synthetic optimizer fence generation")
    assert database.q_raw("select revision from analysis_state where case_id=?", [cid]) == []
    forbid_inverted_writer_lock(database, monkeypatch)
    with analysis_snapshot.case_read_snapshot(cid):
        assert database._lock._is_owned() and not database.in_read_snapshot()
        assert database.q_raw("select revision from analysis_state where case_id=?", [cid])


def test_snapshot_binding_change_while_waiting_for_admission_fails_without_retry(database, monkeypatch):
    admission = database._read_admission

    class Gate:
        def __enter__(self):
            admission.__enter__()
            monkeypatch.setattr(database, "_read_ready", None)

        def __exit__(self, *args):
            return admission.__exit__(*args)

    def no_retry(*args, **kwargs):
        raise AssertionError("binding change must not initialize or open another reader")

    monkeypatch.setattr(database, "_read_admission", Gate())
    monkeypatch.setattr(database, "con", no_retry)
    monkeypatch.setattr(database.duckdb, "connect", no_retry)
    with pytest.raises(RuntimeError, match="binding changed"):
        with database.read_snapshot():
            pytest.fail("changed binding entered a snapshot")


def test_nested_import_preserves_outer_policy_and_failure_releases_fence(database, monkeypatch):
    state = simulated_policy(database, monkeypatch)
    with database.transaction():
        with database.import_transaction():
            assert state["sets"] == []
    with pytest.raises(ValueError, match="synthetic rollback"):
        with database.import_transaction():
            with database.import_transaction():
                assert state["sets"] == ["join_order,build_side_probe_side"]
                raise ValueError("synthetic rollback")
    assert state["sets"] == ["join_order,build_side_probe_side", ""]

    def read():
        with database.read_snapshot() as active:
            assert active
            return database.q_raw("select 7 n")

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(read).result(3) == [{"n": 7}]
