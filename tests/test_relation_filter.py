"""Draft relation filtering exercises the real controller and exact graph policy."""
from pathlib import Path
import subprocess


def test_relation_filter_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/relation_filter_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
