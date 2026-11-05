import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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
  let document;
  const observers = [], pendingMutations = new Set();
  const changed = (node, attribute) => {
    for (const observer of observers) if (observer.target?.contains(node) && observer.options.attributes
      && (!observer.options.attributeFilter || observer.options.attributeFilter.includes(attribute))) pendingMutations.add(observer);
  };
  class Element extends EventTarget {
    constructor(tag) {
      super(); this.tagName = tag.toUpperCase(); this.className = ''; this.children = []; this.parentElement = null;
      this.dataset = {}; this.attributes = new Map(); this.hidden = false; this.inert = false;
      this.style = { setProperty() {} }; this._text = '';
    }
    get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
    get hidden() { return this._hidden ?? false; }
    set hidden(value) { this._hidden = Boolean(value); changed(this, 'hidden'); }
    get inert() { return this._inert ?? false; }
    set inert(value) { this._inert = Boolean(value); changed(this, 'inert'); }
    get isConnected() { return this === document.body || !!this.parentElement?.isConnected; }
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
    closest(selector) { return selector === '[inert]' ? this.inert ? this : this.parentElement?.closest(selector) || null : null; }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(node => [node, ...node.querySelectorAll('*')]);
      return descendants.filter(node => selector === '*' || (selector.startsWith('.') ? node.className.split(' ').includes(selector.slice(1)) : node.tagName === selector.toUpperCase()));
    }
    getBoundingClientRect() { return { height: 42 }; }
    click() { this.onclick?.(); this.dispatchEvent(new Event('click')); }
    focus() { if (this.isConnected && !this.disabled && !this.closest('[inert]')) document.activeElement = this; }
  }
  document = new EventTarget();
  Object.assign(document, { body: new Element('body'), hidden: false, readyState: 'complete', modal: false, legacyModal: null,
    createElement: tag => new Element(tag), querySelector: () => document.modal ? {} : null,
    querySelectorAll: () => document.legacyModal ? [document.legacyModal] : [] });
  document.activeElement = document.body;
  const window = new EventTarget();
  Object.assign(window, { location: { search: '' }, matchMedia: () => ({ matches: false }), getComputedStyle: node => node.style });
  const timers = new Map();
  let timerId = 0;
  class MutationObserver {
    constructor(fn) { this.fn = fn; observers.push(this); }
    observe(target, options) { this.target = target; this.options = options; }
    disconnect() { this.target = null; pendingMutations.delete(this); }
  }
  return { window, document, MutationObserver, observers, timers,
    flushMutations(limit = 20) {
      let batches = 0;
      while (pendingMutations.size) {
        assert.ok(++batches <= limit, 'feedback MutationObserver must settle without repeated same-value attribute writes');
        const next = [...pendingMutations]; pendingMutations.clear();
        for (const observer of next) if (observer.target) observer.fn();
      }
      return batches;
    },
    setTimeout(fn, delay) { timers.set(++timerId, { fn, delay }); return timerId; }, clearTimeout(id) { timers.delete(id); } };
}

async function withRenderer(name, run, prepare = () => {}) {
  const harness = domHarness();
  const keys = ['window', 'document', 'MutationObserver', 'setTimeout', 'clearTimeout'];
  const previous = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
  for (const key of keys) globalThis[key] = harness[key];
  let feedback;
  try {
    prepare(harness);
    ({ feedback } = await import(`../static/feedback-ui.mjs?${name}`));
    await run(harness, feedback);
  } finally {
    feedback?.dispose();
    for (const key of keys) { if (previous[key] === undefined) delete globalThis[key]; else globalThis[key] = previous[key]; }
  }
}

function assertToastOnly(document) {
  for (const name of ['ff-entry', 'ff-entry-badge', 'ff-panel', 'ff-task', 'ff-task-list', 'ff-dock']) {
    assert.equal(document.body.querySelectorAll('.' + name).length, 0, `no global ${name} control`);
  }
  assert.equal(document.body.querySelector('footer'), null);
}

