import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import test from 'node:test';

const require = createRequire(import.meta.url);
const presentation = require('../static/graph-presentation.js');
const controllerSource = readFileSync(new URL('../static/canvas-investigation.js', import.meta.url), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const time1 = '2026-10-23 10:00:00.123456', time2 = '2026-10-23 10:00:00.123457';
const sourceGraph = () => ({ nodes: [
  { account: 'A', name: 'Synthetic A', id_no: 'SYNTHETIC-ID', photo_at: 'synthetic-photo-version', in_cnt: 2, out_cnt: 2,
    in_totals: [{ currency: 'CNY', amount: '10000000000000000.01' }], out_totals: [{ currency: 'USD', amount: '0.02' }] },
  { account: 'B', name: 'Synthetic B', in_cnt: 2, out_cnt: 0 },
], edges: [{ source: 'A', target: 'B', cnt: 2, ids: ['9007199254740993', '9007199254740994'],
  amount_totals: [{ currency: 'CNY', amount: '10000000000000000.01' }, { currency: 'USD', amount: '0.02' }] }],
tracked_ids: ['9007199254740993', '9007199254740994'], main_accounts: ['A'] });
const projection = (amount = '0.01') => ({ nodes: [{ account: 'A', in_cnt: 0, out_cnt: 1,
  in_totals: [], out_totals: [{ currency: 'CNY', amount }] }, { account: 'B', in_cnt: 1, out_cnt: 0 }],
edges: [{ source: 'A', target: 'B', cnt: 1, ids: ['9007199254740993'], amount_totals: [{ currency: 'CNY', amount }] }],
timeline: [{ time: time1 }, { time: time2 }], summary: { amount_totals: [{ currency: 'CNY', amount }] } });

// This fixture implements only browser behavior used by this controller:
// dynamic form replacement, scoped selectors, dialog lifecycle and event capture.
// It deliberately keeps aborted responses deliverable to test stale-read guards.
function fixture() {
  const elements = new Map(), windowListeners = new Map(), documentListeners = new Map();
  const calls = [], toasts = [], commands = [], fitRequests = [], timers = new Map();
  let timerSequence = 0, historyBusy = false, dragging = false, acceptCommand = true, renderCount = 0;
  const attrValue = (attrs, name) => attrs.match(new RegExp(`(?:^|\\s)${name}="([^"]*)"`))?.[1];
  const decode = value => String(value ?? '').replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  class Element {
    constructor(id = '', tagName = 'div') {
      this.id = id; this.tagName = tagName.toUpperCase(); this.value = ''; this.textContent = ''; this.dataset = {};
      this.checked = false; this.disabled = false; this.open = false; this.hidden = false;
      this.style = { setProperty(name, value) { this[name] = value; } }; this.offsetHeight = 48;
      this.children = []; this.listeners = new Map(); this.parentElement = null;
      if (id) elements.set(id, this);
    }
    addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
    appendChild(child) { child.parentElement = this; this.children.push(child); if (child.id) elements.set(child.id, child); return child; }
    replaceChildren(...children) {
      for (const child of this.children) { if (child.id && elements.get(child.id) === child) elements.delete(child.id); child.parentElement = null; }
      this.children = []; for (const child of children) this.appendChild(child);
    }
    set innerHTML(html) {
      this.replaceChildren(); this.html = html;
      const tags = /<([a-z][\w-]*)\b([^>]*?)>/gi;
      for (let match; (match = tags.exec(html));) {
        const [, tag, attrs] = match, id = attrValue(attrs, 'id'), name = attrValue(attrs, 'name');
        if (!id && !name && !/data-[\w-]+=/.test(attrs)) continue;
        const child = new Element(id || '', tag); child.name = name || ''; child.value = decode(attrValue(attrs, 'value'));
        child.checked = /(?:^|\s)checked(?:\s|$)/.test(attrs); child.disabled = /(?:^|\s)disabled(?:\s|$)/.test(attrs);
        for (const entry of attrs.matchAll(/data-([\w-]+)="([^"]*)"/g)) child.dataset[entry[1].replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = decode(entry[2]);
        if (tag === 'textarea') child.value = decode(html.slice(tags.lastIndex).split('</textarea>')[0]);
        if (tag === 'select') {
          const options = [...html.slice(tags.lastIndex).split('</select>')[0].matchAll(/<option\b([^>]*?)>/g)];
          child.value = decode(attrValue(options.find(option => /\bselected\b/.test(option[1]))?.[1] || options[0]?.[1] || '', 'value'));
        }
        this.appendChild(child);
      }
    }
    get innerHTML() { return this.html || ''; }
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(child => [child, ...child.children]);
      if (selector === 'button,input,select,textarea') return descendants.filter(child => ['BUTTON','INPUT','SELECT','TEXTAREA'].includes(child.tagName));
      if (selector === '[name=groupMember]:checked') return descendants.filter(child => child.name === 'groupMember' && child.checked);
      const data = selector.match(/^\[data-([\w-]+)\]$/)?.[1]?.replace(/-([a-z])/g, (_, char) => char.toUpperCase());
      return data ? descendants.filter(child => Object.hasOwn(child.dataset, data)) : [];
    }
    closest(selector) { for (let node = this; node; node = node.parentElement) if (selector === '#' + node.id) return node; return null; }
    get isConnected() { return !!this.parentElement || !!this.id && elements.get(this.id) === this; }
    focus() {}
    showModal() { this.open = true; }
    close() { if (!this.open) return; this.open = false; for (const fn of this.listeners.get('close') || []) fn({ target: this }); }
    async dispatch(type, extra = {}) {
      const event = { target: this, currentTarget: this, preventDefault() {}, ...extra };
      for (const listener of documentListeners.get(type) || []) listener(event);
      await Promise.all((this.listeners.get(type) || []).map(listener => listener(event)));
    }
    click() { return this.disabled ? Promise.resolve() : this.dispatch('click'); }
  }
  const element = id => elements.get(id) || new Element(id);
  for (const id of ['investigationDialog', 'investigationTitle', 'investigationContext', 'investigationBody', 'investigationStatus', 'investigationClose',
    'investigationTransport', 'investigationFrame', 'investigationSeek', 'investigationPlay', 'investigationPrevious', 'investigationNext', 'investigationKeep', 'investigationStop', 'graphContainer']) element(id);
  const region = new Element('graphRegion'); region.appendChild(element('graphContainer'));
  for (const id of ['investigationFrame', 'investigationSeek', 'investigationPlay', 'investigationPrevious', 'investigationNext', 'investigationKeep', 'investigationStop']) element('investigationTransport').appendChild(element(id));
  const graph = sourceGraph();
  const state = { caseId: 1, canvasId: 0, caseGeneration: 1, canvasGeneration: 1, canvasName: '', cases: [{ id: 1, name: 'Synthetic case' }],
    graphLoading: false, graphView: { revision: 1, exportDepth: 0 }, presentation: presentation.create(), lastGraph: graph,
    graphSelection: { ids: new Set(['A']), status: () => ({ dragging }), rebind(ids) { this.ids = new Set(ids); } } };
  let visible = graph;
  state.graph = { getNodes: () => visible.nodes.map(node => ({ getModel: () => ({ id: node.account, data: node }) })),
    getEdges: () => visible.edges.map(edge => ({ getModel: () => edge })), findById: id => visible.nodes.some(node => node.account === id) ? {} : null };
  const window = { addEventListener(type, fn) { if (!windowListeners.has(type)) windowListeners.set(type, []); windowListeners.get(type).push(fn); },
    FundsCanvasEditor: { history: { status: () => ({ busy: historyBusy }) }, async command(label, action) {
      if (!acceptCommand) return false; await action(); commands.push(label); return true;
    } }, FundsRibbon: { closePopovers() {} }, FundsToolbarMenu: { close() {} }, FundsRibbonCommands: { sync() {}, execute() {} } };
  const document = { readyState: 'complete', hidden: false, getElementById: id => elements.get(id) || null,
    createElement: tag => new Element('', tag), addEventListener(type, fn) { if (!documentListeners.has(type)) documentListeners.set(type, []); documentListeners.get(type).push(fn); } };
  const context = () => ({ caseId: state.caseId, canvasId: state.canvasId, caseGeneration: state.caseGeneration, canvasGeneration: state.canvasGeneration });
  const env = { window, document, state, layoutJob: null, AbortController, crypto: { randomUUID: () => `synthetic-${commands.length}` },
    setTimeout(fn, delay) { const id = ++timerSequence; timers.set(id, { fn, delay }); return id; }, clearTimeout: id => timers.delete(id),
    caseContext: context, isCurrentCase: value => !!value && JSON.stringify(value) === JSON.stringify(context()),
    jsonOpts: body => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
    newGraphPresentation: presentation.create, captureEdgeLabelRatios() {}, savePositions() {},
    totalLabel: totals => totals.map(value => `${value.currency} ${value.amount}`).join(' / '),
    esc: value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])),
    toast: (message, type) => toasts.push({ message, type }), FundsGraphRelations: require('../static/graph-relations.js'),
    fetch(path, options) { const pending = deferred(); calls.push({ path, options, body: JSON.parse(options.body), ...pending }); return pending.promise; },
    renderGraph() { renderCount++; fitRequests.push(state.forceFit === true); visible = window.FundsInvestigation.displayGraph(state.lastGraph); state.graphView.revision++;
      for (const fn of windowListeners.get('funds:graph-metric') || []) fn({ type: 'funds:graph-metric' }); },
  };
  vm.createContext(env); vm.runInContext(controllerSource, env, { filename: 'canvas-investigation.js' });
  const respond = (index, payload = projection(), status = 200) => calls[index].resolve({ ok: status === 200, status, json: async () => clone(payload) });
  const reject = (index, error = new Error('synthetic unavailable')) => calls[index].reject(error);
  return { api: window.FundsInvestigation, state, env, element, calls, toasts, commands, fitRequests, timers, respond, reject,
    render: env.renderGraph, renders: () => renderCount, currentVisible: () => visible,
    setBusy(value) { historyBusy = value; }, setDragging(value) { dragging = value; }, setAccepted(value) { acceptCommand = value; },
    async emit(type, extra = {}) { for (const fn of documentListeners.get(type) || []) fn(extra); await flush(); },
    async emitWindow(type, extra = {}) { for (const fn of windowListeners.get(type) || []) fn(extra); await flush(); },
    rebind(caseId, source = state.lastGraph) { state.caseId = caseId; state.caseGeneration++; state.lastGraph = source; window.FundsInvestigation.sync(); },
  };
}

