"""Synthetic current-view exports preserve their scope and exact model values."""
import json
from pathlib import Path
import re
import subprocess

import pytest


def graph_snapshot():
    return {
        "case_name": "Synthetic </script><script>case</script> __VIEW__",
        "nodes": [
            {"id": "SYN-A", "x": 100, "y": 200, "label": "Synthetic A", "labelVisible": True},
            {"id": "SYN-B", "x": 400, "y": 200, "label": "Synthetic B", "labelVisible": False,
             "data": {"in_amt": "10000000000000000.01"}},
        ],
        "edges": [{"source": "SYN-A", "target": "SYN-B", "type": "line",
                   "label": "2笔·CNY 10,000,000,000,000,000.01", "labelVisible": False,
                   "data": {"cnt": 2, "total": "10000000000000000.01",
                            "amount_totals": [{"currency": "CNY", "amount": "10000000000000000.01"}]}}],
        "view": {"scope": "visible", "mode": "neighborhood", "total_nodes": 1000,
                 "total_edges": 2000, "visible_nodes": 2, "visible_edges": 1, "label_mode": "auto"},
    }


def export_data(html):
    encoded = re.search(r"var DATA = (.*?);\nfunction buildWM", html, re.S)
    assert encoded is not None
    return json.loads(encoded.group(1).replace("<\\/", "</"))


def export_script(html):
    embedded = [re.search(rf'<script id="{name}">(.*?)</script>', html, re.S)
                for name in ("graph-presentation", "graph-label-geometry", "graph-routing", "graph-route-quality")]
    runtime = "var DATA" + html.split("<script>\nvar DATA", 1)[1].split("</script>", 1)[0]
    return "\n".join(match.group(1) for match in embedded if match) + "\n" + runtime


def test_visible_neighborhood_export_marks_partial_scope_without_losing_money(client):
    snapshot = graph_snapshot()
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert "当前邻域视图 2 / 1000 个账户 · 1 / 2000 条关系" in response.text
    assert "图谱仅显示当前视图，追踪数据未删除" in response.text
    assert "标签：自动分级" in response.text
    assert "Synthetic &lt;/script&gt;" in response.text
    assert "</script><script>case</script>" not in response.text
    decoded = export_data(response.text)
    assert decoded["view"] == snapshot["view"]
    assert decoded["nodes"] == snapshot["nodes"]
    assert decoded["edges"] == snapshot["edges"]


def test_legacy_export_without_view_remains_compatible(client):
    snapshot = graph_snapshot()
    del snapshot["view"]
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert "导出图谱 2 个账户 · 1 条关系" in response.text
    assert "当前邻域视图" not in response.text
    assert export_data(response.text)["nodes"] == snapshot["nodes"]


def test_all_scope_can_preserve_neighborhood_view_counts(client):
    snapshot = graph_snapshot()
    snapshot["view"].update(scope="all", total_nodes=2, total_edges=1,
                            visible_nodes=1, visible_edges=0, label_mode="all")
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert "全部图谱 2 个账户 · 1 条关系" in response.text
    assert "标签：全部" in response.text


@pytest.mark.parametrize("update", [
    {"scope": "<script>invalid</script>"}, {"mode": "invalid"}, {"label_mode": "invalid"},
    {"visible_nodes": -1}, {"total_nodes": 2**31}, {"visible_edges": 0.5},
    {"visible_nodes": 1001}, {"visible_nodes": 1}, {"visible_edges": 0},
    {"scope": "all"},
])
def test_export_rejects_invalid_or_inconsistent_view_metadata(client, update):
    snapshot = graph_snapshot()
    snapshot["view"].update(update)
    assert client.post("/api/export/graph.html", json=snapshot).status_code == 422


def test_export_hides_only_label_shapes_and_keeps_authoritative_models(client):
    response = client.post("/api/export/graph.html", json=graph_snapshot())
    assert response.status_code == 200
    script = export_script(response.text)
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
function shape(name, children) { return {name, visible:true, get(key){return this[key]},
  hide(){this.visible=false}, attr(){return 0},remove(){children.splice(children.indexOf(this),1)}}; }
function item(model) { const children=[];children.push(shape('key-shape',children),shape('text-shape',children),shape('text-bg-shape',children));
  return {getModel(){return model}, getContainer(){return {get(){return children}}}, children}; }
