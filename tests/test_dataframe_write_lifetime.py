"""Bulk scans release Python frames inside the original atomic transaction."""
import datetime as dt
from decimal import Decimal
import gc
import weakref

import duckdb
import pandas as pd
import pytest


def watch_selections(monkeypatch):
    select, references = pd.DataFrame.__getitem__, []
    def observe(frame, key):
        value = select(frame, key)
        if isinstance(key, list) and isinstance(value, pd.DataFrame):
            references.append(weakref.ref(value))
        return value
    monkeypatch.setattr(pd.DataFrame, "__getitem__", observe)
    return references


def test_bulk_frames_do_not_accumulate_across_tables_in_one_transaction(database, monkeypatch):
    references = watch_selections(monkeypatch)
    enabled = gc.isenabled()
    gc.disable()
    try:
        with database.transaction():
            for index in range(4):
                source_id = f"synthetic-source-{index}"
                for name, row in (
                    ("flows", {"id": 100 + index, "case_id": 1, "payer_account": "SYN-A", "payee_account": "SYN-B", "amount": "12.34"}),
                    ("import_members", {"case_id": 1, "source_id": source_id, "member_path": "synthetic.csv", "role": "table", "sha256": "a" * 64, "size_bytes": 1, "encoding": "utf-8", "row_count": 1}),
                    ("flow_sources", {"case_id": 1, "source_id": source_id, "source_row": 1, "flow_id": 100 + index, "disposition": "imported"}),
                    ("import_records", {"case_id": 1, "source_id": source_id, "record_no": 1, "member_path": "synthetic.csv", "source_row": 2, "record_key": "b" * 64, "disposition": "imported", "reason_code": "transaction_valid", "details_json": '{"synthetic":"合成\\n内容"}'}),
                    ("resolved_endpoints", {"case_id": 1, "flow_id": 100 + index, "role": "payer", "entity_id": "fte_synthetic", "state": "resolved", "reason": "synthetic proof"}),
                ):
                    frame = pd.DataFrame([row])
                    database.insert_dataframe(name, frame, list(row))
                    del frame
                    assert all(reference() is None for reference in references)
        assert len(references) == 20
    finally:
        if enabled:
            gc.enable()
        gc.collect()  # Test-only cleanup for the intentionally failing old path.


def test_explicit_columns_preserve_order_exact_decimal_nulls_and_time(database):
    frame = pd.DataFrame([
        {"id": "9007199254740993", "case_id": 1, "trade_time": pd.Timestamp("2026-01-01 01:02:03.123456"), "amount": "900719925474099.91", "balance": "9999999999999999.99", "payer_account": "SYN-甲", "payee_account": "", "summary": None, "ignored": "SYN-DO-NOT-INSERT"},
        {"id": "9007199254740994", "case_id": 1, "trade_time": pd.NaT, "amount": "0.01", "balance": None, "payer_account": "", "payee_account": "SYN-乙", "summary": "", "ignored": "SYN-DO-NOT-INSERT"},
    ], index=[17, 4])
    columns = ["summary", "payee_account", "balance", "amount", "trade_time", "payer_account", "case_id", "id"]
    with database.transaction():
        database.insert_dataframe("flows", frame, columns)
    rows = database.q_raw(f"select {','.join(columns)} from flows order by id")
    assert rows == [
        {"id": 9007199254740993, "case_id": 1, "trade_time": dt.datetime(2026, 1, 1, 1, 2, 3, 123456), "amount": Decimal("900719925474099.91"), "balance": Decimal("9999999999999999.99"), "payer_account": "SYN-甲", "payee_account": "", "summary": None},
        {"id": 9007199254740994, "case_id": 1, "trade_time": None, "amount": Decimal("0.01"), "balance": None, "payer_account": "", "payee_account": "SYN-乙", "summary": ""},
    ]


def test_repeated_scans_rollback_every_table_and_leave_no_catalog_relation(database, monkeypatch):
    references = watch_selections(monkeypatch)
    with pytest.raises(RuntimeError, match="Synthetic interruption"):
        with database.transaction():
            for index in range(3):
                database.insert_dataframe("flows", pd.DataFrame([{"id": index + 200, "amount": "1.01"}]), ["id", "amount"])
                database.insert_dataframe("flow_sources", pd.DataFrame([{"case_id": 1, "source_id": f"synthetic-{index}", "source_row": 1, "flow_id": index + 200, "disposition": "imported"}]), ["case_id", "source_id", "source_row", "flow_id", "disposition"])
            raise RuntimeError("Synthetic interruption")
    assert database.q_raw("select count(*) n from flows") == [{"n": 0}]
    assert database.q_raw("select count(*) n from flow_sources") == [{"n": 0}]
    assert database.q_raw("select table_name from information_schema.tables where lower(table_name) in ('_ins_df','_funds_trace_insert_frame')") == []
    assert all(reference() is None for reference in references)
    with database.transaction():
        database.insert_dataframe("flows", pd.DataFrame([{"id": 200, "amount": "0.01"}]), ["id", "amount"])
    assert database.q_raw("select id,amount from flows") == [{"id": 200, "amount": Decimal("0.01")}]


@pytest.mark.parametrize("kind", ["table", "view"])
def test_catalog_name_collision_fails_instead_of_scanning_another_relation(database, kind):
    if kind == "table":
        database.x('create temp table "_FUNDS_TRACE_INSERT_FRAME"(id bigint,amount varchar)')
        database.x('insert into "_FUNDS_TRACE_INSERT_FRAME" values (777,\'99.99\')')
    else:
        database.x('create temp view "_FUNDS_TRACE_INSERT_FRAME" as select 777 id,\'99.99\' amount')
    with pytest.raises(ValueError, match="Bulk scan name is already in use"):
        with database.transaction():
            database.insert_dataframe("flows", pd.DataFrame([{"id": 201, "amount": "1.01"}]), ["id", "amount"])
    assert database.q_raw("select count(*) n from flows") == [{"n": 0}]
    assert database.q_raw('select * from "_FUNDS_TRACE_INSERT_FRAME"') == [{"id": 777, "amount": "99.99"}]


def test_native_constraint_error_rolls_back_previous_chunks_and_allows_retry(database):
    with pytest.raises(duckdb.ConstraintException):
        with database.transaction():
            for _ in range(2):
                database.insert_dataframe("flows", pd.DataFrame([{"id": 301, "amount": "1.01"}]), ["id", "amount"])
    assert database.q_raw("select count(*) n from flows") == [{"n": 0}]
    with database.transaction():
        database.insert_dataframe("flows", pd.DataFrame([{"id": 301, "amount": "1.01"}]), ["id", "amount"])
    assert database.q_raw("select id,amount from flows") == [{"id": 301, "amount": Decimal("1.01")}]
