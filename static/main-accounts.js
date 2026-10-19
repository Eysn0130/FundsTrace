/* Main-account facts are case-scoped. Closing a view cannot release a sent write. */
(function (root) {
  'use strict';
  const actions = new Set(['manual', 'remove', 'auto', 'clear-auto']);
  const sources = new Set(['auto', 'manual']);
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const sameCase = (a, b) => !!a && !!b && String(a.caseId) === String(b.caseId);
  const accountKey = value => typeof value === 'string' ? value.trim() : '';
  const copyRows = rows => rows.map(row => ({ ...row }));
  function create(options) {
    let session = null, job = null, unknown = null, serial = 0, reads = 0, searches = 0;
    const current = context => !!context && options.isCurrent(context, false);
    const ready = (action, context) => options.ready?.(action, context) !== false;
    const shown = () => session && current(session.context) ? session : null;
    const belongs = value => !!shown() && session.operationId === value.id && sameCase(session.context, value.context);
    const owns = value => belongs(value) && current(value.context);
    const blocked = context => !!(job || unknown) && (!context || sameCase(context, (job || unknown).context));
    function status() {
      const view = shown(), active = job || unknown;
      const rows = copyRows(view?.rows || []);
      return { visible: !!view, context: view?.context || active?.context || null,
        operationContext: active?.context || view?.context || null,
        phase: view?.phase || (unknown ? 'unknown' : job ? 'working' : 'idle'),
        screen: view?.screen || 'list', rows, filter: view?.filter || 'all',
        counts: { all: rows.length, auto: rows.filter(row => row.source === 'auto').length,
          manual: rows.filter(row => row.source === 'manual').length },
        query: view?.query || '', candidates: copyRows(view?.candidates || []), total: view?.total || 0,
        page: view?.page || 1, pageSize: 30, listLoaded: !!view?.listLoaded, loading: !!view?.loading, searching: !!view?.searching,
        pending: !!job, sent: !!active?.sent, unknown: !!unknown, committed: !!active?.committed,
        reconciled: !!unknown?.reconciled, error: view?.error || '', refreshError: view?.refreshError || '',
        message: view?.message || '', result: view?.result || null,
        confirmation: view?.confirmation ? { ...view.confirmation } : null };
    }
    const sync = () => options.onState?.(status());
    function close(reason = 'close', force = false) {
      if (!force && job?.sent && !job.reconciling) return false;
      session = null; reads++; searches++; options.dismiss?.(reason); sync(); return true;
    }
    const revoke = () => close('context', true);
    function listResult(value) {
      if (!Array.isArray(value)) throw new Error('主账号资料回执未能确认');
      const seen = new Set();
      return value.map(row => {
        const account = accountKey(row?.account);
        if (!account || !sources.has(row.source) || seen.has(account)) throw new Error('主账号资料回执未能确认');
        seen.add(account); return { ...row, account, name: String(row.name || '') };
      });
    }
    const listPath = context => `/api/main-accounts?case_id=${encodeURIComponent(context.caseId)}`;
    async function refresh() {
      const view = shown();
      if (!view || job || unknown) return false;
      const token = ++reads, context = { ...view.context };
      view.loading = true; view.listLoaded = false; view.error = ''; sync();
      try {
        const rows = listResult(await options.request(listPath(context)));
        if (session !== view || token !== reads || !current(context)) return false;
        view.rows = rows; view.listLoaded = true; view.phase = 'idle'; return true;
      } catch (error) {
        if (session === view && token === reads && current(context)) view.error = String(error?.message || '主账号资料未能读取').slice(0, 300);
        return false;
      } finally {
        if (session === view && token === reads && current(context)) view.loading = false;
        sync();
      }
    }
    function open(trigger) {
      const context = { ...options.context() };
      if (!context.caseId || !current(context)) return false;
      const active = job || unknown;
      if (active && !sameCase(context, active.context)) return false;
      if (!active && !ready('open', context)) return false;
      reads++; searches++;
      session = { id: ++serial, context, screen: 'list', phase: active ? unknown ? 'unknown' : active.sent ? 'working' : 'preflight' : 'idle',
        rows: active?.rows || [], listLoaded: !!active && current(active.context), filter: 'all', query: '', candidates: [], total: 0, page: 1,
        operationId: active?.id, confirmation: null, error: '', refreshError: active?.committed ? '操作已完成，请刷新主账号资料后继续。' : '',
        message: unknown ? '请先核对当前标记，避免重复提交。' : '', result: active?.result || null };
      options.show?.(status(), trigger); sync();
      if (!active) void refresh();
      return true;
    }
    function setFilter(filter) {
      const view = shown();
      if (!view || !['all', 'auto', 'manual'].includes(filter)) return false;
      view.filter = filter; sync(); return true;
    }
    function showSearch() {
      const view = shown();
      if (!view || job || unknown || view.loading || !view.listLoaded) return false;
      view.screen = 'search'; view.confirmation = null; view.error = ''; view.message = ''; sync();
      void search(view.query, 1); return true;
    }
    function back() {
      const view = shown();
      if (!view || job || unknown) return false;
      searches++; view.searching = false; view.screen = 'list'; view.confirmation = null; view.error = ''; sync(); return true;
    }
    async function search(query, page = 1, exact = false) {
      const view = shown();
      if (!view || view.screen !== 'search' || job || unknown || !Number.isSafeInteger(page) || page < 1 || typeof exact !== 'boolean') return false;
      const token = ++searches, context = { ...view.context }, text = String(query ?? '').trim();
      view.query = text; view.page = page; view.searching = true; view.candidates = []; view.total = 0; view.error = ''; sync();
      try {
        const path = `/api/main-accounts/candidates?case_id=${encodeURIComponent(context.caseId)}&q=${encodeURIComponent(text)}&page=${page}&page_size=30${exact ? '&exact=true' : ''}`;
        const value = await options.request(path);
        if (!value || !Array.isArray(value.items) || !count(value.total) || value.page !== page || value.page_size !== 30
          || value.items.length > 30 || value.total < value.items.length) throw new Error('账号查询回执未能确认');
        const seen = new Set();
        const items = value.items.map(row => {
          const account = accountKey(row?.account), marked = row?.is_main === true || row?.is_main === 1;
          if (!account || seen.has(account) || ![true, false, 0, 1].includes(row.is_main)
            || marked && !sources.has(row.source) || !marked && row.source != null) throw new Error('账号查询回执未能确认');
          seen.add(account); return { ...row, account, name: String(row.name || ''), is_main: marked };
        });
        if (session !== view || token !== searches || !current(context) || view.screen !== 'search') return false;
        view.candidates = items; view.total = value.total; return true;
      } catch (error) {
        if (session === view && token === searches && current(context) && view.screen === 'search') view.error = String(error?.message || '账号未能查询').slice(0, 300);
        return false;
      } finally {
        if (session === view && token === searches && current(context)) view.searching = false;
        sync();
      }
    }
    function prepare(action, target) {
      const view = shown();
      if (!view || !actions.has(action) || job || unknown || view.loading || !view.listLoaded || !ready(action, view.context)) return false;
      let row = null;
      if (action === 'manual' || action === 'remove') {
        const account = accountKey(typeof target === 'string' ? target : target?.account);
        row = view.rows.find(item => item.account === account)
          || (action === 'manual' && !view.searching ? view.candidates.find(item => item.account === account) : null);
        if (!row || action === 'manual' && row.source === 'manual') return false;
      }
      if (action === 'clear-auto' && !view.rows.some(row => row.source === 'auto')) return false;
      view.confirmation = { action, account: row?.account || '', name: row?.name || '', source: row?.source || null };
      view.returnScreen = view.screen === 'search' ? 'search' : 'list';
      view.error = ''; view.refreshError = ''; view.message = ''; view.result = null;
      if (action === 'manual') return submit();
      view.screen = 'confirm'; view.phase = 'confirm'; sync(); return true;
    }
    function cancelConfirm() {
      const view = shown();
      if (!view || job || unknown) return false;
      view.confirmation = null; view.screen = view.returnScreen || 'list'; view.phase = 'idle'; view.error = ''; sync(); return true;
    }
    function receipt(action, result, target) {
      if (!result || typeof result !== 'object') throw new Error('服务器回执未能确认');
      if (action === 'manual' && !(result.ok === true && result.account === target.account && result.source === 'manual')) throw new Error('服务器回执未能确认');
      if (action === 'remove' && result.ok !== true) throw new Error('服务器回执未能确认');
      if (action === 'clear-auto' && !count(result.cleared)) throw new Error('服务器回执未能确认');
      if (action === 'auto') {
        if (!Array.isArray(result.detected) || !['added_count', 'manual_preserved', 'existing_auto_count', 'batches_scanned'].every(key => count(result[key]))) throw new Error('服务器回执未能确认');
        const seen = new Set(); let added = 0, manual = 0, existing = 0;
        for (const row of result.detected) {
          const account = accountKey(row?.account);
          if (!account || seen.has(account) || !sources.has(row.source) || typeof row.added !== 'boolean'
            || row.source === 'manual' && row.added) throw new Error('服务器回执未能确认');
          seen.add(account);
          if (row.added) added++;
          else if (row.source === 'manual') manual++;
          else existing++;
        }
        if (added !== result.added_count || manual !== result.manual_preserved || existing !== result.existing_auto_count) throw new Error('服务器回执未能确认');
      }
      return result;
    }
    function recovery(value, message, refreshError = '') {
      unknown = { ...value, reconciled: false, rows: value.rows || [], message, refreshError };
      if (belongs(value)) {
        session.phase = 'unknown'; session.confirmation = null; session.screen = 'list';
        session.error = message; session.refreshError = refreshError; session.result = value.result || null;
      }
    }
    async function submit() {
      const view = shown(), target = view?.confirmation;
      if (!view || !target || !actions.has(target.action) || job || unknown || view.loading || !view.listLoaded || !ready(target.action, view.context)) return false;
      const value = { id: ++serial, sessionId: view.id, context: { ...view.context }, action: target.action,
        target: { ...target }, rows: copyRows(view.rows), sent: false, committed: false };
      view.operationId = value.id; view.phase = 'preflight'; view.error = ''; view.refreshError = '';
      reads++; searches++; view.loading = false; view.searching = false; job = value; sync();
      try {
        if (options.canWrite && !await options.canWrite(value.context, value.action)) {
          if (owns(value)) view.phase = view.screen === 'confirm' ? 'confirm' : 'idle';
          return false;
        }
        if (!current(value.context) || session !== view || session.id !== value.sessionId) return false;
        if (!ready(value.action, value.context)) throw new Error('案件资料正在处理，请稍后操作');
        const path = value.action === 'auto' ? '/api/main-accounts/auto' : value.action === 'clear-auto' ? '/api/main-accounts/clear-auto' : '/api/main-accounts';
        const body = { case_id: value.context.caseId };
        if (value.action === 'manual' || value.action === 'remove') Object.assign(body, { account: target.account, name: target.name });
        value.sent = true; view.phase = 'working'; sync();
        value.result = receipt(value.action, await options.request(path, { method: value.action === 'remove' ? 'DELETE' : 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), target);
        value.committed = true;
        if (!current(value.context)) {
          // A returning case is a new binding. Only an explicit fresh read can
          // reconcile it; an old receipt must never publish graph metadata.
          if (belongs(value)) recovery(value, '', '操作已完成，请刷新当前主账号资料后继续。');
          return true;
        }
        try {
          const rows = listResult(await options.request(listPath(value.context)));
          if (!current(value.context)) {
            if (belongs(value)) recovery(value, '', '操作已完成，请刷新当前主账号资料后继续。');
            return true;
          }
          await options.publish?.(value.context, copyRows(rows), value.result);
          if (!current(value.context)) return true;
          if (owns(value)) {
            view.rows = rows; view.listLoaded = true; view.result = value.result; view.phase = 'done'; view.screen = 'list'; view.confirmation = null;
            view.candidates = []; view.total = 0; view.message = value.action === 'manual' ? '已设为手动主账号'
              : value.action === 'remove' ? '已取消该账号的主账号标记' : value.action === 'clear-auto' ? `已清除 ${value.result.cleared} 个自动标记`
                : `新增 ${value.result.added_count} 个 · 已有自动 ${value.result.existing_auto_count} 个 · 保留手动 ${value.result.manual_preserved} 个`;
          }
        } catch (error) {
          recovery(value, '', '操作已完成，主账号资料未能刷新。请核对当前标记，无需再次提交。');
        }
        return true;
      } catch (error) {
        const definiteRejection = error?.status >= 400 && error.status < 500 && error.status !== 408;
        if (value.sent && !definiteRejection) recovery(value, '操作结果尚未确认。请读取当前标记并核对，避免重复提交。');
        else if (owns(value)) { view.phase = 'error'; view.error = String(error?.message || '操作未完成').slice(0, 300); }
        return false;
      } finally {
        if (job === value) job = null;
        if (owns(value) && view.phase === 'preflight') view.phase = view.screen === 'confirm' ? 'confirm' : 'idle';
        if (belongs(value) && session !== view && !unknown && session.phase === 'preflight') {
          session.phase = 'idle'; session.screen = 'list'; session.confirmation = null;
        }
        sync();
      }
    }
    async function reconcile() {
      const value = unknown, view = shown();
      if (!value || job || !view || !belongs(value)) return null;
      const context = { ...view.context }, reader = { ...value, reconciling: true };
      value.reconciled = false; job = reader; view.phase = 'reconciling'; view.error = ''; sync();
      try {
        const rows = listResult(await options.request(listPath(context)));
        if (unknown !== value || session !== view || !current(context)) return null;
        await options.publish?.(context, copyRows(rows), value.committed ? value.result : null);
        if (unknown !== value || session !== view || !current(context)) return null;
        value.reconciled = true; value.rows = rows; view.rows = rows; view.listLoaded = true;
        view.error = ''; view.refreshError = ''; view.message = '标记已刷新，核对后可继续管理。'; return rows;
      } catch (error) {
        if (unknown === value && session === view && current(context)) view.error = '主账号资料未能读取，请稍后重试。';
        return null;
      } finally {
        if (job === reader) job = null;
        if (session === view && current(context)) view.phase = 'unknown'; sync();
      }
    }
    function acknowledge() {
      const view = shown();
      if (!unknown?.reconciled || job || !view || !belongs(unknown)) return false;
      unknown = null; view.confirmation = null; view.phase = 'idle'; view.screen = 'list'; view.error = ''; view.refreshError = '';
      view.message = ''; sync(); return true;
    }
    return { open, close, revoke, status, blocked, sync, refresh, setFilter, showSearch, back, search,
      prepare, cancelConfirm, submit, reconcile, acknowledge };
  }
  root.FundsMainAccounts = Object.freeze({ create });
})(window);
