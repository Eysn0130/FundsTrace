"""Historical verification fails closed; normal startup keeps temporary runtime state."""
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from app import license as licensing
from app.main import app


def test_unconfigured_signing_material_does_not_block_normal_startup(database, monkeypatch):
    key = licensing.issue("SYNTHETIC-MACHINE")
    monkeypatch.delenv("FUNDS_TRACE_LICENSE_SECRET", raising=False)
    assert licensing.verify(key, "SYNTHETIC-MACHINE") is None
    with TestClient(app) as client:
        assert client.get("/api/cases").status_code == 200
        info = client.get("/api/license/info").json()
        assert info["activated"] is False
        assert info["activation_required"] is False
        assert client.get("/license").status_code == 200


def test_configured_license_still_checks_machine_and_signature(database):
    key = licensing.issue("SYNTHETIC-MACHINE")
    assert licensing.verify(key, "SYNTHETIC-MACHINE") == "0"
    assert licensing.verify(key, "OTHER-MACHINE") is None
    assert licensing.verify(key + "0", "SYNTHETIC-MACHINE") is None


def test_runtime_paths_share_one_explicit_data_directory(tmp_path):
    runtime = tmp_path / "isolated-runtime"
    env = dict(os.environ, FUND_DATA_DIR=str(runtime), PYTHONDONTWRITEBYTECODE="1")
    code = """
from pathlib import Path
from app import db, license
import run
assert Path(license.LICENSE_PATH).parent == db.DATA_DIR
assert Path(run.LOG_PATH).parent == db.DATA_DIR
assert db.DB_PATH.parent == db.DATA_DIR
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