let graph;
class Graph { constructor(){graph=this} data(data){this.nodes=data.nodes.map(item);this.edges=data.edges.map(item)}
  render(){} getNodes(){return this.nodes} getEdges(){return this.edges} fitView(){}
  setAutoPaint(){} paint(){} on(){} }
const context={G6:{Graph},window:{innerWidth:1000,innerHeight:700,addEventListener(){}},
  document:{body:{appendChild(){}},getElementById(){return {style:{},getBoundingClientRect(){return {height:90}}}},
    createElement(){return {style:{},toDataURL(){return 'data:synthetic'},
      getContext(){return {translate(){},rotate(){},fillText(){}}}}}},setTimeout(){}};
vm.runInNewContext(source, context);
const first=graph.nodes[0],hidden=graph.nodes[1],edge=graph.edges[0];
assert.equal(first.children[1].visible,true);
assert.equal(hidden.children[0].visible,true);
assert.equal(hidden.children[1].visible,false);
assert.equal(hidden.children.some(shape=>shape.name==='text-bg-shape'),false);
assert.equal(edge.children[0].visible,true);
assert.equal(edge.children[1].visible,false);
assert.equal(edge.children.some(shape=>shape.name==='text-bg-shape'),false);
assert.equal(edge.getModel().label,'2笔·CNY 10,000,000,000,000,000.01');
assert.equal(edge.getModel().labelCfg.style.background,undefined);
assert.equal(edge.getModel().data.total,'10000000000000000.01');
assert.equal(hidden.getModel().data.in_amt,'10000000000000000.01');
"""
    completed = subprocess.run(["node", "-e", harness], input=script, text=True,
                               capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_filtered_relation_export_declares_currency_bounds_without_rounding(client):
    snapshot = graph_snapshot()
    snapshot["view"].update(mode="filtered", relation_filter={
        "currency": "CNY", "min": "10000000000000000.01", "max": "10000000000000000.02"})
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert "当前筛选视图 2 / 1000 个账户 · 1 / 2000 条关系" in response.text
    assert "按 CNY 聚合关系金额，不少于 10000000000000000.01、不高于 10000000000000000.02" in response.text
    assert "不同币种不相加" in response.text
    assert export_data(response.text)["view"] == snapshot["view"]
    assert export_data(response.text)["edges"][0]["data"]["total"] == "10000000000000000.01"


def test_filtered_selection_view_can_omit_relation_filter(client):
    snapshot = graph_snapshot()
    snapshot["view"]["mode"] = "filtered"
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert export_data(response.text)["view"] == snapshot["view"]
    assert "聚合关系金额" not in response.text


def test_filtered_export_still_requires_snapshot_count_integrity(client):
    snapshot = graph_snapshot()
    snapshot["view"].update(mode="filtered", visible_edges=0)
    assert client.post("/api/export/graph.html", json=snapshot).status_code == 422


def test_export_filter_unlabelled_currency_and_empty_bounds_are_explicit(client):
    snapshot = graph_snapshot()
    snapshot["view"].update(mode="filtered", relation_filter={"currency": "", "min": "", "max": ""})
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert "按 未标注币种 聚合关系金额，不限金额" in response.text
    assert export_data(response.text)["view"]["relation_filter"] == {"currency": "", "min": None, "max": None}


def test_all_graph_export_labels_filter_as_settings_only(client):
    snapshot = graph_snapshot()
    snapshot["view"].update(scope="all", mode="filtered", total_nodes=2, total_edges=1,
                            visible_nodes=1, visible_edges=0,
                            relation_filter={"currency": "USD", "min": "1.00", "max": None})
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert "全部图谱 2 个账户 · 1 条关系" in response.text
    assert "当前视图筛选设置：按 USD 聚合关系金额" in response.text
    assert "当前筛选视图" not in response.text


@pytest.mark.parametrize("rule", [
    {"currency": None, "min": "1.00"}, {"min": "1.00"}, {"currency": 123},
    {"currency": "CNY", "min": 1}, {"currency": "CNY", "min": 0.01},
    {"currency": "CNY", "min": "-1.00"}, {"currency": "CNY", "min": "0.001"},
    {"currency": "CNY", "min": "NaN"}, {"currency": "CNY", "min": "1e10"},
    {"currency": "CNY", "min": "2.00", "max": "1.99"},
    {"currency": "CNY", "min": "1" * 40},
])
def test_export_rejects_ambiguous_or_inexact_relation_filter(client, rule):
    snapshot = graph_snapshot()
    snapshot["view"].update(mode="filtered", relation_filter=rule)
    assert client.post("/api/export/graph.html", json=snapshot).status_code == 422


def test_export_runtime_consumes_only_controlled_label_and_edge_styles(client):
    snapshot = graph_snapshot()
    snapshot["nodes"][0]["labelStyle"] = {
        "fontFamily": "SimSun", "fontSize": 18, "fontWeight": "bold", "fontStyle": "italic",
        "textDecoration": "underline", "fill": "#abc", "lineHeight": 24,
        "background": {"fill": "red"}, "opacity": 0, "x": 99999}
    snapshot["nodes"][1]["labelStyle"] = {
        "fontFamily": "url(https://invalid.example)", "fontSize": 1000, "fontWeight": "900",
        "fontStyle": "oblique", "textDecoration": "line-through", "fill": "red", "lineHeight": -1}
    snapshot["edges"][0].update(style={
        "stroke": "#12AbEF", "lineWidth": 4.5, "lineDash": [6, 4], "opacity": 0,
        "startArrow": {"path": "unexpected"}, "endArrow": {"path": "reverse", "fill": "red"}},
        labelStyle={"fontFamily": "Arial", "fontSize": 16, "fill": "#fedcba", "rotate": 99})
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    script = export_script(response.text)
    # Run the actual exported application script. Capture the models handed to
    # the G6 interface; vendor drawing itself is covered by browser verification.
    harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const source=require('node:fs').readFileSync(0,'utf8');
let graph;
class Graph { constructor(){graph=this} data(data){this.models=data}
  render(){} getNodes(){return []} getEdges(){return []} fitView(){}
  setAutoPaint(){} paint(){} on(){} }
const context={G6:{Graph},window:{innerWidth:1000,innerHeight:700,addEventListener(){}},
  document:{body:{appendChild(){}},getElementById(){return {style:{},getBoundingClientRect(){return {height:90}}}},
    createElement(){return {style:{},toDataURL(){return 'data:synthetic'},
      getContext(){return {translate(){},rotate(){},fillText(){}}}}}},setTimeout(){}};
vm.runInNewContext(source,context);
const plain=value=>JSON.parse(JSON.stringify(value));
const first=graph.models.nodes[0].labelCfg.style,second=graph.models.nodes[1].labelCfg.style;
assert.equal(first.fontFamily,'SimSun');assert.equal(first.fontSize,18);assert.equal(first.fontWeight,'bold');
assert.equal(first.fontStyle,'italic');assert.equal(first.textDecoration,undefined);
assert.equal(first.fill,'#abc');assert.equal(first.lineHeight,24);
assert.equal(first.background,undefined);
assert.equal(first.opacity,undefined);assert.equal(first.x,undefined);
assert.equal(second.fontFamily,'Arial');assert.equal(second.fontSize,13);assert.equal(second.fontWeight,'normal');
assert.equal(second.fontStyle,'normal');assert.equal(second.textDecoration,undefined);
assert.equal(second.fill,'#dfe3ea');assert.equal(second.lineHeight,18);
const edge=graph.models.edges[0];
assert.deepEqual(plain(edge.style),{stroke:'#12AbEF',lineWidth:4.5,lineDash:[6,4],
  lineCap:'round',lineJoin:'round',lineAppendWidth:8,
  endArrow:{path:'M 0,0 L 10,5 L 10,-5 Z',fill:'#12AbEF'}});
assert.equal(edge.labelCfg.style.fontFamily,'Arial');assert.equal(edge.labelCfg.style.fontSize,16);
assert.equal(edge.labelCfg.style.fill,'#fedcba');assert.equal(edge.labelCfg.style.rotate,undefined);
assert.equal(edge.data.total,'10000000000000000.01');
// An invalid second input must fall back without retaining arbitrary style keys.
const dirty=source.replace('"lineWidth": 4.5','"lineWidth": -100')
  .replace('"lineDash": [6, 4]','"lineDash": [9, 9]').replace('"stroke": "#12AbEF"','"stroke": "url(bad)"');
vm.runInNewContext(dirty,context);
assert.deepEqual(plain(graph.models.edges[0].style),{stroke:'#7f8b99',lineWidth:1.4,
  lineCap:'round',lineJoin:'round',lineAppendWidth:8,lineDash:[],
  endArrow:{path:'M 0,0 L 10,5 L 10,-5 Z',fill:'#7f8b99'}});
"""
    completed = subprocess.run(["node", "-e", harness], input=script, text=True,
                               capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr


def amount_width(**overrides):
    return {"currency": "CNY", "min_amount": "0.01", "max_amount": "9999999999999999.99",
            "missing_edges": 2, "invalid_edges": 1, "scale": "full-graph", "transform": "sqrt",
            "min_width": 1.5, "max_width": 7, **overrides}


def test_amount_width_legend_is_exact_explicit_and_escaped(client):
    snapshot = graph_snapshot()
    snapshot["view"].update(mode="filtered", relation_filter={"currency": "USD", "min": "1.00"},
                            amount_width=amount_width(currency='CNY <script>"&'))
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert export_data(response.text)["view"]["amount_width"] == snapshot["view"]["amount_width"]
    assert "9999999999999999.99" in response.text
    assert "全图统一比例" in response.text and "平方根" in response.text
    assert "2 条无该币种" in response.text and "1 条金额无效" in response.text
    assert "CNY &lt;script&gt;&quot;&amp;" in response.text
    assert "按 USD 聚合关系金额，不少于 1.00" in response.text


@pytest.mark.parametrize("override", [
    {"min_amount": 0.01}, {"min_amount": "-1"}, {"min_amount": "0.001"},
    {"min_amount": "NaN"}, {"max_amount": "1e20"}, {"currency": None},
    {"min_amount": "2.00", "max_amount": "1.00"},
    {"min_amount": None}, {"max_amount": None}, {"missing_edges": -1},
    {"invalid_edges": 0.5}, {"scale": "visible"}, {"transform": "linear"},
    {"min_width": 1}, {"max_width": 8},
])
def test_export_rejects_invalid_amount_width_metadata(client, override):
    snapshot = graph_snapshot()
    snapshot["view"]["amount_width"] = amount_width(**override)
    assert client.post("/api/export/graph.html", json=snapshot).status_code == 422


def test_amount_width_without_encodable_relations_declares_missing_range(client):
    snapshot = graph_snapshot()
    snapshot["view"]["amount_width"] = amount_width(currency="", min_amount=None, max_amount=None)
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    assert "未标注币种" in response.text and "无可编码金额" in response.text


def test_export_runtime_plain_labels_midpoint_gaps_identity_and_manual_ratios(client):
    snapshot = graph_snapshot()
    snapshot["nodes"][0].update(annotation={"id": "OTHER-ID", "text": "实际控制人（合成）\n</script><script>window.injected=true</script>", "anchor": "above", "dx": 23, "dy": -14, "size": 20, "fontFamily": "KaiTi", "color": "#C084FC", "html": "<img onerror=evil>"},
                                labelStyle={"fontSize": 24, "lineHeight": 8},
                                labelOffset={"x": 35, "y": -12},
                                labelLeader={"x1": 100, "y1": 200, "x2": 135, "y2": 225},
                                data={"is_main": 1, "out_cnt": 1, "in_cnt": 1})
    snapshot["nodes"][1].update(img="data:image/png;base64,c3ludGhldGlj", data={"is_same_name": 1},
                                photoBorder={"stroke": "#123456", "lineWidth": 4},
                                labelLeader={"x1": 400, "y1": 200, "x2": 440, "y2": 220})
    snapshot["edges"][0].update(labelVisible=True, labelManual=True, labelRatio=0.75,
                                lx=325, ly=200, bw=99999, bh=99999,
                                style={"stroke": "#123456", "lineWidth": 7, "lineDash": [2, 3]})
    snapshot["edges"].append({"source": "SYN-B", "target": "SYN-A", "type": "line", "label": "auto",
                              "lx": 260, "ly": 220, "labelManual": False,
                              "labelLeader": {"x1": 250, "y1": 200, "x2": 260, "y2": 214}})
    snapshot["view"]["total_edges"] = 2001
    snapshot["view"]["visible_edges"] = 2
    response = client.post("/api/export/graph.html", json=snapshot)
    assert response.status_code == 200
    completed = subprocess.run(["node", str(Path(__file__).with_name("graph_html_style_harness.mjs"))],
                               input=response.text, text=True, capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
