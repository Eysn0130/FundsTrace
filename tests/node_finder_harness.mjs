import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/node-finder.js', import.meta.url), 'utf8');
const entitySearch = readFileSync(new URL('../static/entity-search.js', import.meta.url), 'utf8');
const flush = async () => { for (let count = 0; count < 20; count++) await Promise.resolve(); };
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

class Element {
  constructor(id, doc) {
    this.id = id; this.doc = doc; this.value = ''; this.hidden = false; this.disabled = false;
    this.attributes = {}; this.listeners = new Map(); this.children = []; this._text = ''; this.className = '';
    this.classList = {
      toggle: (name, active) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        if (active) classes.add(name); else classes.delete(name); this.className = [...classes].join(' ');
      },
      contains: name => this.className.split(/\s+/).includes(name),
      add: name => { this.classList.toggle(name, true); },
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, listener) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(listener); }
  replaceChildren(...children) { this._text = ''; this.children = children; for (const child of children) child.parentElement = this; }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  closest(selector) { if (selector === '.find-result' && this.classList.contains('find-result')) return this; return this.parentElement?.closest(selector) || null; }
  focus() { this.doc.activeElement = this; }
  scrollIntoView() { this.scrolled = true; }
  emit(name, properties = {}) {
    const event = { target: this, defaultPrevented: false, propagationStopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; }, ...properties };
    for (const listener of this.listeners.get(name) || []) listener(event);
    return event;
  }
}

function harness({ entityInputs = false } = {}) {
  const elements = new Map(), locates = [], selects = [], cancellations = [], clearedPreviews = [], closes = [], places = [];
  const doc = new Element('document'); doc.doc = doc;
  const el = id => { if (!elements.has(id)) elements.set(id, new Element(id, doc)); return elements.get(id); };
  doc.getElementById = el; doc.createElement = tag => new Element(tag, doc);
  doc.createTextNode = value => { const node = new Element('#text', doc); node.textContent = value; return node; };
  const root = { document: doc, console }; root.window = root;
  const context = vm.createContext(root); let pickerOptions, pickerInvalidations = 0;
  if (entityInputs) {
    root.FundsEntityPicker = { create(options) { pickerOptions = options; return { invalidate() { pickerInvalidations++; }, open() {} }; } };
    vm.runInContext(entitySearch, context, { filename: 'entity-search.js' });
  }
  vm.runInContext(source, context);
  let shown = false, blocked = '', onLocate, onSelect;
  const state = { binding: 'case:2/epoch:1/canvas:3/epoch:1', revision: 1, source: {}, ready: true,
    accounts: [{ account: '00123', name: '共同户名' }, { account: '00456', name: '共同户名' }, { account: '00999', name: '唯一户名' },
      { account: '000GRAPH-00001', name: '合成节点 1' }, { account: '000GRAPH-00002', name: '合成节点 2' }], recent: ['000GRAPH-00002', 'not-visible', '000GRAPH-00002'] };
  root.FundsToolbarMenu = {
    open() { if (shown) return true; shown = true; doc.emit('funds:toolbar-menu-change', { detail: { open: true, menuId: 'findNodePopover' } }); return true; },
    close(reason) { if (!shown) return false; shown = false; closes.push(reason); doc.emit('funds:toolbar-menu-change', { detail: { open: false, reason, menuId: 'findNodePopover' } }); return true; },
    place(panel, trigger) { places.push([panel.id, trigger.id]); },
  };
  const controller = root.FundsNodeFinder.create({ document: doc, read: () => ({ ...state }), blocker: () => blocked,
    formatCount: value => String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ','),
    locate(ids, snapshot) { locates.push({ ids: plain(ids), snapshot }); return onLocate ? onLocate(ids, snapshot) : { ok: true }; },
    select(ids, snapshot) { selects.push({ ids: plain(ids), snapshot }); return onSelect ? onSelect(ids, snapshot) : { ok: true }; },
    cancel() { cancellations.push(true); },
    clearPreview() { clearedPreviews.push(true); },
  });
  const input = value => { el('graphFindInput').value = value; el('graphFindInput').emit('input'); };
  return { controller, state, doc, el, input, locates, selects, cancellations, clearedPreviews, closes, places,
    picker: () => pickerOptions, pickerInvalidations: () => pickerInvalidations,
    open: () => controller.open(), setBlocked(value) { blocked = value; controller.update(); },
    onLocate(value) { onLocate = value; }, onSelect(value) { onSelect = value; },
  };
}

