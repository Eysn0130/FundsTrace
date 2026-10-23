"""Synthetic literal-account targeting through the public graph locator."""
from pathlib import Path
import subprocess


def test_exact_graph_location_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/graph_location_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
