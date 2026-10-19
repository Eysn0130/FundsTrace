"""Synthetic regressions for the classic graph's public interaction behavior."""
from pathlib import Path
import subprocess


def test_graph_interaction_behavior():
    root = Path(__file__).resolve().parents[1]
    # Keep a bounded watchdog for the expanded fixture on shared CI runners;
    # camera and interaction timing assertions still execute inside the suite.
    result = subprocess.run(["node", "--test", str(root / "tests/graph_interaction_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
