"""Node-finder interaction uses synthetic graph snapshots and camera callbacks."""
from pathlib import Path
import subprocess


def test_node_finder_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/node_finder_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
