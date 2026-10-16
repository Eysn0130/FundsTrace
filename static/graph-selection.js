/* Binding-scoped graph selection and geometry-only group movement. */
(function (global) {
  'use strict';
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const asIds = values => new Set(Array.from(values || [], String));
  const modelOf = item => item?.getModel?.();
  const itemId = item => modelOf(item)?.id == null ? null : String(modelOf(item).id);

  function create(options) {
    const graph = options?.graph, container = options?.container;
    if (!graph || !container) throw new TypeError('FundsGraphSelection requires a graph and container');
    const document = container.ownerDocument || global.document;
    const context = options.context, ids = asIds(options.ids);
    const current = () => !destroyed && !graph.destroyed && (!options.isCurrent || options.isCurrent(context));
    const interactive = () => !options.canInteract || options.canInteract();
    const layer = document.createElement('div');
    layer.className = 'graph-selection-layer'; layer.setAttribute('aria-hidden', 'true');
    Object.assign(layer.style, { position: 'absolute', inset: '0', overflow: 'hidden', pointerEvents: 'none' });
    container.appendChild(layer);
    const oldTabindex = container.getAttribute('tabindex');
    if (oldTabindex === null) container.setAttribute('tabindex', '0');
    const media = global.matchMedia?.('(prefers-reduced-motion: reduce)');
    const halos = new Map(), styled = new Map(), photoOutlines = new Map(), graphEvents = [];
    let nodes = new Map(), drag = null, destroyed = false, notifying = false, suppressUntil = 0, commitCount = 0, lastCommitMs = 0;

    function notify() {
      if (notifying) return;
      notifying = true;
      try { options.onChange?.({ ids: new Set(ids), count: ids.size }); } finally { notifying = false; }
    }
    function getNodes() {
      return new Map((graph.getNodes?.() || []).map(item => [itemId(item), item]).filter(([id]) => id !== null));
    }
    function node(id) { return nodes.get(String(id)); }
    function focus() { try { container.focus?.({ preventScroll: true }); } catch (_) { container.focus?.(); } }
    function motionAllowed() { return !media?.matches && !document.hidden; }
    function modalOpen() {
      return [...(document.querySelectorAll?.('.modal, [role="dialog"], dialog[open]') || [])].some(element => {
        if (element.hidden || element.getAttribute?.('aria-hidden') === 'true' || element.style?.display === 'none') return false;
        const style = global.getComputedStyle?.(element);
        return !style || (style.display !== 'none' && style.visibility !== 'hidden');
      });
    }
    function editing(target) {
      return !!target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="dialog"], .modal');
    }
    function eventSource(event) { return event?.originalEvent || event?.event || event || {}; }
    function modifier(event) { const source = eventSource(event); return !!(source.ctrlKey || source.metaKey || event?.ctrlKey || event?.metaKey); }
    function stop(event) { event?.preventDefault?.(); eventSource(event).preventDefault?.(); }

    // G6's circle selected style otherwise supplies a white fill. Override only
    // the item state cache; source styles, role shadows and photo models survive.
    function setSelected(item, selected) {
      if (!item || item.destroyed) { styled.delete(item); return; }
      if (selected) {
        const styles = item.get?.('styles') || {};
        if (!styled.has(item)) styled.set(item, styles);
        const model = modelOf(item), attrs = item.getKeyShape?.()?.attr?.() || {};
        const preserved = {};
        for (const key of ['fill', 'shadowColor', 'shadowBlur', 'shadowOffsetX', 'shadowOffsetY', 'img', 'opacity', 'fillOpacity']) {
          if (attrs[key] !== undefined) preserved[key] = attrs[key];
          if (model?.style?.[key] !== undefined) preserved[key] = model.style[key];
        }
        item.set?.('styles', { ...styles, selected: { ...styles.selected, ...preserved,
          shadowBlur: preserved.shadowBlur ?? 0, shadowColor: preserved.shadowColor ?? 'transparent',
          shadowOffsetX: preserved.shadowOffsetX ?? 0, shadowOffsetY: preserved.shadowOffsetY ?? 0,
          stroke: '#66d9ef', lineWidth: 3 } });
        // Canvas Image does not draw its stroke. A single static shape in the
        // node group keeps photos visibly selected even above the pulse budget.
        if (model?.type === 'image' && !photoOutlines.has(item)) {
          const size = sizeOf(model);
          const outline = item.getContainer?.()?.addShape?.('circle', { name: 'funds-selection-outline', capture: false,
            attrs: { x: 0, y: 0, r: Math.max(size.width, size.height) / 2 + 2, stroke: '#66d9ef',
              lineWidth: 2, fill: null, shadowBlur: 0 } });
          if (outline) photoOutlines.set(item, outline);
        }
      }
      graph.setItemState?.(item, 'selected', selected);
      if (!selected) {
        photoOutlines.get(item)?.remove?.(); photoOutlines.delete(item);
        if (styled.has(item)) { item.set?.('styles', styled.get(item)); styled.delete(item); }
      }
    }
    function withPaint(work) {
      const previous = graph.get?.('autoPaint');
      graph.setAutoPaint?.(false);
      try { work(); } finally { graph.setAutoPaint?.(previous === undefined ? true : previous); }
      graph.paint?.();
    }
    function applySelection(previous) {
      withPaint(() => {
        for (const id of previous) if (!ids.has(id)) setSelected(node(id), false);
        for (const id of ids) if (!previous.has(id)) setSelected(node(id), true);
      });
    }
    function removeHalos() {
      for (const halo of halos.values()) halo.remove();
      halos.clear();
    }
    function pauseMotion() {
      const allowed = motionAllowed();
      for (const halo of halos.values()) {
        const ring = halo.firstElementChild;
        if (allowed && !ring.classList.contains('graph-selection-breathe')) {
          // A shared wall-clock phase keeps separately selected rings in sync.
          ring.style.animationDelay = `-${Date.now() % 1800}ms`;
          ring.classList.add('graph-selection-breathe');
        } else if (!allowed) ring.classList.remove('graph-selection-breathe');
      }
    }
    function sizeOf(model) {
      const value = model?.size;
      return Array.isArray(value) ? { width: finite(value[0]) ? value[0] : 32, height: finite(value[1]) ? value[1] : value[0] || 32 }
        : { width: finite(value) ? value : 32, height: finite(value) ? value : 32 };
    }
    function project(x, y) { return graph.getCanvasByPoint?.(x, y) || { x, y }; }
    function dimensions() { return { width: container.clientWidth || 0, height: container.clientHeight || 0 }; }
    function refresh() {
      if (!current()) { cancelDrag(true); removeHalos(); return; }
      if (ids.size < 1 || ids.size > 10) { removeHalos(); return; }
      const zoom = Math.abs(graph.getZoom?.() || 1), { width, height } = dimensions();
      for (const [id, halo] of halos) if (!ids.has(id)) { halo.remove(); halos.delete(id); }
      for (const id of ids) {
        const model = modelOf(node(id));
        if (!model || !finite(model.x) || !finite(model.y)) { halos.get(id)?.remove(); halos.delete(id); continue; }
        const point = project(model.x, model.y), size = sizeOf(model);
        const w = Math.max(0.5, (size.width + 8) * zoom), h = Math.max(0.5, (size.height + 8) * zoom);
        if (!finite(point.x) || !finite(point.y) || point.x + w / 2 < 0 || point.y + h / 2 < 0 || point.x - w / 2 > width || point.y - h / 2 > height) {
          halos.get(id)?.remove(); halos.delete(id); continue;
        }
        let halo = halos.get(id);
        if (!halo) {
          halo = document.createElement('div'); halo.className = 'graph-selection-halo';
          const ring = document.createElement('div'); ring.className = 'graph-selection-ring';
          halo.appendChild(ring); layer.appendChild(halo); halos.set(id, halo);
          Object.assign(halo.style, { position: 'absolute', pointerEvents: 'none', transform: 'translate(-50%, -50%)' });
        }
        Object.assign(halo.style, { left: `${point.x}px`, top: `${point.y}px`, width: `${w}px`, height: `${h}px` });
        halo.firstElementChild.style.borderWidth = `${Math.max(0.5, Math.min(2, 1.5 * zoom))}px`;
        halo.firstElementChild.style.setProperty?.('--graph-breathe-scale', String(1 + 4 / (Math.max(size.width, size.height) + 8)));
      }
      pauseMotion();
    }
    function replace(next, openItem, cancelPending = true) {
      if (!current()) return false;
      if (cancelPending) cancelDrag(false);
      if (!current()) return false;
      const previous = new Set(ids);
      ids.clear(); for (const id of next) if (nodes.has(id)) ids.add(id);
      const changed = ids.size !== previous.size || [...ids].some(id => !previous.has(id));
      if (changed) { applySelection(previous); refresh(); notify(); }
      if (openItem) options.onPrimary?.(openItem);
      return changed;
    }
    function select(id, { toggle = false, open = false } = {}) {
      const item = node(id); if (!item || !current()) return false;
      const key = String(id), next = toggle ? new Set(ids) : new Set();
      if (toggle && next.has(key)) next.delete(key); else next.add(key);
      return replace(next, open && !toggle ? item : null);
    }
    function clear() {
      const changed = replace(new Set());
      // Edge details can remain open with no selected nodes. A clear command
      // still tells the host to dismiss those details, without repainting nodes.
      if (!changed && current()) notify();
      return changed;
    }
    function rebind(nextIds = ids) {
      if (!current()) { cancelDrag(true); removeHalos(); return; }
      cancelDrag(false);
      if (!current()) return;
      const next = asIds(nextIds), previous = new Set(ids);
      withPaint(() => {
        for (const item of styled.keys()) setSelected(item, false);
        nodes = getNodes(); ids.clear();
        for (const id of next) if (nodes.has(id)) { ids.add(id); setSelected(node(id), true); }
      });
      refresh();
      if (ids.size !== previous.size || [...ids].some(id => !previous.has(id))) notify();
    }
    function toWorld(event) {
      const source = eventSource(event), x = finite(source.clientX) ? source.clientX : event?.clientX;
      const y = finite(source.clientY) ? source.clientY : event?.clientY;
      if (!finite(x) || !finite(y)) return null;
      const point = graph.getPointByClient?.(x, y);
      if (point && finite(point.x) && finite(point.y)) return { x: point.x, y: point.y, clientX: x, clientY: y };
      const rect = container.getBoundingClientRect(), zoom = graph.getZoom?.() || 1;
      return { x: (x - rect.left) / zoom, y: (y - rect.top) / zoom, clientX: x, clientY: y };
    }
    function removeDragListeners() {
      global.removeEventListener?.('mousemove', onMouseMove);
      global.removeEventListener?.('mouseup', onMouseUp);
      global.removeEventListener?.('blur', onWindowBlur);
      global.removeEventListener?.('mouseout', onWindowExit);
    }
    function commitPositions(active, dx, dy) {
      if (!current()) return false;
      const deltaX = dx - active.appliedX, deltaY = dy - active.appliedY;
      if (!deltaX && !deltaY) return false;
      const startTime = global.performance?.now?.() ?? Date.now();
      withPaint(() => {
        options.onBeforeMove?.({ ids: new Set(active.ids), dx: deltaX, dy: deltaY, preview: active.preview });
        if (!current()) return;
        const edges = new Set();
        for (const [id, start] of active.starts) {
          const item = node(id), model = modelOf(item);
          if (!model || item.destroyed) continue;
          const point = { x: start.x + dx, y: start.y + dy };
          if (active.preview && graph.refreshPositions) { model.x = point.x; model.y = point.y; }
          else if (item.updatePosition) {
            item.updatePosition(point);
            for (const edge of item.getEdges?.() || []) edges.add(edge);
          } else graph.updateItem?.(item, point);
        }
        if (active.preview) graph.refreshPositions?.();
        else for (const edge of edges) if (!edge.destroyed) edge.refresh?.();
      });
      if (!current()) return false;
      commitCount += 1; lastCommitMs = Math.max(0, (global.performance?.now?.() ?? Date.now()) - startTime);
      active.appliedX = dx; active.appliedY = dy; refresh();
      return true;
    }
    function createPreview(active) {
      const zoom = Math.abs(graph.getZoom?.() || 1);
      let left = Infinity, top = Infinity, right = -Infinity, bottom = -Infinity;
      for (const [id, start] of active.starts) {
        const point = project(start.x, start.y), size = sizeOf(modelOf(node(id)));
        left = Math.min(left, point.x - size.width * zoom / 2); right = Math.max(right, point.x + size.width * zoom / 2);
        top = Math.min(top, point.y - size.height * zoom / 2); bottom = Math.max(bottom, point.y + size.height * zoom / 2);
      }
      const preview = document.createElement('div'); preview.className = 'graph-selection-preview';
      Object.assign(preview.style, { position: 'absolute', pointerEvents: 'none', left: `${left - 6}px`, top: `${top - 6}px`,
        width: `${right - left + 12}px`, height: `${bottom - top + 12}px` });
      layer.appendChild(preview); active.element = preview;
    }
    function updateDrag(event) {
      if (!drag) return;
      if (!current()) { cancelDrag(true); return; }
      if (!drag.started && !interactive()) { cancelDrag(false); return; }
      const point = toWorld(event); if (!point) return;
      if (!drag.started && Math.hypot(point.clientX - drag.start.clientX, point.clientY - drag.start.clientY) < 4) return;
      if (!drag.started) {
        const active = drag;
        if (!ids.has(active.id)) replace(new Set([active.id]), null, false);
        if (!current() || drag !== active) { cancelDrag(true); return; }
        active.ids = new Set(ids); active.starts = new Map();
        for (const id of active.ids) {
          const model = modelOf(node(id));
          if (finite(model?.x) && finite(model?.y)) active.starts.set(id, { x: model.x, y: model.y });
        }
        active.preview = active.ids.size >= 50; active.started = true;
        options.onMoveStart?.({ ids: new Set(active.ids), preview: active.preview });
        if (!current()) { cancelDrag(true); return; }
        if (active.preview) createPreview(active);
      }
      const active = drag;
      active.dx = point.x - active.start.x; active.dy = point.y - active.start.y;
      if (active.preview) {
        const origin = project(active.start.x, active.start.y), target = project(active.start.x + active.dx, active.start.y + active.dy);
        active.element.style.transform = `translate(${target.x - origin.x}px, ${target.y - origin.y}px)`;
      } else commitPositions(active, active.dx, active.dy);
      stop(event);
    }
    function finishDrag(cancelled, stale = false) {
      const active = drag; drag = null; removeDragListeners();
      if (!active) return;
      active.element?.remove();
      if (!active.started) return;
      suppressUntil = Date.now() + 400;
      if (stale || !current()) return;
      if (cancelled && !active.preview) commitPositions(active, 0, 0);
      else if (!cancelled && active.preview) commitPositions(active, active.dx, active.dy);
      const moved = !cancelled && !!(active.dx || active.dy);
      options.onMoveEnd?.({ ids: new Set(active.ids), moved, dx: cancelled ? 0 : active.dx, dy: cancelled ? 0 : active.dy,
        preview: active.preview, cancelled, committed: moved });
      refresh();
    }
    function cancelDrag(stale) { finishDrag(true, stale); }
    function onMouseMove(event) {
      if (event.buttons === 0) { cancelDrag(false); return; }
      updateDrag(event);
    }
    function onMouseUp(event) {
      if (drag && !drag.started && !interactive()) { cancelDrag(false); return; }
      if (drag && current()) updateDrag(event);
      finishDrag(false);
    }
    function onWindowBlur() { cancelDrag(false); }
    function onWindowExit(event) { if (event.relatedTarget === null) cancelDrag(false); }
    function onContainerMouseDown(event) {
      if (!current() || !interactive() || (event.button != null && event.button !== 0) || editing(event.target) || modalOpen()) return;
      // A fresh native gesture cannot be the previous drag's generated click.
      // Capture also covers modifier presses and blank-canvas mousedown events.
      suppressUntil = 0;
    }
    function onMouseDown(event) {
      if (!current() || !interactive() || modifier(event) || editing(eventSource(event).target) || modalOpen()) return;
      const source = eventSource(event);
      if (source.button != null && source.button !== 0) return;
      const id = itemId(event.item), start = toWorld(event); if (!node(id) || !start) return;
      cancelDrag(false); focus();
      drag = { id, start, started: false, dx: 0, dy: 0, appliedX: 0, appliedY: 0 };
      global.addEventListener?.('mousemove', onMouseMove);
      global.addEventListener?.('mouseup', onMouseUp);
      global.addEventListener?.('blur', onWindowBlur);
      global.addEventListener?.('mouseout', onWindowExit);
    }
    function suppressClick() { return Date.now() < suppressUntil; }
    function onNodeClick(event) {
      if (!current() || !interactive() || suppressClick() || editing(eventSource(event).target) || modalOpen()) return;
      focus(); select(itemId(event.item), { toggle: modifier(event), open: !modifier(event) });
    }
    function onCanvasClick(event) {
      if (!current() || !interactive() || suppressClick() || editing(eventSource(event).target) || modalOpen()) return;
      focus(); clear();
    }
    function onKeyDown(event) {
      if (!current() || event.defaultPrevented || event.isComposing || editing(event.target) || modalOpen()
        || !container.contains(event.target || document.activeElement)) return;
      if (!interactive()) { stop(event); return; }
      if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === 'a') {
        stop(event); nodes = getNodes(); replace(new Set(nodes.keys()));
      } else if (event.key === 'Escape') { stop(event); clear(); }
    }
    function onVisibility() { if (document.hidden) cancelDrag(false); pauseMotion(); }
    function onGraph(name, handler) { graph.on?.(name, handler); graphEvents.push([name, handler]); }
    onGraph('node:click', onNodeClick); onGraph('canvas:click', onCanvasClick); onGraph('node:mousedown', onMouseDown);
    for (const name of ['viewportchange', 'afterlayout', 'afterrender', 'node:drag', 'aftergraphrefreshposition', 'afterrefreshpositions']) onGraph(name, refresh);
    container.addEventListener('keydown', onKeyDown);
    container.addEventListener('mousedown', onContainerMouseDown, true);
    document.addEventListener?.('visibilitychange', onVisibility);
    media?.addEventListener?.('change', pauseMotion);
    if (!media?.addEventListener) media?.addListener?.(pauseMotion);
    global.addEventListener?.('resize', refresh);
    nodes = getNodes();
    // initGraph creates an empty G6 instance before data/render. Keep pending IDs
    // until the caller explicitly rebinds the newly rendered graph.
    withPaint(() => { for (const id of ids) if (nodes.has(id)) setSelected(node(id), true); });
    refresh();

    function destroy() {
      if (destroyed) return;
      cancelDrag(!current());
      if (current()) withPaint(() => { for (const item of styled.keys()) setSelected(item, false); });
      destroyed = true;
      for (const [name, handler] of graphEvents) graph.off?.(name, handler);
      container.removeEventListener('keydown', onKeyDown);
      container.removeEventListener('mousedown', onContainerMouseDown, true);
      document.removeEventListener?.('visibilitychange', onVisibility);
      media?.removeEventListener?.('change', pauseMotion);
      if (!media?.removeEventListener) media?.removeListener?.(pauseMotion);
      global.removeEventListener?.('resize', refresh);
      if (oldTabindex === null) container.removeAttribute('tabindex');
      for (const outline of photoOutlines.values()) if (!outline.destroyed) outline.remove?.();
      photoOutlines.clear(); removeHalos(); layer.remove(); styled.clear(); nodes.clear(); ids.clear();
    }
    return Object.freeze({ ids, select, clear, rebind, refresh, destroy,
      status: () => ({ count: ids.size, haloCount: halos.size, animatedCount: [...halos.values()].filter(halo => halo.firstElementChild.classList.contains('graph-selection-breathe')).length,
        dragging: !!drag?.started, preview: !!drag?.preview, commitCount, lastCommitMs, destroyed }) });
  }
  global.FundsGraphSelection = Object.freeze({ create });
})(typeof window === 'undefined' ? globalThis : window);
