"""Synthetic stable expansion, directed layers and structural partition geometry."""
from pathlib import Path
import subprocess


def test_graph_expansion_layout_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/graph_expansion_layout_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