test('opening shows the authoritative scope and visible recent count without moving or selecting', () => {
  const h = harness(); h.open();
  assert.equal(h.controller.getState().open, true); assert.equal(h.locates.length, 0); assert.equal(h.selects.length, 0);
  assert.match(h.el('graphFindScope').textContent, /5 个账户/); assert.equal(h.el('graphFindNewCount').textContent, '1');
  assert.equal(h.el('btnGraphFindAll').disabled, true); assert.equal(h.el('btnGraphFindSelect').disabled, true);
  assert.equal(h.el('btnGraphFindNew').disabled, false); assert.ok(h.places.length);
});

test('typing keeps the canvas and selection unchanged', () => {
  const h = harness(); h.open(); h.input('00123');
  assert.equal(h.locates.length, 0); assert.equal(h.selects.length, 0); assert.equal(h.controller.getState().searched, false);
  assert.equal(h.el('btnGraphFindSearch').disabled, false); assert.equal(h.el('btnGraphFindAll').disabled, true);
});

test('replacing the authoritative accounts array refreshes its cache even with an unchanged stamp', async () => {
  const h = harness(); h.open();
  h.state.accounts = [{ account: '00090071992547409931234567890', name: '新合成主体' }]; h.controller.update();
  assert.match(h.el('graphFindScope').textContent, /1 个账户/);
  h.input('新合成主体'); assert.equal(await h.controller.search(), true);
  assert.deepEqual(h.locates[0].ids, ['00090071992547409931234567890']);
  assert.deepEqual(plain(h.controller.getState().results[0]), { account: '00090071992547409931234567890', name: '新合成主体' });
});

test('a new revision refreshes the cached fields and clears results without merging same-name identities', async () => {
  const h = harness(); h.open(); h.input('共同户名'); await h.controller.search();
  h.state.accounts[0] = { account: '00123', name: '共同户名', idNo: '00000020000101000X' };
  h.state.revision++; h.controller.update();
  assert.equal(h.controller.getState().resultTotal, 0); h.input('共同户名'); await h.controller.search();
  const result = plain(h.controller.getState().results);
  assert.deepEqual(result.map(item => item.account), ['00123', '00456']);
  assert.equal(result[0].idNo, '00000020000101000X'); assert.equal(Object.hasOwn(result[1], 'idNo'), false);
});

test('enhanced node finder uses only current graph members and rejects a stale provider read', async () => {
  const h = harness({ entityInputs: true }); assert.equal(h.picker().scope(), null);
  h.open(); h.input('共同户名'); assert.equal(h.locates.length, 0);
  const request = { query: '共同户名', offset: 0, limit: 30, signal: new AbortController().signal, scope: h.picker().scope() };
  const result = await h.picker().search(request);
  assert.deepEqual(plain(result.items.map(item => item.account)), ['00123', '00456']);
  assert.equal(h.locates.length, 0); assert.equal(h.selects.length, 0);
  h.picker().onSelect(result.items[1]); await flush(); assert.deepEqual(h.locates[0].ids, ['00456']);
  h.state.binding = 'case:2/epoch:3/canvas:3/epoch:3';
  assert.deepEqual(plain(await h.picker().search(request)), { items: [], hasMore: false });
  assert.equal(h.picker().scope(), null); assert.ok(h.pickerInvalidations() > 0);
});

test('exact account wins over substring/name matches and preserves leading zeros', async () => {
  const h = harness(); h.state.accounts.push({ account: '0000123', name: '00123' }); h.open(); h.input('00123');
  assert.equal(await h.controller.search(), true);
  assert.deepEqual(h.locates.map(call => call.ids), [['00123']]); assert.equal(h.selects.length, 0);
  assert.equal(h.el('graphFindInput').value, '00123'); assert.equal(h.controller.getState().activeAccount, '00123');
  assert.equal(h.locates[0].snapshot.binding, h.state.binding); assert.equal(h.locates[0].snapshot.source, h.state.source);
});

