import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { createFeedbackStore } from '../static/feedback-core.mjs';

function clock() {
  let time = 0, next = 0;
  const timers = new Map();
  const store = createFeedbackStore({ now: () => time,
    schedule(fn, delay) { const id = ++next; timers.set(id, { fn, due: time + delay }); return id; },
    cancel(id) { timers.delete(id); },
  });
  return { store, timers, tick(ms) {
    const end = time + ms;
    while (true) {
      const due = [...timers].filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
      if (!due) break;
      time = due[1].due; timers.delete(due[0]); due[1].fn();
    }
    time = end;
  } };
}

test('snapshots stay stable between mutations and item actions expose no executable callback', () => {
  const { store } = clock();
  assert.equal(store.snapshot(), store.snapshot());
  let changes = 0, calls = 0;
  const unsubscribe = store.subscribe(() => changes++);
  const id = store.notify({ severity: 'info', title: '已准备', actions: [{ id: 'open', label: '查看', run: () => calls++ }] });
  const value = store.snapshot();
  assert.equal(value, store.snapshot()); assert.equal(changes, 1);
  assert.deepEqual(value.toasts[0].actions, [{ id: 'open', label: '查看' }]);
  assert.equal(Object.isFrozen(value.toasts), true);
  store.runAction(id, 'open'); assert.equal(calls, 1);
  unsubscribe(); store.dismiss(id); assert.equal(changes, 1);
});

test('three visible messages, bounded queue, and dedupe update through the public notification API', () => {
  const { store, tick } = clock();
  const id = store.notify({ severity: 'success', title: '已保存', dedupeKey: 'save' });
  assert.equal(store.notify({ severity: 'success', title: '再次保存', dedupeKey: 'save' }), id);
  assert.equal(store.snapshot().toasts.length, 1); assert.equal(store.snapshot().toasts[0].count, 2);
  for (let i = 0; i < 30; i++) store.notify({ id: `n-${i}`, severity: 'info', title: `通知${i}` });
  assert.equal(store.snapshot().toasts.length, 3);
  tick(4000);
  // The oldest ten queued messages were replaced; visible messages were never displaced.
  assert.deepEqual(store.snapshot().toasts.map(item => item.id), ['n-10', 'n-11', 'n-12']);
  tick(32000); assert.equal(store.snapshot().toasts.length, 0);
});

test('hover, focus, hidden documents and modal pauses preserve the remaining four seconds', () => {
  const { store, tick, timers } = clock();
  store.notify({ severity: 'info', title: '处理中' });
  tick(1000); store.pause('hover'); store.pause('focus'); assert.equal(timers.size, 0);
  tick(50000); store.resume('hover'); assert.equal(timers.size, 0);
  store.pause('hidden'); store.resume('focus'); tick(50000);
  assert.equal(store.snapshot().toasts.length, 1);
  store.resume('hidden'); tick(2999); assert.equal(store.snapshot().toasts.length, 1);
  tick(1); assert.equal(store.snapshot().toasts.length, 0);
  store.pause('dialog'); store.notify({ severity: 'info', title: '延后通知' }); tick(10000);
  assert.equal(store.snapshot().toasts.length, 1);
  store.resume('dialog'); tick(4000); assert.equal(store.snapshot().toasts.length, 0);
});

test('bind clears scoped short messages synchronously while app messages survive', () => {
  const { store } = clock();
  store.bind({ caseId: 7, generation: 1, canvasId: 2, canvasGeneration: 1 });
  store.notify({ severity: 'success', title: '案件消息' });
  store.notify({ severity: 'info', title: '全局更新', scope: null });
  store.bind({ caseId: '8', generation: '2' });
  assert.deepEqual(store.snapshot().toasts.map(item => item.title), ['全局更新']);
});