test('bounded display remains empty while pending or failed and only an explicit retry sends another request', async () => {
  const h = fixture(), original = JSON.stringify(h.state.lastGraph);
  h.state.presentation.timeWindow = { start: '', end: time1, includeUndated: false };
  for (let i = 0; i < 3; i++) assert.equal(h.api.displayGraph(h.state.lastGraph).edges.length, 0);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].body.end, time1);
  h.reject(0); await flush();
  for (let i = 0; i < 4; i++) { h.api.sync(); assert.equal(h.api.displayGraph(h.state.lastGraph).nodes.length, 0); }
  assert.equal(h.calls.length, 1, 'a persistent read error must not trigger an automatic retry loop');
  assert.match(h.element('investigationScope').textContent, /synthetic unavailable/);
  await h.element('investigationScope').click(); assert.equal(h.calls.length, 2);
  h.respond(1); await flush();
  assert.equal(h.api.displayGraph(h.state.lastGraph).edges[0].amount_totals[0].amount, '0.01');
  assert.equal(JSON.stringify(h.state.lastGraph), original);
});

test('projection merges original account metadata and leaves the complete source facts lossless', async () => {
  const h = fixture(), original = JSON.stringify(h.state.lastGraph);
  h.state.presentation.timeWindow = { start: '', end: time1, includeUndated: true };
  h.api.displayGraph(h.state.lastGraph); h.respond(0, projection('9007199254740993.01')); await flush();
  const display = h.api.displayGraph(h.state.lastGraph), account = display.nodes[0];
  assert.equal(account.name, 'Synthetic A'); assert.equal(account.photo_at, 'synthetic-photo-version'); assert.equal(account.id_no, 'SYNTHETIC-ID');
  assert.equal(account.in_cnt, 0); assert.equal(account.out_totals[0].amount, '9007199254740993.01');
  assert.equal(display.edges[0].ids[0], '9007199254740993'); assert.equal(display.tracked_ids, h.state.lastGraph.tracked_ids);
  assert.equal(JSON.stringify(h.state.lastGraph), original); assert.equal(h.calls.length, 1);
  assert.equal(h.api.exportWindow().end, time1); assert.equal(h.api.exportWindow().preview, false);
  h.state.presentation.timeWindow = { start: '', end: '', includeUndated: true };
  assert.equal(h.api.displayGraph(h.state.lastGraph), h.state.lastGraph, 'clearing the display restriction reveals the original graph');
});

