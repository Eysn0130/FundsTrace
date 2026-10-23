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


def test_workspace_view_menu_is_distinct_from_node_layout_and_preserves_details():
    page = WorkspaceMarkup()
    page.feed((ROOT / 'static/index.html').read_text())
    trigger = page.tags['btnWorkspaceView'][1]
    assert trigger['data-toolbar-menu'] == 'workspaceViewMenu'
    assert trigger['aria-haspopup'] == 'menu'
    assert 'data-ribbon-command' not in trigger
    assert page.tags['workspace'][1]['data-workspace-view'] == 'standard'
    for element, view in [('workspaceViewStandard', 'standard'), ('workspaceViewFocus', 'focus'), ('workspaceViewSplit', 'split')]:
        attrs = page.tags[element][1]
        assert attrs['role'] == 'menuitemradio'
        assert attrs['data-workspace-view'] == view
        assert attrs['aria-checked'] == str(view == 'standard').lower()
    assert page.tags['workspaceViewMenu'][1]['role'] == 'menu'
    assert page.tags['layoutSel'][1]['aria-label'] == '布局算法'
    assert page.tags['drawer'][0] == 'aside'
    assert page.flow_headers == 7
