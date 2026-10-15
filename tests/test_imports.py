"""Import atomicity and case isolation using real temporary DuckDB state."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading

import pytest

from app import importer


def test_import_preserves_deduplication_amounts_and_account_candidates(database, make_case, flow_row):
    case_id = make_case()
    database.x("insert into main_accounts(case_id, account, name) values (?,?,?)",
               [case_id, "SYN-A", "Synthetic sender"])
    rows = [flow_row(), flow_row(), flow_row(summary="Separate transaction")]
    result = importer.commit_rows("synthetic.csv", "A", rows,
                                  stats={"total": 4, "skipped": 1}, case_id=case_id)

    assert result == {
        "total": 4, "skipped": 1, "ok": 2, "rows_dup": 1, "batch_id": result["batch_id"],
        "self_accounts": [{"account": "SYN-A", "name": "Synthetic sender"}],
        "self_accounts_count": 1, "main_matched": ["SYN-A"], "accounts_in_batch": 2,
    }
    amounts = database.con().execute("select amount, balance from flows where case_id = ?", [case_id]).fetchall()
    assert amounts == [(Decimal("123.45"), Decimal("1000.00"))] * 2
    assert database.q("select rows_total, rows_imported, rows_skipped, rows_dup from batches where id = ?",
                      [result["batch_id"]]) == [
        {"rows_total": 4, "rows_imported": 2, "rows_skipped": 1, "rows_dup": 1},
    ]

    repeated = importer.commit_rows("repeat.csv", "A", rows, case_id=case_id)
    assert repeated["ok"] == 0
    assert repeated["rows_dup"] == 3
    assert repeated["self_accounts"] == result["self_accounts"]
    assert repeated["self_accounts_count"] == 1
    assert database.q("select count(*) n from flows where case_id = ?", [case_id]) == [{"n": 2}]


def test_identical_rows_can_be_imported_in_different_cases(database, make_case, flow_row):
    first, second = make_case("First case"), make_case("Second case")

    for case_id in (first, second):
        result = importer.commit_rows("synthetic.csv", "A", [flow_row()], case_id=case_id)
        assert result["ok"] == 1
        assert result["rows_dup"] == 0

    assert database.q("select case_id, count(*) n from flows group by case_id order by case_id") == [
        {"case_id": first, "n": 1}, {"case_id": second, "n": 1},
    ]


@pytest.mark.parametrize("empty", [False, True])
def test_import_rejects_missing_case_without_creating_orphans(database, flow_row, empty):
    with pytest.raises(importer.ImportError, match="案件"):
        importer.commit_rows("synthetic.csv", "A", [] if empty else [flow_row()], case_id=987654321)

    assert database.q("select count(*) n from batches") == [{"n": 0}]
    assert database.q("select count(*) n from flows") == [{"n": 0}]


def test_later_chunk_failure_rolls_back_batch_and_all_imported_rows(database, make_case, flow_row, monkeypatch):
    case_id = make_case()
    original = importer.commit_rows("existing.csv", "A", [flow_row(trade_no="EXISTING")], case_id=case_id)
    original_insert = database.insert_dataframe
    inserted_chunk_sizes = []

    def fail_second_chunk(table, frame, columns):
        inserted_chunk_sizes.append(len(frame))
        if len(inserted_chunk_sizes) == 2:
            raise RuntimeError("synthetic later chunk failure")
        original_insert(table, frame, columns)
        # The first chunk really reached DuckDB before the injected write failure.
        assert database.q("select count(*) n from flows where case_id = ?", [case_id]) == [{"n": 50001}]

    monkeypatch.setattr(database, "insert_dataframe", fail_second_chunk)
    rows = [flow_row(trade_no=f"SYN-{i:06}") for i in range(50001)]
    with pytest.raises(RuntimeError, match="synthetic later chunk failure"):
        importer.commit_rows("failing.csv", "A", rows, case_id=case_id)

    assert inserted_chunk_sizes == [50000, 1]
    assert database.q("select trade_no from flows where case_id = ?", [case_id]) == [{"trade_no": "EXISTING"}]
    assert database.q("select id, filename from batches where case_id = ?", [case_id]) == [
        {"id": original["batch_id"], "filename": "existing.csv"},
    ]
    monkeypatch.setattr(database, "insert_dataframe", original_insert)
    recovered = importer.commit_rows("recovered.csv", "A", [flow_row(trade_no="RECOVERED")], case_id=case_id)
    assert recovered["ok"] == 1


def test_concurrent_identical_imports_store_each_flow_once(database, make_case, flow_row):
    case_id = make_case()
    rows = [flow_row(trade_no=f"SYN-{i:04}") for i in range(100)]
    start = threading.Barrier(2)

    def run_import(filename):
        start.wait(timeout=5)
        return importer.commit_rows(filename, "A", rows, case_id=case_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_import, filename) for filename in ("first.csv", "second.csv")]
        results = [future.result(timeout=15) for future in futures]

    assert sorted(result["ok"] for result in results) == [0, 100]
    assert sorted(result["rows_dup"] for result in results) == [0, 100]
    assert database.q("select count(*) n from flows where case_id = ?", [case_id]) == [{"n": 100}]
    assert database.q("select count(*) n, sum(rows_imported) imported, sum(rows_dup) duplicates "
                      "from batches where case_id = ?", [case_id]) == [
        {"n": 2, "imported": 100, "duplicates": 100},
    ]