test('partial case scopes survive canvas changes and expired tasks never reappear after A-B-A', () => {
  const { store } = clock();
  const a = store.bind({ caseId: 'A', generation: 1, canvasId: '1', canvasGeneration: 1 });
  let calls = 0;
  store.task({ id: 'case-import', scope: { caseId: 'A', generation: 1 }, status: 'running', title: '导入案件', actions: [{ id: 'review', label: '核对', run: () => calls++ }] });
  store.task({ id: 'canvas', scope: a, status: 'running', title: '画布任务' });
  store.bind({ caseId: 'A', generation: 1, canvasId: '2', canvasGeneration: 2 });
  assert.deepEqual(store.snapshot().tasks.map(item => item.id), ['case-import']);
  assert.equal(store.runAction('case-import', 'review'), 0); assert.equal(calls, 1);
  assert.equal(store.notify({ scope: a, severity: 'error', title: '迟到消息' }), false);
  store.bind({ caseId: 'B', generation: 2, canvasId: '1', canvasGeneration: 3 });
  store.bind({ caseId: 'A', generation: 1, canvasId: '2', canvasGeneration: 2 });
  assert.equal(store.snapshot().tasks.length, 0);
  assert.equal(store.runAction('case-import', 'review'), false); assert.equal(calls, 1);
});

test('same-value binding revokes previously captured binding token and global actions remain usable', () => {
  const { store } = clock();
  const before = store.bind({ caseId: 'A', generation: 1 });
  store.task({ id: 'old', scope: before, status: 'running', title: '原任务' });
  let calls = 0;
  store.task({ id: 'global', scope: null, status: 'unknown', title: '更新待核对', actions: [{ id: 'open', label: '查看', run: () => calls++ }] });
  store.bind({ caseId: 'A', generation: 1 });
  assert.equal(store.task({ id: 'old', scope: before, status: 'success', title: '旧完成' }), false);
  assert.deepEqual(store.snapshot().tasks.map(item => item.id), ['global']);
  store.runAction('global', 'open'); assert.equal(calls, 1);
});

test('unknown and important content persists; closing changes presentation but never business task state', () => {
  const { store, tick } = clock();
  store.task({ id: 'u', status: 'unknown', title: '结果待核对', detail: '请核对回执' });
  assert.equal(store.snapshot().toasts.length, 0);
  store.notify({ id: 'warning', severity: 'warning', title: '部分数据需核对' });
  tick(4000); assert.equal(store.snapshot().toasts.length, 0);
  assert.equal(store.snapshot().activity.length, 2);
  store.dismiss('u');
  assert.equal(store.snapshot().tasks[0].status, 'unknown'); assert.equal(store.snapshot().tasks[0].dismissed, true);
  store.revealTasks(); assert.equal(store.snapshot().tasks[0].dismissed, false);
  tick(100000); assert.equal(store.snapshot().tasks[0].status, 'unknown');
});

test('progress requires a finite positive total and valid completed amount', () => {
  const { store } = clock();
  for (const progress of [null, { done: 1 }, { done: 0, total: 0 }, { done: -1, total: 10 }, { done: 11, total: 10 }, { done: 1, total: Infinity }]) {
    store.task({ id: 'p', status: 'running', title: '分析', progress });
    assert.equal(store.snapshot().tasks[0].progress, null);
  }
  store.task({ id: 'p', status: 'running', title: '分析', progress: { done: 4, total: 10 } });
  assert.deepEqual(store.snapshot().tasks[0].progress, { done: 4, total: 10 });
});

test('terminal announcements are explicit and repeated terminal updates cannot announce twice', () => {
  const { store } = clock();
  store.task({ id: 't', status: 'running', title: '导出' });
  store.task({ id: 't', status: 'success', title: '导出完成', announce: true });
  store.task({ id: 't', status: 'success', title: '导出完成', announce: true });
  assert.equal(store.snapshot().toasts.length, 1);
  store.task({ id: 'historical', status: 'error', title: '历史错误' });
  assert.equal(store.snapshot().toasts.length, 1);
});

test('cross-realm actions are rejected and disposal stops timers and further changes', () => {
  const { store, timers } = clock();
  const foreign = vm.runInNewContext('() => 1');
  const id = store.notify({ severity: 'info', title: '消息', actions: [{ id: 'foreign', label: '不接受外部函数', run: foreign }] });
  assert.deepEqual(store.snapshot().toasts[0].actions, []); assert.equal(store.runAction(id, 'foreign'), false);
  assert.equal(timers.size, 1); store.dispose(); assert.equal(timers.size, 0);
  assert.equal(store.notify({ severity: 'info', title: '关闭后' }), false);
  assert.equal(store.snapshot().toasts.length, 0);
});

