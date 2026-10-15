/* Keyboard and pointer boundaries for existing dialogs; no business mutations. */
(function () {
  'use strict';
  window.addEventListener('DOMContentLoaded', () => {
    const modals = [...document.querySelectorAll('.modal')];
    const closeIds = { flowOverview: 'btnCloseFlowOverview', importModal: 'impClose', ctxModal: 'ctxClose', textInputModal: 'tiCancel', photoCropModal: 'pcClose' };
    const opened = new Map(), background = new Map();
    let top = null, sequence = 0;
    const visible = node => node.isConnected && !node.hidden && getComputedStyle(node).display !== 'none';
    const focusable = node => [...node.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')]
      .filter(item => !item.disabled && item.tabIndex >= 0 && !item.closest('[inert]') && item.getClientRects().length);
    function restoreFocus(target) {
      if (target?.isConnected && !target.closest('[inert]') && target.getClientRects().length && !target.disabled) target.focus();
      else if (top) (focusable(top)[0] || top).focus();
    }
    function sync() {
      let closedFocus = null;
      for (const modal of modals) {
        if (visible(modal) && !opened.has(modal)) opened.set(modal, { sequence: ++sequence,
          trigger: modal.id === 'flowOverview' ? document.getElementById('btnFlowOverview') : document.activeElement });
        if (!visible(modal) && opened.has(modal)) {
          if (modal === top) closedFocus = opened.get(modal).trigger;
          opened.delete(modal); modal.inert = false;
        }
      }
      const next = [...opened].sort((a, b) => b[1].sequence - a[1].sequence)[0]?.[0] || null;
      if (next === top) return;
      top = next;
      for (const child of document.body.children) {
        if (['SCRIPT', 'STYLE', 'LINK'].includes(child.tagName) || child.id === 'watermark' || child.id === 'toasts') continue;
        if (top) {
          if (!background.has(child)) background.set(child, child.inert);
          child.inert = child !== top;
        } else if (background.has(child)) { child.inert = background.get(child); background.delete(child); }
      }
      // Inert has already removed the old focus from the accessibility tree.
      if (closedFocus) restoreFocus(closedFocus);
      else if (top && !top.contains(document.activeElement)) {
        const preferred = top.id === 'textInputModal' ? document.querySelector('#tiInput') : focusable(top)[0];
        (preferred || top).focus();
      }
    }
    for (const modal of modals) {
      modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); modal.tabIndex = -1;
      const title = modal.querySelector('.t,h2');
      if (title) { if (!title.id) title.id = modal.id + 'Title'; modal.setAttribute('aria-labelledby', title.id); }
      const close = () => document.getElementById(closeIds[modal.id])?.click();
      let down = null, outside = false;
      modal.addEventListener('pointerdown', event => {
        down = event.target === modal && event.button === 0 ? { x: event.clientX, y: event.clientY, id: event.pointerId } : null;
        outside = false;
      }, true);
      modal.addEventListener('pointerup', event => {
        outside = !!down && down.id === event.pointerId && event.target === modal && Math.hypot(event.clientX - down.x, event.clientY - down.y) < 6;
        down = null;
      }, true);
      modal.addEventListener('pointercancel', () => { down = null; outside = false; }, true);
      // Suppress legacy click-only dismissers so a table/scrollbar drag never closes.
      modal.addEventListener('click', event => {
        if (event.target !== modal) return;
        event.stopImmediatePropagation(); event.preventDefault();
        if (outside) close(); outside = false;
      }, true);
      new MutationObserver(sync).observe(modal, { attributes: true, attributeFilter: ['style', 'hidden'] });
    }
    document.addEventListener('keydown', event => {
      if (!top || document.querySelector('dialog[open]')) return;
      if (top.id === 'textInputModal' && (event.isComposing || event.keyCode === 229) && ['Enter', 'Escape'].includes(event.key)) {
        // The legacy naming input has its own key handler. Let the IME commit
        // or cancel its composition without also submitting/cancelling the name.
        event.stopImmediatePropagation(); return;
      }
      if (event.key === 'Escape' && !event.isComposing) {
        event.preventDefault(); event.stopImmediatePropagation(); document.getElementById(closeIds[top.id])?.click(); return;
      }
      if (event.key !== 'Tab') return;
      const items = focusable(top), first = items[0], last = items[items.length - 1];
      if (!first) { event.preventDefault(); top.focus(); return; }
      if (!top.contains(document.activeElement) || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault(); (event.shiftKey ? last : first).focus();
      }
    }, true);
    document.addEventListener('focusin', event => {
      if (top && !top.contains(event.target) && !document.querySelector('dialog[open]')) restoreFocus(focusable(top)[0]);
    });
    for (const dialog of document.querySelectorAll('dialog.action-dialog')) {
      let down = false;
      dialog.addEventListener('pointerdown', event => {
        const r = dialog.getBoundingClientRect();
        down = event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom);
      });
      dialog.addEventListener('click', event => {
        const r = dialog.getBoundingClientRect();
        if (down && event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) dialog.close();
        down = false;
      });
    }
    const mapping = document.querySelector('#impMapping');
    new MutationObserver(() => {
      for (const select of mapping.querySelectorAll('select[data-field]')) {
        select.id = 'map-' + select.dataset.field;
        const label = select.parentElement.querySelector('label'); if (label) label.htmlFor = select.id;
      }
    }).observe(mapping, { childList: true });
    const suggestions = document.querySelector('#ctxSuggest');
    new MutationObserver(() => {
      for (const item of suggestions.querySelectorAll('.sg-item')) {
        item.tabIndex = 0; item.setAttribute('role', 'button');
        item.setAttribute('aria-label', `勾选推荐流水：${item.querySelector('.sg-main')?.textContent || ''}`);
      }
    }).observe(suggestions, { childList: true });
    suggestions.addEventListener('keydown', event => {
      if (event.target.matches('.sg-item') && ['Enter', ' '].includes(event.key)) {
        event.preventDefault(); event.target.click();
      }
    });
    const contextTabs = document.querySelector('#ctxTabs');
    if (contextTabs) {
      const syncTabs = () => contextTabs.querySelectorAll('.stab').forEach(button => {
        button.setAttribute('aria-pressed', String(button.classList.contains('active')));
      });
      new MutationObserver(syncTabs).observe(contextTabs, { attributes: true, attributeFilter: ['class'], subtree: true });
      syncTabs();
    }
    document.querySelector('#pcCircle').addEventListener('keydown', event => {
      const delta = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
      if (!delta) return;
      event.preventDefault();
      const step = event.shiftKey ? .05 : .01;
      pcState.cx = Math.max(0, Math.min(1, pcState.cx + delta[0] * step));
      pcState.cy = Math.max(0, Math.min(1, pcState.cy + delta[1] * step));
      pcLayout();
    });
    const drawer = document.querySelector('#drawer');
    drawer.setAttribute('aria-labelledby', 'drawerTitle');
    drawer.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); document.querySelector('#btnCloseDrawer').click(); }
    });
    sync();
  });
})();
