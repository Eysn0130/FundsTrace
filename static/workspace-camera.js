/* Camera-only workspace composition; never changes graph model geometry. */
(function (root) {
  'use strict';

  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const validBox = box => box && [box.minX, box.minY, box.maxX, box.maxY].every(finite)
    && box.maxX >= box.minX && box.maxY >= box.minY;
  const sameMatrix = (a, b) => a && b && a.length === b.length && a.every((value, i) => Math.abs(value - b[i]) < 1e-7);
  const affine = (matrix, scale, x, y) => matrix.map((value, i) => i === 6 ? value * scale + x
    : i === 7 ? value * scale + y : [0, 1, 3, 4].includes(i) ? value * scale : value);

  function create(options = {}) {
    const container = options.container, graph = options.graph;
    const initial = options.initialTransform || { scale: 1, ...options.initialOffset };
    let compensation = { scale: finite(initial.scale) && initial.scale > 0 ? initial.scale : 1,
      x: finite(initial.x) ? initial.x : 0, y: finite(initial.y) ? initial.y : 0 };
    let destroyed = false, applying = false, previous = measure(), width = null, height = null;
    let expected = readMatrix(), journey = null, deferred = null, focusJob = null, intent = 0;
    const doc = container?.ownerDocument || root.document;
    const visibility = () => { if (doc?.hidden && focusJob) cancelFocus('hidden'); };
    doc?.addEventListener?.('visibilitychange', visibility);

    function measure() {
      try {
        const rect = container?.getBoundingClientRect?.();
        const width = container?.clientWidth, height = container?.clientHeight;
        return rect && [rect.left, rect.top, width, height].every(finite) && width > 0 && height > 0
          ? { left: rect.left, top: rect.top, width, height,
            spanWidth: finite(rect.width) && rect.width > 0 ? rect.width : width,
            spanHeight: finite(rect.height) && rect.height > 0 ? rect.height : height } : null;
      } catch (_) { return null; }
    }

    function current() {
      return !destroyed && !!graph && !graph.destroyed && (!options.isCurrent || options.isCurrent());
    }

    function readMatrix() {
      try {
        const group = graph?.get?.('group');
        if (typeof group?.getMatrix !== 'function' || typeof group?.setMatrix !== 'function') return null;
        const matrix = group.getMatrix() || [1, 0, 0, 0, 1, 0, 0, 0, 1];
        return matrix.length === 9 && Array.from(matrix).every(finite) ? Array.from(matrix) : null;
      } catch (_) { return null; }
    }

    function dimension(name, fallback) {
      const value = graph.get?.(name);
      return finite(value) && value > 0 ? value : fallback;
    }

    function canCompose() { return !options.canCompose || options.canCompose(); }

    function screenBounds(bounds, matrix) {
      const points = [[bounds.minX, bounds.minY], [bounds.maxX, bounds.minY],
        [bounds.minX, bounds.maxY], [bounds.maxX, bounds.maxY]].map(([x, y]) => ({
        x: matrix[0] * x + matrix[3] * y + matrix[6], y: matrix[1] * x + matrix[4] * y + matrix[7],
      }));
      return { minX: Math.min(...points.map(point => point.x)), minY: Math.min(...points.map(point => point.y)),
        maxX: Math.max(...points.map(point => point.x)), maxY: Math.max(...points.map(point => point.y)) };
    }

    function startJourney(rect, collapsed) {
      const matrix = readMatrix(), content = options.getContentBounds?.(), visible = options.getVisibilityBounds?.();
      const bounds = validBox(content) ? content : visible;
      if (!matrix || !validBox(bounds)) return false;
      const screen = screenBounds(bounds, matrix), visibility = screenBounds(validBox(visible) ? visible : bounds, matrix);
      const zoom = Math.hypot(matrix[0], matrix[1]);
      if (!finite(zoom) || zoom <= 0) return false;
      const padding = Math.max(0, Math.min(finite(options.padding) ? options.padding : 24,
        rect.spanWidth / 4, rect.spanHeight / 4));
      // Hidden/LOD labels protect future growth capacity, but do not decide
      // whether the content currently drawn on screen is already complete.
      journey = { rect: { ...rect }, matrix, screen, visibility, zoom, collapsed, active: true,
        complete: visibility.minX >= padding - 0.5 && visibility.minY >= padding - 0.5
          && visibility.maxX <= rect.spanWidth - padding + 0.5 && visibility.maxY <= rect.spanHeight - padding + 0.5 };
      expected = matrix;
      return true;
    }

    function finishFocus(job, result) {
      if (job.finished) return;
      job.finished = true;
      if (job.frame != null) { try { root.cancelAnimationFrame?.(job.frame); } catch (_) { /* The old frame is also identity guarded. */ } }
      job.frame = null;
      if (focusJob === job) focusJob = null;
      if (!result.ok) intent++;
      job.resolve(result);
      try { job.settings.onFinish?.(result); } catch (_) { /* Completion must still release its owner. */ }
    }

    function cancelFocus(reason = 'cancelled') {
      intent++;
      if (!focusJob) return false;
      finishFocus(focusJob, { ok: false, reason });
      return true;
    }

    function cancelComposition(reason = 'composition-cancelled') {
      journey = null; deferred = null; cancelFocus(reason);
    }

    function focus(point, settings = {}) {
      const reject = reason => {
        const result = { ok: false, reason };
        try { settings.onFinish?.(result); } catch (_) { /* A rejected target never acquires the camera. */ }
        return Promise.resolve(result);
      };
      if (!point || !finite(point.x) || !finite(point.y)) return reject('invalid-target');
      if (settings.bounds != null && !validBox(settings.bounds)) return reject('invalid-bounds');
      if (settings.maxZoom != null && (!finite(settings.maxZoom) || settings.maxZoom <= 0)) return reject('invalid-zoom');
      if (settings.preferredZoom != null && (!finite(settings.preferredZoom) || settings.preferredZoom <= 0)) return reject('invalid-zoom');
      const permitted = () => {
        try {
          if (!current() || settings.isCurrent && !settings.isCurrent()) return 'stale';
          if (options.isSuspended?.()) return 'suspended';
          if (doc?.hidden) return 'hidden';
          return null;
        } catch (_) { return 'stale'; }
      };
      const denied = permitted(); if (denied) return reject(denied);
      resize();
      const rect = measure(), before = readMatrix();
      if (!rect || !before) return reject('unavailable-camera');
      const zoom = Math.hypot(before[0], before[1]);
      if (!finite(zoom) || zoom <= 0) return reject('unavailable-camera');
      // Center the requested node itself, including asymmetric text in a
      // symmetric envelope. The geometry kernel chooses a free screen area;
      // no other node, label, route or business data is visited here.
      const target = { x: point.x, y: point.y };
      const projected = { x: before[0] * target.x + before[3] * target.y + before[6],
        y: before[1] * target.x + before[4] * target.y + before[7] };
      const box = screenBounds(settings.bounds || { minX: point.x, minY: point.y, maxX: point.x, maxY: point.y }, before);
      const halfWidth = Math.max(0.5, Math.abs(box.minX - projected.x), Math.abs(box.maxX - projected.x)) / zoom;
      const halfHeight = Math.max(0.5, Math.abs(box.minY - projected.y), Math.abs(box.maxY - projected.y)) / zoom;
      const bounds = { minX: target.x - halfWidth, maxX: target.x + halfWidth,
        minY: target.y - halfHeight, maxY: target.y + halfHeight };
      // Browsing focus preserves the reading scale. Only an explicit request
      // may enlarge a tiny overview, still bounded by the target and engine.
      const preferredZoom = settings.preferredZoom ?? zoom;
      const maxZoom = Math.min(preferredZoom, settings.maxZoom || preferredZoom, dimension('maxZoom', 4));
      const minZoom = Math.min(maxZoom, dimension('minZoom', 0.02));
      const fitViewport = options.fitViewport || root.FundsGraphLayout?.fitViewport;
      const obstacles = settings.obstacles || [];
      let fit;
      try {
        if (fitViewport) fit = fitViewport({ bounds, width: rect.width, height: rect.height,
          obstacles, padding: finite(options.padding) ? options.padding : 24, minZoom, maxZoom });
        else if (!obstacles.length) {
          const padding = finite(options.padding) ? Math.max(0, options.padding) : 24;
          const available = Math.min(maxZoom, (rect.width - padding * 2) / (halfWidth * 2),
            (rect.height - padding * 2) / (halfHeight * 2));
          fit = { zoom: available, fits: available >= minZoom, center: { x: rect.width / 2, y: rect.height / 2 } };
        }
      } catch (_) { return reject('unavailable-space'); }
      if (!fit || fit.fits === false || !finite(fit.zoom) || fit.zoom < minZoom - 1e-9
        || !finite(fit.center?.x) || !finite(fit.center?.y)) return reject('unavailable-space');
      const nextZoom = Math.min(maxZoom, fit.zoom), ratio = nextZoom / zoom;
      const finalMatrix = affine(before, ratio, fit.center.x - projected.x * ratio, fit.center.y - projected.y * ratio);
      if (!finalMatrix.every(finite)) return reject('unavailable-camera');
      cancelFocus('superseded');
      // A completion handler may deliberately start a newer focus. Its live
      // promise and camera intent must not be orphaned by this older request.
      if (focusJob) return reject('superseded');
      journey = null; deferred = null;
      const reduced = () => {
        try { return !!root.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches; } catch (_) { return false; }
      };
      const duration = Math.max(0, Math.min(1000, finite(settings.duration) ? settings.duration : 320));
      const now = () => root.performance?.now?.() ?? Date.now();
      return new Promise(resolve => {
        const job = { settings, resolve, frame: null, finished: false, expected: before, started: now() };
        focusJob = job;
        const frame = timestamp => {
          job.frame = null;
          if (job.finished || focusJob !== job) return;
          const invalid = permitted();
          if (invalid) { finishFocus(job, { ok: false, reason: invalid }); return; }
          if (!sameMatrix(readMatrix(), job.expected)) { finishFocus(job, { ok: false, reason: 'camera-changed' }); return; }
          const currentRect = measure();
          if (!currentRect || ['left', 'top', 'width', 'height', 'spanWidth', 'spanHeight'].some(key => currentRect[key] !== rect[key])) {
            finishFocus(job, { ok: false, reason: 'resized' }); return;
          }
          const progress = !duration || reduced() || !root.requestAnimationFrame ? 1
            : Math.max(0, Math.min(1, (timestamp - job.started) / duration));
          const eased = 1 - (1 - progress) ** 3;
          const matrix = progress === 1 ? finalMatrix : before.map((value, i) => value + (finalMatrix[i] - value) * eased);
          try {
            // A deliberate focus is user camera intent, not sidebar geometry
            // compensation; history must never apply this translation twice.
            graph.get('group').setMatrix(matrix);
            job.expected = matrix; expected = matrix;
            graph.paint?.();
            settings.onFrame?.({ progress, point: { ...target }, zoom: zoom + (nextZoom - zoom) * eased,
              center: { ...fit.center } });
          } catch (_) { finishFocus(job, { ok: false, reason: 'camera-error' }); return; }
          if (job.finished || focusJob !== job) return;
          if (progress === 1) finishFocus(job, { ok: true, reason: 'focused' });
          else {
            try { job.frame = root.requestAnimationFrame(frame); }
            catch (_) { finishFocus(job, { ok: false, reason: 'camera-error' }); }
          }
        };
        if (!duration || reduced() || !root.requestAnimationFrame || sameMatrix(before, finalMatrix)) frame(job.started + duration);
        else {
          try { job.frame = root.requestAnimationFrame(frame); }
          catch (_) { finishFocus(job, { ok: false, reason: 'camera-error' }); }
        }
      });
    }

    function beginSidebar({ collapsed } = {}) {
      cancelFocus('sidebar');
      if (!current()) return false;
      if (options.isSuspended?.()) {
        deferred = { collapsed: !!collapsed, hadCollapse: !!collapsed || !!deferred?.hadCollapse, ended: false };
        return true;
      }
      resize();
      const rect = measure();
      if (!rect || !canCompose()) { cancelComposition(); return false; }
      if (journey) { journey.active = true; journey.collapsed = !!collapsed; return true; }
      return !!collapsed && startJourney(rect, true);
    }

    function endSidebar() {
      if (options.isSuspended?.()) {
        if (deferred) deferred.ended = true;
        if (journey) journey.finishOnResume = true;
        return null;
      }
      const receipt = resize();
      if (journey) {
        journey.active = false;
        if (!journey.collapsed) journey = null;
      }
      return receipt;
    }

    function compose(next) {
      const base = journey, box = base.screen;
      const gain = Math.max(0, next.spanWidth - base.rect.spanWidth);
      const dx = base.rect.left - next.left, dy = base.rect.top - next.top;
      const padding = Math.max(0, Math.min(finite(options.padding) ? options.padding : 24,
        next.spanWidth / 4, next.spanHeight / 4));
      let factor = 1, x = dx, y = dy;
      if (base.complete) {
        // Use the widened viewport at its real width ratio, constrained by the
        // content envelope and height. Never fit an oversized graph down.
        const centerX = (box.minX + box.maxX) / 2 + dx - gain / 2;
        const centerY = (box.minY + box.maxY) / 2 + dy;
        const halfWidth = Math.max(0.5, (box.maxX - box.minX) / 2);
        const halfHeight = Math.max(0.5, (box.maxY - box.minY) / 2);
        const maximum = dimension('maxZoom', 4) / base.zoom;
        factor = Math.max(1, Math.min(maximum, next.spanWidth / base.rect.spanWidth,
          (centerX - padding) / halfWidth, (next.spanWidth - padding - centerX) / halfWidth,
          (centerY - padding) / halfHeight, (next.spanHeight - padding - centerY) / halfHeight));
        for (const obstacle of options.getViewportObstacles?.({ width: next.width, height: next.height }) || []) {
          const b = obstacle?.bounds; if (!validBox(b)) continue;
          const horizontal = Math.max(b.minX - padding - centerX, centerX - b.maxX - padding, 0) / halfWidth;
          const vertical = Math.max(b.minY - padding - centerY, centerY - b.maxY - padding, 0) / halfHeight;
          factor = Math.min(factor, Math.max(1, horizontal, vertical));
        }
        x = centerX - (box.minX + box.maxX) / 2 * factor;
        y = centerY - (box.minY + box.maxY) / 2 * factor;
      } else {
        // Added width reveals right overflow at the current reading scale. Do
        // not push previously visible left content out to show the final node.
        const overflow = Math.max(0, base.visibility.maxX - base.rect.spanWidth + padding);
        x -= Math.min(gain, overflow);
      }
      return affine(base.matrix, factor, x, y);
    }

    function writeMatrix(matrix, before) {
      const beforeZoom = Math.hypot(before[0], before[1]), afterZoom = Math.hypot(matrix[0], matrix[1]);
      const scale = afterZoom / beforeZoom;
      if (!finite(scale) || scale <= 0) return false;
      const x = matrix[6] - before[6] * scale, y = matrix[7] - before[7] * scale;
      graph.get('group').setMatrix(matrix);
      compensation = { scale: compensation.scale * scale,
        x: compensation.x * scale + x, y: compensation.y * scale + y };
      expected = matrix; graph.paint?.();
      return true;
    }

    function resize() {
      if (!current() || options.isSuspended?.()) {
        if (focusJob) cancelFocus(!current() ? 'stale' : 'suspended');
        return null;
      }
      const next = measure();
      if (!next) { if (focusJob) cancelFocus('unavailable-camera'); return null; }
      const dx = previous ? previous.left - next.left : 0, dy = previous ? previous.top - next.top : 0;
      try {
        const before = readMatrix();
        if (focusJob && previous && (dx || dy || previous.width !== next.width || previous.height !== next.height
          || previous.spanWidth !== next.spanWidth || previous.spanHeight !== next.spanHeight)) cancelFocus('resized');
        if (journey && (!canCompose() || !sameMatrix(before, expected)
          || !journey.active && (dx || dy || previous.width !== next.width || previous.height !== next.height))) journey = null;
        if (deferred) {
          const pending = deferred; deferred = null;
          if (!journey && pending.hadCollapse && canCompose()) startJourney(previous || next, pending.collapsed);
          if (journey) { journey.active = true; journey.collapsed = pending.collapsed; journey.finishOnResume = pending.ended; }
        }
        const resized = dimension('width', width) !== next.width || dimension('height', height) !== next.height;
        applying = true;
        if (resized) graph.changeSize(next.width, next.height);
        if (!current() || options.isSuspended?.()) return null;
        let composed = false;
        if (journey?.active && before) {
          const target = compose(next);
          if (!sameMatrix(before, target)) composed = writeMatrix(target, before);
        } else if (dx || dy) {
          graph.translate(dx, dy);
          compensation.x += dx; compensation.y += dy; expected = readMatrix();
        }
        previous = next; width = next.width; height = next.height;
        const receipt = { width, height, dx, dy, resized, translated: !!(dx || dy), composed };
        if (resized || receipt.translated || composed) options.onResize?.(receipt);
        if (journey?.finishOnResume) {
          journey.active = false; delete journey.finishOnResume;
          if (!journey.collapsed) journey = null;
        }
        return receipt;
      } catch (_) {
        // Preserve the old origin when a late delivery reaches a detached graph;
        // a successful retry still receives the unapplied displacement.
        return null;
      } finally { applying = false; }
    }

    function rebase() {
      cancelComposition('rebased');
      if (!current() || options.isSuspended?.()) return false;
      const next = measure();
      if (!next) return false;
      previous = next; expected = readMatrix();
      return true;
    }

    return {
      resize, resume: resize, rebase, beginSidebar, endSidebar, cancelComposition, focus, cancelFocus,
      getIntent: () => intent,
      isApplying: () => applying,
      getTransform: () => ({ ...compensation }),
      getOffset: () => ({ x: compensation.x, y: compensation.y }),
      destroy() {
        destroyed = true; previous = null; cancelComposition('destroyed');
        doc?.removeEventListener?.('visibilitychange', visibility);
      },
    };
  }

  root.FundsWorkspaceCamera = { create };
})(window);
