/* Canvas-bound investigation workflows. Only display choices enter history;
 * authoritative temporal amounts and path evidence come from effective flows. */
(function (root) {
  'use strict';
  const emptyWindow = () => ({ start: '', end: '', includeUndated: true });
  const normalizeWindow = input => ({ start: input?.start || '', end: input?.end || '', includeUndated: input?.includeUndated !== false });
  const hasWindow = input => !!(input?.start || input?.end || input?.includeUndated === false);
  const windowKey = input => JSON.stringify(normalizeWindow(input));
  const selected = () => [...(state.graphSelection?.ids || [])];
  const ready = () => !!state.caseId && !!state.graph && !state.graphLoading && !layoutJob
    && !root.FundsCanvasEditor?.history.status().busy && !state.graphView.exportDepth;
  const el = id => document.getElementById(id);
  let dialogContext = null, dialogSource = null, dialogRevision = 0, dialogToken = 0, kind = '';
  let cacheSource = null, cacheBinding = '', cache = new Map(), inflight = new Map(), errors = new Map();
  let preview = null, playback = null, frameToken = 0, timer = null, lastWindow = '';
  let lastScope = '', mounted = false, committing = false;
  const controllers = new Set();

  function binding() { return JSON.stringify(caseContext()); }
  function live(context, source) { return isCurrentCase(context) && state.lastGraph === source; }
  function currentDialog(token = dialogToken) {
    return !!el('investigationDialog')?.open && token === dialogToken && live(dialogContext, dialogSource)
      && dialogRevision === state.graphView.revision;
  }
  function dialogStatus(message, error = false) {
    const output = el('investigationStatus'); if (!output) return;
    output.textContent = message; output.dataset.error = String(error);
  }
  function closeDialog() { dialogToken += 1; el('investigationDialog')?.close(); kind = ''; }
  function pause() { clearTimeout(timer); timer = null; frameToken += 1; if (playback) { playback.playing = false; playback.loading = false; } updateTransport(); }
  function resetPreview(render = false) {
    pause(); frameToken += 1; const changed = !!preview;
    preview = null; playback = null; updateTransport();
    if (render && changed && state.graph && !state.graphLoading) { savePositions(); state.forceFit = false; renderGraph(); }
  }
  function ensureSource() {
    const key = binding();
    if (cacheSource === state.lastGraph && cacheBinding === key) return;
    for (const controller of controllers) controller.abort(); controllers.clear();
    cacheSource = state.lastGraph; cacheBinding = key; cache = new Map(); inflight = new Map(); errors = new Map();
    resetPreview();
    if (dialogContext && !live(dialogContext, dialogSource)) closeDialog();
  }
  function effectiveWindow() { return normalizeWindow(preview || state.presentation.timeWindow); }
  function active() { return hasWindow(effectiveWindow()); }
  function exportWindow() {
    const value = effectiveWindow();
    return active() ? { start: value.start || null, end: value.end || null,
      include_undated: value.includeUndated, preview: !!preview } : null;
  }
  function description() {
    if (!active()) return '';
    const w = effectiveWindow();
    return `${preview ? '时间播放预览' : '时间窗口'}：${w.start || '不限起点'} — ${w.end || '不限终点'}（含边界） · ${w.includeUndated ? '含无时间记录' : '排除无时间记录'} · 按窗口内流水重算金额`;
  }
  function requestBody(w, scope = 'all') {
    const value = normalizeWindow(w), context = caseContext();
    const body = { case_id: context.caseId, canvas_id: context.canvasId || 0, scope,
      start: value.start || null, end: value.end || null, include_undated: value.includeUndated };
    if (scope === 'visible') {
      body.visible_accounts = state.graph.getNodes().map(item => String(item.getModel().id));
      body.visible_edges = state.graph.getEdges().map(item => { const m = item.getModel(); return { source: m.source, target: m.target }; });
    }
    return body;
  }
  async function read(path, body) {
    const controller = new AbortController(); controllers.add(controller);
    try {
      const response = await fetch(path, { method: 'POST', ...jsonOpts(body), signal: controller.signal });
      if (!response.ok) {
        let value; try { value = await response.json(); } catch (_) { /* non-JSON */ }
        throw new Error(typeof value?.detail === 'string' ? value.detail : value?.detail?.message
          || (Array.isArray(value?.detail) ? value.detail.map(issue => issue.msg).filter(Boolean).join('；') : '')
          || `读取失败（${response.status}）`);
      }
      return await response.json();
    } finally { controllers.delete(controller); }
  }
  async function loadProjection(w) {
    ensureSource();
    const key = windowKey(w), source = state.lastGraph, context = caseContext();
    if (cache.has(key)) return cache.get(key);
    if (inflight.has(key)) return inflight.get(key);
    const promise = read('/api/canvas/investigation/time', requestBody(w)).then(result => {
      if (!live(context, source)) throw new Error('画布已更新，请重新打开研判');
      const originals = new Map(source.nodes.map(node => [String(node.account), node]));
      result.nodes = result.nodes.map(node => ({ ...originals.get(String(node.account)), ...node }));
      cache.set(key, result); errors.delete(key);
      // Bounded projections; the full tracked graph remains the single fact source.
      while (cache.size > 12) cache.delete(cache.keys().next().value);
      return result;
    }).catch(error => { if (live(context, source) && error.name !== 'AbortError') errors.set(key, error.message); throw error; })
      .finally(() => { if (live(context, source)) inflight.delete(key); });
    inflight.set(key, promise); return promise;
  }
  function displayGraph(full) {
    ensureSource();
    const savedKey = windowKey(state.presentation.timeWindow);
    if (lastWindow && savedKey !== lastWindow && preview) resetPreview();
    lastWindow = savedKey;
    const value = effectiveWindow();
    if (!hasWindow(value)) return full;
    const key = windowKey(value), found = cache.get(key);
    if (found) return { ...full, nodes: found.nodes, edges: found.edges };
    if (!inflight.has(key) && !errors.has(key)) {
      const context = caseContext(), source = full;
      void loadProjection(value).then(() => {
        if (live(context, source) && key === windowKey(effectiveWindow())) { state.forceFit = false; renderGraph(); }
      }).catch(() => sync());
    }
    // Never draw old/full-period totals under a narrower time label.
    return { ...full, nodes: [], edges: [] };
  }
  async function edit(label, change, context = caseContext(), source = state.lastGraph) {
    if (!ready() || !live(context, source)) throw new Error('画布正在更新，请稍后重试');
    let applied = false; committing = true;
    const accepted = await root.FundsCanvasEditor.command(label, () => {
      if (!live(context, source)) throw new Error('画布已切换，本次操作已取消');
      savePositions(); captureEdgeLabelRatios();
      const before = state.presentation, next = newGraphPresentation(before);
      change(next); state.presentation = newGraphPresentation(next);
      try { state.forceFit = false; renderGraph(); applied = true; }
      catch (error) { state.presentation = before; state.forceRebuild = true; renderGraph(); throw error; }
    }).finally(() => { committing = false; if (dialogContext && live(dialogContext,dialogSource)) dialogRevision = state.graphView.revision; });
    if (!accepted || !applied || !live(context,source)) throw new Error('画布操作未完成；请先完成正在进行的账户、导入或画布操作后重试');
    closeDialog(); return true;
  }
  function openDialog(title, type, html) {
    root.FundsRibbon?.closePopovers(); root.FundsToolbarMenu?.close('investigation');
    pause(); closeDialog();
    dialogContext = caseContext(); dialogSource = state.lastGraph; dialogRevision = state.graphView.revision;
    kind = type; el('investigationTitle').textContent = title;
    el('investigationContext').textContent = `${state.cases.find(c => String(c.id) === String(state.caseId))?.name || '当前案件'} · ${state.canvasName || '默认画布'} · ${state.lastGraph.nodes.length} 个已上图账户`;
    el('investigationBody').innerHTML = html; dialogStatus('');
    el('investigationDialog').showModal();
    return dialogToken;
  }
  function on(id, callback) {
    el(id)?.addEventListener('click', async event => {
      const token = dialogToken;
      if (!currentDialog(token)) { closeDialog(); return; }
      const controls = [...el('investigationBody').querySelectorAll('button,input,select,textarea')].map(node => [node,node.disabled]);
      for (const [node] of controls) node.disabled = true;
      try { await callback(event, token); }
      catch (error) { if (currentDialog(token)) dialogStatus(error.message || '操作失败', true); else if (dialogContext && live(dialogContext, dialogSource)) toast(error.message || '操作失败', 'err'); }
      finally { if (currentDialog(token)) for (const [node,disabled] of controls) if (node.isConnected) node.disabled = disabled; }
    });
  }
  const button = (id, label, primary = false) => `<button type="button" class="btn${primary ? ' primary' : ''}" id="${id}">${label}</button>`;
  const hint = text => `<p class="investigation-hint">${text}</p>`;
  function groups() { return state.presentation.groups || []; }
  function groupOptions() { return groups().map(g => `<option value="${esc(g.id)}">${esc(g.name)}（${g.members.length} 个账户）</option>`).join(''); }
  function memberList(ids, editable = false) {
    const names = new Map(state.lastGraph.nodes.map(n => [String(n.account), n.name]));
    return `<ul class="investigation-members">${ids.map(id => `<li>${editable ? `<label><input type="checkbox" name="groupMember" value="${esc(id)}" checked>` : ''}<span><b>${esc(names.get(id) || '未命名账户')}</b><code>${esc(id)}</code>${!names.has(id) ? '<small>当前不在追踪图内</small>' : ''}</span>${editable ? '</label>' : ''}</li>`).join('')}</ul>`;
  }
  function newGroup(ids = selected()) {
    if (!ids.length) throw new Error('先选择要整理的账户');
    if (groups().length >= 100) throw new Error('每张画布最多 100 个分组');
    openDialog('新建研判分组', 'group-create', `${hint('分组用于整理人工研判；账户可属于多个分组，交易和账户身份保持独立。')}
      <form id="groupForm" class="investigation-form"><label>分组名称<input id="groupName" maxlength="60" required placeholder="例如：需补充流水的账户"></label>
      <label>颜色<input id="groupColor" type="color" value="#3b82b6"></label>
      <label class="wide">研判备注<textarea id="groupNotes" maxlength="500" rows="3" placeholder="记录依据、待核问题或下一步调取计划"></textarea></label></form>
      <h3>${ids.length} 个账户</h3>${memberList(ids)}<footer>${button('groupCreate','创建分组',true)}</footer>`);
    const context = dialogContext, source = dialogSource;
    on('groupCreate', async () => {
      const name = el('groupName').value.trim(); if (!name) throw new Error('请输入分组名称');
      const group = { id: `group-${crypto.randomUUID()}`, name, color: el('groupColor').value, members: [...new Set(ids)], notes: el('groupNotes').value.trim() };
      if (groups().some(g => g.name === name)) throw new Error('已有同名分组，请换一个名称');
      await edit('新建研判分组', p => { p.groups.push(group); }, context, source); toast(`已创建分组：${name}`);
    });
    el('groupForm').addEventListener('submit', event => { event.preventDefault(); el('groupCreate').click(); });
    el('groupName').focus();
  }
  function addToGroup() {
    const ids = selected(); if (!ids.length || !groups().length) throw new Error('先选择账户并创建分组');
    openDialog('加入分组', 'group-add', `<label class="investigation-field">目标分组<select id="groupTarget">${groupOptions()}</select></label>${hint(`将选中的 ${ids.length} 个账户加入分组，重复成员只保留一次。`)}${memberList(ids)}<footer>${button('groupAdd','加入分组',true)}</footer>`);
    const context = dialogContext, source = dialogSource;
    on('groupAdd', async () => { const id = el('groupTarget').value;
      await edit('账户加入分组', p => { const g = p.groups.find(g => g.id === id); if (!g) throw new Error('分组已变更'); g.members = [...new Set([...g.members, ...ids])]; }, context, source);
      toast('已加入分组'); });
  }
  function manageGroups(id = groups()[0]?.id) {
    const group = groups().find(g => g.id === id);
    openDialog('分组管理', 'groups', `${hint('分组随画布保存。删除分组只移除人工分类；取消成员勾选后保存即可移出分组。')}
      ${groups().length ? `<label class="investigation-field">当前分组<select id="groupTarget">${groupOptions()}</select></label>` : '<p>尚无分组。先在图上选择账户，再点击“新建分组”。</p>'}
      ${group ? `<div class="investigation-form"><label>名称<input id="groupName" maxlength="60" value="${esc(group.name)}"></label><label>颜色<input id="groupColor" type="color" value="${esc(group.color)}"></label><label class="wide">研判备注<textarea id="groupNotes" rows="3" maxlength="500">${esc(group.notes)}</textarea></label></div>
      ${memberList(group.members,true)}<footer>${button('groupSelect','选中图内成员')}${button('groupOnly','仅看分组账户')}${button('groupDelete','删除分组')}${button('groupSave','保存分组',true)}</footer>` : ''}`);
    if (!group) return;
    el('groupTarget').value = group.id; el('groupTarget').addEventListener('change', () => manageGroups(el('groupTarget').value));
    const context = dialogContext, source = dialogSource;
    on('groupSave', async () => {
      const name = el('groupName').value.trim(), color = el('groupColor').value, notes = el('groupNotes').value.trim();
      const members = [...el('investigationBody').querySelectorAll('[name=groupMember]:checked')].map(n => n.value);
      if (!name) throw new Error('请输入分组名称');
      if (!members.length) throw new Error('分组至少保留一个账户；移除全部成员请删除分组');
      if (groups().some(g => g.id !== id && g.name === name)) throw new Error('已有同名分组');
      await edit('编辑研判分组', p => { const g = p.groups.find(g => g.id === id); Object.assign(g, { name, color, notes, members }); }, context, source); toast('分组已更新');
    });
    on('groupSelect', () => { closeDialog(); focusAccounts(group.members); });
    on('groupOnly', async () => { await edit('查看分组账户', p => { p.onlyAccounts = [...group.members]; p.hiddenAccounts = []; }, context, source); toast('已仅显示分组账户；时间与关系筛选仍有效，返回全图可恢复'); });
    on('groupDelete', () => {
      openDialog('删除研判分组', 'group-delete', `<p>删除“${esc(group.name)}”？账户、流水和其他分组保留。</p><footer>${button('groupDeleteCancel','返回')}${button('groupDeleteConfirm','删除分组',true)}</footer>`);
      on('groupDeleteCancel', () => manageGroups(id));
      on('groupDeleteConfirm', async () => { await edit('删除研判分组', p => { p.groups = p.groups.filter(g => g.id !== id); }, context, source); toast('分组已删除'); });
    });
  }
  function focusAccounts(ids) {
    const present = ids.filter(id => state.graph.findById(id));
    state.graphSelection?.rebind(present);
    root.FundsRibbonCommands?.sync();
    if (present.length) void root.FundsRibbonCommands?.execute('fitSelection');
    if (present.length < ids.length) toast(`${present.length}/${ids.length} 个账户在当前视图中；其余受显示条件限制`);
  }
  function styleDialog(which) {
    const api = root.FundsCanvasDisplay, snapshot = api?.snapshot();
    if (!snapshot || !selected().length) throw new Error('先选择账户节点');
    const shape = which === 'nodeShape';
    const options = shape ? [['circle','圆形'],['rect','矩形'],['diamond','菱形'],['ellipse','椭圆']] : [['none','无图标'],['person','人员'],['organization','机构'],['account','账户'],['flag','关注标记']];
    openDialog(shape ? '节点形状' : '节点图标', 'display', `${hint(`应用到 ${selected().length} 个选中账户；这是人工显示标记，不修改账户资料或交易身份。`)}
      <div class="investigation-choices">${options.map(([value,label]) => `<button type="button" class="btn" data-display-value="${value}">${label}</button>`).join('')}</div>${hint('支持撤销、画布保存与图谱导出。')}`);
    for (const node of el('investigationBody').querySelectorAll('[data-display-value]')) node.addEventListener('click', async () => {
      if (!currentDialog()) return closeDialog();
      try { const value = node.dataset.displayValue; committing = true;
        const accepted = await (shape ? api.setNodeShape(value, snapshot) : api.setNodeIcon(value, snapshot));
        dialogRevision = state.graphView.revision;
        if (accepted) closeDialog(); else dialogStatus('操作未完成，请等待当前操作结束后重试', true);
      } catch (error) { dialogStatus(error.message, true); } finally { committing = false; }
    });
  }
  function windowFields(w) {
    const value = normalizeWindow(w);
    // Native datetime-local inputs reject six-digit fractions and would silently
    // empty a saved microsecond boundary. Plain text preserves the source value.
    return `<div class="investigation-form"><label>开始时间（含）<input id="investigationStart" type="text" maxlength="26" placeholder="YYYY-MM-DD HH:mm:ss" aria-describedby="investigationTimeFormat" value="${esc(value.start)}"></label>
      <label>结束时间（含）<input id="investigationEnd" type="text" maxlength="26" placeholder="YYYY-MM-DD HH:mm:ss" aria-describedby="investigationTimeFormat" value="${esc(value.end)}"></label>
      <small class="wide muted" id="investigationTimeFormat">按原始交易时间填写，如 2026-01-03 10:02:00；秒可省略，支持最多六位小数秒，空白表示不限。</small>
      <label class="wide check"><input id="investigationUndated" type="checkbox" ${value.includeUndated ? 'checked' : ''}>包含无交易时间的记录</label></div>`;
  }
  function readWindow() {
    const w = { start: el('investigationStart').value.trim().replace('T',' '), end: el('investigationEnd').value.trim().replace('T',' '), includeUndated: el('investigationUndated').checked };
    const comparable = value => {
      if (!value) return '';
      const match = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?$/.exec(value);
      if (!match) throw new Error('请按 YYYY-MM-DD HH:mm:ss 填写原始交易时间；秒可省略，小数秒最多六位');
      return `${match[1]}:${match[2] || '00'}.${(match[3] || '').padEnd(6,'0')}`;
    };
    const start = comparable(w.start), end = comparable(w.end);
    if (start && end && start > end) throw new Error('开始时间不能晚于结束时间');
    return w;
  }
  function summary(result) {
    const totals = result.summary?.amount_totals || result.amount_totals || [];
    return `${result.nodes.length} 个账户 · ${result.edges.length} 条有向关系 · ${result.summary?.selected_count ?? result.flows?.length ?? 0} 笔（镜像配对后） · 无时间 ${result.summary?.missing_time_count ?? 0} 笔${totals.length ? ` · ${totalLabel(totals)}` : ''}`;
  }
  async function timeDialog(play = false) {
    resetPreview(true);
    const token = openDialog(play ? '时间播放' : '时间窗口', 'time', `${hint('按当前画布已追踪流水的原交易时间筛选，金额按币种重新汇总。空边界表示不限；不受左侧待查询条件影响。')}
      ${windowFields(state.presentation.timeWindow)}<p id="investigationTimeRange" class="muted">正在读取时间范围…</p>
      <footer>${button('investigationTimePreview','预览范围')}${button('investigationTimeClear','清除时间窗口')}${button('investigationTimeApply','应用窗口',true)}${button('investigationStartPlayback','打开时间播放')}</footer>`);
    const context = dialogContext, source = dialogSource;
    on('investigationTimePreview', async (_, t) => { const result = await loadProjection(readWindow()); if (currentDialog(t)) dialogStatus(summary(result)); });
    on('investigationTimeClear', async () => { resetPreview(); await edit('清除时间窗口', p => { p.timeWindow = emptyWindow(); }, context, source); });
    on('investigationTimeApply', async (_, t) => {
      const w = readWindow(); dialogStatus('正在核对窗口内流水…');
      if (hasWindow(w)) await loadProjection(w);
      if (!currentDialog(t)) return;
      resetPreview(); await edit('设置时间窗口', p => { p.timeWindow = w; }, context, source); toast('时间窗口已应用；已追踪流水完整保留');
    });
    on('investigationStartPlayback', async (_, t) => {
      const w = { ...readWindow(), includeUndated: false };
      const result = await loadProjection(w); if (!currentDialog(t)) return;
      const times = [...new Set((result.timeline || []).map(item => item.time).filter(Boolean))].sort();
      if (!times.length) throw new Error('此范围没有可播放的带时间流水');
      closeDialog(); playback = { times, index: -1, context, source, base: w, playing: false };
      await showFrame(0); updateTransport();
    });
    try {
      const result = await loadProjection(emptyWindow());
      if (!currentDialog(token)) return;
      const times = (result.timeline || []).map(x => x.time).filter(Boolean);
      el('investigationTimeRange').textContent = times.length ? `可用交易时间：${times[0]} — ${times.at(-1)} · ${times.length} 个时间点；播放按时间累计，排除无时间记录。` : '画布内没有带时间的交易记录。';
    } catch (error) { if (currentDialog(token)) dialogStatus(error.message, true); }
  }
  async function showFrame(index) {
    const session = playback; if (!session || !live(session.context, session.source)) return resetPreview();
    if (!ready() || state.graphSelection?.status?.().dragging) { pause(); return; }
    const ticket = ++frameToken; index = Math.max(0, Math.min(session.times.length - 1, index));
    const w = { ...session.base, end: session.times[index], includeUndated: false };
    session.loading = true; updateTransport();
    try {
      await loadProjection(w);
      if (playback !== session || ticket !== frameToken || !live(session.context, session.source)) return;
      if (!ready() || state.graphSelection?.status?.().dragging) { pause(); return; }
      session.index = index; preview = w; savePositions(); state.forceFit = false; renderGraph();
    } catch (error) { if (playback === session && ticket === frameToken) { pause(); toast(error.message, 'err'); } }
    finally { if (playback === session && ticket === frameToken) { session.loading = false; updateTransport(); } }
  }
  async function advance() {
    const session = playback; if (!session?.playing) return;
    if (session.index >= session.times.length - 1) return pause();
    await showFrame(session.index + 1);
    if (playback === session && session.playing) timer = setTimeout(advance, 1200);
  }
  function updateTransport() {
    const strip = el('investigationTransport'); if (!strip) return;
    strip.hidden = !playback;
    if (!playback) return;
    el('investigationFrame').textContent = playback.loading ? '正在读取当前时刻…' : `累计至 ${playback.times[Math.max(0,playback.index)]} · ${playback.index + 1}/${playback.times.length} · 临时预览`;
    el('investigationSeek').max = String(playback.times.length - 1); el('investigationSeek').value = String(Math.max(0,playback.index));
    el('investigationPlay').textContent = playback.playing || playback.loading ? '暂停' : '播放';
    for (const id of ['investigationSeek','investigationPrevious','investigationNext','investigationKeep']) el(id).disabled = !!playback.loading;
    el('investigationPrevious').disabled ||= playback.index <= 0;
    el('investigationNext').disabled ||= playback.index >= playback.times.length - 1;
    strip.parentElement?.style.setProperty('--investigation-transport-height', `${strip.offsetHeight}px`);
  }
  function pathDialog() {
    const ids = selected(), nodes = state.graph.getNodes().map(x => x.getModel());
    const currencies = FundsGraphRelations.currencyChoices(state.lastGraph.edges);
    const token = openDialog('图内找路径', 'paths', `${hint('搜索当前画布已追踪流水。路径是待复核线索；各跳金额不能当作同一笔本金，也不自动认定实际控制关系。')}
      <div class="investigation-form"><label>起点账号<input id="pathSource" list="pathAccounts" value="${esc(ids[0] || '')}" placeholder="完整账号"></label>
      <label>终点账号<input id="pathTarget" list="pathAccounts" value="${esc(ids[1] || '')}" placeholder="完整账号"></label>
      <datalist id="pathAccounts">${nodes.slice(0,2000).map(n => `<option value="${esc(n.id)}">${esc(n.data?.name || '')}</option>`).join('')}</datalist>
      <label>分析范围<select id="pathScope"><option value="visible">当前显示的账户及关系</option><option value="all">完整追踪图（保留时间窗口）</option></select></label>
      <label>路径口径<select id="pathMode"><option value="topological">有向关系可达</option><option value="chronological">严格时间递增（逐笔）</option></select></label>
      <label>币种<select id="pathCurrency"><option value="all">全部币种（分别搜索）</option>${currencies.map(c => `<option value="currency:${esc(c.value)}">${esc(c.label)}</option>`).join('')}</select></label>
      <label>最多跳数<select id="pathHops">${[2,3,4,5,6,7,8].map(n => `<option value="${n}" ${n===4?'selected':''}>${n} 跳</option>`).join('')}</select></label></div>
      <footer>${button('pathSearch','查找路径',true)}</footer><div id="pathResults"></div>`);
    const filter = state.presentation.relationFilter;
    if (filter.active) el('pathCurrency').value = 'currency:' + filter.currency;
    on('pathSearch', async (_, t) => {
      const start = el('pathSource').value.trim(), end = el('pathTarget').value.trim();
      if (!start || !end) throw new Error('请输入完整的起点和终点账号');
      if (start === end) throw new Error('起点和终点须不同；闭环请使用环路检测');
      const scope = el('pathScope').value, currency = el('pathCurrency').value;
      const body = { ...requestBody(effectiveWindow(),scope), start_account:start, end_account:end,
        max_hops:Number(el('pathHops').value), max_paths:30, mode:el('pathMode').value,
        currency:currency === 'all' ? null : currency.slice(9) };
      el('pathSearch').disabled = true; el('pathResults').replaceChildren(); dialogStatus('正在搜索当前范围…');
      try { const result = await read('/api/canvas/investigation/paths', body); if (currentDialog(t)) renderPaths(result, body); }
      finally { if (currentDialog(t)) el('pathSearch').disabled = false; }
    });
    return token;
  }
  function renderPaths(result, parameters) {
    const paths = result.paths || [];
    const complete = result.search?.complete ?? result.complete;
    dialogStatus(`找到 ${paths.length} 条候选路径${complete === false || result.search?.result_limited ? ' · 搜索或展示已达上限，结果并不完整' : ' · 仅限本次范围、跳数和搜索条件'}。零结果不代表全案不存在路径。`);
    const flowById = new Map((result.flows || []).map(flow => [flow.id, flow]));
    const evidence = hop => {
      const ids = hop.flow_ids || hop.ids || [], rows = ids.slice(0,50).map(id => flowById.get(String(id))).filter(Boolean);
      return `<details class="investigation-evidence"><summary>核查 ${ids.length} 笔依据${ids.length>50?'（屏幕前 50 笔）':''}</summary>${rows.map(f => `<p><b>流水 ${esc(f.id)}</b> · ${esc(f.amount)} ${esc(f.currency || '未标注币种')} · ${esc(f.trade_time || '无时间')}<br>${esc(f.summary || '无摘要')}<br>原始记录：${esc((f.original_ids || [f.id]).join('、'))}</p><ul>${(f.provenance || []).flatMap(record => (record.sources || []).map(source => `<li>${esc(source.filename || '来源未登记')} · ${esc(source.member_path || source.sheet || '')} · 行 ${esc(source.member_path ? source.member_row ?? '未登记' : source.source_row ?? '未登记')}${source.member_path ? ` · 包内记录 ${esc(source.source_row ?? '未登记')}` : ''} · 原记录 ${esc(record.flow_id)}<br><small>解析器 ${esc(source.parser_version || '未登记')} · SHA-256 ${esc(source.sha256 || '未登记')}</small></li>`)).join('') || '<li>未登记原始文件来源</li>'}</ul>`).join('')}</details>`;
    };
    el('pathResults').innerHTML = `<p class="muted">${esc(parameters.start_account)} → ${esc(parameters.end_account)} · 最多 ${parameters.max_hops} 跳 · ${parameters.mode === 'chronological' ? '严格时间递增；排除无时间、未知币种及非正金额' : '有向关系可达'} · ${parameters.scope === 'visible' ? '当前显示范围' : '完整追踪图'} · ${esc(parameters.start || '不限起点')} — ${esc(parameters.end || '不限终点')}</p>${button('pathDownload','保存本次结果与完整依据 JSON')}` + paths.map((path,index) => {
      const hops = path.steps || path.edges || [], accounts = path.accounts || path.nodes || [hops[0]?.source, ...hops.map(h=>h.target)].filter(Boolean);
      return `<section class="investigation-path"><h3>路径 ${index+1} · ${esc(path.currency || '未标注币种')} · ${hops.length} 跳</h3>
        <p class="investigation-chain">${accounts.map(esc).join(' → ')}</p>
        <div class="investigation-table-scroll"><table><thead><tr><th>有向交易</th><th>金额 / 币种</th><th>交易时间</th><th>流水依据</th></tr></thead><tbody>${hops.map(h => `<tr><td>${esc(h.source)} → ${esc(h.target)}</td><td>${esc(h.amount ?? h.total ?? '')} ${esc(path.currency ?? h.currency ?? '')}</td><td>${esc(h.trade_time || h.time || `${h.first_time || h.tmin || '—'} — ${h.last_time || h.tmax || '—'}`)}</td><td>${evidence(h)}</td></tr>`).join('')}</tbody></table></div>
        <footer><button type="button" class="btn small" data-path-focus="${index}">定位并选中路径账户</button><button type="button" class="btn small" data-path-group="${index}">用路径账户新建分组</button></footer></section>`;
    }).join('');
    el('pathDownload').addEventListener('click', () => {
      if (!currentDialog()) return closeDialog();
      const url = URL.createObjectURL(new Blob([JSON.stringify({ parameters, result },null,2)], { type:'application/json' }));
      const a = document.createElement('a'); a.href = url; a.download = 'FundsTrace-图内路径与依据.json'; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url),1000);
    });
    const accountsOf = path => path.accounts || path.nodes || [(path.steps || path.edges || [])[0]?.source,...(path.steps || path.edges || []).map(h=>h.target)].filter(Boolean);
    for (const node of el('pathResults').querySelectorAll('[data-path-focus]')) node.addEventListener('click', () => { if (!currentDialog()) return closeDialog(); const ids = accountsOf(paths[Number(node.dataset.pathFocus)]); closeDialog(); focusAccounts(ids); });
    for (const node of el('pathResults').querySelectorAll('[data-path-group]')) node.addEventListener('click', () => { if (!currentDialog()) return closeDialog(); try { newGroup(accountsOf(paths[Number(node.dataset.pathGroup)])); } catch(error) { dialogStatus(error.message,true); } });
  }
  function sync() {
    if (!mounted) return;
    ensureSource();
    const w = windowKey(state.presentation.timeWindow);
    if (lastWindow && lastWindow !== w && preview) resetPreview();
    lastWindow = w;
    if (dialogContext && el('investigationDialog')?.open && (!live(dialogContext,dialogSource) || !committing && dialogRevision !== state.graphView.revision)) closeDialog();
    const scope = el('investigationScope'), key = windowKey(effectiveWindow());
    if (scope) {
      scope.hidden = !active();
      const message = description() + (errors.has(key) ? ` · ${errors.get(key)}；点击重试` : inflight.has(key) ? ' · 正在读取…' : '');
      if (lastScope !== message) { scope.textContent = message; lastScope = message; }
    }
    updateTransport();
  }
  async function execute(command) {
    if (!ready()) return false;
    ensureSource();
    if (command === 'createGroup') newGroup();
    else if (command === 'addToGroup') addToGroup();
    else if (command === 'manageGroups') manageGroups();
    else if (command === 'findGraphPaths') pathDialog();
    else if (command === 'timeWindow' || command === 'timePlayback') await timeDialog(command === 'timePlayback');
    else if (command === 'nodeShape' || command === 'nodeIcon') styleDialog(command);
    else return false;
    return true;
  }
  function mount() {
    mounted = true;
    el('investigationClose').addEventListener('click', closeDialog);
    el('investigationDialog').addEventListener('close', () => { if (!el('investigationDialog').open) { dialogToken += 1; kind = ''; } });
    el('investigationPlay').addEventListener('click', () => { if (!playback) return; if (playback.playing || playback.loading) pause(); else { playback.playing = true; void advance(); } updateTransport(); });
    el('investigationPrevious').addEventListener('click', () => { pause(); void showFrame(playback.index - 1); });
    el('investigationNext').addEventListener('click', () => { pause(); void showFrame(playback.index + 1); });
    el('investigationSeek').addEventListener('change', () => { pause(); void showFrame(Number(el('investigationSeek').value)); });
    el('investigationStop').addEventListener('click', () => {
      pause();
      if (!ready() || state.graphSelection?.status?.().dragging) return toast('画布正在处理，请完成后再结束预览', 'err');
      resetPreview(true);
    });
    el('investigationKeep').addEventListener('click', async () => {
      if (!preview || !playback) return;
      const w = { ...preview }, context = playback.context, source = playback.source;
      pause();
      try {
        if (state.graphSelection?.status?.().dragging) throw new Error('请结束拖动后再应用当前帧');
        await edit('应用播放时间窗口', p => { p.timeWindow = w; }, context, source);
        resetPreview(true);
      } catch (error) { if (live(context, source)) toast(error.message, 'err'); }
    });
    const scope = document.createElement('button'); scope.id = 'investigationScope'; scope.type = 'button'; scope.className = 'investigation-scope'; scope.hidden = true;
    scope.addEventListener('click', () => { const key = windowKey(effectiveWindow()); if (errors.has(key)) { errors.delete(key); renderGraph(); } else void execute('timeWindow'); });
    el('graphContainer').parentElement.appendChild(scope);
    // Keep the transport inside the graph region so window resize/collapsed panes compose.
    el('graphContainer').parentElement.appendChild(el('investigationTransport'));
    const suspendFrame = () => { pause(); frameToken += 1; if (playback) playback.loading = false; updateTransport(); };
    el('graphContainer').addEventListener('pointerdown', suspendFrame, true);
    document.addEventListener('keydown', event => { if ((event.ctrlKey || event.metaKey) && ['z','y'].includes(event.key.toLowerCase())) suspendFrame(); }, true);
    document.addEventListener('click', event => {
      if (playback && !event.target.closest?.('#investigationTransport')) {
        pause(); frameToken += 1; if (playback) playback.loading = false; updateTransport();
      }
    }, true);
    document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); });
    root.addEventListener('funds:graph-metric', sync);
    root.addEventListener('fundstrace:workspace-resize', () => pause());
    sync();
  }
  root.FundsInvestigation = Object.freeze({ execute, sync, active, displayGraph, description, exportWindow, resetPreview,
    normalizeWindow, hasWindow, windowKey, requestBody });
  if (document.readyState === 'loading') root.addEventListener('DOMContentLoaded', mount, { once: true }); else mount();
})(window);
