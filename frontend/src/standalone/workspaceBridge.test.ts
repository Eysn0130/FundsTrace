import { describe, expect, it, vi } from 'vitest';
import { createWorkspaceBridge, WORKSPACE_CHANNEL, WORKSPACE_VERSION } from './workspaceBridge';

function harness() {
  const listeners = new Map<string, Set<(event: Event) => void>>();
  const parent = { postMessage: vi.fn() };
  const target = { parent, location: { origin: 'http://127.0.0.1:8300' },
    addEventListener: (type: string, receive: (event: Event) => void) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type)!.add(receive); },
    removeEventListener: (type: string, receive: (event: Event) => void) => listeners.get(type)?.delete(receive) } as unknown as Window;
  const onContext = vi.fn(); const onCasesChanged = vi.fn(); const onDataChanged = vi.fn(); const onOpenImport = vi.fn(); const onImportSettled = vi.fn();
  const bridge = createWorkspaceBridge({ target, onContext, onCasesChanged, onDataChanged, onOpenImport, onImportSettled });
  const receive = (data: Record<string, unknown>, overrides = {}) => listeners.get('message')?.forEach(listener => listener({ data: { channel: WORKSPACE_CHANNEL, version: WORKSPACE_VERSION, ...data }, source: parent, origin: target.location.origin, ...overrides } as unknown as MessageEvent));
  const modal = (detail: unknown) => listeners.get('funds:feedback-modal')?.forEach(listener => listener(new CustomEvent('funds:feedback-modal', { detail })));
  const bind = (caseId = '1', generation = 1) => receive({ type: 'context', caseId, name: `案件${caseId}`, generation, retainedCaseIds: ['1'] });
  const lastMessage = () => parent.postMessage.mock.calls.at(-1)![0];
  return { bridge, parent, target, onContext, onCasesChanged, onDataChanged, onOpenImport, onImportSettled, receive, modal, bind, lastMessage };
}

