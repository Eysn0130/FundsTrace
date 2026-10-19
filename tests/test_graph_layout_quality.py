"""Bounded, immutable display-layout assessment over synthetic graphs."""
from pathlib import Path
import subprocess


def test_graph_layout_quality_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/graph_layout_quality_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
