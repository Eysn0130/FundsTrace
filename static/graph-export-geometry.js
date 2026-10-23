/* Temporary image-sampling geometry. No model, saved label or node position writes. */
(function (root) {
  'use strict';
  const finite = Number.isFinite;
  const valid = box => box && [box.minX, box.minY, box.maxX, box.maxY].every(finite)
    && box.maxX >= box.minX && box.maxY >= box.minY;
  const inside = (box, viewport) => box.minX >= viewport.minX - 1e-6 && box.maxX <= viewport.maxX + 1e-6
    && box.minY >= viewport.minY - 1e-6 && box.maxY <= viewport.maxY + 1e-6;
  const union = (a, b) => !a ? { ...b } : ({ minX: Math.min(a.minX, b.minX), minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX), maxY: Math.max(a.maxY, b.maxY) });
  const textOf = item => (item.getContainer?.()?.get?.('children') || []).find(shape =>
    ['text-shape', 'node-label', 'edge-label'].includes(shape.get?.('name'))
      || ['node-label', 'edge-label'].includes(shape.get?.('className')));

  function toWorld(box, inverse) {
    if (!valid(box) || typeof inverse !== 'function') return null;
    const points = [inverse(box.minX, box.minY), inverse(box.minX, box.maxY),
      inverse(box.maxX, box.minY), inverse(box.maxX, box.maxY)];
    if (!points.every(point => point && finite(point.x) && finite(point.y))) return null;
    return { minX: Math.min(...points.map(p => p.x)), minY: Math.min(...points.map(p => p.y)),
      maxX: Math.max(...points.map(p => p.x)), maxY: Math.max(...points.map(p => p.y)) };
  }

  function prepare(options = {}) {
    const result = { restore, movedLabels: 0, unresolvedCount: 0 };
    // The caller owns the sampling camera and reapplies canonical labels when
    // restoring it. Export adds no independent text offsets or path shapes.
    function restore() {}
    const { graph, applyLabels, labelBounds, bodyBounds, itemBounds, unprojector, fitViewport } = options;
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
      // gaps, inside the output area. This never changes node coordinates.
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
      const annotations = nodes.flatMap(item => (item.getContainer?.()?.get?.('children') || [])
        .filter(shape => shape.get?.('name') === 'funds-node-annotation')
        .map((shape, index) => ({ key: `annotation:${item.getModel?.().id}:${index}`, bounds: shape.getCanvasBBox?.() })))
        .filter(note => valid(note.bounds));
      // Keep the live path anchor (including manual ratios) at every export
      // scale. Moving only its text breaks the visible connection to the flow.
      const selector = root.FundsGraphLabels?.select;
      // Report unresolved geometry honestly; retaining text is never replaced
      // by hiding an amount or mutating the source layout to make a count pass.
      const finalLabels = readLabels();
      if (typeof selector === 'function') {
        const audit = selector(finalLabels.map(label => ({ key: label.key, kind: label.kind, required: true,
          bounds: label.bounds })), { obstacles: [...nodes.map((item, index) => ({ key: `body:${index}`, bounds: bodyBounds(item) })), ...annotations],
          viewport, padding: 0, obstaclePadding: 0, retainAll: true });
        result.unresolvedCount = audit.conflicts + audit.unknownGeometry;
      } else result.unresolvedCount = finalLabels.length;
      return result;
    } finally { graph.setAutoPaint?.(autoPaint); }
  }
  root.FundsGraphExportGeometry = Object.freeze({ prepare });
})(typeof window === 'undefined' ? globalThis : window);