// The browser module runs unchanged against the DOM methods it uses. Native modal
// state is controlled independently to verify no hidden global action stays usable.
function domHarness() {
  class Element extends EventTarget {
    constructor(tag) {
      super(); this.tagName = tag.toUpperCase(); this.className = ''; this.children = []; this.parentElement = null;
      this.dataset = {}; this.attributes = new Map(); this.hidden = false; this.inert = false;
      this.style = { setProperty() {} }; this._text = '';
    }
    get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
    set textContent(value) { this._text = String(value); this.children = []; }
    append(...nodes) { for (const node of nodes) this.insertBefore(node, null); }
    insertBefore(node, before) {
      node.remove(); const index = before ? this.children.indexOf(before) : -1;
      if (index < 0) this.children.push(node); else this.children.splice(index, 0, node);
      node.parentElement = this; return node;
    }
    remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this); this.parentElement = null; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    removeAttribute(key) { this.attributes.delete(key); }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(node => [node, ...node.querySelectorAll('*')]);
      return descendants.filter(node => selector === '*' || (selector.startsWith('.') && node.className.split(' ').includes(selector.slice(1))));
    }
    getBoundingClientRect() { return { height: 42 }; }
    click() { this.onclick?.(); this.dispatchEvent(new Event('click')); }
  }
  const document = new EventTarget();
  Object.assign(document, { body: new Element('body'), hidden: false, readyState: 'complete', modal: false, legacyModal: null,
    createElement: tag => new Element(tag), querySelector: () => document.modal ? {} : null,
    querySelectorAll: () => document.legacyModal ? [document.legacyModal] : [] });
  const window = new EventTarget();
  Object.assign(window, { location: { search: '' }, matchMedia: () => ({ matches: false }), getComputedStyle: node => node.style });
  const observers = [], timers = new Map();
  let timerId = 0;
  class MutationObserver { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} disconnect() {} }
  class ResizeObserver { observe() {} disconnect() {} }
  return { window, document, MutationObserver, ResizeObserver, observers, timers,
    setTimeout(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; }, clearTimeout(id) { timers.delete(id); } };
}

test('static renderer exposes API immediately, preserves task status on close, and blocks modal-hidden actions', async () => {
  const harness = domHarness();
  const keys = ['window', 'document', 'MutationObserver', 'ResizeObserver', 'setTimeout', 'clearTimeout'];
  const previous = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
  for (const key of keys) globalThis[key] = harness[key];
  let feedback;
  try {
    let ready = 0; harness.window.addEventListener('funds:feedback-ready', () => ready++);
    ({ feedback } = await import('../static/feedback-ui.mjs?synthetic-renderer'));
    assert.equal(ready, 1); assert.equal(harness.window.FundsFeedback, feedback);
    const root = harness.document.body.querySelector('.funds-feedback');
    assert.ok(root); assert.equal(root.querySelector('.ff-dock').dataset.expanded, 'false');
    feedback.task({ id: 'pending', status: 'unknown', title: '核对导入结果' });
    feedback.task({ id: 'running', status: 'running', title: '正在分析' });
    feedback.openTasks();
    const row = root.querySelector('.ff-task');
    assert.equal(row.dataset.status, 'running');
    assert.equal(row.querySelector('.ff-progress').hidden, false);
    assert.equal(row.querySelector('.ff-progress').getAttribute('value'), null);
    feedback.dismiss('running');
    const pendingRow = root.querySelector('.ff-task');
    assert.equal(root.querySelector('.ff-dock-entry').getAttribute('aria-expanded'), 'true');
    assert.equal(pendingRow.querySelector('.ff-progress').hidden, true);
    pendingRow.querySelector('.ff-close').click();
    assert.equal(root.querySelectorAll('.ff-task').length, 0);
    assert.equal(feedback.snapshot().tasks.find(item => item.id === 'pending').status, 'unknown');
    assert.match(root.querySelector('.ff-summary').textContent, /1 项待核对/);
    feedback.openTasks(); assert.equal(root.querySelectorAll('.ff-task').length, 2);
    feedback.task({ id: 'running', status: 'warning', title: '分析已完成，显示需留意' });
    assert.match(root.querySelector('.ff-summary').textContent, /1 项待核对 · 1 项需留意/);
    feedback.notify({ severity: 'error', title: '真正错误' });
    assert.match(root.querySelectorAll('.ff-sr')[1].textContent, /真正错误/);
    assert.equal(root.querySelectorAll('.ff-sr')[0].textContent, '');
    const stack = root.querySelector('.ff-toast-stack');
    harness.document.activeElement = stack.querySelector('.ff-close');
    stack.dispatchEvent(new Event('focusin'));
    assert.equal(harness.timers.size, 0);
    harness.document.activeElement.click();
    feedback.notify({ severity: 'info', title: '关闭焦点提示后的新通知' });
    assert.equal(harness.timers.size, 1);
    harness.document.modal = true; harness.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, true); assert.equal(root.getAttribute('aria-hidden'), 'true');
    assert.equal(root.querySelector('.ff-toast-stack').hidden, true);
    harness.document.modal = false; harness.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, false); assert.equal(root.querySelector('.ff-toast-stack').hidden, false);
    harness.document.legacyModal = { isConnected: true, hidden: false, style: { display: 'flex', visibility: 'visible' } };
    harness.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, true); assert.equal(root.querySelector('.ff-toast-stack').hidden, true);
    harness.document.legacyModal.style.display = 'none'; harness.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, false);
    root.inert = true; harness.observers.forEach(observer => observer.fn()); assert.equal(root.inert, false);
  } finally {
    feedback?.dispose();
    for (const key of keys) { if (previous[key] === undefined) delete globalThis[key]; else globalThis[key] = previous[key]; }
  }
});

