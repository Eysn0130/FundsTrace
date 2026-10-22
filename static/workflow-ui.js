/* Import UI orchestration only. The parser, durable task and graph stay owned by
 * their existing services; a view session never grants a stale response access. */
(function () {
  'use strict';

  let active = null, serial = 0, previewScroll = null;
  // Preserve the deep link before case selection updates its URL.
  const navigation = new URL(window.location.href).searchParams;
  let restored = false;
  // A case switch or a new view session cannot revoke a submitted server job.
  // Leases are per attempt so an old read cannot release a later retry.
  const importLeases = new Set();
  const inCase = session => isCurrentCase(session.context, false);
  const ownsView = session => active === session && inCase(session);
  const visible = session => ownsView(session) && !session.closed && $('#importModal').style.display !== 'none';
  const text = error => String(error?.message || error || '请求失败').slice(0, 200);
  const pending = session => session.committing || session.polling || session.recovering;

  async function request(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let message = await response.text();
      try { message = JSON.parse(message).detail || message; } catch (_) { /* plain text */ }
      const error = new Error(String(message).slice(0, 200)); error.status = response.status; throw error;
    }
    return response.json();
  }

  function releaseSettledLease(lease) {
    // A live same-case completion still owns its list/graph publication. A
    // detached callback cannot publish and needs only its terminal receipt.
    if (lease?.terminal && !(inCase(lease.session) && (lease.session.committing || lease.session.polling))) importLeases.delete(lease);
  }

  function releaseTerminal(lease, progress) {
    if (!lease || !['done', 'error'].includes(progress?.stage)
      || progress.import_id && progress.import_id !== lease.importId) return false;
    lease.terminal = true; releaseSettledLease(lease); return true;
  }

  async function canWrite(context) {
    if (context?.caseId == null) return false;
    const leases = [...importLeases].filter(lease => String(lease.context.caseId) === String(context.caseId));
    for (const lease of leases) {
      if (!importLeases.has(lease)) continue;
      // Progress may still report the old ready state while POST is arriving.
      // An unsettled commit must never be unlocked by that read.
      if (lease.requestPending) return false;
      if (lease.terminal) {
        releaseSettledLease(lease);
        if (importLeases.has(lease)) return false;
        continue;
      }
      try {
        if (!lease.probe) lease.probe = request('/api/import/progress?import_id=' + encodeURIComponent(lease.importId)
          + '&case_id=' + encodeURIComponent(lease.context.caseId)).then(progress => {
          releaseTerminal(lease, progress);
        }).finally(() => { lease.probe = null; });
        await lease.probe;
      } catch (_) { return false; }
      if (importLeases.has(lease)) return false;
    }
    return ![...importLeases].some(lease => String(lease.context.caseId) === String(context.caseId));
  }
  window.FundsImportWorkflow = Object.freeze({ canWrite, restore });

  function recovery(session, message, retry = false) {
    if (!visible(session)) return;
    $('#impRecovery').hidden = !message; $('#impRecoveryText').textContent = message || '';
    $('#impRetryProgress').hidden = !retry;
  }

  function flow(session) {
    const working = !session.result && (session.previewing || session.committing || session.polling);
    let values = ['current', 'pending', 'pending'], status = '选择流水文件';
    if (session.preview || session.phase !== 'file') { values = ['complete', 'current', 'pending']; status = '核对字段与数据预览'; }
    if (session.phase === 'confirm') { values = ['complete', 'complete', working ? 'working' : 'current']; status = '正在导入'; }
    if (session.previewing) { values[session.phase === 'file' ? 0 : 1] = 'working'; status = session.phase === 'file' ? '正在上传或解析文件' : '正在重新识别字段'; }
    if (session.dirty && !working) status = '字段已修改，重新识别后可导入';
    if (session.errorStage) { values[{ file: 0, fields: 1, confirm: 2 }[session.errorStage]] = 'error'; status = session.errorStage === 'fields' ? '重新识别未完成' : session.errorStage === 'file' ? '文件未就绪' : '导入未完成'; }
    if (session.recovering && !session.polling && !session.committing) { values[2] = 'warning'; status = '结果待确认，重新读取进度'; }
    if (session.result) { values = ['complete', 'complete', 'complete']; status = '导入完成'; }
    const labels = ['文件', '字段', '确认'], names = { pending: '未开始', current: '待核对', working: '处理中', complete: '已完成', error: '未完成', warning: '结果待确认' };
    ['impFlowFile', 'impFlowFields', 'impFlowConfirm'].forEach((id, index) => {
      const node = $('#' + id), value = values[index];
      node.setAttribute('data-state', value); node.setAttribute('aria-label', labels[index] + '：' + names[value]);
      if (['current', 'working', 'error', 'warning'].includes(value)) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
    });
    $('#impFlowStatus').textContent = status;
    $('#importModal').setAttribute('aria-busy', String(working));
  }

  function controls(session) {
    if (!ownsView(session)) return;
    const busy = !session.result && (pending(session) || session.previewing), complete = !!session.result;
    $('#impCommit').disabled = busy || !session.preview || session.dirty || session.preview.stats?.ok === 0;
    $('#impRe').disabled = busy || !session.preview;
    $('#impRe').hidden = !session.preview || complete; $('#impCommit').hidden = !session.preview || complete;
    $$('#impMapping select,input[name=impMode],#impMainSel .mk-acct,#impSelfAccts').forEach(node => { node.disabled = busy; });
    $('#impFile').disabled = !!pending(session); $('#impChangeFile').disabled = busy;
    $('#impRetryProgress').disabled = session.polling;
    $('#impCancel').textContent = complete ? '完成' : busy ? '关闭' : '取消';
    $('#impResult').hidden = !complete;
    if (complete) {
      $('#impStep1').style.display = 'none'; $('#impStep2').style.display = 'none'; $('#impStats').textContent = '';
      $('#impResultText').textContent = `新增 ${fmtCount(session.result.ok)} 笔 · 重复 ${fmtCount(session.result.rows_dup || 0)} 笔 · 跳过 ${fmtCount(session.result.skipped || 0)} 行`;
    }
    if (session.dirty) $('#impPreviewNote').textContent = '映射已变更 · 预览待更新';
    flow(session); previewScroll?.sync();
  }

  function newSession() {
    const session = { id: ++serial, context: caseContext(), closed: false, preview: null, result: null, mappingDraft: {},
      request: 0, phase: 'file', dirty: false, errorStage: null,
      previewing: false, committing: false, polling: false, recovering: false, importId: null };
    active = session; state.imp = null;
    $('#impSelfAccts').value = ''; $('#impFile').value = ''; $('#impStats').textContent = '';
    $('#impMapping').innerHTML = ''; $('#impMainSel').innerHTML = '';
    $('#impPreviewScroll').scrollLeft = 0; $('#impContent').scrollTop = 0;
    $('#impStep1').style.display = 'block'; $('#impStep2').style.display = 'none'; hidePbar();
    const current = state.cases.find(item => item.id === session.context.caseId);
    $('#impCaseTag').textContent = current?.name || String(session.context.caseId);
    $('#impCaseName').textContent = current?.name || '当前案件';
    $('#importModal').style.display = 'flex'; recovery(session, ''); controls(session); return session;
  }

  function open() {
    // A submitted or uncertain task retains its identity. Completion remains
    // visible until explicitly dismissed so the check transition is observable.
    if (active && inCase(active) && (pending(active) || active.result) && !active.dismissed) {
      active.closed = false; $('#importModal').style.display = 'flex'; controls(active);
      if (active.recovering) recovery(active, '进度暂不可用，导入可能仍在进行。请重新读取进度；此操作不会再次提交导入。', true);
      return;
    }
    newSession();
  }

  function close() {
    if (active) { active.closed = true; if (active.result) active.dismissed = true; }
    $('#importModal').style.display = 'none';
  }

  function restoreAccounts(selection) {
    if (!selection) return;
    if (Array.isArray(selection.self_accounts)) $('#impSelfAccts').value = selection.self_accounts.join(',');
    // [] is the user's explicit choice to mark none. Only null/missing retains
    // the preview's default, which is to select the detected source accounts.
    if (Array.isArray(selection.mark_accounts)) {
      const selected = new Set(selection.mark_accounts);
      $$('#impMainSel .mk-acct').forEach(node => { node.checked = selected.has(node.value); });
    }
  }

  async function restore() {
    if (restored) return;
    restored = true;
    const importId = navigation.get('import_id'), requestedCase = navigation.get('case_id');
    if (importId) {
      const url = new URL(window.location.href);
      if (url.searchParams.get('import_id') === importId) {
        url.searchParams.delete('import_id');
        window.history.replaceState(null, '', url);
      }
    }
    const context = caseContext(), intent = serial;
    if (importId && requestedCase !== String(context.caseId)) return;
    const current = () => isCurrentCase(context, false) && serial === intent;
    let session = null;
    try {
      const result = await request('/api/v1/cases/' + encodeURIComponent(context.caseId) + '/imports');
      if (!current()) return;
      if (result.case_id !== String(context.caseId)) throw new Error('导入记录与当前案件不匹配');
      const items = result.items.filter(item => item.case_id === String(context.caseId) && item.surface === 'legacy');
      const item = importId ? items.find(item => item.import_id === importId)
        : items.find(item => ['parsing', 'importing'].includes(item.stage)
          || item.stage === 'error' && item.error_code === 'import_interrupted');
      if (!item) {
        if (importId) { session = newSession(); recovery(session, '无法恢复这条导入记录，请在当前案件的导入记录中核对后重试。'); }
        return;
      }
      session = newSession(); session.importId = item.import_id;
      if (['parsing', 'importing', 'done'].includes(item.stage)) {
        session.phase = 'confirm';
        session.lease = { context: { ...context }, session, importId: item.import_id, requestPending: false, probe: null };
        importLeases.add(session.lease);
        setPbarBusy('正在读取已提交任务的进度…');
        await poll(session);
        return;
      }
      if (!item.source_available) throw new Error('原上传文件已清理或不存在，请重新选择文件');
      session.phase = 'fields'; session.previewing = true; controls(session); setPbarBusy('正在恢复原文件预览，请核对后确认导入…');
      const saved = item.request || item;
      const preview = await request('/api/import/repreview', { method: 'POST', ...jsonOpts({
        case_id: context.caseId, import_id: item.import_id, mode: saved.mode, mapping: saved.mapping }) });
      if (!visible(session)) return;
      if (preview.import_id !== item.import_id) throw new Error('预览回执与导入记录不匹配');
      session.preview = preview; session.mappingDraft = { ...preview.mapping }; session.dirty = false;
      state.imp = preview; renderImport(preview); restoreAccounts(item.request); hidePbar();
      recovery(session, '已恢复原文件和上次提交选项。请核对映射与主账号，确认后才会再次导入。');
    } catch (error) {
      if (session) {
        if (visible(session)) { hidePbar(); recovery(session, '无法恢复导入：' + text(error)); }
      } else if (current()) {
        if (importId) { session = newSession(); recovery(session, '无法恢复导入：' + text(error)); }
        else toast('无法读取导入恢复记录：' + text(error), 'err');
      }
    } finally {
      if (session && ownsView(session)) { session.previewing = false; controls(session); }
    }
  }

  async function upload() {
    const session = active, file = $('#impFile').files[0];
    if (!session || !visible(session) || pending(session) || !file) return;
    const sequence = ++session.request;
    session.phase = 'file'; session.errorStage = null; session.result = null; session.dirty = false;
    session.previewing = true; session.preview = null; state.imp = null;
    controls(session); recovery(session, ''); setPbarBusy('正在上传并解析文件…');
    const current = () => visible(session) && sequence === session.request;
    try {
      const result = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/import/preview?case_id=' + encodeURIComponent(session.context.caseId));
        xhr.upload.onprogress = event => {
          if (!current() || !event.lengthComputable) return;
          if (event.loaded >= event.total) setPbarBusy('上传完成，正在解析文件…');
          else setPbar(event.loaded / event.total, `上传文件 ${Math.round(event.loaded / event.total * 100)}%`);
        };
        xhr.onload = () => {
          let result; try { result = JSON.parse(xhr.responseText); } catch (_) { /* invalid receipt */ }
          if (xhr.status === 200 && result) resolve(result);
          else reject(new Error(result?.detail || `解析失败（HTTP ${xhr.status}）`));
        };
        xhr.onerror = () => reject(new Error('网络错误，请重新选择文件'));
        xhr.onabort = () => reject(new Error('文件上传已取消，请重新选择文件'));
        const form = new FormData(); form.append('file', file); xhr.send(form);
      });
      if (!current()) return;
      session.preview = result; session.mappingDraft = { ...result.mapping }; state.imp = result; session.phase = 'fields'; renderImport(result); hidePbar();
    } catch (error) {
      if (current()) { session.errorStage = 'file'; hidePbar(); recovery(session, '文件预览失败：' + text(error) + '。可重新选择文件。'); }
    } finally {
      if (current()) { session.previewing = false; controls(session); }
    }
  }

  function mappingChanged(modeChanged = false) {
    const session = active;
    if (!session || !visible(session) || !session.preview || pending(session) || session.previewing) return;
    session.dirty = true; session.errorStage = null; session.phase = 'fields';
    const { mode } = collectMapping();
    $$('#impMapping select').forEach(node => { session.mappingDraft[node.dataset.field] = node.value; });
    if (modeChanged) {
      const marked = new Set($$('#impMainSel .mk-acct').filter(node => node.checked).map(node => node.value));
      renderImport({ ...session.preview, mode, mapping: session.mappingDraft });
      $$('#impMainSel .mk-acct').forEach(node => { node.checked = marked.has(node.value); });
    } else if (typeof refreshImportMappingState === 'function') refreshImportMappingState();
    recovery(session, ''); controls(session);
  }

  async function repreview() {
    const session = active;
    if (!session || !visible(session) || !session.preview || pending(session) || session.previewing) return;
    const sequence = ++session.request, { mode, mapping } = collectMapping();
    const marked = new Map($$('#impMainSel .mk-acct').map(node => [node.value, node.checked]));
    session.phase = 'fields'; session.errorStage = null; session.dirty = true; session.previewing = true;
    controls(session); recovery(session, ''); setPbarBusy('正在重新识别字段…');
    try {
      const result = await request('/api/import/repreview', { method: 'POST', ...jsonOpts({
        import_id: session.preview.import_id, mode, mapping, case_id: session.context.caseId }) });
      if (!visible(session) || sequence !== session.request) return;
      session.preview = result; session.mappingDraft = { ...session.mappingDraft, ...result.mapping };
      session.dirty = false; state.imp = result; renderImport(result);
      $$('#impMainSel .mk-acct').forEach(node => { if (marked.has(node.value)) node.checked = marked.get(node.value); });
    } catch (error) {
      if (visible(session) && sequence === session.request) {
        session.errorStage = 'fields'; recovery(session, '重新识别失败：' + text(error) + '。请检查映射后重试。');
      }
    } finally {
      if (ownsView(session) && sequence === session.request) { session.previewing = false; hidePbar(); controls(session); }
    }
  }

  async function poll(session) {
    if (session.polling || !inCase(session)) return;
    session.polling = true; session.recovering = false; session.errorStage = null; session.phase = 'confirm';
    recovery(session, ''); controls(session);
    try {
      while (inCase(session)) {
        const progress = await request('/api/import/progress?import_id=' + encodeURIComponent(session.importId)
          + '&case_id=' + encodeURIComponent(session.context.caseId));
        releaseTerminal(session.lease, progress);
        if (!inCase(session)) return;
        if (progress.stage === 'done') {
          const result = progress.result;
          if (!result || result.ok == null) throw new Error('导入完成回执不完整，请重新读取进度');
          session.result = result; session.preview = null;
          let message = `导入完成：新增 ${fmtCount(result.ok)} 笔，跳过 ${fmtCount(result.skipped || 0)} 行，重复 ${fmtCount(result.rows_dup || 0)} 笔`;
          if (result.main_detected?.length) message += `；已标记本方主账号：${result.main_detected.map(item => item.name || item.account.slice(-6)).join('、')}`;
          if (visible(session)) { hidePbar(); $('#impResultText').textContent = message; controls(session); }
          toast(message);
          // Refresh failures cannot relabel a durable completed import as failed.
          try { if(window.FundsAppNavigation) await window.FundsAppNavigation.dataChanged(); else await refreshAll(); } catch (_) { if (visible(session)) recovery(session, '导入已完成，列表刷新失败。关闭后可重新查询。'); }
          return;
        }
        if (progress.stage === 'error') {
          session.errorStage = 'confirm';
          if (visible(session)) { hidePbar(); recovery(session, '导入失败：' + (progress.error || '任务未完成') + '。请检查文件或映射后，手动确认重试。'); }
          if (inCase(session)) toast(progress.error || '导入失败', 'err'); return;
        }
        if (visible(session)) {
          if (progress.stage === 'importing' && progress.total) setPbar(progress.done / progress.total, `写入流水 ${fmtCount(progress.done)} / ${fmtCount(progress.total)} 笔`);
          else setPbarBusy(progress.message || '正在解析文件…');
        }
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    } catch (error) {
      if (!inCase(session)) return;
      session.recovering = error.status !== 404;
      if (!session.recovering) session.errorStage = 'confirm';
      if (visible(session)) { hidePbar(); recovery(session, session.recovering
        ? '进度读取失败：' + text(error) + '。导入可能仍在进行，请重新读取进度；不会再次提交导入。'
        : '未找到已启动的导入任务：' + text(error) + '。请检查文件或映射后，手动确认重试。', session.recovering); }
    } finally { session.polling = false; releaseSettledLease(session.lease); controls(session); }
  }

  async function commit() {
    const session = active;
    if (!session || !visible(session) || !session.preview || session.dirty || session.preview.stats?.ok === 0 || pending(session) || session.previewing) return;
    if (window.FundsDataToolsActions?.blocked(session.context) || window.FundsMainAccountsActions?.blocked(session.context)) {
      recovery(session, '该案件的资料更新尚未结束。请先完成或核对数据工具、主账号标记操作，再提交流水导入。');
      return;
    }
    const { mode, mapping } = collectMapping();
    const payload = { import_id: session.preview.import_id, mode, mapping, case_id: session.context.caseId,
      self_accounts: $('#impSelfAccts').value.trim(),
      mark_accounts: $$('#impMainSel .mk-acct').filter(node => node.checked).map(node => node.value).join(',') };
    session.committing = true; session.phase = 'confirm'; session.errorStage = null; session.importId = payload.import_id;
    const lease = session.lease = { context: { ...session.context }, session, importId: payload.import_id, requestPending: true, probe: null };
    importLeases.add(lease);
    controls(session); recovery(session, ''); setPbarBusy('正在提交导入…');
    try {
      const result = await request('/api/import/commit', { method: 'POST', ...jsonOpts(payload) });
      lease.requestPending = false;
      if (!inCase(session)) return;
      if (result.import_id !== session.importId) throw new Error('导入回执不匹配，请重新读取进度确认');
      await poll(session);
    } catch (error) {
      lease.requestPending = false;
      if (error.status >= 400 && error.status < 500) importLeases.delete(lease);
      if (!inCase(session)) return;
      session.recovering = !error.status || error.status >= 500;
      if (!session.recovering) session.errorStage = 'confirm';
      if (visible(session)) { hidePbar(); recovery(session, session.recovering
        ? '导入提交结果暂无法确认：' + text(error) + '。请重新读取进度；不会再次提交导入。'
        : '导入未提交：' + text(error) + '。请检查文件或映射后重试。', session.recovering); }
    } finally { lease.requestPending = false; session.committing = false; releaseSettledLease(lease); controls(session); }
  }

  function bindDetailRequests() {
    let drawerRequest = 0, drawerBinding = null;
    const stale = {};
    const implicitFocus = node => !node || node === document.body || node === document.documentElement || node.isConnected === false;
    function captureDrawerFocus(binding) {
      const focused = document.activeElement;
      if ($('#drawer').contains(focused)) return { id: focused.id, node: focused };
      return implicitFocus(focused) ? binding?.focusIntent || null : null;
    }
    function restoreDrawerFocus(focus) {
      if (!focus || !implicitFocus(document.activeElement)) return;
      const match = focus.id ? document.getElementById(focus.id) : null;
      const target = match && $('#drawer').contains(match) && !match.disabled ? match : $('#btnCloseDrawer');
      target?.focus();
    }
    const originalApi = api;
    api = async function (path, options = {}) {
      // These GETs belong exclusively to the existing node/edge detail views.
      // All other requests retain their original adapter and feedback contract.
      const detail = /^\/api\/accounts\/[^/]+\/profile\?/.test(path)
        || path.startsWith('/api/main-accounts?')
        || (path.startsWith('/api/flows?') && /[?&]ids=/.test(path));
      if (!detail || (options.method && options.method !== 'GET')) return originalApi(path, options);
      const token = drawerRequest, context = caseContext();
      let result;
      try { result = await request(path, options); }
      catch (error) {
        if (token !== drawerRequest || !isCurrentCase(context)) throw stale;
        throw error;
      }
      if (token !== drawerRequest || !isCurrentCase(context)) throw stale;
      if (/^\/api\/accounts\/[^/]+\/profile\?/.test(path) && drawerBinding?.token === token) drawerBinding.profile = result;
      return result;
    };

    function bindNodeActions(binding) {
      // The account workspace owns its writes, draft and photo lock together.
      if (typeof nodeWorkspace !== 'undefined' && nodeWorkspace) return;
      const { context, account, item, profile } = binding;
      const current = () => binding === drawerBinding && binding.token === drawerRequest && isCurrentCase(context);
      const node = state.lastGraph.nodes.find(value => value.account === account) || {};
      const selectors = ['#ndFocusApply', '#ndIdSave', '#ndPhotoClr', '#ndToggleMain'];
      const buttons = selectors.map(selector => $(selector));
      const write = (path, body, method = 'POST') => request(path, { method,
        ...jsonOpts({ case_id: context.caseId, account, ...body }) });

      function action(selector, failure, run) {
        $(selector).onclick = async () => {
          if (!current() || binding.busy) return;
          binding.busy = true;
          binding.focusIntent = captureDrawerFocus(binding);
          const disabled = buttons.map(button => button.disabled);
          buttons.forEach(button => { button.disabled = true; });
          try { await run(current); }
          catch (error) { if (current()) toast(failure + '：' + text(error), 'err'); }
          finally {
            if (current()) {
              binding.busy = false;
              buttons.forEach((button, index) => { button.disabled = disabled[index]; });
            }
          }
        };
      }

      action('#ndFocusApply', '设置失败', async current => {
        const scale = Number($('#ndFocusScale').value) || 1;
        const result = await write('/api/focus-nodes/set', { on: scale > 1, scale });
        if (!current()) return;
        toast(result.on ? `该节点已重点展示（放大 ${result.scale} 倍）` : '已取消重点展示，节点恢复正常大小');
        state.forceRebuild = true;
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndIdSave', '保存失败', async current => {
        const value = $('#ndIdInput').value.trim();
        await write('/api/account-ids/set', { id_no: value });
        if (!current()) return;
        const graphNode = state.lastGraph.nodes.find(value => value.account === account);
        if (graphNode) graphNode.id_no = value || null;
        toast(value ? '身份证号已保存并更新到图谱' : '已清除该账号身份证号');
        renderGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndPhotoClr', '清除失败', async current => {
        await write('/api/account-photos/clear', {});
        if (!current()) return;
        toast('已清除照片，节点恢复圆点');
        state.forceRebuild = true;
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndToggleMain', '主账号标记失败', async current => {
        const isMain = node.is_main === 1, name = profile?.name || node.name || '';
        await write('/api/main-accounts', { name }, isMain ? 'DELETE' : 'POST');
        if (!current()) return;
        toast(isMain ? '已取消主账号标记' : '已标记为主账号，同户名的其他账号也会自动带标');
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
    }

    for (const [name, original] of [['showNodeDrawer', showNodeDrawer], ['showEdgeDrawer', showEdgeDrawer]]) {
      const wrapped = async function (...args) {
        if (name === 'showNodeDrawer') args[1] = state.graph?.findById?.(args[0]) || args[1];
        const previous = drawerBinding;
        const focus = name === 'showNodeDrawer' && previous?.account === args[0]
          && isCurrentCase(previous.context)
          && (previous.token === drawerRequest || previous.preserveFocus && implicitFocus(document.activeElement))
          ? captureDrawerFocus(previous) : null;
        if (previous) previous.preserveFocus = false;
        const token = ++drawerRequest, context = caseContext();
        const binding = drawerBinding = { token, context, account: args[0], item: args[1], profile: null, busy: false,
          focusIntent: focus, preserveFocus: false };
        try {
          const result = await original.apply(this, args);
          if (name === 'showNodeDrawer' && token === drawerRequest && isCurrentCase(context)) {
            bindNodeActions(binding); restoreDrawerFocus(focus);
          }
          return result;
        }
        catch (error) {
          if (error === stale || token !== drawerRequest || !isCurrentCase(context)) return;
          $('#drawerBody').textContent = '详情加载失败：' + text(error) + '。请再次查看详情重试。';
        }
      };
      if (name === 'showNodeDrawer') showNodeDrawer = wrapped; else showEdgeDrawer = wrapped;
    }
    const originalClose = closeDrawer;
    closeDrawer = function (...args) {
      const binding = drawerBinding;
      if (binding) {
        const preserve = args[0] === false && binding.token === drawerRequest && isCurrentCase(binding.context);
        binding.focusIntent = preserve ? captureDrawerFocus(binding) : null;
        // A graph redraw removes the focused control before the same account is
        // reopened. Only that next request may carry its implicit blur forward.
        binding.preserveFocus = preserve && !!binding.focusIntent;
      }
      drawerRequest += 1;
      return originalClose.apply(this, args);
    };
    $('#btnCloseDrawer').onclick = () => {
      closeDrawer(false, { keepSelection: true });
      $('#graphContainer').focus({ preventScroll: true });
    };

    const originalContext = loadContext;
    loadContext = async function (...args) {
      if (!state.ctx || state.ctx.closed || state.ctx.phase === 'tracking') return;
      const context = caseContext(), selection = state.ctx = { ...state.ctx };
      try { return await originalContext.apply(this, args); }
      catch (error) {
        if (!contextIsCurrent(selection, context)) return;
        selection.phase = 'error';
        const row = document.createElement('tr'), cell = document.createElement('td');
        cell.colSpan = 8; cell.className = 'context-empty'; cell.textContent = '流水明细加载失败：' + text(error) + '。';
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'btn small'; retry.textContent = '重新加载明细';
        retry.onclick = () => { if (contextIsCurrent(selection, context)) return loadContext(); };
        cell.append(retry); row.append(cell); $('#ctxTbody').replaceChildren(row);
        $('#ctxPos').textContent = '流水明细读取失败';
        $('#ctxSuggest').textContent = '匹配线索暂不可用';
        syncContextSelection();
      }
    };

    const status = $('#ctxTrackStatus');
    $('#ctxTrack').onclick = async () => {
      const selection = state.ctx, context = selection?.context || caseContext();
      if (!contextIsCurrent(selection, context) || selection.phase !== 'ready' || selection.submission) return;
      const ids = $$('#ctxTbody .ctx-ck').filter(node => node.checked && !node.disabled).map(node => Number(node.dataset.id));
      if (!ids.length) { syncContextSelection(); return; }
      const submission = selection.submission = {};
      const current = () => contextIsCurrent(selection, context) && selection.submission === submission;
      selection.phase = 'tracking'; status.textContent = '正在上图…'; syncContextSelection();
      try {
        const applied = await trackFlows(ids);
        if (!current()) return;
        if (applied === true) { closeContext(); }
        else status.textContent = '本次上图未完成，已保留勾选。请等待当前画布操作完成后重试。';
      } catch (error) {
        if (current()) status.textContent = '上图失败：' + text(error) + '。勾选已保留，可重试。';
      } finally {
        if (current()) {
          selection.phase = 'ready'; selection.submission = null; syncContextSelection();
          if (document.activeElement === document.body) $('#ctxTrack').focus();
        }
      }
    };
  }

  window.addEventListener('DOMContentLoaded', () => {
    const notice = document.createElement('div');
    notice.id = 'impRecovery'; notice.className = 'import-recovery'; notice.hidden = true;
    notice.setAttribute('role', 'status');
    const message = document.createElement('span'); message.id = 'impRecoveryText';
    const retry = document.createElement('button'); retry.id = 'impRetryProgress'; retry.type = 'button';
    retry.className = 'btn small'; retry.textContent = '重新读取进度'; retry.hidden = true;
    retry.onclick = () => { if (active?.recovering) return poll(active); };
    notice.append(message, retry); $('#impPbar').after(notice);
    $('#btnImport').onclick = open; $('#btnImport2').onclick = open;
    $('#impClose').onclick = close; $('#impCancel').onclick = close;
    $('#impFile').onchange = upload; $('#impRe').onclick = repreview; $('#impCommit').onclick = commit;
    $('#impChangeFile').onclick = () => { if (active && !pending(active) && !active.previewing) { newSession(); $('#impFile').click(); } };
    $('#impMapping').addEventListener('change', () => mappingChanged());
    $$('input[name=impMode]').forEach(input => { input.onchange = () => mappingChanged(true); });
    if (typeof bindTableHorizontalScroll === 'function') previewScroll = bindTableHorizontalScroll({
      viewport: $('#impPreviewScroll'), range: $('#impHorizontalPosition'), left: $('#impScrollLeft'), right: $('#impScrollRight'), table: $('#impPreviewTable') });
    // The shared modal layer owns focus, Escape and genuine backdrop gestures.
    bindDetailRequests();
  });
})();
