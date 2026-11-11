"""Run actual classic-script trace entry and relationship chooser regressions."""
from pathlib import Path
import subprocess


def test_bound_trace_anchor_chooser():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/trace_anchor_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
