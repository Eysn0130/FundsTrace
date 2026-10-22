/* Case operations own their request and binding, independently of modal visibility. */
(function (root) {
  'use strict';
  function create(options) {
    const doc = options.document, $ = id => doc.getElementById(id);
    const dialog = $('caseActions');
    let view = 'home', phase = 'idle', context = null, summary = {}, pending = null;
    let result = null, trigger = null, file = null, feedback = '', error = '', reconcileScope = 'case', savedNote = '';
    const titles = { new: '新建案件', import: '导入案件包', export: '导出案件包', delete: '删除当前案件' };
    const labels = { new: '创建案件', import: '导入案件包', export: '导出案件包', delete: '确认删除' };
    const count = value => new Intl.NumberFormat('zh-CN').format(value || 0);
    const active = binding => !!binding && options.isCurrent(binding);
    const deletionLocked = () => pending?.view === 'delete' && !pending.committed;
    const capture = () => { context = { ...options.context() }; summary = { ...options.readSummary() }; };
    function refresh() {
      if (view === 'home' && !pending) capture();
      dialog.dataset.phase = phase;
      dialog.setAttribute('aria-busy', String(!!pending));
      if (deletionLocked()) dialog.setAttribute('data-capture-locked', '');
      else dialog.removeAttribute('data-capture-locked');
      $('caseClose').disabled = $('caseCloseFooter').disabled = deletionLocked();
      $('caseContext').textContent = `当前案件：${summary.name || '未选择案件'} · 画布：${summary.canvasName || '默认画布'}`;
      $('caseCounts').textContent = `流水 ${count(summary.flows)} 笔 · 账户 ${count(summary.accounts)} 个`;
      $('caseHome').hidden = view !== 'home';
      $('caseHomeFooter').hidden = view !== 'home';
      $('caseDetail').hidden = view === 'home';
      $('caseDetailFooter').hidden = view === 'home';
      $('caseDetailTitle').textContent = titles[view] || '';
      $('caseDetailHelp').textContent = ({
        new: '创建独立案件，现有案件的数据和画布继续保留。',
        import: '案件包会导入为新案件；同名案件自动编号。',
        export: '导出整个案件的已保存数据与命名画布。原始上传文件不包含在案件包内。',
        delete: `将永久删除「${summary.name}」及其全部数据，无法恢复。`,
      })[view] || '';
      $('caseNameGroup').hidden = view !== 'new' || phase === 'done';
      $('caseFileGroup').hidden = view !== 'import' || phase === 'done';
      $('caseExportGroup').hidden = view !== 'export' || phase === 'done';
      $('caseDeleteGroup').hidden = view !== 'delete' || phase === 'done';
      $('caseFileName').textContent = file?.name || '尚未选择案件包';
      $('caseCanvasNameGroup').hidden = !($('caseSaveCanvas').checked && !context?.canvasId);
      $('caseDeleteScope').textContent = `流水 ${count(summary.flows)} 笔、导入批次、账户资料与照片、全部画布及追踪记录。`;
      $('caseResult').hidden = phase !== 'done';
      $('caseResultText').textContent = result ? `「${result.name}」${view === 'new' ? '已创建' : '已导入'}${result.flows != null ? ` · ${count(result.flows)} 笔流水` : ''}` : '';
      $('caseOpenResult').hidden = !result || phase !== 'done';
      $('caseFeedback').textContent = feedback;
      $('caseError').textContent = error;
      $('caseError').hidden = !error;
      $('caseBusy').hidden = !pending;
      for (const id of ['btnNewCase', 'btnImportCase', 'btnExportCase', 'btnDelCase', 'caseChooseFile',
        'caseName', 'caseSaveCanvas', 'caseCanvasName', 'caseOpenResult', 'caseBack']) $(id).disabled = !!pending;
      $('btnDelCase').disabled = !!pending || !!summary.defaultCase;
      $('btnDelCase').title = summary.defaultCase ? '默认案件不可删除' : '删除当前案件及全部数据';
      $('caseSubmit').disabled = !!pending || phase === 'done';
      $('caseSubmit').classList.toggle('danger', view === 'delete' && phase !== 'unknown');
      $('caseSubmitLabel').textContent = phase === 'unknown' ? (reconcileScope === 'canvas' ? '核对画布列表' : '核对案件列表') : labels[view] || '';
    }
    function home() {
      if (pending) return;
      view = 'home'; phase = 'idle'; result = null; feedback = ''; error = ''; file = null; savedNote = '';
      $('casePkgFile').value = ''; capture(); refresh();
      $('btnNewCase').focus();
    }
    function choose(action) {
      if (pending) return;
      capture();
      if (action === 'delete' && summary.defaultCase) return;
      view = action; phase = 'idle'; result = null; error = ''; feedback = ''; file = null; savedNote = '';
      $('caseName').value = ''; $('caseCanvasName').value = '';
      $('caseSaveCanvas').checked = false; $('casePkgFile').value = '';
      refresh();
      (action === 'new' ? $('caseName') : action === 'delete' ? $('caseBack') : $('caseSubmit')).focus();
    }
    function open(button) {
      trigger = button || trigger;
      if (!pending && phase === 'done' && !result) home();
      if (!context || view === 'home') capture();
      refresh();
      if (!dialog.open) dialog.showModal();
    }
    function close() { if (!deletionLocked() && dialog.open) dialog.close(); }
    function revoke() {
      // A closed dialog does not cancel a request which may already be committed.
      close();
      if (!pending && phase !== 'done' && phase !== 'unknown') {
        view = 'home'; context = null; phase = 'idle'; file = null; error = ''; feedback = '';
      }
    }
    async function submit() {
      if (pending || view === 'home' || phase === 'done') return;
      if (phase === 'unknown') {
        pending = { reconcile: true }; refresh();
        try {
          const items = reconcileScope === 'canvas' ? await options.listCanvases(context) : await options.listCases();
          feedback = `${reconcileScope === 'canvas' ? '已保存画布' : '当前案件'}：${items.map(item => item.name).join('、') || '无'}。请核对后再返回；系统不会重复提交。`;
        } catch (_) { feedback = '列表暂时无法读取，请恢复连接后再次核对。'; }
        finally { pending = null; refresh(); }
        return;
      }
      if (!active(context)) { error = '当前案件或画布已变化，请返回后重新选择操作。'; phase = 'error'; refresh(); return; }
      const name = $('caseName').value.trim();
      if (view === 'new' && !name) { error = '请输入案件名称'; phase = 'error'; refresh(); $('caseName').focus(); return; }
      if (view === 'import' && (!file || !/\.(fundcase|zip)$/i.test(file.name))) {
        error = '请选择 .fundcase 或 .zip 案件包'; phase = 'error'; refresh(); return;
      }
      const save = view === 'export' && $('caseSaveCanvas').checked;
      const canvasName = $('caseCanvasName').value.trim();
      if (save && !context.canvasId && !canvasName) {
        error = '请为当前画布填写名称'; phase = 'error'; refresh(); $('caseCanvasName').focus(); return;
      }
      const job = { view, context: { ...context }, file, name, mutationSent: false, committed: false, saving: false };
      pending = job; phase = 'pending'; error = '';
      feedback = ({ new: '正在创建案件…', import: '正在导入案件包…', export: '正在生成案件包…', delete: '正在删除案件…' })[view];
      refresh();
      try {
        if (job.view === 'delete') {
          if (!await options.canLeave(job.context) || !active(job.context)) {
            phase = 'idle'; feedback = ''; return;
          }
          job.mutationSent = true;
          const deleted = await options.deleteCase(job.context.caseId);
          job.committed = true;
          phase = 'done'; feedback = `案件已删除 · ${count(deleted.deleted_flows)} 笔流水`;
          try { await options.listCases(); } catch (_) { feedback += '。案件列表刷新失败，请重新打开软件后核对。'; }
          if (active(job.context)) await options.openCase(1, job.context, { discardApproved: true });
        } else if (job.view === 'new' || job.view === 'import') {
          job.mutationSent = true;
          const value = job.view === 'new' ? await options.createCase(job.name) : await options.importCase(job.file);
          job.committed = true;
          result = { id: value.id ?? value.case_id, name: value.name, ...(value.flows != null ? { flows: value.flows } : {}) };
          phase = 'done'; feedback = '打开案件后开始工作。当前编辑内容继续保留。';
          try { await options.listCases(); } catch (_) { feedback = '案件已保存；案件列表刷新失败，可稍后重新打开软件查看。'; }
          if (job.view === 'new' && options.autoOpenCreated && active(job.context)) {
            if (await options.openCase(result.id, job.context)) close();
          }
        } else {
          if (save) {
            feedback = '正在保存当前画布…'; refresh();
            job.saving = true;
            await options.saveCanvas(job.context, canvasName || summary.canvasName);
            job.saving = false;
            savedNote = '当前画布形态已保存。';
            // Export retries must not repeat an already committed default-canvas clone.
            $('caseSaveCanvas').checked = false;
          }
          if (!active(job.context)) { phase = 'error'; error = '案件或画布已变化，导出已停止。'; feedback = ''; return; }
          feedback = savedNote + '正在生成案件包…'; refresh();
          const value = await options.exportCase(job.context.caseId);
          if (!active(job.context)) { phase = 'error'; error = '案件或画布已变化，未下载旧案件包。'; feedback = ''; return; }
          await options.download(value.blob, value.filename);
          phase = 'done'; feedback = '案件包已生成并交给浏览器下载。';
        }
      } catch (failure) {
        // Transport/5xx after a write is not proof that the transaction failed.
        if (job.committed) { phase = 'done'; error = '操作已完成，界面刷新失败。请重新打开案件。'; return; }
        const unknown = (job.mutationSent || job.saving && failure?.writeSent) && !(failure?.status >= 400 && failure.status < 500);
        phase = unknown ? 'unknown' : 'error'; feedback = savedNote;
        reconcileScope = job.saving ? 'canvas' : 'case';
        error = unknown ? (job.saving ? '画布保存结果尚未确认，未继续导出。请先核对画布列表。' : '操作结果尚未确认。请先核对案件列表，避免重复提交。')
          : String(failure?.message || '操作失败，请重试').slice(0, 300);
      } finally {
        pending = null; refresh();
      }
    }
    for (const [id, action] of [['btnNewCase', 'new'], ['btnImportCase', 'import'], ['btnExportCase', 'export'], ['btnDelCase', 'delete']]) {
      $(id).onclick = () => choose(action);
    }
    $('caseChooseFile').onclick = () => { if (!pending) $('casePkgFile').click(); };
    $('casePkgFile').onchange = () => {
      if (pending || view !== 'import') return;
      file = $('casePkgFile').files?.[0] || null; phase = 'idle'; error = ''; refresh();
    };
    $('caseSaveCanvas').onchange = () => { if (!pending) refresh(); };
    $('caseSubmit').onclick = () => submit();
    $('caseBack').onclick = home;
    $('caseClose').onclick = $('caseCloseFooter').onclick = close;
    $('caseOpenResult').onclick = async () => {
      if (pending || !result) return;
      // Opening a completed operation is a new, explicit user action in the current binding.
      const binding = { ...options.context() };
      pending = { opening: true }; refresh();
      try {
        if (await options.openCase(result.id, binding)) { close(); pending = null; home(); }
      } catch (failure) { error = String(failure?.message || '打开案件失败'); }
      finally { pending = null; refresh(); }
    };
    for (const id of ['caseName', 'caseCanvasName']) $(id).addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); submit(); }
    });
    dialog.addEventListener('close', () => { if (trigger?.isConnected && !doc.querySelector('dialog[open]')) trigger.focus(); });
    dialog.addEventListener('cancel', event => { if (deletionLocked()) event.preventDefault(); });
    return { open, refresh, revoke, submit, status: () => ({ view, phase, pending: !!pending,
      context: context ? { ...context } : null, result: result ? { ...result } : null, dialogOpen: dialog.open }) };
  }
  root.FundsCaseWorkspace = { create };
})(typeof window === 'undefined' ? globalThis : window);
