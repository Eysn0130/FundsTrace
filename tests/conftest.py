"""Synthetic in-process API fixtures; never attach to an existing server."""
import os
from pathlib import Path
import tempfile

import pytest

# Set these before importing any application module, including during collection.
# Override inherited values even when pytest is run without scripts/test.py.
_sandbox = tempfile.TemporaryDirectory(prefix="funds-trace-pytest-")
os.environ["FUND_DATA_DIR"] = str(Path(_sandbox.name) / "data")
os.environ["MPLCONFIGDIR"] = str(Path(_sandbox.name) / "matplotlib")
os.environ["FUNDS_TRACE_LICENSE_SECRET"] = "synthetic-regression-key-not-for-use"

from app import db, license as licensing  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def database(tmp_path, monkeypatch):
    """One database per test, with no checkpoint thread sharing a closed handle."""
    with db._lock:
        if db._con is not None:
            db._con.close()
        db._con = None
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(db, "DATA_DIR", data_dir)
        monkeypatch.setattr(db, "DB_PATH", data_dir / "fund.duckdb")
        monkeypatch.setattr(db, "_start_ckpt", lambda: None)
        monkeypatch.setattr(licensing, "LICENSE_PATH", str(data_dir / "license.dat"))
        db.con()
    yield db
    with db._lock:
        if db._con is not None:
            db._con.close()
        db._con = None


@pytest.fixture
def client(database, monkeypatch):
    # Authenticate the real middleware using a synthetic, machine-bound license.
    monkeypatch.setattr(licensing, "machine_code", lambda: "SYNTHETIC-TEST-MACHINE")
    licensing.save_key(licensing.issue(licensing.machine_code()))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_case(database):
    def create(name="Synthetic case"):
        return database.q("insert into cases(name) values (?) returning id", [name])[0]["id"]
    return create


@pytest.fixture
def flow_row():
    def create(**overrides):
        row = {
            "trade_no": "SYN-001", "trade_time": "2026-01-01 10:00:00",
            "payer_name": "Synthetic sender", "payer_account": "SYN-A", "payer_bank": "",
            "payee_name": "Synthetic recipient", "payee_account": "SYN-B", "payee_bank": "",
            "amount": 123.45, "balance": 1000.00, "currency": "CNY", "summary": "synthetic",
            "channel": "test", "self_account": "SYN-A", "self_name": "Synthetic sender",
        }
        row.update(overrides)
        return row
    return create


def pytest_sessionfinish(session, exitstatus):
    with db._lock:
        if db._con is not None:
            db._con.close()
            db._con = None
    _sandbox.cleanup()
