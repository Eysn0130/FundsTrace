"""Synthetic parent-owned v1 import leases; no browser or business server."""
from pathlib import Path
import subprocess


def test_parent_owned_import_leases():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/import_workflow_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
