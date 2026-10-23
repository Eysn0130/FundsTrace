import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const viewSource = readFileSync(new URL('../static/main-accounts-view.js', import.meta.url), 'utf8');
const controllerSource = readFileSync(new URL('../static/main-accounts.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const flush = async () => { for (let pass = 0; pass < 30; pass++) await Promise.resolve(); };
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const candidate = (account = '00001', name = '合成同名', extra = {}) => ({ account, name, is_main: false, source: null, ...extra });

// The real view and case-bound controller run together. The DOM keeps moved or
// replaced node identity, modal visibility and focus; requests use synthetic rows.
function harness() {
  const ids = new Map(), timers = new Map(), rootEvents = new Map(), requests = [], previews = [], captures = [];
  let doc, timerId = 0, route = null, candidates = [candidate()], total = null, previewError = false, cameraIntent = 0;
  let pickerOptions, selected = null, invalidations = 0;
  const binding = { caseId: 11, generation: 1, canvasId: 3, canvasGeneration: 1 };
  let records = [];
  class Element {
    constructor(tag = 'div', id = '') {
      this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.parentElement = null;
      this.attributes = new Map(); this.listeners = new Map(); this.style = {}; this.dataset = {}; this.hidden = false; this.inert = false;
      this.disabled = false; this.open = false; this.value = ''; this.textContent = ''; this.title = '';
      this.classList = { contains: name => this.className.split(/\s+/).includes(name),
        add: name => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), name])].join(' '); },
        remove: name => { this.className = this.className.split(/\s+/).filter(value => value !== name).join(' '); },
        toggle: (name, on = !this.classList.contains(name)) => { this.classList[on ? 'add' : 'remove'](name); return on; } };
      if (id) ids.set(id, this);
    }
    get className() { return this.getAttribute('class') || ''; }
    set className(value) { this.setAttribute('class', value); }
    get isConnected() { return this === doc?.body || !!this.parentElement?.isConnected; }
    append(...nodes) { for (const node of nodes) { node.remove(); node.parentElement = this; this.children.push(node); } }
    appendChild(node) { this.append(node); return node; }
    remove() {
      if (this.contains(doc?.activeElement)) doc.activeElement = doc.body;
      if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      this.parentElement = null;
    }
    replaceChildren(...nodes) { for (const node of [...this.children]) node.remove(); this.append(...nodes); }
    contains(node) { return !!node && (node === this || this.children.some(child => child.contains(node))); }
    setAttribute(key, value) {
      this.attributes.set(key, String(value));
      if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    matches(selector) {
      return selector.split(',').some(part => {
        const text = part.trim(), tag = text.match(/^[\w-]+/)?.[0], id = text.match(/#([\w-]+)/)?.[1];
        if (text === '*') return true;
        if (tag && this.tagName !== tag.toUpperCase() || id && this.id !== id) return false;
        if ([...text.matchAll(/\.([\w-]+)/g)].some(match => !this.classList.contains(match[1]))) return false;
        for (const match of text.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)) {
          if (!this.attributes.has(match[1]) || match[2] !== undefined && this.getAttribute(match[1]) !== match[2]) return false;
        }
        return true;
      });
    }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    querySelectorAll(selector) { return this.children.flatMap(child => [child, ...child.querySelectorAll('*')]).filter(node => node.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    getClientRects() {
      for (let node = this; node; node = node.parentElement) if (node.hidden || node.tagName === 'DIALOG' && !node.open) return [];
      return this.isConnected ? [this.getBoundingClientRect()] : [];
    }
    getBoundingClientRect() { return { left: 400, top: 20, width: 140, height: 30, right: 540, bottom: 50 }; }
    focus() {
      if (!this.disabled && this.isConnected && this.getClientRects().length && doc.activeElement !== this) {
        doc.activeElement = this; this.fire('focus', { bubbles: false });
      }
    }
    addEventListener(type, callback) { const list = this.listeners.get(type) || []; list.push(callback); this.listeners.set(type, list); }
    fire(type, options = {}) {
      const event = { type, target: this, key: '', isComposing: false, defaultPrevented: false, stopped: false, immediateStopped: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; },
        stopImmediatePropagation() { this.stopped = true; this.immediateStopped = true; }, ...options };
      for (let node = this; node; node = node.parentElement) {
        node['on' + type]?.(event);
        for (const callback of node.listeners.get(type) || []) { callback(event); if (event.immediateStopped) break; }
        if (event.stopped || event.bubbles === false) break;
      }
      return event;
    }
    key(key, options = {}) {
      const event = this.fire('keydown', { key, ...options });
      if (key === 'Escape' && !event.defaultPrevented && !event.isComposing && event.keyCode !== 229) {
        // Native search controls consume Escape to clear their value before a
        // dialog cancel event. Exercise that default after the keydown path.
        if (this.getAttribute('type') === 'search' && this.value) { this.value = ''; this.fire('input'); }
        else {
          const dialog = this.closest('dialog');
          if (dialog?.open && !dialog.fire('cancel').defaultPrevented) dialog.close();
        }
      }
      return event;
    }
    click() {
      if (this.disabled) return;
      this.focus(); const event = this.fire('click');
      if (!event.defaultPrevented && this.tagName === 'BUTTON' && this.getAttribute('type') === 'submit') this.closest('form')?.fire('submit');
      return event;
    }
    showModal() { this.open = true; }
    close() { this.open = false; this.fire('close'); }
  }
  doc = { body: new Element('body'), activeElement: null, createElement: tag => new Element(tag),
    createTextNode: text => Object.assign(new Element('#text'), { textContent: text }), getElementById: id => ids.get(id) };
  doc.activeElement = doc.body;
  const trigger = doc.body.appendChild(new Element('button', 'btnMainActions'));
  const stack = [doc.body], markup = html.match(/<dialog id="mainAccountsDialog"[\s\S]*?<\/dialog>/)?.[0];
  assert.ok(markup, 'the harness uses the current production dialog structure');
  for (const match of markup.matchAll(/<(\/?)([a-z][\w-]*)([^>]*)>/gi)) {
    const [, close, tag, attributes] = match;
    if (close) { stack.pop(); continue; }
    const id = /\bid="([^"]+)"/.exec(attributes)?.[1] || '', node = new Element(tag, id);
    for (const attr of attributes.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      node.setAttribute(attr[1], attr[2] ?? '');
      if (attr[1] === 'hidden' || attr[1] === 'disabled' || attr[1] === 'inert') node[attr[1]] = true;
    }
    stack.at(-1).append(node);
    if (!['input', 'img', 'br', 'hr'].includes(tag.toLowerCase())) stack.push(node);
  }
  const root = { addEventListener(type, callback) { const list = rootEvents.get(type) || []; list.push(callback); rootEvents.set(type, list); },
    FundsToolbarMenu: { place() {}, close() {} }, FundsRibbon: { closePopovers() {} },
    FundsEntitySearch: { mount(input, options) {
      pickerOptions = options;
      const open = () => { input.setAttribute('aria-expanded', String(!!input.value.trim() && options.scope() !== null)); };
      input.addEventListener('focus', open);
      input.addEventListener('click', open);
      input.addEventListener('input', open);
      input.addEventListener('keydown', event => {
        if (event.isComposing || event.keyCode === 229 || event.key !== 'Escape' || input.getAttribute('aria-expanded') !== 'true') return;
        event.preventDefault(); event.stopImmediatePropagation(); input.setAttribute('aria-expanded', 'false');
      });
      return { getSelected: () => selected, invalidate() { invalidations++; selected = null; input.setAttribute('aria-expanded', 'false'); }, close() { input.setAttribute('aria-expanded', 'false'); } };
    } } };
  root.FundsGraphLocator = {
    capture() { const token = { ...binding, cameraIntent }; captures.push(token); return token; },
    availability(account) { return account === '00003' ? { visible: false, tracked: true, reason: '已上图，但不在当前视图' }
      : account === '00004' ? { visible: false, tracked: false, reason: '该账户尚未上图' } : { visible: true, tracked: true, reason: '' }; },
    async previewAccount(account, options) {
      if (previewError) throw new Error('Synthetic preview failure');
      const token = options.binding;
      if (Object.keys(token).some(key => token[key] !== (key === 'cameraIntent' ? cameraIntent : binding[key]))) return false;
      previews.push({ account, options: plain(options) }); return true;
    },
  };
  const context = vm.createContext({ window: root, setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; }, clearTimeout: id => timers.delete(id) });
  vm.runInContext(viewSource, context); vm.runInContext(controllerSource, context);
  const isCurrent = (value, canvas = true) => value && value.caseId === binding.caseId && value.generation === binding.generation
    && (!canvas || value.canvasId === binding.canvasId && value.canvasGeneration === binding.canvasGeneration);
  const view = root.FundsMainAccountsView.create(doc, { isCurrent, readCaseName: () => '合成案件' });
  const controller = root.FundsMainAccounts.create({ context: () => ({ ...binding }), isCurrent,
    request: async (path, options = {}) => {
      requests.push({ path, options });
      if (route) { const result = route(path, options); if (result !== undefined) return result; }
      if (path.includes('/candidates?')) {
        const parameters = new URL(path, 'http://synthetic.local').searchParams;
        return { items: candidates.map(row => ({ ...row })), total: total ?? candidates.length, page: Number(parameters.get('page')), page_size: 30 };
      }
      if (!options.method) return records.map(row => ({ ...row }));
      const body = JSON.parse(options.body); records = [...records, { ...body, source: 'manual' }];
      return { ok: true, ...body, source: 'manual' };
    }, show: view.show, dismiss: view.dismiss, onState: view.onState,
  });
  view.attach(controller);
  return { root, view, controller, binding, requests, previews, captures, doc,
    el: id => ids.get(id), candidates(values, count = null) { candidates = values; total = count; }, route(value) { route = value; },
    previewFailure(value = true) { previewError = value; }, pan() { cameraIntent++; },
    async searchScreen() { controller.open(trigger); await flush(); controller.showSearch(); await flush(); },
    async timers() { const tasks = [...timers.values()]; timers.clear(); for (const callback of tasks) callback(); await flush(); },
    input(text, event = 'input') { ids.get('maQuery').value = text; ids.get('maQuery').fire(event); },
    async submit() { ids.get('maSearchForm').fire('submit'); await flush(); },
    picker: { options: () => pickerOptions, invalidations: () => invalidations,
      select(value) { selected = value; ids.get('maQuery').value = value.value; pickerOptions.onSelect(value); },
      submit() { pickerOptions.onSubmit(ids.get('maQuery').value, selected); },
    },
    locate(account) { return ids.get('maCandidates').children.find(row => row.children[0].children[1].textContent === account)?.children[1].children[0]; },
    add(account) { return ids.get('maCandidates').children.find(row => row.children[0].children[1].textContent === account)?.children[1].children[1]; },
    switchCase(value = 12) { binding.caseId = value; binding.generation++; controller.revoke(); },
    switchCanvas(value = 4) { binding.canvasId = value; binding.canvasGeneration++; },
  };
}

