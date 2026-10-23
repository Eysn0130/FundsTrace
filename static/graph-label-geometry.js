/* Pure-text label placement and display-only path gaps shared with offline HTML. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.FundsGraphLabelGeometry = api;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';

  const states = new WeakMap();
  const visibleName = 'funds-edge-visible-path';
  const identity = () => [1, 0, 0, 0, 1, 0, 0, 0, 1];
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const point = value => value && finite(value.x) && finite(value.y);
  const children = item => item?.getContainer?.()?.get?.('children') || [];
  const named = (shape, name) => shape?.get?.('name') === name || shape?.get?.('className') === name;
  const textShape = item => children(item).find(shape => named(shape, 'text-shape') || named(shape, 'edge-label') || named(shape, 'node-label'));
  const plainObject = value => {
    if (!value || typeof value !== 'object') return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === null || Object.prototype.hasOwnProperty.call(prototype, 'constructor') && prototype.constructor?.name === 'Object';
  };
  const copy = value => Array.isArray(value) ? value.map(copy)
    : plainObject(value)
      ? Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, copy(entry)])) : value;
  const equal = (a, b) => {
    if (Object.is(a, b)) return true;
    if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((value, i) => equal(value, b[i]));
    if (!plainObject(a) || !plainObject(b)) return false;
    const keys = Object.keys(a);
    return keys.length === Object.keys(b).length && keys.every(key => Object.prototype.hasOwnProperty.call(b, key) && equal(a[key], b[key]));
  };
  const attrs = (shape, values) => {
    if (!shape?.attr) return;
    const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => !equal(shape.attr(key), value)));
    if (Object.keys(changed).length) shape.attr(changed);
  };
  const matrixOf = shape => typeof shape?.getMatrix === 'function' ? shape.getMatrix() : shape?.attr?.('matrix');
  const resetMatrix = shape => {
    const matrix = identity();
    if (equal(matrixOf(shape), matrix)) return;
    if (typeof shape.setMatrix === 'function') shape.setMatrix(matrix); else attrs(shape, { matrix });
  };
  let fontGeneration = 0;
  const textFonts = new WeakMap();
  let textMeasurements = new WeakMap();
  const maxTextMeasurements = 4096, maxTextCharacters = 262144, maxCachedTextLength = 8192;
  const metricProperties = ['font', 'fontKerning', 'fontStretch', 'fontVariantCaps', 'letterSpacing',
    'wordSpacing', 'textRendering', 'direction', 'lang', 'textAlign', 'textBaseline'];
  const fonts = typeof document === 'undefined' ? null : document.fonts;
  if (typeof fonts?.addEventListener === 'function') {
    fonts.addEventListener('loadingdone', () => { fontGeneration++; });
    fonts.addEventListener('loadingerror', () => { fontGeneration++; });
  }
  function refreshFontBounds(text) {
    if (!text) return;
    const version = fontGeneration + ':' + fonts?.status, previous = textFonts.get(text);
    if (previous !== undefined && previous !== version) text.clearCacheBBox?.();
    textFonts.set(text, version);
  }

  function clearTextMeasurements() { textMeasurements = new WeakMap(); }

  // Only Canvas text metrics are shared. G6 body/glyph/canvas envelopes remain
  // live reads: zoom, shadows, photographs and external shape writes own them.
  function measureText(context, value, style) {
    const text = String(value ?? ''), lines = text.split('\n');
    if (context) context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize}px ${style.fontFamily}`;
    let cache = null, key = null;
    if (typeof context?.measureText === 'function' && finite(style.fontSize) && finite(style.lineHeight)
      && text.length <= maxCachedTextLength) {
      const version = fontGeneration + ':' + fonts?.status;
      cache = textMeasurements.get(context);
      if (!cache || cache.version !== version || cache.measure !== context.measureText) {
        cache = { version, measure: context.measureText, entries: new Map(), characters: 0 };
        textMeasurements.set(context, cache);
      }
      // Use the context's actual font after assignment: an unsupported Canvas
      // font string can retain its previous value. Other shaping properties
      // can also be edited by a host without changing the requested font.
      key = JSON.stringify([text, style.fontSize, style.lineHeight,
        metricProperties.map(name => [typeof context[name], context[name]])]);
      const saved = cache.entries.get(key);
      if (saved) {
        cache.entries.delete(key); cache.entries.set(key, saved);
        return { width: saved.width, height: saved.height };
      }
    }
    let width = 0;
    for (const line of lines) {
      const metrics = context?.measureText?.(line);
      width = Math.max(width, metrics ? Math.max(metrics.width,
        (metrics.actualBoundingBoxLeft || 0) + (metrics.actualBoundingBoxRight || 0))
        : Array.from(line).length * style.fontSize);
    }
    const result = { width: width + 12,
      height: style.fontSize + Math.max(0, lines.length - 1) * style.lineHeight + 6 };
    if (cache) {
      cache.entries.set(key, { ...result, characters: text.length }); cache.characters += text.length;
      while (cache.entries.size > maxTextMeasurements || cache.characters > maxTextCharacters) {
        const oldest = cache.entries.keys().next().value;
        cache.characters -= cache.entries.get(oldest).characters; cache.entries.delete(oldest);
      }
    }
    return result;
  }

  function isSimpleGraph(data) {
    const nodes = data?.nodes, edges = data?.edges;
    if (!Array.isArray(nodes) || !Array.isArray(edges) || nodes.length > 24 || edges.length > 30) return false;
    const parents = new Map(), degrees = new Map(), pairs = new Map(), locations = new Map();
    for (const node of nodes) {
      const id = node?.id ?? node?.account;
      if (id === undefined || id === null || parents.has(String(id))) return false;
      parents.set(String(id), String(id));
      degrees.set(String(id), 0);
      locations.set(String(id), node);
    }
    function root(id) {
      let current = id;
      while (parents.get(current) !== current) current = parents.get(current);
      return current;
    }
    for (const edge of edges) {
      if (edge?.source === undefined || edge?.source === null || edge?.target === undefined || edge?.target === null) return false;
      const source = String(edge?.source), target = String(edge?.target);
      if (!parents.has(source) || !parents.has(target) || source === target) return false;
      const lower = source < target ? source : target, upper = source < target ? target : source;
      if (pairs.get(lower)?.has(upper)) return false;
      if (!pairs.has(lower)) pairs.set(lower, new Set());
      pairs.get(lower).add(upper);
      degrees.set(source, degrees.get(source) + 1);
      degrees.set(target, degrees.get(target) + 1);
      if (degrees.get(source) > 5 || degrees.get(target) > 5) return false;
      const sourceRoot = root(source), targetRoot = root(target);
      if (sourceRoot === targetRoot) return false;
      parents.set(sourceRoot, targetRoot);
    }
    // Bound topology alone cannot distinguish a sparse tree from the same tree
    // laid out with crossing branches. Missing geometry keeps cache compatibility.
    if ([...locations.values()].every(point)) {
      const segments = edges.map(edge => ({
        source: String(edge.source), target: String(edge.target),
        a: locations.get(String(edge.source)), b: locations.get(String(edge.target))
      }));
      for (let i = 0; i < segments.length; i++) {
        const first = segments[i];
        if (first.a.x === first.b.x && first.a.y === first.b.y) return false;
        for (let j = i + 1; j < segments.length; j++) {
          const second = segments[j];
          if (first.source === second.source || first.source === second.target
            || first.target === second.source || first.target === second.target) continue;
          if (segmentsCross(first.a, first.b, second.a, second.b)) return false;
        }
      }
    }
    return true;
  }

  function orientation(a, b, c) {
    const left = (b.x - a.x) * (c.y - a.y), right = (b.y - a.y) * (c.x - a.x);
    const cross = left - right, tolerance = 1e-9 * Math.max(1, Math.abs(left), Math.abs(right));
    return Math.abs(cross) <= tolerance ? 0 : Math.sign(cross);
  }

  function segmentsCross(a, b, c, d) {
    const abc = orientation(a, b, c), abd = orientation(a, b, d);
    const cda = orientation(c, d, a), cdb = orientation(c, d, b);
    if (abc * abd < 0 && cda * cdb < 0) return true;
    if (abc !== 0 || abd !== 0 || cda !== 0 || cdb !== 0) return false;
    const axis = Math.abs(b.x - a.x) >= Math.abs(b.y - a.y) ? 'x' : 'y';
    const overlap = Math.min(Math.max(a[axis], b[axis]), Math.max(c[axis], d[axis]))
      - Math.max(Math.min(a[axis], b[axis]), Math.min(c[axis], d[axis]));
    return overlap > 1e-9 * Math.max(1, Math.abs(b[axis] - a[axis]), Math.abs(d[axis] - c[axis]));
  }

  // Keep the control point halfway along the directed chord. The perpendicular
  // bend is continuous, bounded and reverses with transfer direction, so the
  // quadratic cannot overshoot its endpoint along that chord.
  function compactControlPoint(source, target) {
    if (!point(source) || !point(target)) return null;
    const dx = target.x - source.x, dy = target.y - source.y, span = Math.hypot(dx, dy);
    const middle = { x: source.x / 2 + target.x / 2, y: source.y / 2 + target.y / 2 };
    if (!finite(span)) return null;
    if (span < 1e-9) return middle;
    const bend = Math.min(28, span * .08);
    return { x: middle.x - dy / span * bend, y: middle.y + dx / span * bend };
  }

  function plain(item) {
    for (const shape of children(item).slice()) {
      if (named(shape, 'text-bg-shape') || named(shape, 'funds-label-leader')) shape.remove?.();
    }
    const text = textShape(item);
    attrs(text, { stroke: null, lineWidth: 0, shadowBlur: 0, shadowColor: 'transparent' });
    return text || null;
  }

  function boxOf(shape) {
    const box = shape?.getBBox?.();
    if (!box) return null;
    const minX = finite(box.minX) ? box.minX : box.x;
    const minY = finite(box.minY) ? box.minY : box.y;
    const maxX = finite(box.maxX) ? box.maxX : minX + box.width;
    const maxY = finite(box.maxY) ? box.maxY : minY + box.height;
    return [minX, minY, maxX, maxY].every(finite)
      ? { minX, minY, maxX, maxY, width: maxX - minX, height: maxY - minY } : null;
  }

  function placeNode(item, options) {
    const config = options || {}, text = plain(item), body = boxOf(item?.getKeyShape?.());
    if (!text || !body) return { mode: 'none' };
    refreshFontBounds(text);
    const zoom = finite(config.zoom) && config.zoom !== 0 ? Math.abs(config.zoom) : 1;
    const gap = finite(config.gap) ? Math.max(0, config.gap) : 6;
    const x = (body.minX + body.maxX) / 2, y = body.maxY + gap / zoom;
    // Re-read current glyph/body envelopes rather than caching a node position:
    // photos, emphasis, shadows and external G6 writes can change independently.
    if (equal(matrixOf(text), identity()) && text.attr('textAlign') === 'center' && text.attr('textBaseline') === 'top') {
      const current = boxOf(text), screenBody = item.getKeyShape()?.getCanvasBBox?.(), screenText = text.getCanvasBBox?.();
      const close = (a, b) => finite(a) && finite(b) && Math.abs(a - b) < 1e-9;
      const anchored = screenBody && screenText
        ? close(screenText.minY - screenBody.maxY, gap) && close(screenText.minX + screenText.maxX, screenBody.minX + screenBody.maxX)
        : current && close(current.minY, y) && close(current.minX + current.maxX, x * 2);
      if (current && anchored) return { mode: 'bottom', bbox: current, gap };
    }
    resetMatrix(text);
    attrs(text, { x, y, textAlign: 'center', textBaseline: 'top' });
    const measured = boxOf(text);
    if (!measured) return { mode: 'none' };
    // Measure the actual glyph envelope: multiline baselines, photos and node
    // emphasis sizes must all share the same single, screen-pixel anchor.
    attrs(text, { x: x + x - (measured.minX + measured.maxX) / 2, y: y + y - measured.minY });
    const screenBody = item.getKeyShape()?.getCanvasBBox?.(), screenText = text.getCanvasBBox?.();
    if (screenBody && screenText && [screenBody.minX, screenBody.maxX, screenBody.maxY,
      screenText.minX, screenText.maxX, screenText.minY].every(finite)) {
      // G6's canvas envelope also includes the node's visible shadow. Keep the
      // gap outside that envelope instead of letting it consume text spacing.
      attrs(text, { x: text.attr('x') + ((screenBody.minX + screenBody.maxX - screenText.minX - screenText.maxX) / 2) / zoom,
        y: text.attr('y') + (screenBody.maxY + gap - screenText.minY) / zoom });
    }
    return { mode: 'bottom', bbox: boxOf(text), gap };
  }

  function clearClip(shape) { if (shape?.setClip && (typeof shape.getClip !== 'function' || shape.getClip() != null)) shape.setClip(null); }

  // G6 4.8.19's Canvas endArrow:false branch clears startArrowShape instead.
  // G6 updateItem also normalizes a disabled arrow into {path:''}. Suppress
  // these explicit empty paths too; enabled styles remain owned by G6,
  // including their geometry, color and later recreation after an update.
  function syncArrows(shape) {
    if (typeof shape?.attr !== 'function' || typeof shape.get !== 'function' || typeof shape.set !== 'function') return false;
    let changed = false;
    for (const name of ['startArrow', 'endArrow']) {
      const setting = shape.attr(name), path = setting && typeof setting === 'object' ? setting.path : undefined;
      if (setting !== false && path !== '' && !(Array.isArray(path) && path.length === 0)) continue;
      const cache = name + 'Shape', arrow = shape.get(cache);
      if (!arrow) continue;
      shape.set(cache, null);
      arrow.destroy?.();
      changed = true;
    }
    if (changed) shape.clearCacheBBox?.();
    return changed;
  }

  function shapeState(shape, clone = true) {
    if (!shape) return null;
    if (typeof shape.attr !== 'function' || typeof shape.getClip !== 'function') return undefined;
    const clip = shape.getClip(), snapshot = clone ? copy : value => value;
    return { shape, attrs: snapshot(shape.attr()), matrix: snapshot(matrixOf(shape)), visible: shape.get?.('visible'),
      isVisible: shape.isVisible?.(), capture: shape.get?.('capture'), bbox: snapshot(shape.get?.('bbox')),
      bboxMethod: shape.getBBox, clip, clipAttrs: snapshot(typeof clip?.attr === 'function' ? clip.attr() : clip?.attrs),
      clipType: clip?.get?.('type') || clip?.type };
  }
  function sameShape(saved, shape) {
    const current = shapeState(shape, false);
    if (saved === null || current === null) return saved === current;
    if (!saved || !current || saved.shape !== current.shape || saved.clip !== current.clip || saved.bboxMethod !== current.bboxMethod) return false;
    return equal(saved.attrs, current.attrs) && equal(saved.matrix, current.matrix) && saved.visible === current.visible
      && saved.isVisible === current.isVisible && saved.capture === current.capture && equal(saved.bbox, current.bbox)
      && saved.clipType === current.clipType && equal(saved.clipAttrs, current.clipAttrs);
  }
  function rememberPlacement(state, item, text, config, path, anchor, shown, result) {
    const visible = children(item).find(shape => named(shape, visibleName)), outline = config.outline || null;
    state.placement = { text, visible, outline, group: item.getContainer?.(), children: children(item).slice(), input: copy({ path, anchor, shown,
      above: !!config.above, gap: finite(config.gap) ? Math.max(0, config.gap) : 4, base: state.base,
      fontGeneration, fontStatus: fonts?.status }), keyState: shapeState(state.key), textState: shapeState(text),
      visibleState: shapeState(visible || null), outlineState: shapeState(outline), result: copy(result) };
    return result;
  }
  function priorPlacement(state, item, text, config, path, anchor, shown) {
    const prior = state.placement, list = children(item), outline = config.outline || null;
    if (!prior || prior.text !== text || prior.outline !== outline || prior.group !== item.getContainer?.()
      || prior.visible !== list.find(shape => named(shape, visibleName)) || text && !list.includes(text)
      || outline && !list.includes(outline) || prior.children.length !== list.length || prior.children.some((shape, i) => shape !== list[i])) return null;
    const input = { path, anchor, shown, above: !!config.above, gap: finite(config.gap) ? Math.max(0, config.gap) : 4,
      base: state.base, fontGeneration, fontStatus: fonts?.status };
    if (!equal(prior.input, input) || !sameShape(prior.keyState, state.key) || !sameShape(prior.textState, text)
      || !sameShape(prior.visibleState, prior.visible || null) || !sameShape(prior.outlineState, outline)) return null;
    return copy(prior.result);
  }

  function stateFor(item, style) {
    const key = item?.getKeyShape?.();
    if (!key?.attr) return null;
    let state = states.get(item);
    if (!state || state.key !== key) {
      if (state?.outline) clearClip(state.outline);
      state = { key, base: { opacity: 1, strokeOpacity: 1, fillOpacity: 1, ...copy(key.attr() || {}) }, outline: null };
      delete state.base.path;
      states.set(item, state);
    }
    if (style && typeof style === 'object') state.base = { ...state.base, ...copy(style) };
    delete state.base.path;
    return state;
  }

  // Theme changes own paint only. Keep the hit path transparent, and retain
  // arrow geometry, clipping, label anchors and the visible-copy arrangement.
  function paintEdge(item, style) {
    if (!style || typeof style !== 'object') return false;
    const state = stateFor(item);
    if (!state) return false;
    const colors = {};
    for (const name of ['stroke', 'fill']) if (typeof style[name] === 'string') colors[name] = style[name];
    const arrowColors = name => {
      const source = style[name], result = {};
      for (const property of ['fill', 'stroke']) {
        if (source && typeof source === 'object' && typeof source[property] === 'string') result[property] = source[property];
      }
      return result;
    };
    const recolor = (values, name) => {
      const setting = values[name];
      if (!setting || typeof setting !== 'object') return setting;
      return { ...copy(setting), ...arrowColors(name) };
    };
    Object.assign(state.base, colors);
    for (const name of ['startArrow', 'endArrow']) {
      if (state.base[name] && typeof state.base[name] === 'object') state.base[name] = recolor(state.base, name);
    }
    for (const shape of [state.key, ...children(item).filter(value => named(value, visibleName))]) {
      const patch = { ...colors };
      for (const name of ['startArrow', 'endArrow']) {
        const setting = shape.attr(name);
        // Never enable an arrow suppressed on the transparent hit target.
        if (setting && typeof setting === 'object') patch[name] = recolor({ [name]: setting }, name);
      }
      attrs(shape, patch);
      syncArrows(shape);
      // G6 caches arrows separately and does not repaint their colours when
      // the parent path's stroke changes. Update that cache without recreating it.
      for (const name of ['startArrow', 'endArrow']) {
        const arrow = shape.get?.(name + 'Shape');
        if (!arrow?.attr) continue;
        const setting = shape.attr(name), patch = arrowColors(name);
        if (!Object.prototype.hasOwnProperty.call(patch, 'stroke') && typeof colors.stroke === 'string'
          && (!setting || typeof setting !== 'object' || !setting.stroke)) patch.stroke = colors.stroke;
        attrs(arrow, patch);
      }
    }
    return true;
  }

  function removeVisible(item) {
    children(item).filter(shape => named(shape, visibleName)).forEach(shape => shape.remove?.());
  }

  function restoreEdge(item, options) {
    const config = options || {}, state = stateFor(item, config.style);
    if (!state) return { mode: 'none', clip: false, visibleShape: null };
    const path = copy(state.key.attr('path'));
    state.placement = null;
    attrs(state.key, { ...copy(state.base), path });
    syncArrows(state.key);
    clearClip(state.key);
    removeVisible(item);
    if (state.outline && state.outline !== config.outline) clearClip(state.outline);
    clearClip(config.outline);
    state.outline = config.outline || null;
    return { mode: 'none', clip: false, visibleShape: null };
  }

  function inverseRectangle(outer, hole) {
    // The opposite winding leaves a real hole under Canvas' nonzero clip rule.
    return [
      ['M', outer.minX, outer.minY], ['L', outer.maxX, outer.minY],
      ['L', outer.maxX, outer.maxY], ['L', outer.minX, outer.maxY], ['Z'],
      ['M', hole.minX, hole.minY], ['L', hole.minX, hole.maxY],
      ['L', hole.maxX, hole.maxY], ['L', hole.maxX, hole.minY], ['Z']
    ];
  }

  function placeEdge(item, options) {
    const config = options || {}, text = plain(item), state = stateFor(item, config.style);
    if (!state) return { mode: 'none', clip: false, visibleShape: null };
    refreshFontBounds(text);
    const key = state.key, path = key.attr('path');
    syncArrows(key);
    let visible = children(item).find(shape => named(shape, visibleName));
    syncArrows(visible);
    const shown = config.visible !== false && text && text.get?.('visible') !== false
      && (typeof text.isVisible !== 'function' || text.isVisible()) && String(text.attr('text') ?? '') !== '';
    const anchor = point(config.anchor) ? config.anchor : key.getPoint?.(0.5);
    const previous = priorPlacement(state, item, text, config, path, anchor, shown);
    if (previous) return previous;
    if (!shown || !point(anchor) || !Array.isArray(path) || !path.length) {
      const result = restoreEdge(item, config);
      return rememberPlacement(state, item, text, config, path, anchor, shown, result);
    }
    const gap = finite(config.gap) ? Math.max(0, config.gap) : 4;
    const lineWidth = finite(state.base.lineWidth) ? Math.max(0, state.base.lineWidth) : 1;
    resetMatrix(text);
    attrs(text, { x: anchor.x, y: anchor.y, textAlign: 'center', textBaseline: 'middle' });
    let box = boxOf(text);
    if (!box) return restoreEdge(item, config);
    attrs(text, {
      x: anchor.x + anchor.x - (box.minX + box.maxX) / 2,
      y: config.above ? anchor.y + anchor.y - box.maxY - lineWidth / 2 - gap
        : anchor.y + anchor.y - (box.minY + box.maxY) / 2
    });
    box = boxOf(text);
    if (!box) return restoreEdge(item, config);
    if (config.above) {
      restoreEdge(item, config);
      return rememberPlacement(state, item, text, config, path, anchor, shown,
        { mode: 'above', anchor: { x: anchor.x, y: anchor.y }, bbox: box, gap, clip: false, visibleShape: null });
    }
    const group = item.getContainer?.();
    if (typeof group?.addShape !== 'function' || typeof key.setClip !== 'function') return restoreEdge(item, config);
    const pathBox = boxOf(key) || box;
    const padding = Math.max(64, lineWidth * 4, gap * 4);
    const outer = {
      minX: Math.min(pathBox.minX, box.minX) - padding,
      minY: Math.min(pathBox.minY, box.minY) - padding,
      maxX: Math.max(pathBox.maxX, box.maxX) + padding,
      maxY: Math.max(pathBox.maxY, box.maxY) + padding
    };
    const hole = { minX: box.minX - gap, minY: box.minY - gap, maxX: box.maxX + gap, maxY: box.maxY + gap };
    const clip = { type: 'path', attrs: { path: inverseRectangle(outer, hole) } };
    const visibleAttrs = { ...copy(state.base), path: copy(path) };
    if (visible) attrs(visible, visibleAttrs);
    else visible = group.addShape('path', { name: visibleName, capture: false, attrs: visibleAttrs });
    syncArrows(visible);
    if (visible.get?.('capture') !== false) visible.set?.('capture', false);
    if (visible.get?.('visible') !== true) visible.show?.();
    visible.toBack?.();
    visible.setClip(clip);
    if (key.get?.('capture') !== true) key.set?.('capture', true);
    attrs(key, { opacity: 0, strokeOpacity: 0, fillOpacity: 0, startArrow: false, endArrow: false });
    syncArrows(key);
    clearClip(key);
    if (state.outline && state.outline !== config.outline) clearClip(state.outline);
    if (config.outline) {
      attrs(config.outline, { path: copy(path) });
      config.outline.setClip?.(copy(clip));
    }
    state.outline = config.outline || null;
    return rememberPlacement(state, item, text, config, path, anchor, shown,
      { mode: 'inline', anchor: { x: anchor.x, y: anchor.y }, bbox: box, gap, clip: true, visibleShape: visibleName });
  }

  return Object.freeze({ isSimpleGraph, compactControlPoint, measureText, clearTextMeasurements,
    plain, placeNode, placeEdge, restoreEdge, paintEdge, syncArrows });
});
