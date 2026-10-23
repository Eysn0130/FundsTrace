"""Shared candidate behavior uses synthetic entities and a DOM harness, not a business server."""
from pathlib import Path
import subprocess


def test_entity_picker_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/entity_picker_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
