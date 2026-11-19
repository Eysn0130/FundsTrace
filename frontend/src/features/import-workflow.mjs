/* Shared import state for both entry points. Hosts own HTTP, leases and refresh. */
export function createImportWorkflow(options) {
  const lifetime = new AbortController(), listeners = new Set();
  const receiptChecks = new WeakMap();
  const unconfirmed = '原任务未确认完成，后续文件未上传';
  let items = [], serial = 0, selectedId = null, reading = false, running = false, stopped = false, error = '', started = false;
  let intake = 0, dataVersion = 0, withdrawing = false;
  let snapshot;
  const alive = () => !lifetime.signal.aborted && !options.signal.aborted;
  const current = (item, token) => alive() && items.includes(item) && item.request === token;
  const active = item => ['uploading', 'previewing', 'committing', 'polling'].includes(item.phase);
  function publish() {
    snapshot = { items: items.map(({ files, clockStart, timed, autoCancelled, ...item }) => ({ ...item })), selectedId, reading, running, stopped, error, dataVersion, withdrawing };
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
    intake++;
    stopped = true;
    for (const item of items) {
      if (item.phase === 'queued') { item.files = []; item.phase = 'skipped'; item.error = message; }
      if (item.phase === 'uploading') item.autoCancelled = true;
    }
    publish();
  }
  async function pause(signal) {
    if (signal.aborted) return;
    await new Promise(resolve => {
      const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
      const timer = setTimeout(finish, 300);
      signal.addEventListener('abort', finish, { once: true });
    });
  }
  async function poll(item, token, queryingOnly, signal) {
    update(item, { phase: 'polling', error: '', loadingMembers: false, queryingOnly });
    try {
      while (current(item, token)) {
        const job = await options.client.job(item.importId, signal);
        if (!current(item, token)) return;
        if (job.case_id !== options.target.caseId || job.import_id !== item.importId) throw new Error('导入进度与当前案件不一致。');
        item.job = job; item.error = ''; item.queryingOnly = false;
        if (job.stage === 'done') {
          if (!job.result || job.result.ok == null) throw new Error('导入完成回执不完整，请重新查询原任务。');
          if (!item.notified) dataVersion++;
          update(item, { phase: 'done' }); task(item);
          if (!item.notified) {
            item.notified = true;
            try { await options.onImported?.(); }
            catch { if (current(item, token)) update(item, { error: '导入已完成，列表刷新失败。请重新查询当前案件。' }); }
          }
          return;
        }
        if (job.stage === 'error') { update(item, { phase: 'failed', error: job.error || '导入失败，未完成写入。' }); task(item); return; }
        publish(); task(item); await pause(signal);
      }
    } catch (failure) {
      if (!current(item, token)) return;
      if (failure?.code === 'import_job_not_found') update(item, { phase: 'ready', queryingOnly: false, job: null, error: '服务端确认该导入任务尚未开始，请核对预览后重新开始导入。' });
      else { update(item, { phase: 'unknown', queryingOnly: false, error: `进度暂无法确认：${failure.message}。请重新查询进度，确认前不会再次提交。` }); task(item); }
    }
  }
  async function runCommit(item, token, pollSignal) {
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
      await poll(item, token, false, pollSignal);
    } catch (failure) {
      if (current(item, token)) { update(item, { phase: 'unknown', error: failure.message }); task(item); }
    }
  }
  async function followImport(item, token, submitting = false) {
    const controller = new AbortController();
    const cancelRead = () => controller.abort();
    lifetime.signal.addEventListener('abort', cancelRead, { once: true });
    const queryingOnly = item.phase === 'unknown' || item.queryingOnly === true;
    let settledToken;
    const hint = new Promise(resolve => {
      receiptChecks.set(item, () => {
        if (settledToken !== undefined) return;
        settledToken = ++item.request;
        resolve();
        cancelRead();
      });
    });
    try {
      const pending = submitting ? runCommit(item, token, controller.signal) : poll(item, token, queryingOnly, controller.signal);
      // The hint transfers the read before either request can publish a stale receipt.
      await Promise.race([pending, hint]);
      if (settledToken !== undefined && current(item, settledToken)) {
        await poll(item, settledToken, queryingOnly, lifetime.signal);
      }
      if (submitting && alive() && item.phase !== 'done') stop(unconfirmed);
    } finally {
      lifetime.signal.removeEventListener('abort', cancelRead);
      receiptChecks.delete(item);
    }
  }
  async function drain() {
    if (!alive() || running || withdrawing || stopped || items.some(item => active(item) || item.phase === 'unknown') || !items.some(item => item.phase === 'queued')) return;
    running = true;
    try {
      while (alive() && !stopped) {
        if (items.some(item => active(item) || item.phase === 'unknown')) break;
        const item = items.find(row => row.phase === 'queued');
        if (!item) break;
        const token = ++item.request, selected = item.files; item.files = [];
        update(item, { phase: 'uploading' });
        try {
          const value = item.directory ? await options.client.previewDirectory(selected, lifetime.signal)
            : await options.client.preview(selected[0], lifetime.signal);
          if (!current(item, token)) return;
          accept(item, value);
          // Only a fresh upload can authorize automatic submission. Repreview,
          // recovery and resuming a stopped queue never replay this decision.
          if (value.automatic === true && !item.autoCancelled) await followImport(item, token, true);
        } catch (failure) {
          if (!current(item, token)) return;
          const fileFailure = failure?.status === 400 && failure?.failureKind === 'file';
          update(item, { phase: fileFailure ? 'failed' : 'unknown', error: failure.message });
          if (!fileFailure) stop(unconfirmed);
        }
      }
    } finally { if (alive()) { running = false; publish(); } }
  }
  async function receive(read, directory, append) {
    if (!alive() || reading || withdrawing || (!append && (running || items.some(item => active(item) || item.phase === 'unknown' && item.importId)))) return;
    if (append && items.some(item => item.phase === 'unknown' && !item.importId)) {
      error = '请先从导入记录核对未确认的文件，再追加资料。'; publish(); return;
    }
    const token = ++intake;
    reading = true; error = ''; publish();
    try {
      const selection = read(lifetime.signal);
      const files = Array.isArray(selection) ? selection : await selection;
      if (!alive() || token !== intake || !files.length) return;
      if (directory && !options.client.previewDirectory) throw new Error('当前宿主不支持文件夹导入，请选择文件或压缩包。');
      const directoryPath = files[0].webkitRelativePath || files[0].name;
      const accepted = directory ? [makeItem(directoryPath.includes('/') ? directoryPath.split('/')[0] : '所选文件夹', files, true)]
        : files.map(file => makeItem(file.name, [file]));
      const materialGroupId = crypto.randomUUID().replaceAll('-', '');
      for (const item of accepted) item.materialGroupId = materialGroupId;
      items = append ? [...items, ...accepted] : accepted;
      if (!append || selectedId === null) selectedId = accepted[0].id;
      // A fresh selection authorizes those files only. Previously skipped files stay skipped.
      stopped = false;
    } catch (failure) { if (alive() && token === intake) error = failure.message; }
    finally { if (alive()) { reading = false; publish(); } }
    await drain();
  }
  async function repreview(id) {
    const item = items.find(row => row.id === id);
    if (!item || !alive() || running || withdrawing || items.some(active) || item.phase === 'unknown' || item.phase === 'done') return;
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
    await drain();
  }
  async function query(id) {
    const item = items.find(row => row.id === id);
    if (!item || !alive() || withdrawing || items.some(active) || receiptChecks.has(item) || !item.importId) return;
    await followImport(item, ++item.request);
    await drain();
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
        update(item, { loadingMembers: false, job: { ...item.job, member_results: job.member_results, withdrawn: job.withdrawn, source_available: job.source_available } });
      } catch {
        if (current(item, token)) update(item, { loadingMembers: false, error: '历史回执已保留，成员处置暂未读取，请稍后从导入记录重新打开。' });
      }
    } else if (source.stage === 'parsing' || source.stage === 'importing') await query(item.id);
    else if (source.source_available && (source.stage === 'ready' || source.recoverable)) await repreview(item.id);
    else update(item, { phase: 'failed', error: '原始上传文件不可用于恢复，请重新上传。' });
  }
  function removeItem(item) {
    const index = items.indexOf(item);
    item.files = [];
    items.splice(index, 1);
    if (selectedId === item.id) selectedId = (items[index] ?? items[index - 1])?.id ?? null;
    publish();
  }
  async function withdraw(id, batchId) {
    if (!alive()) return false;
    if (withdrawing || reading || running || items.some(row => active(row) || row.phase === 'queued' || row.phase === 'unknown')) {
      throw new Error('请在导入结算后撤回。');
    }
    const item = items.find(row => row.id === id);
    const receipt = item?.phase === 'done' && item.job?.result;
    if (!receipt?.source_id || receipt.batch_id !== batchId || item.job.withdrawn || !options.client.withdrawImportSource) {
      throw new Error('该原件的导入状态已变化，请重新核对文件信息。');
    }
    withdrawing = true; publish();
    const token = ++item.request;
    try {
      const result = await options.client.withdrawImportSource(receipt.source_id, batchId, lifetime.signal);
      if (!current(item, token)) return false;
      if (result.case_id !== options.target.caseId || result.source_id !== receipt.source_id || result.batch_id !== batchId) {
        throw new Error('撤回回执与当前原件不一致，请重新核对文件信息。');
      }
      dataVersion++;
      removeItem(item);
      try {
        await options.onImported?.();
      } catch {
        if (alive()) error = '导入项已删除，案件数据暂未刷新。';
      }
      return alive();
    } finally { if (alive()) { withdrawing = false; publish(); } }
  }
  function dispose() {
    lifetime.abort(); items = []; selectedId = null; reading = running = withdrawing = false; publish();
    options.signal.removeEventListener('abort', dispose);
  }
  options.signal.addEventListener('abort', dispose, { once: true });
  publish();
  return {
    snapshot: () => snapshot,
    subscribe: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    start, stop, query, repreview, withdraw, dispose,
    upload: (read, directory = false) => receive(read, directory, false),
    append: (read, directory = false) => receive(read, directory, true),
    remove(id) {
      const item = items.find(row => row.id === id);
      if (!alive() || withdrawing || !item || item.queryingOnly || !(['queued', 'ready', 'failed', 'skipped'].includes(item.phase) || item.phase === 'done' && item.job?.withdrawn)) return false;
      removeItem(item);
      return true;
    },
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
      if (!item || !alive() || running || withdrawing || item.phase !== 'ready' || item.dirty || !item.preview
        || !item.preview.feedback && !item.preview.table_summary && item.preview.stats.ok === 0
        || items.some(row => active(row) || row.phase === 'unknown')) return;
      await followImport(item, ++item.request, true);
      await drain();
    },
    settled(importId) {
      const item = items.find(row => row.importId === importId);
      if (!item || !alive() || !['committing', 'polling', 'unknown'].includes(item.phase)) return;
      const check = receiptChecks.get(item);
      if (check) check();
      else if (item.phase === 'unknown') {
        // Another file's repreview can delay uploads without blocking this receipt read.
        void poll(item, ++item.request, true, lifetime.signal).then(drain);
      }
    },
  };
}