for (const change of ['A-B-A', 'source-replacement']) test(`${change} discards a delayed projection without poisoning the new binding cache`, async () => {
  const h = fixture(); h.state.presentation.timeWindow = { start: '', end: time1, includeUndated: false };
  h.api.displayGraph(h.state.lastGraph); const first = h.calls[0];
  if (change === 'A-B-A') { h.rebind(2); h.rebind(1); } else h.rebind(1, clone(h.state.lastGraph));
  assert.equal(first.options.signal.aborted, true);
  h.api.displayGraph(h.state.lastGraph); assert.equal(h.calls.length, 2);
  h.respond(0, projection('111.11')); await flush();
  assert.equal(h.api.displayGraph(h.state.lastGraph).edges.length, 0, 'old successful response cannot display or complete the new read');
  h.respond(1, projection('222.22')); await flush();
  assert.equal(h.api.displayGraph(h.state.lastGraph).edges[0].amount_totals[0].amount, '222.22');
  assert.equal(h.calls.length, 2);
});

test('group form creates, edits and deletes only display metadata through successful commands', async () => {
  const h = fixture(), original = JSON.stringify(h.state.lastGraph);
  await h.api.execute('createGroup'); h.element('groupName').value = '待补充流水'; h.element('groupNotes').value = '核对原始凭证';
  await h.element('groupCreate').click();
  assert.equal(h.state.presentation.groups.length, 1); assert.equal(h.state.presentation.groups[0].name, '待补充流水');
  assert.deepEqual(h.state.presentation.groups[0].members, ['A']); assert.equal(h.commands.length, 1);
  const id = h.state.presentation.groups[0].id;
  h.state.graphSelection.ids = new Set(['A', 'B']); await h.api.execute('addToGroup'); await h.element('groupAdd').click();
  assert.deepEqual(h.state.presentation.groups[0].members, ['A', 'B'], 'duplicate membership is deduplicated');
  await h.api.execute('manageGroups'); h.element('groupName').value = '待人工复核';
  const boxes = h.element('investigationBody').querySelectorAll('[name=groupMember]:checked'); boxes.find(box => box.value === 'B').checked = false;
  await h.element('groupSave').click(); assert.equal(h.state.presentation.groups[0].name, '待人工复核');
  assert.deepEqual(h.state.presentation.groups[0].members, ['A']); assert.equal(h.state.presentation.groups[0].id, id);
  await h.api.execute('manageGroups'); await h.element('groupDelete').click(); await h.element('groupDeleteConfirm').click();
  assert.deepEqual(h.state.presentation.groups, []); assert.equal(JSON.stringify(h.state.lastGraph), original);
});

