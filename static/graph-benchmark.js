/* Synthetic-only instrumentation. Production index.html never loads this file. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && root.document) api.mount(root);
})(typeof window === 'object' ? window : null, function () {
  'use strict';
  const SCHEMA = 'funds-graph-interaction.v1';
  const MAX_RUNS = 20;
  const MAX_SAMPLES = 500;
  const round = value => Math.round(value * 1000) / 1000;
  const delay = (end, start) => round(Math.max(0, end - start));
  const contextKey = detail => [detail.caseId, detail.canvasId, detail.generation, detail.canvasGeneration].map(String).join(':');
  const labelMode = value => ['auto', 'focus', 'all'].includes(value) ? value : 'auto';
  function summarize(values) {
    const ordered = values.filter(Number.isFinite).slice().sort((a, b) => a - b);
    if (!ordered.length) return { samples: 0 };
    return { samples: ordered.length, medianMs: ordered[Math.ceil(ordered.length * 0.5) - 1],
      p95NearestRankMs: ordered[Math.ceil(ordered.length * 0.95) - 1], minMs: ordered[0], maxMs: ordered.at(-1) };
  }
  function createRecorder({ now, raf, caf, onChange = () => {} }) {
    let current = null, token = 0, frame = null, previousFrame = null, dragging = false;
    let pending = { zoom: [], drag: [], 'node-drag': [], 'canvas-drag': [] };
    const runs = [];
    let discardedRuns = 0;
    function publish() { onChange(); }
    function cancelPending() {
      token += 1;
      if (frame !== null) caf(frame);
      frame = null; previousFrame = null; dragging = false;
      pending = { zoom: [], drag: [], 'node-drag': [], 'canvas-drag': [] };
    }
    function keepSample(run, sample) {
      if (run.samples.length >= MAX_SAMPLES) run.droppedSamples += 1;
      else run.samples.push(sample);
      publish();
    }
    function observeFrame(at) {
      frame = null;
      if (!current) return;
      if (previousFrame !== null) current.maxObservedRafGapMs = Math.max(current.maxObservedRafGapMs, delay(at, previousFrame));
      previousFrame = at;
      // An ignored wheel at a zoom boundary must not start a permanent RAF loop.
      // A later commit still records its real input-to-frame delay independently.
      if (dragging) frame = raf(observeFrame);
      else previousFrame = null;
    }
    function observeGesture() {
      if (frame === null) { previousFrame = now(); frame = raf(observeFrame); }
    }
    function event(detail) {
      if (!detail || !['request', 'paint', 'layout', 'reset', 'viewport', 'node-drag', 'canvas-drag'].includes(detail.phase)) return;
      if (detail.caseId === undefined || detail.canvasId === undefined || detail.generation === undefined || detail.canvasGeneration === undefined) return;
      const at = Number.isFinite(detail.at) ? detail.at : now();
      if (detail.phase === 'reset') {
        if (current) { current.resetAtMs = round(at); current.resetReason = String(detail.reason || '').slice(0, 100); }
        cancelPending(); current = null; publish(); return;
      }
      if (detail.phase === 'request') {
        cancelPending();
        current = { context: { caseId: String(detail.caseId), canvasId: String(detail.canvasId), generation: String(detail.generation),
            canvasGeneration: String(detail.canvasGeneration) },
          key: contextKey(detail), requestedAtMs: round(at), labelMode: labelMode(detail.labelMode),
          maxObservedRafGapMs: 0, samples: [], droppedSamples: 0, counts: {} };
        runs.push(current);
        if (runs.length > MAX_RUNS) { runs.shift(); discardedRuns += 1; }
        publish(); return;
      }
      if (!current || current.key !== contextKey(detail)) return;
      for (const key of ['nodes', 'edges', 'renderedNodes', 'renderedEdges', 'labels', 'drawnNodes', 'drawnEdges', 'createdLabels']) {
        if (Number.isSafeInteger(detail[key]) && detail[key] >= 0) current.counts[key] = detail[key];
      }
      if (detail.phase === 'layout') {
        current.layoutSettledAtMs = round(at);
        current.requestToLayoutSettledMs = delay(at, current.requestedAtMs);
        publish(); return;
      }
      const run = current, capturedToken = token;
      if (detail.phase === 'paint') {
        if (run.paintCommittedAtMs !== undefined) return;
        run.paintCommittedAtMs = round(at);
        raf(() => {
          // The RAF frame timestamp is shared by callbacks in a frame and may
          // precede a G6 commit made earlier in that same callback batch.
          // Receipt time preserves the event -> commit -> callback ordering.
          const receivedAt = now();
          if (capturedToken !== token || current !== run) return;
          run.firstGraphPaintAtMs = round(receivedAt);
          run.navigationToFirstGraphPaintMs = round(receivedAt);
          run.requestToFirstGraphPaintMs = delay(receivedAt, run.requestedAtMs);
          run.paintCommitToAnimationFrameMs = delay(receivedAt, at);
          publish();
        });
        return;
      }
      const kind = detail.phase === 'viewport' ? 'zoom' : detail.phase;
      const candidateInputs = kind === 'zoom' ? pending[kind] : pending[kind].concat(pending.drag);
      const inputs = candidateInputs.filter(input => input.receivedAt <= at).sort((a, b) => a.receivedAt - b.receivedAt || a.at - b.at);
      pending[kind] = pending[kind].filter(input => input.receivedAt > at);
      if (kind !== 'zoom') pending.drag = pending.drag.filter(input => input.receivedAt > at);
      if (!inputs.length) return; // Fit, programmatic viewport and stale gestures are not input samples.
      const input = inputs.at(-1);
      raf(() => {
        const receivedAt = now();
        if (capturedToken !== token || current !== run) return;
        keepSample(run, { kind, labelMode: labelMode(detail.labelMode === undefined ? run.labelMode : detail.labelMode),
          inputAtMs: round(input.at), receivedAtMs: round(input.receivedAt), committedAtMs: round(at), renderedAtMs: round(receivedAt),
          eventProcessingDelayMs: delay(input.receivedAt, input.at), captureToCommitMs: delay(at, input.receivedAt),
          commitToAnimationFrameMs: delay(receivedAt, at), eventToRenderMs: delay(receivedAt, input.at),
          coalescedInputEvents: inputs.length, maxObservedRafGapMs: round(run.maxObservedRafGapMs) });
      });
    }
    function input(kind, at, receivedAt = now()) {
      if (!current || !pending[kind] || !Number.isFinite(at) || !Number.isFinite(receivedAt) || at < 0 || receivedAt < at) return;
      // Duplicate pointer/mouse streams are prevented by the DOM adapter below.
      if (pending[kind].length >= 128) { pending[kind].shift(); current.droppedSamples += 1; }
      pending[kind].push({ at, receivedAt });
      observeGesture();
    }
    function gesture(active) {
      if (!current) return;
      dragging = Boolean(active);
      if (dragging) observeGesture();
      else {
        // A pointer ending without G6 commit must not leak into the next gesture.
        pending.drag = []; pending['node-drag'] = []; pending['canvas-drag'] = [];
        publish();
      }
    }
    function report() {
      return { schemaVersion: SCHEMA, discardedRuns, currentContext: current ? { ...current.context } : null,
        definitions: {
          renderProxy: 'The endpoint is performance.now() at receipt of the next requestAnimationFrame callback after a G6 commit, not the RAF frame timestamp or physical screen presentation.',
          firstGraphPaint: 'requestToFirstGraphPaintMs measures graph request to next RAF callback receipt after visible G6 data commit; layout settling is separate.',
          navigationTiming: 'firstGraphPaintAtMs and navigationToFirstGraphPaintMs are navigation-relative performance.now timestamps, not graph-request durations. All *AtMs fields share the performance.timeOrigin clock.',
          interaction: 'Latest matching DOM input timestamp to next RAF callback receipt after G6 render commit; coalescedInputEvents counts pending DOM inputs matched to this commit, not browser hardware samples.',
          eventProcessingDelay: 'DOM event timestamp to capture-listener receipt, not exclusive G6 handler CPU time.',
          rafGap: 'Largest observed gap between RAF frame timestamps while input is pending or a drag gesture is active (first interval starts at observer registration); separate from callback receipt latency, not FPS or GPU presentation.',
          limits: `${MAX_RUNS} runs, ${MAX_SAMPLES} samples per run; discarded/dropped counts are explicit. No timers, network upload or financial payload capture.` },
        runs: runs.map(run => ({ ...run, counts: { ...run.counts }, samples: run.samples.map(sample => ({ ...sample })),
          summary: Object.fromEntries(['zoom', 'node-drag', 'canvas-drag'].map(kind => {
            const samples = run.samples.filter(sample => sample.kind === kind);
            return [kind, { eventToRender: summarize(samples.map(sample => sample.eventToRenderMs)),
              eventProcessingDelay: summarize(samples.map(sample => sample.eventProcessingDelayMs)),
              captureToCommit: summarize(samples.map(sample => sample.captureToCommitMs)) }];
          })) })) };
    }
    function clear() { cancelPending(); current = null; runs.length = 0; discardedRuns = 0; publish(); }
    return { event, input, gesture, report, clear };
  }
  function mount(window) {
    const document = window.document;
    const fixtureNode = document.getElementById('graphBenchmarkFixture');
    if (!fixtureNode || window.location.pathname !== '/synthetic/graph-benchmark') return;
    let fixture;
    try { fixture = JSON.parse(fixtureNode.textContent); } catch (_) { return; }
    if (fixture.syntheticOnly !== true) return;
    const panel = document.createElement('details');
    panel.id = 'graphBenchmarkPanel';
    panel.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:1200;width:min(370px,calc(100vw - 24px));max-height:38vh;overflow:auto;padding:10px;background:#fff;color:#162b42;border:1px solid #8094ac;border-radius:8px;box-shadow:0 3px 14px #14283f30;font:12px/1.5 system-ui';
    const heading = document.createElement('summary');
    heading.textContent = '合成图谱交互基准（展开查看 / 导出）';
    heading.style.cursor = 'pointer';
    panel.append(heading);
    const note = document.createElement('p');
    note.textContent = '仅在此合成预览路由记录。首绘与布局分别计时；缩放、节点拖拽、画布拖拽使用真实输入。';
    panel.append(note);
    const commands = document.createElement('div');
    commands.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap';
    const clear = document.createElement('button'); clear.type = 'button'; clear.textContent = '清除观测'; clear.id = 'graphBenchmarkClear';
    const download = document.createElement('button'); download.type = 'button'; download.textContent = '下载基准 JSON'; download.id = 'graphBenchmarkDownload';
    commands.append(clear, download);
    for (const [mode, label] of [['auto', '自动分级'], ['all', '全标签对照']]) {
      const link = document.createElement('a');
      const url = new URL(window.location.href); url.searchParams.set('label_mode', mode);
      link.href = url.pathname + url.search; link.textContent = label; commands.append(link);
    }
    panel.append(commands);
    const output = document.createElement('pre'); output.id = 'graphBenchmarkResults';
    output.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;font:10px/1.4 ui-monospace,monospace';
    panel.append(output); document.body.append(panel);
    function snapshot() {
      return { ...recorder.report(), fixture, environment: { userAgent: window.navigator.userAgent,
        viewport: { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio },
        hardwareConcurrency: window.navigator.hardwareConcurrency || null, timeOrigin: window.performance.timeOrigin },
        observedAtUtc: new Date().toISOString() };
    }
    const recorder = createRecorder({ now: () => window.performance.now(), raf: callback => window.requestAnimationFrame(callback),
      caf: id => window.cancelAnimationFrame(id), onChange: () => { output.textContent = JSON.stringify(snapshot(), null, 2); } });
    output.textContent = JSON.stringify(snapshot(), null, 2);
    document.addEventListener('funds:graph-metric', event => recorder.event(event.detail));
    // The app dispatches on window; tests/adapters may dispatch on document. Bubbling events are deduplicated.
    window.addEventListener('funds:graph-metric', event => { if (event.target !== document) recorder.event(event.detail); });
    const graph = document.getElementById('graphContainer');
    let activePointer = null, pointerStream = false, mouseDrag = false;
    const eventAt = event => {
      const timestamp = Number(event.timeStamp);
      return timestamp > window.performance.timeOrigin ? timestamp - window.performance.timeOrigin : timestamp;
    };
    const insideGraph = event => graph && (event.target === graph || graph.contains(event.target));
    function move(event) {
      const at = eventAt(event), received = window.performance.now();
      // Which G6 handler commits determines the gesture type without double-counting input.
      recorder.input('drag', at, received);
    }
    document.addEventListener('wheel', event => { if (insideGraph(event)) recorder.input('zoom', eventAt(event)); }, { capture: true, passive: true });
    document.addEventListener('pointerdown', event => {
      if (!insideGraph(event) || event.button !== 0) return;
      pointerStream = true; activePointer = event.pointerId; recorder.gesture(true);
    }, true);
    document.addEventListener('pointermove', event => { if (event.pointerId === activePointer) move(event); }, true);
    const endPointer = event => { if (event.pointerId === activePointer) { activePointer = null; recorder.gesture(false); } };
    document.addEventListener('pointerup', endPointer, true); document.addEventListener('pointercancel', endPointer, true);
    document.addEventListener('mousedown', event => {
      if (pointerStream || !insideGraph(event) || event.button !== 0) return;
      mouseDrag = true; recorder.gesture(true);
    }, true);
    document.addEventListener('mousemove', event => { if (mouseDrag) move(event); }, true);
    document.addEventListener('mouseup', () => { if (mouseDrag) { mouseDrag = false; recorder.gesture(false); } }, true);
    window.addEventListener('blur', () => { activePointer = null; mouseDrag = false; recorder.gesture(false); });
    clear.addEventListener('click', () => recorder.clear());
    download.addEventListener('click', () => {
      const blob = new window.Blob([JSON.stringify(snapshot(), null, 2)], { type: 'application/json' });
      const url = window.URL.createObjectURL(blob), anchor = document.createElement('a');
      anchor.href = url; anchor.download = 'funds-trace-graph-interaction.json'; anchor.click(); window.URL.revokeObjectURL(url);
    });
  }
  return { SCHEMA, summarize, createRecorder, mount };
});
