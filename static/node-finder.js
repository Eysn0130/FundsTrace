/* Find current graph members without changing selection during camera previews. */
(function (root) {
  'use strict';
  const pageSize = 20;

  function create(options) {
    const doc = options.document || root.document, el = id => doc.getElementById(id);
    const panel = el('findNodePopover'), trigger = el('btnFindNode'), field = el('graphFindInput');
    const list = el('graphFindResults');
    const format = options.formatCount || (value => root.FundsMoney?.count(value) ?? String(value));
    let shown = false, binding, snapshot, source = null, session = 0, serial = 0, pending = null;
    let query = String(field?.value || ''), searched = false, results = [], visibleCount = 0;
    let activeAccount = '', failure = '', message = '', outcome = '', rendered = [];
    const stamp = value => ({ binding: value.binding, revision: value.revision, source: value.source, ready: value.ready });
    const sameStamp = (a, b) => !!a && !!b && a.binding === b.binding && a.revision === b.revision && a.source === b.source && a.ready === b.ready;
    const blocked = () => String(options.blocker?.() || (!snapshot?.ready ? '当前画布尚未就绪' : ''));
    const text = (id, value) => { const node = el(id); if (node) node.textContent = value; };
    const normalize = value => String(value ?? '').trim().toLocaleLowerCase();

    function accounts(value = snapshot) {
      const seen = new Set(), items = [];
      for (const item of value?.accounts || []) {
        const account = String(item?.account ?? '').trim();
        if (!account || seen.has(account)) continue;
        seen.add(account); items.push({ account, name: String(item?.name || '') });
      }
      return items;
    }
    function recent(value = snapshot) {
      const ids = new Set((value?.recent || []).map(item => String(item)));
      return accounts(value).filter(item => ids.has(item.account));
    }
    function count(id, value, suffix = '', prefix = '') {
      const node = el(id); if (!node) return;
      const label = String(format(value));
      node.replaceChildren(doc.createTextNode(prefix), ...label.split(',').flatMap((part, index, parts) => index < parts.length - 1
        ? [doc.createTextNode(part + ','), doc.createElement('wbr')] : [doc.createTextNode(part)]), doc.createTextNode(suffix));
      node.title = prefix + label + suffix;
      node.setAttribute('data-size', label.length > 18 ? 'extended' : label.length > 10 ? 'long' : 'normal');
    }
    function cancelWork() {
      const ownCamera = pending?.kind === 'locate';
      pending = null; serial += 1;
      if (ownCamera) options.cancel?.();
    }
    function resetResults() {
      options.clearPreview?.();
      results = []; visibleCount = 0; searched = false; activeAccount = ''; failure = ''; message = ''; outcome = '';
    }
    function row(item) {
      const button = doc.createElement('button'); button.type = 'button'; button.className = 'find-result';
      button.setAttribute('data-account', item.account);
      button.setAttribute('aria-label', `定位 ${item.name || '未填写户名'}，${item.account}`);
      const lines = doc.createElement('span'); lines.className = 'find-result-text';
      const name = doc.createElement('span'); name.className = 'find-result-name'; name.textContent = item.name || '未填写户名';
      const account = doc.createElement('span'); account.className = 'find-result-account'; account.textContent = item.account;
      const icon = doc.createElement('img'); icon.className = 'ui-icon'; icon.src = '/static/ui-icons/target.svg'; icon.alt = '';
      lines.replaceChildren(name, account); button.replaceChildren(lines, icon);
      button.addEventListener('click', () => { void locate(item.account); });
      return { ...item, button };
    }
    function renderResults(reason) {
      if (!list) return;
      const visible = results.slice(0, visibleCount);
      const compatible = rendered.length <= visible.length && rendered.every((item, index) => item.account === visible[index].account && item.name === visible[index].name);
      if (!compatible) { rendered = []; list.replaceChildren(); }
      for (const item of visible.slice(rendered.length)) {
        const entry = row(item); rendered.push(entry); list.appendChild(entry.button);
      }
      for (const item of rendered) {
        const current = item.account === activeAccount;
        item.button.classList.toggle('is-current', current);
        if (current) item.button.setAttribute('aria-current', 'true'); else item.button.removeAttribute('aria-current');
        item.button.disabled = !!reason;
        item.button.setAttribute('aria-disabled', String(!!reason || pending?.key === `account:${item.account}`));
        item.button.setAttribute('aria-busy', String(pending?.key === `account:${item.account}`));
      }
      list.hidden = !visible.length;
    }
    function render() {
      const reason = blocked(), total = accounts().length, newTotal = recent().length;
      text('graphFindScope', `当前显示范围 · ${format(total)} 个账户`);
      count('graphFindCount', results.length, ' 个', '匹配结果 · ');
      count('graphFindNewCount', newTotal);
      const heading = el('graphFindCount'); if (heading) heading.hidden = !searched;
      const search = el('btnGraphFindSearch'), clear = el('btnGraphFindClear'), more = el('btnGraphFindMore');
      if (search) { search.disabled = !!reason || !query.trim() || pending?.key === `search:${normalize(query)}`; search.setAttribute('aria-busy', String(pending?.key === `search:${normalize(query)}`)); }
      if (clear) clear.disabled = !query && !searched && !pending;
      if (field) field.disabled = false;
      if (more) {
        more.hidden = results.length <= visibleCount; more.disabled = !!reason;
        more.textContent = `显示更多（${format(visibleCount)} / ${format(results.length)}）`;
      }
      for (const [id, key, available] of [
        ['btnGraphFindAll', 'all', results.length > 0], ['btnGraphFindSelect', 'select', results.length > 0], ['btnGraphFindNew', 'new', newTotal > 0]
      ]) {
        const button = el(id); if (!button) continue;
        button.disabled = !!reason || !available || pending?.key === key;
        button.setAttribute('aria-busy', String(pending?.key === key));
      }
      text('graphFindStatus', failure || reason || (pending ? pending.kind === 'select' ? '正在选择…' : '正在定位…' : message));
      el('graphFindStatus')?.setAttribute('data-state', failure ? 'error' : reason ? 'notice' : pending ? 'working' : outcome || 'ready');
      panel?.setAttribute('aria-busy', String(!!pending));
      renderResults(reason);
      if (shown) root.FundsToolbarMenu?.place(panel, trigger);
    }
    function end() {
      shown = false; session += 1; cancelWork(); resetResults(); render();
    }
    function close(reason = 'close') {
      if (!shown) return false;
      root.FundsToolbarMenu?.close(reason);
      if (shown) end();
      return true;
    }
    function update() {
      const latest = options.read(), nextSource = stamp(latest), changedBinding = binding !== undefined && binding !== latest.binding;
      if (changedBinding) {
        close('context'); cancelWork(); resetResults(); query = ''; if (field) field.value = '';
      } else if (source && !sameStamp(source, nextSource)) {
        cancelWork(); resetResults();
      }
      binding = latest.binding; snapshot = latest; source = nextSource;
      if (pending && blocked()) cancelWork();
      render();
      return getState();
    }
    function begin() {
      update(); cancelWork(); session += 1; shown = true; resetResults();
      query = String(field?.value || ''); render();
    }
    function input() {
      update(); cancelWork(); query = String(field?.value || ''); resetResults(); render();
    }
    function current(job) {
      return shown && pending === job && job.serial === serial && job.session === session && sameStamp(job.stamp, stamp(options.read()));
    }
    async function run(kind, key, ids, success) {
      update();
      if (!shown || blocked() || !ids.length || pending?.key === key) return false;
      const valid = new Set(accounts().map(item => item.account));
      const unique = Array.from(new Set(ids.map(item => String(item)))).filter(id => valid.has(id));
      if (!unique.length || unique.length !== new Set(ids.map(item => String(item))).size) {
        failure = '账户已不在当前显示范围，请重新查找'; render(); return false;
      }
      cancelWork(); failure = ''; message = ''; outcome = '';
      const job = { kind, key, serial, session, stamp: stamp(snapshot) }; pending = job; render();
      try {
        const result = await (kind === 'select' ? options.select(unique, snapshot) : options.locate(unique, snapshot));
        if (!current(job)) return false;
        if (result?.ok !== true) { failure = String(result?.reason || '本次操作未完成，请重试').slice(0, 300); return false; }
        if (kind === 'locate') activeAccount = unique.length === 1 ? unique[0] : '';
        message = success(unique); outcome = 'done'; return true;
      } catch (error) {
        if (current(job)) failure = String(error?.message || '本次操作未完成，请重试').slice(0, 300);
        return false;
      } finally {
        if (pending === job) { pending = null; update(); }
      }
    }
    async function search(value) {
      update();
      if (!shown || blocked()) return false;
      if (typeof value === 'string' && field) field.value = value;
      query = String(field?.value || '');
      const needle = normalize(query), key = `search:${needle}`;
      if (pending?.key === key) return false;
      cancelWork(); resetResults(); searched = true;
      if (!needle) { failure = '请输入卡号、账号或户名'; render(); field?.focus({ preventScroll: true }); return false; }
      const items = accounts(), exact = items.find(item => normalize(item.account) === needle);
      results = exact ? [exact] : items.filter(item => normalize(item.account).includes(needle) || normalize(item.name).includes(needle));
      visibleCount = Math.min(pageSize, results.length);
      if (!results.length) { message = '当前显示范围内没有匹配账户'; outcome = 'notice'; }
      render();
      if (results.length !== 1) return false;
      return run('locate', key, [results[0].account], ids => `已定位 · ${results.find(item => item.account === ids[0])?.name || ids[0]}`);
    }
    function locate(account) {
      update();
      const item = results.find(item => item.account === String(account));
      if (!item) return Promise.resolve(false);
      return run('locate', `account:${item.account}`, [item.account], () => `已定位 · ${item.name || item.account}`);
    }
    function locateAll() {
      update();
      return run('locate', 'all', results.map(item => item.account), ids => `已定位全部 ${format(ids.length)} 个账户`);
    }
    function locateNew() {
      update();
      return run('locate', 'new', recent().map(item => item.account), ids => `已定位新增 ${format(ids.length)} 个账户`);
    }
    function selectResults() {
      update();
      return run('select', 'select', results.map(item => item.account), ids => `已选中 ${format(ids.length)} 个账户`);
    }
    function more() {
      update();
      if (!shown || blocked()) return;
      visibleCount = Math.min(results.length, visibleCount + pageSize); render();
    }
    function getState() {
      return { open: shown, binding, query, searched, results: results.map(item => ({ ...item })), resultTotal: results.length, visibleCount,
        activeAccount, pending: !!pending, pendingKind: pending?.kind || '', error: failure,
        status: failure || blocked() || message, busy: blocked(), recentCount: recent().length };
    }
    doc.addEventListener('funds:toolbar-menu-change', event => {
      if (event.detail?.menuId !== 'findNodePopover') return;
      if (event.detail.open) begin(); else end();
    });
    field?.addEventListener('input', input);
    panel?.addEventListener('keydown', event => {
      event.stopPropagation();
      if (event.target !== field || event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
      event.preventDefault(); if (!event.repeat) void search();
    });
    list?.addEventListener('keydown', event => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      const buttons = rendered.map(item => item.button).filter(button => !button.disabled);
      const index = buttons.indexOf(event.target?.closest?.('.find-result'));
      if (index < 0 || !buttons.length) return;
      event.preventDefault(); event.stopPropagation();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)));
      buttons[next].focus({ preventScroll: true }); buttons[next].scrollIntoView?.({ block: 'nearest' });
    });
    el('btnGraphFindSearch')?.addEventListener('click', () => { void search(); });
    el('btnGraphFindClear')?.addEventListener('click', () => { if (field) field.value = ''; input(); field?.focus({ preventScroll: true }); });
    el('btnGraphFindClose')?.addEventListener('click', () => close('close'));
    el('btnGraphFindMore')?.addEventListener('click', more);
    el('btnGraphFindAll')?.addEventListener('click', () => { void locateAll(); });
    el('btnGraphFindNew')?.addEventListener('click', () => { void locateNew(); });
    el('btnGraphFindSelect')?.addEventListener('click', () => { void selectResults(); });
    update();
    return { update, open: () => root.FundsToolbarMenu?.open(trigger) || false, close, search, locate, locateAll, locateNew, selectResults, getState };
  }
  root.FundsNodeFinder = Object.freeze({ create });
})(window);
