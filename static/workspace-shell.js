/* Workspace chrome only: never changes graph data, geometry, layout or viewport. */
(function (root) {
  'use strict';

  function mount(doc = root.document) {
    const workspace = doc.getElementById('workspace');
    const sidebar = doc.getElementById('left');
    const toolbar = doc.getElementById('editorToolbar');
    const sidebarToggle = doc.getElementById('btnSidebarToggle');
    const toolbarToggle = doc.getElementById('btnToolbarToggle');
    const resizeHandle = doc.getElementById('workspaceSidebarResize');
    if (!workspace || !sidebar || !toolbar || !sidebarToggle || !toolbarToggle || !resizeHandle) return null;
    if (workspace.fundsWorkspaceShell) return workspace.fundsWorkspaceShell;

    const state = { view: 'standard', sidebarCollapsed: false, toolbarCollapsed: false };
    const viewStates = {
      standard: { sidebarCollapsed: false, toolbarCollapsed: false },
      focus: { sidebarCollapsed: true, toolbarCollapsed: true },
      split: { sidebarCollapsed: false, toolbarCollapsed: false },
    };
    const viewLabels = { standard: '标准', focus: '专注', split: '上下分栏' };
    const viewButton = doc.getElementById('btnWorkspaceView');
    const viewCurrent = doc.getElementById('workspaceViewCurrent');
    const viewOptions = {
      standard: doc.getElementById('workspaceViewStandard'),
      focus: doc.getElementById('workspaceViewFocus'),
      split: doc.getElementById('workspaceViewSplit'),
    };
    let previousView = 'standard';
    let drag = null;
    let preferredSidebarWidth = null;
    let preferredBottomHeight = null;
    let savedSidebarWidth = null;
    let sidebarEndTimer = null;
    let sidebarTransitionActive = false;
    let toolbarTransitionActive = false;
    let viewChangeVersion = 0;
    let preferenceStorage = null;
    try {
      preferenceStorage = root.FundsDesktopPreferences || root.localStorage;
      const saved = preferenceStorage?.getItem('fund_sidebar_width');
      if (typeof saved === 'string' && /^(0|[1-9]\d{0,2})$/.test(saved) && Number(saved) <= 760) {
        preferredSidebarWidth = Number(saved);
        savedSidebarWidth = saved;
      }
    } catch (_) { /* Unavailable storage must not prevent the workbench opening. */ }

    function saveSidebarWidth() {
      if (preferredSidebarWidth === null || !preferenceStorage?.setItem) return;
      const value = String(preferredSidebarWidth);
      if (value === savedSidebarWidth) return;
      try {
        preferenceStorage.setItem('fund_sidebar_width', value);
        savedSidebarWidth = value;
      } catch (_) { /* Resizing remains available when the preference cannot be saved. */ }
    }

    function emitResize(reason, phase = 'start') {
      root.dispatchEvent(new root.CustomEvent('fundstrace:workspace-resize', {
        detail: { ...state, reason, phase },
      }));
    }

    function updateToggle(button, expanded, label, icon) {
      button.setAttribute('aria-expanded', String(expanded));
      button.setAttribute('aria-label', label);
      button.title = label;
      const image = button.querySelector('img');
      if (image) image.src = `/static/ui-icons/${icon}.svg`;
    }

    function applySidebarState() {
      const collapsed = state.sidebarCollapsed;
      workspace.classList.toggle('sidebar-collapsed', collapsed);
      sidebar.inert = collapsed;
      sidebar.setAttribute('aria-hidden', String(collapsed));
      resizeHandle.tabIndex = collapsed ? -1 : 0;
      const icon = state.view === 'split'
        ? (collapsed ? 'chevron-up' : 'chevron-down')
        : (collapsed ? 'chevron-right' : 'chevron-left');
      updateToggle(sidebarToggle, !collapsed, collapsed ? '展开查询区' : '收起查询区', icon);
    }

    function applyToolbarState() {
      const collapsed = state.toolbarCollapsed;
      toolbar.classList.toggle('is-collapsed', collapsed);
      updateToggle(toolbarToggle, !collapsed, collapsed ? '展开顶部工具带' : '收起顶部工具带', collapsed ? 'chevron-down' : 'chevron-up');
    }

    function updateViewMenu() {
      for (const [view, option] of Object.entries(viewOptions)) {
        option?.setAttribute('aria-checked', String(view === state.view));
      }
      if (viewCurrent) viewCurrent.textContent = viewLabels[state.view];
    }

    function setView(view) {
      if (!Object.prototype.hasOwnProperty.call(viewStates, view) || view === state.view) return;
      if (drag) endResize({ pointerId: drag.pointerId }, false);
      root.clearTimeout?.(sidebarEndTimer); sidebarEndTimer = null;
      sidebarTransitionActive = false;
      toolbarTransitionActive = false;
      if (view !== 'focus') previousView = view;
      state.view = view;
      Object.assign(state, viewStates[view]);
      if (state.sidebarCollapsed && sidebar.contains(doc.activeElement)) sidebarToggle.focus();
      // A view change is chrome only; its receipt must never request sidebar construction.
      emitResize('workspace-view', 'prepare');
      workspace.classList.add('workspace-view-changing');
      workspace.setAttribute('data-workspace-view', view);
      applySidebarState();
      applyToolbarState();
      updateSidebarBounds('workspace-view', false);
      updateViewMenu();
      emitResize('workspace-view');
      emitResize('workspace-view', 'end');
      const version = ++viewChangeVersion;
      const finishViewChange = () => {
        if (version === viewChangeVersion) workspace.classList.remove('workspace-view-changing');
      };
      if (root.requestAnimationFrame) root.requestAnimationFrame(finishViewChange);
      else finishViewChange();
    }

    function toggleFocus() {
      setView(state.view === 'focus' ? previousView : 'focus');
    }

    function setSidebarCollapsed(collapsed) {
      collapsed = Boolean(collapsed);
      if (collapsed === state.sidebarCollapsed) return;
      if (drag) endResize({ pointerId: drag.pointerId }, false);
      if (collapsed && sidebar.contains(doc.activeElement)) sidebarToggle.focus();
      state.sidebarCollapsed = collapsed;
      viewStates[state.view].sidebarCollapsed = collapsed;
      // Camera reads the starting geometry before CSS changes the sidebar.
      emitResize('sidebar', 'prepare');
      sidebarTransitionActive = true;
      applySidebarState();
      updateSidebarBounds('sidebar', false);
      emitResize('sidebar');
      root.clearTimeout?.(sidebarEndTimer);
      const reduced = root.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      const durations = (root.getComputedStyle?.(sidebar).transitionDuration || '0.22s').split(',').map(value => {
        const amount = Number.parseFloat(value) || 0;
        return value.trim().endsWith('ms') ? amount : amount * 1000;
      });
      const duration = Math.max(...durations);
      if (reduced || duration === 0) finishSidebarTransition();
      else sidebarEndTimer = root.setTimeout?.(finishSidebarTransition, duration + 34);
    }

    function setToolbarCollapsed(collapsed) {
      collapsed = Boolean(collapsed);
      if (collapsed === state.toolbarCollapsed) return;
      state.toolbarCollapsed = collapsed;
      viewStates[state.view].toolbarCollapsed = collapsed;
      toolbarTransitionActive = true;
      applyToolbarState();
      updateSidebarBounds('toolbar', false);
      emitResize('toolbar');
    }

    function bounds() {
      const viewport = workspace.getBoundingClientRect().width || root.innerWidth;
      // Keep 200px for the canvas when a 360px sidebar fits; very small windows
      // retain at least a 44px edge with the external collapse control reachable.
      const canvasSpace = Math.min(200, Math.max(44, viewport - 360));
      const maximum = Math.max(0, Math.min(760, viewport - canvasSpace));
      return { minimum: Math.min(360, maximum), maximum };
    }

    function updateSidebarBounds(reason, notify = true) {
      if (state.view === 'split') {
        updateBottomBounds(reason, notify);
        return;
      }
      const { minimum, maximum } = bounds();
      // CSS combines this bound with its responsive default or the user's width.
      // Never overwrite the preference with a temporarily constrained width.
      workspace.style.setProperty('--sidebar-maximum-width', `${maximum}px`);
      if (preferredSidebarWidth !== null) {
        workspace.style.setProperty('--sidebar-width', `${Math.max(minimum, preferredSidebarWidth)}px`);
      }
      const width = preferredSidebarWidth ?? sidebar.getBoundingClientRect().width;
      const next = Math.round(Math.min(maximum, Math.max(minimum, width)));
      resizeHandle.setAttribute('aria-valuemin', String(minimum));
      resizeHandle.setAttribute('aria-valuemax', String(maximum));
      resizeHandle.setAttribute('aria-valuenow', String(next));
      resizeHandle.setAttribute('aria-orientation', 'vertical');
      resizeHandle.setAttribute('aria-label', '调整查询区宽度');
      if (notify && !state.sidebarCollapsed) emitResize(reason);
    }

    function bottomBounds() {
      const height = Math.max(0, workspace.getBoundingClientRect().height || 0);
      const maximum = Math.max(0, Math.floor(height - Math.min(140, height)));
      return { minimum: Math.min(240, maximum), maximum, height };
    }

    function updateBottomBounds(reason, notify = true) {
      const { minimum, maximum, height } = bottomBounds();
      if (preferredBottomHeight === null) preferredBottomHeight = Math.round(height * 0.44);
      const next = Math.round(Math.min(maximum, Math.max(minimum, preferredBottomHeight)));
      workspace.style.setProperty('--workspace-bottom-height', `${next}px`);
      resizeHandle.setAttribute('aria-valuemin', String(minimum));
      resizeHandle.setAttribute('aria-valuemax', String(maximum));
      resizeHandle.setAttribute('aria-valuenow', String(next));
      resizeHandle.setAttribute('aria-orientation', 'horizontal');
      resizeHandle.setAttribute('aria-label', '调整查询区高度');
      if (notify && !state.sidebarCollapsed) emitResize(reason);
    }

    function setBottomHeight(height, reason = 'sidebar-height') {
      const { minimum, maximum } = bottomBounds();
      preferredBottomHeight = Math.round(Math.min(maximum, Math.max(minimum, height)));
      updateBottomBounds(reason);
    }

    function setSidebarWidth(width, reason = 'sidebar-width') {
      const { minimum, maximum } = bounds();
      preferredSidebarWidth = Math.round(Math.min(maximum, Math.max(minimum, width)));
      updateSidebarBounds(reason);
    }

    sidebarToggle.addEventListener('click', () => setSidebarCollapsed(!state.sidebarCollapsed));
    toolbarToggle.addEventListener('click', () => setToolbarCollapsed(!state.toolbarCollapsed));
    sidebar.addEventListener('transitionend', event => {
      if (event.target === sidebar && (event.propertyName === 'width' || event.propertyName === 'height')) {
        finishSidebarTransition();
      }
    });
    function finishSidebarTransition() {
      if (!sidebarTransitionActive) return;
      sidebarTransitionActive = false;
      root.clearTimeout?.(sidebarEndTimer); sidebarEndTimer = null;
      updateSidebarBounds('sidebar', false);
      emitResize('sidebar', 'end');
    }
    toolbar.addEventListener('transitionend', event => {
      if (toolbarTransitionActive && event.target === toolbar && event.propertyName === 'height') {
        toolbarTransitionActive = false;
        // Keep the final size correct even where ResizeObserver is unavailable.
        updateSidebarBounds('toolbar', false);
        emitResize('toolbar', 'end');
      }
    });
    for (const [view, option] of Object.entries(viewOptions)) {
      option?.addEventListener('click', () => {
        setView(view);
        viewButton?.focus();
      });
    }

    resizeHandle.addEventListener('pointerdown', event => {
      if (drag || state.sidebarCollapsed || event.button !== 0 || event.isPrimary === false) return;
      const split = state.view === 'split';
      drag = { pointerId: event.pointerId, split, startX: event.clientX, startY: event.clientY,
        startWidth: sidebar.getBoundingClientRect().width,
        startHeight: sidebar.getBoundingClientRect().height };
      resizeHandle.setPointerCapture(event.pointerId);
      workspace.classList.add('is-resizing');
      doc.body.classList.add('workspace-resizing');
      doc.body.classList.toggle('workspace-resizing-vertical', split);
      event.preventDefault();
    });
    resizeHandle.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      if (drag.split) setBottomHeight(drag.startHeight + drag.startY - event.clientY);
      else setSidebarWidth(drag.startWidth + event.clientX - drag.startX);
    });
    function endResize(event, notify = true) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const pointerId = drag.pointerId;
      const split = drag.split;
      drag = null;
      workspace.classList.remove('is-resizing');
      doc.body.classList.remove('workspace-resizing');
      doc.body.classList.remove('workspace-resizing-vertical');
      if (resizeHandle.hasPointerCapture?.(pointerId)) resizeHandle.releasePointerCapture(pointerId);
      if (!split) saveSidebarWidth();
      if (notify) emitResize(split ? 'sidebar-height' : 'sidebar-width', 'end');
    }
    resizeHandle.addEventListener('pointerup', endResize);
    resizeHandle.addEventListener('pointercancel', endResize);
    resizeHandle.addEventListener('lostpointercapture', endResize);
    resizeHandle.addEventListener('keydown', event => {
      if (state.sidebarCollapsed) return;
      const split = state.view === 'split';
      const direction = split
        ? (event.key === 'ArrowUp' ? 1 : event.key === 'ArrowDown' ? -1 : 0)
        : (event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0);
      if (!direction) return;
      event.preventDefault();
      if (split) setBottomHeight(sidebar.getBoundingClientRect().height + direction * (event.shiftKey ? 50 : 20));
      else {
        setSidebarWidth(sidebar.getBoundingClientRect().width + direction * (event.shiftKey ? 50 : 20));
        saveSidebarWidth();
      }
    });
    workspace.setAttribute('data-workspace-view', state.view);
    applySidebarState();
    applyToolbarState();
    updateViewMenu();
    updateSidebarBounds('mount', false);

    root.addEventListener('resize', () => updateSidebarBounds('window'));
    if (typeof root.ResizeObserver === 'function') {
      let { width: observedWidth, height: observedHeight } = workspace.getBoundingClientRect();
      const resizeObserver = new root.ResizeObserver(() => {
        const { width, height } = workspace.getBoundingClientRect();
        const changed = state.view === 'split' ? height !== observedHeight : width !== observedWidth;
        observedWidth = width; observedHeight = height;
        // Toolbar and host-slot resizing need the same bounds as browser resizing.
        // updateSidebarBounds clamps display only, retaining the user's preference.
        if (changed) updateSidebarBounds('workspace-size');
      });
      resizeObserver.observe(workspace);
    }

    const controller = {
      setSidebarCollapsed,
      setToolbarCollapsed,
      setView,
      toggleFocus,
      getState: () => ({ ...state }),
    };
    workspace.fundsWorkspaceShell = controller;
    return controller;
  }

  root.FundsWorkspaceShell = { mount };
  if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', () => mount(), { once: true });
  else mount();
})(window);
