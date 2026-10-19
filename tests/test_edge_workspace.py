"""Synthetic edge workspace display and case-bound tracked membership actions."""
from pathlib import Path
import subprocess


def test_edge_workspace_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/edge_workspace_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
