"""Shared presentation core behavior with disposable synthetic clocks and scopes."""
from pathlib import Path
import subprocess


def test_feedback_core_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/feedback_core_harness.mjs")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
