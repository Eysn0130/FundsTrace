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
    focus() { if (!this.disabled && this.isConnected && this.getClientRects().length) doc.activeElement = this; }
    addEventListener(type, callback) { const list = this.listeners.get(type) || []; list.push(callback); this.listeners.set(type, list); }
    fire(type, options = {}) {
      const event = { type, target: this, key: '', isComposing: false, defaultPrevented: false, stopped: false,
        preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...options };
      for (let node = this; node; node = node.parentElement) {
        node['on' + type]?.(event);
        for (const callback of node.listeners.get(type) || []) callback(event);
        if (event.stopped) break;
      }
      return event;
    }
    click() { if (!this.disabled) return this.fire('click'); }
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
    FundsToolbarMenu: { place() {}, close() {} }, FundsRibbon: { closePopovers() {} } };
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
    locate(account) { return ids.get('maCandidates').children.find(row => row.children[0].children[1].textContent === account)?.children[1].children[0]; },
    add(account) { return ids.get('maCandidates').children.find(row => row.children[0].children[1].textContent === account)?.children[1].children[1]; },
    switchCase(value = 12) { binding.caseId = value; binding.generation++; controller.revoke(); },
    switchCanvas(value = 4) { binding.canvasId = value; binding.canvasGeneration++; },
  };
}

test('explicit search previews one exact candidate without changing its leading-zero identity or mark', async () => {
  const h = harness(); await h.searchScreen(); h.input('00001'); await h.submit();
  assert.equal(h.previews.length, 1); assert.equal(h.previews[0].account, '00001');
  assert.equal(h.captures.length, 1); assert.equal(h.requests.some(row => row.options.method), false);
  assert.equal(h.controller.status().screen, 'search'); assert.equal(h.el('mainAccountsDialog').open, true);
  assert.equal(h.doc.activeElement, h.el('maQuery'));
});

test('opening, debounce, paging, retry and direct controller search never preview the camera', async () => {
  const h = harness(); h.candidates([candidate()], 31); await h.searchScreen();
  h.input('合成'); await h.timers();
  h.el('maNext').click(); await flush(); h.el('maPrev').click(); await flush(); h.el('maRetry').click(); await flush();
  await h.controller.search('00001', 1, true);
  assert.equal(h.previews.length, 0); assert.equal(h.captures.length, 0);
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
    if (action === 'submit') assert.deepEqual(h.previews.map(row => row.account), ['00002']);
    else assert.equal(h.previews.length, 0);
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
