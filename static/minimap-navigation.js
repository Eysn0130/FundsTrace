/* Minimap chrome and camera controls; graph geometry remains owned by the host. */
(function (root) {
  'use strict';

  const storageKey = 'fund_minimap_expanded';
  const themes = [
    { name: 'warm', colors: { node: '#6f7e8c', main: '#a47d36', viewportStroke: '#aa6d54', viewportFill: 'rgba(190,119,92,0.08)' } },
    { name: 'graphite', colors: { node: '#92a6bd', main: '#dfbf7d', viewportStroke: '#9dbde8', viewportFill: 'rgba(157,189,232,0.09)' } },
    { name: 'mist', colors: { node: '#647e76', main: '#9c8145', viewportStroke: '#4e7d73', viewportFill: 'rgba(81,134,125,0.08)' } },
  ];
  const openFrame = { opacity: 1, transform: 'translateY(0) scale(1)' };
  const closedFrame = { opacity: 0, transform: 'translateY(8px) scale(.985)' };
  const minimumZoom = 0.02, maximumZoom = 4;
  const finite = value => typeof value === 'number' && Number.isFinite(value);

  function create(options = {}) {
    const doc = options.document || root.document;
    const el = id => doc?.getElementById?.(id);
    const panel = el('graphMinimapPanel'), popover = el('graphMinimapPopover');
    const host = el('canvasWrap');
    const toggle = el('btnMinimapToggle'), ribbon = el('btnMinimap');
    const canvas = el('graphMinimap'), value = el('minimapZoomValue');
    const zoomOut = el('btnMinimapZoomOut'), zoomIn = el('btnMinimapZoomIn'), fit = el('btnMinimapFit');
    const status = el('minimapStatus'), empty = el('minimapEmpty'), recovery = el('minimapRecovery');
    const listeners = [];
    const view = doc?.defaultView || root;
    let motionPreference;
    try { motionPreference = view.matchMedia?.('(prefers-reduced-motion: reduce)'); } catch (_) { /* Immediate fallback remains available. */ }
    let preferredExpanded = false, expanded = false, destroyed = false, theme = null, themeColors = null, animation = null, animationSerial = 0;
    let layout = { panelVisible: true, canExpand: true, key: 'unmeasured' }, snapshot = null, syncing = false, ready = false;
    try { preferredExpanded = options.storage?.getItem(storageKey) === '1'; } catch (_) { /* Keep the local preference if storage is unavailable. */ }
    expanded = preferredExpanded;

    function listen(node, type, callback) {
      if (!node) return;
      node.addEventListener(type, callback);
      listeners.push(() => node.removeEventListener(type, callback));
    }

    function read() {
      try {
        const state = options.getState?.() || {};
        return { loading: !!state.loading, busy: !!state.busy, hasNodes: !!state.hasNodes,
          zoom: finite(state.zoom) && state.zoom > 0 ? state.zoom : null, status: state.status || 'empty' };
      } catch (_) {
        return { loading: true, busy: true, hasNodes: false, zoom: null, status: 'empty' };
      }
    }

    function disabled(node, blocked) {
      if (!node) return;
      node.disabled = blocked;
      node.setAttribute('aria-disabled', String(blocked));
    }

    function measureLayout() {
      const fallback = { panelVisible: true, canExpand: true, key: 'unmeasured' };
      try {
        const box = host?.getBoundingClientRect?.();
        if (!box || !finite(box.width) || !finite(box.height) || box.width < 0 || box.height < 0) return fallback;
        const style = panel && view.getComputedStyle?.(panel);
        if (!style) return fallback;
        const pixels = name => {
          const text = (style.getPropertyValue?.(name) || style[name] || '').trim();
          return /^\d+(?:\.\d+)?px$/.test(text) ? Number.parseFloat(text) : null;
        };
        const width = pixels('--mm-width'), mapHeight = pixels('--mm-map-height');
        const controlsHeight = pixels('--mm-controls-height'), gap = pixels('--mm-gap');
        const cssRight = pixels('right'), cssBottom = pixels('bottom');
        if ([width, mapHeight, controlsHeight, gap, cssRight, cssBottom].some(value => value === null)
          || width <= 0 || mapHeight <= 0 || controlsHeight <= 0) return fallback;
        const right = Math.max(6, cssRight), bottom = Math.max(6, cssBottom);
        const panelVisible = box.width >= width + right + 6 && box.height >= controlsHeight + bottom + 6;
        return { panelVisible, canExpand: panelVisible && box.height >= mapHeight + gap + controlsHeight + bottom + 6,
          key: [box.width, box.height, box.left, box.top, width, mapHeight, controlsHeight, gap, right, bottom].join(':') };
      } catch (_) { return fallback; }
    }

    // RAF navigation commits may precede the observer delivery. This guard is read-only.
    function isNavigationAvailable() {
      if (destroyed || !expanded) return false;
      const current = measureLayout();
      return current.panelVisible && current.canExpand;
    }

    function focusOutside() {
      for (const id of ['btnSidebarToggle', 'btnToolbarToggle', 'graphContainer']) {
        const node = el(id);
        if (!node || node.disabled || node.hidden || node.inert) continue;
        if (node.closest?.('[hidden],[inert]') || node.getClientRects && !node.getClientRects().length) continue;
        focus(node); return;
      }
    }

    function renderExpanded() {
      if (popover) {
        popover.inert = !expanded;
        popover.setAttribute('aria-hidden', String(!expanded));
      }
      panel?.classList.toggle('is-expanded', expanded);
      panel?.setAttribute('data-expanded', String(expanded));
      for (const button of [toggle, ribbon]) {
        button?.setAttribute('aria-expanded', String(expanded));
        button?.setAttribute('aria-controls', 'graphMinimapPopover');
      }
      const toggleLabel = !layout.canExpand ? '画布空间不足，增大窗口或收起查询区后显示缩略图' : expanded ? '收起缩略图' : '展开缩略图';
      for (const button of [toggle, ribbon]) {
        button?.setAttribute('aria-label', toggleLabel);
        button?.setAttribute('title', toggleLabel);
        button?.setAttribute('aria-disabled', String(!layout.canExpand));
      }
      // Keep the visible capsule toggle focusable when its map is temporarily retracted.
      if (toggle) toggle.disabled = false;
      if (ribbon) ribbon.disabled = !layout.canExpand;
      toggle?.setAttribute('aria-pressed', String(expanded));
      ribbon?.setAttribute('aria-pressed', String(expanded));
    }

    function settle() {
      animationSerial++;
      animation?.cancel();
      animation = null;
      if (popover) popover.hidden = !expanded;
    }

    function animateExpanded() {
      if (!popover || typeof popover.animate !== 'function' || motionPreference?.matches) { settle(); return; }
      // Read the in-flight presentation before cancelling, so a reversal has no jump.
      let from = popover.hidden ? closedFrame : openFrame;
      if (animation) {
        const current = view.getComputedStyle?.(popover);
        if (current) from = { opacity: current.opacity, transform: current.transform };
      }
      const target = expanded ? openFrame : closedFrame;
      const distance = Math.abs(Number(target.opacity) - Number(from.opacity));
      if (distance <= 0.02 + Number.EPSILON) { settle(); return; }
      settle();
      popover.hidden = false;
      const serial = animationSerial;
      const running = popover.animate([from, target], {
        duration: Math.max(60, (expanded ? 220 : 160) * distance),
        easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'both',
      });
      animation = running;
      running.onfinish = () => {
        if (destroyed || serial !== animationSerial || animation !== running) return;
        settle();
      };
    }

    function syncAppearance() {
      if (destroyed) return;
      // Appearance owns resolved system themes and preview/cancel state in these attributes.
      const appearance = doc?.documentElement?.dataset;
      const next = appearance?.ftTheme === 'light' ? themes[appearance.ftAccent === 'teal' ? 2 : 0] : themes[1];
      const colors = { ...next.colors };
      try {
        const accent = view.getComputedStyle?.(doc.documentElement)?.getPropertyValue?.('--accent')?.trim();
        if (accent && view.CSS?.supports?.('color', accent) !== false) colors.viewportStroke = accent;
      } catch (_) { /* The palette remains usable without computed application tokens. */ }
      if (next === theme && Object.keys(colors).every(name => colors[name] === themeColors[name])) return;
      theme = next;
      themeColors = colors;
      panel?.setAttribute('data-theme', theme.name);
      options.onThemeChange?.(theme.name, { ...themeColors });
    }

    function sync(settings = {}) {
      if (destroyed) return null;
      if (syncing) return snapshot;
      syncing = true;
      try {
        const previousExpanded = expanded, previousLayout = layout;
        layout = measureLayout();
        const layoutChanged = previousLayout.key !== layout.key;
        const focusInPanel = panel?.contains(doc?.activeElement), focusInMap = popover?.contains(doc?.activeElement);
        expanded = preferredExpanded && layout.canExpand;
        if (panel) {
          panel.hidden = !layout.panelVisible;
          panel.inert = !layout.panelVisible;
          panel.setAttribute('aria-hidden', String(!layout.panelVisible));
        }
        if (expanded !== previousExpanded || layoutChanged && !layout.canExpand) {
          if (settings.animate && layout.panelVisible) animateExpanded(); else settle();
        }
        const state = read(), working = state.loading || state.busy;
        const available = !working && state.hasNodes && layout.panelVisible;
        const hasCurrentZoom = state.hasNodes && !state.loading && state.zoom !== null;
        const percent = hasCurrentZoom ? `${Math.round(state.zoom * 100)}%` : '—';
        const hint = working ? '正在更新画布…' : !state.hasNodes ? '当前范围暂无节点'
          : state.status === 'outside' ? '视窗已移出图形' : '拖动视窗定位 · 方向键平移';
        renderExpanded();
        if (value) {
          const label = hasCurrentZoom ? `当前缩放 ${percent}，点击恢复 100%` : '缩放不可用';
          value.setAttribute('aria-label', label);
          value.setAttribute('title', label);
        }
        disabled(zoomOut, !available || state.zoom === null || state.zoom <= minimumZoom);
        disabled(zoomIn, !available || state.zoom === null || state.zoom >= maximumZoom);
        disabled(value, !available || state.zoom === null);
        disabled(fit, !available);
        disabled(recovery, !available);
        if (status && status.textContent !== hint) status.textContent = hint;
        if (empty) {
          empty.hidden = available;
          const message = working ? '正在更新画布…' : '当前范围暂无节点';
          empty.setAttribute('aria-label', message);
          empty.setAttribute('data-loading', String(working));
        }
        if (recovery) recovery.hidden = !available || state.status !== 'outside';
        panel?.setAttribute('aria-busy', String(working));
        if (canvas) {
          canvas.setAttribute('role', 'region');
          canvas.setAttribute('aria-disabled', String(!expanded || !available));
          canvas.setAttribute('aria-busy', String(working));
          canvas.setAttribute('aria-describedby', 'minimapStatus minimapHelp');
          canvas.tabIndex = expanded && available ? 0 : -1;
        }
        snapshot = { ...state, expanded, preferredExpanded, available, panelVisible: layout.panelVisible, canExpand: layout.canExpand };
        if (focusInPanel && !layout.panelVisible) focusOutside();
        else if (focusInMap && !expanded) focus(toggle);
        if (ready && expanded !== previousExpanded) options.onExpandedChange?.(expanded);
        if (ready && layoutChanged) options.onLayoutChange?.(snapshot);
        return snapshot;
      } finally { syncing = false; }
    }

    function focus(node) { node?.focus?.({ preventScroll: true }); }

    function setExpanded(next, settings = {}) {
      if (destroyed) return false;
      sync();
      if (!layout.canExpand) return expanded;
      next = !!next;
      const changed = preferredExpanded !== next;
      const restoreFocus = !next && (settings.focus === true || popover?.contains(doc?.activeElement));
      preferredExpanded = next;
      sync({ animate: true });
      if (changed) {
        try { options.storage?.setItem(storageKey, preferredExpanded ? '1' : '0'); } catch (_) { /* The current controller still retains the choice. */ }
      }
      if (restoreFocus) focus(toggle);
      else if (expanded && settings.focus === true) focus(canvas?.tabIndex === 0 ? canvas : toggle);
      return expanded;
    }

    function camera(kind, direction) {
      const state = sync();
      if (!state?.available) return false;
      if (kind === 'zoom') {
        if (state.zoom === null || direction === 'in' && state.zoom >= maximumZoom
          || direction === 'out' && state.zoom <= minimumZoom) return false;
        const result = options.onZoom?.(direction);
        sync();
        return result;
      }
      if (kind === 'pan') {
        if (!expanded) return false;
        const result = options.onPan?.(direction);
        sync();
        return result;
      }
      const result = options.onFit?.();
      sync();
      return result;
    }

    const stop = event => { event.preventDefault(); event.stopPropagation(); };
    listen(toggle, 'click', () => setExpanded(!expanded));
    listen(view, 'funds-appearance-change', syncAppearance);
    const previousRibbonClick = ribbon?.onclick;
    const previousRibbonDisabled = ribbon?.disabled, previousRibbonAriaDisabled = ribbon?.getAttribute('aria-disabled');
    const ribbonClick = () => setExpanded(!expanded, { focus: expanded });
    if (ribbon) ribbon.onclick = ribbonClick;
    listen(zoomOut, 'click', () => camera('zoom', 'out'));
    listen(zoomIn, 'click', () => camera('zoom', 'in'));
    listen(value, 'click', () => camera('zoom', 'reset'));
    listen(fit, 'click', () => camera('fit'));
    listen(recovery, 'click', () => {
      if (!expanded || recovery.hidden || camera('fit') === false) return;
      const state = sync();
      if (state?.available && state.expanded && recovery.hidden) focus(canvas);
    });
    listen(popover, 'keydown', event => {
      if (expanded && event.key === 'Escape' && !event.isComposing) {
        stop(event);
        setExpanded(false, { focus: true });
      }
    });
    listen(canvas, 'keydown', event => {
      if (!isNavigationAvailable() || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
      const direction = { ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left', ArrowRight: 'right' }[event.key];
      const zoom = { '+': 'in', '=': 'in', '-': 'out', '0': 'reset' }[event.key];
      if (!direction && !zoom) return;
      stop(event);
      if (direction) camera('pan', direction);
      else camera('zoom', zoom);
    });

    const motionChanged = () => { if (motionPreference?.matches) settle(); };
    listen(motionPreference, 'change', motionChanged);
    listen(view, 'resize', () => sync());
    listen(view, 'fundstrace:workspace-resize', () => sync());
    if (host && typeof view.ResizeObserver === 'function') {
      const observer = new view.ResizeObserver(() => sync());
      observer.observe(host); listeners.push(() => observer.disconnect());
    }
    if (host && typeof view.MutationObserver === 'function') {
      const observer = new view.MutationObserver(records => {
        if (records.some(record => record.target === host || record.target?.matches?.('.investigation-transport')
          || [...record.addedNodes || [], ...record.removedNodes || []].some(node => node.matches?.('.investigation-transport') || node.querySelector?.('.investigation-transport')))) sync();
      });
      observer.observe(host, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'style', 'class'] });
      listeners.push(() => observer.disconnect());
    }

    function destroy() {
      if (destroyed) return;
      const wasExpanded = expanded;
      expanded = false;
      if (panel?.contains(doc?.activeElement)) focusOutside();
      layout = { panelVisible: false, canExpand: false, key: 'destroyed' };
      renderExpanded();
      if (panel) { panel.hidden = true; panel.inert = true; panel.setAttribute('aria-hidden', 'true'); }
      canvas?.setAttribute('aria-disabled', 'true');
      if (canvas) canvas.tabIndex = -1;
      settle();
      destroyed = true;
      for (const remove of listeners) remove();
      if (ribbon?.onclick === ribbonClick) {
        ribbon.onclick = previousRibbonClick;
        ribbon.disabled = previousRibbonDisabled;
        if (previousRibbonAriaDisabled === null) ribbon.removeAttribute?.('aria-disabled');
        else ribbon.setAttribute('aria-disabled', previousRibbonAriaDisabled);
      }
      if (wasExpanded) options.onExpandedChange?.(false);
    }

    syncAppearance();
    sync();
    settle();
    ready = true;
    options.onExpandedChange?.(expanded);
    options.onLayoutChange?.(snapshot);
    return { sync, setExpanded, isNavigationAvailable, destroy };
  }

  root.FundsMinimapNavigation = Object.freeze({ create });
})(window);
