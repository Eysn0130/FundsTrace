"""Transaction behavior against isolated, synthetic DuckDB databases."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest


def test_transaction_yields_connection_and_reenters_query_helpers(database):
    with database.transaction() as connection:
        assert connection is database.con()
        database.x("insert into cases(name) values (?)", ["Outer"])
        with database.transaction() as nested:
            assert nested is connection
            database.executemany("insert into cases(name) values (?)", [["Nested A"], ["Nested B"]])
        assert database.q("select count(*) n from cases where name like 'Nested%'") == [{"n": 2}]

    assert database.q("select name from cases where name != '默认案件' order by name") == [
        {"name": "Nested A"}, {"name": "Nested B"}, {"name": "Outer"},
    ]


def test_transaction_rolls_back_and_recovers_after_exception(database):
    with pytest.raises(ValueError, match="synthetic failure"):
        with database.transaction():
            database.x("insert into cases(name) values ('Rolled back')")
            raise ValueError("synthetic failure")

    assert database.q("select count(*) n from cases where name = 'Rolled back'") == [{"n": 0}]
    with database.transaction():
        database.x("insert into cases(name) values ('Recovered')")
    assert database.q("select count(*) n from cases where name = 'Recovered'") == [{"n": 1}]


def test_caught_nested_failure_marks_outer_transaction_rollback_only(database):
    with pytest.raises(RuntimeError, match="rollback-only"):
        with database.transaction():
            database.x("insert into cases(name) values ('Before inner failure')")
            try:
                with database.transaction():
                    database.x("insert into cases(name) values ('Inside failure')")
                    raise ValueError("synthetic inner failure")
            except ValueError:
                pass
            database.x("insert into cases(name) values ('After inner failure')")

    assert database.q("select name from cases") == [{"name": "默认案件"}]
    with database.transaction():
        database.x("insert into cases(name) values ('Recovered')")
    assert database.q("select count(*) n from cases where name = 'Recovered'") == [{"n": 1}]


def test_transaction_holds_lock_until_completion(database):
    entered = threading.Event()
    completed = threading.Event()

    def write_from_other_thread():
        entered.set()
        database.x("insert into cases(name) values ('Other thread')")
        completed.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.transaction():
            database.x("insert into cases(name) values ('Transaction owner')")
            future = executor.submit(write_from_other_thread)
            assert entered.wait(5)
            assert not completed.wait(0.05)
            assert database.q("select count(*) n from cases where name = 'Other thread'") == [{"n": 0}]
        future.result(timeout=5)

    assert completed.is_set()
    assert database.q("select count(*) n from cases where name = 'Other thread'") == [{"n": 1}]
