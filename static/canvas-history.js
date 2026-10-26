/* In-memory display history. The caller supplies case-bound display snapshots only. */
(function (global) {
  'use strict';

  function create({ capture, restore, onChange, maxEntries = 40, maxBytes = 8 * 1024 * 1024 }) {
    if (typeof capture !== 'function' || typeof restore !== 'function') {
      throw new TypeError('Canvas history requires capture and restore functions.');
    }
    for (const value of [maxEntries, maxBytes]) {
      if (!Number.isSafeInteger(value) || value < 0) throw new RangeError('History limits must be non-negative integers.');
    }

    let entries = [], cursor = 0, bytes = 0, generation = 0, bindingGeneration = 0, pending = 0;
    let queue = Promise.resolve(), message = '', limitReached = false;

    function status() {
      return {
        busy: pending > 0,
        canUndo: pending === 0 && cursor > 0,
        canRedo: pending === 0 && cursor < entries.length,
        undoLabel: cursor > 0 ? entries[cursor - 1].label : '',
        redoLabel: cursor < entries.length ? entries[cursor].label : '',
        count: entries.length, bytes, message, limitReached,
      };
    }

    function changed() {
      // An observer must not break serialization or leave the controller busy.
      if (typeof onChange === 'function') {
        try { onChange(status()); } catch (_) { /* UI observers do not own history. */ }
      }
    }

    function serialize(value) {
      const json = JSON.stringify(value);
      if (json === undefined) throw new TypeError('History snapshots must be JSON serializable.');
      return json;
    }

    function clear() {
      entries = []; cursor = 0; bytes = 0;
    }

    function reset(_bindingKey) {
      // Every reset is a new binding, including A -> B -> A or a same-key reload.
      generation += 1;
      bindingGeneration += 1;
      queue = Promise.resolve(); pending = 0;
      clear(); message = ''; limitReached = false;
      changed();
    }

    function invalidate() {
      // New graph authority retires old snapshots, but an already-running
      // command still owns its lock until it actually finishes. Later work
      // stays serialized behind it; old queued work cannot start or record.
      generation += 1;
      clear(); message = ''; limitReached = false;
      changed();
    }

    function recordJSON(label, before, after) {
      if (before === after) return false;
      label = String(label || '');
      // Retain strings, never caller-owned objects. UTF-16 storage is a conservative
      // byte estimate for the serialized snapshots and label (metadata is bounded by maxEntries).
      const size = 2 * (before.length + after.length + label.length);
      if (maxEntries === 0 || size > maxBytes) {
        // Keeping an older undo after skipping this change would cross an untracked edit.
        clear(); limitReached = true;
        message = '此操作超出历史容量，当前历史已清空。';
        return false;
      }
      while (entries.length > cursor) bytes -= entries.pop().size;
      entries.push({ label, before, after, size });
      cursor += 1; bytes += size;
      limitReached = false; message = '';
      while (entries.length > maxEntries || bytes > maxBytes) {
        bytes -= entries.shift().size; cursor -= 1;
        limitReached = true; message = '历史容量已满，较早操作已清除。';
      }
      return true;
    }

    function record(label, before, after) {
      if (pending > 0) return false;
      const recorded = recordJSON(label, serialize(before), serialize(after));
      if (recorded || limitReached) changed();
      return recorded;
    }

    function enqueue(operation) {
      const token = generation, binding = bindingGeneration;
      pending += 1;
      const task = queue.then(async () => {
        try {
          if (token !== generation) return false;
          return await operation(token);
        } finally {
          // A late old-binding completion must not release a new binding's busy state.
          if (binding === bindingGeneration) { pending -= 1; changed(); }
        }
      });
      queue = task.catch(() => {});
      changed();
      return task;
    }

    function run(label, action) {
      return enqueue(async token => {
        const before = serialize(capture());
        await action();
        if (token !== generation) return false;
        recordJSON(label, before, serialize(capture()));
        return true;
      });
    }

    function travel(direction) {
      return enqueue(async token => {
        const index = direction < 0 ? cursor - 1 : cursor;
        const entry = entries[index];
        if (!entry) return false;
        const target = direction < 0 ? entry.before : entry.after;
        const expected = direction < 0 ? entry.after : entry.before;
        // restore rejects or returns false on a stale binding/state. Stack movement
        // happens only after success; parsed arguments cannot mutate stored history.
        const restored = await restore(JSON.parse(target), JSON.parse(expected));
        if (token !== generation || restored === false) return false;
        cursor += direction;
        return true;
      });
    }

    return Object.freeze({ reset, invalidate, run, record, undo: () => travel(-1), redo: () => travel(1), status });
  }

  const api = Object.freeze({ create });
  global.FundsCanvasHistory = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
