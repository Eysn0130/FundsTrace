"""Exercise browser state transitions with synthetic DOM and deferred responses."""
from pathlib import Path
import subprocess


def test_legacy_navigation_behavior():
    root = Path(__file__).resolve().parents[1]
    # Match the graph/canvas wrappers for this expanded shared fixture. Its
    # 1000-node layout and full navigation suite need CPU time on CI runners;
    # browser timers are synthetic and all behavior assertions remain active.
    result = subprocess.run(["node", "--test", str(root / "tests/legacy_navigation_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
