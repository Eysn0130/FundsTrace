"""Host-owned iframe navigation, binding validation and stable page surfaces."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_app_navigation_iframe_requests_and_case_binding():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/app_navigation_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