describe('same-origin workspace bridge', () => {
  it('defaults an older host without navigationPending to an idle presentation', () => {
    const h = harness(); h.bind();
    expect(h.bridge.context).toMatchObject({ caseId: '1', generation: 1, navigationPending: false });
    h.bridge.dispose();
  });

  it.each([undefined, null, 0, 1, 'false', {}, []].map(value => [value]))('rejects a present non-boolean navigationPending value (%j)', navigationPending => {
    const h = harness(); h.bind();
    const current = h.bridge.context;
    h.receive({ type: 'context', caseId: '2', name: '案件2', generation: 2, retainedCaseIds: [], navigationPending });
    expect(h.bridge.context).toBe(current);
    expect(h.onContext).toHaveBeenCalledOnce();
    h.bridge.dispose();
  });

  it('updates only pending presentation without settling or rebinding an in-flight request', async () => {
    const h = harness(); h.bind();
    const resolved = vi.fn(), rejected = vi.fn();
    const controller = new AbortController();
    const pending = h.bridge.request('open-graph', {}, controller.signal).then(resolved, rejected);
    const request = h.lastMessage();
    for (const navigationPending of [true, false, true]) {
      h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 1, retainedCaseIds: [], navigationPending });
      expect(h.bridge.context).toMatchObject({ caseId: '1', name: '案件1', generation: 1, navigationPending });
      await Promise.resolve();
      expect(resolved).not.toHaveBeenCalled(); expect(rejected).not.toHaveBeenCalled();
    }
    expect(h.parent.postMessage).toHaveBeenCalledTimes(2);
    expect(controller.signal.aborted).toBe(false);
    h.receive({ type: 'result', requestId: request.requestId, ok: true });
    await pending; expect(resolved).toHaveBeenCalledOnce(); expect(rejected).not.toHaveBeenCalled();
    h.bridge.dispose();
  });

  it('confirms a selected target from its pending context without waiting for the old result', async () => {
    const h = harness(); h.bind();
    const completed = vi.fn();
    const selected = h.bridge.request('select-case', { caseId: '2' }).then(completed);
    const request = h.lastMessage();
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 1, retainedCaseIds: [], navigationPending: true });
    await Promise.resolve(); expect(completed).not.toHaveBeenCalled();
    h.receive({ type: 'context', caseId: '2', name: '案件2', generation: 2, retainedCaseIds: [], navigationPending: true });
    await selected; expect(completed).toHaveBeenCalledOnce();
    expect(h.bridge.context).toMatchObject({ caseId: '2', generation: 2, navigationPending: true });
    h.receive({ type: 'result', requestId: request.requestId, ok: false, error: '旧绑定结果' });
    expect(completed).toHaveBeenCalledOnce(); expect(h.parent.postMessage).toHaveBeenCalledTimes(2);
    h.bridge.dispose();
  });

  it('retains generation, abort and disposal isolation across pending-only and A–B–A updates', async () => {
    const h = harness(); h.bind();
    const stale = h.bridge.request('open-graph', {});
    const staleFailure = expect(stale).rejects.toMatchObject({ name: 'AbortError' });
    const staleRequest = h.lastMessage();
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 1, retainedCaseIds: [], navigationPending: true });
    h.bind('2', 2); h.bind('1', 3); await staleFailure;
    const current = h.bridge.context;
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 1, retainedCaseIds: [], navigationPending: true });
    h.receive({ type: 'result', requestId: staleRequest.requestId, ok: true });
    expect(h.bridge.context).toBe(current);
    const controller = new AbortController();
    const aborted = h.bridge.request('open-graph', {}, controller.signal);
    const abortedFailure = expect(aborted).rejects.toMatchObject({ name: 'AbortError' });
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 3, retainedCaseIds: [], navigationPending: true });
    controller.abort(); await abortedFailure;
    const disposed = h.bridge.request('open-graph', {});
    const disposedFailure = expect(disposed).rejects.toMatchObject({ name: 'AbortError' });
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 3, retainedCaseIds: [], navigationPending: false });
    h.bridge.dispose(); await disposedFailure;
    const last = h.bridge.context;
    h.receive({ type: 'context', caseId: '2', name: '案件2', generation: 4, retainedCaseIds: [], navigationPending: true });
    expect(h.bridge.context).toBe(last);
  });

  it('only refreshes data for the current case generation and retains unresolved import metadata', () => {
    const h = harness(); h.bind();
    h.receive({ type: 'data-changed', caseId: '2', generation: 1 });
    h.receive({ type: 'data-changed', caseId: '1', generation: 0 });
    h.receive({ type: 'data-changed', caseId: '1', generation: 1 }, { source: {} });
    expect(h.onDataChanged).not.toHaveBeenCalled();
    h.receive({ type: 'data-changed', caseId: '1', generation: 1 });
    expect(h.onDataChanged).toHaveBeenCalledOnce();
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 1, retainedCaseIds: [], pendingImports: [{ importId: '0123456789ab', phase: 'unknown' }] });
    expect(h.bridge.context?.pendingImports).toEqual([{ importId: '0123456789ab', phase: 'unknown' }]);
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 1, retainedCaseIds: [], pendingImports: [{ importId: '0123456789ab', phase: 'done' }] });
    expect(h.bridge.context?.pendingImports?.[0].phase).toBe('unknown'); h.bridge.dispose();
  });

  it('binds acquire/release and data notifications to the current host context', async () => {
    const h = harness(); h.bind();
    for (const [action, payload] of [['import-acquire', { importId: '0123456789ab' }], ['import-release', { importId: '0123456789ab', rejected: true }], ['data-changed', {}]] as const) {
      const request = h.bridge.request(action, payload);
      expect(h.lastMessage()).toMatchObject({ action, payload, caseId: '1', generation: 1 });
      h.receive({ type: 'result', requestId: h.lastMessage().requestId, ok: true }); await request;
    }
    h.bridge.dispose();
  });
  it('accepts only a well-formed direct-parent context with the exact origin and protocol', () => {
    const h = harness();
    expect(h.lastMessage()).toEqual({ channel: WORKSPACE_CHANNEL, version: 1, type: 'ready', instanceId: expect.any(String) });
    const context = { type: 'context', caseId: '9007199254740993', name: '合成案件', generation: 2, retainedCaseIds: [] };
    h.receive(context, { origin: 'https://unrelated.example' }); h.receive(context, { source: {} });
    h.receive({ ...context, generation: -1 }); h.receive({ ...context, retainedCaseIds: [3] }); h.receive({ ...context, version: 2 });
    expect(h.onContext).not.toHaveBeenCalled();
    h.receive(context); expect(h.bridge.context?.caseId).toBe('9007199254740993');
    h.receive({ ...context, caseId: 'older', generation: 1 }); h.receive({ ...context, caseId: 'conflicting' });
    expect(h.onContext).toHaveBeenCalledOnce(); h.bridge.dispose();
  });

  it('waits for the target case context before completing a successful selection', async () => {
    const h = harness(); h.bind();
    const completed = vi.fn(); const selected = h.bridge.request('select-case', { caseId: '2' }).then(completed);
    const request = h.lastMessage();
    expect(request).toMatchObject({ type: 'request', caseId: '1', generation: 1, action: 'select-case', payload: { caseId: '2' } });
    h.receive({ type: 'result', requestId: request.requestId, ok: true });
    await Promise.resolve(); expect(completed).not.toHaveBeenCalled();
    h.bind('2', 2); await selected; expect(completed).toHaveBeenCalledOnce(); h.bridge.dispose();
  });

  it('invalidates callbacks across A–B–A and ignores delayed old results', async () => {
    const h = harness(); h.bind();
    const first = h.bridge.request('open-graph', {}); const firstFailure = expect(first).rejects.toMatchObject({ name: 'AbortError' });
    const request = h.lastMessage(); h.bind('2', 2); h.bind('1', 3); await firstFailure;
    h.receive({ type: 'result', requestId: request.requestId, ok: true });
    const current = h.bridge.request('case-action', { action: 'export' });
    h.receive({ type: 'result', requestId: h.lastMessage().requestId, ok: false, error: '合成导出失败' });
    await expect(current).rejects.toThrow('合成导出失败'); h.bridge.dispose();
  });

  it('preserves pending requests on a retained-work notification and cancels aborted work', async () => {
    const h = harness(); h.bind();
    const controller = new AbortController();
    const request = h.bridge.request('open-graph', {}, controller.signal); const failure = expect(request).rejects.toMatchObject({ name: 'AbortError' });
    h.receive({ type: 'context', caseId: '1', name: '案件1', generation: 1, retainedCaseIds: ['1', '2'] });
    h.receive({ type: 'cases-changed' }); expect(h.onCasesChanged).toHaveBeenCalledOnce();
    controller.abort(); await failure;
    const next = h.bridge.request('open-graph', {}); h.receive({ type: 'result', requestId: h.lastMessage().requestId, ok: true }); await next;
    h.bridge.dispose(); await expect(h.bridge.request('open-graph', {})).rejects.toMatchObject({ name: 'AbortError' });
  });
});


