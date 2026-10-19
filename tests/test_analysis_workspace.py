"""Synthetic whole-case analysis workspace behavior without a business server."""
from pathlib import Path
import subprocess


def test_analysis_workspace_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/analysis_workspace_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
