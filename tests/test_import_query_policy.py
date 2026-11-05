"""Import-only query planning cannot leak through success or SQL rollback."""
import duckdb
import pytest


def policy(database):
    return database.con().execute("select current_setting('disabled_optimizers')").fetchone()[0]


@pytest.mark.parametrize("failure", [None, "python", "sql"])
@pytest.mark.parametrize("previous", ["", "filter_pushdown", "join_order"])
def test_import_query_policy_restores_original_after_transaction(database, failure, previous):
    connection = database.con()
    connection.execute("set disabled_optimizers=?", [previous])
    original = policy(database)
    connection.execute("create temporary table synthetic_policy(value integer)")
    try:
        with database.import_transaction():
            assert {"join_order", "build_side_probe_side"} <= set(policy(database).split(","))
            assert set(original.split(",")) - {""} <= set(policy(database).split(","))
            connection.execute("insert into synthetic_policy values (1)")
            if failure == "python":
                raise RuntimeError("Synthetic failure")
            if failure == "sql":
                # This aborts DuckDB's transaction; SET cannot restore policy
                # until the outer transaction has issued ROLLBACK.
                connection.execute("insert into synthetic_policy values ('invalid integer')")
    except (RuntimeError, duckdb.ConversionException):
        assert failure is not None
    else:
        assert failure is None
    assert policy(database) == original
    assert connection.execute("select count(*) from synthetic_policy").fetchone()[0] == (0 if failure else 1)
    connection.execute("set disabled_optimizers=''")


def test_nested_import_respects_outer_query_policy_and_rollback(database):
    original = policy(database)
    with database.transaction():
        with database.import_transaction():
            assert policy(database) == original
    assert policy(database) == original
