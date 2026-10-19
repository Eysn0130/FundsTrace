/* Display geometry assessment only. No account facts, amounts or directions are changed. */
(function (root) {
  'use strict';
  const MAX_CHECKS = 65536, MAX_WORK = 262144, MAX_CELLS = 64;
  const EDGE_CHECKS = 8192, EDGE_NODE_CHECKS = 16384;
  const compareId = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const positive = (value, fallback) => Number.isFinite(value) && value > 0 ? value : fallback;
  function dimensions(node) {
    const size = positive(Array.isArray(node.size) ? Math.max(...node.size) : node.size, 48);
    return { width: Math.max(size, positive(node.width, size)), height: Math.max(size, positive(node.height, size)),
      offsetX: Number.isFinite(node.offsetX) ? node.offsetX : 0,
      offsetY: Number.isFinite(node.offsetY) ? node.offsetY : 0 };
  }
  function inputIds(input) {
    if (!Array.isArray(input?.nodes)) return null;
    const ids = new Set();
    for (const node of input.nodes) {
      if (!node || typeof node.id !== 'string' || ids.has(node.id)) return null;
      ids.add(node.id);
    }
    return ids;
  }
  function validate(input, result) {
    const ids = inputIds(input);
    if (!ids) return { valid: false, reason: 'invalid-input-ids' };
    if (!Array.isArray(result?.nodes)) return { valid: false, reason: 'missing-result-nodes' };
    if (result.nodes.length !== ids.size) return { valid: false, reason: 'node-count-mismatch' };
    let dimension = Math.max(1, positive(input.width, 900), positive(input.height, 600));
    for (const node of input.nodes) {
      const d = dimensions(node);
      if (d.width > 1e9 || d.height > 1e9 || Math.abs(d.offsetX) > 1e12 || Math.abs(d.offsetY) > 1e12) return { valid: false, reason: 'input-geometry-overflow' };
      dimension = Math.max(dimension, d.width + Math.abs(d.offsetX) * 2, d.height + Math.abs(d.offsetY) * 2);
    }
    // Permit large world-space layouts; reject magnitudes that make rendering
    // arithmetic unsafe or indicate a failed numerical solver.
    const limit = Math.min(1e12, Math.max(1e6, dimension * Math.max(64, ids.size + 1) * 4096));
    const seen = new Set();
    for (const node of result.nodes) {
      if (!node || typeof node.id !== 'string' || !ids.has(node.id)) return { valid: false, reason: 'unexpected-node-id' };
      if (seen.has(node.id)) return { valid: false, reason: 'duplicate-node-id' };
      seen.add(node.id);
      if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return { valid: false, reason: 'nonfinite-coordinate' };
      if (Math.abs(node.x) > limit || Math.abs(node.y) > limit) return { valid: false, reason: 'coordinate-overflow' };
    }
    return { valid: true, reason: null };
  }
  function profile(input) {
    const ids = inputIds(input);
    if (!ids) return { valid: false, nodeCount: 0, edgeCount: 0, componentCount: 0, maxDegree: 0, density: 0,
      cyclic: false, largestComponent: 0, undirectedEdgeCount: 0 };
    const adjacency = new Map(), out = new Map(), incoming = new Map();
    for (const id of ids) { adjacency.set(id, new Set()); out.set(id, new Set()); incoming.set(id, 0); }
    let edgeCount = 0, undirectedEdgeCount = 0, nonselfEdges = 0, selfLoop = false;
    for (const edge of input.edges || []) {
      if (!edge || !ids.has(edge.source) || !ids.has(edge.target) || out.get(edge.source).has(edge.target)) continue;
      out.get(edge.source).add(edge.target); edgeCount++; incoming.set(edge.target, incoming.get(edge.target) + 1);
      if (edge.source === edge.target) { selfLoop = true; continue; }
      nonselfEdges++;
      if (!adjacency.get(edge.source).has(edge.target)) undirectedEdgeCount++;
      adjacency.get(edge.source).add(edge.target); adjacency.get(edge.target).add(edge.source);
    }
    let componentCount = 0, largestComponent = 0, maxDegree = 0;
    const seen = new Set();
    for (const id of ids) {
      maxDegree = Math.max(maxDegree, adjacency.get(id).size);
      if (seen.has(id)) continue;
      componentCount++;
      const queue = [id]; seen.add(id);
      for (let head = 0; head < queue.length; head++) for (const next of adjacency.get(queue[head])) {
        if (!seen.has(next)) { seen.add(next); queue.push(next); }
      }
      largestComponent = Math.max(largestComponent, queue.length);
    }
    const queue = [];
    for (const [id, degree] of incoming) if (degree === 0) queue.push(id);
    let visited = 0;
    for (let head = 0; head < queue.length; head++) {
      visited++;
      for (const next of out.get(queue[head])) {
        const degree = incoming.get(next) - 1; incoming.set(next, degree);
        if (degree === 0) queue.push(next);
      }
    }
    return { valid: true, nodeCount: ids.size, edgeCount, componentCount, maxDegree,
      density: ids.size > 1 ? nonselfEdges / (ids.size * (ids.size - 1)) : 0,
      cyclic: selfLoop || visited !== ids.size, largestComponent, undirectedEdgeCount };
  }
  function candidates(input) {
    const kind = input?.kind || 'auto';
    if (kind !== 'auto') return [kind];
    const shape = profile(input);
    if (!shape.valid || shape.nodeCount < 3) return ['auto'];
    let alternative = 'dagre';
    if (shape.maxDegree >= Math.max(4, shape.nodeCount * 0.4) && shape.undirectedEdgeCount <= shape.nodeCount * 1.3) alternative = 'structural';
    else if (shape.edgeCount > shape.nodeCount * 1.4 || shape.cyclic && shape.nodeCount > 12 ||
      shape.componentCount > 2 && shape.largestComponent < shape.nodeCount / 2) alternative = 'partition';
    return ['auto', alternative];
  }
  function rectangles(input, result, zoom) {
    const points = new Map(result.nodes.map(node => [node.id, node]));
    return input.nodes.map(node => {
      const point = points.get(node.id), d = dimensions(node);
      const screenGap = positive(node.labelGap, 0), reservedGap = positive(node.gapWorld, screenGap);
      const extra = zoom > 0 ? Math.max(0, screenGap / zoom - reservedGap) : 0;
      // The label stays below its body. A fixed CSS-pixel gap grows in world
      // coordinates when fitting out; only the bottom of the envelope moves.
      d.height += extra; d.offsetY += extra / 2;
      const x = point.x + d.offsetX, y = point.y + d.offsetY;
      return { id: node.id, x: point.x, y: point.y, minX: x - d.width / 2, minY: y - d.height / 2,
        maxX: x + d.width / 2, maxY: y + d.height / 2, width: d.width, height: d.height };
    }).sort((a, b) => compareId(a.id, b.id));
  }
  const intersects = (a, b) => a.minX < b.maxX - 1e-7 && b.minX < a.maxX - 1e-7 && a.minY < b.maxY - 1e-7 && b.minY < a.maxY - 1e-7;
  function overlapAssessment(boxes) {
    if (boxes.length < 2) return { count: 0, checks: 0, work: 0, coverage: 'exact', totalPairs: 0 };
    const sizes = boxes.map(b => Math.max(b.width, b.height)).sort((a, b) => a - b);
    const cellSize = Math.max(16, sizes[Math.floor(sizes.length / 2)]), cells = new Map(), overflow = [];
    let count = 0, checks = 0, work = 0, complete = true;
    for (let index = 0; index < boxes.length; index++) {
      const box = boxes[index], minX = Math.floor(box.minX / cellSize), maxX = Math.floor(box.maxX / cellSize),
        minY = Math.floor(box.minY / cellSize), maxY = Math.floor(box.maxY / cellSize);
      const large = (maxX - minX + 1) * (maxY - minY + 1) > MAX_CELLS;
      const checked = new Set();
      const check = other => {
        if (++work > MAX_WORK) return false;
        if (checked.has(other)) return true;
        if (checks >= MAX_CHECKS) return false;
        checked.add(other); checks++;
        if (intersects(box, boxes[other])) count++;
        return true;
      };
      if (large) {
        for (let other = 0; other < index; other++) if (!check(other)) { complete = false; break; }
      } else {
        for (const other of overflow) if (!check(other)) { complete = false; break; }
        for (let x = minX; complete && x <= maxX; x++) for (let y = minY; complete && y <= maxY; y++) {
          if (++work > MAX_WORK) { complete = false; break; }
          for (const other of cells.get(`${x},${y}`) || []) if (!check(other)) { complete = false; break; }
        }
      }
      if (!complete) break;
      if (large) overflow.push(index);
      else for (let x = minX; x <= maxX; x++) for (let y = minY; y <= maxY; y++) {
        const key = `${x},${y}`; if (!cells.has(key)) cells.set(key, []); cells.get(key).push(index);
      }
    }
    return { count, checks, work, coverage: complete ? 'exact' : 'sampled', totalPairs: boxes.length * (boxes.length - 1) / 2 };
  }
  function pairAt(index, length) {
    // Flatten the upper triangle. Binary search avoids enumerating skipped pairs.
    let lo = 0, hi = length - 1;
    const start = row => row * (2 * length - row - 1) / 2;
    while (lo + 1 < hi) { const mid = Math.floor((lo + hi) / 2); if (start(mid) <= index) lo = mid; else hi = mid; }
    return [lo, lo + 1 + index - start(lo)];
  }
  const turn = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  function crosses(a, b, c, d) {
    return turn(a, b, c) * turn(a, b, d) < -1e-7 && turn(c, d, a) * turn(c, d, b) < -1e-7;
  }
  function throughBox(a, b, box) {
    let lo = 0, hi = 1;
    const dx = b.x - a.x, dy = b.y - a.y;
    for (const [p, q] of [[-dx, a.x - box.minX], [dx, box.maxX - a.x], [-dy, a.y - box.minY], [dy, box.maxY - a.y]]) {
      if (p === 0) { if (q <= 0) return false; continue; }
      const t = q / p;
      if (p < 0) lo = Math.max(lo, t); else hi = Math.min(hi, t);
      if (lo >= hi) return false;
    }
    return hi > 0 && lo < 1;
  }
  function edgeEstimates(input, boxes) {
    const byId = new Map(boxes.map(box => [box.id, box])), unique = new Map();
    for (const edge of input.edges || []) if (byId.has(edge?.source) && byId.has(edge?.target) && edge.source !== edge.target) {
      const key = JSON.stringify([edge.source, edge.target]); if (!unique.has(key)) unique.set(key, { source: edge.source, target: edge.target });
    }
    const edges = [...unique.values()].sort((a, b) => compareId(a.source, b.source) || compareId(a.target, b.target));
    const pairCount = edges.length * (edges.length - 1) / 2, pairSamples = Math.min(EDGE_CHECKS, pairCount);
    let crossings = 0, crossingChecks = 0;
    for (let sample = 0; sample < pairSamples; sample++) {
      const [i, j] = pairAt(Math.floor(sample * pairCount / pairSamples), edges.length), a = edges[i], b = edges[j];
      if (a.source === b.source || a.source === b.target || a.target === b.source || a.target === b.target) continue;
      crossingChecks++;
      if (crosses(byId.get(a.source), byId.get(a.target), byId.get(b.source), byId.get(b.target))) crossings++;
    }
    const possibilities = edges.length * boxes.length, nodeSamples = Math.min(EDGE_NODE_CHECKS, possibilities);
    let nodeIntersections = 0, nodeChecks = 0;
    for (let sample = 0; sample < nodeSamples; sample++) {
      const flat = Math.floor(sample * possibilities / nodeSamples), edge = edges[Math.floor(flat / boxes.length)], box = boxes[flat % boxes.length];
      if (edge.source === box.id || edge.target === box.id) continue;
      nodeChecks++;
      if (throughBox(byId.get(edge.source), byId.get(edge.target), box)) nodeIntersections++;
    }
    return { geometry: 'straight-center-line-estimate', crossings: { count: crossings, checks: crossingChecks,
      coverage: pairSamples === pairCount ? 'exact' : 'sampled', totalPairs: pairCount },
      nodeIntersections: { count: nodeIntersections, checks: nodeChecks,
        coverage: nodeSamples === possibilities ? 'exact' : 'sampled', totalPairs: possibilities } };
  }
  function evaluate(input, result, options = {}) {
    const valid = validate(input, result);
    if (!valid.valid) return valid;
    const width = positive(input.width, 900), height = positive(input.height, 600), padding = 24;
    const maxZoom = positive(input.maxZoom, 1.6), bounds = { minX: 0, minY: 0, maxX: 0, maxY: 0 };
    let zoom = maxZoom, boxes, fitMethod = 'padded-viewport-estimate', iterations = 0;
    const measure = () => {
      boxes = rectangles(input, result, zoom);
      bounds.minX = boxes.length ? Infinity : 0; bounds.minY = boxes.length ? Infinity : 0;
      bounds.maxX = boxes.length ? -Infinity : 0; bounds.maxY = boxes.length ? -Infinity : 0;
      for (const box of boxes) { bounds.minX = Math.min(bounds.minX, box.minX); bounds.minY = Math.min(bounds.minY, box.minY);
        bounds.maxX = Math.max(bounds.maxX, box.maxX); bounds.maxY = Math.max(bounds.maxY, box.maxY); }
    };
    // Bounded fixed-point estimate mirrors the host's camera-only fit. It is
    // deliberately an estimate: shadows and actual routed edges are host data.
    for (; iterations < 8; iterations++) {
      measure();
      let nextZoom = Math.max(0.000001, Math.min(maxZoom, Math.max(1, width - padding * 2) / Math.max(1, bounds.maxX - bounds.minX),
        Math.max(1, height - padding * 2) / Math.max(1, bounds.maxY - bounds.minY)));
      if (typeof options.fitViewport === 'function') {
        const fitted = options.fitViewport({ bounds, width, height, padding, maxZoom, minZoom: 0.000001, obstacles: input.chromeObstacles || [] });
        if (Number.isFinite(fitted?.zoom) && fitted.zoom > 0) { nextZoom = fitted.zoom; fitMethod = 'host-viewport-estimate'; }
      }
      if (Math.abs(nextZoom - zoom) < 1e-7) { zoom = nextZoom; iterations++; break; }
      zoom = nextZoom;
    }
    measure();
    let fontSize = positive(input.fontSize, Infinity);
    for (const node of input.nodes) fontSize = Math.min(fontSize, positive(node.fontSize, Infinity));
    for (const edge of input.edges || []) fontSize = Math.min(fontSize, positive(edge.fontSize, Infinity));
    if (!Number.isFinite(fontSize)) fontSize = 13;
    const minimumFontPixels = fontSize * zoom, targetFontPixels = positive(input.minimumFontPixels, 12);
    const area = boxes.reduce((sum, box) => sum + box.width * box.height, 0);
    return { ...valid, profile: profile(input), overlaps: overlapAssessment(boxes), bounds,
      fit: { zoom, minimumFontPixels, targetFontPixels, readable: minimumFontPixels >= targetFontPixels, method: fitMethod, iterations },
      compactness: boxes.length ? Math.max(1, bounds.maxX - bounds.minX) * Math.max(1, bounds.maxY - bounds.minY) / Math.max(1, area) : 1,
      edges: edgeEstimates(input, boxes) };
  }
  function prefer(a, b) {
    if (!a?.valid) return b?.valid ? b : null;
    if (!b?.valid) return a;
    // A bounded scan's zero is not proof of zero collisions. Partial scans can
    // inspect different pairs, so neither their counts nor a change of coverage
    // proves dominance. Keep the current valid candidate in that case.
    if (a.overlaps?.coverage !== 'exact' || b.overlaps?.coverage !== 'exact') return a;
    const readable = score => Math.min(1, score.fit.minimumFontPixels / score.fit.targetFontPixels);
    const readingA = readable(a), readingB = readable(b);
    // Keep the current candidate whenever collision reduction trades away
    // reading size, or larger text trades away collision clearance. Automatic
    // selection requires a non-deteriorating candidate, not a weighted score.
    if (b.overlaps.count > a.overlaps.count || readingB < readingA - 1e-7) return a;
    if (b.overlaps.count < a.overlaps.count || readingB > readingA + 1e-7) return b;
    if (Math.abs(a.compactness - b.compactness) > 1e-7) return a.compactness < b.compactness ? a : b;
    return a;
  }
  root.FundsGraphLayoutQuality = { validate, profile, evaluate, prefer, candidates };
})(typeof self !== 'undefined' ? self : globalThis);