test('a refused group command never reports success or records an unexecuted change', async () => {
  const h = fixture(); await h.api.execute('createGroup'); h.element('groupName').value = '尚未创建'; h.setAccepted(false);
  await h.element('groupCreate').click();
  assert.deepEqual(h.state.presentation.groups, []); assert.equal(h.commands.length, 0);
  assert.equal(h.toasts.some(value => /已创建分组/.test(value.message)), false, 'command refusal cannot become a success toast');
  assert.ok(h.toasts.some(value => value.type === 'err') || h.element('investigationStatus').dataset.error === 'true');
});

test('a group dialog from a previous canvas generation cannot mutate the current grouping', async () => {
  const h = fixture(); await h.api.execute('createGroup'); const oldButton = h.element('groupCreate'); h.element('groupName').value = '旧窗口';
  h.rebind(2); h.rebind(1); await oldButton.click();
  assert.deepEqual(h.state.presentation.groups, []); assert.equal(h.commands.length, 0);
});

async function startPlayback(h) {
  const opening = h.api.execute('timePlayback'); h.respond(h.calls.length - 1); await opening;
  const start = h.element('investigationStartPlayback').click(); await flush();
  h.respond(h.calls.length - 1); await flush();
  // The first cumulative frame has a narrower end boundary and its own request.
  assert.equal(h.calls.at(-1).body.end, time1, JSON.stringify({calls:h.calls.map(call=>call.body),status:h.element('investigationStatus').textContent,toasts:h.toasts})); h.respond(h.calls.length - 1); await start; await flush();
  assert.equal(h.api.exportWindow().end, time1); assert.equal(h.api.exportWindow().preview, true);
}

