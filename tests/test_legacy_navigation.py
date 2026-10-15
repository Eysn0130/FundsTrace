"""Exercise browser state transitions with synthetic DOM and deferred responses."""
from pathlib import Path
import subprocess


def test_legacy_navigation_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/legacy_navigation_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
