"""Exercise case-bound main-account management through its real host boundary."""
from pathlib import Path
import subprocess


def test_main_accounts_controller_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/main_accounts_harness.mjs"),
                             str(root / "tests/main_accounts_view_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