test('editing and applying a saved microsecond boundary preserves all digits', async () => {
  const h = fixture(); h.state.presentation.timeWindow = { start: time1, end: time2, includeUndated: false };
  const opening = h.api.execute('timeWindow'); h.respond(0); await opening;
  assert.match(h.element('investigationBody').innerHTML, /id="investigationEnd" type="text"/,
    'native datetime-local fields discard six-digit fractions');
  assert.equal(h.element('investigationEnd').value, time2);
  const applying = h.element('investigationTimeApply').click(); await flush();
  assert.equal(h.calls[1].body.start, time1); assert.equal(h.calls[1].body.end, time2);
  h.respond(1); await applying;
  assert.equal(h.state.presentation.timeWindow.end, time2); assert.equal(h.commands.length, 1);
});

test('equivalent fractional boundaries compare as the same local time and preserve source precision', async () => {
  const h = fixture(), opening = h.api.execute('timeWindow'); h.respond(0); await opening;
  h.element('investigationStart').value = '2026-10-23 10:00:00.100000';
  h.element('investigationEnd').value = '2026-10-23T10:00:00.1';
  const checking = h.element('investigationTimePreview').click(); await flush();
  assert.equal(h.calls[1].body.end, '2026-10-23 10:00:00.1'); h.respond(1); await checking;
  assert.equal(h.element('investigationStatus').dataset.error, 'false');
});

test('invalid timestamp syntax is explained before a request and calendar errors are visible', async () => {
  const h = fixture(), opening = h.api.execute('timeWindow'); h.respond(0); await opening;
  h.element('investigationStart').value = '10/23/2026'; await h.element('investigationTimeApply').click();
  assert.equal(h.calls.length, 1); assert.equal(h.commands.length, 0);
  assert.match(h.element('investigationStatus').textContent, /YYYY-MM-DD/);
  h.element('investigationStart').value = '2026-02-30 10:00';
  const checking = h.element('investigationTimeApply').click(); await flush();
  h.respond(1, {detail:[{msg:'Value error, 时间格式无效'}]}, 422); await checking;
  assert.match(h.element('investigationStatus').textContent, /时间格式无效/);
  assert.equal(h.element('investigationDialog').open, true); assert.equal(h.commands.length, 0);
});

