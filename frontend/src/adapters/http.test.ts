import { describe, expect, it, vi } from 'vitest';
import type { CaseStats } from '../../../contracts/api-v1';
import type { FundsApi } from '../lib/api';
import { createHttpFundsSession } from './http';

const unused = async (): Promise<never> => { throw new Error('Unexpected synthetic transport call'); };
const statistics: CaseStats = { case_id: '1', flows: 0, accounts: 0, batches: 0, amount_total: '0.00', currency: null, amount_totals: [] };
function transport(overrides: Partial<FundsApi> = {}): FundsApi {
  return { cases: unused, createCase: unused, stats: unused, flows: unused, preview: unused, previewDirectory: unused, repreview: unused, commit: unused, job: unused, imports: unused, sources: unused, downloadSource: unused, downloadImportReport: unused, cleanup: unused, ...overrides };
}

describe('case-bound HTTP adapter', () => {
  it('keeps old hosts without optional report reads or report export usable', () => {
    const bound = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic' }, scopeKey: 'old-host', generation: 1, transport: transport() });
    expect(bound.session.client.importFundsReport).toBeUndefined();
    expect(bound.session.client.verifyImportSource).toBeUndefined();
    expect(bound.session.client.importReportRecords).toBeUndefined();
    expect(bound.session.actions!.downloadFundsReport).toBeUndefined();
  });
  it('binds report verification, rows and export to the selected case and forwards revisions unchanged', async () => {
    const report = vi.fn(unused), verify = vi.fn(unused), records = vi.fn(unused), document = vi.fn(async () => {});
    const context = { caseId: '1', name: 'Synthetic' };
    const bound = createHttpFundsSession({ context, scopeKey: 'report', generation: 1, transport: transport({ importFundsReport: report, verifyImportSource: verify, importReportRecords: records }), actions: { downloadFundsReport: document } });
    context.caseId = '2';
    const query = { memberPath: 'data.csv', currency: 'CNY', account: '0009007199254740993', expectedRevision: '9007199254740993' };
    for (const call of [() => bound.session.client.importFundsReport!('source', query), () => bound.session.client.verifyImportSource!('source', query), () => bound.session.client.importReportRecords!('source', { ...query, counterparty: '0002', direction: 'in' })]) await expect(call()).rejects.toThrow('Unexpected synthetic transport call');
    expect(report).toHaveBeenCalledWith('1', 'source', query, expect.any(AbortSignal));
    expect(verify).toHaveBeenCalledWith('1', 'source', query, expect.any(AbortSignal));
    expect(records).toHaveBeenCalledWith('1', 'source', { ...query, counterparty: '0002', direction: 'in' }, expect.any(AbortSignal));
    const source = { sourceId: 'source', filename: 'synthetic.html', ...query };
    await bound.session.actions!.downloadFundsReport!(source);
    expect(document).toHaveBeenCalledWith(source, expect.any(AbortSignal));
    bound.revoke();
    for (const call of [() => bound.session.client.importFundsReport!('source', query), () => bound.session.client.verifyImportSource!('source', query), () => bound.session.client.importReportRecords!('source', query), () => bound.session.actions!.downloadFundsReport!(source)]) await expect(call()).rejects.toMatchObject({ code: 'session_revoked' });
    for (const method of [report, verify, records, document]) expect(method).toHaveBeenCalledOnce();
  });
  it('rejects a report reply from a revoked same-case binding even if its transport ignores cancellation', async () => {
    type Report = Awaited<ReturnType<NonNullable<FundsApi['importFundsReport']>>>;
    let finish!: (value: Report) => void, signal: AbortSignal | undefined;
    const report = vi.fn((_case: string, _source: string, _query: unknown, combined?: AbortSignal) => { signal = combined; return new Promise<Report>(resolve => { finish = resolve; }); });
    const context = { caseId: '1', name: 'Synthetic' }, query = { memberPath: 'data.csv' };
    const old = createHttpFundsSession({ context, scopeKey: 'report', generation: 1, transport: transport({ importFundsReport: report }) });
    const pending = old.session.client.importFundsReport!('source', query), rejected = expect(pending).rejects.toMatchObject({ code: 'session_revoked' });
    old.revoke();
    const next = createHttpFundsSession({ context, scopeKey: 'report', generation: 2, transport: transport({ importFundsReport: unused }) });
    expect(next.session.signal.aborted).toBe(false); expect(signal?.aborted).toBe(true);
    // Revocation must reject before a caller can consume any part of a late payload.
    finish({ case_id: '1' } as Report); await rejected;
    await expect(old.session.client.importFundsReport!('source', query)).rejects.toMatchObject({ code: 'session_revoked' });
    expect(report).toHaveBeenCalledOnce();
  });
  it('rejects an already-aborted report read without revoking the current session', async () => {
    const report = vi.fn(unused), controller = new AbortController(); controller.abort();
    const bound = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic' }, scopeKey: 'report', generation: 1, transport: transport({ importFundsReport: report }) });
    await expect(bound.session.client.importFundsReport!('source', { memberPath: 'data.csv' }, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(report).not.toHaveBeenCalled(); expect(bound.session.signal.aborted).toBe(false);
  });
  it('revokes optional issue navigation including a transport that returns after cancellation',async()=>{
    type Page=Awaited<ReturnType<NonNullable<FundsApi['importReview']>>>;
    let finish!:(value:Page)=>void,signal:AbortSignal|undefined;
    const method=vi.fn((_case:string,_source:string,_member:string,_query:unknown,combined?:AbortSignal)=>{signal=combined;return new Promise<Page>(resolve=>{finish=resolve;});});
    const bound=createHttpFundsSession({context:{caseId:'1',name:'Synthetic'},scopeKey:'review',generation:1,transport:transport({importReview:method})});
    const captured=bound.session.client.importReview!,query={view:'review' as const,page:1,issueIndex:2};
    const pending=captured('SYN-SOURCE','table.csv',query),rejected=expect(pending).rejects.toMatchObject({code:'session_revoked'});
    bound.revoke();expect(signal?.aborted).toBe(true);
    finish({case_id:'1',source_id:'SYN-SOURCE',member_path:'table.csv',view:'review',counts:{all:0,review:0,accepted:0},items:[],page:1,page_size:50,total:0});await rejected;
    await expect(captured('SYN-SOURCE','table.csv',query)).rejects.toMatchObject({code:'session_revoked'});
    expect(method).toHaveBeenCalledOnce();
  });
  it('binds optional source-record reads and revokes dispatched and captured calls', async () => {
    let finish!: (value: Awaited<ReturnType<NonNullable<FundsApi['importRecords']>>>) => void, signal: AbortSignal | undefined;
    const importRecords = vi.fn((_case: string, _source: string, _page = 1, combined?: AbortSignal) => {
      signal = combined; return new Promise<Awaited<ReturnType<NonNullable<FundsApi['importRecords']>>>>(resolve => { finish = resolve; });
    });
    const bound = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic case' }, scopeKey: 'records', generation: 1, transport: transport({ importRecords }) });
    const captured = bound.session.client.importRecords!;
    const pending = captured('SYN-SOURCE', 2), rejected = expect(pending).rejects.toMatchObject({ code: 'session_revoked' });
    expect(importRecords).toHaveBeenCalledWith('1', 'SYN-SOURCE', 2, expect.any(AbortSignal));
    bound.revoke(); expect(signal?.aborted).toBe(true);
    finish({ case_id: '1', source_id: 'SYN-SOURCE', total: 0, page: 2, page_size: 50, items: [] }); await rejected;
    await expect(captured('SYN-SOURCE')).rejects.toMatchObject({ code: 'session_revoked' });
    expect(importRecords).toHaveBeenCalledOnce();
    const absent = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic case' }, scopeKey: 'old-host', generation: 0, transport: transport() });
    expect(absent.session.client.importRecords).toBeUndefined();
  });

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

  it('binds folder uploads to the case and revokes a captured folder action', async () => {
    const previewDirectory = vi.fn(unused);
    const bound = createHttpFundsSession({ context: { caseId: '1', name: 'Synthetic folder case' },
      scopeKey: 'folder', generation: 1, transport: transport({ previewDirectory }) });
    const upload = bound.session.client.previewDirectory!;
    const files = [new File(['synthetic'], 'folder/account.csv')];
    await expect(upload(files)).rejects.toThrow('Unexpected synthetic transport call');
    expect(previewDirectory).toHaveBeenCalledWith('1', files, expect.any(AbortSignal));
    bound.revoke();
    await expect(upload(files)).rejects.toMatchObject({ code: 'session_revoked' });
    expect(previewDirectory).toHaveBeenCalledOnce();
  });
});
