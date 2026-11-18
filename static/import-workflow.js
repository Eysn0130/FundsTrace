/* Parent-owned leases for case-panel and canvas imports. A detached view cannot cancel
 * a submitted write; only a fresh durable terminal receipt settles its lease. */
(function () {
  'use strict';
  function create(options) {
    const legacy = options.legacy || {}, leases = new Map();
    const current = context => options.isCurrent ? options.isCurrent(context) : true;
    const blocked = context => !!options.accountBlocked?.(context);
    const hostPending = context => !!options.hostPending?.(context);
    const key = (context, importId) => String(context.caseId) + ':' + importId;
    const changed = () => options.changed?.();
    const failure = message => Object.assign(new Error(message), { code: 'import-unresolved' });
    const validId = value => typeof value === 'string' && /^[a-f0-9]{12,64}$/.test(value);
    const inCase = context => [...leases.values()].filter(lease => String(lease.context.caseId) === String(context?.caseId));
    async function json(path) {
      const response = await options.fetch(path);
      if (!response.ok) throw failure('导入状态尚未确认，请保留当前任务后重新核对。');
      return response.json();
    }
    function basePath(context) { return '/api/v1/cases/' + encodeURIComponent(context.caseId) + '/imports'; }
    async function session(context, importId, surface = 'v1') {
      const value = await json(basePath(context) + '?import_id=' + encodeURIComponent(importId));
      if (value.case_id !== String(context.caseId) || !Array.isArray(value.items)) throw failure('导入记录与当前案件不一致。');
      const item = value.items.find(row => row.import_id === importId && row.case_id === String(context.caseId) && row.surface === surface);
      if (!item || !Number.isSafeInteger(item.attempt) || item.attempt < 0) throw failure('无法核对本案导入记录，请重新打开导入记录。');
      return item;
    }
    async function canWrite(context) {
      if (context?.caseId == null || blocked(context) || inCase(context).length) return false;
      const available = !legacy.canWrite || !!await legacy.canWrite(context);
      return available && !blocked(context) && inCase(context).length === 0;
    }
    async function acquireExternal(context, importId, surface = 'v1') {
      if (context?.caseId == null || !validId(importId) || !['v1', 'legacy'].includes(surface) || !current(context)) throw failure('案件或导入记录已变化。');
      if (blocked(context) || options.accountPending?.(context) || hostPending(context) || inCase(context).length) throw failure('本案仍有未确认的写入，请先核对原任务。');
      const lease = { context: { ...context }, importId, surface, phase: 'pending', attempt: null, probe: null };
      const id = key(context, importId);
      // Reserve before either await: two iframe requests cannot both pass.
      leases.set(id, lease); changed();
      try {
        if (legacy.canWrite && !await legacy.canWrite(context)) throw failure('本案流水导入尚未结束，请先核对原任务。');
        if (hostPending(context)) throw failure('案件或资料操作正在处理，请稍后再导入。');
        const item = await session(context, importId, surface);
        if (!current(context) || blocked(context) || options.accountPending?.(context) || hostPending(context) || item.stage !== 'ready') throw failure('该任务尚未就绪或已经开始，请从导入记录核对。');
        lease.attempt = item.attempt;
      } catch (error) {
        if (leases.get(id) === lease) { leases.delete(id); changed(); }
        throw error;
      }
    }
    async function observeExternal(context, importId, surface = 'v1') {
      if (context?.caseId == null || !validId(importId) || !['v1', 'legacy'].includes(surface) || !current(context)) throw failure('案件或导入记录已变化。');
      const id = key(context, importId);
      if (leases.has(id)) return;
      // Restoring a submitted job must reserve its numeric case too. Unlike a
      // new write this observes an existing attempt, never authorizes a POST.
      const lease = { context: { ...context }, importId, surface, phase: 'pending', attempt: null, observed: true, probe: null };
      leases.set(id, lease); changed();
      try {
        const item = await session(context, importId, surface);
        if (!['parsing', 'importing'].includes(item.stage)) { if (leases.get(id) === lease) leases.delete(id); }
        else lease.attempt = item.attempt;
        changed();
      } catch (error) {
        // The known submitted job remains blocked until its state can be read.
        lease.phase = 'unknown'; changed(); throw error;
      }
    }
    async function releaseExternal(context, importId, jobId, rejected = false) {
      if (!validId(importId) || jobId !== undefined && jobId !== importId) throw failure('导入任务标识不一致。');
      const id = key(context, importId), lease = leases.get(id);
      if (!lease) return;
      if (lease.attempt === null) {
        if (!lease.observed) throw failure('导入正在准备，请稍后核对。');
        const item = await session(lease.context, importId, lease.surface);
        lease.attempt = item.attempt;
      }
      if (lease.probe) return lease.probe;
      lease.probe = (async () => {
        try {
          if (rejected === true) {
            const item = await session(lease.context, importId, lease.surface);
            if (item.stage !== 'ready' || item.attempt !== lease.attempt) throw failure('当前记录不能证明本次提交被拒绝，请继续核对原任务。');
            if (leases.get(id) === lease) { leases.delete(id); changed(); }
            return;
          }
          const job = await json(lease.surface === 'legacy' ? '/api/import/progress?case_id=' + encodeURIComponent(lease.context.caseId) + '&import_id=' + encodeURIComponent(importId) : basePath(lease.context) + '/' + encodeURIComponent(importId));
          if ((lease.surface !== 'legacy' || job.case_id !== undefined) && job.case_id !== String(lease.context.caseId) || job.import_id !== importId || !['done', 'error'].includes(job.stage)) throw failure('导入尚未结束；当前只核对进度，不会重新提交。');
          const item = await session(lease.context, importId, lease.surface);
          if ((lease.observed ? item.attempt !== lease.attempt : item.attempt <= lease.attempt) || item.stage !== job.stage) throw failure('当前回执尚不能确认本次写入，请稍后再核对。');
          if (leases.get(id) === lease) { leases.delete(id); changed(); }
        } catch (error) {
          lease.phase = 'unknown'; changed(); throw error;
        } finally { lease.probe = null; }
      })();
      return lease.probe;
    }
    return Object.freeze({ ...legacy, canWrite, acquireExternal, observeExternal, releaseExternal,
      externalStatus: context => inCase(context).map(lease => ({ importId: lease.importId, phase: lease.phase })) });
  }
  window.FundsImportLease = Object.freeze({ create });
  const workflow = window.FundsImportWorkflow = create({ legacy: window.FundsImportWorkflow,
    fetch: (...args) => fetch(...args), isCurrent: context => isCurrentCase(context, false),
    // Account mutations reserve their own pending job before asking canWrite.
    // Only unknown account writes block that preflight; import entry points
    // separately reject every pending account job so they cannot race it.
    accountBlocked: context => !!window.FundsAccountActions?.operations(context)?.length,
    accountPending: context => window.FundsAccountActions?.blocked(context),
    hostPending: context => !!window.FundsAppNavigation?.importBlocker?.(context),
    changed: () => { window.FundsAppNavigation?.sync(); window.dispatchEvent(new CustomEvent('funds:import-state')); } });
})();
