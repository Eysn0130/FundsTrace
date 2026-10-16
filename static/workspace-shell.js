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
    const popovers = [
      { button: doc.getElementById('btnDisplayActions'), panel: doc.getElementById('toolbarDisplayExtra') },
      { button: doc.getElementById('btnMainActions'), panel: doc.getElementById('toolbarMainActions') },
    ].filter(item => item.button && item.panel);
    let drag = null;
    let preferredSidebarWidth = null;

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

    function closePopovers(returnFocus = false) {
      for (const { button, panel } of popovers) {
        const wasOpen = panel.classList.contains('is-open');
        const restoreFocus = state.toolbarCollapsed && wasOpen && (returnFocus || panel.contains(doc.activeElement));
        // Move focus before making its current container inert. Resize and scroll
        // close these panels too, and must not leave their focused controls hidden.
        if (restoreFocus && button.isConnected !== false && !button.disabled && !button.inert &&
            (!button.getClientRects || button.getClientRects().length)) button.focus();
        panel.classList.remove('is-open');
        button.setAttribute('aria-expanded', 'false');
        panel.inert = state.toolbarCollapsed;
        panel.setAttribute('aria-hidden', String(state.toolbarCollapsed));
      }
    }

    function setSidebarCollapsed(collapsed) {
      collapsed = Boolean(collapsed);
      if (collapsed === state.sidebarCollapsed) return;
      if (collapsed && sidebar.contains(doc.activeElement)) sidebarToggle.focus();
      state.sidebarCollapsed = collapsed;
      workspace.classList.toggle('sidebar-collapsed', collapsed);
      sidebar.inert = collapsed;
      sidebar.setAttribute('aria-hidden', String(collapsed));
      resizeHandle.tabIndex = collapsed ? -1 : 0;
      updateToggle(sidebarToggle, !collapsed, collapsed ? '展开查询区' : '收起查询区', collapsed ? 'chevron-right' : 'chevron-left');
      updateSidebarBounds('sidebar', false);
      emitResize('sidebar');
    }

    function setToolbarCollapsed(collapsed) {
      collapsed = Boolean(collapsed);
      if (collapsed === state.toolbarCollapsed) return;
      state.toolbarCollapsed = collapsed;
      toolbar.classList.toggle('is-collapsed', collapsed);
      closePopovers();
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
      workspace.style.setProperty('--sidebar-width', `${preferredSidebarWidth}px`);
      updateSidebarBounds(reason);
    }

    sidebarToggle.addEventListener('click', () => setSidebarCollapsed(!state.sidebarCollapsed));
    toolbarToggle.addEventListener('click', () => setToolbarCollapsed(!state.toolbarCollapsed));
    sidebar.addEventListener('transitionend', event => {
      if (event.target === sidebar && event.propertyName === 'width') {
        updateSidebarBounds('sidebar', false);
        emitResize('sidebar', 'end');
      }
    });
    toolbar.addEventListener('transitionend', event => {
      if (event.target === toolbar && event.propertyName === 'height') emitResize('toolbar', 'end');
    });

    resizeHandle.addEventListener('pointerdown', event => {
      if (state.sidebarCollapsed || event.button !== 0) return;
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
    function endResize(event) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      drag = null;
      workspace.classList.remove('is-resizing');
      doc.body.classList.remove('workspace-resizing');
      emitResize('sidebar-width', 'end');
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
    });
    updateSidebarBounds('mount', false);

    for (const item of popovers) {
      const { button, panel } = item;
      button.addEventListener('click', () => {
        const opening = !panel.classList.contains('is-open');
        closePopovers();
        if (!state.toolbarCollapsed || !opening) return;
        panel.inert = false;
        panel.setAttribute('aria-hidden', 'false');
        panel.classList.add('is-open');
        button.setAttribute('aria-expanded', 'true');
        const rect = button.getBoundingClientRect();
        const panelWidth = panel.getBoundingClientRect().width;
        panel.style.left = `${Math.max(12, Math.min(rect.left, root.innerWidth - panelWidth - 12))}px`;
        panel.style.top = `${rect.bottom + 8}px`;
      });
    }
    doc.addEventListener('pointerdown', event => {
      if (!popovers.some(({ button, panel }) => button.contains(event.target) || panel.contains(event.target))) closePopovers();
    });
    doc.addEventListener('keydown', event => {
      if (event.key === 'Escape' && popovers.some(({ panel }) => panel.classList.contains('is-open'))) {
        event.preventDefault(); closePopovers(true);
      }
    });
    root.addEventListener('resize', () => {
      closePopovers();
      updateSidebarBounds('window');
    });
    doc.getElementById('graphToolbar')?.addEventListener('scroll', () => closePopovers());

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