test('exact account matching is case insensitive and resolves the canonical account', async () => {
  const h = harness(); h.open(); h.input('000graph-00001'); await h.controller.search();
  assert.deepEqual(h.locates[0].ids, ['000GRAPH-00001']);
});

test('unique fuzzy account and name searches immediately locate without selecting', async () => {
  const h = harness(); h.open(); h.input('456'); await h.controller.search(); h.input('唯一户名'); await h.controller.search();
  assert.deepEqual(h.locates.map(call => call.ids), [['00456'], ['00999']]); assert.equal(h.selects.length, 0);
});

test('ambiguous matches list results and never pick an arbitrary account', async () => {
  const h = harness(); h.open(); h.input('共同户名'); assert.equal(await h.controller.search(), false);
  assert.equal(h.locates.length, 0); assert.equal(h.selects.length, 0);
  assert.equal(h.controller.getState().resultTotal, 2); assert.equal(h.el('graphFindResults').children.length, 2);
  assert.equal(h.el('btnGraphFindAll').disabled, false); assert.equal(h.el('btnGraphFindSelect').disabled, false);
});

test('result buttons preview the requested account, retain query and use the shared SVG', async () => {
  const h = harness(); h.open(); h.input('共同户名'); await h.controller.search();
  const button = h.el('graphFindResults').children[1]; button.emit('click'); await flush();
  assert.deepEqual(h.locates[0].ids, ['00456']); assert.equal(h.selects.length, 0); assert.equal(h.el('graphFindInput').value, '共同户名');
  assert.equal(button.attributes['aria-current'], 'true'); assert.equal(button.classList.contains('is-current'), true);
  assert.equal(button.children[1].src, '/static/ui-icons/target.svg');
  assert.equal(button.children[0].children[1].textContent, '00456');
});

test('only explicit select-results changes selection and a guard failure is shown', async () => {
  const h = harness(); h.open(); h.input('共同户名'); await h.controller.search();
  h.onSelect(() => ({ ok: false, reason: '请先保存账户资料' })); assert.equal(await h.controller.selectResults(), false);
  assert.deepEqual(h.selects[0].ids, ['00123', '00456']); assert.equal(h.locates.length, 0);
  assert.match(h.el('graphFindStatus').textContent, /请先保存账户资料/); assert.equal(h.controller.getState().resultTotal, 2);
  h.onSelect(() => ({ ok: true })); assert.equal(await h.controller.selectResults(), true);
  assert.match(h.el('graphFindStatus').textContent, /已选中 2/);
});

test('locate-all uses every match rather than just the currently visible result page', async () => {
  const h = harness(); h.state.accounts = Array.from({ length: 55 }, (_, index) => ({ account: `000${index}`, name: '共同结果' }));
  h.open(); h.input('共同结果'); await h.controller.search(); await h.controller.locateAll();
  assert.equal(h.locates[0].ids.length, 55); assert.equal(h.el('graphFindResults').children.length, 20);
  assert.equal(h.selects.length, 0); assert.equal(h.controller.getState().activeAccount, '');
});

test('results page by 20 and show-more preserves existing rows without camera motion', async () => {
  const h = harness(); h.state.accounts = Array.from({ length: 45 }, (_, index) => ({ account: `000${index}`, name: '共同结果' }));
  h.open(); h.input('共同结果'); await h.controller.search(); const first = h.el('graphFindResults').children[0];
  assert.equal(h.el('graphFindResults').children.length, 20); assert.match(h.el('btnGraphFindMore').textContent, /20 \/ 45/);
  h.el('btnGraphFindMore').emit('click'); assert.equal(h.el('graphFindResults').children.length, 40);
  assert.equal(h.el('graphFindResults').children[0], first);
  h.el('btnGraphFindMore').emit('click'); assert.equal(h.el('graphFindResults').children.length, 45);
  assert.equal(h.el('btnGraphFindMore').hidden, true); assert.equal(h.locates.length, 0);
});

