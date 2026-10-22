"""Session-only case/canvas work restoration with no cached business facts."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_workspace_drafts_isolation_safe_fields_and_explicit_cleanup():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/workspace_drafts_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
