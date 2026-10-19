"""Ribbon adapters preserve case/canvas state, exact facts and editor history."""
from pathlib import Path
import subprocess


def test_ribbon_commands_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/ribbon_commands_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
