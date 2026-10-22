"""Case-bound account commands and parent-owned iframe mutation boundary."""
from pathlib import Path
import subprocess


def test_account_navigation_parent_commands_and_legacy_guards():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/account_navigation_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
