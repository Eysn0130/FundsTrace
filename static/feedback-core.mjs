// Presentation state only. Business operations and cancellation stay with callers.
const SCOPE_KEYS = ['caseId', 'canvasId', 'generation', 'canvasGeneration'];
const SEVERITIES = new Set(['success', 'info', 'warning', 'error']);
const STATUSES = new Set(['queued', 'running', 'unknown', 'success', 'warning', 'error', 'cancelled']);
const TERMINAL = new Set(['success', 'warning', 'error', 'cancelled']);
const IMPORTANT = new Set(['warning', 'error']);
const MAX_TOASTS = 3;
const MAX_QUEUE = 20;
const MAX_ACTIVITY = 100;

export function createFeedbackStore({ now = Date.now, schedule = setTimeout, cancel = clearTimeout } = {}) {
  let sequence = 0, binding = 0, disposed = false;
  let scope = Object.freeze({ binding });
  let cached;
  const listeners = new Set(), pauses = new Set(), records = new Map(), tasks = new Map(), activity = new Map();
  const active = [], queue = [];

  function normalizeScope(value) {
    if (value === null) return null;
    const normalized = {};
    for (const key of SCOPE_KEYS) if (value && value[key] !== undefined) normalized[key] = value[key] === null ? null : String(value[key]);
    if (value && value.binding !== undefined) normalized.binding = value.binding;
    return Object.freeze(normalized);
  }

  function matches(value) {
    return value === null || ((!('binding' in value) || value.binding === binding)
      && SCOPE_KEYS.every(key => !(key in value) || value[key] === scope[key]));
  }

  function current(record) { return record.valid && matches(record.scope); }
  function actionsFor(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    return value.filter(action => action && action.id != null && typeof action.run === 'function' && action.run instanceof Function)
      .map(action => ({ id: String(action.id), label: String(action.label || action.id), run: action.run }))
      .filter(action => !seen.has(action.id) && seen.add(action.id));
  }

  function view(record) {
    const item = {
      id: record.id, kind: record.kind, title: record.title, detail: record.detail, scope: record.scope,
      createdAt: record.createdAt, updatedAt: record.updatedAt,
      actions: Object.freeze(record.actions.map(({ id, label }) => Object.freeze({ id, label }))),
    };
    if (record.kind === 'task') Object.assign(item, { status: record.status, progress: record.progress, dismissed: record.dismissed });
    else Object.assign(item, { severity: record.severity, count: record.count });
    return Object.freeze(item);
  }

  function rebuild() {
    cached = Object.freeze({
      scope,
      toasts: Object.freeze(active.filter(current).map(view)),
      tasks: Object.freeze([...tasks.values()].filter(current).reverse().map(view)),
      activity: Object.freeze([...activity.values()].filter(current).reverse().map(view)),
    });
  }

  function emit() {
    rebuild();
    for (const listener of [...listeners]) listener();
  }

  function stopTimer(record) {
    if (record.timer !== undefined) {
      cancel(record.timer);
      record.timer = undefined;
      record.remaining = Math.max(0, record.remaining - Math.max(0, now() - record.startedAt));
    }
  }

  function startTimer(record) {
    if (disposed || pauses.size || record.duration === 0 || record.timer !== undefined) return;
    record.startedAt = now();
    record.timer = schedule(() => {
      record.timer = undefined;
      removeToast(record);
      promote();
      emit();
    }, record.remaining);
  }

  function removeToast(record) {
    stopTimer(record);
    for (const list of [active, queue]) {
      const index = list.indexOf(record);
      if (index >= 0) list.splice(index, 1);
    }
    if (!activity.has(record.id)) records.delete(record.id);
  }

  function promote() {
    while (active.length < MAX_TOASTS && queue.length) {
      const record = queue.shift();
      if (current(record)) { active.push(record); startTimer(record); }
      else if (!activity.has(record.id)) records.delete(record.id);
    }
  }

  function remember(record) {
    activity.delete(record.id);
    activity.set(record.id, record);
    while (activity.size > MAX_ACTIVITY) {
      const id = activity.keys().next().value;
      activity.delete(id);
      if (!active.some(item => item.id === id) && !queue.some(item => item.id === id) && !tasks.has(id)) records.delete(id);
    }
  }

  function publish(input, eventScope) {
    const severity = SEVERITIES.has(input.severity) ? input.severity : 'info';
    const key = input.dedupeKey == null ? null : String(input.dedupeKey);
    let record = input.id == null ? null : records.get(String(input.id));
    if (!record && key !== null) record = [...records.values()].find(item => item.kind === 'notification' && item.dedupeKey === key && current(item)
      && JSON.stringify(item.scope) === JSON.stringify(eventScope));
    if (record?.kind !== 'notification' || !current(record)) record = null;
    const duration = typeof input.duration === 'number' && Number.isFinite(input.duration) && input.duration >= 0 ? input.duration : 4000;
    if (record) {
      stopTimer(record);
      Object.assign(record, { title: String(input.title || ''), detail: input.detail == null ? '' : String(input.detail), severity,
        actions: actionsFor(input.actions), updatedAt: now(), duration, remaining: duration, count: record.count + 1 });
    } else {
      record = { id: input.id == null ? `feedback-${++sequence}` : String(input.id), kind: 'notification',
        title: String(input.title || ''), detail: input.detail == null ? '' : String(input.detail), severity,
        scope: eventScope, valid: true, actions: actionsFor(input.actions), dedupeKey: key, count: 1,
        createdAt: now(), updatedAt: now(), duration, remaining: duration, timer: undefined };
      // Notification IDs must not overwrite an unrelated task's action target.
      if (tasks.has(record.id)) return false;
      records.set(record.id, record);
    }
    if (IMPORTANT.has(severity)) remember(record);
    if (!active.includes(record) && !queue.includes(record)) {
      if (active.length < MAX_TOASTS) active.push(record);
      else {
        if (queue.length >= MAX_QUEUE) removeToast(queue[0]);
        queue.push(record);
      }
    }
    if (active.includes(record)) startTimer(record);
    return record.id;
  }

  function inputScope(input) { return input.scope === undefined ? scope : normalizeScope(input.scope); }
  const api = {
    bind(value) {
      if (disposed) return scope;
      const normalized = normalizeScope(value);
      scope = Object.freeze({ ...(normalized || {}), binding: ++binding });
      for (const record of records.values()) if (record.scope !== null && !matches(record.scope)) record.valid = false;
      // Short messages belong to the view that produced them. App messages are explicit null scopes.
      for (const record of [...active, ...queue]) if (record.scope !== null) removeToast(record);
      promote();
      emit();
      return scope;
    },
    notify(input) {
      if (disposed || !input) return false;
      const eventScope = inputScope(input);
      if (!matches(eventScope)) return false;
      const result = publish(input, eventScope);
      if (result !== false) emit();
      return result;
    },
    task(input) {
      if (disposed || !input || input.id == null || !STATUSES.has(input.status)) return false;
      const eventScope = inputScope(input);
      if (!matches(eventScope)) return false;
      const id = String(input.id), old = tasks.get(id);
      const previous = old && current(old) ? old : null;
      const done = input.progress?.done, total = input.progress?.total;
      const progress = Number.isFinite(done) && Number.isFinite(total) && total > 0 && done >= 0 && done <= total
        ? Object.freeze({ done, total }) : null;
      const record = {
        id, kind: 'task', scope: eventScope, valid: true, status: input.status,
        title: String(input.title || ''), detail: input.detail == null ? '' : String(input.detail), progress,
        actions: actionsFor(input.actions), createdAt: previous?.createdAt ?? now(), updatedAt: now(),
        dismissed: previous?.dismissed ?? false,
      };
      if (previous && TERMINAL.has(previous.status) && !TERMINAL.has(input.status)) record.dismissed = false;
      const collision = records.get(id);
      if (collision?.kind === 'notification') removeToast(collision);
      tasks.set(id, record); records.set(id, record);
      if (TERMINAL.has(input.status) || input.status === 'unknown' || activity.has(id)) remember(record);
      if (input.announce === true && previous?.status !== input.status && (TERMINAL.has(input.status) || input.status === 'unknown')) {
        publish({ title: record.title, detail: record.detail, actions: input.actions,
          severity: input.status === 'unknown' ? 'warning' : input.status === 'cancelled' ? 'info' : input.status }, eventScope);
      }
      emit();
      return id;
    },
    dismiss(id) {
      if (disposed) return false;
      const record = records.get(String(id));
      if (!record || !current(record)) return false;
      if (record.kind === 'task') record.dismissed = true;
      else { removeToast(record); promote(); }
      emit();
      return true;
    },
    revealTasks() {
      if (disposed) return;
      let changed = false;
      for (const record of tasks.values()) if (current(record) && record.dismissed) { record.dismissed = false; changed = true; }
      if (changed) emit();
    },
    runAction(itemId, actionId) {
      if (disposed) return false;
      const record = records.get(String(itemId));
      if (!record || !current(record)) return false;
      const action = record.actions.find(item => item.id === String(actionId));
      if (!action) return false;
      return action.run() ?? true;
    },
    pause(reason = 'manual') {
      if (disposed || pauses.has(reason)) return;
      pauses.add(reason);
      if (pauses.size === 1) for (const record of active) stopTimer(record);
    },
    resume(reason = 'manual') {
      if (disposed || !pauses.delete(reason) || pauses.size) return;
      for (const record of active) startTimer(record);
    },
    subscribe(listener) {
      if (disposed) return () => {};
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot() { return cached; },
    dispose() {
      if (disposed) return;
      disposed = true;
      for (const record of active) stopTimer(record);
      listeners.clear(); pauses.clear(); records.clear(); tasks.clear(); activity.clear(); active.length = 0; queue.length = 0;
      rebuild();
    },
  };
  rebuild();
  return Object.freeze(api);
}
