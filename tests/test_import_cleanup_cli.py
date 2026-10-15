"""The maintenance entry point only acts on an explicitly selected runtime."""
import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys

from fastapi import UploadFile

from app.services import imports


def test_cleanup_cli_defaults_to_plan_and_applies_only_registered_case_uploads(database, make_case):
    cid = make_case("Synthetic CLI cleanup")
    other = make_case("Other synthetic CLI case")
    content = "交易时间,付款账号,收款账号,交易金额\n2026-01-01 10:00:00,SYN-A,SYN-B,1.23\n".encode()
    previews = [asyncio.run(imports.upload(str(case), UploadFile(file=io.BytesIO(content), filename="synthetic.csv")))
                for case in (cid, other)]
    database.x("update import_sessions set expires_at=current_timestamp-interval '1 day'")
    paths = [database.DATA_DIR / "uploads" / f"{preview['import_id']}.csv" for preview in previews]
    unknown = database.DATA_DIR / "uploads" / "unknown.csv"
    unknown.write_bytes(b"synthetic unregistered file")
    database._con.close()
    database._con = None
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "scripts/cleanup_imports.py", "--data-dir", str(database.DATA_DIR), "--case-id", str(cid)]

    def run(*options):
        result = subprocess.run([*command, *options], cwd=root, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    plan = run()
    assert plan["dry_run"] and plan["eligible"] == 1 and plan["deleted"] == 0
    assert all(path.is_file() for path in paths)
    applied = run("--apply")
    assert not applied["dry_run"] and applied["deleted"] == 1 and applied["failed"] == 0
    assert not paths[0].exists() and paths[1].read_bytes() == content
    assert unknown.read_bytes() == b"synthetic unregistered file"
    assert database.q("select source_state from import_sessions where import_id=?", [previews[0]["import_id"]]) == [{"source_state": "cleaned"}]


def test_cleanup_cli_rejects_an_absent_database_without_creating_it(tmp_path):
    root = Path(__file__).resolve().parents[1]
    missing = tmp_path / "not-a-runtime"
    result = subprocess.run([sys.executable, "scripts/cleanup_imports.py", "--data-dir", str(missing)],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "existing managed runtime" in result.stderr
    assert not missing.exists()
