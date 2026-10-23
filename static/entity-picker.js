(function (root, factory) {
  'use strict';
  const api = factory(root);
  root.FundsEntityPicker = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  'use strict';
  let instance = 0;
  const PAGE_SIZE = 30, MAX_ROWS = 70, OVERSCAN = 5;
  const text = value => value == null ? '' : String(value);

  function create(options) {
    const input = options.input, doc = input.ownerDocument;
    const win = doc.defaultView || root;
    const prefix = 'fep-' + (++instance);
    const scope = typeof options.scope === 'function' ? options.scope : () => 'default';
    const debounce = Number.isFinite(options.debounce) ? Math.max(0, options.debounce) : 140;
    const host = input.closest('label')?.parentElement || input.parentElement;
    if (!host || typeof options.search !== 'function') throw new TypeError('Entity picker requires an attached input and search provider');
    const popup = doc.createElement('div');
    popup.className = 'fep-popup'; popup.id = prefix + '-popup'; popup.setAttribute('popover', 'manual');
    const list = doc.createElement('div'); list.className = 'fep-scroll'; list.id = prefix + '-list';
    list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', (input.getAttribute('aria-label') || '关联') + '候选');
    const feedback = doc.createElement('div'); feedback.className = 'fep-feedback'; feedback.setAttribute('role', 'status');
    popup.append(list, feedback); host.appendChild(popup);
    const ghost = doc.createElement('div'); ghost.className = 'fep-ghost'; ghost.setAttribute('aria-hidden', 'true');
    ghost.setAttribute('popover', 'manual'); host.appendChild(ghost);
    const saved = new Map(['role', 'aria-autocomplete', 'aria-expanded', 'aria-controls', 'aria-activedescendant', 'autocomplete'].map(name => [name, input.getAttribute(name)]));
    input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'both');
    input.setAttribute('aria-expanded', 'false'); input.setAttribute('aria-controls', list.id); input.setAttribute('autocomplete', 'off');

    let disposed = false, composing = false, writing = false, wanted = false, shown = false, ghostShown = false;
    let timer = null, frame = null, controller = null, serial = 0, currentScope = null, currentQuery = '';
    let currentTokenStart = -1, currentTokenEnd = -1, caretSignature = '', selectedToken = '';
    let items = [], offsets = [0], heights = [], active = -1, nextOffset = 0, hasMore = false, loading = false, failure = false;
    let selected = null, selectedScope = null, selectedValue = '', rowEstimate = 34, narrow = false, writeInputSeen = false, lastWidth = 0;
    const listeners = [];
    const listen = (element, type, callback, capture) => {
      element.addEventListener(type, callback, capture); listeners.push(() => element.removeEventListener(type, callback, capture));
    };
    const raf = fn => win.requestAnimationFrame ? win.requestAnimationFrame(fn) : win.setTimeout(fn, 0);
    const cancelRaf = id => win.cancelAnimationFrame ? win.cancelAnimationFrame(id) : win.clearTimeout(id);
    const token = () => {
      const value = text(input.value), supplied = options.token?.(input);
      if (supplied) {
        const start = Math.max(0, Math.min(value.length, supplied.start));
        const end = Math.max(start, Math.min(value.length, supplied.end));
        return { query: text(supplied.query), start, end };
      }
      return { query: value, start: 0, end: value.length };
    };
    const validScope = () => { const value = scope(); return value == null ? null : text(value); };
    const editable = () => !input.disabled && !input.readOnly && !input.closest('[hidden], [inert]');
    const tokenSignature = part => JSON.stringify([part.query, part.start, part.end]);
    const bindingValid = () => {
      const part = token();
      return !disposed && editable() && currentScope !== null && currentScope === validScope() && currentQuery === part.query.trim()
        && (!options.token || currentTokenStart === part.start && currentTokenEnd === part.end);
    };
    const hideLayer = element => { try { element.hidePopover(); } catch (_) { /* Removed/disconnected native layer. */ } element.hidden = true; };
    const showLayer = element => { element.hidden = false; if (typeof element.showPopover === 'function') { try { element.showPopover(); } catch (_) { /* Fallback retains fixed positioned DOM. */ } } };
    const clearGhost = () => { ghostShown = false; hideLayer(ghost); ghost.replaceChildren(); };
    function close() {
      wanted = false; shown = false; input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant'); hideLayer(popup); clearGhost();
      if (timer !== null) { win.clearTimeout(timer); timer = null; }
      if (controller) { controller.abort(); controller = null; }
      serial++; loading = false;
    }
    function reset() {
      if (controller) { controller.abort(); controller = null; }
      if (timer !== null) { win.clearTimeout(timer); timer = null; }
      serial++; items = []; offsets = [0]; heights = []; active = -1; nextOffset = 0; hasMore = false; loading = false; failure = false;
      selected = null; selectedScope = null; selectedValue = ''; selectedToken = ''; list.scrollTop = 0; list.replaceChildren(); clearGhost();
      input.removeAttribute('aria-activedescendant');
    }
    function invalidate() {
      close(); reset(); currentScope = null; currentQuery = ''; currentTokenStart = currentTokenEnd = -1;
    }
    function rebuildOffsets() {
      offsets = [0];
      for (let index = 0; index < items.length; index++) offsets.push(offsets[index] + (heights[index] || rowEstimate));
    }
    function indexAt(value) {
      let low = 0, high = items.length;
      while (low < high) { const mid = (low + high) >>> 1; if (offsets[mid + 1] <= value) low = mid + 1; else high = mid; }
      return Math.min(low, Math.max(0, items.length - 1));
    }
    function addHighlight(element, value, query) {
      const valueText = text(value), at = query ? valueText.toLocaleLowerCase().indexOf(query.toLocaleLowerCase()) : -1;
      if (at < 0) { element.textContent = valueText; return; }
      element.appendChild(doc.createTextNode(valueText.slice(0, at)));
      const mark = doc.createElement('mark'); mark.textContent = valueText.slice(at, at + query.length); element.appendChild(mark);
      element.appendChild(doc.createTextNode(valueText.slice(at + query.length)));
    }
    function span(className, value, query) {
      const element = doc.createElement('span'); element.className = className; addHighlight(element, value, query); return element;
    }
    function row(index, offscreen) {
      const item = items[index], element = doc.createElement('div');
      element.id = prefix + '-option-' + index; element.className = 'fep-option' + (offscreen ? ' fep-offscreen' : '');
      element.dataset.index = String(index); element.setAttribute('role', 'option');
      element.setAttribute('aria-selected', String(index === active)); element.setAttribute('aria-posinset', String(index + 1));
      if (!hasMore) element.setAttribute('aria-setsize', String(items.length));
      const accountMatch = options.kind === 'account' && item.matchedField === 'account' && !!item.account;
      element.dataset.layout = accountMatch ? 'number' : narrow ? 'stack' : 'inline';
      const matched = text(item.matchedField), q = currentQuery;
      if (item.label) element.appendChild(span('fep-name', item.label, ['label', 'name'].includes(matched) ? q : ''));
      if (item.account) element.appendChild(span('fep-account', item.account, matched === 'account' ? q : ''));
      if (item.idNo) {
        const identity = span('fep-identity', item.idNo, ['id', 'idNo', 'id_number'].includes(matched) ? q : '');
        const label = doc.createElement('span'); label.className = 'fep-identity-label'; label.textContent = '证 '; identity.prepend(label); element.appendChild(identity);
      }
      if (item.number && item.number !== item.account) element.appendChild(span('fep-number', item.number, matched === 'number' ? q : ''));
      if (item.detail) element.appendChild(span('fep-detail', item.detail, matched === 'detail' ? q : ''));
      if (item.onGraph === false) element.appendChild(span('fep-state', '未上图', ''));
      return element;
    }
    function feedbackState() {
      feedback.replaceChildren(); feedback.hidden = !loading && !failure && items.length > 0;
      list.setAttribute('aria-busy', String(loading));
      if (loading) feedback.textContent = '加载中…';
      else if (failure) {
        const label = doc.createElement('span'); label.textContent = '加载失败';
        const retry = doc.createElement('button'); retry.type = 'button'; retry.textContent = '重试';
        retry.addEventListener('pointerdown', event => event.preventDefault());
        retry.addEventListener('click', () => load(items.length > 0)); feedback.append(label, retry);
      } else if (!items.length) feedback.textContent = '无结果';
    }
    function render() {
      if (!shown || !bindingValid()) return;
      const top = list.scrollTop, height = list.clientHeight || 280;
      const start = Math.max(0, indexAt(top) - OVERSCAN);
      const end = Math.min(items.length, start + MAX_ROWS, indexAt(top + height) + OVERSCAN + 1);
      const children = [], before = doc.createElement('div'), after = doc.createElement('div');
      before.className = after.className = 'fep-spacer'; before.setAttribute('aria-hidden', 'true'); after.setAttribute('aria-hidden', 'true');
      before.style.height = (offsets[start] || 0) + 'px'; after.style.height = Math.max(0, (offsets[items.length] || 0) - (offsets[end] || 0)) + 'px';
      children.push(before);
      for (let index = start; index < end; index++) children.push(row(index, false));
      children.push(after);
      if (active >= 0 && (active < start || active >= end)) children.push(row(active, true));
      list.replaceChildren(...children); list.scrollTop = top;
      if (active >= 0) input.setAttribute('aria-activedescendant', prefix + '-option-' + active);
      else input.removeAttribute('aria-activedescendant');
      let changed = false, correction = 0;
      for (const element of children) {
        if (!element.classList.contains('fep-option') || element.classList.contains('fep-offscreen')) continue;
        const index = Number(element.dataset.index);
        if (!narrow && element.dataset.layout === 'inline') {
          element.style.flexWrap = 'nowrap'; element.style.whiteSpace = 'nowrap';
          for (const part of element.children) part.style.flexShrink = '0';
          if (element.scrollWidth > element.clientWidth + 1) element.dataset.layout = 'stack';
          element.style.flexWrap = ''; element.style.whiteSpace = '';
          for (const part of element.children) part.style.flexShrink = '';
        }
        const measured = element.getBoundingClientRect().height;
        if (measured > 0 && Math.abs((heights[index] || rowEstimate) - measured) > 0.5) {
          if (index < indexAt(top)) correction += measured - (heights[index] || rowEstimate);
          heights[index] = measured; changed = true;
        }
      }
      if (changed) {
        rebuildOffsets(); before.style.height = (offsets[start] || 0) + 'px';
        after.style.height = Math.max(0, offsets[items.length] - (offsets[end] || 0)) + 'px'; list.scrollTop = Math.max(0, top + correction);
      }
      feedbackState();
    }
    function completionSuffix() {
      if (!shown || !wanted || options.ghost === false || input.tagName === 'TEXTAREA' || !items.length || !bindingValid()
        || composing || input.scrollLeft > 0 || doc.activeElement !== input || input.selectionStart !== input.selectionEnd || input.selectionEnd !== input.value.length) return '';
      const candidate = items[active >= 0 ? active : 0], part = token(), value = text(candidate.matchedText || candidate.value), query = part.query;
      if (part.start !== 0 || part.end !== input.value.length || !query || value.length <= query.length || !value.toLocaleLowerCase().startsWith(query.toLocaleLowerCase())) return '';
      return value.slice(query.length);
    }
    function positionGhost() {
      const completion = completionSuffix();
      if (!completion) { clearGhost(); return; }
      const rect = input.getBoundingClientRect(), style = win.getComputedStyle(input);
      Object.assign(ghost.style, { left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px',
        font: style.font, letterSpacing: style.letterSpacing, lineHeight: style.lineHeight, padding: style.padding, borderWidth: style.borderWidth, borderStyle: 'solid', borderColor: 'transparent', textAlign: style.textAlign });
      ghost.replaceChildren(); const typed = doc.createElement('span'); typed.style.visibility = 'hidden'; typed.textContent = input.value;
      const suffix = doc.createElement('span'); suffix.className = 'fep-ghost-suffix'; suffix.textContent = completion; ghost.append(typed, suffix);
      if (!ghostShown) { showLayer(ghost); ghostShown = true; }
    }
    function position() {
      if (!shown || !bindingValid()) { if (shown) close(); return; }
      const rect = input.getBoundingClientRect(), viewport = win.visualViewport;
      const leftEdge = viewport?.offsetLeft || 0, topEdge = viewport?.offsetTop || 0;
      const width = viewport?.width || win.innerWidth, height = viewport?.height || win.innerHeight;
      if (!input.isConnected || !editable() || !rect.width || !rect.height || rect.bottom <= topEdge || rect.top >= topEdge + height || rect.right <= leftEdge || rect.left >= leftEdge + width) { close(); return; }
      const popupWidth = Math.max(0, Math.min(Math.max(rect.width, 240), 420, width - 24));
      const nextNarrow = popupWidth < 340;
      const resized = Math.abs(popupWidth - lastWidth) > 0.5;
      if (resized || nextNarrow !== narrow) {
        const anchorIndex = indexAt(list.scrollTop), within = list.scrollTop - (offsets[anchorIndex] || 0);
        narrow = nextNarrow; rowEstimate = narrow ? 54 : 34; heights = []; rebuildOffsets();
        list.scrollTop = (offsets[anchorIndex] || 0) + within; lastWidth = popupWidth;
      }
      popup.style.width = popupWidth + 'px'; popup.style.left = Math.max(leftEdge + 12, Math.min(rect.left, leftEdge + width - popupWidth - 12)) + 'px';
      const below = topEdge + height - rect.bottom - 12, above = rect.top - topEdge - 12;
      const natural = Math.min(360, Math.max(36, (offsets[items.length] || 0) + (feedback.hidden ? 0 : 34)));
      const flip = below < Math.min(natural, 180) && above > below;
      const available = Math.max(0, (flip ? above : below) - 5);
      if (available < 30) { close(); return; }
      popup.style.maxHeight = Math.min(360, available) + 'px';
      list.style.maxHeight = Math.max(0, Math.min(360, available) - (feedback.hidden ? 0 : 34) - 2) + 'px';
      render();
      if (resized && active >= 0) ensureActiveVisible();
      const measured = Math.min(popup.getBoundingClientRect().height, available);
      popup.style.top = (flip ? rect.top - 5 - measured : rect.bottom + 5) + 'px';
      positionGhost();
    }
    function schedulePosition() {
      if (frame !== null || disposed) return;
      frame = raf(() => { frame = null; position(); });
    }
    function reveal() {
      if (!wanted || !bindingValid()) return;
      if (!shown) { shown = true; showLayer(popup); input.setAttribute('aria-expanded', 'true'); }
      feedbackState(); position();
    }
    async function load(more) {
      if (disposed || loading || !wanted || !bindingValid() || (more && !hasMore)) return;
      const query = currentQuery, snapshotScope = currentScope, requestSerial = serial;
      const offset = more ? nextOffset : 0;
      const beforeCount = items.length;
      controller = new win.AbortController(); const requestController = controller;
      loading = true; failure = false; reveal();
      try {
        const result = await options.search({ query, offset, limit: PAGE_SIZE, signal: requestController.signal, scope: snapshotScope });
        if (requestController.signal.aborted || requestSerial !== serial || !bindingValid() || snapshotScope !== currentScope || query !== currentQuery) return;
        const received = Array.isArray(result?.items) ? result.items : [];
        const seen = new Set(items.map(item => item.key));
        for (const candidate of received.slice(0, PAGE_SIZE)) {
          if (!candidate || candidate.key == null || candidate.value == null) continue;
          const item = { ...candidate, key: text(candidate.key), label: text(candidate.label), value: text(candidate.value) };
          if (seen.has(item.key)) continue; seen.add(item.key); items.push(item);
        }
        const requestedNext = result?.nextOffset == null ? offset + received.length : result.nextOffset;
        nextOffset = Number.isSafeInteger(requestedNext) && requestedNext > offset ? requestedNext : offset;
        hasMore = !!result?.hasMore && nextOffset > offset;
        failure = false; rebuildOffsets();
      } catch (error) {
        if (requestController.signal.aborted || requestSerial !== serial || !bindingValid()) return;
        failure = true;
      } finally {
        if (requestSerial === serial && controller === requestController) {
          controller = null; loading = false;
          if (bindingValid() && wanted) {
            reveal(); positionGhost();
            // A short/variable-height page can leave the viewport at the loaded boundary.
            // Continue only after actual progress; failures or duplicate-only pages never spin.
            if (hasMore && !failure && items.length > beforeCount && nearLoadedEnd()) {
              Promise.resolve().then(() => { if (requestSerial === serial && bindingValid() && wanted && nearLoadedEnd()) load(true); });
            }
          }
        }
      }
    }
    function queryChanged(immediate, preserveSelected) {
      if (disposed || composing || writing) return;
      const nextScope = validScope(), part = token(), query = part.query.trim();
      const picked = preserveSelected ? getSelected() : null;
      reset(); currentScope = nextScope; currentQuery = query; shown = false; hideLayer(popup); input.setAttribute('aria-expanded', 'false');
      currentTokenStart = part.start; currentTokenEnd = part.end; caretSignature = tokenSignature(part);
      if (picked) { selected = picked; selectedScope = nextScope; selectedValue = input.value; selectedToken = caretSignature; }
      wanted = doc.activeElement === input && nextScope !== null && !!query && editable();
      if (!wanted) return;
      if (immediate || !debounce) load(false);
      else timer = win.setTimeout(() => { timer = null; if (bindingValid()) load(false); }, debounce);
    }
    function open() {
      if (disposed || composing || writing) return;
      if (!bindingValid() || !currentQuery || (!items.length && !loading)) { queryChanged(true, true); return; }
      wanted = true; reveal(); positionGhost();
    }
    function notifyInput() {
      writing = true; writeInputSeen = false;
      try { input.dispatchEvent(new win.Event('input', { bubbles: true })); } finally { writing = false; }
    }
    function write(value, start, end) {
      writing = true; writeInputSeen = false;
      let inserted = false;
      try {
        input.focus(); input.setSelectionRange?.(start, end);
        // The browser editing command joins its native undo history. setRangeText is a fallback for unsupported hosts.
        inserted = typeof doc.execCommand === 'function' && doc.execCommand('insertText', false, value);
        if (!inserted) {
          if (typeof input.setRangeText === 'function') input.setRangeText(value, start, end, 'end');
          else input.value = input.value.slice(0, start) + value + input.value.slice(end);
        }
      } finally { writing = false; }
      if (!writeInputSeen) notifyInput();
      caretSignature = tokenSignature(token());
    }
    function choose(index) {
      if (!bindingValid() || !items[index]) return;
      const item = items[index], part = token();
      write(item.value, part.start, part.end);
      selected = item; selectedScope = currentScope; selectedValue = input.value;
      selectedToken = caretSignature;
      close(); options.onSelect?.(item);
    }
    function ensureActiveVisible() {
      if (active < 0) return;
      const height = list.clientHeight || 280;
      if (offsets[active] < list.scrollTop) list.scrollTop = offsets[active];
      else if (offsets[active + 1] > list.scrollTop + height) list.scrollTop = offsets[active + 1] - height;
      render();
    }
    function move(next) { active = Math.max(0, Math.min(items.length - 1, next)); ensureActiveVisible(); positionGhost(); if (hasMore && active >= items.length - 3 && !failure) load(true); }
    function keydown(event) {
      if (event.isComposing || composing || event.keyCode === 229) {
        if (['Enter', 'Escape', 'ArrowDown', 'ArrowUp'].includes(event.key) || event.keyCode === 229) event.stopImmediatePropagation();
        return;
      }
      if (shown && !bindingValid()) invalidate();
      if (event.key === 'Escape' && (shown || wanted)) { event.preventDefault(); event.stopImmediatePropagation(); close(); return; }
      if (event.key === 'Tab') { close(); return; }
      if (event.key === 'Enter' && event.shiftKey && input.tagName !== 'TEXTAREA') {
        event.preventDefault(); event.stopImmediatePropagation(); const suffix = completionSuffix();
        if (suffix) { const end = input.value.length; write(suffix, end, end); queryChanged(false); }
        else clearGhost();
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey) {
        if (shown && active >= 0) { event.preventDefault(); event.stopImmediatePropagation(); choose(active); }
        else if (options.onSubmit) { event.preventDefault(); event.stopImmediatePropagation(); const picked = getSelected(); close(); options.onSubmit(token().query, picked); }
        return;
      }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'PageDown', 'PageUp'].includes(event.key)) return;
      if (options.token && ['Home', 'End'].includes(event.key)) return;
      if (!shown) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); event.stopImmediatePropagation(); open(); }
        return;
      }
      if (!items.length || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      event.preventDefault(); event.stopImmediatePropagation();
      const page = Math.max(1, Math.floor((list.clientHeight || 280) / rowEstimate) - 1);
      if (event.key === 'Home') move(0);
      else if (event.key === 'End') { move(items.length - 1); if (hasMore && !failure) load(true); }
      else if (event.key === 'PageDown') move(active < 0 ? indexAt(list.scrollTop + (list.clientHeight || 280)) : active + page);
      else if (event.key === 'PageUp') move(active < 0 ? indexAt(list.scrollTop) : active - page);
      else move(active < 0 ? (event.key === 'ArrowUp' ? items.length - 1 : 0) : active + (event.key === 'ArrowUp' ? -1 : 1));
    }
    function getSelected() {
      return selected && selectedScope === validScope() && selectedValue === input.value && (!options.token || selectedToken === tokenSignature(token())) ? selected : null;
    }
    function caretChanged() {
      if (disposed || writing || composing || doc.activeElement !== input) return;
      if (options.token) {
        const signature = tokenSignature(token());
        if (signature !== caretSignature) queryChanged(false);
      } else positionGhost();
    }
    function nearLoadedEnd() { return list.scrollTop + list.clientHeight >= (offsets[items.length] || 0) - rowEstimate * 4; }
    listen(input, 'input', () => { if (writing) writeInputSeen = true; else queryChanged(false); });
    listen(input, 'focus', open);
    listen(input, 'keydown', keydown, true);
    listen(input, 'compositionstart', () => { composing = true; invalidate(); });
    listen(input, 'compositionend', () => { composing = false; queryChanged(false); });
    listen(input, 'click', () => {
      caretChanged();
      if (!shown && !wanted) open();
      else if (bindingValid()) positionGhost();
    });
    listen(input, 'select', caretChanged);
    listen(input, 'keyup', caretChanged);
    listen(doc, 'selectionchange', caretChanged);
    listen(input, 'scroll', positionGhost);
    listen(input, 'blur', () => { win.setTimeout(() => { if (!popup.contains(doc.activeElement) && doc.activeElement !== input) close(); }, 0); });
    listen(list, 'pointerdown', event => { if (event.target.closest('.fep-option')) event.preventDefault(); });
    listen(list, 'click', event => { const element = event.target.closest('.fep-option'); if (element && list.contains(element)) choose(Number(element.dataset.index)); });
    listen(popup, 'wheel', event => event.stopPropagation());
    listen(popup, 'pointerdown', event => event.stopPropagation());
    listen(list, 'scroll', () => { render(); if (hasMore && !loading && !failure && nearLoadedEnd()) load(true); });
    listen(doc, 'pointerdown', event => { if (shown && event.target !== input && !popup.contains(event.target) && !ghost.contains(event.target)) close(); }, true);
    listen(doc, 'scroll', event => { if (!popup.contains(event.target)) schedulePosition(); }, true);
    listen(win, 'resize', schedulePosition);
    if (win.visualViewport) { listen(win.visualViewport, 'resize', schedulePosition); listen(win.visualViewport, 'scroll', schedulePosition); }
    const observer = typeof win.ResizeObserver === 'function' ? new win.ResizeObserver(schedulePosition) : null;
    observer?.observe(input); observer?.observe(popup);
    const mutationObserver = typeof win.MutationObserver === 'function' ? new win.MutationObserver(() => {
      if (!editable()) close(); else schedulePosition();
    }) : null;
    if (mutationObserver) {
      for (let ancestor = input; ancestor; ancestor = ancestor.parentElement) mutationObserver.observe(ancestor, { attributes: true, attributeFilter: ['disabled', 'readonly', 'hidden', 'inert', 'style', 'class'] });
    }
    hideLayer(popup); hideLayer(ghost);
    return { open, close, invalidate, getSelected, destroy() {
      if (disposed) return; close(); disposed = true; observer?.disconnect(); mutationObserver?.disconnect(); listeners.forEach(remove => remove());
      if (frame !== null) cancelRaf(frame); popup.remove(); ghost.remove();
      for (const [name, value] of saved) { if (value === null) input.removeAttribute(name); else input.setAttribute(name, value); }
    } };
  }
  return { create };
});
