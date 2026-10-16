"""Workspace chrome contracts; graph behavior is verified by its own harnesses."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tags = {}
        self.flow_headers = 0
        self.in_flow_table = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
            self.tags[attrs['id']] = (tag, attrs)
        if tag == 'table' and attrs.get('id') == 'flowTable':
            self.in_flow_table = True
        if tag == 'th' and self.in_flow_table:
            self.flow_headers += 1

    def handle_endtag(self, tag):
        if tag == 'table':
            self.in_flow_table = False


def test_workspace_preserves_unique_controls_and_real_seven_column_table():
    page = WorkspaceMarkup()
    page.feed((ROOT / 'static/index.html').read_text())
    assert not [name for name, count in Counter(page.ids).items() if count > 1]
    assert page.flow_headers == 7
    assert page.tags['graphToolbar'][1]['role'] == 'group'
    for name in ('btnUndo', 'btnRedo', 'btnCanvasSave', 'btnCanvasNew', 'btnCanvasClone',
                 'btnAutoMain', 'btnClearAutoMain', 'btnSidebarToggle', 'btnToolbarToggle'):
        assert page.tags[name][0] == 'button'
    for name in ('btnSidebarToggle', 'btnToolbarToggle'):
        assert page.tags[name][1]['type'] == 'button'
        assert page.tags[name][1]['aria-expanded'] == 'true'
    assert page.tags['btnSidebarToggle'][1]['aria-controls'] == 'left'
    assert page.tags['btnToolbarToggle'][1]['aria-controls'] == 'graphToolbar'
    assert 'data-action-dialog' not in page.tags['btnDisplayActions'][1]
    assert 'data-action-dialog' not in page.tags['btnMainActions'][1]


def test_workspace_shell_state_and_resize_behavior():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/workspace_shell_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
