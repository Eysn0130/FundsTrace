import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const original = readFileSync(new URL('../static/unified-import.js', import.meta.url), 'utf8');
// The browser module loader is the only replaced port; adapter ownership and
// binding checks run from the production script.
assert.equal(original.split("import('/workspace/assets/import-entry.js')").length, 2);
const source = original.replace("import('/workspace/assets/import-entry.js')", 'loadImportModule()');
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function harness(url = 'http://synthetic.test/?case_id=1', loader, read) {
  let context = { caseId: 1, generation: 1, canvasId: 0, canvasGeneration: 1 }, replacement;
  const mounted = [], elements = [], requests = [], warnings = [], listeners = new Map();
  const window = { location: { href: url }, history: { replaceState(_a, _b, location) { replacement = String(location); } },
    FundsAppNavigation: { receiveImportFeedback: () => true, dataChanged: async () => {} },
    FundsImportWorkflow: { acquireExternal: async () => {}, releaseExternal: async () => {}, observeExternal: async () => {} },
    addEventListener(name, callback) { listeners.set(name, callback); } };
  const sandbox = { window, URL, AbortController, state: { cases: [{ id: 1, name: '合成案件' }] },
    document: { createElement: () => ({ remove() { this.removed = true; } }), body: { append(value) { elements.push(value); } } },
    loadImportModule: loader || (async () => ({ mountImport(element, options) { mounted.push({ element, options }); return { dispose() {}, settled() {} }; } })),
    caseContext: () => ({ ...context }), isCurrentCase: (value, canvas) => value.caseId === context.caseId && value.generation === context.generation
      && (!canvas || value.canvasId === context.canvasId && value.canvasGeneration === context.canvasGeneration),
    toast: value => warnings.push(value), fetch: async (path, options) => {
      requests.push(path);
      return read ? read(path, options) : { ok: true, json: async () => ({ case_id: '1', items: [] }) };
    } };
  vm.runInNewContext(source, sandbox);
  return { api: window.FundsUnifiedImport, mounted, elements, requests, warnings, listeners,
    replacement: () => replacement, switchCase(value) { context = value; listeners.get('funds:feedback-binding')?.(); } };
}

test('the canvas consumes a deep link once and refuses a different case binding', async () => {
  const h = harness('http://synthetic.test/?case_id=2&import_id=0123456789ab');
  await h.api.restore(); await h.api.restore();
  assert.equal(h.requests.length, 0); assert.equal(h.mounted.length, 0);
  assert(!new URL(h.replacement()).searchParams.has('import_id')); assert.match(h.warnings[0], /当前案件不一致/);
});
test('a case change revokes the native mount and ignores a late module response', async () => {
  const gate = deferred(), h = harness(undefined, () => gate.promise); let mounts = 0;
  const pending = h.api.open();
  h.switchCase({ caseId: 2, generation: 2 });
  gate.resolve({ mountImport() { mounts++; } }); await pending;
  assert.equal(mounts, 0); assert(h.elements.every(element => element.removed));
});
test('the canvas refuses to mount without a case and aborts its owner on page exit', async () => {
  const h = harness(); h.switchCase({ caseId: null, generation: 2 }); await h.api.open();
  assert.equal(h.mounted.length, 0); assert.match(h.warnings[0], /先选择案件/);
  h.switchCase({ caseId: 1, generation: 3 }); await h.api.open();
  assert.equal(h.mounted.length, 1); assert.equal(h.mounted[0].options.signal.aborted, false);
  h.listeners.get('pagehide')(); assert.equal(h.mounted[0].options.signal.aborted, true);
});

const sourceId = '0123456789abcdef0123456789abcdef';
const sourceFocus = { memberPath: '原始资料/开户.csv', sourceRow: 87 };
const completed = () => ({ ok: true, json: async () => ({ case_id: '1', items: [
  { case_id: '1', import_id: sourceId, stage: 'done', result: { source_id: sourceId } },
] }) });

test('an opening source navigates with its exact member and row in the bound case', async () => {
  const h = harness(undefined, undefined, completed);
  await h.api.restore(sourceId, sourceFocus);
  assert.deepEqual(h.requests, [`/api/v1/cases/1/imports?import_id=${sourceId}`]);
  assert.equal(h.mounted.length, 1);
  assert.equal(h.mounted[0].options.resume.import_id, sourceId);
  assert.deepEqual(h.mounted[0].options.sourceFocus, sourceFocus);
  h.switchCase({ caseId: 1, generation: 1, canvasId: 2, canvasGeneration: 2 });
  assert.equal(h.mounted[0].options.signal.aborted, true);
});

test('a same-case canvas change cancels the focused source read before a late reply can mount it', async () => {
  const gate = deferred(); let signal;
  const h = harness(undefined, undefined, (_path, options) => { signal = options.signal; return gate.promise; });
  const read = h.api.restore(sourceId, sourceFocus);
  h.switchCase({ caseId: 1, generation: 1, canvasId: 2, canvasGeneration: 2 });
  assert.equal(signal.aborted, true);
  gate.resolve(completed()); await read;
  assert.equal(h.mounted.length, 0);
  assert.equal(h.warnings.length, 0);
});

test('a source without a local session mounts the exact case-bound journal location', async () => {
  const h = harness(); await h.api.restore(sourceId, sourceFocus);
  assert.equal(h.mounted.length, 1);
  assert.equal(h.mounted[0].options.resume, undefined);
  assert.deepEqual({ ...h.mounted[0].options.sourceFocus }, { ...sourceFocus, sourceId });
  assert.equal(h.warnings.length, 0);
});