test('playback controls preserve saved scope and wait for layout, history and node drag before fetching another frame', async () => {
  const h = fixture(), saved = clone(h.state.presentation.timeWindow); await startPlayback(h);
  const count = h.calls.length;
  for (const mode of ['layout', 'history', 'drag']) {
    if (mode === 'layout') h.env.layoutJob = {}; else if (mode === 'history') h.setBusy(true); else h.setDragging(true);
    await h.element('investigationNext').click(); await flush();
    assert.equal(h.calls.length, count, `${mode} blocks a new frame read`);
    assert.equal(h.api.exportWindow().end, time1);
    h.env.layoutJob = null; h.setBusy(false); h.setDragging(false); h.api.sync();
  }
  assert.deepEqual(h.state.presentation.timeWindow, saved); assert.equal(h.commands.length, 0);
  const next = h.element('investigationNext').click(); await flush(); assert.equal(h.calls.length, count + 1);
  h.respond(count, projection('0.02')); await next; await flush(); assert.equal(h.api.exportWindow().end, time2);
  await h.element('investigationKeep').click();
  assert.equal(h.state.presentation.timeWindow.end, time2); assert.equal(h.api.exportWindow().preview, false);
  assert.equal(h.commands.length, 1, 'only applying the playback window enters display history');
});

test('playback frames never request a fit or consume a saved-canvas opening fit', async () => {
  const h = fixture(); let openingFits = 0;
  h.state.graphView.openingFit = { beforeProjection() { openingFits++; return () => openingFits++; } };
  await startPlayback(h);
  const next = h.element('investigationNext').click(); await flush();
  h.respond(h.calls.length - 1, projection('0.02')); await next; await flush();
  assert.equal(h.api.exportWindow().end, time2);
  assert.equal(h.fitRequests.length, 2, 'both cumulative frames were rendered');
  assert.deepEqual(h.fitRequests, [false, false]);
  assert.equal(openingFits, 0, 'frame publication does not inherit opening camera ownership');
});

for (const interruption of ['pointer', 'stop', 'history-busy', 'layout-busy', 'dragging', 'hidden', 'resize', 'pause', 'binding-change']) test(`pending playback frame is discarded after ${interruption}`, async () => {
  const h = fixture(); await startPlayback(h);
  const next = h.element('investigationNext').click(); await flush(); const pending = h.calls.length - 1;
  assert.equal(h.calls[pending].body.end, time2);
  if (interruption === 'pointer') await h.element('graphContainer').dispatch('pointerdown');
  else if (interruption === 'stop') await h.element('investigationStop').click();
  else if (interruption === 'history-busy') h.setBusy(true);
  else if (interruption === 'layout-busy') h.env.layoutJob = {};
  else if (interruption === 'dragging') h.setDragging(true);
  else if (interruption === 'hidden') { h.env.document.hidden = true; await h.emit('visibilitychange'); }
  else if (interruption === 'resize') await h.emitWindow('fundstrace:workspace-resize');
  else if (interruption === 'pause') {
    assert.equal(h.element('investigationPlay').disabled,false,'Pause stays reachable while a frame is loading');
    await h.element('investigationPlay').click();
  }
  else h.rebind(2);
  h.respond(pending, projection('0.02')); await next; await flush();
  if (interruption === 'stop' || interruption === 'binding-change') assert.equal(h.api.exportWindow(), null);
  else assert.equal(h.api.exportWindow().end, time1);
  assert.equal(h.commands.length, 0); assert.equal(h.timers.size, 0);
});

test('a cancelled frame continuation cannot cancel a newer explicit request for the same pending timestamp', async () => {
  const h = fixture(); await startPlayback(h);
  await h.element('investigationNext').click(); await flush(); const pending = h.calls.length - 1;
  await h.element('investigationPlay').click();
  await h.element('investigationNext').click(); await flush();
  assert.equal(h.calls.length, pending + 1, 'the newer frame may reuse the existing read for this exact timestamp');
  h.respond(pending, projection('0.02')); await flush();
  assert.equal(h.api.exportWindow().end, time2, 'only the latest explicit frame intent owns publication');
  assert.equal(h.element('investigationNext').disabled, true, 'the final frame was reached');
});

