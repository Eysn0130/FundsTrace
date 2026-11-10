"""Synthetic import-view races and analysis text boundaries, without a server."""
from pathlib import Path
import io
import sqlite3
import subprocess

from openpyxl import Workbook
import pytest


def test_workflow_ui_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/workflow_ui_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_legacy_bad_csv_has_a_machine_classified_file_failure(client, database, make_case):
    cid = make_case("Synthetic queue file failure")
    response = client.post(f"/api/import/preview?case_id={cid}", files={"file": (
        "bad.csv", b"date,payer_account,payee_account,amount\n2026-01-01,SYN-A,SYN-B,1.01,unexpected\n")})
    assert response.status_code == 400 and response.json()["detail"]
    assert response.headers["x-fundstrace-import-error-code"] == "invalid_table_file"
    assert response.headers["x-fundstrace-import-failure-kind"] == "file"
    assert database.q_raw("select id from flows where case_id=?", [cid]) == []


@pytest.mark.parametrize("fault", [MemoryError, OSError, sqlite3.OperationalError, RuntimeError])
def test_wrapped_excel_resource_or_unknown_failure_cannot_continue_a_queue(
        client, database, make_case, monkeypatch, fault):
    import openpyxl

    book = Workbook()
    book.active.append(["date", "payer_account", "payee_account", "amount"])
    book.active.append(["2026-01-01", "SYN-A", "SYN-B", "1.01"])
    stream = io.BytesIO(); book.save(stream); book.close()
    cid = make_case("Synthetic queue resource failure")

    def fail(*args, **kwargs):
        raise fault("SYN-INTERNAL-RESOURCE-MARKER")

    monkeypatch.setattr(openpyxl, "load_workbook", fail)
    response = client.post(f"/api/import/preview?case_id={cid}", files={"file": ("synthetic.xlsx", stream.getvalue())})
    assert response.status_code == 400
    assert response.headers["x-fundstrace-import-failure-kind"] == "unknown"
    assert "SYN-INTERNAL-RESOURCE-MARKER" not in response.text
    assert database.q_raw("select id from flows where case_id=?", [cid]) == []
    assert database.q_raw("select import_id from import_sessions where case_id=?", [cid]) == []


@pytest.mark.parametrize("chain, expected", [("format", "file"), ("hidden-resource", "unknown"),
                                             ("cycle", "unknown")])
def test_public_excel_failure_checks_both_exception_links_and_cycles(
        client, make_case, monkeypatch, chain, expected):
    import openpyxl
    from app.table_import import TableImportError

    book = Workbook()
    book.active.append(["date", "amount"])
    stream = io.BytesIO(); book.save(stream); book.close()
    cid = make_case("Synthetic queue exception chain")
    error = TableImportError("合成格式错误")
    error.__cause__ = UnicodeError("SYN-FORMAT")
    if chain == "hidden-resource":
        error.__context__ = MemoryError("SYN-INTERNAL-RESOURCE-MARKER")
        error.__suppress_context__ = True
    elif chain == "cycle":
        error.__cause__ = error

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(openpyxl, "load_workbook", fail)
    response = client.post(f"/api/import/preview?case_id={cid}", files={"file": ("synthetic.xlsx", stream.getvalue())})
    assert response.status_code == 400
    assert response.headers["x-fundstrace-import-failure-kind"] == expected
    assert "SYN-INTERNAL-RESOURCE-MARKER" not in response.text
