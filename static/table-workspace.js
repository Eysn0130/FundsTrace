/* Transaction workspace: one query/table, two explicit selection meanings.
 * Graph writes belong exclusively to the canvas editor's binding-scoped commands. */
(function () {
  'use strict';
  let rows = [], request = 0, anchors = new Set(), applied = '', draft = false, inputRevision = 0;
  let open = false, mounted = false, binding = 0, membership = null, rendering = false, refreshBinding = null;
  let scrolls = { sidebar: [0, 0], overview: [0, 0] }, slots = [];
  let workspaceView = 'standard';
  const entityPickers = [];
  const queryViews = new Map([['split', true]]);
  const viewScrolls = new Map();
  const viewPositions = new Map();
  const modePositions = { sidebar: null, overview: null };
  const MAX_PAGES = 5, PAGE_SIZE = 50;
  let query = null, retry = null, restoredInputs = false, restoredViewport = null, programmaticScroll = null;
  let columnGroup = null, tableMinWidth = '';
  let lastScrollTop = 0, geometryPending = 0;
  const id = value => String(value);
  const fields = [['fAccount', '账户'], ['fName', '户名'], ['fCounter', '对手'], ['fKw', '关键词'],
    ['fMin', '最小金额'], ['fMax', '最大金额'], ['fFrom', '开始'], ['fTo', '结束']];
  const selectedRows = () => $$('#flowTbody .flow-ck');
  const member = () => membership?.snapshot() || { confirmedIds: [], desiredIds: [], pendingIds: [], failures: {}, available: false };

  function rememberAnchors() {
    if (open || rendering) return;
    selectedRows().forEach(box => box.checked ? anchors.add(id(box.dataset.id)) : anchors.delete(id(box.dataset.id)));
  }
  function anchorStatus() {
    if (!mounted) return;
    const boxes = selectedRows(), count = boxes.filter(box => anchors.has(id(box.dataset.id))).length;
    $('#flowCkCount').textContent = `已选锚点 ${anchors.size} 笔`;
    $('#flowCkAll').checked = boxes.length > 0 && count === boxes.length;
    $('#flowCkAll').indeterminate = count > 0 && count < boxes.length;
  }
  function summary() {
    const values = fields.filter(([key]) => $('#' + key)?.value.trim()).map(([key, label]) => `${key === 'fAccount' && $('#fAccount').dataset.exactAccount === $('#fAccount').value ? '账号（精确）' : label} ${$('#' + key).value.trim()}`);
    if ($('#fTracked')?.checked) values.push('仅已上图');
    return values.join(' · ') || '全部流水';
  }
  function updateMembership() {
    if (!mounted) return;
    const status = member(), desired = new Set(status.desiredIds.map(id)), pending = new Set(status.pendingIds.map(id));
    for (const tr of $$('#flowTbody tr[data-id]')) {
      const key = id(tr.dataset.id), box = tr.querySelector('.flow-ck'), line = tr.querySelector('.membership-status');
      if (!box) continue;
      if (open) {
        box.checked = desired.has(key); box.disabled = !status.available;
        box.setAttribute('aria-label', `${box.checked ? '从当前画布移除' : '加入当前画布'}，流水 ${key}`);
      } else {
        box.checked = anchors.has(key); box.disabled = false;
        box.setAttribute('aria-label', `选为追踪锚点，流水 ${key}`);
      }
      const error = status.failures[key];
      tr.classList.toggle('membership-pending', pending.has(key));
      tr.classList.toggle('membership-failed', !!error);
      if (line) {
        line.textContent = error ? '失败 · 重试' : pending.has(key) ? (desired.has(key) ? '添加中' : '移除中') : desired.has(key) ? '已上图' : '未上图';
        line.title = error || ''; line.classList.toggle('is-error', !!error);
      }
    }
    const all = $('#overviewTrackAll'), pageIds = rows.map(row => id(row.id));
    const count = pageIds.filter(key => desired.has(key)).length;
    all.checked = pageIds.length > 0 && count === pageIds.length;
    all.indeterminate = count > 0 && count < pageIds.length;
    all.disabled = !pageIds.length || !status.available;
    const failCount = Object.keys(status.failures).length;
    $('#overviewMembershipStatus').textContent = pending.size ? `${pending.size} 笔处理中，可继续选择` : failCount ? `${failCount} 笔操作失败，已回退；勾选可重试` : `当前显示 ${count}/${pageIds.length} 笔已上图`;
    const history = window.FundsCanvasEditor?.history.status();
    $('#btnOverviewUndo').disabled = !history?.canUndo || history.busy || status.busy;
    $('#btnOverviewRedo').disabled = !history?.canRedo || history.busy || status.busy;
    $('#btnOverviewUndo').textContent = history?.canUndo ? `撤销${history.undoLabel ? '：' + history.undoLabel : ''}` : '撤销';
    $('#btnOverviewRedo').title = history?.redoLabel ? '重做：' + history.redoLabel : '暂无可重做的操作';
    anchorStatus();
  }
  function decorate() {
    for (const tr of $$('#flowTbody tr[data-id]')) {
      const box = tr.querySelector('.flow-ck');
      if (!box) continue;
      if (!tr.querySelector('.membership-status')) {
        const line = document.createElement('span'); line.className = 'membership-status'; box.after(line);
      }
    }
    updateMembership();
  }
  const originalRender = renderFlows;
  renderFlows = function (values) {
    const viewport = $('#flowTableScroll'), active = document.activeElement;
    const focusedRow = viewport.contains(active) ? active.closest('tr[data-id]') : null;
    const focus = focusedRow ? { id: focusedRow.dataset.id,
      selector: ['.flow-ck', '.act-ctx', '.act-track'].find(selector => active.matches(selector)) } : null;
    const scroll = [viewport.scrollLeft, viewport.scrollTop];
    rememberAnchors(); rows = values;
    // The legacy renderer calls syncFlowCkCount while its new checkboxes are all
    // unchecked. Read anchors only before replacement, then restore them below.
    rendering = true;
    try { originalRender(values); } finally { rendering = false; }
    decorate();
    if (focus) {
      const row = $$('#flowTbody tr[data-id]').find(item => item.dataset.id === focus.id);
      const target = focus.selector ? row?.querySelector(focus.selector) : row;
      (target || row || viewport).focus({ preventScroll: true });
    }
    viewport.scrollLeft = scroll[0]; viewport.scrollTop = scroll[1]; syncFlowTableScroll();
  };
  syncFlowCkCount = function () { rememberAnchors(); anchorStatus(); };

  const currentQuery = value => value && query === value && value.sequence === request && value.binding === binding && isCurrentCase(value.context);
  const pageNumbers = value => [...value.pages.keys()].sort((a, b) => a - b);
  function pageFor(rowId, value = query) {
    return value ? pageNumbers(value).find(page => value.pages.get(page).some(row => id(row.id) === rowId)) : null;
  }
  function visibleTop(viewport) {
    const top = viewport.getBoundingClientRect?.()?.top || 0;
    const head = viewport.querySelector('thead')?.getBoundingClientRect?.();
    return top + Math.min(viewport.clientHeight || 0, head?.height || 0);
  }
  function capturePosition() {
    const viewport = $('#flowTableScroll'), top = visibleTop(viewport);
    const row = $$('#flowTbody tr[data-id]').find(node => {
      const rect = node.getBoundingClientRect?.(); return rect && rect.bottom > top;
    });
    return { rowId: row?.dataset.id || '', page: pageFor(row?.dataset.id) || state.search.page || 1,
      offset: row ? row.getBoundingClientRect().top - top : 0,
      scrollLeft: viewport.scrollLeft, scrollTop: viewport.scrollTop };
  }
  function restorePosition(position) {
    if (!position) return;
    const viewport = $('#flowTableScroll');
    const row = $$('#flowTbody tr[data-id]').find(node => node.dataset.id === position.rowId);
    viewport.scrollLeft = position.scrollLeft || 0;
    const rect = row?.getBoundingClientRect?.();
    if (rect) viewport.scrollTop += rect.top - visibleTop(viewport) - position.offset;
    else viewport.scrollTop = position.scrollTop || 0;
    programmaticScroll = [viewport.scrollLeft, viewport.scrollTop];
    lastScrollTop = viewport.scrollTop;
    syncFlowTableScroll();
  }
  function columnMode() {
    const viewport = $('#flowTableScroll');
    return (viewport.dataset.flowDensity === 'comfortable' ? 'comfortable' : 'compact') + ':'
      + (viewport.dataset.flowWrap === 'single' ? 'single' : 'wrap');
  }
  function columns() {
    if (columnGroup) return columnGroup.children;
    const table = $('#flowTable');
    if (!table?.insertBefore) return null;
    tableMinWidth = table.style.minWidth || '';
    columnGroup = document.createElement('colgroup'); columnGroup.dataset.flowWindowColumns = '';
    for (let index = 0; index < 7; index++) columnGroup.appendChild(document.createElement('col'));
    table.insertBefore(columnGroup, table.firstChild);
    return columnGroup.children;
  }
  function clearColumns() {
    if (!columnGroup) return;
    for (const col of columnGroup.children) { col.style.width = ''; col.style.minWidth = ''; }
    $('#flowTable').style.minWidth = tableMinWidth;
  }
  function applyColumns(value) {
    const sizes = value?.widths.get(columnMode()), cols = columns();
    clearColumns();
    if (!sizes || !cols) return;
    sizes.forEach((width, index) => { cols[index].style.width = width + 'px'; cols[index].style.minWidth = width + 'px'; });
    $('#flowTable').style.minWidth = Math.max(sizes.reduce((sum, width) => sum + width, 0), parseFloat(tableMinWidth) || 0) + 'px';
  }
  function rememberColumns(value) {
    if (!value) return;
    const headers = [...$('#flowTable').querySelectorAll('thead th')];
    if (headers.length !== 7) return;
    const measured = headers.map(node => node.getBoundingClientRect?.()?.width);
    if (!measured.every(width => Number.isFinite(width) && width > 0)) return;
    const key = columnMode(), previous = value.widths.get(key) || [];
    // Four display modes, seven numbers each. No evicted row or text is kept.
    value.widths.set(key, measured.map((width, index) => Math.max(width, previous[index] || 0)));
  }
  function status(message, action = null, direction = 0) {
    $('#resultInfo').textContent = message; $('#resultInfo').title = message;
    retry = action ? { run: action, direction } : null;
  }
  function resultStatus(value) {
    if (!value) return;
    status(`共 ${fmtCount(value.total)} 笔`);
  }
  function checkedResult(result, wanted) {
    if (!result || !Array.isArray(result.rows) || !Number.isSafeInteger(result.page) || result.page < 1
      || !Number.isSafeInteger(result.size) || result.size < 1 || result.size > PAGE_SIZE
      || !Number.isSafeInteger(result.total) || result.total < 0 || result.rows.length > result.size
      || result.page !== wanted || new Set(result.rows.map(row => id(row.id))).size !== result.rows.length) {
      throw new Error('流水查询回执不完整，请重试');
    }
    return result;
  }
  function publishWindow(value, position) {
    applyColumns(value);
    renderFlows(pageNumbers(value).flatMap(page => value.pages.get(page)));
    rememberColumns(value); applyColumns(value);
    restorePosition(position);
    state.search.total = value.total; state.search.size = value.size;
    state.search.page = pageFor(capturePosition().rowId, value) || pageNumbers(value)[0];
    resultStatus(value);
  }

  // Every submitted query or authoritative refresh creates a new epoch. Scroll
  // reads use its frozen parameters, never the still-editable query draft.
  async function search(page = 1, options = {}, prepared = null) {
    const context = caseContext(), generation = binding, sequence = ++request;
    const previous = query;
    const restoring = restoredInputs;
    if (restoredViewport?.context && isCurrentCase(restoredViewport.context)) page = restoredViewport.page;
    page = Number.isSafeInteger(page) && page > 0 ? page : 1;
    const parameters = prepared ? new URLSearchParams(prepared.parameters)
      : options.refresh && previous && !restoring ? new URLSearchParams(previous.parameters) : searchParams(page);
    const requestedSummary = prepared?.summary || (options.refresh && previous && !restoring ? previous.summary : summary());
    const position = prepared?.position || (restoredViewport?.context && isCurrentCase(restoredViewport.context) ? restoredViewport
      : options.refresh ? capturePosition() : { scrollLeft: $('#flowTableScroll').scrollLeft, scrollTop: 0 });
    restoredViewport = null; restoredInputs = false;
    parameters.set('page', String(page)); parameters.set('size', String(Math.min(PAGE_SIZE, Math.max(1, Number(parameters.get('size')) || PAGE_SIZE))));
    const revision = inputRevision, locator = window.FundsGraphLocator;
    const preview = { [parameters.get('account_exact') ? 'exactAccount' : 'account']: parameters.get('account') || '',
      entity: parameters.get('entity') || '', name: parameters.get('name') || '',
      [parameters.get('counter_exact') ? 'exactCounterparty' : 'counterparty']: parameters.get('counter') || '' };
    let previewBinding;
    try { if (options?.locate === true && Object.values(preview).some(Boolean)) previewBinding = locator?.capture?.(); } catch (_) { /* Preview availability cannot reject a transaction query. */ }
    if (!context.caseId) return false;
    if (mounted) { $('#flowTableScroll').setAttribute('aria-busy', 'true'); status('查询中…'); }
    try {
      let result = checkedResult(await api('/api/flows?' + parameters), page), clampedReads = 0;
      if (generation !== binding || sequence !== request || !isCurrentCase(context)) return false;
      // Deleting a batch may shrink a restored or refreshed deep position.
      // Re-read only the new last page, in this same query epoch and binding.
      while (result.page > Math.max(1, Math.ceil(result.total / result.size))) {
        if (++clampedReads > 2) throw new Error('流水范围持续变化，请重试查询');
        page = Math.max(1, Math.ceil(result.total / result.size)); parameters.set('page', String(page));
        result = checkedResult(await api('/api/flows?' + parameters), page);
        if (generation !== binding || sequence !== request || !isCurrentCase(context)) return false;
      }
      rememberAnchors(); parameters.set('size', String(result.size));
      query = { context, binding: generation, sequence, parameters, summary: requestedSummary,
        size: result.size, total: result.total, pages: new Map([[result.page, result.rows]]), loading: null, failedPage: null,
        widths: options.refresh && previous && !restoring ? previous.widths : new Map(),
        horizontal: options.refresh && previous && !restoring ? previous.horizontal : new Map() };
      applied = requestedSummary; draft = summary() !== requestedSummary; publishWindow(query, position);
      if (previewBinding != null && result.total > 0 && revision === inputRevision && !draft) {
        try { await locator?.previewFields?.(preview, { binding: previewBinding }); } catch (_) { /* A camera preview cannot turn a successful query into a failure. */ }
      }
      return true;
    } catch (error) {
      if (generation !== binding || sequence !== request || !isCurrentCase(context)) return false;
      // A failed new query keeps the old readable result, with a fresh epoch so
      // its retired scroll request can never append into that retained result.
      if (previous && isCurrentCase(previous.context)) query = { ...previous, sequence, loading: null, failedPage: null };
      const saved = { parameters: new URLSearchParams(parameters), summary: requestedSummary, position };
      draft = summary() !== (applied || '全部流水');
      status(rows.length ? '查询失败 · 保留上次结果' : '查询失败 · 请重试', () => search(page, { ...options, locate: false }, saved));
      $('#resultInfo').title += '：' + String(error?.message || '请求未完成').slice(0, 300);
      return false;
    } finally {
      if (generation === binding && sequence === request && mounted) $('#flowTableScroll').setAttribute('aria-busy', 'false');
    }
  }
  doSearch = search;

  async function loadNeighbor(direction, force = false) {
    const value = query;
    if (!currentQuery(value)) return false;
    const pages = pageNumbers(value), page = direction < 0 ? pages[0] - 1 : pages.at(-1) + 1;
    if (page < 1 || page > Math.ceil(value.total / value.size)) return false;
    if (value.loading) return value.loading.page === page ? value.loading.promise : false;
    if (!force && value.failedPage === page) return false;
    const parameters = new URLSearchParams(value.parameters); parameters.set('page', String(page));
    const flight = { page, promise: null };
    value.loading = flight; value.failedPage = null;
    $('#flowTableScroll').setAttribute('aria-busy', 'true'); status(`共 ${fmtCount(value.total)} 笔 · 正在读取…`);
    flight.promise = (async () => {
      try {
        const result = checkedResult(await api('/api/flows?' + parameters), page);
        if (!currentQuery(value)) return false;
        if (result.size !== value.size) throw new Error('流水查询范围已变化，请重新查询');
        if (result.total !== value.total) return search(state.search.page || pages[0], { refresh: true });
        const existing = new Set(rows.map(row => id(row.id)));
        if (result.rows.some(row => existing.has(id(row.id)))) throw new Error('流水顺序已更新，请重新查询');
        const position = capturePosition();
        const evictedPage = pages.length >= MAX_PAGES ? (direction > 0 ? pages[0] : pages.at(-1)) : null;
        // The reader may have moved back across the window during a slow read.
        // Discard that prefetch if eviction would remove the row now in view.
        // Offscreen keyboard focus alone does not pin an old page indefinitely.
        if (evictedPage != null && value.pages.get(evictedPage).some(row => id(row.id) === position.rowId)) {
          resultStatus(value); return false;
        }
        rememberColumns(value);
        rememberAnchors(); value.pages.set(page, result.rows);
        const all = pageNumbers(value);
        if (all.length > MAX_PAGES) value.pages.delete(direction > 0 ? all[0] : all.at(-1));
        publishWindow(value, position);
        return true;
      } catch (error) {
        if (!currentQuery(value)) return false;
        value.failedPage = page;
        status(`共 ${fmtCount(value.total)} 笔 · 续载失败`, () => loadNeighbor(direction, true), direction);
        $('#resultInfo').title += '：' + String(error?.message || '请求未完成').slice(0, 300);
        return false;
      } finally {
        if (value.loading === flight) value.loading = null;
        if (currentQuery(value)) $('#flowTableScroll').setAttribute('aria-busy', 'false');
      }
    })();
    return flight.promise;
  }
  function scroll() {
    const viewport = $('#flowTableScroll');
    const vertical = viewport.scrollTop !== lastScrollTop;
    lastScrollTop = viewport.scrollTop;
    if (programmaticScroll && viewport.scrollLeft === programmaticScroll[0] && viewport.scrollTop === programmaticScroll[1]) { programmaticScroll = null; return; }
    programmaticScroll = null;
    if (!vertical || geometryPending) return;
    if (retry?.direction === 0) return;
    if (!currentQuery(query)) return;
    const position = capturePosition(); state.search.page = position.page;
    const height = viewport.clientHeight || 0;
    if (!height) return;
    const bottom = viewport.scrollHeight - viewport.scrollTop - height;
    if (bottom <= Math.min(160, height / 2)) void loadNeighbor(1);
    else if (viewport.scrollTop <= Math.min(160, height / 2)) void loadNeighbor(-1);
  }

  function setQueryCollapsed(collapsed) {
    const panel = $('#queryFields'), button = $('#btnQueryToggle');
    if (collapsed && panel.contains(document.activeElement)) button.focus();
    panel.classList.toggle('is-collapsed', collapsed); panel.inert = collapsed;
    panel.setAttribute('aria-hidden', String(collapsed)); button.setAttribute('aria-expanded', String(!collapsed));
    button.title = collapsed ? '展开筛选条件' : '收起筛选条件';
    button.querySelector('img').src = `/static/ui-icons/chevron-${collapsed ? 'down' : 'up'}.svg`;
  }
  window.addEventListener('fundstrace:workspace-resize', event => {
    const detail = event.detail || {};
    if (!mounted || detail.reason !== 'workspace-view'
      || !['standard', 'focus', 'split'].includes(detail.view)) return;
    const viewport = $('#flowTableScroll');
    if (detail.phase === 'prepare' && detail.view !== workspaceView) {
      // Wider slots and hidden panels clamp native scroll offsets. Capture
      // before the shell changes geometry, not after the browser has clamped.
      viewScrolls.set(workspaceView, open ? [...scrolls.sidebar] : [viewport.scrollLeft, viewport.scrollTop]);
      viewPositions.set(workspaceView, open ? modePositions.sidebar : capturePosition());
      return;
    }
    if (detail.phase === 'end' && detail.view === workspaceView) {
      const scroll = viewScrolls.get(workspaceView) || [0, 0];
      scrolls.sidebar = [...scroll];
      modePositions.sidebar = viewPositions.get(workspaceView) || null;
      if (!open) restorePosition(viewPositions.get(workspaceView) || { scrollLeft: scroll[0], scrollTop: scroll[1] });
      return;
    }
    if (detail.phase !== 'start' || detail.view === workspaceView) return;
    queryViews.set(workspaceView, $('#btnQueryToggle').getAttribute('aria-expanded') !== 'true');
    workspaceView = detail.view;
    // View preferences affect disclosure only; the original table retains its
    // query, anchors and scroll, and never enters overview's write-on-check mode.
    setQueryCollapsed(queryViews.get(workspaceView) ?? false);
  });
  function setOpen(next, returnFocus = true) {
    next = Boolean(next); if (next === open || !mounted) return;
    const viewport = $('#flowTableScroll');
    if (next) rememberAnchors();
    scrolls[open ? 'overview' : 'sidebar'] = [viewport.scrollLeft, viewport.scrollTop];
    modePositions[open ? 'overview' : 'sidebar'] = capturePosition();
    open = next;
    if (next) {
      slots = ['.flow-table-tools', '#flowTableScroll'].flatMap(selector => {
        const node = document.querySelector('#tab-search ' + selector);
        if (!node) return [];
        const marker = document.createComment('table workspace slot');
        node.before(marker); $('#flowOverviewContent').appendChild(node); return [[node, marker]];
      });
      const current = state.cases.find(value => value.id === state.caseId);
      $('#flowOverviewContext').textContent = `${current?.name || '当前案件'} · ${state.canvasName || '默认画布'}`;
      $('#flowSelectionHeader').textContent = '上图'; $('#flowSelectionHeader').setAttribute('aria-label', '上图');
      $('#flowOverview').style.display = 'flex'; $('#btnCloseFlowOverview').focus();
    } else {
      // Moving the exact nodes back preserves event listeners and avoids copied IDs.
      slots.forEach(([node, marker]) => { marker.replaceWith(node); }); slots = [];
      $('#flowSelectionHeader').textContent = '锚点'; $('#flowSelectionHeader').setAttribute('aria-label', '追踪锚点');
      $('#flowOverview').style.display = 'none';
      if (returnFocus && !$('#btnFlowOverview').closest('[inert]')) $('#btnFlowOverview').focus();
    }
    const scroll = scrolls[next ? 'overview' : 'sidebar'];
    viewport.scrollLeft = scroll[0]; viewport.scrollTop = scroll[1];
    restorePosition(modePositions[next ? 'overview' : 'sidebar'] || { scrollLeft: scroll[0], scrollTop: scroll[1] });
    decorate(); syncFlowTableScroll();
  }
  function invalidate() {
    entityPickers.forEach(picker => picker.invalidate());
    for (const key of ['fAccount', 'fCounter']) if ($('#' + key)?.dataset) delete $('#' + key).dataset.exactAccount;
    binding++; request++; rows = []; applied = ''; draft = false; query = null; retry = null;
    clearColumns();
    restoredInputs = false; restoredViewport = null; programmaticScroll = null; geometryPending = 0;
    setOpen(false, false); scrolls = { sidebar: [0, 0], overview: [0, 0] };
    viewScrolls.clear(); viewPositions.clear(); modePositions.sidebar = modePositions.overview = null;
    state.search.page = 1; state.search.total = 0;
    // The old canvas switch clears graph facts, but does not clear its table.
    // Revoke both DOM and model together before the new binding can be used.
    renderFlows([]); anchors.clear();
    if (mounted) {
      $('#flowTableScroll').scrollLeft = 0; $('#flowTableScroll').scrollTop = 0;
      lastScrollTop = 0;
      $('#flowTableScroll').setAttribute('aria-busy', 'false');
      status('等待画布就绪后查询');
    }
  }
  for (const [name, original] of [['selectCase', selectCase], ['activateCanvas', activateCanvas]]) {
    const wrapped = function (...args) {
      invalidate(); const result = original.apply(this, args);
      refreshBinding = { generation: binding, context: caseContext() }; return result;
    };
    if (name === 'selectCase') selectCase = wrapped; else activateCanvas = wrapped;
  }
  const originalLoadGraph = loadGraph;
  loadGraph = async function (retry = true, context = caseContext()) {
    // Imports and identity edits also reload this graph without changing case.
    entityPickers.forEach(picker => picker.invalidate());
    const result = await originalLoadGraph.call(this, retry, context);
    if (refreshBinding?.generation === binding && isCurrentCase(context) &&
        isCurrentCase(refreshBinding.context) && !state.graphLoading) {
      // Only the first successful graph read after a binding switch refreshes
      // its empty table. Mutation refreshes retain their existing query owner.
      refreshBinding = null; await doSearch(1);
    }
    return result;
  };

  window.FundsTableDrafts = Object.freeze({
    capture: () => { rememberAnchors(); return [...anchors]; },
    selectedIds: () => { rememberAnchors(); return [...anchors]; },
    restore: ids => {
      anchors = new Set((ids || []).map(id)); inputRevision++; restoredInputs = true;
      request++; query = null; status($('#resultInfo').textContent); updateMembership(); anchorStatus();
    },
    captureViewport: () => {
      const position = restoredViewport || capturePosition();
      return { rowId: position.rowId, page: position.page, offset: position.offset, scrollLeft: position.scrollLeft };
    },
    restoreViewport: value => {
      restoredInputs = true;
      if (!value || typeof value.rowId !== 'string' || !Number.isSafeInteger(value.page) || value.page < 1
        || !Number.isFinite(value.offset) || !Number.isFinite(value.scrollLeft) || value.scrollLeft < 0) return false;
      restoredViewport = { rowId: value.rowId, page: value.page, offset: value.offset, scrollLeft: value.scrollLeft, context: caseContext() };
      state.search.page = value.page; return true;
    }
  });
  window.addEventListener('DOMContentLoaded', () => {
    mounted = true; membership = window.FundsCanvasEditor.membership;
    $('#flowTableScroll').addEventListener('scroll', scroll, { passive: true });
    $('#flowTableScroll').addEventListener('wheel', event => {
      if (event.shiftKey || Math.abs(event.deltaX || 0) > Math.abs(event.deltaY || 0)) return;
      if (!event.deltaY) return;
      const viewport = $('#flowTableScroll');
      const direction = event.deltaY < 0 ? -1 : 1;
      const edge = direction < 0 ? viewport.scrollTop <= 0 : viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight;
      if (!edge) return;
      if (retry?.direction === 0) void retry.run();
      else void loadNeighbor(direction, true);
    }, { passive: true });
    for (const selector of ['#flowTableDensity', '#flowTableWrap']) $(selector)?.addEventListener('change', () => {
      const position = capturePosition(), context = caseContext(), epoch = request, value = query;
      geometryPending++;
      if (value) { rememberColumns(value); value.horizontal.set(columnMode(), position.scrollLeft); }
      // Remove the old mode's single-line constraints before the host applies
      // wrapping/density. Its geometry must never become the new mode's baseline.
      clearColumns();
      const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
      schedule(() => {
        geometryPending = Math.max(0, geometryPending - 1);
        if (epoch !== request || !isCurrentCase(context)) return;
        applyColumns(value); rememberColumns(value); applyColumns(value);
        if (value?.horizontal.has(columnMode())) position.scrollLeft = value.horizontal.get(columnMode());
        restorePosition(position);
      });
    }, true);
    $('#btnQueryToggle').onclick = () => setQueryCollapsed($('#btnQueryToggle').getAttribute('aria-expanded') === 'true');
    $('#btnFlowOverview').onclick = () => setOpen(true);
    $('#btnCloseFlowOverview').onclick = () => setOpen(false);
    $('#btnOverviewUndo').onclick = () => $('#btnUndo').click();
    $('#btnOverviewRedo').onclick = () => $('#btnRedo').click();
    $('#overviewTrackAll').onchange = event => membership.set(rows.map(row => row.id), event.target.checked);
    $('#flowCkAll').onchange = event => {
      selectedRows().forEach(box => { box.checked = event.target.checked; }); rememberAnchors(); anchorStatus();
    };
    $('#btnSearch').onclick = () => doSearch(1, { locate: true });
    $('#queryFields').addEventListener('input', event => {
      if (['fAccount', 'fCounter'].includes(event.target?.id)) delete event.target.dataset.exactAccount;
      inputRevision++; draft = summary() !== (applied || '全部流水');
    });
    $('#queryFields').addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && event.target.matches('input') && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); doSearch(1, { locate: true }); }
    });
    $('#btnReset').onclick = () => {
      entityPickers.forEach(picker => picker.invalidate());
      for (const key of ['fAccount', 'fCounter']) delete $('#' + key).dataset.exactAccount;
      fields.forEach(([key]) => { $('#' + key).value = ''; }); $('#fTracked').checked = false; doSearch(1);
    };
    if (window.FundsEntitySearch) for (const key of ['fAccount', 'fCounter']) {
      const field = $('#' + key);
      const picker = window.FundsEntitySearch.mount(field, { kind: 'account', source: 'flows', caseId: () => state.caseId,
        scope: () => state.caseId ? JSON.stringify([String(state.caseId), binding]) : null,
        onSelect: item => { field.dataset.exactAccount = item.account; },
        onSubmit: () => { void doSearch(1, { locate: true }); } });
      if (picker) entityPickers.push(picker);
    }
    $('#flowTbody').addEventListener('click', event => {
      if (open && event.target.matches('.flow-ck')) event.stopImmediatePropagation();
    }, true);
    $('#flowTbody').addEventListener('change', event => {
      const box = event.target;
      if (!box.matches('.flow-ck')) return;
      if (open) { event.stopImmediatePropagation(); membership.set([box.dataset.id], box.checked); }
      else { rememberAnchors(); anchorStatus(); }
    }, true);
    $('#flowTableScroll').addEventListener('keydown', event => {
      const tr = event.target.closest('tr[data-id]');
      const viewport = $('#flowTableScroll'), direction = event.key === 'ArrowUp' ? -1 : 1;
      const edge = tr ? event.target === tr && !(direction < 0 ? tr.previousElementSibling : tr.nextElementSibling)?.matches('tr[data-id]')
        : event.target === viewport && (direction < 0 ? viewport.scrollTop <= 0 : viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight);
      if (retry?.direction === 0 && !event.isComposing && ['ArrowUp', 'ArrowDown'].includes(event.key)
        && edge) {
        event.preventDefault(); event.stopImmediatePropagation(); void retry.run(); return;
      }
      if (tr && event.target === tr && !event.isComposing && ['ArrowUp', 'ArrowDown'].includes(event.key) && currentQuery(query)) {
        const direction = event.key === 'ArrowUp' ? -1 : 1;
        const adjacent = direction < 0 ? tr.previousElementSibling : tr.nextElementSibling;
        const pages = pageNumbers(query), targetPage = direction < 0 ? pages[0] - 1 : pages.at(-1) + 1;
        if (!adjacent?.matches('tr[data-id]') && targetPage > 0 && targetPage <= Math.ceil(query.total / query.size)) {
          event.preventDefault(); event.stopImmediatePropagation();
          const value = query;
          void loadNeighbor(direction, true).then(ok => {
            if (!ok || !currentQuery(value)) return;
            if (!$('#flowTableScroll').contains(document.activeElement)
              || document.activeElement.closest('tr[data-id]')?.dataset.id !== tr.dataset.id) return;
            const values = value.pages.get(targetPage), target = direction < 0 ? values?.at(-1) : values?.[0];
            const row = $$('#flowTbody tr[data-id]').find(node => node.dataset.id === id(target?.id));
            if (!row) return;
            row.focus({ preventScroll: true });
            const viewport = $('#flowTableScroll'), rect = row.getBoundingClientRect?.();
            if (rect) {
              const top = visibleTop(viewport), bottom = (viewport.getBoundingClientRect?.()?.top || 0) + viewport.clientHeight;
              if (rect.top < top) viewport.scrollTop += rect.top - top;
              else if (rect.bottom > bottom) viewport.scrollTop += rect.bottom - bottom;
            }
          });
          return;
        }
      }
      if (open && tr && event.target === tr && event.key === ' ') {
        event.preventDefault(); event.stopImmediatePropagation();
        const box = tr.querySelector('.flow-ck'); if (!box.disabled) membership.set([tr.dataset.id], !box.checked);
      }
    }, true);
    membership.subscribe(updateMembership);
    decorate();
  });
})();