describe('workspace feedback messages', () => {
  it('publishes scalar feedback without a request or lease and rejects old bindings and disposal', () => {
    const h = harness(); const readyInstance = h.lastMessage().instanceId; h.bind();
    const binding = h.bridge.context!;
    const event = { id: 'import:0123456789ab', kind: 'task' as const, operation: 'import' as const, importId: '0123456789ab', status: 'unknown' as const, title: '导入结果待核对' };
    expect(h.bridge.publishFeedback(event, binding)).toBe(true);
    expect(h.lastMessage()).toMatchObject({ type: 'feedback', caseId: '1', generation: 1, instanceId: expect.any(String), event });
    expect(h.lastMessage().action).toBeUndefined(); expect(h.lastMessage().requestId).toBeUndefined();
    const instanceId = h.lastMessage().instanceId;
    expect(instanceId).toBe(readyInstance);
    h.bind('2', 2); h.bind('1', 3);
    expect(h.bridge.publishFeedback(event, binding)).toBe(false);
    expect(h.bridge.publishFeedback(event)).toBe(true); expect(h.lastMessage().instanceId).toBe(instanceId);
    h.bridge.dispose(); expect(h.bridge.publishFeedback(event)).toBe(false);
  });

  it('opens an original import only for a direct-parent current-scope command', () => {
    const h = harness(); h.bind();
    const event = { type: 'feedback-open-import', caseId: '1', generation: 1, importId: '0123456789ab' };
    h.receive(event, { origin: 'https://other.invalid' }); h.receive(event, { source: {} });
    h.receive({ ...event, generation: 0 }); h.receive({ ...event, caseId: '2' }); h.receive({ ...event, importId: '<script>' });
    expect(h.onOpenImport).not.toHaveBeenCalled();
    h.receive(event); expect(h.onOpenImport).toHaveBeenCalledWith('0123456789ab');
    h.bind('2', 2); h.bind('1', 3); h.receive(event); expect(h.onOpenImport).toHaveBeenCalledOnce();
    h.bridge.dispose(); h.receive({ ...event, generation: 3 }); expect(h.onOpenImport).toHaveBeenCalledOnce();
  });
});


