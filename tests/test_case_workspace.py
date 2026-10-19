"""Synthetic case-management behavior without a business server."""
from pathlib import Path
import subprocess


def test_case_workspace_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/case_workspace_harness.cjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