test('explicit main-account search reads candidates without moving the canvas or changing a mark', async () => {
  const h = harness(); await h.searchScreen(); h.input('00001'); await h.submit();
  assert.equal(h.previews.length, 0); assert.equal(h.captures.length, 0); assert.equal(h.requests.some(row => row.options.method), false);
  assert.equal(new URL(h.requests.at(-1).path, 'http://synthetic.local').searchParams.get('q'), '00001');
  assert.equal(h.controller.status().screen, 'search'); assert.equal(h.el('mainAccountsDialog').open, true);
  assert.equal(h.doc.activeElement, h.el('maQuery'));
});

test('typing uses only candidate suggestions; explicit paging, retry and controller search never preview the camera', async () => {
  const h = harness(); h.candidates([candidate()], 31); await h.searchScreen();
  const reads = h.requests.length; h.input('合成'); await h.timers(); assert.equal(h.requests.length, reads);
  h.el('maNext').click(); await flush(); h.el('maPrev').click(); await flush(); h.el('maRetry').click(); await flush();
  await h.controller.search('00001', 1, true);
  assert.equal(h.previews.length, 0); assert.equal(h.captures.length, 0);
});

test('shared candidates fill an exact account without querying the registry until explicit submit', async () => {
  const h = harness(); h.candidates([candidate('00001'), candidate('00002')]); await h.searchScreen();
  const configuration = h.picker.options(), reads = h.requests.length;
  assert.equal(configuration.source, 'flows'); assert.equal(configuration.kind, 'account'); assert.equal(configuration.caseId(), 11);
  assert.match(configuration.scope(), /"generation":1/);
  h.picker.select({ key: '00002', value: '00002', account: '00002', label: '合成同名' }); await flush();
  assert.equal(h.el('maQuery').value, '00002'); assert.equal(h.requests.length, reads);
  assert.equal(h.previews.length, 0); assert.equal(h.requests.some(row => row.options.method), false);
  h.picker.submit(); await flush();
  const parameters = new URL(h.requests.at(-1).path, 'http://synthetic.local').searchParams;
  assert.equal(parameters.get('q'), '00002'); assert.equal(parameters.get('exact'), 'true');
  const button = h.el('maSearchForm').querySelector('button');
  assert.equal(button.children[0].src, '/static/ui-icons/search.svg'); assert.equal(button.getAttribute('aria-label'), '查询本案件账号或户名');
  assert.equal(h.el('maSearchForm').children.filter(node => node.tagName === 'IMG').length, 0);
});

