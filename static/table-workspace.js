/* Transaction workspace: one query/table, two explicit selection meanings.
 * Graph writes belong exclusively to the canvas editor's binding-scoped commands. */
(function () {
  'use strict';
  let rows = [], request = 0, anchors = new Set(), applied = '', draft = false;
  let open = false, mounted = false, binding = 0, membership = null, rendering = false, refreshBinding = null;
  let scrolls = { sidebar: [0, 0], overview: [0, 0] }, slots = [];
  const id = value => String(value);
  const fields = [['fAccount', '账号'], ['fName', '户名'], ['fCounter', '对手'], ['fKw', '关键词'],
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
    $('#flowCkCount').textContent = `已选锚点 ${count} 笔`;
    $('#flowCkAll').checked = boxes.length > 0 && count === boxes.length;
    $('#flowCkAll').indeterminate = count > 0 && count < boxes.length;
  }
  function summary() {
    const values = fields.filter(([key]) => $('#' + key)?.value.trim()).map(([key, label]) => `${label} ${$('#' + key).value.trim()}`);
    if ($('#fTracked')?.checked) values.push('仅已上图');
    return values.join(' · ') || '全部流水';
  }
  function updateSummary() {
    if (!mounted) return;
    const text = `${draft ? '待查询 · ' + summary() + ' · 上次结果 ' : (applied || summary()) + ' · '}${fmtCount(state.search.total)} 笔`;
    $('#querySummary').textContent = text; $('#querySummary').title = text;
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
    $('#overviewMembershipStatus').textContent = pending.size ? `${pending.size} 笔处理中，可继续选择` : failCount ? `${failCount} 笔操作失败，已回退；勾选可重试` : `本页 ${count}/${pageIds.length} 笔已上图`;
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

  // The public query entry replaces the old unsequenced implementation. There is
  // still one API, renderer and result model; late responses never publish.
  doSearch = async function (page = 1) {
    const context = caseContext(), generation = binding, sequence = ++request;
    const parameters = searchParams(page), requestedSummary = summary();
    if (!context.caseId) return false;
    if (mounted) { $('#flowTableScroll').setAttribute('aria-busy', 'true'); $('#resultInfo').textContent = '查询中…'; }
    try {
      const result = await api('/api/flows?' + parameters);
      if (generation !== binding || sequence !== request || !isCurrentCase(context)) return false;
      rememberAnchors(); state.search.page = result.page; state.search.total = result.total;
      applied = requestedSummary; draft = summary() !== requestedSummary; renderFlows(result.rows);
      const pages = Math.max(1, Math.ceil(result.total / result.size));
      $('#resultInfo').textContent = `共 ${fmtCount(result.total)} 笔`;
      $('#pageInfo').textContent = `第 ${result.page} / ${pages} 页`;
      $('#btnPrev').disabled = result.page <= 1; $('#btnNext').disabled = result.page >= pages;
      updateSummary(); return true;
    } catch (error) {
      if (generation !== binding || sequence !== request || !isCurrentCase(context)) return false;
      $('#resultInfo').textContent = rows.length ? '查询失败 · 保留上次结果' : '查询失败 · 请重试';
      return false;
    } finally {
      if (generation === binding && sequence === request && mounted) $('#flowTableScroll').setAttribute('aria-busy', 'false');
    }
  };

  function setQueryCollapsed(collapsed) {
    const panel = $('#queryFields'), button = $('#btnQueryToggle');
    if (collapsed && panel.contains(document.activeElement)) button.focus();
    panel.classList.toggle('is-collapsed', collapsed); panel.inert = collapsed;
    panel.setAttribute('aria-hidden', String(collapsed)); button.setAttribute('aria-expanded', String(!collapsed));
    button.title = collapsed ? '展开筛选条件' : '收起筛选条件';
    button.querySelector('img').src = `/static/ui-icons/chevron-${collapsed ? 'down' : 'up'}.svg`;
    updateSummary();
  }
  function setOpen(next, returnFocus = true) {
    next = Boolean(next); if (next === open || !mounted) return;
    const viewport = $('#flowTableScroll');
    if (next) rememberAnchors();
    scrolls[open ? 'overview' : 'sidebar'] = [viewport.scrollLeft, viewport.scrollTop];
    open = next;
    if (next) {
      slots = ['.flow-table-tools', '#flowTableHint', '#flowTableScroll', '.flow-horizontal-scroll', '.pager'].map(selector => {
        const node = document.querySelector('#tab-search ' + selector), marker = document.createComment('table workspace slot');
        node.before(marker); $('#flowOverviewContent').appendChild(node); return [node, marker];
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
    decorate(); syncFlowTableScroll();
  }
  function invalidate() {
    binding++; request++; rows = []; applied = ''; draft = false;
    setOpen(false, false); scrolls = { sidebar: [0, 0], overview: [0, 0] };
    state.search.page = 1; state.search.total = 0;
    // The old canvas switch clears graph facts, but does not clear its table.
    // Revoke both DOM and model together before the new binding can be used.
    renderFlows([]); anchors.clear();
    if (mounted) {
      $('#flowTableScroll').setAttribute('aria-busy', 'false');
      $('#resultInfo').textContent = '等待画布就绪后查询'; $('#pageInfo').textContent = '';
      $('#btnPrev').disabled = true; $('#btnNext').disabled = true; updateSummary();
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
    const result = await originalLoadGraph.call(this, retry, context);
    if (refreshBinding?.generation === binding && isCurrentCase(context) &&
        isCurrentCase(refreshBinding.context) && !state.graphLoading) {
      // Only the first successful graph read after a binding switch refreshes
      // its empty table. Mutation refreshes retain their existing query owner.
      refreshBinding = null; await doSearch(1);
    }
    return result;
  };

  window.addEventListener('DOMContentLoaded', () => {
    mounted = true; membership = window.FundsCanvasEditor.membership;
    $('#btnQueryToggle').onclick = () => setQueryCollapsed($('#btnQueryToggle').getAttribute('aria-expanded') === 'true');
    $('#btnFlowOverview').onclick = () => setOpen(true);
    $('#btnCloseFlowOverview').onclick = () => setOpen(false);
    $('#btnOverviewUndo').onclick = () => $('#btnUndo').click();
    $('#btnOverviewRedo').onclick = () => $('#btnRedo').click();
    $('#overviewTrackAll').onchange = event => membership.set(rows.map(row => row.id), event.target.checked);
    $('#flowCkAll').onchange = event => {
      selectedRows().forEach(box => { box.checked = event.target.checked; }); rememberAnchors(); anchorStatus();
    };
    $('#queryFields').addEventListener('input', () => { draft = summary() !== (applied || '全部流水'); updateSummary(); });
    $('#queryFields').addEventListener('keydown', event => {
      if (event.key === 'Enter' && event.target.matches('input') && !event.isComposing) { event.preventDefault(); doSearch(1); }
    });
    $('#btnReset').onclick = () => {
      fields.forEach(([key]) => { $('#' + key).value = ''; }); $('#fTracked').checked = false; doSearch(1);
    };
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
      if (open && tr && event.target === tr && event.key === ' ') {
        event.preventDefault(); event.stopImmediatePropagation();
        const box = tr.querySelector('.flow-ck'); if (!box.disabled) membership.set([tr.dataset.id], !box.checked);
      }
    }, true);
    membership.subscribe(updateMembership);
    updateSummary(); decorate();
  });
})();
