/* Shared import state for both entry points. Hosts own HTTP, leases and refresh. */
export function createImportWorkflow(options) {
  const lifetime = new AbortController(), listeners = new Set();
  let items = [], serial = 0, selectedId = null, reading = false, running = false, stopped = false, error = '', started = false;
  let snapshot;
  const alive = () => !lifetime.signal.aborted && !options.signal.aborted;
  const current = (item, token) => alive() && items.includes(item) && item.request === token;
  const active = item => ['uploading', 'previewing', 'committing', 'polling'].includes(item.phase);
  function publish() {
    snapshot = { items: items.map(({ files, clockStart, timed, ...item }) => ({ ...item })), selectedId, reading, running, stopped, error };
    for (const listener of listeners) listener();
  }
  function task(item) {
    if (!alive() || !item.importId) return;
    options.onTask?.({ id: `import:${item.importId}`, importId: item.importId, operation: 'import',
      status: item.phase === 'done' ? 'success' : item.phase === 'failed' ? 'error' : item.phase === 'unknown' ? 'unknown' : 'running',
      title: item.phase === 'done' ? '流水导入已完成' : item.phase === 'failed' ? '流水导入未完成' : item.phase === 'unknown' ? '导入结果待核对' : '正在导入流水',
      detail: item.phase === 'unknown' ? '只查询原任务，确认前不会再次提交。' : item.phase === 'done' ? '处理结果已保存，可打开原任务查看。' : '关闭进度后已提交任务仍继续。',
      progress: item.job?.total > 0 ? { done: item.job.done, total: item.job.total } : null });
  }
  function update(item, value) {
    if (item.timed && value.phase && value.phase !== item.phase) {
      if (item.clockStart !== null) { item.processingMs = (item.processingMs ?? 0) + performance.now() - item.clockStart; item.clockStart = null; }
      if (['uploading', 'previewing', 'committing', 'polling'].includes(value.phase)) item.clockStart = performance.now();
    }
    if (value.phase === 'unknown') { item.timed = false; item.processingMs = undefined; item.clockStart = null; }
    Object.assign(item, value); publish();
  }
  function makeItem(filename, files = [], directory = false) {
    return { id: String(++serial), filename, files, directory, phase: 'queued', importId: null, preview: null, job: null,
      mode: 'A', mapping: {}, mappingDrafts: {}, marked: [], selfAccounts: [], selfAccountsText: '', referencePolicy: false, dirty: false, error: '', request: 0,
      notified: false, selectionInitialized: false, resume: null, sizeBytes: files.length ? files.reduce((total, file) => total + file.size, 0) : undefined,
      timed: files.length > 0, clockStart: null, processingMs: undefined };
  }
  function accept(item, value) {
    if (value.case_id !== options.target.caseId || item.importId && value.import_id !== item.importId) throw new Error('预览与当前案件导入记录不一致。');
    item.importId = value.import_id; if (!item.directory) item.filename = value.filename; item.preview = value;
    item.mode = value.mode; item.mapping = { ...value.mapping }; item.mappingDrafts[value.mode] = item.mapping; item.referencePolicy = value.reference_policy === 'transaction-reference-v1';
    if (!item.selectionInitialized) { item.marked = value.self_accounts_in_file.map(account => account.account); item.selectionInitialized = true; }
    update(item, { dirty: false, phase: 'ready', error: '' });
  }
  function stop(message = '已停止后续文件；已提交任务继续处理') {
    stopped = true;
    for (const item of items) if (item.phase === 'queued') { item.files = []; item.phase = 'skipped'; item.error = message; }
    publish();
  }
  async function pause() {
    if (!alive()) return;
    await new Promise(resolve => {
      const finish = () => { clearTimeout(timer); lifetime.signal.removeEventListener('abort', finish); resolve(); };
      const timer = setTimeout(finish, 300);
      lifetime.signal.addEventListener('abort', finish, { once: true });
    });
  }
  async function poll(item, token, queryingOnly = false) {
    update(item, { phase: 'polling', error: '', loadingMembers: false, queryingOnly });
    try {
      while (current(item, token)) {
        const job = await options.client.job(item.importId, lifetime.signal);
        if (!current(item, token)) return;
        if (job.case_id !== options.target.caseId || job.import_id !== item.importId) throw new Error('导入进度与当前案件不一致。');
        item.job = job; item.error = ''; item.queryingOnly = false;
        if (job.stage === 'done') {
          if (!job.result || job.result.ok == null) throw new Error('导入完成回执不完整，请重新查询原任务。');
          update(item, { phase: 'done' }); task(item);
          if (!item.notified) {
            item.notified = true;
            try { await options.onImported?.(); }
            catch { if (current(item, token)) update(item, { error: '导入已完成，列表刷新失败。请重新查询当前案件。' }); }
          }
          return;
        }
        if (job.stage === 'error') { update(item, { phase: 'failed', error: job.error || '导入失败，未完成写入。' }); task(item); return; }
        publish(); task(item); await pause();
      }
    } catch (failure) {
      if (!current(item, token)) return;
      if (failure?.code === 'import_job_not_found') update(item, { phase: 'ready', queryingOnly: false, job: null, error: '服务端确认该导入任务尚未开始，请核对预览后重新开始导入。' });
      else { update(item, { phase: 'unknown', queryingOnly: false, error: `进度暂无法确认：${failure.message}。请重新查询进度，确认前不会再次提交。` }); task(item); }
    }
  }
  async function runCommit(item, token) {
    if (!current(item, token)) return;
    const value = item.preview;
    update(item, { phase: 'committing', error: '', job: null }); task(item);
    try {
      const result = await options.client.commit(item.importId, { mode: value.feedback ? value.mode : item.mode,
        mapping: value.feedback ? value.mapping : item.mapping, mark_accounts: item.marked,
        self_accounts: value.feedback ? [] : item.selfAccounts,
        ...(item.materialGroupId ? { material_group_id: item.materialGroupId } : {}) }, lifetime.signal);
      if (!current(item, token)) return;
      if (result.case_id !== options.target.caseId || result.import_id !== item.importId) throw new Error('导入确认与当前案件不一致。');
      await poll(item, token);
    } catch (failure) {
      if (current(item, token)) { update(item, { phase: 'unknown', error: failure.message }); task(item); }
    }
  }
  async function upload(read, directory = false) {
    if (!alive() || reading || running || items.some(item => active(item) || item.phase === 'unknown' && item.importId)) return;
    reading = true; stopped = false; error = ''; publish();
    try {
      const selection = read(lifetime.signal);
      const files = Array.isArray(selection) ? selection : await selection;
      if (!alive() || stopped || !files.length) return;
      if (directory && !options.client.previewDirectory) throw new Error('当前宿主尚未提供文件夹导入，请选择单个文件或反馈包。');
      const directoryPath = files[0].webkitRelativePath || files[0].name;
      items = directory ? [makeItem(directoryPath.includes('/') ? directoryPath.split('/')[0] : '所选文件夹', files, true)]
        : files.map(file => makeItem(file.name, [file]));
      const materialGroupId = crypto.randomUUID().replaceAll('-', '');
      for (const item of items) item.materialGroupId = materialGroupId;
      selectedId = items[0].id; running = true; publish();
      for (const item of items) {
        if (!alive() || stopped) break;
        const token = ++item.request, selected = item.files; item.files = [];
        update(item, { phase: 'uploading' });
        try {
          const value = directory ? await options.client.previewDirectory(selected, lifetime.signal)
            : await options.client.preview(selected[0], lifetime.signal);
          if (!current(item, token)) return;
          accept(item, value);
          // Only a fresh upload can authorize automatic submission. Repreview,
          // recovery and resuming a stopped queue never replay this decision.
          if (value.automatic === true && !stopped) await runCommit(item, token);
        } catch (failure) {
          if (!current(item, token)) return;
          const fileFailure = failure?.status === 400 && failure?.failureKind === 'file';
          update(item, { phase: fileFailure ? 'failed' : 'unknown', error: failure.message });
        }
        if (item.phase === 'unknown' || item.phase === 'failed' && item.importId) stop('原任务结果待核对，后续文件未上传');
      }
    } catch (failure) { if (alive()) { error = failure.message; publish(); } }
    finally { if (alive()) { reading = false; running = false; publish(); } }
  }
  async function repreview(id) {
    const item = items.find(row => row.id === id);
    if (!item || !alive() || running || active(item) || item.phase === 'unknown' || item.phase === 'done') return;
    const token = ++item.request;
    item.timed = true; item.processingMs = 0; item.clockStart = null;
    update(item, { phase: 'previewing', error: '', job: null, dirty: true });
    try {
      const body = { mode: item.preview?.feedback ? item.preview.mode : item.mode,
        mapping: item.preview?.feedback ? item.preview.mapping : item.mapping,
        ...(item.preview?.reference_policy_available || item.resume?.request?.reference_policy !== undefined
          ? { reference_policy: item.referencePolicy ? 'transaction-reference-v1' : null } : {}) };
      const value = await options.client.repreview(item.importId, body, lifetime.signal);
      if (current(item, token)) accept(item, value);
    } catch (failure) { if (current(item, token)) update(item, { phase: item.preview ? 'ready' : 'failed', error: failure.message }); }
  }
  async function query(id) {
    const item = items.find(row => row.id === id);
    if (!item || !alive() || active(item) || !item.importId) return;
    await poll(item, ++item.request, item.phase === 'unknown');
  }
  async function start() {
    if (started || !alive()) return;
    started = true;
    const source = options.resume;
    if (!source) return;
    if (source.case_id !== options.target.caseId || source.surface !== (options.surface || 'v1')) { error = '导入记录与当前案件工作区不一致。'; publish(); return; }
    const item = makeItem(source.filename); item.resume = source; item.importId = source.import_id;
    const saved = source.request || source;
    Object.assign(item, { mode: saved.mode, mapping: saved.mapping, marked: saved.mark_accounts ?? [],
      selfAccounts: saved.self_accounts ?? [], selfAccountsText: (saved.self_accounts ?? []).join(','), selectionInitialized: saved.mark_accounts != null,
      referencePolicy: saved.reference_policy === 'transaction-reference-v1', phase: 'ready' });
    items = [item]; selectedId = item.id; publish();
    if (source.stage === 'done') {
      if (!source.result || source.result.ok == null) { update(item, { phase: 'unknown', error: '历史导入回执不完整，请查询原任务。' }); return; }
      update(item, { phase: 'done', notified: true, loadingMembers: true, job: { ...source, done: source.result?.ok ?? 0, total: source.result?.total ?? 0, message: '完成', error: null } });
      const token = ++item.request;
      try {
        const job = await options.client.job(item.importId, lifetime.signal);
        if (!current(item, token)) return;
        if (job.case_id !== options.target.caseId || job.import_id !== item.importId || job.stage !== 'done') throw new Error('历史任务的成员处置尚无法确认。');
        // Keep the original receipt; only the explicitly current member journal
        // is refreshed. A failed read cannot erase known durable completion.
        update(item, { loadingMembers: false, job: { ...item.job, member_results: job.member_results } });
      } catch {
        if (current(item, token)) update(item, { loadingMembers: false, error: '历史回执已保留，成员处置暂未读取，请稍后从导入记录重新打开。' });
      }
    } else if (source.stage === 'parsing' || source.stage === 'importing') await query(item.id);
    else if (source.source_available && (source.stage === 'ready' || source.recoverable)) await repreview(item.id);
    else update(item, { phase: 'failed', error: '原始上传文件不可用于恢复，请重新上传。' });
  }
  function dispose() {
    lifetime.abort(); items = []; selectedId = null; reading = running = false; publish();
    options.signal.removeEventListener('abort', dispose);
  }
  options.signal.addEventListener('abort', dispose, { once: true });
  publish();
  return {
    snapshot: () => snapshot,
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    start, upload, stop, query, repreview, dispose,
    select(id) { if (alive() && items.some(item => item.id === id)) { selectedId = id; publish(); } },
    edit(id, change) {
      const item = items.find(row => row.id === id);
      if (!item || !alive() || active(item) || item.phase !== 'ready' || running) return;
      if (change.mode && change.mode !== item.mode) {
        item.mappingDrafts[item.mode] = item.mapping;
        if (item.mappingDrafts[change.mode]) change = { ...change, mapping: item.mappingDrafts[change.mode] };
      }
      for (const key of ['mapping', 'mode', 'marked', 'selfAccounts', 'selfAccountsText', 'referencePolicy']) if (Object.hasOwn(change, key)) item[key] = change[key];
      if (Object.hasOwn(change, 'selfAccountsText')) item.selfAccounts = change.selfAccountsText.split(/[,，;；\s]+/).filter(Boolean);
      else if (Object.hasOwn(change, 'selfAccounts')) item.selfAccountsText = change.selfAccounts.join(',');
      if (Object.keys(change).some(key => ['mapping', 'mode', 'referencePolicy'].includes(key))) item.dirty = true;
      publish();
    },
    async commit(id) {
      const item = items.find(row => row.id === id);
      if (!item || !alive() || running || item.phase !== 'ready' || item.dirty || !item.preview
        || !item.preview.feedback && !item.preview.table_summary && item.preview.stats.ok === 0
        || items.some(row => active(row) || row.phase === 'unknown')) return;
      await runCommit(item, ++item.request);
    },
    settled(importId) {
      const item = items.find(row => row.importId === importId);
      if (!item || !alive() || ['done', 'failed'].includes(item.phase)) return;
      void poll(item, ++item.request, item.phase === 'unknown' || item.queryingOnly === true);
    },
  };
}
