"""Task observation never owns writes or crosses a revoked case binding."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_feedback_workspace_public_behavior():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/feedback_workspace_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
