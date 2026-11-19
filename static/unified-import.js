/* Canvas adapter for the same React import workflow used by the case panel. */
(function () {
  'use strict';
  let modulePromise, view = null, intent = 0, restored = false, last = null;
  const navigation = new URL(window.location.href).searchParams;
  const current = context => isCurrentCase(context, false);
  function revoke() {
    intent++;
    if (view) { const previous = view; view = null; previous.controller.abort(); previous.mount?.dispose(); previous.element.remove(); }
  }
  function feedback(context, event) {
    if (!current(context)) return false;
    if (event.kind === 'task' && event.importId) {
      last = { context, importId: event.importId, status: event.status };
    }
    return window.FundsAppNavigation.receiveImportFeedback(context, event);
  }
  async function display(context, resume) {
    revoke(); const token = intent, controller = new AbortController();
    const element = document.createElement('div'); element.className = 'funds-trace ft-import-mount';
    document.body.append(element); const owner = view = { context, controller, element, mount: null };
    try {
      modulePromise ||= import('/workspace/assets/import-entry.js');
      const module = await modulePromise;
      if (token !== intent || !current(context) || controller.signal.aborted) return;
      const name = state.cases.find(item => String(item.id) === String(context.caseId))?.name || '当前案件';
      owner.mount = module.mountImport(element, { caseId: String(context.caseId), name, generation: context.generation, signal: controller.signal, resume,
        acquire: (id, surface) => window.FundsImportWorkflow.acquireExternal(context, id, surface),
        release: (id, jobId, rejected) => window.FundsImportWorkflow.releaseExternal(context, id, jobId, rejected),
        changed: async () => { if (current(context)) await window.FundsAppNavigation.dataChanged(); },
        feedback: event => feedback(context, event), close: () => { if (view === owner) { if (last && ['success', 'error'].includes(last.status)) last = null; revoke(); } },
        openNewImport: () => { if (view === owner && current(context)) void display(context); } });
    } catch (failure) {
      modulePromise = null;
      if (token === intent && current(context)) { revoke(); toast('导入界面加载失败，请重新打开：' + String(failure.message).slice(0, 160), 'err'); }
    }
  }
  async function restore(requestedId) {
    if (!requestedId && restored) return;
    const importId = requestedId || navigation.get('import_id'), context = caseContext(), token = ++intent;
    if (context.caseId == null) return;
    restored = true;
    if (!requestedId && importId) {
      const location = new URL(window.location.href); location.searchParams.delete('import_id');
      window.history.replaceState(null, '', location);
      if (navigation.get('case_id') !== String(context.caseId)) { toast('导入链接与当前案件不一致，请从本案导入记录中打开。', 'err'); return; }
    }
    if (importId && !/^[a-f0-9]{12,32}$/.test(importId)) return;
    try {
      const response = await fetch('/api/v1/cases/' + encodeURIComponent(context.caseId) + '/imports' + (importId ? '?import_id=' + encodeURIComponent(importId) : ''));
      if (!response.ok) throw new Error('导入记录暂时无法读取');
      const value = await response.json();
      if (token !== intent || !current(context)) return;
      if (value.case_id !== String(context.caseId)) throw new Error('导入记录与当前案件不一致');
      const item = importId ? value.items.find(row => row.import_id === importId && row.case_id === String(context.caseId))
        : value.items.find(row => row.case_id === String(context.caseId) && (['parsing', 'importing'].includes(row.stage) || row.stage === 'error' && row.error_code === 'import_interrupted'));
      if (item) {
        if (['parsing', 'importing'].includes(item.stage)) await window.FundsImportWorkflow.observeExternal(context, item.import_id, item.surface);
        if (token === intent && current(context)) await display(context, item);
      }
      else if (importId) toast('未找到当前案件的导入记录，请从导入记录中核对。', 'err');
    } catch (failure) { if (token === intent && current(context)) toast(String(failure.message).slice(0, 180), 'err'); }
  }
  function open() {
    if (view && current(view.context)) return;
    if (last && current(last.context) && !['success', 'error'].includes(last.status)) return restore(last.importId);
    const context = caseContext();
    if (context.caseId == null) { toast('请先选择案件，再导入流水。', 'err'); return; }
    return display(context);
  }
  window.FundsUnifiedImport = Object.freeze({ open, restore, revoke });
  window.addEventListener('funds:feedback-binding', () => { if (view && !current(view.context)) revoke(); });
  window.addEventListener('funds:import-settled', event => { if (view && current(view.context) && String(event.detail?.caseId) === String(view.context.caseId) && event.detail.generation === view.context.generation) view.mount?.settled(event.detail.importId); });
  window.addEventListener('pagehide', revoke);
})();
