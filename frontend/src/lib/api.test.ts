import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { createHttpFundsSession } from '../adapters/http';

afterEach(() => vi.unstubAllGlobals());
describe('HTTP failure status', () => {
  it('preserves an empty currency filter and independent report table pages', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }); vi.stubGlobal('fetch', fetcher);
    await api.importFundsReport('1', 'synthetic', { memberPath: 'table.csv', currency: '', accountsPage: 1, relationsPage: 2, peerAccount: '000SYN-9007199254740993', timelineGrain: 'month', timelinePage: 3, expectedRevision: 'current' });
    const parameters = new URL(fetcher.mock.calls[0][0], 'http://synthetic.local').searchParams;
    expect(parameters.has('currency')).toBe(true); expect(parameters.get('currency')).toBe('');
    expect(parameters.get('accounts_page')).toBe('1'); expect(parameters.get('relations_page')).toBe('2');
    expect(parameters.get('peer_account')).toBe('000SYN-9007199254740993'); expect(parameters.get('timeline_grain')).toBe('month'); expect(parameters.get('timeline_page')).toBe('3');
  });
  it('encodes file report scope and preserves string revisions and account identifiers', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ case_id: 'SYN case' }) });
    vi.stubGlobal('fetch', fetcher);
    const signal = new AbortController().signal;
    await api.importFundsReport('SYN case', 'SYN/source', { memberPath: '资料/账 单.xlsx/Sheet 1', currency: 'CNY', account: '0009007199254740993', page: 2, expectedRevision: '9007199254740993' }, signal);
    expect(fetcher).toHaveBeenCalledWith('/api/v1/cases/SYN%20case/imports/SYN%2Fsource/report?member_path=%E8%B5%84%E6%96%99%2F%E8%B4%A6+%E5%8D%95.xlsx%2FSheet+1&page=2&page_size=20&currency=CNY&account=0009007199254740993&expected_revision=9007199254740993', { signal });
  });
  it('keeps verification and drilldown bound to the member and expected report revision', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal('fetch', fetcher);
    const signal = new AbortController().signal;
    await api.verifyImportSource('1', 'SYN/source', { memberPath: 'nested/data.csv', expectedRevision: '17' }, signal);
    await api.importReportRecords('1', 'SYN/source', { memberPath: 'nested/data.csv', expectedRevision: '17', account: '0001', counterparty: '0002', direction: 'in', currency: 'CNY', page: 2 }, signal);
    expect(fetcher.mock.calls).toEqual([
      ['/api/v1/cases/1/imports/SYN%2Fsource/report/verify?member_path=nested%2Fdata.csv&expected_revision=17', { method: 'POST', signal }],
      ['/api/v1/cases/1/imports/SYN%2Fsource/report/records?member_path=nested%2Fdata.csv&page=2&page_size=50&currency=CNY&account=0001&expected_revision=17&counterparty=0002&direction=in', { signal }],
    ]);
  });
  it('downloads the chosen report page and cancels before creating a browser download', async () => {
    const controller = new AbortController(), createObjectURL = vi.fn();
    vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL }));
    const fetcher = vi.fn().mockResolvedValue({ ok: true, blob: async () => { controller.abort(); return new Blob(['synthetic report']); } });
    vi.stubGlobal('fetch', fetcher);
    await expect(api.downloadFundsReport('SYN case', { sourceId: 'SYN/source', filename: 'synthetic-report.html', memberPath: 'data.csv', expectedRevision: '7', account: '0001', currency: 'CNY', peerAccount: '0002', timelineGrain: 'day', timelinePage: 4, page: 2 }, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetcher).toHaveBeenCalledWith('/api/v1/cases/SYN%20case/imports/SYN%2Fsource/report/document?member_path=data.csv&page=2&page_size=20&currency=CNY&account=0001&peer_account=0002&timeline_grain=day&timeline_page=4&expected_revision=7', { signal: controller.signal });
    expect(createObjectURL).not.toHaveBeenCalled();
  });
  it('encodes the review scope, full-file view and issue ordinal as read-only query parameters', async()=>{
    const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({case_id:'1'})});vi.stubGlobal('fetch',fetcher);
    const signal=new AbortController().signal;
    await api.importReview('1','SYN/source','nested/data.csv',{view:'review',page:2,issueIndex:5},signal);
    expect(fetcher).toHaveBeenCalledWith('/api/v1/cases/1/imports/SYN%2Fsource/review?member_path=nested%2Fdata.csv&view=review&page=2&page_size=50&issue_index=5',{signal});
    await api.importReview('1', 'SYN/source', 'nested/data.csv', { view: 'all', page: 1, sourceRow: 105, issueIndex: 5 }, signal);
    expect(fetcher).toHaveBeenLastCalledWith('/api/v1/cases/1/imports/SYN%2Fsource/review?member_path=nested%2Fdata.csv&view=all&page=1&page_size=50&source_row=105', { signal });
  });
  it('reads recent history and a known older receipt through the bound case', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ case_id: '4', items: [] }) });
    vi.stubGlobal('fetch', fetcher);
    const bound = createHttpFundsSession({ context: { caseId: '4', name: 'Synthetic' }, scopeKey: 'receipts', generation: 1, transport: api });
    const read = bound.session.client.imports;
    await read();
    await read(undefined, '0123456789ab');
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/cases/4/imports?limit=100', '/api/v1/cases/4/imports?import_id=0123456789ab',
    ]);
    bound.revoke();
    await expect(read(undefined, '0123456789ab')).rejects.toMatchObject({ code: 'session_revoked' });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('reads an encoded case-bound source page through the existing records endpoint', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ case_id: 'SYN case' }) });
    vi.stubGlobal('fetch', fetcher); const signal = new AbortController().signal;
    await api.importRecords('SYN case', 'SYN/source', 2, signal);
    expect(fetcher).toHaveBeenCalledWith('/api/v1/cases/SYN%20case/imports/SYN%2Fsource/records?page=2&page_size=50', { signal });
  });

  it.each([422, 500])('preserves HTTP %i without classifying network failures as a rejection', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status, headers: new Headers(), json: async () => ({ error: { code: 'synthetic_failure', message: '合成失败' } }) }));
    await expect(api.commit('1', '0123456789ab', { mode: 'A', mapping: {}, mark_accounts: [] })).rejects.toMatchObject({ code: 'synthetic_failure', status });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Disconnected')));
    await expect(api.commit('1', '0123456789ab', { mode: 'A', mapping: {}, mark_accounts: [] })).rejects.toMatchObject({ code: 'network_error', status: undefined });
  });
  it('transports independent trend selection, calendar bounds and paged alias search without losing identifiers',async()=>{
    const fetcher=vi.fn().mockResolvedValue({ok:true,json:async()=>({})});vi.stubGlobal('fetch',fetcher);
    const bound=createHttpFundsSession({context:{caseId:'9007199254740993',name:'Synthetic'},scopeKey:'trend',generation:7,transport:api});
    const accounts=bound.session.client.importReportAccounts!;
    await accounts('SYN/source',{memberPath:'folder/table.csv',expectedRevision:'r',currency:'',search:'合成 户名/卡',page:2});
    let url=new URL('http://synthetic'+fetcher.mock.calls[0][0]);expect(url.pathname).toContain('/9007199254740993/');expect(url.searchParams.get('currency')).toBe('');expect(url.searchParams.get('search')).toBe('合成 户名/卡');expect(url.searchParams.get('page')).toBe('2');
    await api.importReportRecords('9007199254740993','SYN/source',{memberPath:'folder/table.csv',expectedRevision:'r',timelineScope:'selected',timelineAccounts:['0001','9007199254740993'],timelineSearch:'合成',timeStart:'2026-02-01 00:00:00',timeEnd:'2026-03-01 00:00:00',timeBasis:'unqualified'});
    url=new URL('http://synthetic'+fetcher.mock.calls[1][0]);expect(url.searchParams.getAll('timeline_accounts')).toEqual(['0001','9007199254740993']);expect(url.searchParams.get('timeline_scope')).toBe('selected');expect(url.searchParams.get('time_end')).toBe('2026-03-01 00:00:00');expect(url.searchParams.get('time_basis')).toBe('unqualified');expect(url.searchParams.get('timeline_search')).toBe('合成');
    bound.revoke();await expect(accounts('SYN/source',{memberPath:'folder/table.csv',expectedRevision:'r',currency:'CNY'})).rejects.toMatchObject({code:'session_revoked'});expect(fetcher).toHaveBeenCalledTimes(2);
  });

});
