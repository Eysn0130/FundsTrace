/* One case-bound, in-memory sequence. Files are never read or persisted here. */
(function (root) {
  'use strict';
  function create(files, options) {
    const context = Object.freeze({ ...options.context });
    const items = Array.from(files, (file, index) => ({ id: index + 1, name: file.name,
      state: 'queued', importId: null, message: '', file }));
    files = null;
    let running = false, started = false, stopped = false, reason = '';
    const snapshot = () => ({ context, running, stopped, reason,
      items: items.map(({ file, ...item }) => ({ ...item })) });
    const changed = () => options.changed?.(snapshot());
    function update(id, value) {
      const item = items.find(item => item.id === id);
      if (!item) return;
      for (const key of ['state', 'importId', 'message', 'counts']) {
        if (Object.hasOwn(value, key)) item[key] = value[key];
      }
      changed();
    }
    function stop(message = '已停止后续文件') {
      stopped = true; reason = message;
      for (const item of items) if (item.state === 'queued') {
        item.state = 'skipped'; item.message = '未上传'; item.file = null;
      }
      changed();
    }
    async function start() {
      if (started) return;
      started = true; running = true; changed();
      try {
        for (const item of items) {
          if (stopped) break;
          if (!options.isCurrent(context)) { stop('案件绑定已失效，未启动的文件不会上传'); break; }
          const file = item.file; item.file = null;
          item.state = 'working'; changed();
          let outcome;
          try { outcome = await options.run({ id: item.id, name: item.name }, file, value => update(item.id, value)); }
          catch (_) { outcome = { state: 'unknown', message: '处理结果未确认，请从导入记录核对' }; }
          if (!outcome || !['done', 'waiting', 'failed', 'unknown', 'skipped'].includes(outcome.state)) {
            outcome = { state: 'unknown', message: '未取得可靠处理结果，请从导入记录核对' };
          }
          update(item.id, outcome);
          if (outcome.stop || outcome.state === 'unknown') stop(outcome.message || '处理结果未确认，已停止后续文件');
        }
      } finally {
        running = false; changed();
      }
    }
    return Object.freeze({ context, snapshot, update, start, stop });
  }
  root.FundsImportQueue = Object.freeze({ create });
})(window);
