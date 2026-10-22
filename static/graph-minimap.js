/* Geometry-only overview. No account facts, amounts, I/O or G6 objects are retained. */
(function (global) {
  'use strict';

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const resolveTheme = theme => theme === 'light' ? 'light' : 'dark';
  const themeColors = {
    dark: { point: '#679bd6', viewportFill: 'rgba(122,184,255,0.12)', viewportStroke: '#b7d9ff' },
    light: { point: '#2367ad', viewportFill: 'rgba(35,103,173,0.12)', viewportStroke: '#2367ad' },
  };

  function bounds(nodes) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const node of Array.isArray(nodes) ? nodes : []) {
      if (!node || !finite(node.x) || !finite(node.y)) continue;
      minX = Math.min(minX, node.x); minY = Math.min(minY, node.y);
      maxX = Math.max(maxX, node.x); maxY = Math.max(maxY, node.y);
    }
    if (!finite(minX) || !finite(minY)) return null;
    if (minX === maxX) { minX -= 1; maxX += 1; }
    if (minY === maxY) { minY -= 1; maxY += 1; }
    if (!(maxX > minX && maxY > minY) || !finite(maxX - minX) || !finite(maxY - minY)) return null;
    return { minX, minY, maxX, maxY };
  }

  function transform(box, width, height, padding = 8) {
    if (!box || ![box.minX, box.minY, box.maxX, box.maxY, width, height, padding].every(finite)
      || box.maxX <= box.minX || box.maxY <= box.minY || width <= 0 || height <= 0) return null;
    const inset = clamp(padding, 0, Math.max(0, (Math.min(width, height) - 1) / 2));
    const scale = Math.min((width - 2 * inset) / (box.maxX - box.minX),
      (height - 2 * inset) / (box.maxY - box.minY));
    if (!finite(scale) || scale <= 0) return null;
    // Referencing the minimum avoids summing potentially large graph coordinates.
    return { minX: box.minX, minY: box.minY, maxX: box.maxX, maxY: box.maxY, scale, width, height,
      left: (width - (box.maxX - box.minX) * scale) / 2,
      top: (height - (box.maxY - box.minY) * scale) / 2 };
  }

  function project(point, mapping) {
    if (!mapping || !point || !finite(point.x) || !finite(point.y)) return null;
    const x = mapping.left + (point.x - mapping.minX) * mapping.scale;
    const y = mapping.top + (point.y - mapping.minY) * mapping.scale;
    return finite(x) && finite(y) ? { x, y } : null;
  }

  function unproject(point, mapping) {
    if (!mapping || !point || !finite(point.x) || !finite(point.y)) return null;
    const x = mapping.minX + (point.x - mapping.left) / mapping.scale;
    const y = mapping.minY + (point.y - mapping.top) / mapping.scale;
    return finite(x) && finite(y) ? { x, y } : null;
  }

  function viewportRect(viewport, mapping) {
    if (!mapping || !viewport || ![viewport.minX, viewport.minY, viewport.maxX, viewport.maxY].every(finite)
      || viewport.minX > viewport.maxX || viewport.minY > viewport.maxY) return null;
    const first = project({ x: viewport.minX, y: viewport.minY }, mapping);
    const last = project({ x: viewport.maxX, y: viewport.maxY }, mapping);
    if (!first || !last) return null;
    const left = clamp(first.x, 0, mapping.width), top = clamp(first.y, 0, mapping.height);
    const right = clamp(last.x, 0, mapping.width), bottom = clamp(last.y, 0, mapping.height);
    if (right <= left || bottom <= top) return null;
    return { x: left, y: top, width: right - left, height: bottom - top };
  }

  function create(options) {
    const canvas = options?.canvas, context = canvas?.getContext?.('2d');
    if (!context) throw new TypeError('FundsGraphMinimap requires a Canvas2D canvas');
    const makeCanvas = options.createCanvas || (() => canvas.ownerDocument?.createElement('canvas'));
    const backing = makeCanvas(), baseContext = backing?.getContext?.('2d');
    if (!baseContext) throw new TypeError('FundsGraphMinimap requires a separate Canvas2D backing canvas');
    const raf = options.raf || global.requestAnimationFrame.bind(global);
    const caf = options.caf || global.cancelAnimationFrame.bind(global);
    const onNavigate = typeof options.onNavigate === 'function' ? options.onNavigate : () => {};
    let points = [], mapping = null, viewport = null, frame = null, frameSerial = 0;
    let generation = 0, pointerId = null, pendingNavigation = null, destroyed = false;
    let theme = resolveTheme(options.theme);

    function paint() {
      context.clearRect(0, 0, canvas.width, canvas.height);
      if (!mapping) return;
      context.drawImage(backing, 0, 0);
      const rect = viewportRect(viewport, mapping);
      if (!rect) return;
      context.fillStyle = themeColors[theme].viewportFill;
      context.fillRect(rect.x, rect.y, rect.width, rect.height);
      context.strokeStyle = themeColors[theme].viewportStroke; context.lineWidth = 1.5;
      context.strokeRect(rect.x, rect.y, rect.width, rect.height);
    }

    function schedule() {
      if (destroyed || frame !== null) return;
      const currentGeneration = generation, serial = ++frameSerial;
      frame = raf(() => {
        if (destroyed || generation !== currentGeneration || serial !== frameSerial) return;
        frame = null;
        const target = pendingNavigation; pendingNavigation = null;
        if (target) onNavigate(target);
        if (!destroyed && generation === currentGeneration) paint();
      });
    }

    function cancelFrame() {
      frameSerial += 1;
      if (frame !== null) caf(frame);
      frame = null; pendingNavigation = null;
    }

    function releasePointer() {
      const previous = pointerId; pointerId = null;
      if (previous !== null) {
        try { canvas.releasePointerCapture?.(previous); } catch (_) { /* The browser may already have released it. */ }
      }
    }

    function rebuild() {
      backing.width = canvas.width; backing.height = canvas.height;
      baseContext.clearRect(0, 0, backing.width, backing.height);
      mapping = transform(bounds(points), backing.width, backing.height);
      if (!mapping) return;
      // Two batched paths; no edges, labels, images or duplicate G6 item tree.
      baseContext.fillStyle = themeColors[theme].point; baseContext.beginPath();
      for (const point of points) {
        if (point.isMain) continue;
        const p = project(point, mapping);
        baseContext.rect(p.x - 1.1, p.y - 1.1, 2.2, 2.2);
      }
      baseContext.fill(); baseContext.fillStyle = '#ffd16c'; baseContext.beginPath();
      for (const point of points) {
        if (!point.isMain) continue;
        const p = project(point, mapping);
        baseContext.moveTo(p.x + 2.5, p.y); baseContext.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
      }
      baseContext.fill();
    }

    function queueNavigation(event) {
      const box = canvas.getBoundingClientRect();
      if (!mapping || !finite(box.width) || !finite(box.height) || box.width <= 0 || box.height <= 0
        || !finite(event.clientX) || !finite(event.clientY)) return;
      const target = unproject({ x: (event.clientX - box.left) * canvas.width / box.width,
        y: (event.clientY - box.top) * canvas.height / box.height }, mapping);
      if (!target) return;
      pendingNavigation = { x: clamp(target.x, mapping.minX, mapping.maxX),
        y: clamp(target.y, mapping.minY, mapping.maxY) };
      schedule();
    }

    const handlers = {
      pointerdown(event) {
        if (destroyed || !mapping || pointerId !== null || event.button !== 0 || event.isPrimary === false) return;
        pointerId = event.pointerId;
        try { canvas.setPointerCapture?.(pointerId); } catch (_) { /* Click navigation also works without capture. */ }
        event.preventDefault(); queueNavigation(event);
      },
      pointermove(event) {
        if (destroyed || pointerId === null || event.pointerId !== pointerId) return;
        event.preventDefault(); queueNavigation(event);
      },
      pointerup(event) {
        if (pointerId === null || event.pointerId !== pointerId) return;
        event.preventDefault(); queueNavigation(event); releasePointer();
      },
      pointercancel(event) {
        if (event.pointerId !== pointerId) return;
        pendingNavigation = null; releasePointer();
      },
      lostpointercapture(event) {
        if (event.pointerId !== pointerId) return;
        pointerId = null; pendingNavigation = null;
      },
    };
    for (const [name, handler] of Object.entries(handlers)) canvas.addEventListener(name, handler);

    function clear() {
      if (destroyed) return;
      generation += 1; cancelFrame(); releasePointer();
      points = []; mapping = null; viewport = null;
      baseContext.clearRect(0, 0, backing.width, backing.height);
      context.clearRect(0, 0, canvas.width, canvas.height);
    }

    return Object.freeze({
      setData(data) {
        if (destroyed) return;
        clear();
        // Copy only geometry. Caller mutation, account metadata and monetary fields never enter the cache.
        for (const node of Array.isArray(data?.nodes) ? data.nodes : []) {
          if (node && finite(node.x) && finite(node.y)) points.push({ x: node.x, y: node.y, isMain: node.isMain === true });
        }
        rebuild(); schedule();
      },
      setViewport(next) {
        if (destroyed) return;
        viewport = next ? { minX: next.minX, minY: next.minY, maxX: next.maxX, maxY: next.maxY } : null;
        schedule();
      },
      setTheme(next) {
        if (destroyed) return;
        const resolved = resolveTheme(next);
        if (theme === resolved) return;
        theme = resolved;
        rebuild(); schedule();
      },
      resize() {
        if (destroyed) return;
        rebuild(); schedule();
      },
      clear,
      destroy() {
        if (destroyed) return;
        clear(); destroyed = true;
        for (const [name, handler] of Object.entries(handlers)) canvas.removeEventListener(name, handler);
        backing.width = 0; backing.height = 0;
      },
    });
  }

  global.FundsGraphMinimap = Object.freeze({ create, bounds, transform, project, unproject, viewportRect });
})(typeof window === 'undefined' ? globalThis : window);
