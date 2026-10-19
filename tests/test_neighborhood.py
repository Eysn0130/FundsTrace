"""Neighborhood controller behavior uses only synthetic, binding-scoped state."""
from pathlib import Path
import subprocess


def test_neighborhood_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/neighborhood_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