test('an exact account remains exact on repeated submit and executed pagination, while input editing clears its binding', async () => {
  const h = harness(); h.candidates([candidate('00001'), candidate('100001')], 31); await h.searchScreen();
  h.picker.select({ key: '00001', value: '00001', account: '00001', label: '合成同名' });
  await h.submit(); await h.submit();
  let searches = h.requests.filter(row => row.path.includes('/candidates?')).slice(-2);
  assert.equal(searches.length, 2);
  for (const request of searches) {
    const parameters = new URL(request.path, 'http://synthetic.local').searchParams;
    assert.equal(parameters.get('q'), '00001'); assert.equal(parameters.get('exact'), 'true');
  }
  h.el('maNext').click(); await flush();
  let parameters = new URL(h.requests.at(-1).path, 'http://synthetic.local').searchParams;
  assert.equal(parameters.get('page'), '2'); assert.equal(parameters.get('exact'), 'true');
  h.input('00001'); await h.submit();
  parameters = new URL(h.requests.at(-1).path, 'http://synthetic.local').searchParams;
  assert.equal(parameters.get('q'), '00001'); assert.equal(parameters.get('exact'), null);
  h.picker.select({ key: '00001', value: '00001', account: '00001', label: '合成同名' });
  h.el('maBack').click(); h.controller.showSearch(); await flush(); await h.submit();
  parameters = new URL(h.requests.at(-1).path, 'http://synthetic.local').searchParams;
  assert.equal(parameters.get('exact'), null);
  assert.equal(h.previews.length, 0); assert.equal(h.requests.some(row => row.options.method), false);
});