test('embedded analysis exposes feedback state without adding a duplicate task dock', async () => {
  const harness = domHarness(); harness.window.location.search = '?embedded=1';
  const keys = ['window', 'document', 'MutationObserver', 'ResizeObserver', 'setTimeout', 'clearTimeout'];
  const previous = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
  for (const key of keys) globalThis[key] = harness[key];
  let feedback;
  try {
    ({ feedback } = await import('../static/feedback-ui.mjs?synthetic-embedded'));
    assert.equal(harness.window.FundsFeedback, feedback);
    assert.equal(harness.document.body.children.length, 0);
  } finally {
    feedback?.dispose();
    for (const key of keys) { if (previous[key] === undefined) delete globalThis[key]; else globalThis[key] = previous[key]; }
  }
});

test('external iframe modal flags combine with local dialogs and can block before the renderer mounts', async () => {
  const harness = domHarness();
  const keys = ['window', 'document', 'MutationObserver', 'ResizeObserver', 'setTimeout', 'clearTimeout'];
  const previous = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
  for (const key of keys) globalThis[key] = harness[key];
  let feedback;
  try {
    harness.window.addEventListener('funds:feedback-ready', event => event.detail.setModalBlocked(true));
    ({ feedback } = await import('../static/feedback-ui.mjs?synthetic-external-modal'));
    const root = harness.document.body.querySelector('.funds-feedback');
    const stack = root.querySelector('.ff-toast-stack');
    assert.equal(root.inert, true); assert.equal(root.getAttribute('aria-hidden'), 'true'); assert.equal(stack.hidden, true);
    feedback.notify({ severity: 'info', title: '等待外部对话框关闭' });
    assert.equal(harness.timers.size, 0);
    feedback.setModalBlocked(false);
    assert.equal(root.inert, false); assert.equal(stack.hidden, false); assert.equal(harness.timers.size, 1);
    harness.document.modal = true; harness.observers.forEach(observer => observer.fn());
    feedback.setModalBlocked(true);
    harness.document.modal = false; harness.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, true); assert.equal(stack.hidden, true); assert.equal(harness.timers.size, 0);
    feedback.setModalBlocked(false);
    assert.equal(root.inert, false); assert.equal(harness.timers.size, 1);
    harness.document.legacyModal = { isConnected: true, hidden: false, style: { display: 'flex', visibility: 'visible' } };
    feedback.setModalBlocked(true); feedback.setModalBlocked(false);
    assert.equal(root.inert, true); assert.equal(harness.timers.size, 0);
    harness.document.legacyModal.hidden = true; harness.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, false); assert.equal(stack.hidden, false); assert.equal(harness.timers.size, 1);
  } finally {
    feedback?.dispose();
    for (const key of keys) { if (previous[key] === undefined) delete globalThis[key]; else globalThis[key] = previous[key]; }
  }
});
