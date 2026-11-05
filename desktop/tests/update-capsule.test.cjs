'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  class Element {
    constructor() { this.children = []; this.attributes = {}; this.listeners = {}; this.isConnected = true;
      this.style = { setProperty() {} }; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    click() { if (!this.disabled) this.listeners.click?.(); }
    get dataset() { return this.data ||= {}; }
    set textContent(value) { this.text = value; this.children = []; }
    get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  }
  const definitions = new Map(), calls = [], feedback = [], listeners = new Map();
  let notify;
  const current = { phase: 'current', currentVersion: '0.1.9', version: '' };
  const available = { phase: 'available', currentVersion: '0.1.9', version: '0.1.10' };
  const bridge = { state: async () => current, subscribe(callback) { notify = callback; return () => {}; },
    check: async () => { calls.push('check'); return available; },
    download: async () => { calls.push('download'); return { ...available, phase: 'ready' }; },
    install: async () => { calls.push('install'); return { ...available, phase: 'installing' }; } };
  const window = { FundsDesktopUpdates: bridge, FundsFeedback: { task: event => feedback.push(event) },
    addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener: name => listeners.delete(name),
    customElements: { get: name => definitions.get(name), define: (name, value) => definitions.set(name, value) } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/desktop-updates.js'), 'utf8'), {
    window,
    HTMLElement: Element, document: { createElement: () => new Element() },
  });
  const view = new (definitions.get('funds-desktop-update'))();
  view.connectedCallback();
  const button = view.children[0], status = view.children[1];
  return { view, button, status, calls, bridge, available, feedback, window, listeners, emit: value => notify(value) };
}

test('only available updates occupy the capsule, retaining download and restart actions', async () => {
  const { view, button, status, calls, bridge, available, emit } = fixture();
  await flush();
  for (const phase of ['idle', 'checking', 'current', 'unavailable', 'error']) {
    emit({ phase, currentVersion: '0.1.9', version: '' });
    assert.equal(view.hidden, true, `${phase} must not add an update prompt`);
    assert.equal(view.textContent, '');
  }
  emit(available);
  assert.equal(view.hidden, false); assert.equal(button.textContent, '更新');
  let finish;
  bridge.download = () => { calls.push('download'); emit({ ...available, phase: 'downloading', percent: 25 });
    return new Promise(resolve => { finish = resolve; }); };
  button.click(); await flush();
  assert.equal(view.hidden, false); assert.equal(button.textContent, '25%'); assert.equal(button.disabled, true);
  finish({ ...available, phase: 'ready' }); await flush();
  assert.equal(button.textContent, '重启更新'); assert.equal(button.disabled, false);
  button.click(); await flush();
  assert.equal(view.hidden, false); assert.equal(button.textContent, '正在重启');
  assert.deepEqual(calls, ['download', 'install']);
  emit({ phase: 'current', currentVersion: '0.1.10', version: '' });
  assert.equal(view.hidden, true); assert.equal(view.textContent, ''); assert.equal(status.textContent, '');
});

test('a discovered update retains its retry entry after a rejected bridge action', async () => {
  const { view, button, bridge, available, calls, emit } = fixture();
  await flush(); emit(available);
  bridge.download = async () => { throw new Error('synthetic bridge rejection'); };
  button.click(); await flush();
  assert.equal(view.hidden, false); assert.equal(button.textContent, '重试更新');
  button.click(); await flush();
  assert.equal(view.hidden, false); assert.equal(button.textContent, '更新');
  assert.deepEqual(calls, ['check']);
});

test('the application task mirrors real update states and never reports installation completion', async () => {
  const h = fixture(); await flush();
  h.emit({ phase: 'checking', currentVersion: '0.1.9', version: '' });
  assert.equal(h.feedback.at(-1).status, 'running'); assert.equal('progress' in h.feedback.at(-1), false);
  h.emit({ ...h.available, phase: 'downloading', percent: 25.5 });
  const downloading = h.feedback.at(-1);
  assert.equal(downloading.scope, null); assert.equal(downloading.progress.done, 25.5); assert.equal(downloading.progress.total, 100);
  h.emit({ ...h.available, phase: 'ready', percent: 100 });
  const ready = h.feedback.at(-1);
  assert.equal(ready.id, downloading.id); assert.equal(ready.status, 'warning');
  assert.match(ready.detail, /重启后继续安装/); assert.equal(ready.announce, false);
  await ready.actions[0].run();
  assert.deepEqual(h.calls, ['install']); assert.equal(h.feedback.at(-1).status, 'running');
  assert.match(h.feedback.at(-1).detail, /下次启动后确认/);
  assert.equal(h.feedback.some(event => /安装完成/.test(event.title + event.detail)), false);
});

test('an error without an offered version remains in tasks and retries only through the existing bridge', async () => {
  const h = fixture(); await flush();
  h.emit({ phase: 'error', currentVersion: '0.1.9', version: '', errorCode: 'ERR_CONNECTION_RESET' });
  const error = h.feedback.at(-1);
  assert.equal(h.view.hidden, true); assert.equal(error.status, 'error'); assert.equal(error.scope, null);
  assert.equal(error.actions[0].id, 'check'); await error.actions[0].run();
  assert.deepEqual(h.calls, ['check']); assert.equal(h.feedback.at(-1).status, 'warning');
  const count = h.feedback.length; await error.actions[0].run();
  assert.equal(h.feedback.length, count, 'a stale task action cannot issue a new command in a different phase');
});

test('feedback readiness replays the latest update state without duplicate progress entries', async () => {
  const h = fixture(); delete h.window.FundsFeedback; await flush();
  h.emit({ ...h.available, phase: 'downloading', percent: 40 });
  assert.equal(h.feedback.length, 0);
  h.window.FundsFeedback = { task: event => h.feedback.push(event) };
  h.listeners.get('funds:feedback-ready')();
  assert.equal(h.feedback.length, 1); assert.equal(h.feedback[0].progress.done, 40);
  h.emit({ ...h.available, phase: 'downloading', percent: 40 });
  assert.equal(h.feedback.length, 1);
  h.view.disconnectedCallback(); assert.equal(h.listeners.has('funds:feedback-ready'), false);
});