test('registry work revokes old candidates but permits a new draft; screen changes and rebinding revoke its scope', async () => {
  const h = harness(); await h.searchScreen(); const start = h.picker.invalidations();
  const pending = deferred(); h.route(path => path.includes('/candidates?') ? pending.promise : undefined);
  h.input('00001'); h.el('maSearchForm').fire('submit');
  assert.equal(h.picker.options().scope(), null); assert.ok(h.picker.invalidations() > start);
  const reads = h.requests.length; h.input('新的检索');
  assert.notEqual(h.picker.options().scope(), null); assert.equal(h.requests.length, reads);
  pending.resolve({ items: [candidate()], total: 1, page: 1, page_size: 30 }); await flush();
  assert.notEqual(h.picker.options().scope(), null);
  h.el('maBack').click(); assert.equal(h.picker.options().scope(), null);
  h.controller.showSearch(); await flush(); const before = h.picker.invalidations(); h.switchCase();
  assert.equal(h.picker.options().scope(), null); assert.ok(h.picker.invalidations() > before); assert.equal(h.el('mainAccountsDialog').open, false);
});

test('SVG submit keeps suggestions closed through disabled-button focus fallback but new drafts remain editable', async () => {
  const h = harness(); await h.searchScreen(); const pending = deferred();
  h.route(path => path.includes('/candidates?') ? pending.promise : undefined);
  h.input('合成'); assert.equal(h.el('maQuery').getAttribute('aria-expanded'), 'true');
  const button = h.el('maSearchForm').querySelector('button'); button.click();
  assert.equal(button.disabled, true); assert.equal(h.doc.activeElement, h.el('maQuery'));
  assert.equal(h.el('maQuery').disabled, false);
  assert.equal(h.el('maQuery').getAttribute('aria-expanded'), 'false');
  h.input('新的检索'); assert.equal(h.el('maQuery').getAttribute('aria-expanded'), 'true');
  h.input('合成'); assert.equal(h.el('maQuery').getAttribute('aria-expanded'), 'false');
  pending.resolve({ items: [candidate()], total: 1, page: 1, page_size: 30 }); await flush();
  assert.equal(h.el('maQuery').getAttribute('aria-expanded'), 'false');
  h.el('maQuery').click(); assert.equal(h.el('maQuery').getAttribute('aria-expanded'), 'true');
  assert.equal(h.controller.status().query, '合成');
});

