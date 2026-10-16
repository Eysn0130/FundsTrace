/* Import UI orchestration only. The parser, durable task and graph stay owned by
 * their existing services; a view session never grants a stale response access. */
(function () {
  'use strict';

  let active = null, serial = 0;
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
      const error = new Error(String(message).slice(0, 200));
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function recovery(session, message, retry = false) {
    if (!visible(session)) return;
    $('#impRecovery').hidden = !message;
    $('#impRecoveryText').textContent = message || '';
    $('#impRetryProgress').hidden = !retry;
  }

  function controls(session) {
    if (!ownsView(session)) return;
    const busy = pending(session) || session.previewing;
    $('#impCommit').disabled = busy || !session.preview;
    $('#impRe').disabled = busy || !session.preview;
    $$('#impMapping select,input[name=impMode],#impMainSel .mk-acct,#impSelfAccts').forEach(node => { node.disabled = busy; });
    $('#impRetryProgress').disabled = session.polling;
  }

  function newSession() {
    const session = { id: ++serial, context: caseContext(), closed: false, preview: null,
      request: 0, previewing: false, committing: false, polling: false, recovering: false, importId: null };
    active = session;
    state.imp = null;
    $('#impSelfAccts').value = '';
    $('#impFile').value = '';
    $('#impStep1').style.display = 'block';
    $('#impStep2').style.display = 'none';
    hidePbar();
    const current = state.cases.find(item => item.id === session.context.caseId);
    $('#impCaseTag').textContent = `导入到案件：${current?.name || session.context.caseId}`;
    $('#impCaseName').textContent = current ? `「${current.name}」` : '当前案件';
    $('#importModal').style.display = 'flex';
    recovery(session, ''); controls(session);
    return session;
  }

  function open() {
    // Closing a submitted task only hides its view. Reopening restores the same
    // read-only progress/recovery controls instead of permitting a second write.
    if (active && inCase(active) && pending(active)) {
      active.closed = false;
      $('#importModal').style.display = 'flex';
      controls(active);
      if (active.recovering) recovery(active, '进度暂不可用，导入可能仍在进行。请重新读取进度；此操作不会再次提交导入。', true);
      return;
    }
    newSession();
  }

  function close() {
    if (active) active.closed = true;
    $('#importModal').style.display = 'none';
  }

  async function upload() {
    const session = active;
    const file = $('#impFile').files[0];
    if (!session || !visible(session) || pending(session) || !file) return;
    const sequence = ++session.request;
    session.previewing = true; session.preview = null; state.imp = null;
    controls(session); recovery(session, '');
    setPbarBusy('正在上传并解析文件…');
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
          let result;
          try { result = JSON.parse(xhr.responseText); } catch (_) { /* invalid receipt */ }
          if (xhr.status === 200 && result) resolve(result);
          else reject(new Error(result?.detail || `解析失败（HTTP ${xhr.status}）`));
        };
        xhr.onerror = () => reject(new Error('网络错误，请重新选择文件'));
        xhr.onabort = () => reject(new Error('文件上传已取消，请重新选择文件'));
        const form = new FormData(); form.append('file', file); xhr.send(form);
      });
      if (!current()) return;
      session.preview = result; state.imp = result;
      renderImport(result); hidePbar();
    } catch (error) {
      if (current()) { hidePbar(); recovery(session, '文件预览失败：' + text(error) + '。可重新选择文件。'); }
    } finally {
      if (current()) { session.previewing = false; controls(session); }
    }
  }

  async function repreview() {
    const session = active;
    if (!session || !visible(session) || !session.preview || pending(session) || session.previewing) return;
    const sequence = ++session.request, { mode, mapping } = collectMapping();
    session.previewing = true; controls(session); recovery(session, '');
    try {
      const result = await request('/api/import/repreview', { method: 'POST', ...jsonOpts({
        import_id: session.preview.import_id, mode, mapping, case_id: session.context.caseId }) });
      if (!visible(session) || sequence !== session.request) return;
      session.preview = result; state.imp = result; renderImport(result);
    } catch (error) {
      if (visible(session) && sequence === session.request) recovery(session, '重新识别失败：' + text(error) + '。请检查映射后重试。');
    } finally {
      if (ownsView(session) && sequence === session.request) { session.previewing = false; controls(session); }
    }
  }

  async function poll(session) {
    if (session.polling || !inCase(session)) return;
    session.polling = true; session.recovering = false;
    recovery(session, ''); controls(session);
    try {
      while (inCase(session)) {
        const progress = await request('/api/import/progress?import_id=' + encodeURIComponent(session.importId)
          + '&case_id=' + encodeURIComponent(session.context.caseId));
        if (!inCase(session)) return;
        if (progress.stage === 'done') {
          const result = progress.result;
          if (visible(session)) setPbar(1, `导入完成：新增 ${fmtCount(result.ok)} 笔`);
          let message = `导入完成：新增 ${fmtCount(result.ok)} 笔，跳过 ${fmtCount(result.skipped || 0)} 行，重复 ${fmtCount(result.rows_dup || 0)} 笔`;
          if (result.main_detected?.length) message += `；已标记本方主账号：${result.main_detected.map(item => item.name || item.account.slice(-6)).join('、')}`;
          else if (result.main_matched?.length) message += `；保留 ${result.main_matched.length} 个已有主账号标记`;
          toast(message);
          session.preview = null;
          // This delayed close cannot dismiss a later import or another case.
          setTimeout(() => { if (visible(session)) { hidePbar(); close(); } }, 700);
          await refreshAll();
          return;
        }
        if (progress.stage === 'error') {
          if (visible(session)) {
            hidePbar();
            recovery(session, '导入失败：' + (progress.error || '任务未完成') + '。请检查文件或映射后，手动确认重试。');
          }
          if (inCase(session)) toast(progress.error || '导入失败', 'err');
          return;
        }
        if (visible(session)) {
          if (progress.stage === 'parsing') setPbar(0.05, progress.message || '解析文件…');
          else if (progress.stage === 'importing') setPbar(progress.total ? progress.done / progress.total : 0.1,
            `写入数据库 ${fmtCount(progress.done)} / ${fmtCount(progress.total)} 笔`);
        }
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    } catch (error) {
      if (!inCase(session)) return;
      session.recovering = error.status !== 404;
      if (visible(session)) {
        hidePbar();
        recovery(session, session.recovering
          ? '进度读取失败：' + text(error) + '。导入可能仍在进行，请重新读取进度；不会再次提交导入。'
          : '未找到已启动的导入任务：' + text(error) + '。请检查文件或映射后，手动确认重试。', session.recovering);
      }
    } finally {
      session.polling = false; controls(session);
    }
  }

  async function commit() {
    const session = active;
    if (!session || !visible(session) || !session.preview || pending(session) || session.previewing) return;
    const { mode, mapping } = collectMapping();
    const payload = { import_id: session.preview.import_id, mode, mapping, case_id: session.context.caseId,
      self_accounts: $('#impSelfAccts').value.trim(),
      mark_accounts: $$('#impMainSel .mk-acct').filter(node => node.checked).map(node => node.value).join(',') };
    session.committing = true; session.importId = payload.import_id;
    controls(session); recovery(session, ''); setPbarBusy('正在提交导入…');
    try {
      const result = await request('/api/import/commit', { method: 'POST', ...jsonOpts(payload) });
      if (!inCase(session)) return;
      if (result.import_id !== session.importId) throw new Error('导入回执不匹配，请重新读取进度确认');
      if (visible(session)) setPbar(0.02, result.started === false ? '导入进行中…' : '解析文件…');
      await poll(session);
    } catch (error) {
      if (!inCase(session)) return;
      // A lost receipt can follow a committed write. Recovery only reads the
      // known import ID; it never silently retries a mutation.
      session.recovering = !error.status || error.status >= 500;
      if (visible(session)) {
        hidePbar();
        recovery(session, session.recovering
          ? '导入提交结果暂无法确认：' + text(error) + '。请重新读取进度；不会再次提交导入。'
          : '导入未提交：' + text(error) + '。请检查文件或映射后重试。', session.recovering);
      }
    } finally {
      session.committing = false; controls(session);
    }
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
          $('#drawerBody').textContent = '详情加载失败：' + text(error) + '。请重新选择该节点或连线重试。';
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
    $('#btnCloseDrawer').onclick = closeDrawer;

    const originalContext = loadContext;
    loadContext = async function (...args) {
      if (!state.ctx) return;
      // The frozen reader already compares object identity before publication.
      // Give each side/window request its own identity, including recursive
      // system-account corrections, rather than mutating one shared request.
      const context = caseContext(), selection = state.ctx = { ...state.ctx };
      $('#ctxTrack').disabled = false; status.textContent = '';
      try { return await originalContext.apply(this, args); }
      catch (error) {
        if (!isCurrentCase(context) || state.ctx !== selection || $('#ctxModal').style.display === 'none') return;
        const row = document.createElement('tr'), cell = document.createElement('td');
        cell.colSpan = 7; cell.textContent = '流水明细加载失败：' + text(error) + '。';
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'btn small'; retry.textContent = '重新加载明细';
        retry.onclick = () => loadContext(); cell.append(retry); row.append(cell);
        $('#ctxTbody').replaceChildren(row);
      }
    };

    const status = document.createElement('span');
    status.id = 'ctxTrackStatus'; status.className = 'workflow-status'; status.setAttribute('role', 'status');
    $('#ctxTrack').after(status);
    $('#ctxTrack').onclick = async () => {
      const context = caseContext(), selection = state.ctx;
      const ids = $$('#ctxTbody .ctx-ck').filter(node => node.checked).map(node => Number(node.dataset.id));
      if (!ids.length) { status.textContent = '请先勾选要上图的流水'; return; }
      $('#ctxTrack').disabled = true; status.textContent = '正在上图…';
      try {
        const applied = await trackFlows(ids);
        if (!isCurrentCase(context) || state.ctx !== selection) return;
        if (applied === true) { status.textContent = ''; $('#ctxModal').style.display = 'none'; }
        else status.textContent = '本次上图未完成，已保留勾选。请等待当前画布操作完成后重试。';
      } catch (error) {
        if (isCurrentCase(context) && state.ctx === selection) status.textContent = '上图失败：' + text(error) + '。勾选已保留，可重试。';
      } finally {
        if (isCurrentCase(context) && state.ctx === selection) $('#ctxTrack').disabled = false;
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
    $('#impClose').onclick = close;
    $('#impFile').onchange = upload; $('#impRe').onclick = repreview; $('#impCommit').onclick = commit;
    // The shared modal layer owns focus, Escape and genuine backdrop gestures.
    bindDetailRequests();
  });
})();
