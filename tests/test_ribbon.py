"""Ribbon contracts use synthetic markup and DOM, never a running business instance."""
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


class RibbonMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.tags = {}
        self.tabs = []
        self.panels = []
        self.icons = []
        self.scripts = []
        self.analysis_algorithms = []
        self.planned_features = {}

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
            self.tags[attrs['id']] = (tag, attrs)
        if attrs.get('role') == 'tab' and 'data-ribbon-tab' in attrs:
            self.tabs.append(attrs)
        if attrs.get('role') == 'tabpanel' and 'ribbon-panel' in attrs.get('class', '').split():
            self.panels.append(attrs)
        if tag == 'img' and attrs.get('src', '').startswith('/static/ui-icons/'):
            self.icons.append(attrs['src'])
        if 'data-analysis-algorithm' in attrs:
            self.analysis_algorithms.append((tag, attrs))
        if 'data-planned-feature' in attrs:
            self.planned_features[attrs['data-planned-feature']] = (tag, attrs)
        if tag == 'script':
            self.scripts.append(attrs.get('src', '').split('?')[0])


def test_ribbon_has_four_tabs_and_preserves_single_business_controls():
    page = RibbonMarkup()
    page.feed((ROOT / 'static/index.html').read_text())
    assert not [key for key, count in Counter(page.ids).items() if count > 1]
    assert [tab['data-ribbon-tab'] for tab in page.tabs] == ['common', 'trace', 'style', 'view']
    assert len(page.panels) == 4
    assert sum('hidden' not in panel for panel in page.panels) == 1
    for key in ('btnCanvasSave', 'btnUndo', 'btnRedo', 'btnRelayout', 'btnFit', 'neighborAccount',
                'neighborDirection', 'btnNeighborhood', 'btnExpandNeighbors', 'btnGraphOverview',
                'btnMinimap', 'graphLabelMode', 'relationCurrency', 'relationMin', 'relationMax',
                'btnRelationApply', 'btnRelationClear', 'amountWidthCk', 'widthCurrency'):
        assert page.ids.count(key) == 1
    for key in ('relationMin', 'relationMax'):
        assert page.tags[key][1]['inputmode'] == 'decimal'
        assert page.tags[key][1].get('type') != 'number'
    assert page.tags['labelUnderline'][1]['data-planned'] == 'true'
    assert 'disabled' in page.tags['labelUnderline'][1]
    scripts = page.scripts
    assert scripts.index('/static/money.js') < scripts.index('/static/graph-relations.js') < scripts.index('/static/app.js')
    assert scripts.index('/static/workflow-ui.js') < scripts.index('/static/ribbon-commands.js') < scripts.index('/static/ribbon.js')



def test_analysis_shortcuts_are_case_bound_algorithm_links_without_running_analysis():
    page = RibbonMarkup()
    page.feed((ROOT / 'static/index.html').read_text())
    assert [attrs['data-analysis-algorithm'] for _, attrs in page.analysis_algorithms] == [
        'paths', 'cycles', 'fast_flows', 'outliers']
    for tag, attrs in page.analysis_algorithms:
        assert tag == 'a'
        assert attrs['href'] == '/analysis'
        assert attrs['target'] == '_blank'
        assert attrs['rel'] == 'noopener'
        assert 'data-ribbon-proxy' not in attrs



def test_confirmed_style_placeholders_are_explicitly_planned_and_disabled():
    page = RibbonMarkup()
    page.feed((ROOT / 'static/index.html').read_text())
    for feature in ('fixLayout', 'unfixLayout', 'presetDefault', 'presetReport'):
        tag, attrs = page.planned_features[feature]
        assert tag == 'button'
        assert 'disabled' in attrs
        assert attrs['data-planned'] == 'true'
        assert 'data-ribbon-command' not in attrs
        assert 'data-ribbon-proxy' not in attrs
        assert 'planned' in attrs['class'].split()


def test_every_ribbon_icon_is_local_well_formed_svg():
    page = RibbonMarkup()
    page.feed((ROOT / 'static/index.html').read_text())
    for source in set(page.icons):
        path = ROOT / source.lstrip('/')
        assert path.is_file(), source
        svg = ET.parse(path).getroot()
        assert svg.tag == '{http://www.w3.org/2000/svg}svg'
        assert svg.get('viewBox') == '0 0 24 24'
        assert not list(svg.iter('{http://www.w3.org/2000/svg}script'))


def test_ribbon_interactions_preserve_controls_and_do_not_invoke_graph_on_chrome_changes():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/ribbon_harness.mjs')],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_node_appearance_controls_use_existing_popovers_and_scoped_commands():
    page = RibbonMarkup()
    page.feed((ROOT / "static/index.html").read_text(encoding="utf-8"))
    for identifier, command in (("nodeBorderColor", "nodeBorderColor"), ("nodeBorderWidth", "nodeBorderWidth"), ("nodeSize", "nodeSize")):
        assert page.tags[identifier][1]["data-ribbon-command"] == command
    assert page.tags["btnNodeBorder"][1]["data-ribbon-popover"] == "nodeBorderPopover"
    assert page.tags["btnNodeSize"][1]["data-ribbon-popover"] == "nodeSizePopover"