test('static renderer is Toast-only, never adds layout controls, and keeps tasks silent unless explicitly announced', async () => {
  const css = readFileSync(new URL('../static/feedback.css', import.meta.url), 'utf8');
  const rootRule = css.match(/\.funds-feedback\.ff-root\s*\{([^}]+)\}/)?.[1] || '';
  assert.match(rootRule, /position:\s*fixed/); assert.doesNotMatch(rootRule, /flex|height/);
  assert.doesNotMatch(css, /ff-dock|ff-entry|ff-panel|ff-task|ff-summary|ff-progress|feedback-dock-height|feedback-bottom/);
  await withRenderer('synthetic-toast-only', async (h, feedback) => {
    const root = h.document.body.querySelector('.ff-root'), stack = root.querySelector('.ff-toast-stack');
    assert.equal(h.window.FundsFeedback, feedback); assert.equal(typeof feedback.openTasks, 'undefined');
    assert.equal(typeof feedback.openNotifications, 'undefined');
    assertToastOnly(h.document); assert.equal(root.querySelectorAll('button').length, 0);
    feedback.task({ id: 'unknown', status: 'unknown', title: '原操作待核对' });
    feedback.task({ id: 'running', status: 'running', title: '原操作进行中', progress: { done: 1, total: 2 } });
    feedback.task({ id: 'historical', status: 'error', title: '历史错误' });
    assert.equal(feedback.snapshot().tasks.length, 3); assert.equal(feedback.snapshot().toasts.length, 0);
    assert.equal(root.querySelectorAll('button').length, 0); assertToastOnly(h.document);
    feedback.task({ id: 'running', status: 'success', title: '显式后台完成', announce: true });
    assert.equal(stack.querySelectorAll('.ff-toast').length, 1);
    feedback.task({ id: 'running', status: 'success', title: '显式后台完成', announce: true });
    assert.equal(stack.querySelectorAll('.ff-toast').length, 1);
    stack.querySelector('.ff-close').click();
    assert.equal(feedback.snapshot().tasks.find(item => item.id === 'running').status, 'success');
    assert.equal(feedback.snapshot().tasks.find(item => item.id === 'unknown').status, 'unknown');
    feedback.notify({ severity: 'error', title: '真正错误' });
    assert.match(root.querySelectorAll('.ff-sr')[1].textContent, /真正错误/);
    assert.doesNotMatch(root.querySelectorAll('.ff-sr')[0].textContent, /真正错误/);
    h.document.activeElement = stack.querySelector('.ff-close'); stack.dispatchEvent(new Event('focusin'));
    assert.equal(h.timers.size, 0); h.document.activeElement.click();
    feedback.notify({ severity: 'info', title: '关闭焦点提示后的新通知' });
    assert.equal(h.timers.size, 1);
    h.document.hidden = true; h.document.dispatchEvent(new Event('visibilitychange')); assert.equal(h.timers.size, 0);
    h.document.hidden = false; h.document.dispatchEvent(new Event('visibilitychange')); assert.equal(h.timers.size, 1);
    assertToastOnly(h.document);
  });
});

test('Toast actions recheck scope after A-B-A and cannot execute while a local or external modal blocks feedback', async () => {
  await withRenderer('synthetic-toast-actions', async (h, feedback) => {
    const root = h.document.body.querySelector('.ff-root'), stack = root.querySelector('.ff-toast-stack');
    let calls = 0;
    feedback.bind({ caseId: 'A', generation: 1 });
    feedback.notify({ id: 'scoped', severity: 'info', title: '原案件提示', actions: [{ id: 'open', label: '查看', run: () => calls++ }] });
    const old = stack.querySelector('.ff-action'); old.click(); assert.equal(calls, 1);
    feedback.bind({ caseId: 'B', generation: 2 });
    feedback.bind({ caseId: 'A', generation: 3 });
    old.onclick(); assert.equal(calls, 1); assert.equal(stack.querySelectorAll('.ff-toast').length, 0);
    feedback.notify({ id: 'fresh', severity: 'info', title: '当前案件提示', actions: [{ id: 'open', label: '查看', run: () => calls++ }] });
    const action = stack.querySelector('.ff-action');
    feedback.setModalBlocked(true); action.onclick(); assert.equal(calls, 1);
    assert.equal(root.inert, true); assert.equal(root.getAttribute('aria-hidden'), 'true'); assert.equal(stack.hidden, true);
    feedback.setModalBlocked(false); action.click(); assert.equal(calls, 2);
    h.document.modal = true; h.observers.forEach(observer => observer.fn()); action.onclick();
    assert.equal(calls, 2); assert.equal(root.inert, true); assert.equal(stack.hidden, true);
    h.document.modal = false; h.observers.forEach(observer => observer.fn()); assert.equal(root.inert, false);
    h.document.legacyModal = { isConnected: true, hidden: false, style: { display: 'flex', visibility: 'visible' } };
    h.observers.forEach(observer => observer.fn()); action.onclick(); assert.equal(calls, 2); assert.equal(stack.hidden, true);
    h.document.legacyModal.style.display = 'none'; h.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, false); assert.equal(stack.hidden, false); action.click(); assert.equal(calls, 3);
    assertToastOnly(h.document);
  });
});

