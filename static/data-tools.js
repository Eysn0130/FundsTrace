/* Case-bound bulk tools. Overlay lifetime never owns the mutation lock. */
(function (root) {
  'use strict';
  const actions = new Set(['import', 'demo']);
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const sameCase = (a, b) => a && b && String(a.caseId) === String(b.caseId);
  const copyFile = file => file ? { name: String(file.name || '未命名文件'), size: Number(file.size) || 0 } : null;
  function create(options) {
    let session = null, job = null, unknown = null, serial = 0, picker = 0, templatePending = false;
    const current = context => !!context && options.isCurrent(context, false);
    const ready = (action, context) => options.ready?.(action, context) !== false;
    const blocked = context => !!(job || unknown) && (!context || sameCase(context, (job || unknown).context));
    function status() {
      const active = job || unknown;
      const shown = session && current(session.context) ? session : null;
      return { pending: !!job, unknown: !!unknown, blocked: !!active, sent: !!active?.sent,
        action: shown?.action || active?.action || '', context: shown?.context || active?.context || null,
        operationContext: active?.context || shown?.operationContext || shown?.context || null,
        visible: !!shown, phase: shown?.phase || (unknown ? 'unknown' : job ? job.reconciling ? 'reconciling' : job.sent ? 'working' : 'preflight' : 'idle'),
        file: copyFile(shown?.file || active?.file), result: shown?.result || null, error: shown?.error || '',
        refreshError: shown?.refreshError || '', reconciled: !!unknown?.reconciled,
        rows: shown?.rows || [], templatePending };
    }
    const sync = () => options.onState?.(status());
    function close(reason = 'close', force = false) {
      if (!force && job?.sent && !job.reconciling) return false;
      session = null; picker += 1; options.dismiss?.(reason); sync();
      return true;
    }
    function revoke() { close('context', true); }
    function display(value) {
      return !!session && session.operationId === value.id && current(session.context);
    }
    function open(action, trigger) {
      if (!actions.has(action)) return false;
      const context = { ...options.context() };
      if (!context.caseId || !current(context)) return false;
      const active = job || unknown;
      if (active) {
        if (!sameCase(context, active.context)) return false;
        session = { id: ++serial, operationId: active.id, operationContext: active.context, context,
          action: active.action, file: active.file, phase: unknown ? 'unknown' : active.sent ? 'working' : 'preflight',
          error: unknown ? '操作结果尚未确认。请先读取当前资料并核对，避免重复提交。' : '', rows: active.rows || [] };
      } else {
        if (!ready(action, context)) return false;
        session = { id: ++serial, context, action, file: null, phase: action === 'demo' ? 'confirm' : 'select' };
      }
      picker += 1;
      options.show?.(status(), trigger); sync(); return true;
    }
    async function pick() {
      const shown = session;
      if (!shown || shown.action !== 'import' || job || unknown || !current(shown.context)) return false;
      const token = ++picker;
      // Capture the case and view session before the native picker is opened.
      const context = { ...shown.context };
      let file;
      try { file = await options.pickFile?.(context); }
      catch (error) {
        if (session === shown && token === picker && current(context)) {
          shown.error = String(error?.message || '无法打开文件选择器').slice(0, 300); sync();
        }
        return false;
      }
      if (!file || session !== shown || token !== picker || !current(context) || job || unknown) return false;
      if (/\.xls$/i.test(String(file.name || ''))) {
        shown.file = null; shown.error = '旧版 .xls 请另存为 .xlsx 后导入。'; sync(); return false;
      }
      shown.file = file; shown.phase = 'select'; shown.result = null; shown.error = ''; shown.refreshError = '';
      sync(); return true;
    }
    function validate(action, result) {
      if (!result || typeof result !== 'object') throw new Error('服务器回执未能确认');
      const keys = action === 'import' ? ['imported', 'invalid', 'blank_account'] : ['ok', 'rows_dup', 'skipped'];
      if (!keys.every(key => count(result[key]))) throw new Error('服务器回执未能确认');
      return Object.fromEntries(keys.map(key => [key, result[key]]));
    }
    async function submit() {
      const shown = session;
      if (!shown || job || unknown || !current(shown.context) || !actions.has(shown.action)
        || shown.phase === 'done' || (shown.action === 'import' && !shown.file) || !ready(shown.action, shown.context)) return false;
      const value = { id: ++serial, sessionId: shown.id, context: { ...shown.context }, action: shown.action,
        file: shown.file, sent: false, committed: false };
      shown.operationId = value.id; shown.operationContext = value.context;
      shown.phase = 'preflight'; shown.error = ''; shown.refreshError = '';
      job = value; sync();
      try {
        if (options.canWrite && !await options.canWrite(value.context, value.action)) {
          if (display(value)) session.phase = value.action === 'import' ? 'select' : 'confirm';
          return false;
        }
        // Closing before the request is sent cancels the authorization. A new
        // session or A-B-A case binding cannot inherit a picker/confirmation.
        if (!current(value.context) || !session || session.id !== value.sessionId) return false;
        if (!ready(value.action, value.context)) throw new Error('案件资料正在处理，请稍后操作');
        let path, request;
        if (value.action === 'import') {
          const body = options.formData ? options.formData() : new root.FormData();
          body.append('file', value.file); body.append('case_id', String(value.context.caseId));
          path = '/api/account-ids/import'; request = { method: 'POST', body };
        } else {
          path = '/api/sample-data'; request = { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ case_id: value.context.caseId }) };
        }
        value.sent = true; if (display(value)) session.phase = 'working'; sync();
        const raw = await options.request(path, request);
        const result = validate(value.action, raw);
        value.committed = true;
        if (display(value)) { session.phase = 'done'; session.result = result; session.error = ''; }
        if (!current(value.context)) return true;
        try { await options.publish?.(value.action, value.context, raw); }
        catch (error) {
          if (display(value)) session.refreshError = '操作已完成，界面资料未能刷新。请重新调阅资料；无需再次提交。';
          return true;
        }
        return true;
      } catch (error) {
        const ambiguous = value.sent && !(error?.status >= 400 && error.status < 500);
        if (ambiguous) {
          unknown = { ...value, reconciled: false, rows: [] };
          if (display(value)) {
            session.phase = 'unknown'; session.error = '操作结果尚未确认。请先读取当前资料并核对，避免重复提交。';
          }
        } else if (display(value)) {
          session.phase = 'error'; session.error = String(error?.message || '操作未完成').slice(0, 300);
        }
        return false;
      } finally {
        if (job === value) job = null;
        if (display(value) && session.phase === 'preflight') session.phase = value.action === 'import' ? 'select' : 'confirm';
        sync();
      }
    }
    async function reconcile() {
      const value = unknown, shown = session;
      if (!value || job || !shown || !display(value) || !sameCase(shown.context, value.context)) return null;
      const reader = { ...shown.context };
      job = { ...value, reconciling: true }; shown.phase = 'reconciling'; sync();
      try {
        const rows = options.reconcile ? await options.reconcile(value.action, value.context)
          : await options.request(`${value.action === 'import' ? '/api/account-ids/list' : '/api/batches'}?case_id=${encodeURIComponent(value.context.caseId)}`);
        if (!Array.isArray(rows)) throw new Error('资料回执未能确认');
        if (unknown !== value || session !== shown || !current(reader)) return null;
        value.reconciled = true; value.rows = rows;
        shown.rows = rows; shown.error = '资料已读取。请核对后结束本次操作；此处不会再次提交。';
        return rows;
      } catch (error) {
        if (unknown === value && session === shown && current(reader)) shown.error = '资料未能读取，请稍后再次核对。';
        return null;
      } finally {
        job = null;
        if (session === shown && current(reader)) shown.phase = 'unknown';
        sync();
      }
    }
    function acknowledge() {
      if (!unknown?.reconciled || job || !session || !display(unknown)) return false;
      unknown = null; close('acknowledge'); return true;
    }
    async function downloadTemplate() {
      if (templatePending) return false;
      const context = { ...options.context() };
      templatePending = true; sync();
      try {
        await options.downloadTemplate(context);
        if (current(context)) options.notify?.('模板已准备下载', 'ok');
        return true;
      } catch (error) {
        if (current(context)) options.notify?.(String(error?.message || '模板下载未完成').slice(0, 300), 'err');
        return false;
      } finally { templatePending = false; sync(); }
    }
    return { open, pick, submit, close, revoke, status, sync, blocked, reconcile, acknowledge, downloadTemplate };
  }

  function createView(doc, options) {
    const el = id => doc.getElementById(id), dialog = el('dtDialog');
    let controller, trigger = null, binding = null, state = null, closeReason = '', composing = false;
    const usable = node => node?.isConnected && !node.disabled && node.getClientRects().length;
    const current = context => !!context && options.isCurrent(context, false);
    function place() {
      if (!dialog.open) return;
      if (!usable(trigger)) trigger = el('btnDataActions');
      if (usable(trigger)) root.FundsToolbarMenu?.place(dialog, trigger);
    }
    function dismiss(reason = 'close') {
      closeReason = reason;
      if (dialog.open) dialog.close();
    }
    function rows(items, action) {
      const list = el('dtReconcileList');
      list.replaceChildren(...items.slice(0, 100).map(row => {
        const item = doc.createElement('li');
        const suffix = value => `•••• ${String(value).slice(-4)}`;
        item.textContent = action === 'import' ? `${row.account ? suffix(row.account) : '空账号'} · ${row.id_no ? suffix(row.id_no) : '无身份证号'}`
          : `${row.filename || '未命名批次'} · 新增 ${row.rows_imported ?? 0} 笔 · 重复 ${row.rows_dup ?? 0} 笔`;
        return item;
      }));
      if (items.length > 100) {
        const note = doc.createElement('li'); note.textContent = `共 ${items.length} 条，此处列出前 100 条。`;
        list.appendChild(note);
      }
      list.hidden = !state.reconciled;
    }
    function onState(next) {
      state = next;
      if (!next.visible || !current(next.context)) { dismiss('context'); return; }
      binding = next.context;
      const working = next.pending, finished = next.phase === 'done', uncertain = next.unknown;
      el('dtTitle').textContent = next.action === 'demo' ? '加入演示数据' : '导入身份证对照表';
      el('dtCase').textContent = options.readCaseName?.(next.operationContext || binding) || `案件 #${binding.caseId}`;
      el('dtImportBody').hidden = next.action !== 'import';
      el('dtDemoBody').hidden = next.action !== 'demo';
      el('dtRange').textContent = next.action === 'import' ? '本案件全部画布' : '当前案件流水';
      el('dtOverwrite').hidden = next.action !== 'import';
      el('dtFilename').textContent = next.file?.name || '尚未选择文件';
      el('dtFilesize').textContent = next.file ? formatSize(next.file.size) : 'XLSX、CSV、TSV、TXT';
      el('dtChoose').textContent = next.file ? '更换文件' : '选择文件';
      el('dtChoose').disabled = working || uncertain || finished;
      el('dtTemplate').disabled = next.templatePending;
      el('dtTemplate').hidden = next.action !== 'import';
      el('dtProgress').hidden = !working && !finished;
      el('dtProgress').setAttribute('data-state', finished ? 'done' : 'working');
      const image = el('dtProgress').querySelector('img');
      if (image) image.src = `/static/ui-icons/${finished ? 'check' : 'refresh'}.svg`;
      el('dtProgressText').textContent = finished ? '已完成' : next.phase === 'reconciling' ? '正在读取当前资料…'
        : next.phase === 'preflight' ? '正在确认资料状态…' : next.action === 'import' ? '正在导入并更新…' : '正在加入演示数据…';
      el('dtResult').hidden = !finished || next.action !== 'import';
      el('dtDemoResult').hidden = !finished || next.action !== 'demo';
      if (finished && next.result) {
        if (next.action === 'import') {
          el('dtImported').textContent = String(next.result.imported);
          el('dtInvalid').textContent = String(next.result.invalid);
          el('dtBlank').textContent = String(next.result.blank_account);
        } else el('dtDemoResult').textContent = `新增 ${next.result.ok} 笔 · 重复 ${next.result.rows_dup} 笔 · 跳过 ${next.result.skipped} 行`;
      }
      el('dtError').textContent = next.refreshError || next.error;
      el('dtError').hidden = !next.refreshError && !next.error;
      el('dtSubmit').hidden = uncertain || finished;
      el('dtSubmit').disabled = working || next.action === 'import' && !next.file;
      el('dtSubmit').textContent = next.action === 'demo' ? '加入当前案件' : '导入并更新';
      el('dtCancel').textContent = working || uncertain || finished ? '收起' : '取消';
      el('dtCancel').disabled = working && next.sent && next.phase !== 'reconciling';
      el('dtClose').disabled = el('dtCancel').disabled;
      el('dtReconcile').hidden = !uncertain;
      el('dtReconcile').disabled = working;
      el('dtAcknowledge').hidden = !uncertain || !next.reconciled;
      el('dtAcknowledge').disabled = working;
      dialog.setAttribute('aria-busy', String(working));
      rows(next.rows || [], next.action);
      if (!dialog.open) { dialog.showModal(); place(); }
      else place();
    }
    function show(next, anchor) {
      root.FundsToolbarMenu?.close('command'); root.FundsRibbon?.closePopovers?.();
      trigger = usable(anchor) ? anchor : el('btnDataActions'); closeReason = '';
      onState(next);
      (next.unknown ? el('dtReconcile') : next.action === 'import' ? el('dtChoose') : el('dtCancel')).focus({ preventScroll: true });
    }
    function pickFile() {
      // Each native picker owns its callbacks and input. Resetting a shared
      // input cannot distinguish a delayed event from a newly opened session.
      const input = doc.createElement('input');
      input.type = 'file'; input.accept = '.xlsx,.csv,.tsv,.txt'; input.hidden = true;
      doc.body.appendChild(input);
      return new Promise((resolve, reject) => {
        let settled = false;
        const finish = file => { if (settled) return; settled = true; input.remove(); resolve(file || null); };
        input.addEventListener('change', () => finish(input.files?.[0]), { once: true });
        input.addEventListener('cancel', () => finish(null), { once: true });
        try { input.click(); } catch (error) { input.remove(); reject(error); }
      });
    }
    el('dtForm').addEventListener('submit', event => { event.preventDefault(); if (!composing) controller?.submit(); });
    el('dtForm').addEventListener('compositionstart', () => { composing = true; });
    el('dtForm').addEventListener('compositionend', () => { composing = false; });
    dialog.addEventListener('keydown', event => {
      if (event.key === 'Enter' && (event.isComposing || event.keyCode === 229)) event.preventDefault();
      // Native dialog confines focus. Graph shortcuts must not interpret input.
      event.stopPropagation();
    });
    el('dtChoose').onclick = () => controller?.pick();
    el('dtTemplate').onclick = () => controller?.downloadTemplate();
    el('dtClose').onclick = () => controller?.close('close');
    el('dtCancel').onclick = () => controller?.close('close');
    el('dtReconcile').onclick = () => controller?.reconcile();
    el('dtAcknowledge').onclick = () => controller?.acknowledge();
    dialog.addEventListener('cancel', event => { event.preventDefault(); controller?.close('escape'); });
    dialog.addEventListener('close', () => {
      if (dialog.open) return;
      if (closeReason !== 'context' && current(binding) && usable(trigger)) trigger.focus({ preventScroll: true });
    });
    root.addEventListener('resize', place);
    root.addEventListener('fundstrace:workspace-resize', place);
    root.addEventListener('scroll', place, true);
    return { show, onState, dismiss, pickFile, attach(value) { controller = value; } };
  }
  function formatSize(size) {
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  root.FundsDataTools = Object.freeze({ create, createView });
})(window);
