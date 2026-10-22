/* One set of controls, moved between an Office-style ribbon and a compact strip. */
(function (root) {
  'use strict';
  function mount(doc = root.document) {
    const toolbar = doc.getElementById('editorToolbar');
    if (!toolbar) return null;
    if (toolbar.fundsRibbon) return toolbar.fundsRibbon;
    const tabs = Array.from(doc.querySelectorAll('[data-ribbon-tab]'));
    const panels = Array.from(doc.querySelectorAll('.ribbon-panel'));
    const bundles = Array.from(doc.querySelectorAll('[data-ribbon-bundle]'));
    const triggers = Array.from(doc.querySelectorAll('[data-ribbon-popover]'));
    const storage = doc.getElementById('ribbonStorage');
    const compact = doc.getElementById('ribbonCompact');
    const topbar = doc.getElementById('topbar');
    const panelContainer = doc.getElementById('ribbonPanels');
    const tabbar = doc.getElementById('ribbonTabs')?.parentElement;
    const toolbarHost = toolbar.parentElement;
    const status = { tab: 'common', collapsed: false, selectionCount: 0, edgeSelectionCount: 0, busy: false, cameraReady:false };
    const commandOverrides = new Map();
    const mixedOptions = new WeakMap(), mixedTitles = new WeakMap();
    let currentPopover = null, toolbarScale = null;
    const popoverHomes = new Map();
    for (const trigger of triggers) {
      const panel = doc.getElementById(trigger.dataset.ribbonPopover);
      if (panel) popoverHomes.set(panel, { parent: panel.parentElement, next: panel.nextSibling });
    }

    function updateToolbarFit(notify = true) {
      // The navigation rail occupies viewport space outside the toolbar's slot.
      // Measure the unscaled host; reading the toolbar would feed its own zoom
      // back into the next measurement. Hidden slots wait for visible geometry.
      const hostWidth = toolbarHost?.clientWidth;
      const width = Number.isFinite(hostWidth) ? hostWidth : doc.documentElement?.clientWidth || root.innerWidth || 1280;
      if (width <= 0) return;
      const scale = Math.min(1, width / 1280);
      const changed = toolbarScale !== null && scale !== toolbarScale;
      const announce = phase => root.dispatchEvent(new root.CustomEvent('fundstrace:workspace-resize', {
        detail: { ...doc.getElementById('workspace')?.fundsWorkspaceShell?.getState?.(),
          toolbarCollapsed: status.collapsed, reason: 'toolbar-fit', phase }
      }));
      if (changed && notify) announce('prepare');
      toolbarScale = scale;
      for (const bar of [topbar, toolbar]) {
        if (!bar) continue;
        bar.style.setProperty('--toolbar-fit-width', `${Math.max(1280, width)}px`);
        bar.style.setProperty('--toolbar-fit-scale', String(scale));
      }
      if (changed && notify) announce('start');
    }

    const adaptiveInline = panel => panel.classList.contains('ribbon-adaptive-popover') && !status.collapsed;
    function validFocus(node) {
      return node && node.isConnected !== false && !node.disabled && !node.inert &&
        (!node.getClientRects || node.getClientRects().length);
    }
    function closePopovers(restore = false) {
      for (const trigger of triggers) {
        const panel = doc.getElementById(trigger.dataset.ribbonPopover);
        if (!panel) continue;
        const open = trigger.getAttribute('aria-expanded') === 'true';
        if (open && (restore || panel.contains(doc.activeElement)) && validFocus(trigger)) trigger.focus();
        trigger.setAttribute('aria-expanded', 'false');
        panel.classList.remove('is-open');
        const inline = adaptiveInline(panel);
        panel.setAttribute('role', inline ? 'group' : 'dialog');
        panel.hidden = !inline;
        panel.inert = panel.hidden;
        const home = popoverHomes.get(panel);
        if (home && panel.parentElement !== home.parent) {
          home.parent.insertBefore(panel, home.next?.parentElement === home.parent ? home.next : null);
        }
      }
      currentPopover = null;
    }
    function placePopover(panel, trigger) {
      const rect = trigger.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      const left = Math.max(8, Math.min(rect.left, root.innerWidth - panelRect.width - 8));
      const top = Math.max(8, Math.min(rect.bottom + 6, root.innerHeight - panelRect.height - 8));
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
      panel.style.maxHeight = `${Math.max(120, root.innerHeight - top - 8)}px`;
    }
    function openPopover(trigger) {
      root.FundsToolbarMenu?.close('ribbon');
      const panel = doc.getElementById(trigger.dataset.ribbonPopover);
      if (!panel || adaptiveInline(panel)) return;
      const opening = trigger.getAttribute('aria-expanded') !== 'true';
      closePopovers();
      if (!opening) return;
      // Like the shared toolbar menus, floating surfaces use unscaled viewport
      // coordinates. Return the same controls home when closing or expanding.
      doc.body.appendChild(panel);
      panel.hidden = false;
      panel.inert = false;
      panel.classList.add('is-open');
      trigger.setAttribute('aria-expanded', 'true');
      placePopover(panel, trigger);
      currentPopover = { panel, trigger };
    }
    function render() {
      closePopovers();
      for (const panel of panels) {
        const active = !status.collapsed && panel.id === `ribbonPanel-${status.tab}`;
        panel.hidden = !active;
        panel.inert = !active;
      }
      for (const tab of tabs) {
        const active = tab.dataset.ribbonTab === status.tab;
        tab.setAttribute('aria-selected', String(active));
        tab.tabIndex = active ? 0 : -1;
      }
      if (tabbar) { tabbar.hidden = status.collapsed; tabbar.inert = status.collapsed; }
      if (panelContainer) { panelContainer.hidden = status.collapsed; panelContainer.inert = status.collapsed; }
      compact.hidden = !status.collapsed;
      compact.inert = !status.collapsed;
      for (const bundle of bundles) {
        const name = bundle.dataset.ribbonBundle;
        const parent = status.collapsed ?
          compact.querySelector(`[data-ribbon-slot="${name}"]`) || doc.querySelector(`[data-ribbon-compact-slot="${name}"]`) :
          doc.getElementById(`ribbonPanel-${bundle.dataset.ribbonHome}`)?.querySelector(`[data-ribbon-slot="${name}"]`);
        (parent || storage).appendChild(bundle);
      }
      // Adaptive panels become inline only in the expanded ribbon.
      closePopovers();
      const active = doc.activeElement;
      if (active && toolbar.contains(active) && active.closest?.('[hidden]')) {
        (status.collapsed ? compact.querySelector('[data-ribbon-collapse]') : tabs.find(tab => tab.dataset.ribbonTab === status.tab))?.focus();
      }
    }
    function activateTab(name, focus = false) {
      root.FundsToolbarMenu?.close('tab');
      if (!tabs.some(tab => tab.dataset.ribbonTab === name)) return false;
      status.tab = name;
      render();
      if (focus) tabs.find(tab => tab.dataset.ribbonTab === name)?.focus();
      return true;
    }
    function setCollapsed(collapsed) {
      root.FundsToolbarMenu?.close('collapse');
      if (status.collapsed === Boolean(collapsed)) return;
      status.collapsed = Boolean(collapsed);
      render();
    }
    function sendCommand(command, value) {
      doc.dispatchEvent(new root.CustomEvent('funds:ribbon-command', { detail: { command, value } }));
    }
    const selectionCommands = new Set(['clearSelection','invertSelection','inspectSelection','accountDetails','viewFlows','fitSelection','focusSelection','hideSelection','showOnlySelection','alignLeft','alignTop','distributeHorizontal','distributeVertical','emphasize','annotateSelection']);
    const edgeSelectionCommands = new Set(['clearSelection','inspectSelection','viewFlows','fitSelection','focusSelection']);
    const cameraCommands=new Set(['zoomIn','zoomOut','zoomReset','fitSelection','focusSelection','locateNew']);
    function refreshAvailability() {
      for (const node of doc.querySelectorAll('[data-ribbon-command]')) {
        if (node.dataset.planned === 'true') continue;
        const command = node.dataset.ribbonCommand;
        let allowed = commandOverrides.has(command) ? commandOverrides.get(command) :
          !selectionCommands.has(command) || status.selectionCount > 0 || status.edgeSelectionCount > 0 && edgeSelectionCommands.has(command);
        if(command==='inspectSelection'){
          const singleNode=status.selectionCount===1&&status.edgeSelectionCount===0;
          const singleEdge=status.selectionCount===0&&status.edgeSelectionCount===1;
          allowed=allowed&&(singleNode||singleEdge);
          const label=singleNode?'查看账户详情':singleEdge?'查看资金流向':'查看详情';
          node.setAttribute('aria-label',label);
          node.title=status.busy?'画布更新中，稍后查看详情':singleNode||singleEdge
            ?`${label}；聚焦画布后按 Enter`:'先选择一个节点或一条关系';
        }
        node.disabled = status.busy&&!(status.cameraReady&&cameraCommands.has(command)) || !allowed;
      }
      const output = doc.getElementById('ribbonSelectionStatus');
      if (output) output.textContent = status.busy ? '正在更新画布…' : status.selectionCount ? `已选 ${status.selectionCount} 个节点` :
        status.edgeSelectionCount ? `已选 ${status.edgeSelectionCount} 条关系` : '未选择节点';
    }
    function setStatus(values = {}) {
      if ('selectionCount' in values) status.selectionCount = Math.max(0, Number(values.selectionCount) || 0);
      if ('edgeSelectionCount' in values) status.edgeSelectionCount = Math.max(0, Number(values.edgeSelectionCount) || 0);
      if ('busy' in values) status.busy = Boolean(values.busy);
      if ('cameraReady' in values) status.cameraReady=Boolean(values.cameraReady);
      refreshAvailability();
    }
    function setCommandEnabled(command, enabled) {
      commandOverrides.set(command, Boolean(enabled));
      refreshAvailability();
    }
    function setValues(values = {}) {
      for (const [id, value] of Object.entries(values)) {
        const node = doc.getElementById(id);
        if (!node) continue;
        const mixed = value !== null && typeof value === 'object' && value.mixed === true;
        node.setAttribute('data-mixed', String(mixed));
        if (!mixedTitles.has(node)) mixedTitles.set(node, node.getAttribute('title') || '');
        node.setAttribute('title', mixed ? '混合值：所选对象的此属性不同；修改后统一应用' : mixedTitles.get(node));
        if (node.hasAttribute('aria-pressed')) node.setAttribute('aria-pressed', mixed ? 'mixed' : String(Boolean(value)));
        else if (mixed && node.tagName === 'SELECT') {
          let option = mixedOptions.get(node);
          if (!option) {
            option = doc.createElement('option'); option.value = ''; option.textContent = '混合值';
            option.disabled = true; option.hidden = true;
            node.appendChild(option); mixedOptions.set(node, option);
          }
          node.value = '';
        } else if (mixed) {
          // Colour inputs have no native indeterminate value. The data attribute
          // styles their existing wrapper; keep the picker itself usable.
          node.setAttribute('aria-description', '混合值，修改后应用到全部所选对象');
        } else {
          node.setAttribute('aria-description', '');
          if (node.type === 'checkbox') node.checked = Boolean(value);
          else node.value = String(value ?? '');
        }
      }
    }
    for (const tab of tabs) {
      tab.addEventListener('click', () => activateTab(tab.dataset.ribbonTab));
      tab.addEventListener('keydown', event => {
        const index = tabs.indexOf(tab);
        let next = null;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === null) return;
        event.preventDefault();
        activateTab(tabs[next].dataset.ribbonTab, true);
      });
    }
    for (const button of doc.querySelectorAll('[data-ribbon-collapse]')) button.addEventListener('click', () => {
      const collapsed = button.dataset.ribbonCollapse === 'true';
      doc.getElementById('workspace')?.fundsWorkspaceShell?.setToolbarCollapsed(collapsed);
    });
    for (const button of doc.querySelectorAll('[data-ribbon-expand-tab]')) button.addEventListener('click', () => {
      activateTab(button.dataset.ribbonExpandTab);
      doc.getElementById('workspace')?.fundsWorkspaceShell?.setToolbarCollapsed(false);
    });
    for (const trigger of triggers) trigger.addEventListener('click', () => openPopover(trigger));
    for (const button of doc.querySelectorAll('[data-ribbon-proxy]')) button.addEventListener('click', () => {
      const target = doc.getElementById(button.dataset.ribbonProxy);
      if (!target || target.disabled) return;
      closePopovers();
      target.click();
    });
    for (const node of doc.querySelectorAll('[data-ribbon-command]')) {
      const form = node.tagName === 'SELECT' || node.tagName === 'INPUT';
      node.addEventListener(form ? 'change' : 'click', () => {
        if (node.disabled || node.dataset.planned === 'true') return;
        let value;
        if (form) value = node.type === 'checkbox' ? node.checked : node.value;
        else if (node.id && node.hasAttribute('aria-pressed')) {
          value = node.getAttribute('aria-pressed') !== 'true';
          node.setAttribute('aria-pressed', String(value));
        }
        // The disabled mixed-value placeholder is a display state, never a font
        // or size command (including synthetic or programmatic change events).
        if (form && node.tagName === 'SELECT' && node.getAttribute('data-mixed') === 'true' && node.value === '') return;
        if (node.dataset.ribbonCommand === 'findNode') value = doc.getElementById('graphFindInput').value.trim();
        sendCommand(node.dataset.ribbonCommand, value);
      });
    }
    doc.addEventListener('pointerdown', event => {
      if (currentPopover && !currentPopover.panel.contains(event.target) && !currentPopover.trigger.contains(event.target)) closePopovers();
    });
    doc.addEventListener('keydown', event => {
      if (event.key === 'Escape' && currentPopover) {
        event.preventDefault();
        event.stopPropagation();
        closePopovers(true);
      }
    });
    root.addEventListener('resize', () => { closePopovers(); updateToolbarFit(); });
    if (root.ResizeObserver && toolbarHost) {
      // One observer per mounted ribbon, matching the window listeners' lifetime.
      // The mount guard above also prevents duplicate observers on re-entry.
      const fitObserver = new root.ResizeObserver(() => { closePopovers(); updateToolbarFit(); });
      fitObserver.observe(toolbarHost);
    }
    for (const node of [...panels, compact]) node.addEventListener('scroll', () => closePopovers());
    root.addEventListener('fundstrace:workspace-resize', event => setCollapsed(event.detail.toolbarCollapsed));
    const api = { activateTab, setCollapsed, closePopovers, setStatus, setValues, setCommandEnabled, getState: () => ({ ...status }) };
    toolbar.fundsRibbon = api;
    root.FundsRibbon = api;
    updateToolbarFit(false);
    render();
    refreshAvailability();
    return api;
  }
  root.FundsRibbon = { mount };
  // All controls exist before this script; moving them before business binding preserves their identity.
  if (root.document.getElementById('editorToolbar')) mount();
  else root.document.addEventListener('DOMContentLoaded', () => mount(), { once: true });
})(window);
