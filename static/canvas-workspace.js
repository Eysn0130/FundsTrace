/* Canvas commands own their binding and write lock, independently of menu visibility. */
(function (root) {
  'use strict';
  const titles = { new: '新建画布', clone: '另存为新画布', save: '保存画布', rename: '重命名画布', delete: '删除画布' };
  function create(options) {
    let job = null, unknown = null;
    const current = value => options.isCurrent(value.context);
    const status = () => ({ pending: !!job, unknown: !!unknown, action: (job || unknown)?.action || '',
      context: (job || unknown)?.context || null, reconciled: !!unknown?.reconciled, external: !!(job || unknown)?.external });
    const sync = () => options.onState?.(status());
    const blocked = () => !!(job || unknown);
    function revoke() { options.dismiss?.(); root.FundsToolbarMenu?.close('context'); sync(); }
    function uncertain(value, error) {
      return value.sent && !(error?.status >= 400 && error.status < 500);
    }
    async function run(action, trigger) {
      if (blocked()) { options.showStatus?.(status(), trigger); return false; }
      const context = { ...options.context() };
      if (!context.caseId) return false;
      if (action === 'save' && !context.canvasId) action = 'clone';
      if (['rename', 'delete'].includes(action) && !context.canvasId) return false;
      if (!titles[action] || !options.ready(action)) return false;
      const value = { action, context, sent: false, committed: false };
      job = value; sync();
      try {
        if (['new', 'clone', 'delete'].includes(action) && !await options.canLeave(context)) return false;
        if (!current(value)) return false;
        const answer = await options.ask(action, context, trigger);
        if (!current(value) || answer === null || answer === false) return false;
        const name = action === 'delete' ? '' : String(answer ?? '').trim();
        if (action !== 'delete' && !name) return false;
        if (!options.ready(action)) throw new Error('画布正在处理，请稍后操作');
        let snapshot = null;
        if (action === 'save' || action === 'clone') {
          snapshot = await options.collect();
          if (!current(value)) return false;
          if (!snapshot) throw new Error('图谱已变化，请重新操作');
        } else if (action === 'new') snapshot = { nodes: [], edges: [] };
        if (!current(value)) return false;
        let path = '/api/canvases/save';
        let body = { case_id: context.caseId, canvas_id: action === 'new' ? null : context.canvasId, name, snapshot };
        if (action === 'clone') { path = '/api/canvases/clone'; body = { case_id: context.caseId, from_canvas: context.canvasId, name, snapshot }; }
        if (action === 'rename') { path = '/api/canvases/rename'; body = { case_id: context.caseId, canvas_id: context.canvasId, name }; }
        if (action === 'delete') path = `/api/canvases/${context.canvasId}?case_id=${context.caseId}`;
        value.sent = true; sync();
        const result = await options.request(path, action === 'delete' ? { method: 'DELETE' } : {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        value.committed = true;
        if (!current(value)) return true;
        await options.publish(action, context, action === 'rename' ? { ...result, name } : result, snapshot);
        if (current(value)) options.complete?.();
        return true;
      } catch (error) {
        if (value.committed) {
          if (current(value)) {
            options.complete?.();
            options.notify('画布操作已完成，界面刷新未完成；请重新调阅画布', 'err');
          }
          return true;
        }
        if (uncertain(value, error)) {
          unknown = { ...value, reconciled: false };
          if (current(value)) options.failure?.('操作结果尚未确认。请读取画布列表并核对，避免重复提交。', true);
        } else if (current(value)) options.failure?.(String(error?.message || '操作失败').slice(0, 300), false);
        return false;
      } finally {
        if (job === value) job = null;
        sync();
      }
    }
    async function reconcile() {
      const value = unknown;
      if (!value || job) return null;
      const reader = { ...options.context() };
      job = { ...value, reconciling: true }; sync();
      try {
        const rows = await options.request(`/api/canvases?case_id=${value.context.caseId}`);
        if (unknown !== value || !options.isCurrent(reader)) return null;
        value.reconciled = true;
        await options.reconciled?.(rows, value.context);
        return rows;
      } catch (error) {
        if (options.isCurrent(reader)) options.failure?.('画布列表未能读取，请稍后再次核对。', true);
        return null;
      } finally { job = null; sync(); }
    }
    function acknowledge() {
      if (!unknown?.reconciled || job) return false;
      unknown = null; options.complete?.(); sync(); return true;
    }
    async function withWrite(context, operation) {
      if (!options.isCurrent(context)) throw new Error('案件或画布已变化，请重新保存');
      if (blocked()) throw new Error('画布操作尚未结束，请先核对画布管理中的结果');
      if (!options.ready('save')) throw new Error('当前画布不可保存，请先切换到可用画布');
      const value = { action: 'save', context: { ...context }, external: true, sent: false };
      job = value; sync();
      try { return await operation(); }
      catch (error) {
        value.sent = !!error?.writeSent;
        if (uncertain(value, error)) unknown = { ...value, reconciled: false };
        throw error;
      } finally { if (job === value) job = null; sync(); }
    }
    return { run, revoke, sync, status, blocked, reconcile, acknowledge, withWrite };
  }

  function createView(doc, options) {
    const el = id => doc.getElementById(id), dialog = el('canvasActionForm'), form = el('canvasNameForm');
    let resolver = null, action = '', trigger = null, binding = null, submitted = false, departurePending = false, controller;
    const usable = node => node?.isConnected && !node.disabled && node.getClientRects().length;
    function place() { if (dialog.open && usable(trigger)) root.FundsToolbarMenu?.place(dialog, trigger); }
    function dismiss(force = false) {
      if (departurePending && !force) return;
      const done = resolver; resolver = null;
      if (dialog.open) dialog.close();
      done?.(null);
    }
    function open(nextAction, context, anchor, readOnly = false) {
      dismiss(true); action = nextAction; binding = context; submitted = readOnly; departurePending = false;
      trigger = usable(anchor) ? anchor : el('btnCanvasActions');
      root.FundsToolbarMenu?.close('command'); root.FundsRibbon?.closePopovers();
      el('canvasActionTitle').textContent = titles[action];
      el('canvasNameField').hidden = action === 'delete' || readOnly;
      el('canvasDeleteText').hidden = action !== 'delete' || readOnly;
      el('canvasDeleteText').textContent = `删除「${options.readName() || '当前画布'}」及这张画布的追踪关系？原始流水和其他画布保留。`;
      el('canvasActionError').hidden = true; el('canvasActionProgress').hidden = true;
      el('canvasActionReconcile').hidden = true; el('canvasActionAcknowledge').hidden = true;
      el('canvasActionSubmit').hidden = readOnly;
      el('canvasActionSubmit').disabled = false;
      el('canvasActionSubmit').classList.toggle('danger', action === 'delete');
      el('canvasActionSubmit').classList.toggle('primary', action !== 'delete');
      el('canvasActionSubmit').textContent = action === 'delete' ? '删除画布' : action === 'rename' ? '保存名称' : action === 'new' ? '创建画布' : '保存';
      el('canvasActionCancel').textContent = '取消';
      el('canvasActionCancel').disabled = false;
      el('canvasReconcileList').hidden = true;
      el('canvasActionName').required = action !== 'delete' && !readOnly;
      el('canvasActionName').value = ['rename','save'].includes(action) ? options.readName() : '';
      el('canvasActionName').disabled = readOnly;
      dialog.showModal(); place();
      (action === 'delete' || readOnly ? el('canvasActionCancel') : el('canvasActionName')).focus();
      if (!readOnly && action !== 'delete') el('canvasActionName').select();
    }
    function ask(nextAction, context, anchor) {
      open(nextAction, context, anchor);
      return new Promise(resolve => { resolver = resolve; });
    }
    function failure(message, unknown) {
      if (!dialog.open || !options.isCurrent(binding)) return;
      departurePending = false;
      el('canvasActionCancel').disabled = false;
      submitted = unknown;
      el('canvasActionError').textContent = message; el('canvasActionError').hidden = false;
      el('canvasActionProgress').hidden = true; el('canvasActionSubmit').hidden = true;
      el('canvasActionCancel').textContent = '关闭';
      el('canvasActionReconcile').hidden = !unknown;
    }
    function onState(state) {
      if (!dialog.open || !options.isCurrent(binding)) return;
      departurePending = state.pending && submitted && ['new', 'clone', 'delete'].includes(action);
      el('canvasActionCancel').disabled = departurePending;
      if (state.pending && submitted) {
        el('canvasActionProgress').hidden = false; el('canvasActionProgress').textContent = '正在处理…';
      } else el('canvasActionProgress').hidden = true;
      el('canvasActionReconcile').disabled = state.pending;
      el('canvasActionAcknowledge').hidden = !state.reconciled;
      dialog.setAttribute('aria-busy', String(state.pending && submitted));
    }
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (submitted || !resolver || !options.isCurrent(binding)) return;
      const name = el('canvasActionName').value.trim();
      if (action !== 'delete' && !name) { el('canvasActionName').focus(); return; }
      submitted = true;
      departurePending = ['new', 'clone', 'delete'].includes(action);
      el('canvasActionCancel').disabled = departurePending;
      el('canvasActionName').disabled = true; el('canvasActionSubmit').disabled = true;
      const done = resolver; resolver = null; done(action === 'delete' ? true : name);
    });
    el('canvasActionName').addEventListener('keydown', event => {
      if (event.key === 'Enter' && (event.isComposing || event.keyCode === 229)) event.preventDefault();
    });
    el('canvasActionCancel').onclick = () => dismiss();
    dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
    dialog.addEventListener('close', () => {
      if (dialog.open) return;
      const done = resolver; resolver = null; done?.(null);
      if (options.isCurrent(binding) && usable(trigger)) trigger.focus();
    });
    el('canvasActionReconcile').onclick = async () => {
      const rows = await controller.reconcile();
      if (!rows || !dialog.open || !options.isCurrent(binding)) return;
      el('canvasActionError').textContent = '列表已更新，请核对画布后结束本次操作。关闭或结束不会再次提交。';
      el('canvasReconcileList').replaceChildren(...rows.map(row => {
        const item = doc.createElement('li'); item.textContent = `${row.name} · #${row.id}`; return item;
      }));
      el('canvasReconcileList').hidden = false;
    };
    el('canvasActionAcknowledge').onclick = () => controller.acknowledge();
    root.addEventListener('resize', place);
    root.addEventListener('fundstrace:workspace-resize', place);
    return { ask, failure, dismiss: () => dismiss(true), onState, complete: () => dismiss(true),
      attach(value) { controller = value; },
      showStatus(state, anchor) {
        if (!state.context) return;
        open(state.action, options.context(), anchor, true);
        if (state.unknown) {
          el('canvasActionTitle').textContent = '核对画布操作';
          failure(`案件 #${state.context.caseId} 的上次操作结果尚未确认。请读取画布列表并核对，避免重复提交。`, true);
        }
        onState(state);
      } };
  }
  root.FundsCanvasWorkspace = Object.freeze({ create, createView });
})(window);
