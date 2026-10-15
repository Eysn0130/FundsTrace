"""Synthetic preview and browser-instrumentation contract; no business instance."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from scripts.preview_synthetic import (fixture_summary, graph_fixture_rows, parse_args,
                                      GRAPH_ASSET_FILES, register_preview_routes, source_fingerprint)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("shape", ["chain", "hub"])
def test_graph_fixture_preserves_exact_money_and_isolation(client, database, make_case, shape):
    from app import importer

    cases = [make_case("Synthetic benchmark first"), make_case("Synthetic benchmark isolated")]
    summaries = []
    graphs = []
    for index, case_id in enumerate(cases):
        rows = graph_fixture_rows(17, shape, seed=2026, isolated=bool(index))
        assert rows == graph_fixture_rows(17, shape, seed=2026, isolated=bool(index))
        assert len({row["payer_account"] for row in rows}) == (1 if shape == "hub" else 16)
        importer.commit_rows("synthetic-graph.csv", "A", rows, case_id=case_id)
        database.x("insert into tracked_flows(case_id,flow_id,canvas_id) select case_id,id,0 from flows where case_id=?", [case_id])
        graph = client.get(f"/api/graph?case_id={case_id}").json()
        assert len(graph["nodes"]) == 17
        assert len(graph["edges"]) == 16
        assert all(isinstance(part["amount"], str) for edge in graph["edges"] for part in edge["amount_totals"])
        expected = {}
        for row in rows:
            expected[row["currency"]] = expected.get(row["currency"], 0) + int(row["amount"] * 100)
        actual = {}
        for edge in graph["edges"]:
            for part in edge["amount_totals"]:
                actual[part["currency"]] = actual.get(part["currency"], 0) + int(Decimal(part["amount"]) * 100)
        assert actual == expected
        summary = fixture_summary(case_id, rows, shape, 2026)
        assert summary["nodes"] == 17 and summary["edges"] == 16
        assert {currency: int(Decimal(value) * 100) for currency, value in summary["totalsByCurrency"].items()} == expected
        summaries.append(summary)
        graphs.append(graph)
    assert {node["account"] for node in graphs[0]["nodes"]} == {node["account"] for node in graphs[1]["nodes"]}
    assert summaries[0]["totalsByCurrency"] != summaries[1]["totalsByCurrency"]
    again = client.get(f"/api/graph?case_id={cases[0]}").json()
    def canonical(value):
        # The graph API's grouped rows have no ordering contract.
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return sorted((canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
        return value
    assert canonical(again) == canonical(graphs[0])


def test_benchmark_route_only_registered_by_disposable_preview(client):
    assert client.get("/synthetic/graph-benchmark").status_code == 404
    app = FastAPI()
    fixture = {"syntheticOnly": True, "fixtureVersion": "test", "caseId": "42", "escaped": "</script>"}
    register_preview_routes(app, ROOT, fixture)
    with TestClient(app) as preview:
        response = preview.get("/synthetic/graph-benchmark?case_id=42&label_mode=all")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.text.index("graph-benchmark.js") < response.text.index('src="/static/app.js')
        assert '"escaped":"\\u003c/script>"' in response.text
        assert "/synthetic/graph-benchmark" not in preview.get("/openapi.json").json()["paths"]
    assert "graph-benchmark.js" not in (ROOT / "static/index.html").read_text()


@pytest.mark.parametrize("arguments", [
    ["--graph-nodes", "1"], ["--graph-nodes", "5001"], ["--graph-shape", "random"],
    ["--graph-seed", "-1"], ["--port", "65536"],
])
def test_graph_preview_rejects_unbounded_or_unknown_fixture_arguments(arguments):
    with pytest.raises(SystemExit) as error:
        parse_args(arguments)
    assert error.value.code == 2


def comparison_assets(tmp_path):
    directory = tmp_path / "comparison-static"
    for name in GRAPH_ASSET_FILES:
        asset = directory / name
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text("/* synthetic comparison " + name + " */", encoding="utf-8")
    (directory / "index.html").write_text(
        '<!doctype html><title>Synthetic comparison renderer</title>'
        '<script src="/static/money.js?v=old"></script>'
        '<script src="/static/app.js?v=old"></script>', encoding="utf-8")
    return directory


def test_graph_preview_asset_override_serves_one_selected_renderer_and_its_actual_fingerprint(tmp_path):
    from fastapi.staticfiles import StaticFiles

    directory = comparison_assets(tmp_path)
    args = parse_args(["--graph-nodes", "5000", "--graph-shape", "hub", "--graph-assets", str(directory)])
    assert args.graph_assets == directory.resolve()
    app = FastAPI()

    @app.get("/")
    def original_index():
        return "old preview entry"

    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    metadata = {"syntheticOnly": True, "rendererAssetsOverride": True,
                "sourceSha256": source_fingerprint(ROOT, args.graph_assets)}
    register_preview_routes(app, ROOT, metadata, graph_assets=args.graph_assets)
    with TestClient(app) as preview:
        assert "Synthetic comparison renderer" in preview.get("/").text
        benchmark = preview.get("/synthetic/graph-benchmark")
        assert benchmark.status_code == 200
        assert "Synthetic comparison renderer" in benchmark.text
        assert benchmark.text.index("graph-benchmark.js") < benchmark.text.index('src="/static/app.js')
        assert str(directory) not in benchmark.text
        encoded = benchmark.text.split('id="graphBenchmarkFixture">', 1)[1].split("</script>", 1)[0]
        fixture = json.loads(encoded)
        assert fixture["rendererAssetsOverride"] is True
        expected = (directory / "app.js").read_bytes()
        assert preview.get("/static/app.js").content == expected
        assert preview.get("/static/graph-benchmark.js").content == (directory / "graph-benchmark.js").read_bytes()
        assert fixture["sourceSha256"]["static/app.js"] == hashlib.sha256(expected).hexdigest()
        assert fixture["sourceSha256"] != source_fingerprint(ROOT)
        assert "static/money.js" in fixture["sourceSha256"]
        assert "/synthetic/graph-benchmark" not in preview.get("/openapi.json").json()["paths"]
    assert parse_args([]).graph_assets is None


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "incomplete", "missing-reference", "outside-reference"])
def test_graph_preview_rejects_incomplete_or_invalid_asset_override(tmp_path, kind, capsys):
    directory = comparison_assets(tmp_path)
    if kind == "relative":
        argument = "relative/static"
    elif kind == "missing":
        argument = str(tmp_path / "does-not-exist")
    elif kind == "file":
        argument = str(directory / "app.js")
    elif kind == "incomplete":
        (directory / "graph-layout.js").unlink()
        argument = str(directory)
    elif kind == "missing-reference":
        with (directory / "index.html").open("a") as handle:
            handle.write('<script src="/static/needed-helper.js?v=1"></script>')
        argument = str(directory)
    else:
        with (directory / "index.html").open("a") as handle:
            handle.write('<script src="/static/../outside-helper.js"></script>')
        argument = str(directory)
    with pytest.raises(SystemExit) as error:
        parse_args(["--graph-assets", argument])
    assert error.value.code == 2
    assert "graph-assets" in capsys.readouterr().err


def test_browser_recorder_observes_real_phase_order_and_rejects_stale_callbacks():
    # A deterministic RAF clock checks behavior, not browser speed or a fabricated FPS.
    source = r'''
const assert = require('node:assert/strict');
const { createRecorder, summarize, SCHEMA } = require('./static/graph-benchmark.js');
let clock = 100, serial = 0;
const queue = new Map();
const recorder = createRecorder({ now: () => clock,
  raf: callback => { queue.set(++serial, callback); return serial; }, caf: id => queue.delete(id) });
const phase = (name, extra = {}) => recorder.event({ phase: name, caseId: '1', canvasId: '0', generation: 1, canvasGeneration: 0, ...extra });
function frame(timestamp, receivedAt = timestamp) { clock = receivedAt; const batch = [...queue.values()]; queue.clear(); batch.forEach(callback => callback(timestamp)); }
phase('request', { at: 100, labelMode: 'all' });
phase('layout', { at: 110 });
assert.equal(recorder.report().runs[0].requestToLayoutSettledMs, 10);
assert.equal(recorder.report().runs[0].requestToFirstGraphPaintMs, undefined);
phase('paint', { at: 120, nodes: 5000, edges: 4999, labels: 9999 });
frame(130);
let run = recorder.report().runs[0];
assert.equal(run.requestToFirstGraphPaintMs, 30);
assert.equal(run.navigationToFirstGraphPaintMs, 130);
assert.equal(run.paintCommitToAnimationFrameMs, 10);
assert.equal(run.counts.nodes, 5000);
clock = 140;
recorder.input('zoom', 135, 140);
recorder.input('zoom', 139, 141);
phase('viewport', { at: 150 });
frame(160);
run = recorder.report().runs[0];
assert.equal(run.samples.length, 1);
assert.deepEqual(run.samples[0], { kind: 'zoom', labelMode: 'all', inputAtMs: 139, receivedAtMs: 141,
 committedAtMs: 150, renderedAtMs: 160, eventProcessingDelayMs: 2, captureToCommitMs: 9,
 commitToAnimationFrameMs: 10, eventToRenderMs: 21, coalescedInputEvents: 2, maxObservedRafGapMs: 20 });
phase('viewport', { at: 165 }); // Programmatic viewport without input is excluded.
frame(170);
assert.equal(recorder.report().runs[0].samples.length, 1);
clock = 180;
recorder.gesture(true);
recorder.input('node-drag', 178, 180);
phase('node-drag', { at: 182 });
frame(200);
recorder.input('node-drag', 201, 202);
phase('node-drag', { at: 204 });
phase('reset', { at: 205, reason: 'case-change' });
frame(220); // Stale case's pending frame is not an observation in a new context.
assert.equal(recorder.report().runs[0].samples.length, 2);
assert.equal(recorder.report().currentContext, null);
phase('request', { caseId: '2', generation: 2, at: 230 });
phase('paint', { caseId: '1', generation: 1, at: 235 });
frame(240);
assert.equal(recorder.report().runs[1].requestToFirstGraphPaintMs, undefined);
phase('paint', { caseId: '2', generation: 2, at: 245 });
frame(250);
assert.equal(recorder.report().runs[1].requestToFirstGraphPaintMs, 20);
assert.deepEqual(recorder.report().currentContext, { caseId: '2', canvasId: '0', generation: '2', canvasGeneration: '0' });
assert.equal(recorder.report().schemaVersion, SCHEMA);
clock = 251;
recorder.gesture(true);
recorder.input('drag', 250, 251);
phase('canvas-drag', { caseId: '2', generation: 2, at: 252 });
frame(255);
recorder.gesture(false);
assert.equal(recorder.report().runs[1].samples[0].kind, 'canvas-drag');
assert.equal(recorder.report().runs[1].samples[0].eventToRenderMs, 5);
// An A-B-A canvas round trip rejects old A's queued RAF and late commits.
phase('request', { caseId: '2', generation: 2, canvasGeneration: 1, at: 256, labelMode: 'focus' });
phase('paint', { caseId: '2', generation: 2, canvasGeneration: 1, at: 257 });
phase('request', { caseId: '2', generation: 2, canvasId: '8', canvasGeneration: 2, at: 258 });
phase('request', { caseId: '2', generation: 2, canvasGeneration: 3, at: 259, labelMode: 'focus' });
frame(260);
assert.equal(recorder.report().runs.at(-1).firstGraphPaintAtMs, undefined);
assert.equal(recorder.report().runs.at(-1).labelMode, 'focus');
phase('paint', { caseId: '2', generation: 2, canvasGeneration: 1, at: 261 });
frame(262);
assert.equal(recorder.report().runs.at(-1).firstGraphPaintAtMs, undefined);
clock = 263;
recorder.input('zoom', 262, 263);
phase('viewport', { caseId: '2', generation: 2, canvasGeneration: 1, at: 264 });
frame(265);
assert.equal(recorder.report().runs.at(-1).samples.length, 0);
phase('viewport', { caseId: '2', generation: 2, canvasGeneration: 3, at: 266, labelMode: 'focus' });
frame(267);
assert.equal(recorder.report().runs.at(-1).samples[0].labelMode, 'focus');
assert.equal(recorder.report().runs.at(-1).samples[0].eventToRenderMs, 5);
assert.deepEqual(summarize([9, 1, 3, 7]), { samples: 4, medianMs: 3, p95NearestRankMs: 9, minMs: 1, maxMs: 9 });
clock = 270;
recorder.input('zoom', 270);
frame(280); // Ignored zoom-at-limit never leaves an endless RAF observer running.
assert.equal(queue.size, 0);
recorder.clear();
assert.deepEqual(recorder.report().runs, []);
// A shared RAF timestamp can precede a commit in the same frame. The endpoint
// must use actual callback receipt, so decomposed durations retain clock order.
clock = 300;
phase('request', { at: 300 });
phase('paint', { at: 330 });
frame(320, 350);
run = recorder.report().runs[0];
assert.equal(run.firstGraphPaintAtMs, 350);
assert.equal(run.navigationToFirstGraphPaintMs, 350);
assert.equal(run.requestToFirstGraphPaintMs, 50);
assert.equal(run.paintCommitToAnimationFrameMs, 20);
assert.ok(run.firstGraphPaintAtMs >= run.paintCommittedAtMs);
clock = 400;
recorder.input('zoom', 390, 400);
phase('viewport', { at: 420 });
frame(410, 440);
const delayed = recorder.report().runs[0].samples[0];
assert.equal(delayed.renderedAtMs, 440);
assert.equal(delayed.eventToRenderMs, 50);
assert.equal(delayed.eventProcessingDelayMs, 10);
assert.equal(delayed.captureToCommitMs, 20);
assert.equal(delayed.commitToAnimationFrameMs, 20);
assert.equal(delayed.eventToRenderMs,
  delayed.eventProcessingDelayMs + delayed.captureToCommitMs + delayed.commitToAnimationFrameMs);
assert.ok(delayed.inputAtMs <= delayed.receivedAtMs);
assert.ok(delayed.receivedAtMs <= delayed.committedAtMs);
assert.ok(delayed.committedAtMs <= delayed.renderedAtMs);
assert.equal(delayed.maxObservedRafGapMs, 10); // Retains RAF timestamp gap semantics.
console.log('recorder phase, coalescing, stale-case, frame and reset checks passed');
'''
    result = subprocess.run(["node", "-e", source], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
