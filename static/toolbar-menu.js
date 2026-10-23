/* Anchored toolbar commands. The shell owns interaction, never business state. */
(function (root) {
  'use strict';
  const instances = new WeakMap();

  function mount(doc = root.document) {
    if (!doc?.body) return null;
    if (instances.has(doc)) return instances.get(doc);
    let current = null, frame = null, dismissed = null, lastReason = null;
    const ownedPanels = new WeakSet();
    const menuSelector = '[role="menuitem"],[role="menuitemcheckbox"],[role="menuitemradio"]';
    const stop = event => { event.preventDefault(); event.stopImmediatePropagation(); };
    const visible = node => {
      if (!node || node.isConnected === false || node.hidden || node.closest?.('[hidden],[inert]')) return false;
      if (node.getClientRects && !node.getClientRects().length) return false;
      const style = root.getComputedStyle?.(node);
      return !style || style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse';
    };
    const focus = node => { if (visible(node) && !node.disabled) node.focus({ preventScroll: true }); };
    const unavailable = node => node.disabled || node.getAttribute('aria-disabled') === 'true' || node.getAttribute('aria-busy') === 'true';
    const items = panel => Array.from(panel.querySelectorAll(menuSelector)).filter(visible);
    const formItems = panel => Array.from(panel.querySelectorAll('input,select,textarea,button,a[href],[tabindex],[contenteditable="true"]'))
      .filter(node => visible(node) && !unavailable(node) && node.tabIndex >= 0);
    const triggerFor = target => target?.closest?.('[data-toolbar-menu]');
    const graphTarget = target => !!target?.closest?.('#graphContainer,#graphMinimapPanel,#graphMinimap');

    function prepare(trigger) {
      const panel = doc.getElementById(trigger?.getAttribute('data-toolbar-menu'));
      if (!panel) return null;
      const form = panel.getAttribute('data-toolbar-panel') === 'form';
      if (!ownedPanels.has(panel) && form) panel.addEventListener('keydown', event => event.stopPropagation());
      ownedPanels.add(panel);
      trigger.setAttribute('aria-haspopup', form ? 'dialog' : 'menu');
      trigger.setAttribute('aria-controls', panel.id);
      trigger.setAttribute('aria-expanded', String(current?.trigger === trigger));
      panel.setAttribute('role', form ? 'dialog' : 'menu');
      panel.classList.add('toolbar-menu');
      if (!form) for (const item of panel.querySelectorAll(menuSelector)) item.tabIndex = -1;
      // A body portal avoids ribbon clipping and the minimap stacking context.
      if (panel.parentElement !== doc.body) doc.body.appendChild(panel);
      if (current?.panel !== panel) { panel.hidden = true; panel.inert = true; }
      return panel;
    }
    function announce(open, reason, entry) {
      doc.dispatchEvent(new root.CustomEvent('funds:toolbar-menu-change', {
        detail: { open, reason, triggerId: entry.trigger.id || '', menuId: entry.panel.id }
      }));
    }
    function close(reason = 'programmatic') {
      if (!current) return false;
      const previous = current;
      current = null; lastReason = reason;
      if (frame !== null) { root.cancelAnimationFrame?.(frame); frame = null; }
      previous.trigger.setAttribute('aria-expanded', 'false');
      previous.panel.classList.remove('is-open');
      previous.panel.hidden = true; previous.panel.inert = true;
      // Activation hands focus to its next form; outside clicks own their focus.
      if (reason === 'escape' || reason === 'close') focus(previous.trigger);
      announce(false, reason, previous);
      return true;
    }
    function place(panel, trigger) {
      const width = Math.max(0, root.innerWidth || doc.documentElement?.clientWidth || 0);
      const height = Math.max(0, root.innerHeight || doc.documentElement?.clientHeight || 0);
      const mx = Math.min(8, width / 2), my = Math.min(8, height / 2), gap = 6;
      panel.style.maxWidth = `${Math.max(0, width - mx * 2)}px`;
      panel.style.maxHeight = `${Math.max(0, height - my * 2)}px`;
      const anchor = trigger.getBoundingClientRect(), box = panel.getBoundingClientRect();
      const below = Math.max(0, height - my - anchor.bottom - gap);
      const above = Math.max(0, anchor.top - gap - my);
      const flip = box.height > below && above > below;
      const available = Math.min(Math.max(0, height - my * 2), flip ? above : below);
      panel.style.maxHeight = `${available}px`;
      const panelHeight = Math.min(box.height, available), panelWidth = Math.min(box.width, Math.max(0, width - mx * 2));
      panel.style.left = `${Math.max(mx, Math.min(anchor.left, width - mx - panelWidth))}px`;
      panel.style.top = `${Math.max(my, Math.min(flip ? anchor.top - gap - panelHeight : anchor.bottom + gap, height - my - panelHeight))}px`;
      panel.style.transformOrigin = flip ? 'bottom left' : 'top left';
      panel.setAttribute('data-placement', flip ? 'top' : 'bottom');
      return [anchor.left, anchor.top, anchor.width, anchor.height].join(',');
    }
    function watchAnchor() {
      if (!current || !root.requestAnimationFrame) return;
      frame = root.requestAnimationFrame(() => {
        frame = null;
        if (!current) return;
        if (!visible(current.trigger) || current.panel.isConnected === false || current.panel.hidden || current.panel.inert) {
          close('anchor-hidden'); return;
        }
        const rect = current.trigger.getBoundingClientRect();
        if ([rect.left, rect.top, rect.width, rect.height].join(',') !== current.anchor) { close('anchor-moved'); return; }
        watchAnchor();
      });
    }
    function open(value, edge = 'first') {
      const trigger = typeof value === 'string' ? doc.getElementById(value) : value;
      if (!visible(trigger) || unavailable(trigger)) return false;
      const panel = prepare(trigger);
      if (!panel) return false;
      if (current?.trigger === trigger) return true;
      close('switch');
      root.FundsRibbon?.closePopovers?.();
      dismissed = null;
      panel.hidden = false; panel.inert = false;
      panel.classList.add('is-open'); trigger.setAttribute('aria-expanded', 'true');
      const form = panel.getAttribute('data-toolbar-panel') === 'form';
      current = { trigger, panel, form, anchor: place(panel, trigger) }; lastReason = 'open';
      const commands = form ? formItems(panel) : items(panel);
      if (!commands.length) panel.tabIndex = -1;
      const initial = form ? commands.find(node => node.matches('input,select,textarea')) || commands[0]
        : edge === 'last' ? commands[commands.length - 1] : commands[0];
      focus(initial || panel);
      announce(true, 'open', current); watchAnchor();
      return true;
    }
    function tabOut(event) {
      const trigger = current.trigger;
      close('tab');
      if (event.shiftKey) { stop(event); focus(trigger); return; }
      const candidates = Array.from(doc.querySelectorAll('a[href],button,input,select,textarea,[tabindex],[contenteditable="true"]'))
        .filter(node => visible(node) && !node.disabled && node.tabIndex >= 0)
        .map((node, index) => ({ node, index, order: node.tabIndex > 0 ? node.tabIndex : Infinity }))
        .sort((a, b) => a.order - b.order || a.index - b.index).map(item => item.node);
      const next = candidates[candidates.indexOf(trigger) + 1];
      if (next) { stop(event); focus(next); }
      else focus(trigger); // Let the browser leave the document at its tab boundary.
    }

    doc.addEventListener('click', event => {
      const trigger = triggerFor(event.target);
      if (trigger) {
        stop(event);
        if (current?.trigger === trigger) close('toggle');
        else open(trigger);
        return;
      }
      const item = event.target?.closest?.(menuSelector);
      const panel = item?.closest?.('.toolbar-menu');
      // Dialogs reuse the same menu styles but own their local focus and
      // activation. Only intercept panels mounted by this toolbar controller.
      if (!panel || !ownedPanels.has(panel)) return;
      if (!current || !current.panel.contains(item) || unavailable(item)) { stop(event); return; }
      // The existing onclick runs once, after the menu has relinquished focus.
      close('activate');
    }, true);
    doc.addEventListener('keydown', event => {
      if (event.isComposing || event.keyCode === 229) return;
      const trigger = triggerFor(event.target), key = event.key;
      if (trigger && ['Enter', ' ', 'Spacebar', 'ArrowDown', 'ArrowUp'].includes(key)) {
        stop(event);
        if (!event.repeat) open(trigger, key === 'ArrowUp' ? 'last' : 'first');
        return;
      }
      if (!current) return;
      // A nested combobox owns its first Escape; the next closes this menu.
      if (key === 'Escape' && event.target?.getAttribute?.('aria-expanded') === 'true'
        && doc.getElementById(event.target.getAttribute('aria-controls'))?.classList.contains('fep-scroll')) return;
      if (current.form && current.panel.contains(event.target)) {
        if (key === 'Escape') { stop(event); close('escape'); return; }
        if (key === 'Tab') {
          const fields = formItems(current.panel), index = fields.indexOf(doc.activeElement);
          if (event.shiftKey ? index <= 0 : index < 0 || index === fields.length - 1) tabOut(event);
        }
        // Native input navigation, IME and local undo belong to the form. Its
        // bubble boundary protects document-level graph keyboard shortcuts.
        return;
      }
      // These commands otherwise reach the graph's document-level shortcuts.
      if ((event.ctrlKey || event.metaKey) && ['z', 'y'].includes(key.toLowerCase())) { stop(event); return; }
      if (key === 'Escape') { stop(event); close('escape'); return; }
      if (key === 'Tab') { tabOut(event); return; }
      if (!current.panel.contains(event.target)) return;
      const commands = items(current.panel), index = commands.indexOf(doc.activeElement);
      let next = null;
      if (key === 'ArrowDown') next = index < 0 ? 0 : (index + 1) % commands.length;
      if (key === 'ArrowUp') next = index < 0 ? commands.length - 1 : (index + commands.length - 1) % commands.length;
      if (key === 'Home') next = 0;
      if (key === 'End') next = commands.length - 1;
      if (next !== null) { stop(event); if (commands[next]) focus(commands[next]); return; }
      if (['Enter', ' ', 'Spacebar'].includes(key)) {
        stop(event);
        const item = event.target.closest?.(menuSelector);
        if (!event.repeat && item && !unavailable(item)) item.click();
      }
    }, true);

    function suppression(event) {
      if (!dismissed) return false;
      const now = Date.now(), down = event.type === 'pointerdown' || event.type === 'mousedown';
      const samePlace = Math.hypot((event.clientX || 0) - dismissed.x, (event.clientY || 0) - dismissed.y) < 6;
      if ((dismissed.complete || dismissed.released) && now > dismissed.until) { dismissed = null; return false; }
      if (down && (dismissed.complete || dismissed.released)) {
        if (!graphTarget(event.target) || !samePlace) { dismissed = null; return false; }
        // A nearby second click belongs to the same double-click gesture.
        dismissed.complete = false; dismissed.released = false; dismissed.pointerId = event.pointerId;
      }
      if (event.pointerId != null && dismissed.pointerId != null && event.pointerId !== dismissed.pointerId) return false;
      if (dismissed.complete && (!['click', 'dblclick', 'contextmenu'].includes(event.type) || !graphTarget(event.target) || !samePlace)) return false;
      stop(event);
      if (event.type === 'pointerup' || event.type === 'mouseup') { dismissed.released = true; dismissed.until = now + 1000; }
      if (event.type === 'click' || event.type === 'dblclick' || event.type === 'pointercancel') {
        dismissed.complete = true; dismissed.until = now + 500;
      }
      return true;
    }
    const pointerTypes = ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'mousedown', 'mousemove', 'mouseup', 'click', 'dblclick', 'contextmenu'];
    // Registered before the command click handler's bubble listeners. All of a
    // graph-dismissal gesture is consumed, without an overlay or history lock.
    function outside(event) {
      if (suppression(event)) return;
      if (!current) return;
      if (!['pointerdown', 'mousedown', 'click', 'contextmenu'].includes(event.type)) return;
      if (current.panel.contains(event.target) || current.trigger.contains(event.target)) return;
      const graph = graphTarget(event.target);
      close('outside');
      if (!graph) return;
      dismissed = { pointerId: event.pointerId, x: event.clientX || 0, y: event.clientY || 0, complete: false, until: Date.now() + 2000 };
      stop(event);
      if (event.type === 'click' || event.type === 'contextmenu') { dismissed.complete = true; dismissed.until = Date.now() + 500; }
    }
    // Use capture on window so a dismissal reaches neither G6 nor document
    // gesture listeners, regardless of their registration order.
    for (const type of pointerTypes) root.addEventListener(type, outside, true);
    doc.addEventListener('focusin', event => {
      if (current && !current.panel.contains(event.target) && !current.trigger.contains(event.target)) close('focus-out');
    }, true);
    doc.addEventListener('scroll', event => {
      if (current && event.target !== current.panel && !current.panel.contains(event.target)) close('scroll');
    }, true);
    doc.addEventListener('funds:toolbar-menu-close', () => close('external'));
    root.addEventListener('resize', () => close('resize'));
    root.addEventListener('fundstrace:workspace-resize', () => close('workspace-resize'));
    root.addEventListener('blur', () => { close('blur'); dismissed = null; });
    doc.addEventListener('visibilitychange', () => { if (doc.hidden) { close('visibility'); dismissed = null; } });
    for (const trigger of doc.querySelectorAll('[data-toolbar-menu]')) prepare(trigger);
    const api = { mount, open, close, place, isOpen: () => !!current,
      getState: () => ({ open: !!current, triggerId: current?.trigger.id || null, menuId: current?.panel.id || null, reason: lastReason }) };
    instances.set(doc, api);
    if (doc === root.document) root.FundsToolbarMenu = api;
    return api;
  }
  root.FundsToolbarMenu = { mount, open: value => mount()?.open(value), close: reason => mount()?.close(reason), place: (panel, trigger) => mount()?.place(panel, trigger),
    isOpen: () => mount()?.isOpen() || false, getState: () => mount()?.getState() || { open: false } };
  if (root.document?.body) mount();
  else root.document?.addEventListener('DOMContentLoaded', () => mount(), { once: true });
})(window);