test('long accounts, names and totals are complete text and never become HTML', async () => {
  const h = harness(), full = '000' + '1234567890'.repeat(10);
  h.state.accounts = Array.from({ length: 1001 }, (_, index) => ({ account: index === 0 ? full : `000-${index}`, name: '<img onerror=synthetic>共同' }));
  h.open(); h.input('共同'); await h.controller.search();
  const button = h.el('graphFindResults').children[0];
  assert.equal(button.children[0].children[1].textContent, full); assert.equal(button.children[0].children[0].textContent, '<img onerror=synthetic>共同');
  assert.equal(h.el('graphFindCount').textContent, '匹配结果 · 1,001 个'); assert.equal(h.el('graphFindCount').title, '匹配结果 · 1,001 个');
  assert.match(h.el('graphFindScope').textContent, /1,001 个账户/);
});

test('empty and no-match queries do not locate or change graph scope', async () => {
  const h = harness(); h.open(); h.input(''); assert.equal(await h.controller.search(), false);
  assert.match(h.el('graphFindStatus').textContent, /请输入/);
  h.input('不存在'); assert.equal(await h.controller.search(), false); assert.match(h.el('graphFindStatus').textContent, /没有匹配账户/);
  assert.equal(h.locates.length, 0); assert.equal(h.selects.length, 0); assert.equal(h.state.accounts.length, 5);
});

test('recent targets include only unique currently displayed members and disable at zero', async () => {
  const h = harness(); h.open(); assert.equal(await h.controller.locateNew(), true);
  assert.deepEqual(h.locates[0].ids, ['000GRAPH-00002']);
  h.state.recent = ['not-visible']; h.controller.update();
  assert.equal(h.el('graphFindNewCount').textContent, '0'); assert.equal(h.el('btnGraphFindNew').disabled, true);
  assert.equal(await h.controller.locateNew(), false); assert.equal(h.locates.length, 1);
});

test('readiness and business blockers disable operations while query remains editable', async () => {
  const h = harness(); h.open(); h.input('共同户名'); await h.controller.search(); h.setBlocked('画布正在更新');
  for (const id of ['btnGraphFindSearch', 'btnGraphFindAll', 'btnGraphFindSelect', 'btnGraphFindNew']) assert.equal(h.el(id).disabled, true);
  assert.equal(h.el('graphFindInput').disabled, false); assert.equal(await h.controller.locateAll(), false);
  h.input('00999'); assert.equal(h.el('graphFindInput').value, '00999'); assert.equal(h.locates.length, 0);
  h.setBlocked(''); h.state.ready = false; h.controller.update(); assert.equal(await h.controller.search(), false);
  assert.match(h.el('graphFindStatus').textContent, /尚未就绪/); assert.equal(h.el('graphFindInput').disabled, false);
});

test('duplicate locate and search are blocked until their current operation finishes', async () => {
  const h = harness(), wait = deferred(); h.onLocate(() => wait.promise); h.open(); h.input('00999');
  const first = h.controller.search(); assert.equal(await h.controller.search(), false);
  assert.equal(h.locates.length, 1); assert.equal(h.el('btnGraphFindSearch').disabled, true);
  assert.equal(h.el('graphFindInput').disabled, false); wait.resolve({ ok: true }); assert.equal(await first, true);
  assert.equal(h.el('btnGraphFindSearch').disabled, false);
});

test('pending and completion updates preserve result row DOM and focused element', async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('共同户名'); await h.controller.search();
  const button = h.el('graphFindResults').children[1]; button.focus(); h.onLocate(() => wait.promise);
  const action = h.controller.locate('00456');
  assert.equal(h.el('graphFindResults').children[1], button); assert.equal(h.doc.activeElement, button);
  assert.equal(button.disabled, false); assert.equal(button.attributes['aria-disabled'], 'true');
  wait.resolve({ ok: true }); await action;
  assert.equal(h.el('graphFindResults').children[1], button); assert.equal(h.doc.activeElement, button);
});

test('a new blocking operation cancels pending finder camera and rejects its completion', async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('00999'); h.onLocate(() => wait.promise); const action = h.controller.search();
  h.setBlocked('正在切换显示范围'); assert.equal(h.cancellations.length, 1);
  wait.resolve({ ok: true }); assert.equal(await action, false); assert.equal(h.controller.getState().activeAccount, '');
});