test('real Escape keydown dismisses suggestions first, then the dialog without native search clearing', async () => {
  const h = harness(); await h.searchScreen(); h.input('合成');
  const first = h.el('maQuery').key('Escape'); assert.equal(first.defaultPrevented, true);
  assert.equal(h.el('maQuery').getAttribute('aria-expanded'), 'false'); assert.equal(h.el('mainAccountsDialog').open, true);
  assert.equal(h.el('maQuery').value, '合成');
  const second = h.el('maQuery').key('Escape'); assert.equal(second.defaultPrevented, true);
  assert.equal(h.el('mainAccountsDialog').open, false); assert.equal(h.el('maQuery').value, '合成');
});

test('Escape preserves IME and cannot close a sent main-account write', async () => {
  const h = harness(); await h.searchScreen(); h.input('合成');
  h.el('maQuery').fire('compositionstart');
  h.el('maQuery').key('Escape', { isComposing: true, keyCode: 229 });
  assert.equal(h.el('mainAccountsDialog').open, true); assert.equal(h.el('maQuery').value, '合成');
  h.el('maQuery').fire('compositionend');
  const pending = deferred(); h.route((path, options) => options.method ? pending.promise : undefined);
  h.add('00001').click(); await flush();
  assert.equal(h.controller.status().pending, true); assert.equal(h.controller.status().sent, true);
  const escape = h.el('mainAccountsDialog').key('Escape');
  assert.equal(escape.defaultPrevented, true); assert.equal(h.el('mainAccountsDialog').open, true);
  pending.resolve({ ok: true, account: '00001', source: 'manual' }); await flush();
  h.el('mainAccountsDialog').key('Escape'); assert.equal(h.el('mainAccountsDialog').open, false);
});

test('Escape closes the row action menu before the parent dialog and restores its trigger', async () => {
  const h = harness();
  h.route(path => path.startsWith('/api/main-accounts?') ? [candidate('00001', '合成同名', { source: 'auto' })] : undefined);
  await h.searchScreen(); h.el('maBack').click();
  const trigger = h.el('maRows').children[0].children[2].children[0]; trigger.click();
  assert.equal(h.el('maRowMenu').hidden, false); assert.equal(h.doc.activeElement, h.el('maConvert'));
  const first = h.el('maConvert').key('Escape');
  assert.equal(first.defaultPrevented, true); assert.equal(h.el('maRowMenu').hidden, true);
  assert.equal(h.doc.activeElement, trigger); assert.equal(h.el('mainAccountsDialog').open, true);
  trigger.key('Escape'); assert.equal(h.el('mainAccountsDialog').open, false);
});

test('explicit empty, multi-result and malformed searches never pick an arbitrary first candidate', async () => {
  const h = harness(); await h.searchScreen(); h.input('   '); await h.submit();
  assert.equal(h.previews.length, 0); assert.equal(h.captures.length, 0);
  h.candidates([candidate('00001'), candidate('00002')]); h.input('合成同名'); await h.submit();
  assert.equal(h.previews.length, 0);
  h.candidates([candidate()], 30); h.input('000'); await h.submit(); assert.equal(h.previews.length, 0);
  h.route(path => path.includes('/candidates?') ? Promise.resolve({ items: null }) : undefined);
  h.input('broken'); await h.submit(); assert.equal(h.previews.length, 0); assert.ok(h.controller.status().error);
});

test('IME composition and keyCode 229 preserve input without running an explicit preview', async () => {
  const h = harness(); await h.searchScreen(); const count = h.requests.length;
  h.el('maQuery').fire('compositionstart'); h.input('合成'); await h.submit(); await h.timers();
  assert.equal(h.requests.length, count); assert.equal(h.previews.length, 0);
  assert.equal(h.el('maQuery').fire('keydown', { key: 'Enter', isComposing: true, keyCode: 229 }).defaultPrevented, true);
  h.el('maQuery').fire('compositionend'); await h.timers(); assert.equal(h.previews.length, 0);
});

