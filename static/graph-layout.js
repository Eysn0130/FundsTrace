/* Deterministic display-only layout kernel. No account amounts are converted or summed here. */
(function (root) {
  'use strict';
  function hash(id) { let h = 2166136261; for (const c of String(id)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; }
  function seed(id, width, height) {
    const h = hash(id), angle = (h % 6283) / 1000, radius = 0.12 + ((h >>> 13) % 1000) / 4000;
    return { x: width / 2 + Math.cos(angle) * width * radius, y: height / 2 + Math.sin(angle) * height * radius };
  }
  const compareId = (a, b) => a < b ? -1 : a > b ? 1 : 0;

  // Geometry only: a degree anchor is never a business main-account decision.
  // Sparse multi-source BFS replaces all-pairs distances. Crowded levels spill
  // into more rings; coordinates may exceed the viewport instead of collapsing
  // thousands of nodes into overlapping positions. The host owns fit/zoom.
  function* structuralPositions(input) {
    const width = Number.isFinite(input.width) && input.width > 0 ? input.width : 900;
    const height = Number.isFinite(input.height) && input.height > 0 ? input.height : 600;
    const source = input.nodes || [], index = new Map(), adjacency = source.map(() => new Set());
    let diameter = Number.isFinite(input.nodeSize) && input.nodeSize > 0 ? input.nodeSize : 48;
    let work = 0, interactions = 0;
    const checkpoint = () => ++work % 256 === 0;
    for (let i = 0; i < source.length; i++) {
      const node = source[i];
      if (typeof node.id !== 'string' || index.has(node.id)) throw new Error('structural layout requires unique string node IDs');
      index.set(node.id, i);
      if (Number.isFinite(node.size) && node.size > 0) diameter = Math.max(diameter, node.size);
      if (checkpoint()) yield;
    }
    for (const edge of input.edges || []) {
      const a = index.get(edge.source), b = index.get(edge.target);
      if (a !== undefined && b !== undefined && a !== b) { adjacency[a].add(b); adjacency[b].add(a); }
      if (checkpoint()) yield;
    }
    const byId = (a, b) => compareId(source[a].id, source[b].id);
    const order = source.map((_, i) => i).sort(byId);
    const adjacent = [];
    for (const neighbors of adjacency) { adjacent.push([...neighbors].sort(byId)); if (checkpoint()) yield; }
    const spacing = diameter + 16, componentGap = spacing * 1.5;
    const seen = new Set(), components = [], depth = new Int32Array(source.length).fill(-1);
    for (const start of order) {
      if (seen.has(start)) continue;
      const queue = [start]; seen.add(start);
      for (let head = 0; head < queue.length; head++) {
        for (const next of adjacent[queue[head]]) {
          interactions++;
          if (!seen.has(next)) { seen.add(next); queue.push(next); }
          if (checkpoint()) yield;
        }
        if (checkpoint()) yield;
      }
      const members = queue.sort(byId);
      let anchors = members.filter(i => source[i].isMain === true || source[i].isMain === 1);
      const explicit = anchors.length > 0;
      if (!explicit) {
        let best = members[0];
        for (const i of members) { if (adjacent[i].length > adjacent[best].length) best = i; if (checkpoint()) yield; }
        anchors = [best];
      }
      const bfs = [...anchors], groups = [anchors.slice()];
      anchors.forEach(i => { depth[i] = 0; });
      for (let head = 0; head < bfs.length; head++) {
        const current = bfs[head];
        for (const next of adjacent[current]) {
          interactions++;
          if (depth[next] === -1) {
            depth[next] = depth[current] + 1;
            if (!groups[depth[next]]) groups[depth[next]] = [];
            groups[depth[next]].push(next); bfs.push(next);
          }
          if (checkpoint()) yield;
        }
        if (checkpoint()) yield;
      }
      const points = new Map(), maxDepth = groups.length - 1;
      // A very deep path/ring is folded into a bounded-area BFS grid instead
      // of consuming one new radius per hop. No topology or edge is removed.
      const grid = members.length > 16 && maxDepth > Math.sqrt(members.length) * 2;
      if (grid) {
        const columns = Math.max(2, Math.ceil(Math.sqrt(members.length * Math.min(2, Math.max(0.5, width / height)))));
        for (let i = 0; i < bfs.length; i++) {
          const row = Math.floor(i / columns), col = i % columns;
          points.set(bfs[i], { x: (row % 2 ? columns - col - 1 : col) * spacing, y: row * spacing });
          if (checkpoint()) yield;
        }
      } else {
        let radius = 0;
        const rotation = (hash(source[anchors[0]].id) % 6283) / 1000;
        for (let level = 0; level < groups.length; level++) {
          const group = groups[level];
          if (level === 0 && group.length === 1) { points.set(group[0], { x: 0, y: 0 }); continue; }
          let placed = 0;
          while (placed < group.length) {
            radius += spacing;
            // Chord distance, rather than circumference alone, bounds spacing.
            const capacity = Math.max(1, Math.floor(Math.PI / Math.asin(Math.min(1, spacing / (2 * radius)))));
            const count = Math.min(capacity, group.length - placed);
            for (let slot = 0; slot < count; slot++) {
              const angle = rotation + radius / spacing * 0.37 + 2 * Math.PI * slot / count;
              points.set(group[placed++], { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
              if (checkpoint()) yield;
            }
          }
        }
      }
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const point of points.values()) {
        minX = Math.min(minX, point.x - diameter / 2); minY = Math.min(minY, point.y - diameter / 2);
        maxX = Math.max(maxX, point.x + diameter / 2); maxY = Math.max(maxY, point.y + diameter / 2);
        if (checkpoint()) yield;
      }
      components.push({ members, anchors, explicit, points, minX, minY,
        width: maxX - minX, height: maxY - minY, method: grid ? 'bfs-grid' : 'bfs-rings' });
      if (checkpoint()) yield;
    }
    components.sort((a, b) => b.members.length - a.members.length || byId(a.members[0], b.members[0]));
    let area = 0, largestWidth = 0;
    for (const component of components) {
      area += (component.width + componentGap) * (component.height + componentGap);
      largestWidth = Math.max(largestWidth, component.width);
      if (checkpoint()) yield;
    }
    const targetWidth = Math.max(largestWidth, Math.sqrt(area * Math.min(2, Math.max(0.5, width / height))));
    let x = 0, y = 0, rowHeight = 0, worldWidth = 0, worldHeight = 0;
    for (const component of components) {
      if (x > 0 && x + component.width > targetWidth) { x = 0; y += rowHeight + componentGap; rowHeight = 0; }
      component.x = x; component.y = y;
      x += component.width + componentGap; rowHeight = Math.max(rowHeight, component.height);
      worldWidth = Math.max(worldWidth, component.x + component.width);
      worldHeight = Math.max(worldHeight, component.y + component.height);
      if (checkpoint()) yield;
    }
    const offsetX = 70 + Math.max(0, (width - 140 - worldWidth) / 2);
    const offsetY = 70 + Math.max(0, (height - 140 - worldHeight) / 2);
    const positioned = new Map(), summaries = [];
    for (const component of components) {
      for (const [i, point] of component.points) {
        positioned.set(i, { x: offsetX + component.x + point.x - component.minX,
          y: offsetY + component.y + point.y - component.minY });
        if (checkpoint()) yield;
      }
      summaries.push({ nodeCount: component.members.length, anchorIds: component.anchors.map(i => source[i].id),
        anchorReason: component.explicit ? 'explicit' : 'degree', method: component.method,
        bounds: { x: offsetX + component.x, y: offsetY + component.y, width: component.width, height: component.height } });
    }
    const nodes = [];
    for (let i = 0; i < source.length; i++) { nodes.push({ id: source[i].id, ...positioned.get(i) }); if (checkpoint()) yield; }
    return { nodes, interactions, structure: { componentCount: summaries.length, components: summaries, spacing,
      bounds: { x: offsetX, y: offsetY, width: worldWidth, height: worldHeight } } };
  }
  function structuralSimulation(input) {
    const iterator = structuralPositions(input);
    let result = null, steps = 0;
    return { step() {
      if (result) return false;
      steps++;
      const next = iterator.next();
      if (!next.done) return true;
      result = { ...next.value, iterations: steps }; return false;
    }, result: () => result || { nodes: [], iterations: steps, interactions: 0 } };
  }
  function simulation(input) {
    if (input.kind === 'structural') return structuralSimulation(input);
    const width = Math.max(180, input.width || 900), height = Math.max(180, input.height || 600);
    const nodes = input.nodes.map(n => ({ id: n.id, ...seed(n.id, width, height), ...(Number.isFinite(n.x) && Number.isFinite(n.y) ? { x: n.x, y: n.y } : {}) }));
    const index = new Map(nodes.map((n, i) => [n.id, i]));
    const edges = input.edges.map(e => [index.get(e.source), index.get(e.target)]).filter(([a, b]) => a != null && b != null && a !== b);
    const iterations = nodes.length > 1000 ? 70 : 110;
    let iteration = 0, interactions = 0;
    function hierarchy() {
      // Cyclic/unreachable components retain a deterministic fallback layer.
      const degree = nodes.map(() => 0), outgoing = nodes.map(() => []), level = nodes.map(() => 0);
      for (const [a, b] of edges) { degree[b]++; outgoing[a].push(b); }
      const queue = []; degree.forEach((d, i) => { if (!d) queue.push(i); });
      for (let head = 0; head < queue.length; head++) { const i = queue[head]; for (const j of outgoing[i]) { level[j] = Math.max(level[j], level[i] + 1); if (--degree[j] === 0) queue.push(j); } }
      const groups = new Map(); nodes.forEach((n, i) => { const l = degree[i] ? 0 : level[i]; if (!groups.has(l)) groups.set(l, []); groups.get(l).push(n); });
      const levels = [...groups.keys()].sort((a, b) => a - b);
      levels.forEach((l, row) => { const group = groups.get(l).sort((a, b) => String(a.id).localeCompare(String(b.id))); group.forEach((n, col) => { n.x = 70 + (width - 140) * (col + 1) / (group.length + 1); n.y = 70 + (height - 140) * (row + 1) / (levels.length + 1); }); });
      iteration = iterations;
    }
    function step() {
      if (iteration >= iterations || nodes.length < 2) return false;
      if (input.kind === 'dagre') { hierarchy(); return false; }
      const n = nodes.length, k = Math.sqrt(width * height / n) * 0.62, k2 = k * k;
      // Fixed 12x12 grid bounds each node's aggregate-cell work. Nearby cells use
      // exact pair forces; distant cells use their centroid. This approximates
      // geometry only and avoids quadratic force work on a large spread graph.
      const cellSize = Math.max(width, height) / 12, cells = new Map();
      for (let i = 0; i < n; i++) { const p = nodes[i], gx = Math.floor(p.x / cellSize), gy = Math.floor(p.y / cellSize), key = `${gx},${gy}`; let c = cells.get(key); if (!c) { c = { gx, gy, x: 0, y: 0, items: [] }; cells.set(key, c); } c.items.push(i); c.x += p.x; c.y += p.y; }
      for (const c of cells.values()) { c.x /= c.items.length; c.y /= c.items.length; }
      const dx = new Float64Array(n), dy = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        const p = nodes[i], gx = Math.floor(p.x / cellSize), gy = Math.floor(p.y / cellSize);
        for (const c of cells.values()) {
          if (Math.abs(gx - c.gx) <= 1 && Math.abs(gy - c.gy) <= 1) {
            const sample = c.items.slice(0, 24);
            for (const j of sample) { if (j === i) continue; let x = p.x - nodes[j].x, y = p.y - nodes[j].y; if (x * x + y * y < 0.01) { x = ((hash(p.id) % 23) - 11) / 100 || 0.1; y = ((hash(nodes[j].id) % 19) - 9) / 100 || 0.1; } const d2 = Math.max(0.01, x * x + y * y); dx[i] += x * k2 / d2; dy[i] += y * k2 / d2; interactions++; }
            // Dense or coincident saved positions also have bounded work.
            if (c.items.length > sample.length) { const x = p.x - c.x || ((hash(p.id) % 23) - 11) / 100 || 0.1, y = p.y - c.y || 0.1, d2 = Math.max(0.01, x * x + y * y); dx[i] += x * k2 * (c.items.length - sample.length) / d2; dy[i] += y * k2 * (c.items.length - sample.length) / d2; interactions++; }
          } else { const x = p.x - c.x, y = p.y - c.y, d2 = Math.max(0.01, x * x + y * y); dx[i] += x * k2 * c.items.length / d2; dy[i] += y * k2 * c.items.length / d2; interactions++; }
        }
      }
      for (const [a, b] of edges) { const x = nodes[a].x - nodes[b].x, y = nodes[a].y - nodes[b].y, factor = Math.sqrt(x * x + y * y) / k; dx[a] -= x * factor; dy[a] -= y * factor; dx[b] += x * factor; dy[b] += y * factor; }
      const temperature = (1 - iteration / iterations) * Math.min(width, height) * 0.1 + 2;
      for (let i = 0; i < n; i++) { const p = nodes[i]; dx[i] += (width / 2 - p.x) * 0.06; dy[i] += (height / 2 - p.y) * 0.06; const distance = Math.hypot(dx[i], dy[i]) || 1, limit = Math.min(distance, temperature); p.x = Math.max(70, Math.min(width - 70, p.x + dx[i] / distance * limit)); p.y = Math.max(70, Math.min(height - 70, p.y + dy[i] / distance * limit)); }
      iteration++;
      return iteration < iterations;
    }
    return { step, result: () => ({ nodes, iterations: iteration, interactions }) };
  }
  async function run(input, options = {}) {
    const model = simulation(input), pause = options.pause || (() => new Promise(resolve => setTimeout(resolve, 0)));
    while (!options.cancelled?.()) { if (!model.step()) return model.result(); await pause(); }
    return null;
  }
  root.FundsGraphLayout = Object.freeze({ seed, simulation, run });
  if (typeof document === 'undefined' && typeof root.postMessage === 'function' && typeof root.importScripts === 'function') {
    root.onmessage = event => { const { requestId, input } = event.data; try { const model = simulation(input); while (model.step()) { /* worker can be terminated between requests */ } root.postMessage({ requestId, result: model.result() }); } catch (error) { root.postMessage({ requestId, error: String(error.message || error) }); } };
  }
})(typeof globalThis === 'undefined' ? self : globalThis);
