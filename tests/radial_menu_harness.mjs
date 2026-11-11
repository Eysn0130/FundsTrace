import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = readFileSync(new URL('../static/graph-radial-menu.js', import.meta.url), 'utf8');

function fixture({ reduced = false, width = 1200, height = 800, rect = { left: 56, top: 48, width: 1000, height: 700 } } = {}) {
  let document, now = 1000, nextId = 1, busy = false, selectionVisible = true;
  const frames = new Map(), timers = new Map(), commands = [], commandFocus = [], effects = { interrupt: 0, hover: 0, locate: 0, paint: 0 };
  class Events {
    constructor() { this.handlers = new Map(); this.parentNode = null; }
    addEventListener(type, callback, options = false) {
      const list = this.handlers.get(type) || [];
      list.push({ callback, capture: options === true || !!options?.capture, once: !!options?.once }); this.handlers.set(type, list);
    }
    get ownerDocument() { return document; }
    removeEventListener(type, callback, options = false) {
      const capture = options === true || !!options?.capture;
      const list = (this.handlers.get(type) || []).filter(item => item.callback !== callback || item.capture !== capture);
      if (list.length) this.handlers.set(type, list); else this.handlers.delete(type);
    }
    dispatchEvent(event) {
      if (!event.target) event.target = this;
      event.preventDefault ||= function () { this.defaultPrevented = true; };
      event.stopPropagation ||= function () { this.stopped = true; };
      event.stopImmediatePropagation ||= function () { this.stopped = true; this.immediate = true; };
      const path = []; for (let node = this; node; node = node.parentNode) path.push(node);
      const invoke = (node, capture) => {
        event.currentTarget = node;
        for (const item of [...(node.handlers.get(event.type) || [])]) {
          if (item.capture !== capture) continue;
          if (item.once) node.removeEventListener(event.type, item.callback, item.capture);
          item.callback(event); if (event.immediate) break;
        }
      };
      for (const node of [...path].reverse()) { invoke(node, true); if (event.stopped) return !event.defaultPrevented; }
      invoke(this, false);
      if (!event.stopped && event.bubbles) for (const node of path.slice(1)) { invoke(node, false); if (event.stopped) break; }
      return !event.defaultPrevented;
    }
    fire(type, values = {}) { const event = { type, bubbles: true, ...values }; this.dispatchEvent(event); return event; }
    listenerCount() { return [...this.handlers.values()].reduce((sum, list) => sum + list.length, 0); }
  }
  function matches(element, selector) {
    return selector.split(',').some(part => {
      part = part.trim(); if (!part) return false;
      const excluded = [...part.matchAll(/:not\(([^)]+)\)/g)].map(match => match[1]);
      if (excluded.some(value => matches(element, value))) return false;
      part = part.replace(/:not\([^)]+\)/g, '');
      const ancestors = part.split(/\s+(?![^[]*\])/); const last = ancestors.pop();
      const tag = last.match(/^[\w-]+/)?.[0];
      if (tag && element.tagName !== tag.toUpperCase()) return false;
      for (const [, id] of last.matchAll(/#([\w-]+)/g)) if (element.id !== id) return false;
      for (const [, name] of last.matchAll(/\.([\w-]+)/g)) if (!element.classList?.contains(name)) return false;
      for (const [, name, , value] of last.matchAll(/\[([^=\]]+)(?:=(["']?)(.*?)\2)?\]/g)) {
        if (!element.hasAttribute?.(name)) return false;
        if (value !== undefined && element.getAttribute(name) !== value) return false;
      }
      if (ancestors.length && !element.parentNode?.closest?.(ancestors.join(' '))) return false;
      return true;
    });
  }
  class Element extends Events {
    constructor(tag = 'div') {
      super(); this.tagName = tag.toUpperCase(); this.children = []; this.attrs = new Map(); this.dataset = {};
      this.className = ''; this._text = ''; this.hidden = false; this.disabled = false; this.tabIndex = -1;
      this.style = { setProperty(name, value) { this[name] = String(value); }, getPropertyValue(name) { return this[name] || ''; }, removeProperty(name) { delete this[name]; } };
      this.classList = {
        contains: name => this.className.split(/\s+/).includes(name),
        add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(' '); },
        remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(' '); },
        toggle: (name, force) => { const add = force ?? !this.classList.contains(name); this.classList[add ? 'add' : 'remove'](name); return add; },
      };
    }
    get parentElement() { return this.parentNode instanceof Element ? this.parentNode : null; }
    get isConnected() { for (let node = this; node; node = node.parentNode) if (node === document) return true; return false; }
    get id() { return this.getAttribute('id') || ''; } set id(value) { this.setAttribute('id', value); }
    set textContent(value) { this._text = String(value); } get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    get firstElementChild() { return this.children[0] || null; }
    get clientWidth() { return this === container ? rect.width : Number.parseFloat(this.style.width) || 0; }
    get clientHeight() { return this === container ? rect.height : Number.parseFloat(this.style.height) || 0; }
    setAttribute(name, value) {
      this.attrs.set(name, String(value)); if (name === 'class') this.className = String(value);
      if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    }
    getAttribute(name) { return name === 'class' ? this.className : this.attrs.get(name) ?? null; }
    hasAttribute(name) { return this.getAttribute(name) !== null; }
    removeAttribute(name) { this.attrs.delete(name); }
    appendChild(child) { child.remove(); child.parentNode = this; this.children.push(child); return child; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    replaceChildren(...children) { this.children.forEach(child => { child.parentNode = null; }); this.children = []; this.append(...children); }
    remove() { if (this.parentNode?.children) this.parentNode.children = this.parentNode.children.filter(child => child !== this); this.parentNode = null; }
    contains(target) { for (let node = target; node; node = node.parentNode) if (node === this) return true; return false; }
    closest(selector) { for (let node = this; node instanceof Element; node = node.parentNode) if (matches(node, selector)) return node; return null; }
    querySelectorAll(selector) { const result = []; const visit = node => { for (const child of node.children) { if (matches(child, selector)) result.push(child); visit(child); } }; visit(this); return result; }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    focus() {
      if (this.disabled || document.activeElement === this) return;
      const old = document.activeElement; document.activeElement = this;
      old?.fire('focusout', { relatedTarget: this }); this.fire('focus', { bubbles: false, relatedTarget: old }); this.fire('focusin', { relatedTarget: old });
    }
    click() { if (!this.disabled) this.fire('click', { button: 0, detail: 0 }); }
    getBoundingClientRect() {
      if (this === container) return { ...rect, right: rect.left + rect.width, bottom: rect.top + rect.height };
      let left = Number.parseFloat(this.style.left) || 0, top = Number.parseFloat(this.style.top) || 0;
      let w = Number.parseFloat(this.style.width) || 0, h = Number.parseFloat(this.style.height) || 0;
      const scale = Number(this.style.transform?.match(/^scale\(([-.\d]+)\)/)?.[1] || this.style.scale || 1);
      if (scale !== 1) { if (!this.style.transformOrigin?.includes('left')) left += w * (1 - scale) / 2; if (!this.style.transformOrigin?.includes('top')) top += h * (1 - scale) / 2; w *= scale; h *= scale; }
      return { left, top, width: w, height: h, right: left + w, bottom: top + h };
    }
    getClientRects() { return this.closest('[hidden]') || this.hidden || this.style.display === 'none' ? [] : [this.getBoundingClientRect()]; }
    setPointerCapture() {} releasePointerCapture() {}
  }
  const window = new Events(), media = new Events(); media.matches = reduced;
  document = new Element('document'); document.hidden = false; document.readyState = 'complete'; document.parentNode = window;
  document.documentElement = new Element('html'); document.documentElement.dataset.ftTheme = 'dark'; document.appendChild(document.documentElement);
  Object.defineProperties(document.documentElement, { clientWidth: { get: () => width }, clientHeight: { get: () => height } });
  document.body = new Element('body'); document.documentElement.appendChild(document.body);
  document.createElement = tag => new Element(tag); document.createElementNS = (_ns, tag) => new Element(tag);
  document.getElementById = id => document.querySelector('#' + id);
  const container = new Element('div'); container.id = 'graphContainer'; container.tabIndex = 0; document.body.appendChild(container);
  const outside = new Element('button'); outside.id = 'outside'; document.body.appendChild(outside);
  for (const id of ['btnUndo', 'btnCanvasSave', 'btnExportActions', 'btnRelayout', 'btnFit', 'btnMinimapToggle', 'legend', 'workspace', 'bulkX']) {
    const element = new Element('button'); element.id = id; element.value = '5'; document.body.appendChild(element);
  }
  document.getElementById('workspace').fundsWorkspaceShell = { getState: () => ({ view: 'standard', sidebarCollapsed: false }) };
  const context = { caseId: 'SYN-CASE-A', canvasId: 'SYN-CANVAS-A', generation: 1, canvasGeneration: 1 };
  const state = { caseId: context.caseId, graph: null, graphLoading: false, graphView: { revision: 1, edgeItems: new Map(), selectedEdge: null }, graphDisposers: [], lastGraph: { nodes: [], edges: [] }, presentation: { edgeLabels: {} } };
  const graph = new Events(); graph.on = graph.addEventListener.bind(graph); graph.off = graph.removeEventListener.bind(graph);
  graph.emit = (type, detail = {}) => { for (const item of [...(graph.handlers.get(type) || [])]) item.callback(detail); };
  const item = model => ({ getModel: () => model, getType: () => model.source ? 'edge' : 'node', destroyed: false });
  const a = item({ id: 'SYN-A', x: 450, y: 280, size: 32, data: { name: '合成甲', account: 'SYN-A' } });
  const b = item({ id: 'SYN-B', x: 650, y: 430, size: 32, data: { name: '合成乙', account: 'SYN-B' } });
  const edge = item({ id: 'SYN-A=>SYN-B', source: 'SYN-A', target: 'SYN-B', ids: ['9007199254740993'], data: { ids: ['9007199254740993'], source_name: '合成甲', target_name: '合成乙' } });
  graph.nodes = [a, b]; graph.edges = [edge]; graph.getNodes = () => graph.nodes; graph.getEdges = () => graph.edges;
  graph.findById = id => [...graph.nodes, ...graph.edges].find(value => value.getModel().id === id);
  graph.getCanvasByPoint = (x, y) => ({ x, y }); graph.getZoom = () => 1; graph.paint = () => { effects.paint++; };
  graph.get = name => name === 'group' ? { getMatrix: () => [1, 0, 0, 0, 1, 0, 0, 0, 1] } : undefined;
  state.graphView.edgeItems.set('SYN-A=>SYN-B', edge); state.graphView.nodeItems = new Map([['SYN-A', a], ['SYN-B', b]]);
  const selection = { ids: new Set(['SYN-A']), status: () => ({ dragging: false, visualsVisible: selectionVisible }),
    setVisualsVisible(value) { selectionVisible = value; return true; }, clear() { this.ids.clear(); },
    rebind(ids) { this.ids = new Set(ids); }, select(id) { this.ids = new Set([id]); } };
  state.graphSelection = selection;
  const record = (command, value) => {
    commandFocus.push(document.activeElement === container);
    commands.push({ command, ...(value === undefined ? {} : { value }), nodeIds: [...selection.ids], edgeKey: state.graphView.selectedEdge }); return true;
  };
  document.addEventListener('funds:ribbon-command', event => record(event.detail.command, event.detail.value));
  Object.assign(window, {
    document, innerWidth: width, innerHeight: height, performance: { now: () => now },
    matchMedia: () => media,
    getComputedStyle: element => ({ display: element.hidden ? 'none' : element.style.display || 'block', visibility: element.style.visibility || 'visible', getPropertyValue: name => name === '--accent' ? '#7f77dd' : element.style.getPropertyValue(name) }),
    setTimeout: (callback, delay = 0) => { const id = nextId++; timers.set(id, { callback, due: now + delay }); return id; },
    clearTimeout: id => timers.delete(id), requestAnimationFrame: callback => { const id = nextId++; frames.set(id, callback); return id; }, cancelAnimationFrame: id => frames.delete(id),
    FundsRibbonCommands: { execute: record }, FundsCanvasEditor: { history: { status: () => ({ busy }) }, traceAnchors() {} },
    CustomEvent: class { constructor(type, init = {}) { this.type = type; Object.assign(this, init); } },
  });
  media.addListener = callback => media.addEventListener('change', callback); media.removeListener = callback => media.removeEventListener('change', callback);
  const env = { window, document, state, layoutJob: null,
    Date: class extends Date { static now() { return now; } }, setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    caseContext: () => ({ ...context }), isCurrentCase: saved => Object.keys(context).every(key => saved?.[key] === context[key]),
    edgeKey: model => `${model.source}=>${model.target}`, annotationReady: () => true, neighborhoodBlocker: () => null,
    graphCameraInteractionAllowed: () => true,
    interruptGraphCamera: () => { effects.interrupt++; }, clearGraphHover: () => { effects.hover++; }, clearGraphLocateFeedback: () => { effects.locate++; },
    selectItem(value) { selection.ids.clear(); state.graphView.selectedEdge = null; if (value?.getModel().source) state.graphView.selectedEdge = `${value.getModel().source}=>${value.getModel().target}`; else if (value) selection.ids.add(value.getModel().id); },
    refreshSelectedEdgeAppearance() {},
  };
  vm.runInNewContext(source, env, { filename: 'graph-radial-menu.js' });
  state.graph = graph;
  const api = window.FundsGraphRadialMenu, dispose = api.attach({ graph, container }); container.focus();
  const tick = (ms = 16) => {
    now += ms;
    for (const [id, timer] of [...timers]) if (timer.due <= now) { timers.delete(id); timer.callback(); }
    const ready = [...frames]; frames.clear(); for (const [, callback] of ready) callback(now);
  };
  const settle = () => { let n = 0; while (frames.size || [...timers.values()].some(timer => timer.due <= now)) { assert.ok(n++ < 300, 'menu animation must settle'); tick(); } };
  const open = (kind = 'node', target = kind === 'edge' ? edge : kind === 'blank' ? null : a) => graph.emit('contextmenu', { item: target, clientX: rect.left + Math.min(450, rect.width / 2), clientY: rect.top + Math.min(280, rect.height / 2), preventDefault() {}, originalEvent: { preventDefault() {}, stopPropagation() {} } });
  const menu = () => document.querySelector('.funds-radial');
  const labels = () => menu()?.querySelectorAll('.funds-radial-item').filter(element => !element.hidden) || [];
  const key = (name, values = {}) => {
    const target = document.activeElement || container, event = target.fire('keydown', { key: name, ...values });
    // An unhandled Enter on a native button activates it. Product handlers that
    // already activate an item preventDefault, so this cannot double-dispatch.
    if (name === 'Enter' && !event.defaultPrevented && target.tagName === 'BUTTON' && target.isConnected) target.click();
    return event;
  };
  return { api, window, document, graph, state, context, selection, a, b, edge, container, outside, commands, commandFocus, effects, media,
    tick, settle, open, menu, labels, key, dispose, frames, timers, time: () => now, setBusy(value) { busy = value; },
    counts: () => ({ document: document.listenerCount(), window: window.listenerCount(), media: media.listenerCount(), frames: frames.size }),
  };
}

for (const [kind, names] of [['node', ['详情', '查看流水', '标注']], ['edge', ['详情', '来源', '去向']], ['blank', ['全选', '撤回', '保存', '导出']]]) {
  test(`${kind} exposes only the requested flat menu`, () => {
    const f = fixture(); f.open(kind); f.settle();
    assert.deepEqual(f.labels().map(element => element.textContent), names);
    assert.equal(f.api.getState().groupOpen, false); assert.equal(f.menu().querySelectorAll('[aria-haspopup="menu"]').length, 0);
  });
}

test('rounded sectors use convex corner arcs and only the inner main arc reverses direction', () => {
  const f = fixture(); f.open('blank');
  for (const path of f.menu().querySelectorAll('.funds-radial-wedge')) {
    const arcs = [...path.getAttribute('d').matchAll(/A\s+[-.\d]+\s+[-.\d]+\s+0\s+[01]\s+([01])/g)].map(match => Number(match[1]));
    assert.deepEqual(arcs, [1, 1, 1, 0, 1, 1], 'small corner arcs must follow the convex outer contour');
  }
});

test('mouse opening and cancellation preserve multiple selection without forcing a label focus', () => {
  const f = fixture(); f.selection.ids = new Set(['SYN-A', 'SYN-B']); f.open(); f.tick(0);
  assert.deepEqual([...f.selection.ids], ['SYN-A', 'SYN-B']);
  assert.ok(!f.labels().includes(f.document.activeElement));
  f.key('Escape'); f.settle(); assert.equal(f.api.isOpen(), false); assert.deepEqual([...f.selection.ids], ['SYN-A', 'SYN-B']);
});

test('keyboard focus lights the corresponding sector and uses the captured node for execution', () => {
  const f = fixture(); f.key('F10', { shiftKey: true }); f.tick(0); f.settle();
  assert.ok(f.document.activeElement === f.labels()[0], 'keyboard opening focuses the first action'); assert.equal(f.api.getState().active, 0);
  assert.ok(f.menu().querySelectorAll('.funds-radial-slice')[0].classList.contains('is-active'));
  assert.ok(f.effects.interrupt > 0 && f.effects.hover > 0 && f.effects.locate > 0);
  f.key('ArrowRight'); assert.ok(f.document.activeElement === f.labels()[1], 'the next action receives focus'); assert.equal(f.api.getState().active, 1);
  f.selection.ids = new Set(['SYN-B']); f.key('Enter');
  assert.deepEqual(f.commands, [{ command: 'viewFlows', nodeIds: ['SYN-A'], edgeKey: null }]);
  assert.deepEqual(f.commandFocus, [true], 'graph focus is restored before the command can open a native dialog');
  assert.equal(f.api.isOpen(), false);
});

test('the first ArrowDown from a mouse-opened root focuses the first item', () => {
  const f = fixture(); f.open('node'); f.settle();
  assert.ok(f.document.activeElement === f.menu(), 'mouse opening owns root focus without preselecting an action');
  f.key('ArrowDown');
  assert.ok(f.document.activeElement === f.labels()[0], 'the first ArrowDown selects the first action'); assert.equal(f.api.getState().active, 0);
});

test('hub focus Enter closes without executing a previously active item', () => {
  const f = fixture(); f.open('node'); f.settle();
  const box = f.menu().getBoundingClientRect(), action = f.labels()[1];
  f.menu().fire('pointermove', { clientX: box.left + Number.parseFloat(action.style.left) + Number.parseFloat(action.style.width) / 2,
    clientY: box.top + Number.parseFloat(action.style.top) + Number.parseFloat(action.style.height) / 2 });
  f.settle(); assert.equal(f.api.getState().active, 1);
  f.menu().querySelector('.funds-radial-hub').focus(); f.key('Enter'); f.settle();
  assert.equal(f.api.isOpen(), false); assert.equal(f.commands.length, 0);
  assert.deepEqual([...f.selection.ids], ['SYN-A']);
});

test('source and target actions retain their exact direction and captured relationship', () => {
  const f = fixture();
  for (const [label, value] of [['来源', 'source'], ['去向', 'target']]) {
    f.open('edge'); f.settle(); const action = f.labels().find(element => element.textContent === label);
    assert.ok(action, `the relationship menu must expose ${label}`); action.click();
    assert.deepEqual(f.commands.at(-1), { command: 'traceEdge', value, nodeIds: [], edgeKey: 'SYN-A=>SYN-B' });
    assert.equal(f.commandFocus.at(-1), true, 'the command owns graph focus at dispatch, before any dialog autofocus');
  }
});

test('a disabled action cannot dispatch a command', () => {
  const f = fixture(); f.document.getElementById('btnUndo').disabled = true; f.open('blank'); f.settle();
  const undo = f.labels().find(element => element.textContent === '撤回'); assert.equal(undo.disabled, true); undo.click();
  const box = f.menu().getBoundingClientRect();
  const index = f.labels().indexOf(undo); assert.ok(f.menu().querySelectorAll('.funds-radial-slice')[index].classList.contains('is-disabled'));
  f.menu().fire('click', { clientX: box.left + Number.parseFloat(undo.style.left) + Number.parseFloat(undo.style.width) / 2,
    clientY: box.top + Number.parseFloat(undo.style.top) + Number.parseFloat(undo.style.height) / 2 });
  assert.equal(f.commands.length, 0);
});

test('select-all is unavailable while the canvas history is busy', () => {
  const f = fixture(); f.setBusy(true); f.open('blank'); f.settle();
  if (!f.api.isOpen()) { assert.equal(f.commands.length, 0); return; }
  const all = f.labels().find(element => element.textContent === '全选');
  assert.equal(all.disabled, true); all.click(); assert.equal(f.commands.length, 0);
});

test('reopening during animated close leaves one menu and one set of listeners and frames', () => {
  const f = fixture(), baseline = f.counts(); f.open('blank'); f.settle();
  f.key('Escape'); assert.equal(f.api.isOpen(), true); f.open('blank');
  assert.equal(f.document.querySelectorAll('.funds-radial').length, 1); assert.ok(f.frames.size <= 1);
  f.settle(); f.api.close(); assert.equal(f.api.isOpen(), false); assert.equal(f.document.querySelectorAll('.funds-radial').length, 0);
  assert.deepEqual(f.counts(), baseline);
});

test('Enter during closing cannot queue an action', () => {
  const f = fixture(); f.key('ContextMenu'); f.tick(0); f.settle();
  f.key('Escape'); f.key('Enter'); f.settle(); assert.equal(f.commands.length, 0); assert.equal(f.api.isOpen(), false);
});

for (const [owner, event, detail] of [['window', 'funds:graph-metric', { phase: 'reset' }], ['window', 'funds:feedback-binding'],
  ['window', 'funds:selection-drag-start'], ['window', 'fundstrace:workspace-resize'], ['window', 'funds-appearance-change'], ['document', 'funds:toolbar-menu-change']]) {
  test(`${event} closes through its actual ${owner} event owner`, () => {
    const f = fixture(), baseline = f.counts(); f.open(); f.settle(); f[owner].fire(event, { detail, bubbles: false }); f.settle();
    assert.equal(f.api.isOpen(), false); assert.equal(f.document.querySelectorAll('.funds-radial').length, 0); assert.deepEqual(f.counts(), baseline);
  });
}

test('stale graph membership and A-B-A bindings cannot execute a retained menu action', () => {
  for (const invalidate of [f => { f.graph.nodes[0] = { ...f.a }; f.state.graphView.nodeItems.set('SYN-A', f.graph.nodes[0]); },
    f => { f.context.caseId = 'SYN-CASE-B'; f.context.generation++; f.context.caseId = 'SYN-CASE-A'; f.context.generation++; },
    f => { f.state.graphView = { ...f.state.graphView, revision: f.state.graphView.revision + 1 }; }]) {
    const f = fixture(); f.open(); f.settle(); const inspect = f.labels().find(element => element.textContent === '详情'); invalidate(f); inspect.click();
    assert.equal(f.commands.length, 0); assert.equal(f.api.isOpen(), false);
  }
});

test('a replaced edge instance cannot receive a command from its retired menu', () => {
  const f = fixture(); f.open('edge'); f.settle();
  const action = f.labels().find(element => element.textContent === '详情');
  const replacement = { ...f.edge }; f.graph.edges = [replacement]; f.state.graphView.edgeItems.set('SYN-A=>SYN-B', replacement);
  action.click(); assert.equal(f.commands.length, 0); assert.equal(f.api.isOpen(), false);
});

test('switching reduced motion while open immediately settles animation and releases RAF work', () => {
  const f = fixture(); f.open('blank'); f.tick(); assert.ok(f.frames.size > 0);
  f.media.matches = true; f.media.fire('change', { bubbles: false });
  assert.equal(f.frames.size, 0); assert.equal(f.api.isOpen(), true);
  assert.ok(f.menu().querySelectorAll('.funds-radial-item').every(element => Number(element.style.opacity) === 1));
  f.key('Escape'); assert.equal(f.api.isOpen(), false); assert.equal(f.frames.size, 0);
});

test('hover animation settles without painting the G6 graph', () => {
  const f = fixture(); f.open('blank'); f.settle(); const box = f.menu().getBoundingClientRect();
  f.menu().fire('pointermove', { clientX: box.left + box.width / 2, clientY: box.top + 35 });
  f.settle(); assert.equal(f.api.getState().active, 0); assert.equal(f.frames.size, 0); assert.equal(f.effects.paint, 0);
  f.menu().fire('pointerleave', { bubbles: false }); f.settle(); assert.equal(f.frames.size, 0); assert.equal(f.api.getState().active, -1);
});

test('the outer visible hover cap stays inside the clickable sector', () => {
  const f = fixture(); f.open('blank'); f.settle(); const box = f.menu().getBoundingClientRect();
  f.menu().fire('pointermove', { clientX: box.left + box.width / 2, clientY: box.top + 35 }); f.settle();
  const slice = f.menu().querySelectorAll('.funds-radial-slice')[0];
  const transform = slice.getAttribute('transform');
  const [, centerX, centerY] = /translate\(([-.\d]+) ([-.\d]+)\)/.exec(transform);
  const scale = Number(/scale\(([-.\d]+)\)/.exec(transform)[1]);
  const arc = /A\s+([-.\d]+)\s+/.exec(slice.querySelector('.funds-radial-wedge').getAttribute('d'));
  const radius = Number(arc[1]);
  f.menu().fire('click', { clientX: box.left + Number(centerX), clientY: box.top + Number(centerY) - radius * scale + 1 });
  assert.deepEqual(f.commands.map(command => [command.command, command.value]), [['canvasAction', 'selectAll']]);
});

for (const options of [{ width: 320, height: 600, rect: { left: 56, top: 48, width: 244, height: 520 } },
  { rect: { left: 1040, top: 48, width: 120, height: 700 } }]) {
  test(`menu fits the viewport when the graph slot is ${options.rect.width}px wide`, () => {
    const f = fixture(options); f.open('blank'); f.settle(); const box = f.menu().getBoundingClientRect();
    assert.ok(box.left >= 8 && box.top >= 8); assert.ok(box.right <= f.window.innerWidth - 8, JSON.stringify(box));
    assert.ok(box.bottom <= f.window.innerHeight - 8, JSON.stringify(box));
  });
}

test('outside pointer, resize, hidden document and disposal clear the menu and transient listeners', () => {
  for (const close of [f => { f.outside.fire('pointerdown', { button: 0, clientX: 15, clientY: 15 }); f.outside.fire('pointercancel'); },
    f => f.window.fire('resize', { bubbles: false }), f => { f.document.hidden = true; f.document.fire('visibilitychange', { bubbles: false }); }]) {
    const f = fixture(), baseline = f.counts(); f.open(); f.settle(); close(f); f.settle(); assert.equal(f.api.isOpen(), false); assert.deepEqual(f.counts(), baseline);
  }
  const f = fixture(); f.open(); f.settle(); f.dispose(); assert.equal(f.api.isOpen(), false); assert.equal(f.frames.size, 0);
  assert.equal(f.graph.listenerCount(), 0); assert.equal(f.container.listenerCount(), 0);
});

test('an outside dismissal consumes the same gesture before it can select the canvas', () => {
  const f = fixture(); f.selection.ids = new Set(['SYN-A', 'SYN-B']); f.open(); f.settle();
  const down = f.container.fire('pointerdown', { button: 0, clientX: 80, clientY: 80 });
  assert.equal(down.defaultPrevented, true); assert.equal(down.immediate, true);
  const tail = f.container.fire('click', { button: 0, detail: 1, clientX: 80, clientY: 80 });
  assert.equal(tail.defaultPrevented, true); f.settle();
  assert.equal(f.api.isOpen(), false); assert.deepEqual([...f.selection.ids], ['SYN-A', 'SYN-B']); assert.equal(f.commands.length, 0);
});

for (const reduced of [false, true]) test(`outside dismissal survives the close until its pointer tail (reduced: ${reduced})`, () => {
  const f = fixture({ reduced }), baseline = f.counts(), point = { button: 0, clientX: 80, clientY: 80, pointerId: 7 };
  f.selection.ids = new Set(['SYN-A', 'SYN-B']); let canvasClicks = 0;
  f.container.addEventListener('click', () => { canvasClicks++; f.selection.ids.clear(); });
  f.open(); f.settle(); const beforeClose = f.time();
  const down = f.container.fire('pointerdown', point); assert.equal(down.defaultPrevented, true);
  f.settle(); assert.equal(f.api.isOpen(), false); assert.equal(f.frames.size, 0);
  if (!reduced) assert.ok(f.time() - beforeClose >= 140, 'the tail arrives after the full menu fade');
  for (const type of ['pointerup', 'mouseup']) {
    const values = type === 'pointerup' ? point : { button: point.button, clientX: point.clientX, clientY: point.clientY };
    const tail = f.container.fire(type, values); assert.equal(tail.defaultPrevented, true, `${type} must not reach the underlying canvas`);
  }
  const click = f.container.fire('click', { ...point, detail: 1 });
  assert.equal(click.defaultPrevented, true); assert.equal(canvasClicks, 0);
  assert.deepEqual([...f.selection.ids], ['SYN-A', 'SYN-B']); assert.deepEqual(f.counts(), baseline);
});

test('a new legitimate pointerdown releases the old dismissal shield', () => {
  const f = fixture({ reduced: true }), baseline = f.counts(), point = { button: 0, clientX: 80, clientY: 80, pointerId: 7 };
  f.selection.ids = new Set(['SYN-A', 'SYN-B']); let canvasClicks = 0;
  f.container.addEventListener('click', () => { canvasClicks++; f.selection.ids.clear(); });
  f.open(); f.settle(); f.container.fire('pointerdown', point); f.settle();
  assert.equal(f.api.isOpen(), false); assert.deepEqual([...f.selection.ids], ['SYN-A', 'SYN-B']);
  // The first gesture lost its tail; an independent press at the same position
  // must still reach the canvas rather than being swallowed by a time heuristic.
  const nextDown = f.container.fire('pointerdown', { ...point, pointerId: 8 });
  assert.notEqual(nextDown.defaultPrevented, true);
  const nextClick = f.container.fire('click', { ...point, pointerId: 8, detail: 1 });
  assert.notEqual(nextClick.defaultPrevented, true); assert.equal(canvasClicks, 1); assert.equal(f.selection.ids.size, 0);
  assert.deepEqual(f.counts(), baseline);
});

test('a fresh outside press during Escape fade reaches its control and keeps its focus', () => {
  const f = fixture(), baseline = f.counts(); let delivered = 0;
  f.outside.addEventListener('pointerdown', () => { delivered++; f.outside.focus(); });
  f.open('node'); f.settle(); f.key('Escape');
  assert.equal(f.api.isOpen(), true, 'the old menu is still in its animated fade');
  assert.ok(f.menu().classList.contains('is-closing'));
  const down = f.outside.fire('pointerdown', { button: 0, pointerId: 9, clientX: 20, clientY: 20 });
  const focusedAtPress = f.document.activeElement === f.outside;
  f.settle();
  assert.deepEqual({ delivered, prevented: !!down.defaultPrevented, focusedAtPress, focusedAfterFade: f.document.activeElement === f.outside },
    { delivered: 1, prevented: false, focusedAtPress: true, focusedAfterFade: true },
    'the fade belongs to the retired Escape gesture, not to this new control press');
  assert.equal(f.api.isOpen(), false); assert.equal(f.commands.length, 0); assert.deepEqual(f.counts(), baseline);
});
