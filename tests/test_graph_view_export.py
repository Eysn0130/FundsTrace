"""Synthetic current-view exports preserve their scope and exact model values."""
import json
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
    script = "var DATA" + response.text.split("<script>\nvar DATA", 1)[1].split("</script>", 1)[0]
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
function shape(name) { return {name, visible:true, get(key){return this[key]},
  hide(){this.visible=false}, attr(){return 0}}; }
function item(model) { const children=[shape('key-shape'),shape('text-shape'),shape('text-bg-shape')];
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
assert.equal(hidden.children[2].visible,false);
assert.equal(edge.children[0].visible,true);
assert.equal(edge.children[1].visible,false);
assert.equal(edge.children[2].visible,false);
assert.equal(edge.getModel().label,'2笔·CNY 10,000,000,000,000,000.01');
assert.equal(edge.getModel().labelCfg.style.background.fill,'transparent');
assert.equal(edge.getModel().data.total,'10000000000000000.01');
assert.equal(hidden.getModel().data.in_amt,'10000000000000000.01');
"""
    completed = subprocess.run(["node", "-e", harness], input=script, text=True,
                               capture_output=True, check=False)
    assert completed.returncode == 0, completed.stderr
