"""Synthetic dialog focus, keyboard and pointer behavior without a browser."""
from pathlib import Path
import subprocess


def test_ui_dialogs_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/ui_dialogs_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
