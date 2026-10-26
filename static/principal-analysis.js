/* Conditional source shares reuse the context ledger and never rewrite graph facts. */
(function (root) {
  'use strict';
  const el = id => document.getElementById(id);
  const text = value => String(value ?? '');
  let session = null, serial = 0, mounted = false;
  const reasons = {
    amount_evidence_unknown: '历史分录金额缺失或方向金额契约未明确',
    currency_evidence_unknown: '未知币种占位不能视为同一币种进行本金分配',
    ledger_coverage_unknown: '区间账本完整性未知', credit_availability_unknown: '透支或授信情况尚未明确',
    funds_availability_unknown: '资金是否全额可用尚未明确（可能存在未建模冻结或在途资金）',
    account_ledger_identity_unknown: '本方账务账户身份尚未明确，不能将显示号码相同的记录视为同一账本',
    opening_balance_unknown: '区间起点前余额未知', no_own_ledger_entries: '未找到该账户本方记账分录',
    undated_own_entry: '本方分录存在未知时间', cancellation_availability_not_established: '冲正记录的历史可用资金语义尚未明确',
    ambiguous_own_entry_direction: '本方收支方向不明确', posting_order_unknown: '同刻记账顺序尚未明确',
    inconsistent_order_constraints: '记账顺序与余额约束不一致', negative_book_balance: '账面余额不符合无透支条件',
    source_or_target_not_in_own_interval: '来源或目标不在该账户本方账本区间内',
    source_must_be_credit_and_target_debit: '来源须为入账，目标须为出账',
    account_interval_resource_limit: '账本区间超过本次计算资源边界', witness_resource_limit: '完整见证超过本次计算资源边界',
    insufficient_funds: '账本资金不足，不满足无透支条件', balance_mismatch: '来源余额与收支不一致',
    negative_balance: '账本资金不足，不满足无透支条件', post_balance_mismatch: '来源余额与收支不一致',
    external_boundary_returns_unknown: '外部边界是否存在回流尚未明确',
    complete_own_ledgers_and_opening_balances_required: '各账户的完整账本、期初余额、无透支及资金全额可用条件尚未明确',
    source_or_target_not_in_own_ledgers: '来源或目标不在选定的本方账本中', source_must_be_own_credit: '来源须为本方入账',
    conflicting_event_evidence: '跨账簿事件证据存在冲突',
    internal_transfer_requires_confirmed_opposite_ledgers: '内部转账缺少已确认的双边事件证据',
    internal_event_missing_own_entry: '内部事件缺少一侧本方分录', contradictory_cross_ledger_order: '跨账簿顺序互相矛盾',
    target_must_follow_source_and_have_internal_payer: '目标须在来源之后，且付款方属于选定账本',
    network_interval_resource_limit: '多账户区间超过本次计算资源边界', network_event_resource_limit: '事件数量超过本次计算资源边界',
  };
  function live(s = session) {
    return !!s && session === s && el('principalDialog').open && state.ctx === s.selection
      && state.ctx?.data === s.data && !state.ctx.closed && state.ctx.phase === 'ready'
      && isCurrentCase(s.context) && state.lastGraph === s.graph && !state.graphLoading
      && el('ctxModal').style.display !== 'none';
  }
  function status(message, error = false) {
    el('principalStatus').textContent = message;
    el('principalStatus').dataset.error = String(error);
  }
  function clearResult() {
    if (session) { session.result = null; session.request = null; }
    el('principalResults').replaceChildren(); el('principalResults').hidden = true;
    el('principalExport').disabled = true;
  }
  function cancelRead() {
    serial += 1;
    session?.controller?.abort();
    if (session) { session.controller = null; session.busy = false; }
    el('principalDialog').setAttribute('aria-busy', 'false');
  }
  function close() {
    cancelRead(); clearResult(); session = null;
    el('principalDialog').close();
  }
  function revoke() {
    if (session || el('principalDialog').open) close();
    sync();
  }
  function sync() {
    if (session && !live()) { close(); }
    const selection = state.ctx;
    el('ctxPrincipal').disabled = !selection || selection.closed || selection.phase !== 'ready'
      || !selection.data?.anchor || !isCurrentCase(selection.context || caseContext()) || state.graphLoading;
  }
  function changed(event) {
    if (event?.target?.id === 'principalAccountSearch') return;
    if (!live()) { revoke(); return; }
    cancelRead(); clearResult(); el('principalRun').disabled = !session.eligible;
    status('条件或目标已变更，请重新计算。');
  }
  function node(tag, value, className) {
    const item = document.createElement(tag);
    if (value != null) item.textContent = text(value);
    if (className) item.className = className;
    return item;
  }
  function selectedTargets() {
    return [...el('principalTargets').querySelectorAll('input:checked')].map(item => item.value);
  }
  function accountLabel(item) {
    return [item.name, item.kind === 'subaccount' ? '子账户' : '', item.display_account || item.account_number || (/^fte_/.test(text(item.account)) ? '显示号码未提供' : item.account)].filter(Boolean).join(' · ');
  }
  function counterpartyLabel(row) {
    const side = row.dir === 'in' ? 'payer' : 'payee';
    const display = [row.counter_display_account, row[side + '_display_account'], row.counter_account, row[side + '_account']]
      .map(text).find(value => value && !/^fte_/.test(value)) || '显示号码未提供';
    const name = text(row[side + '_name'] || row.counter_name);
    return [!/^fte_/.test(name) ? name : '', display].filter(Boolean).join(' · ');
  }
  function accountChoices() {
    if (!session) return;
    const s = session, query = el('principalAccountSearch').value.trim().toLowerCase();
    const matches = s.graph.nodes.filter(item => text(item.account) !== s.account && (!query || accountLabel(item).toLowerCase().includes(query)));
    el('principalAccountChoices').replaceChildren(...matches.slice(0, 30).map(item => {
      const label = node('label', null, 'principal-check'), checkbox = node('input'); checkbox.type = 'checkbox';
      checkbox.value = text(item.account); checkbox.checked = s.accounts.has(checkbox.value);
      checkbox.addEventListener('change', () => {
        if (!live(s)) return;
        if (checkbox.checked) s.accounts.set(checkbox.value, { item, opening: '', coverage: false, noOverdraft: false, fundsAvailable: false, balanceKind: 'unknown' });
        else s.accounts.delete(checkbox.value);
        accountConditions(); changed();
      });
      label.append(checkbox, node('span', accountLabel(item))); return label;
    }), node('p', matches.length > 30 ? `共 ${matches.length} 个匹配，当前显示前 30 个；可输入更完整的名称或号码。` : `当前匹配 ${matches.length} 个账户。`, 'principal-note'));
  }
  function accountConditions() {
    if (!session) return;
    el('principalAccountConditions').replaceChildren(...[...session.accounts].map(([account, entry]) => {
      const box = node('fieldset'), legend = node('legend', accountLabel(entry.item)); box.append(legend);
      const openingLabel = node('label', '起点前余额 '), opening = node('input'); opening.type = 'text'; opening.inputMode = 'decimal'; opening.maxLength = 40; opening.value = entry.opening; opening.placeholder = '未知';
      opening.addEventListener('input', () => { entry.opening = opening.value; }); openingLabel.append(opening); box.append(openingLabel);
      for (const [key, title] of [['coverage', '声明本币种区间收支完整'], ['noOverdraft', '声明无透支或授信'], ['fundsAvailable', '已明确资金全额可用（无未建模冻结或在途资金）']]) {
        const label = node('label', null, 'principal-check'), input = node('input'); input.type = 'checkbox'; input.checked = entry[key];
        input.addEventListener('change', () => { entry[key] = input.checked; }); label.append(input, node('span', title)); box.append(label);
      }
      const balanceLabel = node('label', '余额含义 '), select = node('select');
      for (const [value, title] of [['unknown', '未知，仅采用来源明确的账面余额'], ['post_book', '声明均为交易后账面余额']]) { const option = node('option', title); option.value = value; select.append(option); }
      select.value = entry.balanceKind; select.addEventListener('change', () => { entry.balanceKind = select.value; }); balanceLabel.append(select); box.append(balanceLabel);
      const remove = node('button', '移除该账本', 'btn small'); remove.type = 'button';
      remove.addEventListener('click', () => { session.accounts.delete(account); accountConditions(); accountChoices(); changed(); }); box.append(remove);
      return box;
    }));
  }
  function open() {
    sync();
    if (el('ctxPrincipal').disabled) return;
    close();
    const selection = state.ctx, data = selection.data, anchor = data.anchor;
    const account = text(data.account), currency = text(anchor.currency);
    const eligible = text(anchor.payee_account) === account && text(anchor.self_account) === account && !!currency;
    session = { selection, data, context: caseContext(), graph: state.lastGraph, eligible,
      account, currency, sourceId: text(anchor.id), result: null, request: null, controller: null, busy: false, accounts: new Map() };
    el('principalStart').value = ''; el('principalEnd').value = '';
    el('principalOpening').value = ''; el('principalBalanceKind').value = 'unknown';
    el('principalCoverage').checked = false; el('principalNoOverdraft').checked = false; el('principalFundsAvailable').checked = false;
    el('principalNetwork').open = false; el('principalNetworkEnabled').checked = false; el('principalBoundary').checked = false; el('principalAccountSearch').value = '';
    const display = data.display_account || anchor.payee_display_account || accountLabel(session.graph.nodes.find(item => text(item.account) === account) || { account });
    el('principalContext').textContent = `本方账务账户 ${text(display)} · 来源 #${text(anchor.id)} · ${currency} ${text(anchor.amount)}`;
    const picked = new Set([...document.querySelectorAll('#ctxTbody .ctx-ck:checked')].map(item => item.dataset.id));
    accountChoices(); accountConditions();
    const candidates = data.rows.filter(row => row.dir === 'out' && text(row.currency) === currency
      && text(row.self_account) === account && text(row.id) !== text(anchor.id));
    el('principalNetworkTargets').value = candidates.filter(row => picked.has(text(row.id))).map(row => text(row.id)).join(', ');
    const choices = candidates.map(row => {
      const label = node('label'), checkbox = node('input');
      checkbox.type = 'checkbox'; checkbox.value = text(row.id); checkbox.checked = picked.has(text(row.id));
      const detail = node('span');
      detail.append(node('b', `${text(row.trade_time)} · ${currency} ${text(row.amount)}`),
        node('small', `#${text(row.id)} · ${counterpartyLabel(row)}`));
      label.append(checkbox, detail); return label;
    });
    el('principalTargets').replaceChildren(...(choices.length ? choices : [node('p', '当前窗口没有该账户的同币种本方出账，请调整前后流水范围。')]));
    el('principalRun').disabled = !eligible;
    status(eligible ? '请填写完整账本区间并选择目标；未明确的条件会保持未知。'
      : '请从该账户本方账簿的入账流水打开。对手方的观察和余额不能代替本方账本。');
    el('principalDialog').showModal(); el('principalStart').focus();
  }
  function requestBody(s) {
    const network = el('principalNetworkEnabled').checked;
    const targets = network ? el('principalNetworkTargets').value.trim().split(/[,，\s]+/).filter(Boolean) : selectedTargets();
    if (!targets.length || targets.length > 20) throw new Error('请选择 1 至 20 笔目标出账。');
    if (targets.some(id => !/^[1-9][0-9]{0,19}$/.test(id)) || new Set(targets).size !== targets.length) throw new Error('目标须为不重复的原流水 ID，不能填写账户或事件标识。');
    const start = el('principalStart').value, end = el('principalEnd').value;
    if (!start || !end || start > end) throw new Error('请填写有序的完整账本区间。');
    const opening = el('principalOpening').value.trim();
    if (opening && !/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$/.test(opening)) throw new Error('余额须为非负十进制数，最多两位小数；未知时留空。');
    const conditions = { coverage: el('principalCoverage').checked ? 'complete_declared' : 'unknown',
      no_overdraft: el('principalNoOverdraft').checked, funds_available: el('principalFundsAvailable').checked,
      opening_balance: opening || null, balance_kind: el('principalBalanceKind').value };
    const request = { case_id: text(s.context.caseId), currency: s.currency,
      start, end, source_flow_id: s.sourceId, target_flow_ids: targets,
    };
    if (!network) return { ...request, account: s.account, conditions };
    if (s.accounts.size >= 20) throw new Error('包括来源账户，最多选择 20 个账本。');
    const accounts = [{ account: s.account, conditions }];
    for (const [account, entry] of s.accounts) {
      const opening = entry.opening.trim();
      if (opening && !/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$/.test(opening)) throw new Error('各账户余额须为非负十进制数，最多两位小数；未知时留空。');
      accounts.push({ account, conditions: { coverage: entry.coverage ? 'complete_declared' : 'unknown',
        no_overdraft: entry.noOverdraft, funds_available: entry.fundsAvailable,
        opening_balance: opening || null, balance_kind: entry.balanceKind } });
    }
    return { ...request, accounts, boundary: el('principalBoundary').checked ? 'terminal_no_return_declared' : 'unknown' };
  }
  async function read(path, request, s) {
    const response = await fetch(path, { method: 'POST', ...jsonOpts(request), signal: s.controller.signal });
    if (!response.ok) {
      const value = await response.json().catch(() => ({}));
      const error = new Error(response.status === 409 ? '来源或关联已更新，旧结果已失效，请重新计算。'
        : typeof value.detail === 'string' ? value.detail : '本次请求未完成，请核对条件后重试。');
      error.status = response.status; throw error;
    }
    return response;
  }
  function showTarget(s, id) {
    if (!live(s)) { revoke(); return; }
    let row = [...el('ctxTbody').querySelectorAll('tr[data-flow-id]')].find(item => item.dataset.flowId === id);
    if (!row && s.data.rows.some(item => text(item.id) === id)) {
      const selected = new Set([...document.querySelectorAll('#ctxTbody .ctx-ck:checked')].map(item => item.dataset.id));
      s.selection.book = 'all'; el('ctxBook').value = 'all'; renderCtxRows(s.data);
      for (const box of document.querySelectorAll('#ctxTbody .ctx-ck')) box.checked = selected.has(box.dataset.id);
      syncContextSelection();
      row = [...el('ctxTbody').querySelectorAll('tr[data-flow-id]')].find(item => item.dataset.flowId === id);
    }
    if (!row) { status(`目标 #${id} 不在当前前后流水窗口，可在流水核对中按编号查看。`); return; }
    close();
    if (row) { row.tabIndex = -1; row.scrollIntoView({ block: 'nearest' }); row.focus(); }
  }
  function render(result, s) {
    const labels = { ok: '条件计算完成', unknown: '条件或证据未知', inconsistency: '账本约束不一致', limited: '计算受限' };
    const reasonText = (result.reasons || []).map(reason => reasons[reason] || '账本或来源条件未满足').join('；');
    status(`${labels[result.status] || '结果状态未知'}${reasonText ? '：' + reasonText : ''}`, result.status === 'inconsistency');
    const wrap = el('principalResults'), summary = node('p', `本次修订 ${text(result.revision)} · ${result.ledger_count == null ? '尚未进入账本计算' : `参与计算 ${text(result.ledger_count)} 笔本方分录`}`, 'principal-note');
    const conditionRows = s.request.accounts || [{ account: s.account, conditions: s.request.conditions }];
    const declarations = node('div');
    for (const { account, conditions } of conditionRows) {
      const accountName = account === s.account ? '来源账户' : accountLabel(s.accounts.get(account).item);
      declarations.append(node('p', `${accountName}：${conditions.coverage === 'complete_declared' ? '已声明账本完整' : '完整性未知'}；${conditions.no_overdraft ? '已声明无透支或授信' : '透支或授信情况未知'}；${conditions.funds_available ? '已声明资金全额可用（无未建模冻结或在途资金）' : '资金可用性未知'}；起点前余额 ${conditions.opening_balance ?? '未知'}；${conditions.balance_kind === 'post_book' ? '已声明交易后账面余额' : '余额含义仅按明确来源采用'}。`, 'principal-note'));
    }
    if (s.request.accounts) declarations.append(node('p', `外部边界：${s.request.boundary === 'terminal_no_return_declared' ? '本次声明无未建模回流；不证明现实资金终止' : '回流情况未知'}。`, 'principal-note'));
    const scroll = node('div', null, 'principal-table-scroll'), table = node('table'), header = node('tr');
    for (const label of ['目标出账', '交易金额', '条件来源范围', 'FIFO 规则份额']) { const th = node('th', label); th.scope = 'col'; header.append(th); }
    const head = node('thead'), body = node('tbody'); head.append(header);
    for (const target of result.targets || []) {
      const row = node('tr'), id = s.request.accounts ? text((target.flow_ids || []).find(id => s.request.target_flow_ids.includes(id)) || '') : text(target.id), first = node('td'), button = node('button', `#${id}`, 'btn small');
      button.type = 'button'; button.setAttribute('aria-label', `返回前后流水中的目标 ${id}`);
      button.addEventListener('click', () => showTarget(s, id)); first.append(button);
      const range = text(target.min) === text(target.max) ? `${text(target.min)}（在这些条件下确定）` : `${text(target.min)} — ${text(target.max)}`;
      row.append(first, node('td', `${s.currency} ${text(target.amount)}`), node('td', `${s.currency} ${range}`), node('td', `${s.currency} ${text(target.fifo)}`));
      body.append(row);
    }
    table.append(head, body); scroll.append(table);
    wrap.replaceChildren(summary, declarations);
    if (s.request.accounts && result.source_amount) wrap.append(node('p', `来源本金 ${s.currency} ${text(result.source_amount)}；FIFO 终止流出 ${text(result.fifo_witness?.external_total)}，期末留存 ${text(result.fifo_witness?.retained_total)}。沿途交易份额不能相加为本金。`, 'principal-note'));
    if ((result.targets || []).length) wrap.append(scroll);
    wrap.append(node('p', '各目标上界不能相加。FIFO 是在指定顺序下的规则分配；匹配评分不参与计算，也不是来源概率。完整分配见证随本次结果导出。', 'principal-note'));
    wrap.hidden = false; el('principalExport').disabled = false;
  }
  async function run(event) {
    event?.preventDefault();
    if (!live() || !session.eligible) { revoke(); return; }
    const s = session;
    let request;
    try { request = requestBody(s); } catch (error) { status(error.message, true); return; }
    cancelRead(); clearResult();
    const token = serial; s.controller = new AbortController(); s.busy = true; s.request = request;
    el('principalDialog').setAttribute('aria-busy', 'true'); el('principalRun').disabled = true;
    status('正在读取完整区间并计算来源份额…');
    try {
      const response = await read('/api/analysis/principal' + (request.accounts ? '/network' : ''), request, s), result = await response.json();
      if (!live(s) || token !== serial) return;
      const matchesTarget = target => request.accounts ? Array.isArray(target.flow_ids) && target.flow_ids.some(id => request.target_flow_ids.includes(id)) : request.target_flow_ids.includes(target.id);
      const exact = value => typeof value === 'string' && /^[0-9]+\.[0-9]{2}$/.test(value);
      if (result.case_id !== request.case_id || (!request.accounts && result.account !== request.account) || result.currency !== request.currency
        || typeof result.revision !== 'string' || !result.revision || !Array.isArray(result.targets)
        || !['ok', 'unknown', 'inconsistency', 'limited'].includes(result.status)
        || result.targets.some(target => !matchesTarget(target)
          || ['amount', 'min', 'max', 'fifo'].some(key => !exact(target[key])))
        || (request.accounts && result.status === 'ok' && (!exact(result.source_amount) || !exact(result.fifo_witness?.external_total) || !exact(result.fifo_witness?.retained_total)))
        || (result.status === 'ok' && (request.target_flow_ids.some(id => !result.targets.some(target => request.accounts ? target.flow_ids?.includes(id) : target.id === id))
          || new Set(result.targets.map(target => target.id)).size !== result.targets.length))) throw new Error('结果与当前账户或修订不匹配，请重新计算。');
      s.result = result; render(result, s);
    } catch (error) {
      if (live(s) && token === serial && error.name !== 'AbortError') { clearResult(); status(error.message, true); }
    } finally {
      if (live(s) && token === serial) { s.busy = false; s.controller = null; el('principalRun').disabled = !s.eligible; el('principalDialog').setAttribute('aria-busy', 'false'); }
    }
  }
  async function exportResult() {
    if (!live() || !session.result || !session.request || session.busy) { if (!live()) revoke(); return; }
    const s = session, request = { ...s.request, expected_revision: s.result.revision };
    const token = ++serial; s.controller = new AbortController(); s.busy = true;
    el('principalExport').disabled = true; el('principalRun').disabled = true;
    el('principalDialog').setAttribute('aria-busy', 'true'); status('正在核对修订并导出…');
    try {
      const response = await read('/api/analysis/principal' + (request.accounts ? '/network' : '') + '/export', request, s), blob = await response.blob();
      if (!live(s) || token !== serial) return;
      const url = URL.createObjectURL(blob), link = node('a');
      link.href = url; link.download = 'FundsTrace-条件来源份额.html'; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      status('已导出同一修订的条件、金额与完整分配见证。');
    } catch (error) {
      if (live(s) && token === serial && error.name !== 'AbortError') { if (error.status === 409) clearResult(); status(error.message, true); }
    } finally {
      if (live(s) && token === serial) { s.busy = false; s.controller = null; el('principalRun').disabled = !s.eligible; el('principalExport').disabled = !s.result; el('principalDialog').setAttribute('aria-busy', 'false'); }
    }
  }
  function mount() {
    if (mounted) return; mounted = true;
    el('ctxPrincipal').addEventListener('click', open);
    el('principalClose').addEventListener('click', close);
    el('principalDialog').addEventListener('close', () => { if (session) close(); });
    el('principalForm').addEventListener('submit', run);
    el('principalForm').addEventListener('input', changed);
    el('principalForm').addEventListener('change', changed);
    el('principalExport').addEventListener('click', exportResult);
    el('principalAccountSearch').addEventListener('input', accountChoices);
    // Revoke before the host changes authority, including same-case reloads.
    const originalSync = syncContextSelection;
    syncContextSelection = function (...args) { const result = originalSync.apply(this, args); sync(); return result; };
    const originalLoad = loadContext;
    loadContext = function (...args) { revoke(); return originalLoad.apply(this, args); };
    const originalClose = closeContext;
    closeContext = function (...args) { revoke(); return originalClose.apply(this, args); };
    const originalCase = selectCase;
    selectCase = function (...args) { revoke(); return originalCase.apply(this, args); };
    const originalCanvas = activateCanvas;
    activateCanvas = function (...args) { revoke(); return originalCanvas.apply(this, args); };
    const originalGraph = readGraphData;
    readGraphData = function (...args) { revoke(); return originalGraph.apply(this, args); };
    for (const name of ['funds:graph-metric', 'funds:account-state']) root.addEventListener(name, sync);
    root.addEventListener('funds:import-state', revoke);
    sync();
  }
  root.FundsPrincipal = { open, close, sync, revoke };
  if (document.readyState === 'loading') root.addEventListener('DOMContentLoaded', mount); else mount();
})(window);
