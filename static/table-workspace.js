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
  let query = null, pendingQuery = null, retry = null, restoredInputs = false, restoredViewport = null, programmaticScroll = null;
  let columnGroup = null, tableMinWidth = '';
  let lastScrollTop = 0, geometryPending = 0;
  let activeRowId = null, resizingColumn = null;
  const fullValues = new WeakMap();
  let hideFlowValue = () => {};
  const columnMinimums = [40, 88, 112, 112, 112, 112, 112];
  const columnMaximums = [40, 240, 640, 640, 2000, 800, 200];
  const columnDefaults = [40, 92, 140, 140, 160, 160, 112];
  const columnNames = ['锚点', '交易时间', '付款方', '收款方', '金额', '摘要', '操作'];
  const columnPreferenceKey = 'funds_flow_columns_v1';
  const textMeasure = document.createElement('canvas').getContext?.('2d');
  let preferredColumns = {};
  try {
    const saved = JSON.parse(window.localStorage?.getItem(columnPreferenceKey) || '{}');
    for (const [key, widths] of Object.entries(saved)) {
      if (/^(sidebar|split|overview):(compact|comfortable):(wrap|single|two-line)$/.test(key)
        && Array.isArray(widths) && widths.length === 7 && widths.every(width => Number.isFinite(width) && width > 0)) preferredColumns[key] = widths;
    }
  } catch (_) { /* A damaged display preference does not affect query data. */ }
  const id = value => String(value);
  const fields = [['fAccount', '账户'], ['fName', '户名'], ['fCounter', '对手'], ['fKw', '关键词'],
    ['fMin', '最小金额'], ['fMax', '最大金额'], ['fFrom', '开始'], ['fTo', '结束']];
  const selectedRows = () => $$('#flowTbody .flow-ck');
  const member = () => membership?.snapshot() || { confirmedIds: [], desiredIds: [], pendingIds: [], failures: {}, available: false };

  function rememberAnchors() {
    if (open || rendering) return;
    // A wider filter can display a whole event for a previously selected single
    // observation. Its mixed checkbox is a projection, not a new selection.
    selectedRows().filter(box => !box.indeterminate).forEach(box => flowObservationIds(box).map(id).forEach(key => box.checked ? anchors.add(key) : anchors.delete(key)));
  }
  function anchorStatus() {
    if (!mounted) return;
    const boxes = selectedRows(), count = boxes.filter(box => flowObservationIds(box).every(value => anchors.has(id(value)))).length;
    $('#flowCkCount').textContent = `已选锚点 ${anchors.size} 条记录`;
    $('#flowCkAll').checked = boxes.length > 0 && count === boxes.length;
    $('#flowCkAll').indeterminate = !$('#flowCkAll').checked && boxes.some(box => flowObservationIds(box).some(value => anchors.has(id(value))));
  }
  function summary() {
    const values = fields.filter(([key]) => $('#' + key)?.value.trim()).map(([key, label]) => `${key === 'fAccount' && ($('#fAccount').dataset.exactValue || $('#fAccount').dataset.exactAccount) === $('#fAccount').value ? '账号（精确）' : label} ${$('#' + key).value.trim()}`);
    if ($('#fTracked')?.checked) values.push('仅已上图');
    return values.join(' · ') || '全部流水';
  }
  function updateMembership() {
    if (!mounted) return;
    const status = member(), desired = new Set(status.desiredIds.map(id)), pending = new Set(status.pendingIds.map(id)), confirmed = new Set(status.confirmedIds.map(id));
    for (const tr of $$('#flowTbody tr[data-id]')) {
      const key = id(tr.dataset.id), box = tr.querySelector('.flow-ck'), line = tr.querySelector('.membership-status');
      const ids = flowObservationIds(tr).map(id), included = ids.some(value => desired.has(value)), waiting = ids.some(value => pending.has(value));
      if (!box) continue;
      if (open) {
        box.checked = included; box.indeterminate = false; box.disabled = !status.available;
        box.setAttribute('aria-label', `${box.checked ? '从当前画布移除' : '加入当前画布'}，流水 ${key}`);
      } else {
        const selected = ids.filter(value => anchors.has(value)).length;
        box.checked = selected === ids.length; box.indeterminate = selected > 0 && selected < ids.length; box.disabled = false;
        box.setAttribute('aria-label', `选为追踪锚点，流水 ${key}`);
      }
      const error = ids.map(value => status.failures[value]).find(Boolean);
      const confirmedCount = ids.filter(value => confirmed.has(value)).length;
      tr.dataset.membership = confirmedCount === ids.length ? 'all' : confirmedCount ? 'partial' : 'none';
      const action = tr.querySelector('.act-track');
      if (action) {
        action.textContent = waiting ? '处理中' : confirmedCount ? (confirmedCount < ids.length ? '部分·移出' : '移出') : '上图';
        action.classList.toggle('primary', !confirmedCount);
        action.disabled = waiting || !status.available;
        action.setAttribute('aria-pressed', confirmedCount === ids.length ? 'true' : confirmedCount ? 'mixed' : 'false');
        action.setAttribute('aria-label', `${confirmedCount ? '从当前画布移出' : '加入当前画布'}，流水 ${key}`);
        action.title = error || (confirmedCount ? '从当前画布移出本行的上图记录，保留原始流水；可撤销' : '将本行流水加入当前画布；可撤销');
      }
      tr.classList.toggle('flow-active', key === activeRowId);
      if (key === activeRowId) tr.setAttribute('aria-current', 'true'); else tr.removeAttribute?.('aria-current');
      tr.classList.toggle('membership-pending', waiting);
      tr.classList.toggle('membership-failed', !!error);
      if (line) {
        line.textContent = error ? '失败 · 重试' : waiting ? (included ? '添加中' : '移除中') : included ? '已上图' : '未上图';
        line.title = error || ''; line.classList.toggle('is-error', !!error);
      }
    }
    const all = $('#overviewTrackAll');
    const count = rows.filter(row => flowObservationIds(row).some(value => desired.has(id(value)))).length;
    all.checked = rows.length > 0 && count === rows.length;
    all.indeterminate = count > 0 && count < rows.length;
    all.disabled = !rows.length || !status.available;
    const failCount = Object.keys(status.failures).length;
    $('#overviewMembershipStatus').textContent = pending.size ? `${pending.size} 条记录处理中，可继续选择` : failCount ? `${failCount} 条记录操作失败，已回退；勾选可重试` : `当前显示 ${count}/${rows.length} 条记录已上图`;
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
      tr.setAttribute('aria-description', open ? '单击选行；双击或 Enter 查看详情；空格更改上图状态' : '单击选行并定位；双击或 Enter 查看详情；空格选择批量追踪锚点');
      if (!tr.querySelector('.membership-status')) {
        const line = document.createElement('span'); line.className = 'membership-status'; box.after(line);
      }
      for (const cell of [...tr.children].slice(1, -1)) {
        const value = cell.getAttribute('title') || [...cell.children].map(child => child.textContent).join('\n') || cell.textContent;
        fullValues.set(cell, value); cell.removeAttribute?.('title');
        for (const part of cell.querySelectorAll('.party-name,.mono')) part.removeAttribute?.('title');
      }
    }
    updateMembership();
  }
  const originalRender = renderFlows;
  renderFlows = function (values) {
    hideFlowValue();
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
    minimumWidths(query, true); decorate(); measureColumnContents(query, true);
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
      + (['single','two-line'].includes(viewport.dataset.flowWrap) ? viewport.dataset.flowWrap : 'wrap');
  }
  const columnProfile = () => `${open ? 'overview' : workspaceView === 'split' ? 'split' : 'sidebar'}:${columnMode()}`;
  const columnCacheKey = () => ((workspaceView === 'split' && !open) || $('#flowTableScroll').dataset.flowWrap === 'two-line') ? columnProfile() : columnMode();
  function horizontalInsets(node, fallback = 10) {
    const style = node && window.getComputedStyle?.(node);
    return style ? Math.ceil(['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth']
      .reduce((sum, key) => sum + (parseFloat(style[key]) || 0), 2)) : fallback;
  }
  function displayTextWidth(value, node) {
    const style = node && window.getComputedStyle?.(node);
    // Numeric font variants can leave the CSSOM font shorthand empty.
    if (textMeasure) textMeasure.font = style
      ? `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}` : '12px sans-serif';
    return Math.ceil(Math.max(0, ...String(value ?? '').split(/\r?\n/)
      .map(line => textMeasure?.measureText(line)?.width ?? line.length * 7)));
  }
  function measureColumnContents(value = query, refresh = false) {
    const density = $('#flowTableScroll').dataset.flowDensity;
    const previous = value?.contentWidths?.get(density);
    if (previous && !refresh) return previous;
    const rowNodes = $$('#flowTbody tr[data-id]'), cells = rowNodes[0]?.children || [];
    const inset = index => horizontalInsets(cells[index], density === 'comfortable' ? 22 : 10);
    const widths = rows.length ? [...columnMinimums] : [...columnDefaults];
    const text = (value, index, selector) => displayTextWidth(value, cells[index]?.querySelector(selector) || cells[index]);
    for (const row of rows) {
      const time = String(fmtTime(row.trade_time)).split(/\s+/);
      widths[1] = Math.max(widths[1], ...time.map(part => text(part, 1, '.flow-clock') + inset(1)));
      for (const [index, prefix] of [[2, 'payer'], [3, 'payee']]) widths[index] = Math.max(widths[index],
        text(row[prefix + '_name'], index, '.party-name') + inset(index),
        text(row[prefix + '_display_account'] || row[prefix + '_account'], index, '.mono') + inset(index));
      widths[4] = Math.max(widths[4], text(FundsMoney.currency(row.amount, row.currency), 4, '.flow-amount-value') + inset(4));
      widths[5] = Math.max(widths[5], Math.ceil(text(String(row.summary ?? '').replace(/\s+/g, ' '), 5, '.flow-summary') / 2) + inset(5));
    }
    for (const row of rowNodes) {
      const actions = row.querySelector('.flow-row-actions');
      const buttons = [...(actions?.querySelectorAll('button') || [])].filter(button => button.getClientRects?.().length);
      if (!buttons.length) continue;
      const buttonWidth = button => displayTextWidth(button.matches('.act-track') ? '部分·移出' : button.textContent, button)
        + horizontalInsets(button, 12);
      const gap = parseFloat(window.getComputedStyle?.(actions)?.gap) || 4;
      widths[6] = Math.max(widths[6], buttons.reduce((sum, button) => sum + buttonWidth(button), 0) + gap * (buttons.length - 1) + inset(6));
    }
    const measured = widths.map((width, index) => Math.max(width, previous?.[index] || 0));
    if (value) { value.contentWidths ||= new Map(); value.contentWidths.set(density, measured); }
    return measured;
  }
  function automaticColumns(value) {
    const minimums = minimumWidths(value), content = measureColumnContents(value), widths = [...minimums];
    widths[1] = Math.max(minimums[1], Math.min(columnMaximums[1], content[1]));
    widths[4] = Math.max(minimums[4], content[4]);
    widths[6] = Math.max(minimums[6], Math.min(columnMaximums[6], content[6]));
    const flexible = [2, 3, 5], party = Math.max(content[2], content[3]);
    // Content guides preferred widths; spare room grows all three text columns.
    const desired = [party, party, content[5]].map((width, index) => Math.max(minimums[flexible[index]], Math.min(420, width)));
    const balanced = desired.map(width => Math.max(width, Math.max(...desired) * .8));
    const needs = balanced.map((width, index) => width - minimums[flexible[index]]);
    const fixed = widths[0] + widths[1] + widths[4] + widths[6];
    const target = Math.max($('#flowTableScroll').clientWidth, fixed + flexible.reduce((sum, index) => sum + minimums[index], 0));
    const extra = target - fixed - flexible.reduce((sum, index) => sum + minimums[index], 0);
    const demand = needs.reduce((sum, width) => sum + width, 0);
    flexible.forEach((column, index) => {
      widths[column] += Math.floor((demand ? Math.min(extra, demand) * needs[index] / demand : 0)
        + Math.max(0, extra - demand) / flexible.length);
    });
    let remainder = target - widths.reduce((sum, width) => sum + width, 0);
    for (const column of flexible) if (remainder-- > 0) widths[column]++;
    return widths;
  }
  function minimumWidths(value = query, measure = false) {
    const result = [...columnMinimums];
    if (open) result[0] = 96;
    if (measure || !value?.amountWidth) {
      const amounts = [...$('#flowTable').querySelectorAll('.flow-amount-value')]
        .map(node => node.getBoundingClientRect?.()?.width || node.scrollWidth || 0);
      const amount = Math.ceil(Math.max(0, ...amounts));
      if (value) value.amountWidth = Math.max(value.amountWidth || 0, amount);
      result[4] = Math.max(result[4], amount + horizontalInsets($('#flowTable').querySelector('.flow-amount-value')?.closest('td'), $('#flowTableScroll').dataset.flowDensity === 'comfortable' ? 22 : 10));
    }
    result[4] = Math.max(result[4], (value?.amountWidth || 0)
      + horizontalInsets($('#flowTable').querySelector('.flow-amount-value')?.closest('td'), $('#flowTableScroll').dataset.flowDensity === 'comfortable' ? 22 : 10));
    if ($('#flowTableScroll').dataset.flowWrap !== 'two-line') {
      if (measure || !value?.timeWidth) {
        const clocks = [...$('#flowTable').querySelectorAll('.flow-date,.flow-clock')];
        const width = Math.max(columnMinimums[1], ...clocks.map(node => node.scrollWidth + 10));
        if (value) value.timeWidth = Math.max(value.timeWidth || 0, width);
      }
      result[1] = Math.max(result[1], value?.timeWidth || 0);
    }
    if ($('#flowTableScroll').dataset.flowWrap === 'single') {
      if (measure || !value?.singleMinimums) {
        const single = [...columnMinimums];
        for (const row of $$('#flowTbody tr[data-id]')) for (const index of [2,3,5]) {
          const spans = [...(row.children[index]?.querySelectorAll('.party-name,.mono,.flow-summary') || [])];
          for (const span of spans) single[index] = Math.max(single[index], span.scrollWidth + 10);
        }
        if (value) value.singleMinimums = single.map((width, index) => Math.max(width, value.singleMinimums?.[index] || 0));
      }
      for (const index of [2,3,5]) result[index] = Math.max(result[index], value?.singleMinimums?.[index] || 0);
    }
    if ($('#flowTableScroll').dataset.flowWrap === 'two-line') {
      const content = value?.contentWidths?.get($('#flowTableScroll').dataset.flowDensity);
      for (const index of [1, 4, 6]) if (content) result[index] = Math.max(result[index],
        index === 4 ? content[index] : Math.min(columnMaximums[index], content[index]));
    }
    return result;
  }
  function boundedWidths(widths, value = query) {
    const minimums = minimumWidths(value);
    const expandedWidth = open || workspaceView === 'split' ? $('#flowTableScroll').clientWidth : 0;
    return widths.map((width, index) => Math.max(minimums[index],
      Math.min(Math.max(columnMaximums[index], minimums[index], [2, 3, 5].includes(index) ? expandedWidth : 0), width)));
  }
  function persistColumns() {
    try { window.localStorage?.setItem(columnPreferenceKey, JSON.stringify(preferredColumns)); }
    catch (_) { /* Resizing remains usable when preference storage is unavailable. */ }
  }
  function syncColumnHandles(widths) {
    const minimums = minimumWidths();
    for (const handle of $$('#flowTable .flow-column-resize')) {
      const index = Number(handle.dataset.column);
      handle.setAttribute('aria-valuemin', String(minimums[index]));
      const expandedWidth = [2, 3, 5].includes(index) && (open || workspaceView === 'split') ? $('#flowTableScroll').clientWidth : 0;
      handle.setAttribute('aria-valuemax', String(Math.max(columnMaximums[index], minimums[index], expandedWidth, Math.round(widths[index]))));
      handle.setAttribute('aria-valuenow', String(Math.round(widths[index])));
    }
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
    $('#flowTable').style.width = ''; $('#flowTable').style.tableLayout = '';
  }
  function applyColumns(value) {
    const preferred = preferredColumns[columnProfile()];
    let sizes = preferred || value?.widths.get(columnCacheKey());
    const cols = columns();
    clearColumns();
    const twoLine = $('#flowTableScroll').dataset.flowWrap === 'two-line';
    if (twoLine) measureColumnContents(value);
    if (twoLine
      && (!sizes || !preferred && (open || workspaceView === 'split'))) sizes = columnDefaults;
    const automatic = !preferred && twoLine && (open || workspaceView === 'split');
    if (automatic) sizes = automaticColumns(value);
    if (!sizes || !cols) return;
    const minimums = minimumWidths(value);
    if (!automatic) sizes = preferred || twoLine ? boundedWidths(sizes, value)
      : sizes.map((width, index) => Math.max(width, minimums[index]));
    if ((open || workspaceView === 'split') && !preferred && !automatic) {
      sizes[5] += Math.max(0, $('#flowTableScroll').clientWidth - sizes.reduce((sum, width) => sum + width, 0));
    }
    sizes.forEach((width, index) => { cols[index].style.width = width + 'px'; cols[index].style.minWidth = width + 'px'; });
    $('#flowTable').style.minWidth = Math.max(sizes.reduce((sum, width) => sum + width, 0), parseFloat(tableMinWidth) || 0) + 'px';
    if (preferred || twoLine) {
      $('#flowTable').style.width = sizes.reduce((sum, width) => sum + width, 0) + 'px';
      $('#flowTable').style.tableLayout = 'fixed';
    }
    syncColumnHandles(sizes);
  }
  function rememberColumns(value) {
    if (!value || preferredColumns[columnProfile()]) return;
    const headers = [...$('#flowTable').querySelectorAll('thead th')];
    if (headers.length !== 7) return;
    const measured = headers.map(node => node.getBoundingClientRect?.()?.width);
    if (!measured.every(width => Number.isFinite(width) && width > 0)) return;
    const key = columnCacheKey(), previous = value.widths.get(key) || [];
    // Fourteen bounded width profiles, seven numbers each; no evicted row or text.
    value.widths.set(key, measured.map((width, index) => Math.max(width, previous[index] || 0)));
  }
  function bindColumnResizing() {
    const headers = [...$('#flowTable').querySelectorAll('thead th')];
    const measured = () => headers.map((node, index) => node.getBoundingClientRect?.()?.width || columnDefaults[index]);
    const measure = textMeasure;
    const autoWidth = index => {
      if (measure) measure.font = window.getComputedStyle?.(headers[index])?.font || '12px sans-serif';
      const values = [columnNames[index], ...$$('#flowTbody tr[data-id]').map(row => {
        const cell = row.children[index];
        return index === 4 ? cell?.querySelector('.flow-amount-value')?.textContent || '' : fullValues.get(cell) || '';
      })];
      let width = 0;
      for (const value of values) for (const line of value.split('\n')) width = Math.max(width, measure?.measureText(line)?.width ?? line.length * 7);
      return Math.ceil(width + 16);
    };
    const resize = (index, width, start) => {
      hideFlowValue();
      const position = capturePosition(), sizes = [...start]; sizes[index] = width;
      preferredColumns[columnProfile()] = boundedWidths(sizes);
      applyColumns(query); restorePosition(position);
    };
    headers.forEach((header, index) => {
      if (!index) return;
      const handle = document.createElement('button'); handle.type = 'button'; handle.className = 'flow-column-resize';
      handle.dataset.column = String(index); handle.setAttribute('role', 'separator'); handle.setAttribute('aria-orientation', 'vertical');
      handle.setAttribute('aria-label', `调整${columnNames[index]}列宽`);
      handle.title = `拖动调整${columnNames[index]}列宽；方向键微调；双击按已加载内容适配`;
      header.appendChild(handle);
      handle.addEventListener('focus', () => requestAnimationFrame(() => {
        if (document.activeElement !== handle) return;
        const viewport = $('#flowTableScroll'), rect = handle.getBoundingClientRect(), area = viewport.getBoundingClientRect();
        const left = area.left + headers[0].getBoundingClientRect().width + 2;
        if (rect.left < left) viewport.scrollLeft -= left - rect.left;
        else if (rect.right > area.right - 2) viewport.scrollLeft += rect.right - area.right + 2;
      }));
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault(); event.stopPropagation(); handle.focus();
        resizingColumn = { handle, index, x: event.clientX, widths: measured(), profile: columnProfile(), previous: preferredColumns[columnProfile()] };
        handle.setPointerCapture?.(event.pointerId);
      });
      handle.addEventListener('pointermove', event => {
        const active = resizingColumn;
        if (active?.handle !== handle || active.profile !== columnProfile()) return;
        resize(index, active.widths[index] + event.clientX - active.x, active.widths);
      });
      const finish = cancel => {
        const active = resizingColumn; if (active?.handle !== handle) return;
        resizingColumn = null;
        if (cancel) {
          const position = capturePosition();
          if (active.previous) preferredColumns[active.profile] = active.previous; else delete preferredColumns[active.profile];
          applyColumns(query); restorePosition(position);
        }
        else persistColumns();
      };
      handle.addEventListener('pointerup', () => finish(false));
      handle.addEventListener('pointercancel', () => finish(true));
      handle.addEventListener('lostpointercapture', () => finish(false));
      handle.addEventListener('dblclick', event => {
        event.preventDefault(); resize(index, autoWidth(index), measured()); persistColumns();
      });
      handle.addEventListener('keydown', event => {
        if (event.key === 'Escape') { finish(true); return; }
        if (!['ArrowLeft','ArrowRight','Home'].includes(event.key) || event.isComposing || event.keyCode === 229 || event.ctrlKey || event.metaKey || event.altKey) return;
        event.preventDefault();
        const widths = measured(), amount = event.shiftKey ? 1 : 8;
        resize(index, event.key === 'Home' ? minimumWidths()[index] : widths[index] + (event.key === 'ArrowLeft' ? -amount : amount), widths);
        persistColumns();
      });
    });
    syncColumnHandles(measured());
  }
  function selectRow(tr, locate = false) {
    const row = rows.find(value => id(value.id) === tr.dataset.id); if (!row) return;
    activeRowId = id(row.id);
    for (const node of $$('#flowTbody tr[data-id]')) {
      const selected = node.dataset.id === activeRowId;
      node.classList.toggle('flow-active', selected);
      if (selected) node.setAttribute('aria-current', 'true'); else node.removeAttribute?.('aria-current');
    }
    if (!locate || open) return;
    const locator = window.FundsGraphLocator;
    const binding = locator?.capture?.();
    void locator?.previewFields?.({ exactAccount: row.payer_account, exactCounterparty: row.payee_account },
      { binding, flowId: id(row.id), quiet: true });
  }
  function bindFullValues() {
    const viewport = $('#flowTableScroll'), panel = document.createElement('div'), text = document.createElement('pre'), copy = document.createElement('button');
    panel.className = 'flow-value-popover'; panel.id = 'flowValuePopover'; panel.hidden = true;
    panel.setAttribute('popover', 'manual'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', '完整单元格值');
    copy.type = 'button'; copy.className = 'btn small'; copy.textContent = '复制完整值'; panel.append(text, copy); viewport.appendChild(panel);
    let owner = null, timer = null, serial = 0;
    const close = () => {
      clearTimeout(timer); serial++;
      if (!panel.hidden) panel.hidePopover?.();
      panel.hidden = true; text.textContent = ''; owner?.removeAttribute?.('aria-describedby'); owner = null;
    };
    const show = cell => {
      if (!cell.isConnected || !currentQuery(query) || resizingColumn) return;
      const page = document.querySelector('#graphPage');
      const ownDialog = cell.closest('.modal,dialog,[role="dialog"]');
      if (page?.inert || page?.classList.contains('page-inactive') || $$('.modal,dialog,[role="dialog"]').some(node =>
        node !== panel && node !== ownDialog && !node.hidden && (node.open || node.style.display !== 'none' && node.getClientRects?.().length))) return;
      const pieces = [...cell.querySelectorAll('.party-name,.mono,.flow-date,.flow-clock,.flow-summary,.source-ledger-note')];
      if (!pieces.some(node => node.scrollWidth > node.clientWidth || node.scrollHeight > node.clientHeight)) return;
      const value = fullValues.get(cell); if (!value) return;
      owner = cell; serial++; text.textContent = value; copy.textContent = '复制完整值';
      panel.hidden = false; panel.showPopover?.(); cell.setAttribute('aria-describedby', panel.id);
      const rect = cell.getBoundingClientRect(), bounds = panel.getBoundingClientRect();
      panel.style.left = Math.max(8, Math.min(rect.left, innerWidth - bounds.width - 8)) + 'px';
      panel.style.top = Math.max(8, rect.bottom + bounds.height + 8 <= innerHeight ? rect.bottom + 6 : rect.top - bounds.height - 6) + 'px';
    };
    viewport.addEventListener('pointerover', event => {
      const cell = event.target.closest('td');
      if (cell === owner) { clearTimeout(timer); return; }
      if (!cell || !fullValues.has(cell)) return;
      close(); timer = setTimeout(() => show(cell), 300);
    });
    viewport.addEventListener('pointerout', event => {
      if (owner?.contains(event.relatedTarget) || panel.contains(event.relatedTarget)) return;
      clearTimeout(timer); timer = setTimeout(close, 150);
    });
    panel.addEventListener('pointerenter', () => clearTimeout(timer));
    panel.addEventListener('pointerleave', () => { timer = setTimeout(close, 150); });
    copy.addEventListener('click', async () => {
      const token = serial, value = text.textContent;
      try { await navigator.clipboard.writeText(value); if (token === serial && !panel.hidden) copy.textContent = '已复制'; }
      catch (_) { if (token === serial && !panel.hidden) copy.textContent = '请选中文字复制'; }
    });
    viewport.addEventListener('scroll', close, { passive: true });
    window.addEventListener('resize', close); window.addEventListener('blur', close);
    document.body.addEventListener('pointerdown', event => { if (!panel.contains(event.target)) close(); }, true);
    document.body.addEventListener('focusin', event => { if (!panel.contains(event.target) && !owner?.contains(event.target)) close(); });
    window.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229 || panel.hidden) return;
      const row = owner?.closest('tr[data-id]'), focused = panel.contains(document.activeElement);
      event.preventDefault(); event.stopImmediatePropagation(); close();
      if (focused && row?.isConnected) row.focus({ preventScroll: true });
    }, true);
    return close;
  }
  function status(message, action = null, direction = 0) {
    $('#resultInfo').textContent = message; $('#resultInfo').title = message;
    $('#resultInfo').hidden = !message;
    retry = action ? { run: action, direction } : null;
  }
  function resultStatus(value) {
    if (!value) return;
    status('');
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
    const context = caseContext(), generation = binding;
    // A refresh follows the latest submitted query even before it has rows;
    // the last successful query and the editable draft may both be different.
    if (!prepared && options.refresh && !restoredInputs && pendingQuery?.binding === generation
        && pendingQuery.sequence === request && isCurrentCase(pendingQuery.context)) {
      prepared = pendingQuery; page = prepared.page;
    }
    const sequence = ++request;
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
    const flight = { context, binding: generation, sequence, page, parameters: new URLSearchParams(parameters),
      summary: requestedSummary, position };
    pendingQuery = flight;
    if (mounted) { $('#flowTableScroll').setAttribute('aria-busy', 'true'); status('查询中…'); }
    try {
      parameters.set('granularity', 'events');
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
      if (pendingQuery === flight) pendingQuery = null;
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
    $('#flowTableScroll').setAttribute('aria-busy', 'true'); status('正在读取…');
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
        status('续载失败', () => loadNeighbor(direction, true), direction);
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
    if (detail.phase === 'prepare') {
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
      applyColumns(query);
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
    hideFlowValue(); resizingColumn = null; open = next;
    if (next) {
      slots = ['.flow-table-tools', '#flowTableScroll', '#flowHorizontalScroll'].flatMap(selector => {
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
    applyColumns(query);
    const scroll = scrolls[next ? 'overview' : 'sidebar'];
    viewport.scrollLeft = scroll[0]; viewport.scrollTop = scroll[1];
    restorePosition(modePositions[next ? 'overview' : 'sidebar'] || { scrollLeft: scroll[0], scrollTop: scroll[1] });
    decorate(); syncFlowTableScroll();
  }
  function invalidate() {
    hideFlowValue(); activeRowId = null; resizingColumn = null;
    entityPickers.forEach(picker => picker.invalidate());
    for (const key of ['fAccount', 'fCounter']) if ($('#' + key)?.dataset) delete $('#' + key).dataset.exactAccount;
    binding++; request++; rows = []; applied = ''; draft = false; query = null; pendingQuery = null; retry = null;
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
  loadGraph = async function (retry = true, context = caseContext(), { tableQuery = 'binding' } = {}) {
    // Imports and identity edits also reload this graph without changing case.
    entityPickers.forEach(picker => picker.invalidate());
    const result = await originalLoadGraph.call(this, retry, context);
    if (refreshBinding?.generation === binding && isCurrentCase(context) &&
        isCurrentCase(refreshBinding.context) && !state.graphLoading) {
      // Only the first successful graph read after a binding switch refreshes
      // its empty table. Startup/case restoration own their final query after
      // restoring inputs; ordinary callers retain this binding refresh.
      refreshBinding = null;
      if (tableQuery !== 'caller') await doSearch(1);
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
    hideFlowValue = bindFullValues(); bindColumnResizing();
    $('#btnFlowColumnsReset').onclick = () => {
      hideFlowValue(); resizingColumn = null; const position = capturePosition();
      delete preferredColumns[columnProfile()]; persistColumns(); applyColumns(query); restorePosition(position);
    };
    if (typeof ResizeObserver === 'function') {
      let previousWidth = $('#flowTableScroll').clientWidth;
      const observer = new ResizeObserver(() => {
        const width = $('#flowTableScroll').clientWidth;
        if (width > 0 && width !== previousWidth && (!previousWidth
          || (open || workspaceView === 'split') && !preferredColumns[columnProfile()])) {
          const position = capturePosition();
          hideFlowValue();
          if (!previousWidth) minimumWidths(query, true);
          applyColumns(query); restorePosition(position);
        }
        previousWidth = width;
      });
      observer.observe($('#flowTableScroll'));
    }
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
      const position = capturePosition(), context = caseContext(), epoch = request, value = query, wasOpen = open, previousView = workspaceView;
      geometryPending++;
      if (value) { rememberColumns(value); value.horizontal.set(columnCacheKey(), position.scrollLeft); }
      // Remove the old mode's single-line constraints before the host applies
      // wrapping/density. Its geometry must never become the new mode's baseline.
      clearColumns();
      const schedule = typeof requestAnimationFrame === 'function' ? requestAnimationFrame : callback => setTimeout(callback, 0);
      schedule(() => {
        geometryPending = Math.max(0, geometryPending - 1);
        if (epoch !== request || !isCurrentCase(context) || wasOpen !== open || previousView !== workspaceView) return;
        applyColumns(value); rememberColumns(value); applyColumns(value);
        if (value?.horizontal.has(columnCacheKey())) position.scrollLeft = value.horizontal.get(columnCacheKey());
        restorePosition(position);
      });
    }, true);
    $('#btnQueryToggle').onclick = () => setQueryCollapsed($('#btnQueryToggle').getAttribute('aria-expanded') === 'true');
    $('#btnFlowOverview').onclick = () => setOpen(true);
    $('#btnCloseFlowOverview').onclick = () => setOpen(false);
    $('#btnOverviewUndo').onclick = () => $('#btnUndo').click();
    $('#btnOverviewRedo').onclick = () => $('#btnRedo').click();
    $('#overviewTrackAll').onchange = event => membership.set([...new Set(rows.flatMap(flowObservationIds).map(id))], event.target.checked);
    $('#flowCkAll').onchange = event => {
      selectedRows().forEach(box => { box.checked = event.target.checked; box.indeterminate = false; }); rememberAnchors(); anchorStatus();
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
        onSelect: item => { field.dataset.exactAccount = item.account; field.dataset.exactValue = item.value; },
        onSubmit: () => { void doSearch(1, { locate: true }); } });
      if (picker) entityPickers.push(picker);
    }
    $('#flowTbody').addEventListener('click', event => {
      const tr = event.target.closest('tr[data-id]'); if (!tr) return;
      if (event.target.matches('.flow-ck') || event.target.closest('.flow-anchor')) {
        if (open || !event.target.matches('.flow-ck')) event.stopImmediatePropagation(); return;
      }
      if (event.target.closest('button') || event.target.closest('input')) return;
      event.stopImmediatePropagation();
      if (event.detail > 1 || window.getSelection?.()?.toString()) return;
      tr.focus({ preventScroll: true }); selectRow(tr, true);
    }, true);
    $('#flowTbody').addEventListener('focusin', event => {
      if (event.target.matches('tr[data-id]')) selectRow(event.target);
    });
    $('#flowTbody').addEventListener('dblclick', event => {
      const tr = event.target.closest('tr[data-id]');
      if (!tr || event.target.closest('button') || event.target.closest('input') || event.target.closest('.flow-anchor')) return;
      event.preventDefault(); event.stopImmediatePropagation(); hideFlowValue(); openContext(tr.dataset.id);
    }, true);
    $('#flowTbody').addEventListener('change', event => {
      const box = event.target;
      if (!box.matches('.flow-ck')) return;
      if (open) { event.stopImmediatePropagation(); membership.set(flowObservationIds(box), box.checked); }
      else { box.indeterminate = false; rememberAnchors(); anchorStatus(); }
    }, true);
    $('#flowTableScroll').addEventListener('keydown', event => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const tr = event.target.closest('tr[data-id]');
      if (tr && event.target === tr && event.key === 'Enter' && !event.isComposing && event.keyCode !== 229 && !event.repeat) {
        event.preventDefault(); event.stopImmediatePropagation(); hideFlowValue(); openContext(tr.dataset.id); return;
      }
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
      if (tr && event.target === tr && event.key === ' ' && !event.isComposing) {
        event.preventDefault(); event.stopImmediatePropagation();
        const box = tr.querySelector('.flow-ck');
        if (!box.disabled) {
          if (open) membership.set(flowObservationIds(tr), !box.checked);
          else { box.checked = !box.checked; box.indeterminate = false; rememberAnchors(); anchorStatus(); }
        }
      }
    }, true);
    membership.subscribe(updateMembership);
    decorate();
  });
})();
