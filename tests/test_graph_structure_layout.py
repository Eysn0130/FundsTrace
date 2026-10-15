"""Deterministic, cancellable synthetic structural layout contract."""
from pathlib import Path
import subprocess


def test_graph_structure_layout_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/graph_structure_layout_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
