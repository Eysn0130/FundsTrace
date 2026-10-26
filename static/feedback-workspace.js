/* Read-only task observation. Business owners retain every write/lease decision. */
(function (root) {
  'use strict';
  const importId = value => typeof value === 'string' && /^[a-f0-9]{12,64}$/.test(value);
  const statuses = new Set(['queued', 'running', 'unknown', 'success', 'warning', 'error', 'cancelled']);
  const operations = new Set(['import', 'account', 'case', 'canvas', 'download', 'analysis']);
  const bounded = (value, max) => typeof value === 'string' && value.length > 0 && value.length <= max;
  function validEvent(event) {
    if (!event || typeof event !== 'object' || Array.isArray(event)) return false;
    if (Object.keys(event).some(key => !['id','kind','operation','status','severity','title','detail','progress','importId','announce'].includes(key))) return false;
    if (!bounded(event.id, 160) || !['toast', 'task'].includes(event.kind) || !operations.has(event.operation) || !bounded(event.title, 160)) return false;
    if (event.detail !== undefined && (typeof event.detail !== 'string' || event.detail.length > 500)) return false;
    if (event.kind === 'task' && !statuses.has(event.status)) return false;
    if (event.kind === 'toast' && !['success','info','warning','error'].includes(event.severity)) return false;
    if (event.importId !== undefined && (!importId(event.importId) || event.operation !== 'import')) return false;
    if (event.announce !== undefined && typeof event.announce !== 'boolean') return false;
    if (event.progress != null && (typeof event.progress !== 'object' || Object.keys(event.progress).some(key => !['done','total'].includes(key)) ||
      !Number.isSafeInteger(event.progress.done) || !Number.isSafeInteger(event.progress.total) || event.progress.total <= 0 || event.progress.done < 0 || event.progress.done > event.progress.total)) return false;
    return true;
  }
  function create(options) {
    let binding = null, epoch = 0, timer = null, flight = null, disposed = false;
    let rows = new Map(), seen = new Map(), recent = new Map(), refreshWarnings = new Set(), sourceTasks = new Map();
    const schedule = options.schedule || setTimeout, cancel = options.cancel || clearTimeout, now = options.now || Date.now;
    const same = value => !disposed && binding && String(value.caseId) === String(binding.caseId) && value.generation === binding.generation && options.isCurrent(value);
    const scope = value => ({ caseId: String(value.caseId), generation: value.generation });
    const id = value => 'import:' + String(binding.caseId) + ':' + value;
    const task = (context, value) => { if (same(context)) options.feedback()?.task({ ...value, scope: scope(context) }); };
    function action(context, row) {
      return [{ id: 'open', label: ['parsing','importing'].includes(row.stage) ? '查看进度' : '查看导入记录', run: () => {
        if (same(context)) return options.openImport(row.import_id, row.surface, context);
      } }];
    }
    async function json(path, signal) {
      const response = await options.fetch(path, { signal });
      if (!response.ok) throw new Error('任务状态暂不可用，请重新查询。');
      return response.json();
    }
    async function read(context, token, signal) {
      const base = '/api/v1/cases/' + encodeURIComponent(context.caseId) + '/imports';
      const result = await json(base + '?limit=200', signal);
      if (!same(context) || token !== epoch) return;
      if (String(result.case_id) !== String(context.caseId) || !Array.isArray(result.items)) throw new Error('任务记录与当前案件不一致。');
      const leases = new Map((options.leases(context) || []).map(item => [item.importId, item]));
      rows = new Map(result.items.filter(row => row && String(row.case_id) === String(context.caseId) && importId(row.import_id)
        && ['v1','legacy'].includes(row.surface) && ['ready','parsing','importing','done','error'].includes(row.stage)).map(row => [row.import_id, row]));
      for (const [key, row] of rows) {
        if (!same(context) || token !== epoch) return;
        const previous = seen.get(key), lease = leases.get(key);
        const active = ['parsing','importing'].includes(row.stage);
        // Past receipts belong to import history. They do not all announce on
        // startup, and a ready session does not prove a sent write was rejected.
        if (!active && !lease && !previous && !(row.stage === 'error' && row.error_code === 'import_interrupted')) continue;
        let status = active ? 'running' : row.stage === 'done' ? 'success' : row.stage === 'error' ? 'error' : 'queued';
        let detail = active ? '后台任务仍在运行；关闭进度界面不会取消任务。' : status === 'success' ? '处理结果和来源已保留，可查看导入记录。'
          : status === 'error' ? (row.error_code === 'import_interrupted' ? '上次任务已中断，请查看记录并核对。' : '任务未完成，请查看处理记录。') : '正在确认提交状态。';
        let progress;
        if (lease && !active && row.stage === 'ready') { status = 'unknown'; detail = '尚不能确认是否写入；只查询状态，不会再次提交。'; }
        if (lease?.phase === 'unknown' && !['done','error'].includes(row.stage)) { status = 'unknown'; detail = '提交结果待核实；只查询状态，不会再次提交。'; }
        const staleObserver = now() - (recent.get(key) || 0) > 2000;
        if (active && staleObserver) {
          try {
            const job = await json(row.surface === 'v1' ? base + '/' + encodeURIComponent(key)
              : '/api/import/progress?case_id=' + encodeURIComponent(context.caseId) + '&import_id=' + encodeURIComponent(key), signal);
            if (!same(context) || token !== epoch) return;
            if (String(job.case_id) !== String(context.caseId) || job.import_id !== key) throw new Error('任务进度与当前案件不一致。');
            if (['done','error'].includes(job.stage)) { status = job.stage === 'done' ? 'success' : 'error'; detail = status === 'success' ? '处理已完成，可查看来源和处理结果。' : '任务未完成，请查看处理记录。'; }
            else if (job.stage === 'importing' && Number.isSafeInteger(job.total) && job.total > 0 && Number.isSafeInteger(job.done) && job.done >= 0 && job.done <= job.total) progress = { done: job.done, total: job.total };
          } catch (error) {
            if (signal.aborted || !same(context)) return;
            status = 'unknown'; detail = '进度暂无法读取；任务可能仍在运行，请查询状态。';
          }
        }
        if (!same(context) || token !== epoch) return;
        if (lease && ['success','error'].includes(status)) {
          try {
            // This existing owner validates both fresh terminal progress and an
            // increased durable attempt. Display state cannot unlock a write.
            await options.release(context, key);
            if (!same(context) || token !== epoch) return;
          } catch (_) { status = 'unknown'; detail = '处理结果已读取，宿主回执尚未核对；请继续查询原任务。'; }
        }
        const changedAttempt = previous && previous.attempt !== row.attempt;
        const terminal = ['success','error'].includes(status);
        const announce = terminal && !!previous && (changedAttempt || ['running','unknown','queued'].includes(previous.status));
        seen.set(key, { status, attempt: row.attempt });
        const retryView = async () => {
          if (!same(context)) return;
          await options.changed?.(context);
          if (!same(context)) return;
          refreshWarnings.delete(key); await refresh(true);
        };
        const warning = status === 'success' && refreshWarnings.has(key);
        task(context, { id: id(key), title: warning ? '导入已完成，列表待刷新' : status === 'unknown' ? '导入结果待核对' : status === 'success' ? '导入已完成' : status === 'error' ? '导入未完成' : '正在处理导入',
          status: warning ? 'warning' : status, detail: warning ? '写入结果已确认，无需再次导入。' : detail, progress, announce: announce && !options.viewingImport?.(), actions: warning ? [{ id: 'refresh', label: '刷新列表', run: retryView }, ...action(context, row)] : status === 'unknown'
            ? [{ id: 'query', label: '查询状态', run: () => refresh(true) }, ...action(context, row)] : action(context, row) });
        if (announce && status === 'success') {
          try { await options.changed?.(context); }
          catch (_) {
            if (!same(context) || token !== epoch) return;
            refreshWarnings.add(key); task(context, { id: id(key), status: 'warning', title: '导入已完成，列表待刷新', detail: '写入结果已确认，无需再次导入。', actions: [{ id: 'refresh', label: '刷新列表', run: retryView }, ...action(context, row)] });
          }
        }
      }
      for (const [key, lease] of leases) if (!rows.has(key)) {
        seen.set(key, { status: 'unknown', attempt: null });
        task(context, { id: id(key), status: 'unknown', title: '导入结果待核对', detail: '当前记录不能确认原任务结果，请查询状态。', actions: [{ id: 'query', label: '查询状态', run: () => refresh(true) }, { id: 'open', label: '查看导入记录', run: () => options.openImport(key, 'v1', context) }] });
      }
    }
    async function refresh(manual = false) {
      if (disposed || !binding || !options.feedback() || flight) return;
      const context = { ...binding }, token = epoch, controller = new AbortController();
      const owner = flight = { controller };
      try { await read(context, token, controller.signal); }
      catch (_) {
        if (!same(context) || controller.signal.aborted) return;
        for (const [key, previous] of seen) if (!['success','error'].includes(previous.status)) {
          previous.status = 'unknown';
          task(context, { id: id(key), status: 'unknown', title: '任务状态待核对', detail: '暂时无法读取进度；不会取消任务或重复提交。', actions: [{ id: 'query', label: '查询状态', run: () => refresh(true) }] });
        }
        if (manual) options.feedback()?.notify({ scope: scope(context), severity: 'warning', title: '任务状态暂不可用', detail: '可以稍后再次查询，原任务不会重新提交。', dedupeKey: 'task-query' });
      } finally {
        if (flight === owner) flight = null;
        if (same(context) && token === epoch) { cancel(timer); timer = schedule(() => { timer = null; void refresh(); }, 2500); }
      }
    }
    function sync() {
      const next = options.context();
      if (!next?.caseId || disposed) return;
      if (!binding || String(next.caseId) !== String(binding.caseId) || next.generation !== binding.generation) {
        epoch++; cancel(timer); timer = null; flight?.controller.abort(); flight = null;
        binding = scope(next); rows = new Map(); seen = new Map(); recent = new Map(); refreshWarnings = new Set(); sourceTasks = new Map();
      }
      void refresh();
    }
    function receive(event, source = 'workspace') {
      if (!validEvent(event) || !binding || !options.feedback()) return false;
      const context = { ...binding }, feedback = options.feedback();
      if (event.operation === 'import' && event.importId) {
        recent.set(event.importId, now());
        if (!seen.has(event.importId)) seen.set(event.importId, { status: 'running', attempt: null });
        const row = rows.get(event.importId) || { import_id: event.importId, surface: 'v1', stage: 'parsing' };
        const terminal = ['success','warning','error','cancelled'].includes(event.status);
        if (terminal && ['success','error'].includes(seen.get(event.importId)?.status) && !(options.leases(context) || []).some(lease => lease.importId === event.importId)) { void refresh(); return true; }
        const status = terminal ? 'unknown' : event.status;
        task(context, { id: id(event.importId), status, title: terminal ? '正在核对导入回执' : event.title,
          detail: terminal ? '读取服务端任务与持久回执，不会再次提交。' : event.detail, progress: event.progress, announce: false,
          actions: status === 'unknown' ? [{ id: 'query', label: '查询状态', run: () => refresh(true) }, ...action(context, row)] : action(context, row) });
        void refresh(); return true;
      }
      if (event.operation === 'account' && event.kind === 'task' && event.id.startsWith('account-operation:')) {
        // The owner also mirrors this operation. A new iframe instance must
        // update that same row, retaining a host-built recovery action.
        const operationId = event.id.slice('account-operation:'.length);
        if (!operationId) return false;
        const held = (options.accountOperations?.(context) || []).some(operation => operation.operationId === operationId);
        const terminal = ['success','warning','error','cancelled'].includes(event.status);
        if (held ? event.status !== 'unknown' : !terminal) return false;
        task(context, { id: 'account:' + operationId, status: event.status, title: event.title, detail: event.detail, announce: false,
          actions: [{ id: 'open', label: '打开核对', run: () => { if (same(context)) return options.openAccount?.(); } }] });
        return true;
      }
      const key = source + ':' + event.id;
      if (event.kind === 'toast') feedback.notify({ id: key, scope: scope(context), severity: event.severity, title: event.title, detail: event.detail, dedupeKey: key });
      else {
        if (['queued','running'].includes(event.status)) sourceTasks.set(key, { source, context, title: event.title });
        else sourceTasks.delete(key);
        feedback.task({ id: key, scope: scope(context), status: event.status, title: event.title, detail: event.detail, progress: event.progress, announce: event.announce === true });
      }
      return true;
    }
    function detachSource(source) {
      for (const [key, value] of sourceTasks) if (value.source === source) {
        task(value.context, { id: key, title: value.title, status: 'unknown', detail: '任务页面已重新载入，原结果尚未确认。请在原功能中重新读取结果；关闭页面不会取消任务。', announce: false });
        sourceTasks.delete(key);
      }
    }
    return Object.freeze({ sync, refresh, receive, detachSource, dispose() { disposed = true; epoch++; cancel(timer); flight?.controller.abort(); flight = null; sourceTasks.clear(); } });
  }
  root.FundsFeedbackWorkspace = Object.freeze({ create, validEvent });
})(window);
