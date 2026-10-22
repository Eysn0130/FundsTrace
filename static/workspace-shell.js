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

    const state = { sidebarCollapsed: false, toolbarCollapsed: false };
    let drag = null;
    let preferredSidebarWidth = null;
    let savedSidebarWidth = null;
    let sidebarEndTimer = null;
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

    function setSidebarCollapsed(collapsed) {
      collapsed = Boolean(collapsed);
      if (collapsed === state.sidebarCollapsed) return;
      if (drag) endResize({ pointerId: drag.pointerId }, false);
      if (collapsed && sidebar.contains(doc.activeElement)) sidebarToggle.focus();
      state.sidebarCollapsed = collapsed;
      // Camera reads the starting geometry before CSS changes the sidebar.
      emitResize('sidebar', 'prepare');
      workspace.classList.toggle('sidebar-collapsed', collapsed);
      sidebar.inert = collapsed;
      sidebar.setAttribute('aria-hidden', String(collapsed));
      resizeHandle.tabIndex = collapsed ? -1 : 0;
      updateToggle(sidebarToggle, !collapsed, collapsed ? '展开查询区' : '收起查询区', collapsed ? 'chevron-right' : 'chevron-left');
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
      toolbar.classList.toggle('is-collapsed', collapsed);
      updateToggle(toolbarToggle, !collapsed, collapsed ? '展开顶部工具带' : '收起顶部工具带', collapsed ? 'chevron-down' : 'chevron-up');
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
      if (notify && !state.sidebarCollapsed) emitResize(reason);
    }

    function setSidebarWidth(width, reason = 'sidebar-width') {
      const { minimum, maximum } = bounds();
      preferredSidebarWidth = Math.round(Math.min(maximum, Math.max(minimum, width)));
      updateSidebarBounds(reason);
    }

    sidebarToggle.addEventListener('click', () => setSidebarCollapsed(!state.sidebarCollapsed));
    toolbarToggle.addEventListener('click', () => setToolbarCollapsed(!state.toolbarCollapsed));
    sidebar.addEventListener('transitionend', event => {
      if (event.target === sidebar && event.propertyName === 'width') {
        finishSidebarTransition();
      }
    });
    function finishSidebarTransition() {
      root.clearTimeout?.(sidebarEndTimer); sidebarEndTimer = null;
      updateSidebarBounds('sidebar', false);
      emitResize('sidebar', 'end');
    }
    toolbar.addEventListener('transitionend', event => {
      if (event.target === toolbar && event.propertyName === 'height') emitResize('toolbar', 'end');
    });

    resizeHandle.addEventListener('pointerdown', event => {
      if (drag || state.sidebarCollapsed || event.button !== 0 || event.isPrimary === false) return;
      drag = { pointerId: event.pointerId, startX: event.clientX, startWidth: sidebar.getBoundingClientRect().width };
      resizeHandle.setPointerCapture(event.pointerId);
      workspace.classList.add('is-resizing');
      doc.body.classList.add('workspace-resizing');
      event.preventDefault();
    });
    resizeHandle.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      setSidebarWidth(drag.startWidth + event.clientX - drag.startX);
    });
    function endResize(event, notify = true) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const pointerId = drag.pointerId;
      drag = null;
      workspace.classList.remove('is-resizing');
      doc.body.classList.remove('workspace-resizing');
      if (resizeHandle.hasPointerCapture?.(pointerId)) resizeHandle.releasePointerCapture(pointerId);
      saveSidebarWidth();
      if (notify) emitResize('sidebar-width', 'end');
    }
    resizeHandle.addEventListener('pointerup', endResize);
    resizeHandle.addEventListener('pointercancel', endResize);
    resizeHandle.addEventListener('lostpointercapture', endResize);
    resizeHandle.addEventListener('keydown', event => {
      if (state.sidebarCollapsed) return;
      const direction = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : 0;
      if (!direction) return;
      event.preventDefault();
      setSidebarWidth(sidebar.getBoundingClientRect().width + direction * (event.shiftKey ? 50 : 20));
      saveSidebarWidth();
    });
    updateSidebarBounds('mount', false);

    root.addEventListener('resize', () => updateSidebarBounds('window'));

    const controller = {
      setSidebarCollapsed,
      setToolbarCollapsed,
      getState: () => ({ ...state }),
    };
    workspace.fundsWorkspaceShell = controller;
    return controller;
  }

  root.FundsWorkspaceShell = { mount };
  if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', () => mount(), { once: true });
  else mount();
})(window);
