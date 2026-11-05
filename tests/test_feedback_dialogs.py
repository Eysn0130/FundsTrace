"""Public confirmation decisions, revocation and focus with a synthetic DOM."""
from pathlib import Path
import subprocess


def test_feedback_dialogs_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/feedback_dialogs_harness.mjs")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
