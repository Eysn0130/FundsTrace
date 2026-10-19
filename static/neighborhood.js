/* Binding-scoped neighborhood drafts. The toolbar owns the floating surface. */
(function (root) {
  'use strict';
  const directions = { both: '双向邻居', in: '转入来源', out: '转出去向' };
  const copyApplied = value => ({ root: '', name: '', direction: 'both', nodes: 0, edges: 0, expanded: 0, remaining: 0, totalNeighbors: 0, ...value });
  const draftKey = value => JSON.stringify([value.account || '', value.direction]);
  const positive = value => /^\d+$/.test(String(value ?? '')) && /[1-9]/.test(String(value));

  function create(options) {
    const doc = options.document || root.document, el = id => doc.getElementById(id);
    const panel = el('neighborPopover'), trigger = el('btnNeighborActions');
    const field = el('neighborAccount'), directionField = el('neighborDirection');
    const format = options.formatCount || (value => root.FundsMoney?.count(value) ?? String(value));
    let shown = false, binding, snapshot, applied = copyApplied(), draft = { account: '', direction: 'both' };
    let selected = null, results = [], resultTotal = 0, searched = false, failure = '', locateMessage = '';
    let session = 0, previewSerial = 0, searchSerial = 0, preview = null, previewPending = false, searchPending = false;
    let pending = null, source = null;

    const currentJob = () => pending && pending.binding === binding;
    const busy = () => String(options.blocker?.() || (!snapshot?.ready ? '当前画布尚未就绪' : ''));
    const dirty = () => draftKey(draft) !== draftKey({ account: applied.root, direction: applied.direction });
    const stamp = value => ({ binding: value.binding, revision: value.revision, source: value.source, ready: value.ready });
    const sameStamp = (a, b) => !!a && Object.keys(b).every(key => a[key] === b[key]);
    const stillCurrent = (opened, saved) => shown && opened === session && sameStamp(saved, stamp(options.read()));
    const setText = (id, value) => { const node = el(id); if (node) node.textContent = value; };
    function accounts(value = snapshot) {
      const seen = new Set(), all = [];
      for (const item of value?.accounts || []) {
        const account = String(item?.account ?? '').trim();
        if (!account || seen.has(account)) continue;
        seen.add(account); all.push({ account, name: String(item.name || '') });
      }
      return all;
    }
    function exactAccount(value = draft.account, current = snapshot) {
      const account = String(value).trim();
      return accounts(current).find(item => item.account === account) || null;
    }
    function showCount(id, value, unit) {
      const node = el(id); if (!node) return;
      const text = value == null ? '—' : format(value), parts = String(text).split(',');
      node.replaceChildren(...parts.flatMap((part, index) => index === parts.length - 1
        ? [doc.createTextNode(part)] : [doc.createTextNode(part + ','), doc.createElement('wbr')]));
      node.title = `${text} ${unit}`;
      node.setAttribute('data-size', text.length > 18 ? 'extended' : text.length > 10 ? 'long' : 'normal');
    }
    function cancelWork() {
      previewSerial += 1; searchSerial += 1; previewPending = false; searchPending = false; preview = null;
    }
    function writeDraft() {
      field.value = draft.account; directionField.value = draft.direction;
    }
    function renderResults() {
      const container = el('neighborSearchResults'); if (!container) return;
      const buttons = results.map(item => {
        const button = doc.createElement('button'); button.type = 'button'; button.className = 'neighborhood-result';
        const name = doc.createElement('span'); name.textContent = item.name || '未填写户名';
        const account = doc.createElement('span'); account.textContent = item.account; account.className = 'neighborhood-account';
        button.replaceChildren(name, account); button.disabled = !!currentJob() || searchPending || !!busy();
        button.addEventListener('click', () => { void choose(item.account); }); return button;
      });
      container.replaceChildren(...buttons); container.hidden = !results.length;
      setText('neighborSearchCount', results.length ? `找到 ${format(resultTotal)} 个账户${resultTotal > 20 ? '，显示前 20 个；可补全账号或户名缩小范围' : '，请选择中心账户'}` : '');
      el('neighborSearchCount').hidden = !results.length;
    }
    function render() {
      const job = currentJob(), reason = busy(), locked = !!job || searchPending || !!reason;
      field.disabled = locked; directionField.disabled = locked;
      for (const key of Object.keys(directions)) {
        const button = el(`neighborDirection${key[0].toUpperCase()}${key.slice(1)}`);
        if (button) { button.disabled = locked; button.setAttribute('aria-pressed', String(draft.direction === key)); }
      }
      const searchButton = el('btnNeighborSearch');
      if (searchButton) { searchButton.disabled = locked || !draft.account.trim(); searchButton.setAttribute('aria-busy', String(searchPending)); }
      setText('neighborResolvedName', selected ? selected.name || '未填写户名' : '');
      el('neighborResolvedName').hidden = !selected;
      setText('neighborDraftState', job ? '正在应用' : dirty() ? '待应用' : applied.root ? '已应用' : '待选择');
      el('neighborDraftState').setAttribute('data-state', job ? 'working' : dirty() ? 'draft' : applied.root ? 'applied' : 'draft');
      showCount('neighborPreviewNodes', preview?.nodes, '个账户'); showCount('neighborPreviewEdges', preview?.edges, '条关系');
      setText('neighborAppliedAccount', applied.root || '尚未查看邻域');
      setText('neighborAppliedName', applied.root ? applied.name || '未填写户名' : '当前显示完整追踪图');
      setText('neighborAppliedDirection', applied.root ? directions[applied.direction] || directions.both : '—');
      showCount('neighborAppliedNodes', applied.nodes, '个账户'); showCount('neighborAppliedEdges', applied.edges, '条关系');
      showCount('neighborExpandedCount', applied.expanded, '个邻居'); showCount('neighborRemainingCount', applied.remaining, '个邻居');
      const expand = el('btnExpandNeighbors'), apply = el('btnNeighborhood');
      if (expand) {
        expand.disabled = locked || !applied.root || !positive(applied.remaining);
        expand.title = !applied.root ? '查看邻域后可继续展开' : positive(applied.remaining) ? '按右侧已应用的中心与方向继续展开，保留待应用条件' : '已无可展开的邻居';
        expand.setAttribute('aria-busy', String(!!job && job.kind === 'expand'));
      }
      setText('neighborExpandHint', !applied.root ? '查看邻域后可继续展开' : positive(applied.remaining) ? '按当前邻域的中心与方向继续展开' : '已无可展开的邻居');
      if (apply) {
        apply.disabled = locked || !selected || !dirty() || previewPending || !preview || preview.rootFound === false;
        apply.setAttribute('aria-busy', String(!!job && job.kind === 'apply'));
      }
      let status = failure || locateMessage || reason;
      if (!status && preview?.rootFound === false) status = '中心账户已不在当前追踪图，请重新搜索';
      if (!status && preview && String(preview.nodes) === '0') status = '当前显示条件下没有可见账户；可调整关系筛选或账户显示条件';
      if (!status && !selected) status = searched ? '请选择一个中心账户后查看邻域' : '输入完整账号，或搜索卡号、账号、户名';
      if (!status) status = '查看邻域后更新画布范围；保留关系筛选与账户显示条件';
      setText('neighborStatus', status); el('neighborStatus').setAttribute('data-state', failure ? 'error' : locateMessage ? 'notice' : 'ready');
      panel?.setAttribute('aria-busy', String(!!job || searchPending || previewPending));
      renderResults();
      if (shown) root.FundsToolbarMenu?.place(panel, trigger);
    }
    async function requestPreview() {
      const serial = ++previewSerial, opened = session, saved = stamp(snapshot);
      preview = null; previewPending = false;
      if (!shown || !snapshot?.ready || !selected) { render(); return; }
      const value = { account: selected.account, direction: draft.direction };
      previewPending = true; render();
      try {
        const result = await options.preview(value, snapshot);
        if (serial !== previewSerial || !stillCurrent(opened, saved) || draftKey(value) !== draftKey(draft)) return;
        preview = result ? { nodes: result.nodes, edges: result.edges, rootFound: result.rootFound !== false, remaining: result.remaining } : null;
      } catch (error) {
        if (serial === previewSerial && stillCurrent(opened, saved)) failure = String(error?.message || '预计结果暂不可用，请重试').slice(0, 300);
      } finally {
        if (serial === previewSerial && opened === session) { previewPending = false; render(); }
      }
    }
    function end() {
      shown = false; session += 1; cancelWork(); results = []; resultTotal = 0; searched = false; failure = ''; locateMessage = '';
      draft = { account: applied.root, direction: applied.direction }; selected = exactAccount(applied.root); source = null;
    }
    function close(reason = 'close') {
      if (!shown) return false;
      root.FundsToolbarMenu?.close(reason);
      if (shown) end();
      return true;
    }
    function update() {
      const latest = options.read(), nextApplied = copyApplied(latest.applied);
      if (binding !== undefined && binding !== latest.binding) { close('context'); end(); pending = null; }
      const wasDirty = dirty(), changedApplied = draftKey({ account: applied.root, direction: applied.direction }) !== draftKey({ account: nextApplied.root, direction: nextApplied.direction });
      const nextSource = stamp(latest), changedSource = !sameStamp(source, nextSource);
      binding = latest.binding; snapshot = latest; applied = nextApplied; source = nextSource;
      if (!shown || !wasDirty && changedApplied) {
        draft = { account: applied.root, direction: applied.direction }; selected = exactAccount(applied.root);
        failure = ''; locateMessage = ''; results = []; searched = false; writeDraft();
      } else if (changedSource) {
        cancelWork(); selected = exactAccount(draft.account); results = []; searched = false; failure = ''; locateMessage = '';
      }
      if (shown && changedSource) void requestPreview(); else render();
      return getState();
    }
    function begin() {
      cancelWork(); session += 1; snapshot = options.read(); binding = snapshot.binding; source = stamp(snapshot);
      applied = copyApplied(snapshot.applied); draft = { account: applied.root, direction: applied.direction };
      selected = exactAccount(applied.root); results = []; resultTotal = 0; searched = false; failure = ''; locateMessage = ''; shown = true;
      writeDraft(); void requestPreview();
    }
    function input() {
      if (!shown || currentJob() || searchPending) return;
      if (options.read().binding !== binding) { update(); return; }
      searchSerial += 1; draft = { account: field.value.trim(), direction: directionField.value in directions ? directionField.value : 'both' };
      selected = exactAccount(); results = []; resultTotal = 0; searched = false; failure = ''; locateMessage = ''; void requestPreview();
    }
    async function choose(account) {
      update();
      if (!shown || currentJob() || searchPending || busy()) return false;
      const found = exactAccount(account);
      if (!found) { failure = '该账户已不在当前追踪图，请重新搜索'; results = []; render(); return false; }
      const serial = ++searchSerial, opened = session, saved = stamp(snapshot);
      selected = found; draft.account = found.account; field.value = found.account; results = []; searched = true;
      failure = ''; locateMessage = ''; searchPending = true; void requestPreview(); render();
      try {
        const result = await options.locate(found.account, binding);
        if (serial !== searchSerial || !stillCurrent(opened, saved)) return false;
        if (!result?.ok) locateMessage = String(result?.reason || '该账户当前不可见；查看邻域后将按现有显示条件展示').slice(0, 300);
        return result?.ok === true;
      } catch (error) {
        if (serial === searchSerial && stillCurrent(opened, saved)) failure = String(error?.message || '暂时无法定位该账户，请重试').slice(0, 300);
        return false;
      } finally {
        if (serial === searchSerial && opened === session) { searchPending = false; render(); }
      }
    }
    async function search() {
      update();
      if (!shown || currentJob() || searchPending || busy()) return false;
      const query = field.value.trim(); draft.account = query; failure = ''; locateMessage = ''; searched = true; resultTotal = 0;
      if (!query) { selected = null; results = []; failure = '请输入卡号、账号或户名'; void requestPreview(); field.focus?.(); return false; }
      const exact = exactAccount(query), needle = query.toLocaleLowerCase();
      if (exact) return choose(exact.account);
      const matches = accounts().filter(item => item.account.toLocaleLowerCase().includes(needle) || item.name.toLocaleLowerCase().includes(needle));
      if (matches.length === 1) return choose(matches[0].account);
      selected = null; results = matches.slice(0, 20); resultTotal = matches.length;
      if (!matches.length) failure = '当前画布的已追踪账户中没有匹配结果';
      void requestPreview(); return false;
    }
    async function submit(kind) {
      update();
      if (!shown || currentJob() || searchPending || busy()) return false;
      if (kind === 'expand' ? !applied.root || !positive(applied.remaining) : !selected || !dirty() || previewPending || !preview || preview.rootFound === false) { render(); return false; }
      const job = { binding, session, kind }; pending = job; failure = ''; locateMessage = ''; render();
      try {
        const ok = kind === 'expand' ? await options.expand(job.binding) === true
          : await options.commit({ account: selected.account, direction: draft.direction }, job.binding) === true;
        if (pending !== job || job.binding !== options.read().binding) return false;
        if (shown && job.session === session) {
          if (ok && kind === 'apply') {
            snapshot = options.read(); applied = copyApplied(snapshot.applied); draft = { account: applied.root, direction: applied.direction };
            selected = exactAccount(); results = []; searched = false; writeDraft();
          } else if (!ok) failure = String(options.blocker?.() || '本次操作未完成，请核对当前画布后重试');
        }
        return ok;
      } catch (error) {
        if (pending === job && shown && job.session === session && job.binding === options.read().binding)
          failure = String(error?.message || '操作未能完成，请稍后重试').slice(0, 300);
        return false;
      } finally {
        if (pending === job) { pending = null; update(); if (shown) void requestPreview(); }
      }
    }
    function getState() {
      return { open: shown, binding, pending: !!currentJob(), applied: { ...applied }, draft: { ...draft }, selected: selected ? { ...selected } : null,
        dirty: dirty(), searchPending, results: results.map(item => ({ ...item })), resultTotal, preview: preview ? { ...preview } : null,
        previewPending, error: failure, locateMessage, busy: busy() };
    }
    doc.addEventListener('funds:toolbar-menu-change', event => {
      if (event.detail?.menuId !== 'neighborPopover') return;
      if (event.detail.open) begin(); else { end(); update(); }
    });
    field?.addEventListener('input', input); directionField?.addEventListener('change', input);
    for (const key of Object.keys(directions)) el(`neighborDirection${key[0].toUpperCase()}${key.slice(1)}`)?.addEventListener('click', () => {
      if (!shown || currentJob() || searchPending || busy()) return;
      directionField.value = key; input();
    });
    panel?.addEventListener('keydown', event => {
      if (event.target !== field || event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
      event.preventDefault(); event.stopPropagation(); if (!event.repeat) void search();
    });
    el('btnNeighborSearch')?.addEventListener('click', () => { void search(); });
    el('btnNeighborhood')?.addEventListener('click', () => { void submit('apply'); });
    el('btnExpandNeighbors')?.addEventListener('click', () => { void submit('expand'); });
    el('btnNeighborClose')?.addEventListener('click', () => close('close'));
    update();
    return { update, open: () => root.FundsToolbarMenu?.open(trigger) || false, close, getState, search, apply: () => submit('apply'), expand: () => submit('expand') };
  }
  root.FundsNeighborhood = Object.freeze({ create });
})(window);
