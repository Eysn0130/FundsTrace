"""Synthetic public query/table behavior through the frozen legacy renderer."""
from html.parser import HTMLParser
from pathlib import Path
import subprocess


class FlowTableMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.controls = {}
        self.in_flow_table = False
        self.columns = 0

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.controls[attrs['id']] = attrs
        if tag == 'table' and attrs.get('id') == 'flowTable':
            self.in_flow_table = True
        if tag == 'th' and self.in_flow_table:
            self.columns += 1

    def handle_endtag(self, tag):
        if tag == 'table':
            self.in_flow_table = False


def test_flow_table_has_one_keyboard_reachable_native_scroll_viewport():
    root = Path(__file__).resolve().parents[1]
    page = FlowTableMarkup()
    page.feed((root / 'static/index.html').read_text())
    viewport = page.controls['flowTableScroll']
    assert {'tbl-wrap', 'flow-table-scroll'} <= set(viewport['class'].split())
    assert viewport['tabindex'] == '0'
    assert viewport['role'] == 'region'
    assert page.columns == 7
    assert not {'flowScrollLeft', 'flowHorizontalPosition', 'flowScrollRight'} & page.controls.keys()
    assert not {'btnPrev', 'btnNext', 'pageInfo', 'btnFlowRetry', 'flowTableHint', 'statLine', 'querySummary'} & page.controls.keys()


def test_table_workspace_behavior():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "--test", str(root / "tests/table_workspace_harness.mjs")],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