describe('iframe feedback modal and identity handshakes', () => {
  it('replies to a trusted hello with its original document instance without touching requests', async () => {
    const h = harness(); const ready = h.lastMessage(); h.bind();
    const request = h.bridge.request('open-graph', {}); const outbound = h.lastMessage();
    const before = h.parent.postMessage.mock.calls.length;
    h.receive({ type: 'feedback-hello' }, { source: {} }); h.receive({ type: 'feedback-hello' }, { origin: 'https://other.invalid' });
    h.receive({ type: 'feedback-hello', version: 2 }); h.receive({ type: 'feedback-hello', channel: 'unrelated' });
    expect(h.parent.postMessage).toHaveBeenCalledTimes(before);
    h.receive({ type: 'feedback-hello' }); expect(h.lastMessage()).toEqual(ready);
    expect(h.onContext).toHaveBeenCalledOnce();
    h.receive({ type: 'result', requestId: outbound.requestId, ok: true }); await request;
    h.bridge.dispose(); const closed = h.parent.postMessage.mock.calls.length;
    h.receive({ type: 'feedback-hello' }); expect(h.parent.postMessage).toHaveBeenCalledTimes(closed);
  });
  it('forwards only boolean local modal state with the current case, generation and ready instance', () => {
    const h = harness(); const instanceId = h.lastMessage().instanceId;
    h.modal({ open: true }); expect(h.parent.postMessage).toHaveBeenCalledOnce();
    h.bind(); expect(h.lastMessage()).toEqual({ channel: WORKSPACE_CHANNEL, version: WORKSPACE_VERSION, type: 'feedback-modal', caseId: '1', generation: 1, instanceId, open: true });
    const count = h.parent.postMessage.mock.calls.length;
    for (const detail of [null, {}, { open: 1 }, { open: 'true' }]) h.modal(detail);
    expect(h.parent.postMessage).toHaveBeenCalledTimes(count);
    h.modal({ open: false }); expect(h.lastMessage()).toMatchObject({ type: 'feedback-modal', open: false, caseId: '1', generation: 1, instanceId });
    h.bind('2', 2); h.bind('1', 3); h.modal({ open: true });
    expect(h.lastMessage()).toMatchObject({ type: 'feedback-modal', caseId: '1', generation: 3, instanceId, open: true });
    h.receive({ type: 'feedback-hello' });
    expect(h.parent.postMessage.mock.calls.at(-2)![0]).toMatchObject({ type: 'ready', instanceId });
    expect(h.lastMessage()).toMatchObject({ type: 'feedback-modal', caseId: '1', generation: 3, open: true });
    h.bridge.dispose(); expect(h.lastMessage()).toMatchObject({ type: 'feedback-modal', open: false });
    const disposed = h.parent.postMessage.mock.calls.length; h.modal({ open: true }); expect(h.parent.postMessage).toHaveBeenCalledTimes(disposed);
  });
});


describe('verified import settlement messages', () => {
  it('accepts settlement only from the exact current parent binding and leaves requests untouched', async () => {
    const h = harness(); h.bind();
    const event = { type: 'feedback-import-settled', caseId: '1', generation: 1, importId: '0123456789ab' };
    h.receive(event, { origin: 'https://other.invalid' }); h.receive(event, { source: {} });
    h.receive({ ...event, version: 2 }); h.receive({ ...event, channel: 'unrelated' });
    h.receive({ ...event, generation: 0 }); h.receive({ ...event, caseId: '2' }); h.receive({ ...event, importId: '<script>' });
    expect(h.onImportSettled).not.toHaveBeenCalled();
    const request = h.bridge.request('import-release', { importId: event.importId, jobId: event.importId });
    const original = h.lastMessage(), resolved = vi.fn(); request.then(resolved);
    h.receive(event); expect(h.onImportSettled).toHaveBeenCalledWith(event.importId);
    await Promise.resolve(); expect(resolved).not.toHaveBeenCalled(); expect(h.lastMessage()).toBe(original);
    h.receive({ type: 'result', requestId: original.requestId, ok: true }); await request;
    h.bind('2', 2); h.bind('1', 3); h.receive(event); expect(h.onImportSettled).toHaveBeenCalledOnce();
    h.bridge.dispose(); h.receive({ ...event, generation: 3 }); expect(h.onImportSettled).toHaveBeenCalledOnce();
  });
});