test('new input cancels only the finder-owned pending camera and rejects late success', async () => {
  const h = harness(), wait = deferred(); h.onLocate(() => wait.promise); h.open(); h.input('00999'); const action = h.controller.search();
  h.input('共同户名'); assert.equal(h.cancellations.length, 1); assert.equal(h.controller.getState().pending, false);
  wait.resolve({ ok: true }); assert.equal(await action, false);
  assert.equal(h.controller.getState().query, '共同户名'); assert.equal(h.controller.getState().activeAccount, '');
  assert.equal(h.el('graphFindStatus').textContent, '');
});

test('a second explicit search supersedes an old camera without accepting its late response', async () => {
  const h = harness(), old = deferred(); h.open(); h.input('00999'); h.onLocate(() => old.promise); const action = h.controller.search();
  h.input('00456'); h.onLocate(() => ({ ok: true })); assert.equal(await h.controller.search(), true);
  old.resolve({ ok: true }); assert.equal(await action, false); assert.equal(h.controller.getState().activeAccount, '00456');
  assert.equal(h.cancellations.length, 1);
});

test('programmatic search synchronizes its explicit query and supersedes the old camera', async () => {
  const h = harness(), old = deferred(); h.open(); h.onLocate(() => old.promise); const action = h.controller.search('00999');
  h.onLocate(() => ({ ok: true })); assert.equal(await h.controller.search('00456'), true);
  assert.equal(h.el('graphFindInput').value, '00456'); assert.equal(h.controller.getState().query, '00456');
  old.resolve({ ok: true }); assert.equal(await action, false); assert.equal(h.cancellations.length, 1);
  assert.equal(h.el('graphFindStatus').attributes['data-state'], 'done');
});

test('close and reopen reject old responses without cancelling unrelated camera work', async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('00999'); h.onLocate(() => wait.promise); const action = h.controller.search();
  h.controller.close(); assert.equal(h.cancellations.length, 1); assert.equal(h.controller.getState().open, false);
  h.open(); wait.resolve({ ok: true }); assert.equal(await action, false); assert.equal(h.controller.getState().activeAccount, '');
  h.controller.close(); assert.equal(h.cancellations.length, 1);
});

test('pending selection is not treated as owned camera work and duplicate selection is blocked', async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('共同户名'); await h.controller.search(); h.onSelect(() => wait.promise);
  const action = h.controller.selectResults(); assert.equal(await h.controller.selectResults(), false); assert.equal(h.selects.length, 1);
  h.input('00999'); assert.equal(h.cancellations.length, 0); wait.resolve({ ok: true }); assert.equal(await action, false);
});

for (const changed of ['revision', 'source', 'ready']) test(`changing graph ${changed} invalidates results and pending success`, async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('00999'); h.onLocate(() => wait.promise); const action = h.controller.search();
  h.state[changed] = changed === 'revision' ? h.state.revision + 1 : changed === 'source' ? {} : false; h.controller.update();
  assert.equal(h.controller.getState().resultTotal, 0); assert.equal(h.cancellations.length, 1);
  wait.resolve({ ok: true }); assert.equal(await action, false); assert.equal(h.controller.getState().activeAccount, '');
});

test('case/canvas binding switches close and reset query, including A-B-A epochs', async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('00999'); h.onLocate(() => wait.promise); const action = h.controller.search();
  h.state.binding = 'case:3/epoch:2/canvas:1/epoch:2'; h.controller.update();
  assert.equal(h.controller.getState().open, false); assert.equal(h.el('graphFindInput').value, ''); assert.deepEqual(h.closes, ['context']);
  h.state.binding = 'case:2/epoch:3/canvas:3/epoch:3'; h.controller.update(); h.open();
  wait.resolve({ ok: true }); assert.equal(await action, false); assert.equal(h.controller.getState().activeAccount, '');
});

test('a source change observed only by callback completion still rejects stale success', async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('00999'); h.onLocate(() => wait.promise); const action = h.controller.search();
  h.state.revision++; wait.resolve({ ok: true }); assert.equal(await action, false);
  assert.equal(h.controller.getState().resultTotal, 0); assert.equal(h.controller.getState().pending, false);
});

