/* Temporary image-sampling geometry. No model, saved label or node position writes. */
(function (root) {
  'use strict';
  const finite = Number.isFinite;
  const valid = box => box && [box.minX, box.minY, box.maxX, box.maxY].every(finite)
    && box.maxX >= box.minX && box.maxY >= box.minY;
  const inside = (box, viewport) => box.minX >= viewport.minX - 1e-6 && box.maxX <= viewport.maxX + 1e-6
    && box.minY >= viewport.minY - 1e-6 && box.maxY <= viewport.maxY + 1e-6;
  const shifted = (box, dx, dy) => ({ minX: box.minX + dx, minY: box.minY + dy, maxX: box.maxX + dx, maxY: box.maxY + dy });
  const union = (a, b) => !a ? { ...b } : ({ minX: Math.min(a.minX, b.minX), minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX), maxY: Math.max(a.maxY, b.maxY) });
  const textOf = item => (item.getContainer?.()?.get?.('children') || []).find(shape =>
    ['text-shape', 'node-label', 'edge-label'].includes(shape.get?.('name'))
      || ['node-label', 'edge-label'].includes(shape.get?.('className')));
  const alive = shape => shape && !shape.destroyed && shape.get?.('destroyed') !== true;

  function toWorld(box, inverse) {
    if (!valid(box) || typeof inverse !== 'function') return null;
    const points = [inverse(box.minX, box.minY), inverse(box.minX, box.maxY),
      inverse(box.maxX, box.minY), inverse(box.maxX, box.maxY)];
    if (!points.every(point => point && finite(point.x) && finite(point.y))) return null;
    return { minX: Math.min(...points.map(p => p.x)), minY: Math.min(...points.map(p => p.y)),
      maxX: Math.max(...points.map(p => p.x)), maxY: Math.max(...points.map(p => p.y)) };
  }

  // Candidate rectangles are complete screen-space envelopes. Keep the search
  // deterministic and bounded even when a large graph has many obstacles.
  function placements(box, obstacles, viewport, scale) {
    const width = box.maxX - box.minX, height = box.maxY - box.minY, gap = 8 * scale;
    if (width > viewport.maxX - viewport.minX || height > viewport.maxY - viewport.minY) return [{ dx: 0, dy: 0, bounds: box }];
    const xs = [box.minX, viewport.minX, viewport.maxX - width];
    const ys = [box.minY, viewport.minY, viewport.maxY - height];
    for (const obstacle of obstacles.slice(0, 96)) {
      xs.push(obstacle.bounds.minX - width - gap, obstacle.bounds.maxX + gap);
      ys.push(obstacle.bounds.minY - height - gap, obstacle.bounds.maxY + gap);
    }
    const axis = (values, min, max, origin) => [...new Set(values.map(value => Math.max(min, Math.min(max, value))))]
      .sort((a, b) => Math.abs(a - origin) - Math.abs(b - origin) || a - b).slice(0, 24);
    const xValues = axis(xs, viewport.minX, viewport.maxX - width, box.minX);
    const yValues = axis(ys, viewport.minY, viewport.maxY - height, box.minY);
    const candidates = [{ dx: 0, dy: 0, bounds: box }];
    for (const x of xValues) for (const y of yValues) {
      const dx = x - box.minX, dy = y - box.minY;
      if (dx || dy) candidates.push({ dx, dy, bounds: shifted(box, dx, dy) });
    }
    candidates.sort((a, b) => (a.dx * a.dx + a.dy * a.dy) - (b.dx * b.dx + b.dy * b.dy)
      || a.dy - b.dy || a.dx - b.dx);
    return candidates.slice(0, 384);
  }

  function localDelta(shape, graph, dx, dy) {
    const matrix = shape.getTotalMatrix?.();
    if (matrix && [matrix[0], matrix[1], matrix[3], matrix[4]].every(finite)) {
      const determinant = matrix[0] * matrix[4] - matrix[1] * matrix[3];
      if (Math.abs(determinant) > 1e-12) return {
        x: (matrix[4] * dx - matrix[3] * dy) / determinant,
        y: (-matrix[1] * dx + matrix[0] * dy) / determinant,
      };
      return null;
    }
    // Legacy adapters without a full shape matrix use the same unrotated
    // uniform camera assumed by their existing node-label anchor code.
    const zoom = Math.abs(graph.getZoom?.());
    return finite(zoom) && zoom > 0 ? { x: dx / zoom, y: dy / zoom } : null;
  }

  function prepare(options = {}) {
    const result = { restore, movedLabels: 0, unresolvedCount: 0 };
    const changes = [], cleanups = [];
    let leaders = null, restored = false;
    function restore() {
      if (restored) return;
      restored = true;
      try { leaders?.remove?.(); } catch (_) { /* disposed sampling canvas */ }
      for (const saved of changes) {
        try { if (alive(saved.shape)) saved.shape.attr({ x: saved.x, y: saved.y }); } catch (_) { /* disposed item */ }
      }
      for (const cleanup of cleanups.reverse()) {
        try { cleanup(); } catch (_) { /* the owner may have revoked the graph */ }
      }
    }
    const { graph, applyLabels, labelBounds, bodyBounds, itemBounds, unprojector, fitViewport, restoreEdge } = options;
    const width = options.width, height = options.height, footerHeight = options.footerHeight || 0;
    const scale = finite(options.scale) && options.scale > 0 ? options.scale : 1;
    const contentHeight = height - footerHeight, padding = 40 * scale;
    if (![width, height, contentHeight].every(finite) || width <= padding * 2 || contentHeight <= padding * 2
      || !graph?.getNodes || !graph.getEdges || !graph.zoomTo || !graph.translate || !graph.getCanvasByPoint
      || ![applyLabels, labelBounds, bodyBounds, itemBounds, unprojector, fitViewport].every(value => typeof value === 'function')) return result;
    const nodes = graph.getNodes(), edges = graph.getEdges(), items = [...nodes, ...edges], nodeSet = new Set(nodes);
    // Old test/host adapters can expose labels without measurable G6 geometry.
    // Do not invent rectangles or replace their established sampling behavior.
    if (!nodes.length || !nodes.every(item => valid(bodyBounds(item)))) return result;
    const initialZoom = Math.abs(graph.getZoom?.());
    if (!finite(initialZoom) || initialZoom <= 0 || !unprojector()) return result;
    const autoPaint = graph.get?.('autoPaint') !== false;
    const viewport = { minX: 16 * scale, minY: 16 * scale, maxX: width - 16 * scale, maxY: contentHeight - 16 * scale };
    const readLabels = () => items.flatMap(item => {
      const shape = textOf(item), kind = nodeSet.has(item) ? 'node' : 'edge';
      if (!shape || typeof shape.attr !== 'function') return [];
      const bounds = labelBounds(item, kind);
      return valid(bounds) ? [{ item, shape, kind, bounds, key: kind === 'node' ? String(item.getModel?.().id)
        : `${item.getModel?.().source}=>${item.getModel?.().target}` }] : [];
    });
    try {
      graph.setAutoPaint?.(false);
      // Settle the real all-label envelope, including fixed screen-pixel node
      // gaps, before reserving the footer. This never changes node coordinates.
      for (let pass = 0; pass < 8; pass++) {
        applyLabels();
        const inverse = unprojector(), labels = readLabels();
        if (!inverse || !labels.length) return result;
        let bounds = null;
        for (const item of items) { const box = itemBounds(item, inverse); if (valid(box)) bounds = union(bounds, box); }
        for (const label of labels) { const box = toWorld(label.bounds, inverse); if (box) bounds = union(bounds, box); }
        if (!bounds) return result;
        const fit = fitViewport({ bounds, width, height: contentHeight, padding, maxZoom: initialZoom, minZoom: .02 });
        if (!fit || !finite(fit.zoom) || !fit.center || !fit.worldCenter) return result;
        graph.zoomTo(fit.zoom);
        const center = graph.getCanvasByPoint(fit.worldCenter.x, fit.worldCenter.y);
        graph.translate(fit.center.x - center.x, fit.center.y - center.y);
        applyLabels();
        if (readLabels().every(label => inside(label.bounds, { minX: padding, minY: padding, maxX: width - padding, maxY: contentHeight - padding }))) break;
      }
      const labels = readLabels(), nodeLabels = labels.filter(label => label.kind === 'node');
      const edgeLabels = labels.filter(label => label.kind === 'edge');
      // Bound relocation work independently of graph size. Unplanned amounts
      // remain visible and participate as obstacles and in the final audit.
      const planned = [...edgeLabels].sort((a, b) =>
        (b.bounds.maxX - b.bounds.minX) * (b.bounds.maxY - b.bounds.minY)
          - (a.bounds.maxX - a.bounds.minX) * (a.bounds.maxY - a.bounds.minY)
          || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)).slice(0, 256);
      const plannedSet = new Set(planned);
      const obstacles = [
        ...nodes.map((item, index) => ({ key: `body:${index}`, bounds: bodyBounds(item) })),
        ...nodeLabels.map(label => ({ key: `node:${label.key}`, bounds: label.bounds })),
        ...edgeLabels.filter(label => !plannedSet.has(label)).map(label => ({ key: `fixed:${label.key}`, bounds: label.bounds })),
      ].filter(obstacle => valid(obstacle.bounds));
      const selector = root.FundsGraphLabels?.select;
      if (typeof selector === 'function') {
        const candidates = planned.map(label => ({ key: label.key, kind: 'edge', required: true,
          priority: (label.bounds.maxX - label.bounds.minX) * (label.bounds.maxY - label.bounds.minY), bounds: label.bounds,
          placements: placements(label.bounds, obstacles, viewport, scale) }));
        const selected = selector(candidates, { obstacles, viewport, padding: 4 * scale, obstaclePadding: 2 * scale, retainAll: true });
        for (const label of planned) {
          const placement = selected.edgePlacements.get(label.key);
          if (!placement || placement.conflicted || (!placement.dx && !placement.dy)) continue;
          const delta = localDelta(label.shape, graph, placement.dx, placement.dy);
          const x = label.shape.attr('x'), y = label.shape.attr('y');
          if (!delta || !finite(x) || !finite(y)) continue;
          changes.push({ shape: label.shape, x, y });
          const cleanup = restoreEdge?.(label.item);
          if (typeof cleanup === 'function') cleanups.push(cleanup);
          label.shape.attr({ x: x + delta.x, y: y + delta.y });
          result.movedLabels += 1;
          const dx = placement.dx, dy = placement.dy, box = placement.bounds;
          if (Math.hypot(dx, dy) > 12 * scale) {
            if (!leaders) {
              leaders = graph.get?.('canvas')?.addGroup?.({ name: 'funds-export-label-guides', capture: false });
              leaders?.toBack?.();
            }
            const start = { x: (label.bounds.minX + label.bounds.maxX) / 2, y: (label.bounds.minY + label.bounds.maxY) / 2 };
            const end = { x: Math.max(box.minX, Math.min(box.maxX, start.x)), y: Math.max(box.minY, Math.min(box.maxY, start.y)) };
            leaders?.addShape?.('path', { capture: false, attrs: { path: [['M', start.x, start.y], ['L', end.x, end.y]],
              stroke: label.item.getKeyShape?.()?.attr?.('stroke') || '#7f8b99', lineWidth: Math.max(1, .6 * scale), opacity: .55 } });
          }
        }
      }
      // Report unresolved geometry honestly; retaining text is never replaced
      // by hiding an amount or mutating the source layout to make a count pass.
      const finalLabels = readLabels();
      if (typeof selector === 'function') {
        const audit = selector(finalLabels.map(label => ({ key: label.key, kind: label.kind, required: true,
          bounds: label.bounds })), { obstacles: nodes.map((item, index) => ({ key: `body:${index}`, bounds: bodyBounds(item) })),
          viewport, padding: 0, obstaclePadding: 0, retainAll: true });
        result.unresolvedCount = audit.conflicts + audit.unknownGeometry;
      } else result.unresolvedCount = finalLabels.length;
      return result;
    } catch (error) {
      restore();
      throw error;
    } finally { graph.setAutoPaint?.(autoPaint); }
  }
  root.FundsGraphExportGeometry = Object.freeze({ prepare });
})(typeof window === 'undefined' ? globalThis : window);