test('embedded analysis retains feedback state without mounting duplicate global UI', async () => {
  await withRenderer('synthetic-toast-embedded', async (h, feedback) => {
    feedback.task({ id: 'internal', status: 'unknown', title: '原功能核对' });
    assert.equal(h.window.FundsFeedback, feedback); assert.equal(feedback.snapshot().tasks.length, 1);
    assert.equal(h.document.body.children.length, 0); assertToastOnly(h.document);
  }, h => { h.window.location.search = '?embedded=1'; });
});

test('external modal state can pause before mounting and combines with native and legacy modal state', async () => {
  await withRenderer('synthetic-toast-external-modal', async (h, feedback) => {
    const root = h.document.body.querySelector('.ff-root'), stack = root.querySelector('.ff-toast-stack');
    assert.equal(root.inert, true); assert.equal(stack.hidden, true);
    feedback.notify({ severity: 'info', title: '等待对话框关闭' }); assert.equal(h.timers.size, 0);
    feedback.setModalBlocked(false); assert.equal(root.inert, false); assert.equal(stack.hidden, false); assert.equal(h.timers.size, 1);
    h.document.modal = true; h.observers.forEach(observer => observer.fn()); feedback.setModalBlocked(true);
    h.document.modal = false; h.observers.forEach(observer => observer.fn());
    assert.equal(root.inert, true); assert.equal(stack.hidden, true); assert.equal(h.timers.size, 0);
    feedback.setModalBlocked(false); assert.equal(root.inert, false); assert.equal(h.timers.size, 1);
    h.document.legacyModal = { isConnected: true, hidden: false, style: { display: 'flex', visibility: 'visible' } };
    feedback.setModalBlocked(true); feedback.setModalBlocked(false); assert.equal(root.inert, true);
    h.document.legacyModal.hidden = true; h.observers.forEach(observer => observer.fn()); assert.equal(root.inert, false);
    assertToastOnly(h.document);
  }, h => { h.window.addEventListener('funds:feedback-ready', event => event.detail.setModalBlocked(true)); });
});

test('Toast modal MutationObserver converges after repeated same-value visibility changes', async () => {
  await withRenderer('synthetic-toast-mutation-convergence', async (h, feedback) => {
    assert.ok(h.flushMutations() <= 2); assert.equal(h.flushMutations(), 0);
    feedback.notify({ severity: 'warning', title: '需留意', detail: '保持原功能处理。' });
    assert.ok(h.flushMutations() <= 2); assert.equal(h.flushMutations(), 0);
    feedback.setModalBlocked(true); assert.ok(h.flushMutations() <= 2); assert.equal(h.flushMutations(), 0);
    feedback.setModalBlocked(true); assert.equal(h.flushMutations(), 0);
    feedback.setModalBlocked(false); assert.ok(h.flushMutations() <= 2); assert.equal(h.flushMutations(), 0);
    feedback.setModalBlocked(false); assert.equal(h.flushMutations(), 0);
    h.document.modal = true; h.observers.forEach(observer => observer.fn()); assert.ok(h.flushMutations() <= 2);
    h.document.modal = false; h.observers.forEach(observer => observer.fn()); assert.ok(h.flushMutations() <= 2);
    h.document.body.querySelector('.ff-close').click();
    assert.ok(h.flushMutations() <= 2); assert.equal(h.flushMutations(), 0); assertToastOnly(h.document);
  });
});
