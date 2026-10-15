/* Synthetic layout-kernel benchmark; does not measure G6 paint or browser FPS. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { Worker, isMainThread, parentPort, workerData } from 'node:worker_threads';
import vm from 'node:vm';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';
const source = readFileSync(new URL('../static/graph-layout.js', import.meta.url), 'utf8');
function candidate(input) {
  // Use this worker's native V8 realm, like the frozen baseline. A sandboxed
  // context adds global/Math lookup overhead that is absent in a browser worker.
  vm.runInThisContext(source, { filename: 'graph-layout.js' });
  const model = globalThis.FundsGraphLayout.simulation(input);
  const start = performance.now(); while (model.step()) { /* worker computation */ }
  return { ...model.result(), kernel_ms: performance.now() - start };
}
if (!isMainThread) {
  parentPort.postMessage(candidate(workerData));
} else {
  const count = Number(process.argv[2] || '1000');
  if (!Number.isSafeInteger(count) || count < 10 || count > 5000) throw new Error('nodes must be 10..5000');
  const width = 1000, height = 700;
  const nodes = Array.from({ length: count }, (_, i) => ({ id: `SYN-${i}`, x: 70 + ((i * 3571) % 860), y: 70 + ((i * 2377) % 560) }));
  const edges = Array.from({ length: count - 1 }, (_, i) => ({ source: nodes[i].id, target: nodes[i + 1].id }));
  const input = { width, height, nodes, edges, kind: 'force' };
  // Frozen previous app.js FR algorithm: N(N-1)/2 pairs, 120/240 iterations,
  // synchronous on the main thread. Only collision jitter is deterministic here.
  function baseline() {
    const items = nodes.map(n => ({ ...n })), n = items.length;
    const idx = new Map(items.map((it, i) => [it.id, i])), k = Math.sqrt(width * height / n) * 0.62, k2 = k * k;
    const iters = n > 300 ? 120 : 240, disp = items.map(() => ({ x: 0, y: 0 }));
    const start = performance.now();
    for (let iteration = 0; iteration < iters; iteration++) {
      const temp = (1 - iteration / iters) * Math.min(width, height) * 0.1 + 2;
      for (const d of disp) { d.x = 0; d.y = 0; }
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
        let x = items[i].x - items[j].x, y = items[i].y - items[j].y;
        if (x * x + y * y < 0.01) { x = ((i % 23) - 11) / 100 || 0.1; y = ((j % 19) - 9) / 100 || 0.1; }
        const d2 = x * x + y * y, fx = x * k2 / d2, fy = y * k2 / d2;
        disp[i].x += fx; disp[i].y += fy; disp[j].x -= fx; disp[j].y -= fy;
      }
      for (const e of edges) { const a = idx.get(e.source), b = idx.get(e.target); if (a === b) continue; const x = items[a].x - items[b].x, y = items[a].y - items[b].y, factor = (Math.hypot(x, y) || 1) / k; disp[a].x -= x * factor; disp[a].y -= y * factor; disp[b].x += x * factor; disp[b].y += y * factor; }
      for (let i = 0; i < n; i++) {
        disp[i].x += (width / 2 - items[i].x) * 0.06; disp[i].y += (height / 2 - items[i].y) * 0.06;
        const distance = Math.hypot(disp[i].x, disp[i].y) || 1, limit = Math.min(distance, temp);
        items[i].x = Math.max(70, Math.min(width - 70, items[i].x + disp[i].x / distance * limit));
        items[i].y = Math.max(70, Math.min(height - 70, items[i].y + disp[i].y / distance * limit));
      }
    }
    return { kernel_ms: performance.now() - start, iterations: iters, pair_interactions: n * (n - 1) / 2 * iters };
  }
  const old = baseline();
  let ticks = 0, maxGap = 0, previous = performance.now();
  const heartbeat = setInterval(() => { const now = performance.now(); maxGap = Math.max(maxGap, now - previous); previous = now; ticks++; }, 10);
  const begin = performance.now();
  const result = await new Promise((resolve, reject) => { const worker = new Worker(fileURLToPath(import.meta.url), { workerData: input }); worker.once('message', resolve); worker.once('error', reject); worker.once('exit', code => { if (code) reject(new Error(`worker exited ${code}`)); }); });
  const workerElapsed = performance.now() - begin; clearInterval(heartbeat);
  assert.equal(result.nodes.length, count);
  assert.ok(result.nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y) && n.x >= 70 && n.x <= width - 70 && n.y >= 70 && n.y <= height - 70));
  assert.deepEqual(result.nodes.map(n => n.id), nodes.map(n => n.id));
  console.log(JSON.stringify({ evidence: 'synthetic-node-layout-kernel-not-browser-fps', node: process.version, nodes: count, edges: edges.length,
    source_sha256: createHash('sha256').update(source).digest('hex'), baseline_sync: old,
    candidate_worker: { kernel_ms: result.kernel_ms, wall_including_worker_startup_ms: workerElapsed, iterations: result.iterations, directed_cell_interactions: result.interactions, main_thread_10ms_heartbeat_ticks: ticks, max_observed_heartbeat_gap_ms: maxGap },
    limitation: 'Single observation; algorithms intentionally change geometry. Pair interactions update two nodes; cell interactions update one. Browser rendering, frame pacing, zoom and drag require browser evidence.' }, null, 2));
}
