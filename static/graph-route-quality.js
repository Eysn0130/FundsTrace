/* Display geometry only. Actual rendered paths are sampled; no graph facts or positions are changed. */
(function (root) {
  'use strict';
  const EPS = 1e-7;
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const pairKey = (...values) => JSON.stringify(values);
  const finitePoint = p => p && Number.isFinite(p.x) && Number.isFinite(p.y);
  const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const boxOf = (a, b) => ({ minX: Math.min(a.x, b.x), minY: Math.min(a.y, b.y), maxX: Math.max(a.x, b.x), maxY: Math.max(a.y, b.y) });
  const validBox = b => b && [b.minX, b.minY, b.maxX, b.maxY].every(Number.isFinite) && b.minX <= b.maxX && b.minY <= b.maxY;
  const boxesMeet = (a, b) => a.minX <= b.maxX + EPS && a.maxX + EPS >= b.minX && a.minY <= b.maxY + EPS && a.maxY + EPS >= b.minY;
  const boxesOverlap = (a, b) => a.minX < b.maxX - EPS && a.maxX > b.minX + EPS && a.minY < b.maxY - EPS && a.maxY > b.minY + EPS;
  const inside = (p, b) => p.x >= b.minX - EPS && p.x <= b.maxX + EPS && p.y >= b.minY - EPS && p.y <= b.maxY + EPS;
  const cross = (a, b) => a.x * b.y - a.y * b.x;

  // Return an arc-length midpoint rather than a Bezier parameter midpoint. M
  // starts a new subpath; its jump is never treated as an actual line segment.
  function sample(path, options = {}) {
    const bounded = (value, fallback, max) => Math.max(1, Math.min(max, Math.floor(Number.isFinite(value) ? value : fallback)));
    const steps = Math.max(2, bounded(options.steps, 12, 64));
    const maxSegments = bounded(options.maxSegments, 4096, 65536);
    const maxWork = bounded(options.maxWork, 131072, 131072);
    const points = [], segments = [];
    let cursor = null, start = null, bounds = null, length = 0, valid = true, truncated = false, work = 0;
    const addPoint = p => {
      points.push(p);
      if (!bounds) bounds = { minX: p.x, minY: p.y, maxX: p.x, maxY: p.y };
      else { bounds.minX = Math.min(bounds.minX, p.x); bounds.minY = Math.min(bounds.minY, p.y); bounds.maxX = Math.max(bounds.maxX, p.x); bounds.maxY = Math.max(bounds.maxY, p.y); }
    };
    const line = p => {
      if (segments.length >= maxSegments) { truncated = true; return false; }
      const size = distance(cursor, p);
      if (!Number.isFinite(size) || !Number.isFinite(length + size)) { valid = false; return false; }
      if (size > EPS) { segments.push({ a: cursor, b: p, length: size, bounds: boxOf(cursor, p) }); length += size; }
      addPoint(p); cursor = p; return true;
    };
    if (!Array.isArray(path)) valid = false;
    for (const command of Array.isArray(path) ? path : []) {
      if (truncated || !valid) break;
      if (work >= maxWork) { truncated = true; break; }
      work++;
      if (!Array.isArray(command) || typeof command[0] !== 'string') { valid = false; break; }
      const raw = command[0], kind = raw.toUpperCase(), relative = raw !== kind;
      const count = { M: 2, L: 2, Q: 4, C: 6, H: 1, V: 1, Z: 0 }[kind];
      if (count === undefined || command.length !== count + 1 || !command.slice(1).every(Number.isFinite) || !cursor && kind !== 'M') { valid = false; break; }
      const base = relative && cursor ? cursor : { x: 0, y: 0 };
      const point = i => ({ x: command[i] + base.x, y: command[i + 1] + base.y });
      if (kind === 'M') { cursor = point(1); if (!finitePoint(cursor)) { valid = false; break; } start = cursor; addPoint(cursor); }
      else if (kind === 'L') line(point(1));
      else if (kind === 'H') line({ x: command[1] + (relative ? cursor.x : 0), y: cursor.y });
      else if (kind === 'V') line({ x: cursor.x, y: command[1] + (relative ? cursor.y : 0) });
      else if (kind === 'Z') line(start);
      else {
        const a = cursor, b = point(1), c = point(3), d = kind === 'C' ? point(5) : null;
        for (let i = 1; i <= steps; i++) {
          if (work >= maxWork) { truncated = true; break; }
          work++;
          const t = i / steps, s = 1 - t;
          const p = d ? { x: s * s * s * a.x + 3 * s * s * t * b.x + 3 * s * t * t * c.x + t * t * t * d.x,
            y: s * s * s * a.y + 3 * s * s * t * b.y + 3 * s * t * t * c.y + t * t * t * d.y } :
            { x: s * s * a.x + 2 * s * t * b.x + t * t * c.x, y: s * s * a.y + 2 * s * t * b.y + t * t * c.y };
          if (!finitePoint(p) || !line(p)) { if (!finitePoint(p)) valid = false; break; }
        }
      }
    }
    let midpoint = points[0] || null, tangent = { x: 0, y: 0 }, walked = 0;
    for (const segment of segments) {
      if (walked + segment.length >= length / 2) {
        const t = (length / 2 - walked) / segment.length;
        midpoint = { x: segment.a.x + (segment.b.x - segment.a.x) * t, y: segment.a.y + (segment.b.y - segment.a.y) * t };
        tangent = { x: (segment.b.x - segment.a.x) / segment.length, y: (segment.b.y - segment.a.y) / segment.length }; break;
      }
      walked += segment.length;
    }
    return { valid: valid && points.length > 0, points, segments, length, midpoint, tangent, bounds, truncated,
      approximation: 'sampled-path', steps, work };
  }

  // Liang-Barsky includes a path touching a box, which can obscure its glyphs.
  function segmentBox(a, b, box) {
    let low = 0, high = 1;
    const dx = b.x - a.x, dy = b.y - a.y;
    for (const [p, q] of [[-dx, a.x - box.minX], [dx, box.maxX - a.x], [-dy, a.y - box.minY], [dy, box.maxY - a.y]]) {
      if (Math.abs(p) <= EPS) { if (q < -EPS) return false; }
      else {
        const t = q / p;
        if (p < 0) low = Math.max(low, t); else high = Math.min(high, t);
        if (low > high + EPS) return false;
      }
    }
    return true;
  }

  function relation(a, b, c, d) {
    const r = { x: b.x - a.x, y: b.y - a.y }, s = { x: d.x - c.x, y: d.y - c.y };
    const q = { x: c.x - a.x, y: c.y - a.y }, denominator = cross(r, s);
    const scale = Math.max(1, Math.hypot(r.x, r.y) * Math.hypot(s.x, s.y));
    if (Math.abs(denominator) <= EPS * scale) {
      if (Math.abs(cross(q, r)) > EPS * Math.max(1, Math.hypot(r.x, r.y) * Math.hypot(q.x, q.y))) return null;
      const horizontal = Math.abs(r.x) >= Math.abs(r.y), key = horizontal ? 'x' : 'y';
      const overlap = Math.min(Math.max(a[key], b[key]), Math.max(c[key], d[key])) - Math.max(Math.min(a[key], b[key]), Math.min(c[key], d[key]));
      return overlap > EPS ? { kind: 'overlap' } : null;
    }
    const t = cross(q, s) / denominator, u = cross(q, r) / denominator;
    if (t < -EPS || t > 1 + EPS || u < -EPS || u > 1 + EPS) return null;
    return { kind: 'crossing', point: { x: a.x + t * r.x, y: a.y + t * r.y }, r, s };
  }

  function spatialIndex(cellSize, spend) {
    const cells = new Map(), overflow = [], stampKey = Symbol('spatial-query');
    let querySerial = 0;
    const range = b => [Math.floor(b.minX / cellSize), Math.floor(b.minY / cellSize), Math.floor(b.maxX / cellSize), Math.floor(b.maxY / cellSize)];
    return {
      add(item) {
        const [x0, y0, x1, y1] = range(item.bounds);
        if ((x1 - x0 + 1) * (y1 - y0 + 1) > 64) { overflow.push(item); return true; }
        for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
          if (!spend()) return false;
          const key = x + ':' + y;
          if (!cells.has(key)) cells.set(key, []);
          cells.get(key).push(item);
        }
        return true;
      },
      query(bounds, visit) {
        const [x0, y0, x1, y1] = range(bounds), stamp = ++querySerial;
        const examine = item => {
          if (item[stampKey] === stamp) return true;
          item[stampKey] = stamp;
          if (!spend()) return false;
          return !boxesMeet(bounds, item.bounds) || visit(item) !== false;
        };
        for (const item of overflow) if (!examine(item)) return false;
        if ((x1 - x0 + 1) * (y1 - y0 + 1) > 4096) {
          // Long routes query occupied cells rather than iterating empty space.
          for (const items of cells.values()) {
            if (!spend()) return false;
            for (const item of items) if (!examine(item)) return false;
          }
        } else for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
          if (!spend()) return false;
          for (const item of cells.get(x + ':' + y) || []) if (!examine(item)) return false;
        }
        return true;
      }
    };
  }

  function evaluate(input = {}) {
    const maxChecks = Math.max(0, Math.min(1000000, Math.floor(Number.isFinite(input.maxChecks) ? input.maxChecks : 100000)));
    const witnessLimit = Math.max(0, Math.min(1000, Math.floor(Number.isFinite(input.witnessLimit) ? input.witnessLimit : 40)));
    let checks = 0, complete = true, sampledSegments = 0, processedEdges = 0, totalLength = 0;
    const edgeLengths = Object.create(null);
    const spend = (amount = 1) => { if (checks + amount > maxChecks) { complete = false; return false; } checks += amount; return true; };
    const counts = { edgeNode: 0, edgeNodeLabel: 0, edgeLabel: 0, labelOverlap: 0, labelNode: 0, crossings: 0, collinearOverlap: 0 };
    const collisions = Object.fromEntries(Object.keys(counts).map(key => [key, []]));
    const found = Object.fromEntries(Object.keys(counts).map(key => [key, new Set()]));
    const record = (kind, key, witness) => {
      if (found[kind].has(key)) return;
      found[kind].add(key); counts[kind]++;
      if (collisions[kind].length < witnessLimit) collisions[kind].push(typeof witness === 'function' ? witness() : witness);
    };
    const nodes = [...(input.nodes || [])].sort((a, b) => compare(String(a.id), String(b.id)));
    const edges = [...(input.edges || [])].sort((a, b) => compare(String(a.id), String(b.id)));
    const invalidPaths = [], invalidGeometry = [], nodeBoxes = new Map(), labels = [], paths = [];
    const boxIndex = spatialIndex(160, spend), segmentIndex = spatialIndex(160, spend);
    const addBox = (box, id, kind, owner) => {
      if (box === undefined || box === null) return true;
      if (!validBox(box)) { invalidGeometry.push({ id, kind }); return true; }
      const item = { bounds: { minX: box.minX, minY: box.minY, maxX: box.maxX, maxY: box.maxY }, id, kind, owner };
      if (kind === 'body') nodeBoxes.set(id, item.bounds); else labels.push(item);
      return boxIndex.add(item);
    };
    for (const node of nodes) {
      if (!spend() || !addBox(node.body, String(node.id), 'body', String(node.id)) || !addBox(node.label, 'nodeLabel:' + node.id, 'nodeLabel', String(node.id))) break;
    }
    if (complete) for (const edge of edges) {
      if (!spend() || !addBox(edge.label, 'edgeLabel:' + edge.id, 'edgeLabel', String(edge.id))) break;
    }
    if (complete) for (const label of labels) {
      if (!boxIndex.query(label.bounds, item => {
        if (label === item || !boxesOverlap(label.bounds, item.bounds)) return true;
        if (item.kind === 'body') {
          if (label.kind !== 'nodeLabel' || label.owner !== item.owner) record('labelNode', pairKey(label.id, item.id), { label: label.id, node: item.id });
        } else if (compare(label.id, item.id) < 0) record('labelOverlap', pairKey(label.id, item.id), { a: label.id, b: item.id });
        return true;
      })) break;
    }
    if (complete) for (const edge of edges) {
      if (!spend()) break;
      const sampled = sample(edge.path, { steps: input.steps, maxSegments: Math.max(1, Math.min(4096, maxChecks - checks)), maxWork: Math.max(1, maxChecks - checks) });
      if (!spend(sampled.work)) break;
      processedEdges++;
      if (!sampled.valid) { invalidPaths.push(String(edge.id)); continue; }
      // Reuse the actual rendered-path sample above. This excludes M jumps and
      // label gaps, and is partial whenever coverage.complete is false.
      totalLength += sampled.length;
      if (!sampled.truncated) edgeLengths[String(edge.id)] = sampled.length;
      if (sampled.truncated) complete = false;
      const path = { id: String(edge.id), source: String(edge.source), target: String(edge.target), sampled };
      paths.push(path); sampledSegments += sampled.segments.length;
      for (const segment of sampled.segments) {
        if (!spend()) break;
        const segmentItem = { ...segment, path };
        if (!boxIndex.query(segment.bounds, item => {
          if (item.kind === 'body' && (item.owner === path.source || item.owner === path.target) || item.kind === 'edgeLabel' && item.owner === path.id) return true;
          if (!segmentBox(segment.a, segment.b, item.bounds)) return true;
          const kind = item.kind === 'body' ? 'edgeNode' : item.kind === 'nodeLabel' ? 'edgeNodeLabel' : 'edgeLabel';
          record(kind, pairKey(path.id, item.id), { edge: path.id, [item.kind === 'body' ? 'node' : 'label']: item.id }); return true;
        })) break;
        if (!segmentIndex.query(segment.bounds, other => {
          if (other.path.id === path.id) return true;
          const hit = relation(segment.a, segment.b, other.a, other.b);
          if (!hit) return true;
          const pair = pairKey(other.path.id, path.id);
          if (hit.kind === 'overlap') record('collinearOverlap', pair, { a: other.path.id, b: path.id });
          else {
            const shared = [path.source, path.target].filter(id => id === other.path.source || id === other.path.target);
            if (shared.some(id => nodeBoxes.has(id) && inside(hit.point, nodeBoxes.get(id)))) return true;
            const ownEnds = [path.sampled.points[0], path.sampled.points[path.sampled.points.length - 1]];
            const otherEnds = [other.path.sampled.points[0], other.path.sampled.points[other.path.sampled.points.length - 1]];
            if (shared.length && ownEnds.some(p => distance(p, hit.point) < 0.01) && otherEnds.some(p => distance(p, hit.point) < 0.01)) return true;
            const location = [Math.round(hit.point.x * 10000), Math.round(hit.point.y * 10000)];
            // Common endpoints, duplicate sampled vertices and witnesses beyond
            // the display limit need the crossing count, but never its angle.
            record('crossings', pairKey(pair, location), () => {
              const cosine = Math.max(-1, Math.min(1, (hit.r.x * hit.s.x + hit.r.y * hit.s.y)
                / Math.hypot(hit.r.x, hit.r.y) / Math.hypot(hit.s.x, hit.s.y)));
              return { a: other.path.id, b: path.id, point: hit.point, angle: Math.acos(Math.abs(cosine)) * 180 / Math.PI };
            });
          }
          return true;
        })) break;
        if (!segmentIndex.add(segmentItem)) break;
      }
      if (!complete) break;
    }
    const valid = invalidPaths.length === 0 && invalidGeometry.length === 0;
    // Counts can hide a new obstruction when an unrelated old one disappears.
    // Keep all identities from the existing deduplication sets, independently
    // of the bounded diagnostic witnesses used for human-readable reports.
    const collisionKeys = Object.fromEntries(Object.entries(found).map(([kind, keys]) => [kind, [...keys]]));
    // A crossing belongs to a pair of relations; its intersection may move
    // during a legitimate reroute. Preserve multiplicity per pair without
    // treating a changed screen location as a different obstruction.
    const crossingCounts = new Map();
    collisionKeys.crossings = collisionKeys.crossings.map(key => {
      const pair = JSON.parse(key)[0], index = crossingCounts.get(pair) || 0;
      crossingCounts.set(pair, index + 1);
      return JSON.stringify([pair, index]);
    });
    return { valid, validity: valid ? complete ? 'valid' : 'unknown' : 'invalid', counts, collisions, collisionKeys, invalidPaths, invalidGeometry, totalLength, edgeLengths,
      coverage: { complete, checks, maxChecks, edges: edges.length, processedEdges, sampledSegments,
        unexamined: !complete, reason: complete ? null : 'budget', pathApproximation: 'sampled-path' },
      approximation: 'sampled-path' };
  }
  const api = { sample, evaluate };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.FundsGraphRouteQuality = api;
})(typeof self !== 'undefined' ? self : globalThis);