for (const mode of ['refused', 'layout', 'history', 'export', 'drag']) test(`applying a frame while ${mode} preserves the paused preview and its scope`, async () => {
  const h = fixture(); await startPlayback(h);
  const saved = clone(h.state.presentation.timeWindow), before = h.renders();
  if (mode === 'refused') h.setAccepted(false);
  if (mode === 'layout') h.env.layoutJob = {};
  if (mode === 'history') h.setBusy(true);
  if (mode === 'export') h.state.graphView.exportDepth = 1;
  if (mode === 'drag') h.setDragging(true);
  await h.element('investigationKeep').click();
  assert.equal(h.renders(), before); assert.equal(h.api.exportWindow()?.preview, true);
  assert.equal(h.api.exportWindow()?.end, time1); assert.equal(h.element('investigationTransport').hidden, false);
  assert.deepEqual(h.state.presentation.timeWindow, saved); assert.equal(h.commands.length, 0);
  assert.equal(h.currentVisible().edges[0].amount_totals[0].amount, '0.01');
  h.setAccepted(true); h.env.layoutJob = null; h.setBusy(false); h.setDragging(false); h.state.graphView.exportDepth = 0;
  await h.element('investigationKeep').click();
  assert.equal(h.api.exportWindow()?.preview, false); assert.equal(h.state.presentation.timeWindow.end, time1);
  assert.equal(h.element('investigationTransport').hidden, true); assert.equal(h.commands.length, 1);
});

for (const mode of ['layout', 'history', 'export', 'drag']) test(`ending playback during ${mode} cannot redraw or cancel the active operation`, async () => {
  const h = fixture(); await startPlayback(h); const before = h.renders();
  if (mode === 'layout') h.env.layoutJob = {};
  if (mode === 'history') h.setBusy(true);
  if (mode === 'export') h.state.graphView.exportDepth = 1;
  if (mode === 'drag') h.setDragging(true);
  await h.element('investigationStop').click();
  assert.equal(h.renders(), before); assert.equal(h.api.exportWindow()?.preview, true);
  h.env.layoutJob = null; h.setBusy(false); h.setDragging(false); h.state.graphView.exportDepth = 0;
  await h.element('investigationStop').click();
  assert.equal(h.renders(), before + 1); assert.equal(h.api.exportWindow(), null);
  assert.equal(h.currentVisible(), h.state.lastGraph);
});

test('ZIP path evidence distinguishes member line from archive record number', async () => {
  const h = fixture(); await h.api.execute('findGraphPaths');
  h.element('pathSource').value = 'A'; h.element('pathTarget').value = 'B';
  const search = h.element('pathSearch').click(); await flush();
  h.respond(0, {complete:true, paths:[{accounts:['A','B'],currency:'CNY',steps:[{
    source:'A',target:'B',amount:'0.01',flow_ids:['7']}]}], flows:[{id:'7',amount:'0.01',currency:'CNY',
    provenance:[{flow_id:'7',sources:[{filename:'synthetic.zip',member_path:'member.csv',source_row:'1',member_row:'2'},
      {filename:'synthetic.csv',sheet:'Sheet 1',source_row:'8'}]}]}]});
  await search;
  const html = h.element('pathResults').innerHTML;
  assert.match(html, /member\.csv · 行 2 · 包内记录 1/);
  assert.match(html, /Sheet 1 · 行 8 · 原记录 7/);
  assert.doesNotMatch(html, /member\.csv · 行 1/);
});

test('path controls display card aliases and preserve selected internal keys for actions', async () => {
  const h = fixture();
  for (const node of h.state.lastGraph.nodes) node.display_account = `SYN-CARD-${node.account}`;
  h.state.graphSelection.ids = new Set(['A', 'B']);
  await h.api.execute('findGraphPaths');
  assert.equal(h.element('pathSource').value, 'SYN-CARD-A');
  assert.equal(h.element('pathTarget').value, 'SYN-CARD-B');
  const search = h.element('pathSearch').click(); await flush();
  assert.equal(h.calls[0].body.start_account, 'A'); assert.equal(h.calls[0].body.end_account, 'B');
  h.respond(0, { complete: true, paths: [{ accounts: ['A', 'B'], currency: 'CNY', steps: [{ source: 'A', target: 'B', amount: '0.01', flow_ids: [] }] }] });
  await search;
  assert.match(h.element('pathResults').innerHTML, /SYN-CARD-A → SYN-CARD-B/);
  assert.doesNotMatch(h.element('pathResults').innerHTML, />A → B</);
});
