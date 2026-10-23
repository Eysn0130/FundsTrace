/* Deterministic display-only layout kernel. No account amounts are converted or summed here. */
(function (root) {
  'use strict';
  function hash(id) { let h = 2166136261; for (const c of String(id)) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return h >>> 0; }
  function seed(id, width, height) {
    const h = hash(id), angle = (h % 6283) / 1000, radius = 0.12 + ((h >>> 13) % 1000) / 4000;
    return { x: width / 2 + Math.cos(angle) * width * radius, y: height / 2 + Math.sin(angle) * height * radius };
  }
  const compareId = (a, b) => a < b ? -1 : a > b ? 1 : 0;

  // Screen-space camera geometry. Corner overlays block only their own area,
  // rather than reserving their full height across the complete canvas.
  function fitViewport(input = {}) {
    const bounds = input.bounds, width = input.width, height = input.height;
    if (!bounds || ![bounds.minX, bounds.minY, bounds.maxX, bounds.maxY, width, height].every(Number.isFinite) ||
        bounds.maxX < bounds.minX || bounds.maxY < bounds.minY || width <= 0 || height <= 0) return null;
    const minZoom = Number.isFinite(input.minZoom) && input.minZoom > 0 ? input.minZoom : 0.02;
    const maxZoom = Math.max(minZoom, Number.isFinite(input.maxZoom) && input.maxZoom > 0 ? input.maxZoom : 1.6);
    const padding = Math.max(0, Number.isFinite(input.padding) ? input.padding : 24);
    const outer = { minX: padding, minY: padding, maxX: width - padding, maxY: height - padding };
    if (outer.maxX <= outer.minX || outer.maxY <= outer.minY) return null;
    const obstacles = [];
    for (const obstacle of input.obstacles || []) {
      const b = obstacle?.bounds;
      if (!b || ![b.minX, b.minY, b.maxX, b.maxY].every(Number.isFinite) || b.maxX <= b.minX || b.maxY <= b.minY) continue;
      const box = { minX: Math.max(outer.minX, b.minX - padding), minY: Math.max(outer.minY, b.minY - padding),
        maxX: Math.min(outer.maxX, b.maxX + padding), maxY: Math.min(outer.maxY, b.maxY + padding) };
      if (box.minX < box.maxX && box.minY < box.maxY) obstacles.push(box);
    }
    const xs = [...new Set([outer.minX, outer.maxX, ...obstacles.flatMap(b => [b.minX, b.maxX])])].sort((a, b) => a - b);
    const worldWidth = Math.max(1, bounds.maxX - bounds.minX), worldHeight = Math.max(1, bounds.maxY - bounds.minY);
    let best = null;
    const consider = viewport => {
      const w = viewport.maxX - viewport.minX, h = viewport.maxY - viewport.minY;
      if (w <= 0 || h <= 0) return;
      const availableZoom = Math.min(maxZoom, w / worldWidth, h / worldHeight);
      const center = { x: (viewport.minX + viewport.maxX) / 2, y: (viewport.minY + viewport.maxY) / 2 };
      const distance = (center.x - width / 2) ** 2 + (center.y - height / 2) ** 2, area = w * h;
      if (!best || availableZoom > best.availableZoom + 1e-9 || Math.abs(availableZoom - best.availableZoom) <= 1e-9 &&
          (distance < best.distance - 1e-9 || Math.abs(distance - best.distance) <= 1e-9 && area > best.area)) {
        best = { availableZoom, distance, area, center, viewport };
      }
    };
    // Every maximal empty rectangle has vertical sides on the canvas or an
    // obstacle. For each such strip, merged blocked Y intervals expose all
    // maximal free rectangles; overlay counts stay small in the host UI.
    for (let left = 0; left < xs.length - 1; left++) for (let right = left + 1; right < xs.length; right++) {
      const minX = xs[left], maxX = xs[right];
      const blocked = obstacles.filter(b => b.minX < maxX && b.maxX > minX).sort((a, b) => a.minY - b.minY || a.maxY - b.maxY);
      let y = outer.minY;
      for (const box of blocked) {
        if (box.minY > y) consider({ minX, minY: y, maxX, maxY: box.minY });
        y = Math.max(y, box.maxY);
      }
      if (y < outer.maxY) consider({ minX, minY: y, maxX, maxY: outer.maxY });
    }
    if (!best) return null;
    return { zoom: Math.max(minZoom, best.availableZoom), center: best.center, viewport: best.viewport,
      worldCenter: { x: bounds.minX / 2 + bounds.maxX / 2, y: bounds.minY / 2 + bounds.maxY / 2 },
      fits: best.availableZoom >= minZoom };
  }

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
      if (Number.isFinite(node.width) && node.width > 0) diameter = Math.max(diameter, node.width + 2 * (Number.isFinite(node.offsetX) ? Math.abs(node.offsetX) : 0));
      if (Number.isFinite(node.height) && node.height > 0) diameter = Math.max(diameter, node.height + 2 * (Number.isFinite(node.offsetY) ? Math.abs(node.offsetY) : 0));
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
  function multilineRestLength(edge, a, b) {
    const singleLineHeight = Number.isFinite(edge.fontSize) && edge.fontSize > 0 ? edge.fontSize + 6 : 19;
    // Measured extra lines need a resting distance as well as width. Ordinary
    // single lines retain the previous force calculations exactly; this is a
    // conservative spring preference, not a guarantee for every routed label.
    return Number.isFinite(edge.labelHeight) && edge.labelHeight > singleLineHeight + 1e-7
      ? edge.labelHeight + a.height / 2 + b.height / 2 + 24 : 0;
  }
  function smallForceSimulation(input) {
    const width = Math.max(180, input.width || 900), height = Math.max(180, input.height || 600);
    const source = input.nodes || [], order = source.map(node => node.id), dimensions = [];
    const nodes = [...source].sort((a, b) => compareId(a.id, b.id)).map(node => {
      const size = Number.isFinite(node.size) && node.size > 0 ? node.size : 48;
      dimensions.push({ width: Number.isFinite(node.width) && node.width > 0 ? Math.max(size, node.width) : size,
        height: Number.isFinite(node.height) && node.height > 0 ? Math.max(size, node.height) : size,
        offsetX: Number.isFinite(node.offsetX) ? node.offsetX : 0, offsetY: Number.isFinite(node.offsetY) ? node.offsetY : 0 });
      return { id: node.id, ...seed(node.id, width, height),
        ...(Number.isFinite(node.x) && Number.isFinite(node.y) ? { x: node.x, y: node.y } : {}) };
    });
    const index = new Map(nodes.map((node, i) => [node.id, i]));
    const baseLength = Math.max(100, Math.sqrt(width * height / Math.max(1, nodes.length)) * 0.62);
    const edges = (input.edges || []).map(edge => {
      const a = index.get(edge.source), b = index.get(edge.target);
      if (a === undefined || b === undefined || a === b) return null;
      const labelWidth = Number.isFinite(edge.labelWidth) && edge.labelWidth > 0 ? edge.labelWidth : 0;
      const orderLength = Math.max(baseLength, labelWidth + dimensions[a].width / 2 + dimensions[b].width / 2 + 24);
      return { a, b, orderLength, length: Math.max(orderLength, multilineRestLength(edge, dimensions[a], dimensions[b])) };
    }).filter(Boolean).sort((a, b) => a.a - b.a || a.b - b.b || a.orderLength - b.orderLength || a.length - b.length);
    const gap = 18, iterations = 160;
    let iteration = 0, interactions = 0, separated = false;
    // Finish with deterministic rectangular collision removal. This consumes
    // measured node + text extents and can extend world coordinates. It never
    // compresses labels back into the initial viewport.
    function separate() {
      const overlaps = (point, dim, other, otherDim) =>
        Math.abs(point.x + dim.offsetX - other.x - otherDim.offsetX) < (dim.width + otherDim.width) / 2 + gap - 1e-7 &&
        Math.abs(point.y + dim.offsetY - other.y - otherDim.offsetY) < (dim.height + otherDim.height) / 2 + gap - 1e-7;
      for (let i = 0; i < nodes.length; i++) {
        const point = nodes[i], dim = dimensions[i], origin = { x: point.x, y: point.y };
        if (!nodes.slice(0, i).some((other, j) => overlaps(point, dim, other, dimensions[j]))) continue;
        const xs = [origin.x], ys = [origin.y];
        for (let j = 0; j < i; j++) {
          const x = nodes[j].x + dimensions[j].offsetX - dim.offsetX, y = nodes[j].y + dimensions[j].offsetY - dim.offsetY;
          xs.push(x - (dim.width + dimensions[j].width) / 2 - gap, x + (dim.width + dimensions[j].width) / 2 + gap);
          ys.push(y - (dim.height + dimensions[j].height) / 2 - gap, y + (dim.height + dimensions[j].height) / 2 + gap);
        }
        let best = null, bestDistance = Infinity;
        // At least the outermost boundary on either axis is free. Testing
        // one-axis moves preserves the force layout's neighborhoods better
        // than replacing it with a grid or tree.
        for (const candidate of [...xs.map(x => ({ x, y: origin.y })), ...ys.map(y => ({ x: origin.x, y }))]) {
          const distance = (candidate.x - origin.x) ** 2 + (candidate.y - origin.y) ** 2;
          if (distance >= bestDistance || nodes.slice(0, i).some((other, j) => overlaps(candidate, dim, other, dimensions[j]))) continue;
          best = candidate; bestDistance = distance;
        }
        if (best) { point.x = best.x; point.y = best.y; }
      }
      separated = true;
    }
    function step() {
      if (iteration >= iterations || nodes.length < 2) { if (!separated) separate(); return false; }
      const n = nodes.length, dx = new Float64Array(n), dy = new Float64Array(n);
      for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
        let x = nodes[i].x + dimensions[i].offsetX - nodes[j].x - dimensions[j].offsetX;
        let y = nodes[i].y + dimensions[i].offsetY - nodes[j].y - dimensions[j].offsetY;
        if (Math.hypot(x, y) < 0.1) {
          const angle = (hash(`${nodes[i].id}|${nodes[j].id}`) % 6283) / 1000;
          x = Math.cos(angle); y = Math.sin(angle);
        }
        const distance = Math.max(1, Math.hypot(x, y)), ux = x / distance, uy = y / distance;
        const extent = Math.abs(ux) * (dimensions[i].width + dimensions[j].width) / 2 +
          Math.abs(uy) * (dimensions[i].height + dimensions[j].height) / 2 + gap;
        // Distant charges from every branch must not stretch each real edge
        // far beyond its measured resting length. Near-field box separation
        // remains strong, while long-range charge only untangles neighbors.
        const repulsion = baseLength * baseLength / distance * 0.08 + Math.max(0, extent - distance) * 2;
        const fx = ux * repulsion, fy = uy * repulsion;
        dx[i] += fx; dy[i] += fy; dx[j] -= fx; dy[j] -= fy; interactions++;
      }
      for (const edge of edges) {
        const a = nodes[edge.a], b = nodes[edge.b], x = a.x - b.x, y = a.y - b.y;
        const distance = Math.max(1, Math.hypot(x, y)), force = (distance - edge.length) * 2.4;
        const fx = x / distance * force, fy = y / distance * force;
        dx[edge.a] -= fx; dy[edge.a] -= fy; dx[edge.b] += fx; dy[edge.b] += fy;
      }
      const temperature = (1 - iteration / iterations) * Math.min(width, height) * 0.06 + 0.5;
      // Center attraction limits the whole tree's spread without hard walls.
      // A two-account relationship already has an explicit resting length;
      // compressing it toward the viewport center would waste its label gap.
      const gravity = n > 2 ? 0.16 : 0;
      for (let i = 0; i < n; i++) {
        dx[i] += (width / 2 - nodes[i].x) * gravity; dy[i] += (height / 2 - nodes[i].y) * gravity;
        const distance = Math.hypot(dx[i], dy[i]) || 1, amount = Math.min(distance, temperature);
        nodes[i].x += dx[i] / distance * amount; nodes[i].y += dy[i] / distance * amount;
      }
      iteration++;
      if (iteration === iterations) { separate(); return false; }
      return true;
    }
    return { step, result: () => {
      const positions = new Map(nodes.map(node => [node.id, node]));
      return { nodes: order.map(id => positions.get(id)), iterations: iteration, interactions };
    } };
  }
  function dimensions(node) {
    const size = Number.isFinite(node.size) && node.size > 0 ? node.size : 48;
    return { width: Number.isFinite(node.width) && node.width > 0 ? Math.max(size, node.width) : size,
      height: Number.isFinite(node.height) && node.height > 0 ? Math.max(size, node.height) : size,
      offsetX: Number.isFinite(node.offsetX) ? node.offsetX : 0, offsetY: Number.isFinite(node.offsetY) ? node.offsetY : 0 };
  }
  function nodeBounds(point, dim, gap = 0) {
    return { minX: point.x + dim.offsetX - dim.width / 2 - gap, maxX: point.x + dim.offsetX + dim.width / 2 + gap,
      minY: point.y + dim.offsetY - dim.height / 2 - gap, maxY: point.y + dim.offsetY + dim.height / 2 + gap };
  }
  function intersects(a, b) { return a.minX < b.maxX - 1e-7 && b.minX < a.maxX - 1e-7 && a.minY < b.maxY - 1e-7 && b.minY < a.maxY - 1e-7; }
  function unionBounds(boxes) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const box of boxes) { minX = Math.min(minX, box.minX); minY = Math.min(minY, box.minY); maxX = Math.max(maxX, box.maxX); maxY = Math.max(maxY, box.maxY); }
    return Number.isFinite(minX) ? { minX, minY, maxX, maxY } : { minX: 0, minY: 0, maxX: 0, maxY: 0 };
  }
  function graphModel(input) {
    const source = input.nodes || [], index = new Map(), dim = source.map(dimensions);
    source.forEach((node, i) => { if (typeof node.id !== 'string' || index.has(node.id)) throw new Error('layout requires unique string node IDs'); index.set(node.id, i); });
    const byId = (a, b) => compareId(source[a].id, source[b].id), order = source.map((_, i) => i).sort(byId);
    const adjacent = source.map(() => new Set()), outgoing = source.map(() => new Set()), incoming = source.map(() => new Set()), edges = [], edgeOut = source.map(() => []), edgeIncident = source.map(() => []);
    for (const edge of input.edges || []) {
      const a = index.get(edge.source), b = index.get(edge.target);
      if (a === undefined || b === undefined) continue;
      const geometry = { a, b, labelWidth: Number.isFinite(edge.labelWidth) && edge.labelWidth > 0 ? edge.labelWidth : 0,
        labelHeight: Number.isFinite(edge.labelHeight) && edge.labelHeight > 0 ? edge.labelHeight : 13,
        singleLineHeight: Number.isFinite(edge.fontSize) && edge.fontSize > 0 ? edge.fontSize + 6 : 19 };
      edges.push(geometry); edgeOut[a].push(geometry); edgeIncident[a].push(geometry); if (a !== b) edgeIncident[b].push(geometry);
      if (a !== b) { adjacent[a].add(b); adjacent[b].add(a); outgoing[a].add(b); incoming[b].add(a); }
    }
    const adj = adjacent.map(values => [...values].sort(byId)), out = outgoing.map(values => [...values].sort(byId)), inc = incoming.map(values => [...values].sort(byId));
    const seen = new Set(), components = [];
    for (const start of order) {
      if (seen.has(start)) continue;
      const queue = [start]; seen.add(start);
      for (let h = 0; h < queue.length; h++) for (const next of adj[queue[h]]) if (!seen.has(next)) { seen.add(next); queue.push(next); }
      components.push(queue.sort(byId));
    }
    components.sort((a, b) => b.length - a.length || byId(a[0], b[0]));
    return { source, index, dim, byId, order, adj, out, inc, edges, edgeOut, edgeIncident, components };
  }
  function* positionedPack(input, graph, parts, method) {
    const gap = 72, width = Number.isFinite(input.width) && input.width > 0 ? input.width : 900;
    const height = Number.isFinite(input.height) && input.height > 0 ? input.height : 600;
    const aspect = Math.min(2, Math.max(0.55, width / height));
    let area = 0, largest = 0, totalWidth = 0, work = 0;
    for (const part of parts) {
      part.box = unionBounds([...part.points].map(([i, p]) => nodeBounds(p, graph.dim[i])));
      part.width = part.box.maxX - part.box.minX; part.height = part.box.maxY - part.box.minY;
      area += (part.width + gap) * (part.height + gap); largest = Math.max(largest, part.width); totalWidth += part.width; yield;
    }
    totalWidth += Math.max(0, parts.length - 1) * gap;
    const sortedWidths = parts.map(part => part.width).sort((a, b) => b - a), targets = new Set();
    const addTarget = value => { if (Number.isFinite(value)) targets.add(Math.max(1, largest, value)); };
    addTarget(largest); addTarget(Math.sqrt(area * aspect)); addTarget(Math.sqrt(area * aspect) * 0.8); addTarget(Math.sqrt(area * aspect) * 1.2); addTarget(totalWidth);
    // Width thresholds are discrete: a continuous square-root estimate can
    // narrowly miss two complete regions and accidentally create a tall column.
    // Sample individual widths and cumulative 2/3/... column thresholds, with
    // at most 16 sequential and logarithmically many large-column candidates.
    for (let sample = 0; sample < Math.min(16, sortedWidths.length); sample++) addTarget(sortedWidths[Math.floor(sample * sortedWidths.length / Math.min(16, sortedWidths.length))]);
    let cumulative = 0;
    for (let i = 0; i < sortedWidths.length; i++) {
      cumulative += sortedWidths[i] + (i ? gap : 0); const count = i + 1;
      if (count <= 16 || (count & (count - 1)) === 0 || count === sortedWidths.length) addTarget(cumulative);
      if (++work % 256 === 0) yield;
    }
    function* arrange(target) {
      let x = 0, y = 0, rowHeight = 0, maxX = 0, rows = parts.length ? 1 : 0;
      const placements = [];
      for (const part of parts) {
        if (x > 0 && x + part.width > target + 1e-7) { x = 0; y += rowHeight + gap; rowHeight = 0; rows++; }
        placements.push({ x, y }); maxX = Math.max(maxX, x + part.width);
        x += part.width + gap; rowHeight = Math.max(rowHeight, part.height);
        if (++work % 256 === 0) yield;
      }
      const maxY = y + rowHeight, bounds = { minX: 0, minY: 0, maxX, maxY };
      const fit = fitViewport({ bounds, width, height, obstacles: input.chromeObstacles || [], padding: 24, maxZoom: 1.6 });
      // Use the actual available ratio even below the minimum interactive zoom;
      // clamping every huge candidate to minZoom would hide packing differences.
      const zoom = fit ? Math.min(1.6, (fit.viewport.maxX - fit.viewport.minX) / Math.max(1, maxX), (fit.viewport.maxY - fit.viewport.minY) / Math.max(1, maxY)) :
        Math.min(1.6, Math.max(1, width - 48) / Math.max(1, maxX), Math.max(1, height - 48) / Math.max(1, maxY));
      return { placements, zoom, area: maxX * maxY, rows, bounds };
    }
    let best = null;
    for (const target of [...targets].sort((a, b) => a - b)) {
      const candidate = yield* arrange(target);
      if (!best || candidate.zoom > best.zoom + 1e-9 || Math.abs(candidate.zoom - best.zoom) <= 1e-9 &&
          (candidate.area < best.area - 1e-7 || Math.abs(candidate.area - best.area) <= 1e-7 && candidate.rows < best.rows)) best = candidate;
      yield;
    }
    const points = new Map(), regions = [];
    for (let index = 0; index < parts.length; index++) {
      const part = parts[index], { x, y } = best.placements[index];
      for (const [i, p] of part.points) { points.set(i, { x: x + p.x - part.box.minX, y: y + p.y - part.box.minY }); }
      regions.push({ method: part.method, nodeIds: [...part.points.keys()].map(i => graph.source[i].id),
        bounds: { minX: x, minY: y, maxX: x + part.width, maxY: y + part.height }, ...(part.details || {}) }); yield;
    }
    const bounds = unionBounds([...points].map(([i, p]) => nodeBounds(p, graph.dim[i])));
    const dx = Math.max(24, (width - (bounds.maxX - bounds.minX)) / 2) - bounds.minX;
    const dy = Math.max(24, (height - (bounds.maxY - bounds.minY)) / 2) - bounds.minY;
    const nodes = graph.source.map((node, i) => ({ id: node.id, x: points.get(i).x + dx, y: points.get(i).y + dy }));
    for (const region of regions) for (const key of ['minX', 'maxX', 'minY', 'maxY']) region.bounds[key] += key.endsWith('X') ? dx : dy;
    return { nodes, interactions: graph.edges.length, layout: { method, componentCount: graph.components.length, regions,
      packing: { method: 'candidate-shelves', candidateCount: targets.size, rows: best.rows, fitZoom: best.zoom } } };
  }
  // Iterative Kosaraju avoids recursive call-stack limits. SCCs are geometric
  // blocks only: original account membership and directed edges are untouched.
  const maximum = (values, fallback = 0) => values.reduce((best, value) => Math.max(best, value), fallback);
  const memberEdges = (graph, members) => members.flatMap(i => graph.edgeOut[i]);
  function simpleCycleOrder(graph, members) {
    const allowed = new Set(members), next = new Map();
    for (const i of members) {
      const out = graph.out[i].filter(j => allowed.has(j)), inc = graph.inc[i].filter(j => allowed.has(j));
      if (out.length !== 1 || inc.length !== 1) return null;
      next.set(i, out[0]);
    }
    const order = [], seen = new Set(); let current = members[0];
    while (!seen.has(current)) { order.push(current); seen.add(current); current = next.get(current); }
    return current === members[0] && order.length === members.length ? order : null;
  }
  function* reserveInternalLabels(graph, members, points) {
    const allowed = new Set(members); let scale = 1, work = 0;
    for (const i of members) for (const edge of graph.edgeOut[i]) {
      // Ordinary single-line SCC geometry is retained. Extra measured lines
      // need room at their own midpoint, not a global gap for every component.
      if (edge.a !== edge.b && allowed.has(edge.b) && edge.labelWidth > 0 && edge.labelHeight > edge.singleLineHeight + 1e-7) {
        const a = points.get(edge.a), b = points.get(edge.b), da = graph.dim[edge.a], db = graph.dim[edge.b];
        const dx = Math.abs(a.x + da.offsetX - b.x - db.offsetX), dy = Math.abs(a.y + da.offsetY - b.y - db.offsetY);
        // Scaling complete-box centers leaves their body offsets unchanged.
        // The triangle bound accounts for the midpoint of the two bodies
        // differing from the midpoint of their below-body text boxes.
        const requiredX = dx > 1e-9 ? (edge.labelWidth + Math.max(da.width, db.width) + 16 + Math.abs(da.offsetX + db.offsetX)) / dx : Infinity;
        const requiredY = dy > 1e-9 ? (edge.labelHeight + Math.max(da.height, db.height) + 16 + Math.abs(da.offsetY + db.offsetY)) / dy : Infinity;
        const required = Math.min(requiredX, requiredY);
        if (Number.isFinite(required)) scale = Math.max(scale, required);
      }
      if (++work % 128 === 0) yield;
    }
    if (scale > 1) for (const [i, point] of points) {
      const dim = graph.dim[i]; points.set(i, { x: (point.x + dim.offsetX) * scale - dim.offsetX, y: (point.y + dim.offsetY) * scale - dim.offsetY });
      if (++work % 128 === 0) yield;
    }
  }
  function* layeredPart(graph, members) {
    const memberSet = new Set(members), visited = new Set(), finish = [];
    let work = 0;
    for (const start of members) {
      if (visited.has(start)) continue;
      visited.add(start); const stack = [{ i: start, next: 0 }];
      while (stack.length) {
        const frame = stack[stack.length - 1], neighbors = graph.out[frame.i];
        if (frame.next < neighbors.length) {
          const next = neighbors[frame.next++];
          if (memberSet.has(next) && !visited.has(next)) { visited.add(next); stack.push({ i: next, next: 0 }); }
        } else { finish.push(frame.i); stack.pop(); }
        if (++work % 128 === 0) yield;
      }
    }
    const blockOf = new Map(), blocks = [];
    for (let h = finish.length - 1; h >= 0; h--) {
      const start = finish[h]; if (blockOf.has(start)) continue;
      const id = blocks.length, queue = [start]; blockOf.set(start, id);
      for (let q = 0; q < queue.length; q++) {
        for (const next of graph.inc[queue[q]]) if (memberSet.has(next) && !blockOf.has(next)) { blockOf.set(next, id); queue.push(next); }
        if (++work % 128 === 0) yield;
      }
      queue.sort(graph.byId);
      const cycle = queue.length > 1 ? simpleCycleOrder(graph, queue) : null, placementOrder = cycle || queue;
      const points = new Map();
      if (queue.length === 1) points.set(queue[0], { x: 0, y: 0 });
      else {
        const extent = maximum(queue.map(i => Math.hypot(graph.dim[i].width, graph.dim[i].height) + 32));
        if (queue.length <= 24) {
          const radius = Math.max(extent, extent / (2 * Math.sin(Math.PI / queue.length)));
          placementOrder.forEach((i, slot) => { const angle = 2 * Math.PI * slot / queue.length - Math.PI / 2; points.set(i, { x: Math.cos(angle) * radius - graph.dim[i].offsetX, y: Math.sin(angle) * radius - graph.dim[i].offsetY }); });
        } else {
          const columns = Math.ceil(Math.sqrt(queue.length));
          if (cycle) {
            // Keep the compact topology-ordered grid, reserving an empty left
            // return lane. Two upper corners and the lower return corner make
            // the cycle close outside the occupied rows instead of diagonally
            // through their bodies and fixed below-node labels.
            const innerCount = queue.length - 3, rows = Math.ceil(innerCount / columns), startRight = rows % 2 === 1;
            placementOrder.forEach((i, slot) => {
              let x, y;
              if (slot === 0) { x = 0; y = -1; }
              else if (slot === 1) { x = startRight ? columns : 1; y = -1; }
              else if (slot === queue.length - 1) { x = 0; y = rows - 1; }
              else {
                const inner = slot - 2, row = Math.floor(inner / columns), col = inner % columns;
                x = startRight !== (row % 2 === 1) ? columns - col : col + 1; y = row;
              }
              points.set(i, { x: x * extent - graph.dim[i].offsetX, y: y * extent - graph.dim[i].offsetY });
            });
          } else placementOrder.forEach((i, slot) => points.set(i, { x: slot % columns * extent - graph.dim[i].offsetX, y: Math.floor(slot / columns) * extent - graph.dim[i].offsetY }));
        }
        yield* reserveInternalLabels(graph, queue, points);
      }
      const box = unionBounds([...points].map(([i, p]) => nodeBounds(p, graph.dim[i])));
      blocks.push({ members: queue, points, box, width: box.maxX - box.minX, height: box.maxY - box.minY, simpleCycle: !!cycle, out: new Set(), inc: new Set(), rank: 0, labelGap: 84 });
    }
    for (const edge of memberEdges(graph, members)) {
      const a = blockOf.get(edge.a), b = blockOf.get(edge.b); if (a === undefined || b === undefined || a === b) continue;
      blocks[a].out.add(b); blocks[b].inc.add(a); blocks[a].labelGap = Math.max(blocks[a].labelGap, edge.labelWidth + 32);
      if (++work % 128 === 0) yield;
    }
    const blockOrder = (a, b) => graph.byId(blocks[a].members[0], blocks[b].members[0]);
    const degree = blocks.map(block => block.inc.size), queue = blocks.map((_, i) => i).filter(i => !degree[i]).sort(blockOrder);
    for (let q = 0; q < queue.length; q++) {
      const a = queue[q];
      for (const b of [...blocks[a].out].sort(blockOrder)) { blocks[b].rank = Math.max(blocks[b].rank, blocks[a].rank + 1); if (--degree[b] === 0) queue.push(b); }
      if (++work % 128 === 0) yield;
    }
    const layers = [];
    blocks.forEach((block, i) => { if (!layers[block.rank]) layers[block.rank] = []; layers[block.rank].push(i); });
    for (const layer of layers) layer.sort(blockOrder);
    const rankWidth = layers.map(layer => maximum(layer.map(i => blocks[i].width))), rankX = [0];
    for (let rank = 1; rank < layers.length; rank++) rankX[rank] = rankX[rank - 1] + rankWidth[rank - 1] / 2 + maximum(layers[rank - 1].map(i => blocks[i].labelGap)) + rankWidth[rank] / 2;
    const layerHeight = layers.map(layer => layer.reduce((sum, i) => sum + blocks[i].height, 0) + Math.max(0, layer.length - 1) * 44), maxHeight = maximum(layerHeight);
    const blockEdges = blocks.flatMap((block, a) => [...block.out].map(b => ({ a, b })));
    const crossingMetric = blockEdges.length <= 600 ? 'all-straight-block-edges' : 'adjacent-rank-inversions';
    const crossingCount = () => {
      if (crossingMetric === 'all-straight-block-edges') {
        const centers = new Map();
        layers.forEach((layer, rank) => { let y = (maxHeight - layerHeight[rank]) / 2; for (const i of layer) { centers.set(i, { x: rankX[rank], y: y + blocks[i].height / 2 }); y += blocks[i].height + 44; } });
        let count = 0;
        for (let a = 0; a < blockEdges.length; a++) for (let b = a + 1; b < blockEdges.length; b++) {
          const first = blockEdges[a], second = blockEdges[b];
          if (first.a !== second.a && first.a !== second.b && first.b !== second.a && first.b !== second.b && segmentCrosses(centers.get(first.a), centers.get(first.b), centers.get(second.a), centers.get(second.b))) count++;
        }
        return count;
      }
      let count = 0;
      for (let rank = 0; rank < layers.length - 1; rank++) {
        const targetOrder = new Map(layers[rank + 1].map((id, p) => [id, p + 1])), tree = new Int32Array(layers[rank + 1].length + 2); let total = 0;
        const prefix = p => { let sum = 0; for (; p > 0; p -= p & -p) sum += tree[p]; return sum; };
        for (const i of layers[rank]) {
          const targets = [...blocks[i].out].filter(j => targetOrder.has(j)).map(j => targetOrder.get(j));
          for (const p of targets) count += total - prefix(p);
          for (let p of targets) { total++; for (; p < tree.length; p += p & -p) tree[p]++; }
        }
      }
      return count;
    };
    // Alternating barycenter sweeps keep the best measured ordering, including
    // the initial order. Large graphs use a bounded adjacent-rank metric;
    // crossing elimination is not guaranteed for arbitrary directed graphs.
    const positions = new Float64Array(blocks.length), crossingBefore = crossingCount();
    let crossingAfter = crossingBefore, bestLayers = layers.map(layer => layer.slice());
    const refresh = () => layers.forEach(layer => layer.forEach((i, p) => { positions[i] = (p + 0.5) / layer.length; }));
    for (let sweep = 0; sweep < 8; sweep++) {
      refresh(); const down = sweep % 2 === 0;
      for (let rank = down ? 1 : layers.length - 2; down ? rank < layers.length : rank >= 0; rank += down ? 1 : -1) {
        const layer = layers[rank], scores = new Map();
        for (const i of layer) { const neighbors = [...(down ? blocks[i].inc : blocks[i].out)]; scores.set(i, neighbors.length ? neighbors.reduce((sum, j) => sum + positions[j], 0) / neighbors.length : positions[i]); }
        const previous = new Map(layer.map((i, p) => [i, p]));
        layer.sort((a, b) => scores.get(a) - scores.get(b) || previous.get(a) - previous.get(b) || blockOrder(a, b));
        layer.forEach((i, p) => { positions[i] = (p + 0.5) / layer.length; }); yield;
      }
      const crossings = crossingCount();
      if (crossings < crossingAfter) { crossingAfter = crossings; bestLayers = layers.map(layer => layer.slice()); }
      yield;
    }
    for (let rank = 0; rank < layers.length; rank++) layers[rank] = bestLayers[rank];
    const points = new Map();
    for (let rank = 0; rank < layers.length; rank++) {
      let y = (maxHeight - layerHeight[rank]) / 2;
      for (const id of layers[rank]) {
        const block = blocks[id], cx = (block.box.minX + block.box.maxX) / 2;
        for (const [i, p] of block.points) points.set(i, { x: rankX[rank] + p.x - cx, y: y + p.y - block.box.minY });
        y += block.height + 44; yield;
      }
    }
    return { points, method: 'flow-layered', details: { rankCount: layers.length, sccCount: blocks.length, cyclicBlockCount: blocks.filter(block => block.members.length > 1).length,
      simpleCycleBlockCount: blocks.filter(block => block.simpleCycle).length, crossingMetric, crossingBefore, crossingAfter } };
  }
  // Trees whose one-step leaf removal leaves a path have an unambiguous clean
  // backbone. Direction chooses its left-to-right orientation, never edge data.
  function backbonePart(graph, members) {
    if (!members.length) return null;
    const edgeCount = members.reduce((sum, i) => sum + graph.adj[i].length, 0) / 2;
    if (edgeCount !== members.length - 1) return null;
    let core = members.filter(i => graph.adj[i].length > 1);
    if (members.every(i => graph.adj[i].length <= 2)) core = members.slice();
    if (!core.length) core = [members[0]];
    const coreSet = new Set(core), coreAdj = i => graph.adj[i].filter(j => coreSet.has(j));
    if (core.some(i => coreAdj(i).length > 2)) return null;
    const endpoints = core.filter(i => coreAdj(i).length <= 1).sort(graph.byId);
    if (!endpoints.length) return null;
    const path = []; let previous = -1, current = endpoints[0];
    while (current !== undefined) { path.push(current); const next = coreAdj(current).find(i => i !== previous); previous = current; current = next; }
    if (path.length !== core.length) return null;
    let score = 0;
    for (let p = 1; p < path.length; p++) { if (graph.out[path[p - 1]].includes(path[p])) score++; if (graph.out[path[p]].includes(path[p - 1])) score--; }
    if (score < 0 || score === 0 && graph.source[path[path.length - 1]].isMain && !graph.source[path[0]].isMain) path.reverse();
    const points = new Map(), edgeGap = new Map(), edgeHeight = new Map();
    for (const edge of memberEdges(graph, members)) { const key = edge.a < edge.b ? `${edge.a}|${edge.b}` : `${edge.b}|${edge.a}`; edgeGap.set(key, Math.max(edgeGap.get(key) || 0, edge.labelWidth)); edgeHeight.set(key, Math.max(edgeHeight.get(key) || 13, edge.labelHeight)); }
    let x = 0, maxBranchWidth = 0, globalAbove = 0, globalBelow = 0;
    for (const i of members) maxBranchWidth = Math.max(maxBranchWidth, graph.dim[i].width + 2 * Math.abs(graph.dim[i].offsetX));
    for (let slot = 0; slot < path.length; slot++) {
      const i = path[slot];
      if (slot) {
        const prev = path[slot - 1], key = prev < i ? `${prev}|${i}` : `${i}|${prev}`;
        const bodyWidth = index => {
          const size = graph.source[index].size;
          return Array.isArray(size) ? Math.max(1, Number.isFinite(size[0]) ? size[0] : 32) : Number.isFinite(size) && size > 0 ? size : 32;
        };
        // The overhead amount must fit between both bodies, independently of
        // the full account text boxes below them. Taking max(widths) alone
        // loses the two endpoint radii for larger bodies and long amounts.
        x += Math.max((graph.dim[prev].width + graph.dim[i].width) / 2 + graph.dim[prev].offsetX - graph.dim[i].offsetX + 36,
          (edgeGap.get(key) || 0) + Math.max(40, (bodyWidth(prev) + bodyWidth(i)) / 2 + 8), maxBranchWidth + 36);
      }
      points.set(i, { x, y: 0 });
      const branches = graph.adj[i].filter(j => !coreSet.has(j)).sort(graph.byId);
      let above = 0, below = 0;
      const coreBox = nodeBounds(points.get(i), graph.dim[i]);
      let topBoundary = coreBox.minY, bottomBoundary = coreBox.maxY;
      for (const j of branches) {
        const incoming = graph.out[j].includes(i) && !graph.out[i].includes(j);
        const outgoing = graph.out[i].includes(j) && !graph.out[j].includes(i);
        const top = branches.length > 1 ? (above === below ? incoming || !outgoing : above < below) :
          globalAbove === globalBelow ? slot % 2 === 1 : globalAbove < globalBelow;
        const dim = graph.dim[j];
        if (top) { above++; globalAbove++; } else { below++; globalBelow++; }
        const labelHeight = edgeHeight.get(i < j ? `${i}|${j}` : `${j}|${i}`) || 13, branchGap = Math.max(58, labelHeight + 14);
        let y = top ? topBoundary - branchGap - dim.offsetY - dim.height / 2 : bottomBoundary + branchGap - dim.offsetY + dim.height / 2;
        // A line's midpoint is between body centers, while a node's complete
        // text box can be offset below its body. Multiline edge labels need
        // enough center distance to clear both endpoint boxes at that midpoint.
        const anchorDim = graph.dim[i];
        const midpointDistance = labelHeight + 4 + (top ?
          Math.max(anchorDim.height - 2 * anchorDim.offsetY, dim.height + 2 * dim.offsetY) :
          Math.max(anchorDim.height + 2 * anchorDim.offsetY, dim.height - 2 * dim.offsetY));
        y = top ? Math.min(y, -midpointDistance) : Math.max(y, midpointDistance);
        const point = { x: x - (top ? 0.23 : -0.23) * (maxBranchWidth + 92), y };
        points.set(j, point);
        const box = nodeBounds(point, dim); if (top) topBoundary = box.minY - 42; else bottomBoundary = box.maxY + 42;
      }
    }
    return { points, method: 'backbone-branches', details: { backboneIds: path.map(i => graph.source[i].id) } };
  }
  function* partitionParts(input, graph, members) {
    const allowed = new Set(members), discovery = new Map(), low = new Map(), parent = new Map(), bridges = new Set();
    const pair = (a, b) => a < b ? `${a}|${b}` : `${b}|${a}`; let clock = 0, work = 0;
    for (const start of members) {
      if (discovery.has(start)) continue;
      discovery.set(start, ++clock); low.set(start, clock); const stack = [{ i: start, next: 0 }];
      while (stack.length) {
        const frame = stack[stack.length - 1];
        if (frame.next < graph.adj[frame.i].length) {
          const next = graph.adj[frame.i][frame.next++]; if (!allowed.has(next)) continue;
          if (!discovery.has(next)) { parent.set(next, frame.i); discovery.set(next, ++clock); low.set(next, clock); stack.push({ i: next, next: 0 }); }
          else if (parent.get(frame.i) !== next) low.set(frame.i, Math.min(low.get(frame.i), discovery.get(next)));
        } else {
          stack.pop(); const p = parent.get(frame.i);
          if (p !== undefined) { low.set(p, Math.min(low.get(p), low.get(frame.i))); if (low.get(frame.i) > discovery.get(p)) bridges.add(pair(p, frame.i)); }
        }
        if (++work % 128 === 0) yield;
      }
    }
    const seen = new Set(), rawGroups = [];
    for (const start of members) {
      if (seen.has(start)) continue;
      const queue = [start]; seen.add(start);
      for (let q = 0; q < queue.length; q++) for (const next of graph.adj[queue[q]]) {
        if (allowed.has(next) && !seen.has(next) && !bridges.has(pair(queue[q], next))) { seen.add(next); queue.push(next); }
        if (++work % 128 === 0) yield;
      }
      rawGroups.push(queue.sort(graph.byId));
    }
    // Bridge-separated dense blocks are meaningful geometric regions. Attach
    // tree tendrils to their nearest block; this is not a business grouping.
    let groups = rawGroups.filter(group => group.length > 1), owner = new Map();
    groups.forEach((group, id) => group.forEach(i => owner.set(i, id)));
    if (groups.length) {
      const queue = [...owner.keys()];
      for (let q = 0; q < queue.length; q++) for (const next of graph.adj[queue[q]]) if (allowed.has(next) && !owner.has(next)) { const id = owner.get(queue[q]); owner.set(next, id); groups[id].push(next); queue.push(next); }
    } else groups = [members.slice()];
    const bounded = [];
    for (const group of groups) {
      if (group.length <= 64) { bounded.push(group.sort(graph.byId)); continue; }
      const pending = new Set(group), sorted = group.slice().sort((a, b) => graph.adj[b].length - graph.adj[a].length || graph.byId(a, b));
      const traversal = [];
      for (const start of sorted) {
        if (!pending.has(start)) continue;
        const queue = [start]; pending.delete(start);
        for (let q = 0; q < queue.length; q++) {
          traversal.push(queue[q]);
          for (const next of graph.adj[queue[q]]) {
            if (pending.has(next)) { queue.push(next); pending.delete(next); }
            if (++work % 128 === 0) yield;
          }
        }
      }
      // Traverse the complete existing bridge/community group before cutting
      // it. Removing a hub at a chunk boundary must not strand each leaf in
      // its own region. Balanced batches also avoid a one-node final sliver.
      const count = Math.ceil(traversal.length / 48); let offset = 0;
      for (let index = 0; index < count; index++) {
        const size = Math.ceil((traversal.length - offset) / (count - index));
        bounded.push(traversal.slice(offset, offset + size).sort(graph.byId)); offset += size; yield;
      }
    }
    const parts = [];
    for (const group of bounded) {
      const ids = new Set(group.map(i => graph.source[i].id));
      const subset = { width: Math.max(380, Math.sqrt(group.length) * 130), height: Math.max(320, Math.sqrt(group.length) * 100),
        nodes: group.map(i => ({ ...graph.source[i], x: undefined, y: undefined })), edges: memberEdges(graph, group).filter(edge => ids.has(graph.source[edge.b].id)).map(edge => ({ source: graph.source[edge.a].id, target: graph.source[edge.b].id,
          labelWidth: edge.labelWidth, labelHeight: edge.labelHeight, fontSize: edge.singleLineHeight - 6 })) };
      const model = smallForceSimulation(subset);
      while (model.step()) yield;
      const points = new Map(model.result().nodes.map(node => [graph.index.get(node.id), { x: node.x, y: node.y }]));
      parts.push({ points, method: 'partition-local-force', details: { partitionReason: bridges.size ? 'bridge-blocks' : 'bounded-neighborhoods' } }); yield;
    }
    return parts;
  }
  function* adaptivePositions(input) {
    const graph = graphModel(input), parts = []; yield;
    for (const members of graph.components) {
      const maxDegree = maximum(members.map(i => graph.adj[i].length));
      let part = input.kind === 'auto' && members.length <= 120 && maxDegree <= 12 ? backbonePart(graph, members) : null;
      const longBackbone = part && part.details.backboneIds.length > 12 && members.length > 24;
      if (longBackbone) part = null;
      if (part) parts.push(part);
      else {
        const edgeCount = members.reduce((sum, i) => sum + graph.adj[i].length, 0) / 2;
        if (input.kind === 'partition' || input.kind === 'auto' && (longBackbone || edgeCount > members.length * 1.6 || members.length > 48 || maxDegree > 12)) parts.push(...yield* partitionParts(input, graph, members));
        else parts.push(yield* layeredPart(graph, members));
      }
      yield;
    }
    return yield* positionedPack(input, graph, parts, input.kind === 'dagre' ? 'flow-layered' : input.kind);
  }
  // Spatial hash stores complete label/body boxes. Long segments use overflow
  // storage instead of allocating an unbounded number of grid cells.
  function geometryIndex(cellSize = 160) {
    const cells = new Map(), overflow = [], entries = [];
    function keys(box) {
      const x0 = Math.floor(box.minX / cellSize), x1 = Math.floor(box.maxX / cellSize), y0 = Math.floor(box.minY / cellSize), y1 = Math.floor(box.maxY / cellSize);
      // Finite world coordinates can still exceed the integer precision of a
      // cell index: x++ would stop advancing. Use the bounded overflow scan.
      if (![x0, x1, y0, y1].every(Number.isSafeInteger) || (x1 - x0 + 1) * (y1 - y0 + 1) > 256) return null;
      const result = []; for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) result.push(`${x},${y}`); return result;
    }
    return { add(box, extra = {}) {
      const entry = { box, ...extra }, id = entries.push(entry) - 1, occupied = keys(box);
      if (!occupied) overflow.push(id); else for (const key of occupied) { if (!cells.has(key)) cells.set(key, []); cells.get(key).push(id); }
    }, query(box) {
      const occupied = keys(box); if (!occupied) return entries.filter(entry => intersects(box, entry.box));
      const ids = new Set(overflow); for (const key of occupied) for (const id of cells.get(key) || []) ids.add(id);
      return [...ids].map(id => entries[id]).filter(entry => intersects(box, entry.box));
    } };
  }
  function segmentHitsBox(a, b, box) {
    let lo = 0, hi = 1; const dx = b.x - a.x, dy = b.y - a.y;
    for (const [p, q] of [[-dx, a.x - box.minX], [dx, box.maxX - a.x], [-dy, a.y - box.minY], [dy, box.maxY - a.y]]) {
      if (Math.abs(p) < 1e-12) { if (q < 0) return false; continue; }
      const t = q / p; if (p < 0) lo = Math.max(lo, t); else hi = Math.min(hi, t); if (lo > hi) return false;
    }
    return true;
  }
  function segmentCrosses(a, b, c, d) {
    const cross = (p, q, r) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
    return cross(a, b, c) * cross(a, b, d) < -1e-7 && cross(c, d, a) * cross(c, d, b) < -1e-7;
  }
  function* incrementalPositions(input) {
    const graph = graphModel(input), positions = new Map(), occupied = geometryIndex(), segments = geometryIndex(), fixed = [];
    const gap = Math.max(18, Number.isFinite(input.gap) ? input.gap : 24); let work = 0, interactions = 0;
    for (const i of graph.order) if (graph.source[i].fixed === true && Number.isFinite(graph.source[i].x) && Number.isFinite(graph.source[i].y)) {
      const point = { x: graph.source[i].x, y: graph.source[i].y }; positions.set(i, point); fixed.push(i); if (!input.externalOnly) occupied.add(nodeBounds(point, graph.dim[i], gap / 2));
      if (++work % 128 === 0) yield;
    }
    if (!fixed.length) return yield* adaptivePositions({ ...input, kind: 'auto' });
    const oldBounds = unionBounds(fixed.map(i => nodeBounds(positions.get(i), graph.dim[i]))), center = { x: (oldBounds.minX + oldBounds.maxX) / 2, y: (oldBounds.minY + oldBounds.maxY) / 2 };
    let allBounds = { ...oldBounds };
    const addSegment = (a, b) => { if (!input.externalOnly && [a?.x, a?.y, b?.x, b?.y].every(Number.isFinite)) segments.add({ minX: Math.min(a.x, b.x) - 10, minY: Math.min(a.y, b.y) - 10, maxX: Math.max(a.x, b.x) + 10, maxY: Math.max(a.y, b.y) + 10 }, { a, b }); };
    for (const edge of graph.edges) { if (positions.has(edge.a) && positions.has(edge.b)) addSegment(positions.get(edge.a), positions.get(edge.b)); if (++work % 128 === 0) yield; }
    for (const obstacle of input.obstacles || []) {
      const box = obstacle?.bounds;
      if (box && [box.minX, box.minY, box.maxX, box.maxY].every(Number.isFinite)) { if (!input.externalOnly) occupied.add(box); allBounds = unionBounds([allBounds, box]); }
      const points = obstacle?.points || [];
      for (let p = 1; p < points.length; p++) { if (![points[p - 1]?.x, points[p - 1]?.y, points[p]?.x, points[p]?.y].every(Number.isFinite)) continue; addSegment(points[p - 1], points[p]); allBounds = unionBounds([allBounds, { minX: Math.min(points[p - 1].x, points[p].x), maxX: Math.max(points[p - 1].x, points[p].x), minY: Math.min(points[p - 1].y, points[p].y), maxY: Math.max(points[p - 1].y, points[p].y) }]); }
      if (++work % 128 === 0) yield;
    }
    // Existing bounds are protected as a whole once the graph has a core. This
    // conservative rule keeps new labels out of dense crossing regions; it may
    // use more world space instead of disturbing the user's old mental map.
    const protectedCore = fixed.length >= 3 ? oldBounds : null;
    const pending = new Set(graph.order.filter(i => !positions.has(i))), order = [], queued = new Set(), queue = [];
    const anchor = graph.index.get(input.anchorId);
    const starts = [...pending].filter(i => graph.adj[i].some(j => positions.has(j))).sort((a, b) => {
      const aa = anchor !== undefined && graph.adj[a].includes(anchor) ? 1 : 0, bb = anchor !== undefined && graph.adj[b].includes(anchor) ? 1 : 0;
      return bb - aa || graph.byId(a, b);
    });
    for (const start of [...starts, ...graph.order]) {
      if (!pending.has(start) || queued.has(start)) continue;
      queue.push(start); queued.add(start);
      for (let q = 0; q < queue.length; q++) { const i = queue[q]; order.push(i); for (const next of graph.adj[i]) if (pending.has(next) && !queued.has(next)) { queued.add(next); queue.push(next); } }
      queue.length = 0; yield;
    }
    if (input.externalOnly === true) {
      let area = 0, maxWidth = 0;
      for (const i of order) { const dim = graph.dim[i]; area += (dim.width + gap) * (dim.height + gap); maxWidth = Math.max(maxWidth, dim.width); }
      const aspect = Math.min(2, Math.max(0.55, (input.width || 900) / (input.height || 600)));
      const laneWidth = Math.max(maxWidth, 380, Math.sqrt(area * aspect)), left = allBounds.maxX + gap * 2;
      let x = 0, y = allBounds.minY, rowHeight = 0;
      for (const i of order) {
        const dim = graph.dim[i];
        if (x > 0 && x + dim.width > laneWidth) { x = 0; y += rowHeight + gap; rowHeight = 0; }
        positions.set(i, { x: left + x + dim.width / 2 - dim.offsetX, y: y + dim.height / 2 - dim.offsetY });
        x += dim.width + gap; rowHeight = Math.max(rowHeight, dim.height); interactions++; yield;
      }
      return { nodes: graph.source.map((node, i) => ({ id: node.id, ...positions.get(i) })), interactions,
        layout: { method: 'incremental', quality: 'external-lanes', fixedCount: fixed.length, addedCount: order.length, outsideCount: order.length, protectedBounds: oldBounds } };
    }
    let outsideCount = 0, disconnectedCount = 0;
    for (const i of order) {
      const dim = graph.dim[i], neighbors = graph.adj[i].filter(j => positions.has(j)), halfW = dim.width / 2 + Math.abs(dim.offsetX), halfH = dim.height / 2 + Math.abs(dim.offsetY);
      let anchorPoint;
      if (neighbors.length) anchorPoint = neighbors.reduce((sum, j) => ({ x: sum.x + positions.get(j).x / neighbors.length, y: sum.y + positions.get(j).y / neighbors.length }), { x: 0, y: 0 });
      else { anchorPoint = { x: allBounds.maxX + halfW + gap + 80, y: allBounds.minY + halfH }; disconnectedCount++; }
      const baseAngle = Math.atan2(anchorPoint.y - center.y, anchorPoint.x - center.x), angles = Array.from({ length: 24 }, (_, p) => baseAngle + (p % 2 ? -1 : 1) * Math.ceil(p / 2) * Math.PI / 12);
      let desired = Math.max(150, dim.width + gap + 32);
      for (const edge of graph.edgeIncident[i]) if ((edge.a === i && positions.has(edge.b)) || (edge.b === i && positions.has(edge.a))) desired = Math.max(desired, edge.labelWidth + dim.width / 2 + graph.dim[edge.a === i ? edge.b : edge.a].width / 2 + 36);
      const candidates = [];
      for (let ring = 1; ring <= 4; ring++) for (const angle of angles) candidates.push({ x: anchorPoint.x + Math.cos(angle) * desired * ring, y: anchorPoint.y + Math.sin(angle) * desired * ring });
      for (const b of [oldBounds, allBounds]) candidates.push({ x: b.maxX + halfW + gap, y: anchorPoint.y }, { x: b.minX - halfW - gap, y: anchorPoint.y }, { x: anchorPoint.x, y: b.maxY + halfH + gap }, { x: anchorPoint.x, y: b.minY - halfH - gap });
      let best = null, bestScore = Infinity;
      for (const point of candidates) {
        const box = nodeBounds(point, dim, gap / 2); interactions++;
        if (protectedCore && intersects(box, protectedCore) || occupied.query(box).length) continue;
        const nearby = segments.query(box); if (nearby.some(segment => segmentHitsBox(segment.a, segment.b, box))) continue;
        let score = Math.hypot(point.x - anchorPoint.x, point.y - anchorPoint.y), crossings = 0;
        for (const j of neighbors) {
          const neighbor = positions.get(j), pathBox = { minX: Math.min(point.x, neighbor.x) - 1, maxX: Math.max(point.x, neighbor.x) + 1, minY: Math.min(point.y, neighbor.y) - 1, maxY: Math.max(point.y, neighbor.y) + 1 };
          for (const segment of segments.query(pathBox)) if (segmentCrosses(point, neighbor, segment.a, segment.b)) crossings++;
        }
        score += crossings * desired * 6;
        const outward = Math.cos(Math.atan2(point.y - anchorPoint.y, point.x - anchorPoint.x) - baseAngle); score += (1 - outward) * desired * 0.22;
        if (score < bestScore - 1e-7) { best = point; bestScore = score; }
        if (++work % 32 === 0) yield;
      }
      if (!best) {
        // Guaranteed external lane beyond every known node/label/path extent.
        best = { x: allBounds.maxX + halfW + gap + 40, y: allBounds.minY + halfH + gap };
      }
      positions.set(i, best); for (const j of neighbors) addSegment(best, positions.get(j)); const box = nodeBounds(best, dim, gap / 2); occupied.add(box); allBounds = unionBounds([allBounds, box]);
      if (!intersects(nodeBounds(best, dim), oldBounds)) outsideCount++;
      yield;
    }
    return { nodes: graph.source.map((node, i) => ({ id: node.id, ...positions.get(i) })), interactions,
      layout: { method: 'incremental', quality: 'scored-sectors', fixedCount: fixed.length, addedCount: order.length, outsideCount, disconnectedCount, protectedBounds: oldBounds } };
  }
  function generatorSimulation(iterator) {
    let result = null, steps = 0;
    return { step() { if (result) return false; steps++; const next = iterator.next(); if (!next.done) return true; result = { ...next.value, iterations: steps }; return false; },
      result: () => result || { nodes: [], iterations: steps, interactions: 0 } };
  }
  function placeIncremental(input = {}) { const model = generatorSimulation(incrementalPositions(input)); while (model.step()) { /* bounded caller-owned batch */ } return model.result(); }

  function unconstrainedSimulation(input) {
    if (input.kind === 'incremental') return generatorSimulation(incrementalPositions(input));
    if (['dagre', 'partition', 'auto'].includes(input.kind)) return generatorSimulation(adaptivePositions(input));
    if (input.kind === 'structural') return structuralSimulation(input);
    if (input.kind !== 'dagre' && input.nodes.length <= 80) return smallForceSimulation(input);
    return largeForceSimulation(input);
  }
  function* anchoredPositions(input, result) {
    const source=new Map(input.nodes.map(node=>[node.id,node])), positions=new Map(result.nodes.map(node=>[node.id,{...node}]));
    const fixed=input.nodes.filter(node=>node.fixed===true&&Number.isFinite(node.x)&&Number.isFinite(node.y)).sort((a,b)=>compareId(a.id,b.id));
    if(!fixed.length)return result;
    // Translate the candidate around the analyst's anchors, then resolve free
    // footprints around those exact positions. Anchors are never a business role.
    const dx=fixed.reduce((sum,node)=>sum+(node.x-positions.get(node.id).x)/fixed.length,0);
    const dy=fixed.reduce((sum,node)=>sum+(node.y-positions.get(node.id).y)/fixed.length,0);
    for(const point of positions.values()){point.x+=dx;point.y+=dy;}
    const fixedIds=new Set(fixed.map(node=>node.id)),occupied=geometryIndex(),gap=18;
    let all=null,work=0;
    const occupy=(point,node)=>{const box=nodeBounds(point,dimensions(node),gap/2);occupied.add(box);all=all?unionBounds([all,box]):box;};
    for(const node of fixed){const point=positions.get(node.id);point.x=node.x;point.y=node.y;occupy(point,node);}
    for(const point of [...positions.values()].filter(point=>!fixedIds.has(point.id)).sort((a,b)=>compareId(a.id,b.id))) {
      const node=source.get(point.id),d=dimensions(node),origin={x:point.x,y:point.y},candidates=[origin],seen=new Set();let chosen=null;
      for(let tries=0;candidates.length&&tries<24;tries++) {
        candidates.sort((a,b)=>(a.x-origin.x)**2+(a.y-origin.y)**2-(b.x-origin.x)**2-(b.y-origin.y)**2||a.x-b.x||a.y-b.y);
        const next=candidates.shift(),key=`${next.x},${next.y}`;if(seen.has(key))continue;seen.add(key);
        const hits=occupied.query(nodeBounds(next,d,gap/2));
        if(!hits.length){chosen=next;break;}
        for(const {box}of hits.slice(0,8))candidates.push(
          {x:box.minX-d.width/2-gap/2-d.offsetX,y:next.y},{x:box.maxX+d.width/2+gap/2-d.offsetX,y:next.y},
          {x:next.x,y:box.minY-d.height/2-gap/2-d.offsetY},{x:next.x,y:box.maxY+d.height/2+gap/2-d.offsetY});
      }
      if(!chosen)chosen={x:all.maxX+d.width/2+gap/2-d.offsetX,y:origin.y};
      point.x=chosen.x;point.y=chosen.y;occupy(point,node);if(++work%32===0)yield;
    }
    return {...result,nodes:result.nodes.map(node=>positions.get(node.id)),layout:{...(result.layout||{}),pinnedCount:fixed.length}};
  }
  function simulation(input) {
    const model=unconstrainedSimulation(input);
    if(input.kind==='incremental'||!input.nodes.some(node=>node.fixed===true&&Number.isFinite(node.x)&&Number.isFinite(node.y)))return model;
    let done=false,repair=null,result=null;
    return {step(){
      if(result)return false;
      if(!done){if(model.step())return true;done=true;repair=anchoredPositions(input,model.result());}
      const next=repair.next();if(!next.done)return true;result=next.value;return false;
    },result:()=>result||model.result()};
  }
  function largeForceSimulation(input) {
    const width = Math.max(180, input.width || 900), height = Math.max(180, input.height || 600);
    const source = input.nodes || [], order = source.map(node => node.id), sorted = [...source].sort((a, b) => compareId(a.id, b.id));
    const dim = sorted.map(dimensions), nodes = sorted.map(node => ({ id: node.id, ...seed(node.id, width, height),
      ...(Number.isFinite(node.x) && Number.isFinite(node.y) ? { x: node.x, y: node.y } : {}) }));
    const index = new Map();
    nodes.forEach((node, i) => { if (typeof node.id !== 'string' || index.has(node.id)) throw new Error('layout requires unique string node IDs'); index.set(node.id, i); });
    const k = Math.sqrt(width * height / Math.max(1, nodes.length)) * 0.62, k2 = k * k, gap = 18;
    const edges = (input.edges || []).map(edge => {
      const a = index.get(edge.source), b = index.get(edge.target);
      if (a === undefined || b === undefined || a === b) return null;
      const labelWidth = Number.isFinite(edge.labelWidth) && edge.labelWidth > 0 ? edge.labelWidth : 0;
      const orderLength = Math.max(k, labelWidth + dim[a].width / 2 + dim[b].width / 2 + 24);
      return { a, b, orderLength, length: Math.max(orderLength, multilineRestLength(edge, dim[a], dim[b])) };
    }).filter(Boolean).sort((a, b) => a.a - b.a || a.b - b.b || a.orderLength - b.orderLength || a.length - b.length);
    const iterations = nodes.length > 1000 ? 70 : 110;
    let iteration = 0, interactions = 0, separator = null, separated = false;
    // Resolve residual collisions against already accepted complete body/text
    // footprints. Search is bounded per node; the nearest exterior side is a
    // guaranteed free fallback, rather than an unbounded pairwise relaxation.
    function* separate() {
      const occupied = geometryIndex(), maximumQueries = 24;
      let all = null, work = 0;
      for (let i = 0; i < nodes.length; i++) {
        const point = nodes[i], d = dim[i], origin = { x: point.x, y: point.y }, candidates = [origin];
        const queued = new Set([`${origin.x},${origin.y}`]);
        const enqueue = candidate => { const key = `${candidate.x},${candidate.y}`; if (!queued.has(key)) { queued.add(key); candidates.push(candidate); } };
        let chosen = null;
        for (let query = 0; candidates.length && query < maximumQueries; query++) {
          candidates.sort((a, b) => (a.x - origin.x) ** 2 + (a.y - origin.y) ** 2 - (b.x - origin.x) ** 2 - (b.y - origin.y) ** 2 || a.x - b.x || a.y - b.y);
          const candidate = candidates.shift(), hits = occupied.query(nodeBounds(candidate, d, gap / 2)); interactions++;
          if (!hits.length) { chosen = candidate; break; }
          // A few nearby boundaries suffice for local repair. Overflow/crowded
          // cells cannot turn this into the cubic small-graph candidate scan.
          for (const { box } of hits.slice(0, 8)) for (const next of [
            { x: box.minX - d.width / 2 - gap / 2 - d.offsetX, y: candidate.y },
            { x: box.maxX + d.width / 2 + gap / 2 - d.offsetX, y: candidate.y },
            { x: candidate.x, y: box.minY - d.height / 2 - gap / 2 - d.offsetY },
            { x: candidate.x, y: box.maxY + d.height / 2 + gap / 2 - d.offsetY }]) enqueue(next);
          if (++work % 32 === 0) yield;
        }
        if (!chosen) {
          const exterior = [
            { x: all.minX - d.width / 2 - gap / 2 - d.offsetX, y: origin.y },
            { x: all.maxX + d.width / 2 + gap / 2 - d.offsetX, y: origin.y },
            { x: origin.x, y: all.minY - d.height / 2 - gap / 2 - d.offsetY },
            { x: origin.x, y: all.maxY + d.height / 2 + gap / 2 - d.offsetY }];
          exterior.sort((a, b) => (a.x - origin.x) ** 2 + (a.y - origin.y) ** 2 - (b.x - origin.x) ** 2 - (b.y - origin.y) ** 2 || a.x - b.x || a.y - b.y);
          chosen = exterior[0];
        }
        point.x = chosen.x; point.y = chosen.y;
        const box = nodeBounds(point, d, gap / 2); occupied.add(box); all = all ? unionBounds([all, box]) : box;
        if (++work % 32 === 0) yield;
      }
    }
    function step() {
      if (iteration >= iterations || nodes.length < 2) {
        if (separated) return false;
        if (!separator) separator = separate();
        const next = separator.next(); separated = next.done; return !separated;
      }
      const n = nodes.length;
      // Keep at most 13x13 occupied cells even when the world grows beyond the
      // viewport. Near cells use bounded exact samples; far cells a centroid.
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (let i = 0; i < n; i++) { const x = nodes[i].x + dim[i].offsetX, y = nodes[i].y + dim[i].offsetY; minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); }
      const cellSize = Math.max(width, height, maxX - minX, maxY - minY) / 12, cells = new Map();
      for (let i = 0; i < n; i++) {
        const x = nodes[i].x + dim[i].offsetX, y = nodes[i].y + dim[i].offsetY;
        const gx = Math.floor((x - minX) / cellSize), gy = Math.floor((y - minY) / cellSize), key = `${gx},${gy}`;
        let c = cells.get(key); if (!c) { c = { gx, gy, x: 0, y: 0, items: [] }; cells.set(key, c); }
        c.items.push(i); c.x += x; c.y += y;
      }
      for (const c of cells.values()) { c.x /= c.items.length; c.y /= c.items.length; }
      const dx = new Float64Array(n), dy = new Float64Array(n);
      for (let i = 0; i < n; i++) {
        const p = nodes[i], px = p.x + dim[i].offsetX, py = p.y + dim[i].offsetY;
        const gx = Math.floor((px - minX) / cellSize), gy = Math.floor((py - minY) / cellSize);
        for (const c of cells.values()) {
          if (Math.abs(gx - c.gx) <= 1 && Math.abs(gy - c.gy) <= 1) {
            const sample = c.items.slice(0, 24);
            for (const j of sample) {
              if (j === i) continue;
              let x = px - nodes[j].x - dim[j].offsetX, y = py - nodes[j].y - dim[j].offsetY;
              if (x * x + y * y < 0.01) { const angle = (hash(`${p.id}|${nodes[j].id}`) % 6283) / 1000; x = Math.cos(angle); y = Math.sin(angle); }
              const distance = Math.max(0.1, Math.hypot(x, y)), ux = x / distance, uy = y / distance;
              const extent = Math.abs(ux) * (dim[i].width + dim[j].width) / 2 + Math.abs(uy) * (dim[i].height + dim[j].height) / 2 + gap;
              const repulsion = k2 / distance + Math.max(0, extent - distance) * 2;
              dx[i] += ux * repulsion; dy[i] += uy * repulsion; interactions++;
            }
            if (c.items.length > sample.length) {
              const x = px - c.x || ((hash(p.id) % 23) - 11) / 100 || 0.1, y = py - c.y || 0.1, d2 = Math.max(0.01, x * x + y * y);
              dx[i] += x * k2 * (c.items.length - sample.length) / d2; dy[i] += y * k2 * (c.items.length - sample.length) / d2; interactions++;
            }
          } else {
            const x = px - c.x, y = py - c.y, d2 = Math.max(0.01, x * x + y * y);
            dx[i] += x * k2 * c.items.length / d2; dy[i] += y * k2 * c.items.length / d2; interactions++;
          }
        }
      }
      for (const edge of edges) {
        const x = nodes[edge.a].x - nodes[edge.b].x, y = nodes[edge.a].y - nodes[edge.b].y, distance = Math.max(1, Math.hypot(x, y));
        const factor = Math.max(0, distance - edge.length) / Math.max(1, k);
        dx[edge.a] -= x * factor; dy[edge.a] -= y * factor; dx[edge.b] += x * factor; dy[edge.b] += y * factor;
      }
      const temperature = (1 - iteration / iterations) * Math.min(width, height) * 0.1 + 2;
      for (let i = 0; i < n; i++) {
        const p = nodes[i]; dx[i] += (width / 2 - p.x) * 0.06; dy[i] += (height / 2 - p.y) * 0.06;
        const distance = Math.hypot(dx[i], dy[i]) || 1, limit = Math.min(distance, temperature);
        p.x += dx[i] / distance * limit; p.y += dy[i] / distance * limit;
      }
      iteration++; return true;
    }
    return { step, result: () => { const positions = new Map(nodes.map(node => [node.id, node])); return { nodes: order.map(id => positions.get(id)), iterations: iteration, interactions }; } };
  }

  async function run(input, options = {}) {
    const model = simulation(input), pause = options.pause || (() => new Promise(resolve => setTimeout(resolve, 0)));
    while (!options.cancelled?.()) { if (!model.step()) return model.result(); await pause(); }
    return null;
  }
  root.FundsGraphLayout = Object.freeze({ seed, simulation, run, fitViewport, placeIncremental });
  if (typeof document === 'undefined' && typeof root.postMessage === 'function' && typeof root.importScripts === 'function') {
    root.onmessage = event => { const { requestId, input } = event.data; try { const model = simulation(input); while (model.step()) { /* worker can be terminated between requests */ } root.postMessage({ requestId, result: model.result() }); } catch (error) { root.postMessage({ requestId, error: String(error.message || error) }); } };
  }
})(typeof globalThis === 'undefined' ? self : globalThis);
