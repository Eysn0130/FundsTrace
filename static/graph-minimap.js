/* Geometry-only overview. No account facts, amounts, I/O or G6 objects are retained. */
(function (global) {
  'use strict';

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const resolveTheme = theme => theme === 'light' ? 'light' : 'dark';
  const themeColors = {
    dark: { node: '#679bd6', main: '#ffd16c', viewportFill: 'rgba(122,184,255,0.12)', viewportStroke: '#b7d9ff' },
    light: { node: '#2367ad', main: '#ffd16c', viewportFill: 'rgba(35,103,173,0.12)', viewportStroke: '#2367ad' },
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
    const onGestureStart = typeof options.onGestureStart === 'function' ? options.onGestureStart : () => {};
    const canNavigate = typeof options.canNavigate === 'function' ? options.canNavigate : () => true;
    const onStatusChange = typeof options.onStatusChange === 'function' ? options.onStatusChange : () => {};
    let theme = resolveTheme(options.theme);
    const color = name => typeof options.colors?.[name] === 'string' ? options.colors[name] : themeColors[theme][name];
    const colors = { node: color('node'), main: color('main'),
      viewportStroke: color('viewportStroke'), viewportFill: color('viewportFill') };
    let points = [], mapping = null, viewport = null, frame = null, frameSerial = 0;
    let generation = 0, pointerId = null, pendingNavigation = null, destroyed = false, enabled = true;
    let dragOffset = null, pointerStart = null, dragMoved = false, navigationSerial = 0;
    let status = Object.freeze({ state: 'empty', viewport: null });

    function validViewport(next) {
      if (!next || ![next.minX, next.minY, next.maxX, next.maxY].every(finite)
        || next.minX >= next.maxX || next.minY >= next.maxY
        || !finite(next.maxX - next.minX) || !finite(next.maxY - next.minY)) return null;
      return { minX: next.minX, minY: next.minY, maxX: next.maxX, maxY: next.maxY };
    }

    function updateStatus() {
      let state = 'empty', rect = null;
      if (mapping) {
        state = 'ready';
        if (viewport) {
          state = viewport.maxX < mapping.minX || viewport.minX > mapping.maxX
            || viewport.maxY < mapping.minY || viewport.minY > mapping.maxY ? 'outside' : 'viewport';
          rect = viewportRect(viewport, mapping);
        }
      }
      if (state === status.state && ((!rect && !status.viewport) || (rect && status.viewport
        && ['x', 'y', 'width', 'height'].every(key => rect[key] === status.viewport[key])))) return;
      status = Object.freeze({ state, viewport: rect ? Object.freeze(rect) : null });
      onStatusChange(status);
    }

    function navigationAllowed() {
      if (destroyed || !enabled) return false;
      try { return canNavigate() !== false; } catch (_) { return false; }
    }

    function paint() {
      context.clearRect(0, 0, canvas.width, canvas.height);
      if (!mapping) return;
      context.drawImage(backing, 0, 0);
      const rect = viewportRect(viewport, mapping);
      if (!rect) return;
      context.fillStyle = colors.viewportFill;
      context.fillRect(rect.x, rect.y, rect.width, rect.height);
      context.strokeStyle = colors.viewportStroke; context.lineWidth = 1.5;
      context.strokeRect(rect.x, rect.y, rect.width, rect.height);
    }

    function schedule() {
      if (destroyed || frame !== null) return;
      const currentGeneration = generation, serial = ++frameSerial;
      frame = raf(() => {
        if (destroyed || generation !== currentGeneration || serial !== frameSerial) return;
        frame = null;
        const target = pendingNavigation; pendingNavigation = null;
        if (target) {
          if (navigationAllowed()) onNavigate(target.point, target.meta);
          else cancelNavigation();
        }
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
      dragOffset = null; pointerStart = null; dragMoved = false;
      if (previous !== null) {
        try { canvas.releasePointerCapture?.(previous); } catch (_) { /* The browser may already have released it. */ }
      }
    }

    function cancelNavigation() {
      navigationSerial += 1;
      pendingNavigation = null;
      releasePointer();
    }

    function rebuild() {
      backing.width = canvas.width; backing.height = canvas.height;
      baseContext.clearRect(0, 0, backing.width, backing.height);
      mapping = transform(bounds(points), backing.width, backing.height);
      if (!mapping) return;
      // Two batched paths; no edges, labels, images or duplicate G6 item tree.
      baseContext.fillStyle = colors.node; baseContext.beginPath();
      for (const point of points) {
        if (point.isMain) continue;
        const p = project(point, mapping);
        baseContext.moveTo(p.x + 1.4, p.y); baseContext.arc(p.x, p.y, 1.4, 0, Math.PI * 2);
      }
      baseContext.fill(); baseContext.fillStyle = colors.main; baseContext.beginPath();
      for (const point of points) {
        if (!point.isMain) continue;
        const p = project(point, mapping);
        baseContext.moveTo(p.x + 2.5, p.y); baseContext.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
      }
      baseContext.fill();
    }

    function eventPoint(event) {
      const box = canvas.getBoundingClientRect();
      if (!mapping || !finite(box.width) || !finite(box.height) || box.width <= 0 || box.height <= 0
        || !finite(event.clientX) || !finite(event.clientY)) return null;
      const point = { x: (event.clientX - box.left) * canvas.width / box.width,
        y: (event.clientY - box.top) * canvas.height / box.height };
      return finite(point.x) && finite(point.y) ? point : null;
    }

    function queueNavigation(event, kind) {
      const point = eventPoint(event), target = unproject(point, mapping);
      if (!target) return;
      // Every drag keeps its takeover viewport offset, including a press outside
      // its frame. A confirmed click instead locates the released bounded point.
      const next = kind === 'drag' && dragOffset ? { x: target.x - dragOffset.x, y: target.y - dragOffset.y }
        : { x: clamp(target.x, mapping.minX, mapping.maxX), y: clamp(target.y, mapping.minY, mapping.maxY) };
      if (!finite(next.x) || !finite(next.y)) return;
      pendingNavigation = { point: next, meta: { kind, position: point } };
      schedule();
    }

    function moved(event) {
      return pointerStart && finite(event.clientX) && finite(event.clientY)
        && Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) >= 4;
    }

    const handlers = {
      pointerdown(event) {
        if (!navigationAllowed() || !mapping || pointerId !== null || event.button !== 0 || event.isPrimary === false) return;
        const point = eventPoint(event), world = unproject(point, mapping);
        if (!world) return;
        const currentGeneration = generation, currentNavigation = navigationSerial;
        pendingNavigation = null;
        try { onGestureStart(); } catch (_) { cancelNavigation(); return; }
        if (!navigationAllowed() || generation !== currentGeneration || navigationSerial !== currentNavigation) return;
        canvas.focus?.({ preventScroll: true });
        dragOffset = viewport ? { x: world.x - (viewport.minX + (viewport.maxX - viewport.minX) / 2),
          y: world.y - (viewport.minY + (viewport.maxY - viewport.minY) / 2) } : null;
        pointerStart = { x: event.clientX, y: event.clientY };
        dragMoved = false;
        pointerId = event.pointerId;
        try { canvas.setPointerCapture?.(pointerId); } catch (_) { /* Click navigation also works without capture. */ }
        event.preventDefault();
      },
      pointermove(event) {
        if (destroyed || pointerId === null || event.pointerId !== pointerId) return;
        if (!navigationAllowed()) { cancelNavigation(); return; }
        event.preventDefault();
        dragMoved = dragMoved || moved(event);
        if (dragMoved) queueNavigation(event, 'drag');
      },
      pointerup(event) {
        if (pointerId === null || event.pointerId !== pointerId) return;
        if (!navigationAllowed()) { cancelNavigation(); return; }
        event.preventDefault();
        dragMoved = dragMoved || moved(event);
        queueNavigation(event, dragMoved ? 'drag' : 'click');
        releasePointer();
      },
      pointercancel(event) {
        if (event.pointerId !== pointerId) return;
        cancelNavigation();
      },
      lostpointercapture(event) {
        if (event.pointerId !== pointerId) return;
        cancelNavigation();
      },
    };
    for (const [name, handler] of Object.entries(handlers)) canvas.addEventListener(name, handler);

    function reset(notify) {
      if (destroyed) return;
      generation += 1; cancelFrame(); releasePointer();
      points = []; mapping = null; viewport = null;
      baseContext.clearRect(0, 0, backing.width, backing.height);
      context.clearRect(0, 0, canvas.width, canvas.height);
      if (notify) updateStatus();
    }

    function clear() { reset(true); }

    return Object.freeze({
      setData(data) {
        if (destroyed) return;
        reset(false);
        // Copy only geometry. Caller mutation, account metadata and monetary fields never enter the cache.
        for (const node of Array.isArray(data?.nodes) ? data.nodes : []) {
          if (node && finite(node.x) && finite(node.y)) points.push({ x: node.x, y: node.y, isMain: node.isMain === true });
        }
        rebuild(); updateStatus(); schedule();
      },
      setViewport(next) {
        if (destroyed) return;
        viewport = validViewport(next);
        updateStatus(); schedule();
      },
      setTheme(next) {
        if (destroyed) return;
        const resolved = resolveTheme(next);
        if (theme === resolved) return;
        theme = resolved;
        Object.assign(colors, themeColors[theme]);
        rebuild(); schedule();
      },
      setColors(patch) {
        if (destroyed || !patch || typeof patch !== 'object') return;
        let changed = false;
        for (const name of Object.keys(colors)) {
          const next = patch[name];
          if (typeof next !== 'string' || !next.trim() || next === colors[name]) continue;
          colors[name] = next; changed = true;
        }
        if (!changed) return;
        cancelNavigation(); rebuild(); schedule();
      },
      resize() {
        if (destroyed) return;
        cancelNavigation(); rebuild(); updateStatus(); schedule();
      },
      setEnabled(next) {
        if (destroyed) return;
        enabled = next !== false;
        if (!enabled) cancelNavigation();
      },
      cancelNavigation,
      getStatus: () => status,
      clear,
      destroy() {
        if (destroyed) return;
        reset(false); destroyed = true; updateStatus();
        for (const [name, handler] of Object.entries(handlers)) canvas.removeEventListener(name, handler);
        backing.width = 0; backing.height = 0;
      },
    });
  }

  global.FundsGraphMinimap = Object.freeze({ create, bounds, transform, project, unproject, viewportRect });
})(typeof window === 'undefined' ? globalThis : window);