test('changed input, newer search and closing revoke a delayed explicit preview', async () => {
  for (const action of ['input', 'submit', 'close']) {
    const h = harness(); await h.searchScreen(); const pending = deferred(), newer = deferred(); let queries = 0;
    h.route(path => path.includes('/candidates?') ? (++queries === 1 ? pending : newer).promise : undefined);
    h.input('00001'); h.el('maSearchForm').fire('submit');
    if (action === 'input') { h.input('00002'); h.input('00001'); }
    if (action === 'submit') { h.input('00002'); h.el('maSearchForm').fire('submit'); }
    if (action === 'close') h.el('maClose').click();
    if (action === 'submit') { newer.resolve({ items: [candidate('00002')], total: 1, page: 1, page_size: 30 }); await flush(); }
    pending.resolve({ items: [candidate()], total: 1, page: 1, page_size: 30 }); await flush();
    assert.equal(h.previews.length, 0);
  }
});

test('case A-B-A and canvas changes or a user pan cannot move a newer camera from an old search', async () => {
  for (const action of [h => { h.switchCase(12); h.switchCase(11); }, h => h.switchCanvas(), h => h.pan()]) {
    const h = harness(); await h.searchScreen(); const pending = deferred();
    h.route(path => path.includes('/candidates?') ? pending.promise : undefined);
    h.input('00001'); h.el('maSearchForm').fire('submit'); action(h);
    pending.resolve({ items: [candidate()], total: 1, page: 1, page_size: 30 }); await flush();
    assert.equal(h.previews.length, 0);
  }
});

test('candidate locate uses SVG and a fresh token while hidden and untracked candidates stay disabled', async () => {
  const h = harness(); h.candidates([candidate('00001'), candidate('00003'), candidate('00004')]); await h.searchScreen();
  const locate = h.locate('00001');
  assert.equal(locate.disabled, false); assert.equal(locate.children[0].src, '/static/ui-icons/target.svg');
  assert.match(locate.getAttribute('aria-label'), /定位.*00001/);
  assert.equal(h.locate('00003').disabled, true); assert.equal(h.locate('00003').title, '已上图，但不在当前视图');
  assert.equal(h.locate('00004').disabled, true); assert.equal(h.locate('00004').title, '该账户尚未上图');
  h.pan(); locate.click(); await flush();
  assert.equal(h.previews.length, 1); assert.equal(h.previews[0].options.binding.cameraIntent, 1);
  assert.equal(h.controller.status().query, ''); assert.equal(h.controller.status().screen, 'search');
  assert.equal(h.requests.some(row => row.options.method), false);
});

test('candidate location never adds a mark and the separate add action preserves its original business command', async () => {
  const h = harness(); await h.searchScreen(); h.locate('00001').click(); await flush();
  assert.equal(h.requests.some(row => row.options.method), false);
  h.add('00001').click(); await flush();
  const writes = h.requests.filter(row => row.options.method);
  assert.equal(writes.length, 1); assert.equal(JSON.parse(writes[0].options.body).account, '00001');
  assert.equal(h.previews.length, 1);
});

test('detached candidate controls and unavailable or failed locator adapters cannot change search facts', async () => {
  const h = harness(); await h.searchScreen(); const stale = h.locate('00001');
  h.input('00001'); await h.submit(); const count = h.previews.length;
  stale.onclick(); await flush(); assert.equal(h.previews.length, count);
  h.previewFailure(); h.input('00001'); await h.submit(); h.locate('00001').click(); await flush();
  assert.equal(h.controller.status().error, ''); assert.equal(h.controller.status().candidates.length, 1);
  h.root.FundsGraphLocator.capture = () => { throw new Error('Synthetic capture failure'); };
  h.input('00001'); await h.submit(); assert.equal(h.controller.status().error, '');
  h.root.FundsGraphLocator = undefined; h.input('00001'); await h.submit();
  assert.equal(h.locate('00001').disabled, true); assert.equal(h.controller.status().error, '');
  assert.equal(h.requests.some(row => row.options.method), false);
});
