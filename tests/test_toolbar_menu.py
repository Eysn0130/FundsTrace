"""Shared toolbar menu keyboard, pointer and placement behavior on synthetic DOM."""
from pathlib import Path
import subprocess


def test_toolbar_menu_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/toolbar_menu_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
