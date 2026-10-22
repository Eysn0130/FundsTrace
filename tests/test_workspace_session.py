"""Execute shipped session/switch/table adapters with synthetic DOM and GETs."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_session_switch_restore_and_no_write_boundaries():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/workspace_session_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
