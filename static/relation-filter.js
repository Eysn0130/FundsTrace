/* Drafted, binding-scoped relation filtering; the shared toolbar owns its overlay. */
(function (root) {
  'use strict';
  const empty = () => ({ currency: null, min: '', max: '', active: false });
  const copy = value => ({ currency: value.currency, min: value.min || '', max: value.max || '', active: !!value.active });
  const key = value => JSON.stringify([value.currency, value.min, value.max]);
  const currencyOption = value => value === null ? 'all' : 'currency:' + value;
  const currencyName = value => value === '' ? '未标注币种' : value;

  function create(options) {
    const doc = options.document || root.document, el = id => doc.getElementById(id);
    const panel = el('relationFilterPopover'), trigger = el('btnRelationFilter');
    const fields = { currency: el('relationCurrency'), min: el('relationMin'), max: el('relationMax') };
    const errorsFor = { currency: el('relationCurrencyError'), min: el('relationMinError'), max: el('relationMaxError') };
    const format = options.formatCount || (value => root.FundsMoney.count(value));
    let shown = false, binding, snapshot, applied = empty(), draft = empty(), errors = {}, failure = '';
    let session = 0, pending = null, timer = null, previewSerial = 0, preview = null, previewPending = false;
    let previewSource = null, choicesKey = '';

    const validate = value => root.FundsGraphRelations.validateFilter(value);
    const dirty = () => { const result = validate(draft); return !result.ok || key(result.filter) !== key(applied); };
    const busy = () => String(options.blocker?.() || (!snapshot?.ready ? '当前画布尚未就绪' : ''));
    const currentJob = () => pending && pending.binding === binding;
    function setText(id, value) { const node = el(id); if (node) node.textContent = value; }
    function cancelPreview() {
      if (timer !== null) { root.clearTimeout(timer); timer = null; }
      previewSerial += 1; previewPending = false;
    }
    function currencies() {
      const choices = [], seen = new Set();
      for (const choice of snapshot?.currencies || []) {
        if (!choice || typeof choice.value !== 'string' || seen.has(choice.value)) continue;
        seen.add(choice.value); choices.push({ value: choice.value, label: String(choice.label || currencyName(choice.value)) });
      }
      for (const value of [applied.currency, draft.currency]) {
        if (value === null || seen.has(value)) continue;
        seen.add(value); choices.push({ value, label: `${currencyName(value)}（当前图无此币种）` });
      }
      const nextKey = JSON.stringify(choices);
      if (nextKey !== choicesKey) {
        const children = [{ value: null, label: '全部币种（不筛选）' }, ...choices].map(choice => {
          const option = doc.createElement('option');
          option.value = currencyOption(choice.value); option.textContent = choice.label; return option;
        });
        fields.currency.replaceChildren(...children); choicesKey = nextKey;
      }
      fields.currency.value = currencyOption(draft.currency);
    }
    function writeDraft() {
      currencies(); fields.min.value = draft.min; fields.max.value = draft.max;
    }
    function appliedSummary() {
      if (!applied.active) return '已应用：全部关系';
      const label = (snapshot?.currencies || []).find(choice => choice.value === applied.currency)?.label || currencyName(applied.currency);
      return `已应用：${label} · ${applied.min || '不限下界'} — ${applied.max || '不限上界'}`;
    }
    function showCount(id, value, unit) {
      const node = el(id); if (!node) return;
      const text = value == null ? '—' : format(value);
      const groups = text.split(',');
      node.replaceChildren(...groups.flatMap((group, index) => index === groups.length - 1
        ? [doc.createTextNode(group)] : [doc.createTextNode(group + ','), doc.createElement('wbr')]));
      node.title = `${text} ${unit}`;
      node.setAttribute('data-size', text.length > 18 ? 'extended' : text.length > 10 ? 'long' : 'normal');
      node.setAttribute('aria-label', `预计显示 ${text} ${unit}`);
    }
    function render() {
      const validation = validate(draft), changed = dirty(), reason = busy(), job = currentJob();
      setText('relationAppliedSummary', appliedSummary());
      const draftState = el('relationDraftState');
      if (draftState) {
        draftState.textContent = job ? '正在应用' : changed ? '待应用' : '已应用';
        draftState.setAttribute('data-state', job ? 'working' : changed ? 'draft' : 'applied');
      }
      for (const name of ['currency', 'min', 'max']) {
        const text = errors[name] || '', node = errorsFor[name];
        if (node) { node.textContent = text; node.hidden = !text; }
        fields[name].setAttribute('aria-invalid', String(!!text));
        fields[name].disabled = !!job || !snapshot?.ready || (name !== 'currency' && draft.currency === null);
      }
      showCount('relationPreviewEdges', preview?.edges, '条关系');
      showCount('relationPreviewNodes', preview?.nodes, '个账户');
      const view = snapshot?.view;
      const currentNodes = view?.index?.nodeIds?.size, currentEdges = view?.index?.edgeKeys?.size;
      const totalNodes = snapshot?.graph?.nodes?.length ?? 0, totalEdges = snapshot?.graph?.edges?.length ?? 0;
      setText('relationCurrentCounts', `当前显示 ${format(currentEdges ?? 0)} 条关系 · ${format(currentNodes ?? 0)} 个账户；全追踪图 ${format(totalEdges)} 条关系 · ${format(totalNodes)} 个账户`);
      const busyNode = el('relationFilterBusy');
      if (busyNode) { busyNode.textContent = job ? '正在应用筛选' : reason; busyNode.hidden = !job && !reason; }
      const apply = el('btnRelationApply'), clear = el('btnRelationClear');
      if (apply) { apply.disabled = !!job || !!reason || !validation.ok || !changed; apply.setAttribute('aria-busy', String(!!job)); }
      if (clear) {
        clear.disabled = !!job || !!reason || !applied.active;
        clear.title = applied.active ? '仅清除已应用的关系筛选，保留邻域及账户显示条件' : '当前没有已应用的关系筛选';
      }
      panel?.setAttribute('aria-busy', String(!!job || previewPending));
      let status = failure;
      if (!status && !validation.ok && Object.keys(errors).length) status = '请修正条件后应用';
      if (!status && preview && String(preview.edges) === '0') status = !totalEdges ? '当前画布暂无追踪关系' : applied.active
        ? '当前显示范围内没有符合条件的关系，可修改条件或清除已应用筛选' : '当前显示范围内没有符合条件的关系，可修改待应用条件';
      if (!status) status = '保留邻域及账户显示条件；不改变追踪数据和左侧流水查询';
      setText('relationFilterStatus', status);
      el('relationFilterStatus')?.setAttribute('data-state', failure ? 'error' : preview && String(preview.edges) === '0' ? 'empty' : 'ready');
      if (shown) root.FundsToolbarMenu?.place(panel, trigger);
    }
    function sourceKey(value) {
      return { graph: value.graph, nodes: value.graph?.nodes, edges: value.graph?.edges,
        presentation: value.presentation, view: value.view, revision: value.view?.revision, ids: value.visibleIds,
        scope: JSON.stringify([value.presentation?.hiddenAccounts || [], value.presentation?.onlyAccounts ?? null]),
        filter: key(value.filter || empty()), ready: value.ready };
    }
    function sameSource(left, right) {
      return !!left && Object.keys(right).every(name => left[name] === right[name]);
    }
    function requestPreview(immediate = false) {
      cancelPreview(); preview = null;
      const validation = validate(draft);
      if (!shown || !snapshot?.ready || !validation.ok) { render(); return; }
      const token = previewSerial, opened = session, bound = binding;
      previewPending = true; render();
      const run = async () => {
        timer = null;
        const latest = options.read();
        if (!shown || token !== previewSerial || opened !== session || bound !== latest.binding) { update(); return; }
        try {
          const result = await options.preview(validation.filter, latest);
          if (!shown || token !== previewSerial || opened !== session || bound !== options.read().binding) return;
          preview = { nodes: result.nodes, edges: result.edges };
        } catch (error) {
          if (!shown || token !== previewSerial || opened !== session || bound !== options.read().binding) return;
          failure = String(error?.message || '预计结果暂不可用，请稍后重试').slice(0, 300);
        } finally {
          if (token === previewSerial && opened === session) { previewPending = false; render(); }
        }
      };
      if (immediate) void run();
      else timer = root.setTimeout(run, options.previewDelay ?? 160);
    }
    function end() {
      shown = false; session += 1; cancelPreview(); preview = null;
      draft = copy(applied); errors = {}; failure = ''; previewSource = null;
    }
    function close(reason = 'close') {
      if (!shown) return false;
      root.FundsToolbarMenu?.close(reason);
      if (shown) end();
      return true;
    }
    function update() {
      const latest = options.read(), nextApplied = copy(latest.filter || empty());
      if (binding !== undefined && latest.binding !== binding) {
        close('context'); end(); pending = null; choicesKey = '';
      }
      const wasDirty = dirty(), changedApplied = key(applied) !== key(nextApplied);
      binding = latest.binding; snapshot = latest; applied = nextApplied;
      if (!shown || (!wasDirty && changedApplied)) { draft = copy(applied); errors = {}; failure = ''; }
      currencies();
      if (!shown || (!wasDirty && changedApplied)) writeDraft();
      const nextSource = sourceKey(latest), changedSource = !sameSource(previewSource, nextSource);
      previewSource = nextSource;
      if (shown && changedSource) requestPreview();
      else render();
      return getState();
    }
    function begin() {
      cancelPreview(); session += 1;
      snapshot = options.read(); binding = snapshot.binding; applied = copy(snapshot.filter || empty());
      draft = copy(applied); errors = {}; failure = ''; shown = true;
      previewSource = sourceKey(snapshot); writeDraft(); requestPreview(true);
    }
    function input() {
      if (!shown || currentJob()) return;
      const current = options.read();
      if (current.binding !== binding) { update(); return; }
      const selected = fields.currency.value;
      draft = { currency: selected === 'all' ? null : selected.startsWith('currency:') ? selected.slice(9) : null,
        min: fields.min.value, max: fields.max.value, active: selected !== 'all' };
      if (draft.currency === null) { draft.min = ''; draft.max = ''; fields.min.value = ''; fields.max.value = ''; }
      failure = ''; errors = {};
      const result = validate(draft);
      if (!result.ok && result.field) errors[result.field] = result.error;
      requestPreview();
    }
    async function submit(clear = false) {
      update();
      if (!shown || currentJob()) return false;
      const reason = busy();
      if (reason) { render(); return false; }
      const result = validate(clear ? empty() : draft);
      if (!result.ok) {
        errors = result.field ? { [result.field]: result.error } : {};
        failure = result.field ? '' : result.error; render(); fields[result.field]?.focus?.(); return false;
      }
      if (key(result.filter) === key(applied)) return false;
      const job = { binding, session }; pending = job; failure = ''; errors = {}; cancelPreview(); render();
      let committed = false;
      try {
        committed = await options.commit(result.filter, job.binding) === true;
        if (pending !== job || job.binding !== options.read().binding) return committed;
        if (shown && job.session === session) {
          if (committed) { snapshot = options.read(); applied = copy(snapshot.filter || empty()); draft = copy(applied); writeDraft(); }
          else failure = String(options.blocker?.() || '本次筛选未应用，请核对当前画布后重试');
        }
        return committed;
      } catch (error) {
        if (pending === job && shown && job.session === session && job.binding === options.read().binding)
          failure = String(error?.message || '筛选未能应用，请稍后重试').slice(0, 300);
        return false;
      } finally {
        if (pending === job) {
          const currentValidation = validate(draft);
          if (!currentValidation.ok && currentValidation.field) errors[currentValidation.field] = currentValidation.error;
          pending = null; update();
          if (shown) requestPreview(true);
        }
      }
    }
    function getState() {
      const result = validate(draft);
      return { open: shown, binding, pending: !!currentJob(), applied: copy(applied), draft: copy(draft), dirty: dirty(),
        valid: result.ok, errors: { ...errors }, error: failure, preview: preview ? { ...preview } : null,
        previewPending, busy: busy() };
    }
    doc.addEventListener('funds:toolbar-menu-change', event => {
      if (event.detail?.menuId !== 'relationFilterPopover') return;
      if (event.detail.open) begin(); else { end(); update(); }
    });
    fields.currency?.addEventListener('change', input);
    for (const name of ['min', 'max']) fields[name]?.addEventListener('input', input);
    panel?.addEventListener('keydown', event => {
      if (event.isComposing || event.keyCode === 229 || event.key !== 'Enter') return;
      // Native button activation remains the sole owner of button Enter.
      if (event.target !== fields.min && event.target !== fields.max) return;
      event.preventDefault(); event.stopPropagation();
      if (!event.repeat) void submit();
    });
    el('btnRelationApply')?.addEventListener('click', () => { void submit(); });
    el('btnRelationClear')?.addEventListener('click', () => { void submit(true); });
    el('btnRelationClose')?.addEventListener('click', () => close('close'));
    update();
    return { update, open: () => root.FundsToolbarMenu?.open(trigger) || false, close, getState, apply: () => submit(), clear: () => submit(true) };
  }
  root.FundsRelationFilter = Object.freeze({ create });
})(window);
