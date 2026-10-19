"""Graph presentation state is serializable, whitelisted and display-only."""
from pathlib import Path
import subprocess


def test_graph_presentation_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/graph_presentation_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
