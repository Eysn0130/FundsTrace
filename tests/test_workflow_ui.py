"""Synthetic import-view races and analysis text boundaries, without a server."""
from pathlib import Path
import subprocess


def test_workflow_ui_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/workflow_ui_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
