"""Synthetic DOM/event regression for conditional principal UI bindings."""
from pathlib import Path
import subprocess


def test_principal_analysis_ui():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/principal_analysis_ui_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
