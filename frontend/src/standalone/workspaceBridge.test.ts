import { describe, expect, it, vi } from 'vitest';
import { createWorkspaceBridge, WORKSPACE_CHANNEL, WORKSPACE_VERSION } from './workspaceBridge';

function harness() {
  const listeners = new Set<(event: MessageEvent) => void>();
  const parent = { postMessage: vi.fn() };
  const target = { parent, location: { origin: 'http://127.0.0.1:8300' },
    addEventListener: (_: string, receive: (event: MessageEvent) => void) => listeners.add(receive),
    removeEventListener: (_: string, receive: (event: MessageEvent) => void) => listeners.delete(receive) } as unknown as Window;
  const onContext = vi.fn(); const onCasesChanged = vi.fn(); const onDataChanged = vi.fn();
  const bridge = createWorkspaceBridge({ target, onContext, onCasesChanged, onDataChanged });
  const receive = (data: Record<string, unknown>, overrides = {}) => listeners.forEach(listener => listener({ data: { channel: WORKSPACE_CHANNEL, version: WORKSPACE_VERSION, ...data }, source: parent, origin: target.location.origin, ...overrides } as unknown as MessageEvent));
  const bind = (caseId = '1', generation = 1) => receive({ type: 'context', caseId, name: `案件${caseId}`, generation, retainedCaseIds: ['1'] });
  const lastMessage = () => parent.postMessage.mock.calls.at(-1)![0];
  return { bridge, parent, target, onContext, onCasesChanged, onDataChanged, receive, bind, lastMessage };
}

describe('same-origin workspace bridge', () => {
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
    expect(h.lastMessage()).toEqual({ channel: WORKSPACE_CHANNEL, version: 1, type: 'ready' });
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
