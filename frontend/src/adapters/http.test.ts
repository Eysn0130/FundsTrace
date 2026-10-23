import { describe, expect, it, vi } from 'vitest';
import type { CaseStats } from '../../../contracts/api-v1';
import type { FundsApi } from '../lib/api';
import { createHttpFundsSession } from './http';

const unused = async (): Promise<never> => { throw new Error('Unexpected synthetic transport call'); };
const statistics: CaseStats = { case_id: '1', flows: 0, accounts: 0, batches: 0, amount_total: '0.00', currency: null, amount_totals: [] };
function transport(overrides: Partial<FundsApi> = {}): FundsApi {
  return { cases: unused, createCase: unused, stats: unused, flows: unused, preview: unused, repreview: unused, commit: unused, job: unused, imports: unused, sources: unused, downloadSource: unused, downloadImportReport: unused, cleanup: unused, ...overrides };
}

describe('case-bound HTTP adapter', () => {
  it('keeps the chosen case and rejects captured old reads, writes and host actions after revocation', async () => {
    const stats = vi.fn(async () => statistics); const commit = vi.fn(unused); const preview = vi.fn(unused); const cleanup = vi.fn(unused);
    const graph = vi.fn(); const legacy = vi.fn(); const download = vi.fn(async () => {});
    const context = { caseId: '1', name: 'Synthetic case' };
    const bound = createHttpFundsSession({ context, scopeKey: 'scope-one', generation: 0, transport: transport({ stats, commit, preview, cleanup }), actions: { openGraph: graph, openLegacyImport: legacy, downloadSource: download } });
    context.caseId = '2';
    await bound.session.client.stats();
    expect(stats).toHaveBeenCalledWith('1', expect.any(AbortSignal));
    expect(bound.session.context.caseId).toBe('1');
    const oldCommit = bound.session.client.commit; const oldGraph = bound.session.actions!.openGraph!;
    bound.revoke();
    const request = { mode: 'A' as const, mapping: {}, mark_accounts: [], self_accounts: [] };
    for (const run of [
      () => bound.session.client.stats(),
      () => oldCommit('0123456789abcdef0123456789abcdef', request),
      () => bound.session.client.preview(new File(['synthetic'], 'synthetic.csv')),
      () => bound.session.client.cleanup(false),
      () => oldGraph(),
      () => bound.session.actions!.openLegacyImport!('0123456789abcdef0123456789abcdef'),
      () => bound.session.actions!.downloadSource!({ sourceId: '0123456789abcdef0123456789abcdef', filename: 'synthetic.csv' }),
    ]) await expect(run()).rejects.toMatchObject({ code: 'session_revoked' });
    expect(stats).toHaveBeenCalledOnce(); expect(commit).not.toHaveBeenCalled(); expect(preview).not.toHaveBeenCalled(); expect(cleanup).not.toHaveBeenCalled();
    expect(graph).not.toHaveBeenCalled(); expect(legacy).not.toHaveBeenCalled(); expect(download).not.toHaveBeenCalled();
  });

  it('aborts a dispatched request and rejects a late response even if transport ignores cancellation', async () => {
    let resolve!: (value: CaseStats) => void;
    const pending = new Promise<CaseStats>(done => { resolve = done; }); let signal: AbortSignal | undefined;
    const bound = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic case' }, scopeKey: 'scope-one', generation: 0, transport: transport({ stats: async (_id, combined) => { signal = combined; return pending; } }) });
    const response = bound.session.client.stats();
    const rejected = expect(response).rejects.toMatchObject({ code: 'session_revoked' });
    bound.revoke(); expect(signal?.aborted).toBe(true);
    resolve(statistics); await rejected;
  });

  it('cancels a caller request without revoking the session or dispatching an already-aborted operation', async () => {
    const commit = vi.fn(unused); const stats = vi.fn(async () => statistics);
    const bound = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic case' }, scopeKey: 'scope-one', generation: 0, transport: transport({ stats, commit }) });
    const controller = new AbortController(); controller.abort();
    await expect(bound.session.client.commit('0123456789abcdef0123456789abcdef', { mode: 'A', mapping: {} }, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(commit).not.toHaveBeenCalled(); expect(bound.session.signal.aborted).toBe(false);
    await expect(bound.session.client.stats()).resolves.toEqual(statistics);
  });

  it('aborts a dispatched report download and prevents a captured action from dispatching after revocation', async () => {
    let resolve!: () => void, signal: AbortSignal | undefined;
    const download = vi.fn((_source, combined?: AbortSignal) => { signal = combined; return new Promise<void>(done => { resolve = done; }); });
    const bound = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic case' }, scopeKey: 'scope-one', generation: 0,
      transport: transport(), actions: { downloadImportReport: download } });
    const captured = bound.session.actions!.downloadImportReport!;
    const source = { sourceId: '0123456789abcdef0123456789abcdef', filename: 'synthetic-report.json' };
    const pending = captured(source);
    const rejected = expect(pending).rejects.toMatchObject({ code: 'session_revoked' });
    expect(download).toHaveBeenCalledWith(source, expect.any(AbortSignal));
    bound.revoke(); expect(signal?.aborted).toBe(true);
    resolve(); await rejected;
    await expect(captured(source)).rejects.toMatchObject({ code: 'session_revoked' });
    expect(download).toHaveBeenCalledOnce();
  });
});