test('locate failures retain results, release busy state, and permit a successful retry', async () => {
  const h = harness(); h.open(); h.input('00999'); h.onLocate(() => ({ ok: false, reason: '节点当前不可见' }));
  assert.equal(await h.controller.search(), false); assert.match(h.el('graphFindStatus').textContent, /当前不可见/);
  assert.equal(h.controller.getState().resultTotal, 1); assert.equal(h.controller.getState().pending, false);
  h.onLocate(() => { throw new Error('合成相机错误'); }); assert.equal(await h.controller.locate('00999'), false);
  assert.match(h.el('graphFindStatus').textContent, /合成相机错误/);
  h.onLocate(() => ({ ok: true })); assert.equal(await h.controller.locate('00999'), true); assert.equal(h.controller.getState().error, '');
});

test('input Enter ignores composition and repeat but explicit Enter searches', async () => {
  const h = harness(); h.open(); h.input('00999');
  for (const props of [{ isComposing: true }, { keyCode: 229 }, { repeat: true }])
    h.el('findNodePopover').emit('keydown', { target: h.el('graphFindInput'), key: 'Enter', ...props });
  await flush(); assert.equal(h.locates.length, 0);
  const event = h.el('findNodePopover').emit('keydown', { target: h.el('graphFindInput'), key: 'Enter' });
  await flush(); assert.equal(h.locates.length, 1); assert.equal(event.defaultPrevented, true); assert.equal(event.propagationStopped, true);
});

test('result arrows/Home/End move focus without locating or changing selection', async () => {
  const h = harness(); h.open(); h.input('共同户名'); await h.controller.search();
  const list = h.el('graphFindResults'), [first, last] = list.children; first.focus();
  let event = list.emit('keydown', { target: first.children[0].children[0], key: 'ArrowDown' });
  assert.equal(h.doc.activeElement, last); assert.equal(event.defaultPrevented, true); assert.equal(event.propagationStopped, true);
  list.emit('keydown', { target: last, key: 'Home' }); assert.equal(h.doc.activeElement, first);
  list.emit('keydown', { target: first, key: 'End' }); assert.equal(h.doc.activeElement, last);
  list.emit('keydown', { target: last, key: 'ArrowUp' }); assert.equal(h.doc.activeElement, first);
  assert.equal(h.locates.length, 0); assert.equal(h.selects.length, 0);
  event = h.el('findNodePopover').emit('keydown', { target: first, key: 'Delete' }); assert.equal(event.propagationStopped, true);
});

test('clear removes results and cancels pending camera without clearing graph facts', async () => {
  const h = harness(), wait = deferred(); h.open(); h.input('00999'); h.onLocate(() => wait.promise); const action = h.controller.search();
  h.el('btnGraphFindClear').emit('click');
  assert.equal(h.el('graphFindInput').value, ''); assert.equal(h.controller.getState().resultTotal, 0);
  assert.equal(h.doc.activeElement, h.el('graphFindInput')); assert.equal(h.state.accounts.length, 5); assert.equal(h.cancellations.length, 1);
  wait.resolve({ ok: true }); assert.equal(await action, false);
});

test('input and clear remove completed finder previews without cancelling an unrelated camera', async () => {
  const h = harness(); h.open(); h.input('00999'); await h.controller.search();
  let previous = h.clearedPreviews.length; h.input('共同户名');
  assert.equal(h.clearedPreviews.length, previous + 1); assert.equal(h.controller.getState().activeAccount, '');
  assert.equal(h.cancellations.length, 0);
  await h.controller.search('00999'); previous = h.clearedPreviews.length;
  h.el('btnGraphFindClear').emit('click');
  assert.equal(h.clearedPreviews.length, previous + 1); assert.equal(h.controller.getState().activeAccount, '');
  assert.equal(h.cancellations.length, 0);
});

test('other menu events do not reset this controller and diagnostics are serializable', async () => {
  const h = harness(); h.open(); h.input('共同户名'); await h.controller.search();
  h.doc.emit('funds:toolbar-menu-change', { detail: { open: false, menuId: 'neighborPopover' } });
  assert.equal(h.controller.getState().open, true); assert.equal(h.controller.getState().resultTotal, 2);
  assert.doesNotThrow(() => JSON.stringify(h.controller.getState()));
});
