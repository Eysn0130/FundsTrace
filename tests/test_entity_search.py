"""Case-bound legacy entity provider and progressive-enhancement contracts."""
from pathlib import Path
import subprocess


def test_entity_search_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/entity_search_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
