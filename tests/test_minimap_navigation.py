"""Minimap disclosure, camera controls and appearance use synthetic DOM and host callbacks."""
from pathlib import Path
import subprocess


def test_minimap_navigation_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/minimap_navigation_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
