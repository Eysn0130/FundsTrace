import { afterEach, describe, expect, it, vi } from 'vitest';
import { accountRegistryApi } from './api';

const caseId = '9007199254740993'; const account = '0009007199254740994';
const row = { account, name: '合成主体', id_no: null, identity_updated_at: null, photo_at: null, is_main: false, main_source: null, main_marked_at: null, has_flows: true };
const accountPage = { case_id: caseId, source: 'identity', total: 1, page: 2, page_size: 30, items: [row] };
const accountProfile = { case_id: caseId, ...row, photo: null, cnt: 1, in_cnt: 1, out_cnt: 0, in_totals: [{ currency: 'CNY', amount: '9999999999999999.99' }], out_totals: [], in_amt: '9999999999999999.99', out_amt: '0.00', tmin: null, tmax: null, top_counterparties: [] };
afterEach(() => vi.restoreAllMocks());
describe('account registry read adapter', () => {
  it('sends only GET reads and preserves string account/case IDs and encoded search terms', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async path => new Response(JSON.stringify(String(path).includes('/profile?') ? accountProfile : accountPage), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const signal = new AbortController().signal;
    await accountRegistryApi.list('9007199254740993', { source: 'identity', q: '合成 & 主体', page: 2, pageSize: 30 }, signal);
    expect(fetch).toHaveBeenLastCalledWith('/api/account-registry?case_id=9007199254740993&source=identity&q=%E5%90%88%E6%88%90+%26+%E4%B8%BB%E4%BD%93&page=2&page_size=30', { signal });
    const profile = await accountRegistryApi.profile('9007199254740993', '0009007199254740994', signal);
    expect(fetch).toHaveBeenLastCalledWith('/api/account-registry/profile?case_id=9007199254740993&account=0009007199254740994', { signal });
    expect(profile.in_totals[0].amount).toBe('9999999999999999.99');
  });
  it('includes the provided refresh nonce in photo URLs and reports unavailable profiles', async () => {
    const url = new URL(accountRegistryApi.photoUrl('1', '000&synthetic', 'same-second:revision:2', true), 'http://localhost');
    expect(url.pathname).toBe('/api/account-photos/orig'); expect(url.searchParams.get('account')).toBe('000&synthetic'); expect(url.searchParams.get('v')).toBe('same-second:revision:2');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: '合成账户不存在' }), { status: 404 }));
    await expect(accountRegistryApi.profile('1', 'missing', new AbortController().signal)).rejects.toThrow('合成账户不存在');
  });
  it('marks an explicit account pick as exact rather than a contains search', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...accountPage, page: 1 }), { status: 200 }));
    const signal = new AbortController().signal;
    await accountRegistryApi.list(caseId, { source: 'identity', q: account, page: 1, pageSize: 30, accountExact: true }, signal);
    expect(fetch).toHaveBeenLastCalledWith(`/api/account-registry?case_id=${caseId}&source=identity&q=${account}&page=1&page_size=30&account_exact=true`, { signal });
  });
  it('rejects a contains result returned for an exact account pick', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ...accountPage, page: 1, total: 2, items: [row, { ...row, account: `${account}0` }] }), { status: 200 }));
    await expect(accountRegistryApi.list(caseId, { source: 'identity', q: account, page: 1, pageSize: 30, accountExact: true }, new AbortController().signal)).rejects.toThrow('账户列表响应不完整');
  });
  it('reads bounded suggestions without profile aggregation and preserves complete identifiers', async () => {
    const response = { case_id: caseId, source: 'flows', query: '合成', items: [{ account, name: '合成主体', id_no: '00000020000101000X', matched_field: 'name', matched_text: '合成主体' }], has_more: false, next_offset: null };
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(response), { status: 200 }));
    const signal = new AbortController().signal;
    const result = await accountRegistryApi.suggestions!(caseId, { source: 'flows', q: ' 合成 ', offset: 0, limit: 30 }, signal);
    expect(result.items[0].account).toBe(account); expect(result.items[0].id_no).toBe('00000020000101000X');
    expect(fetch).toHaveBeenLastCalledWith(`/api/account-suggestions?case_id=${caseId}&source=flows&q=+%E5%90%88%E6%88%90+&offset=0&page_size=30`, { signal });
  });
  it.each([
    { case_id: 'foreign' }, { source: 'identity' }, { query: 'other' }, { has_more: true, next_offset: 1 },
    { next_offset: 30 }, { items: [{ account: 123, name: '合成', id_no: null, matched_field: 'account', matched_text: '123' }] },
    { items: [{ account, name: '合成', id_no: 123, matched_field: 'name', matched_text: '合成' }] },
    { items: [{ account, name: '合成', id_no: null, matched_field: 'unknown', matched_text: '合成' }] },
    { items: Array.from({ length: 2 }, () => ({ account, name: '合成', id_no: null, matched_field: 'name', matched_text: '合成' })) },
  ])('rejects malformed or foreign suggestion receipts %#', async overrides => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ case_id: caseId, source: 'flows', query: '合成', items: [], has_more: false, next_offset: null, ...overrides }), { status: 200 }));
    await expect(accountRegistryApi.suggestions!(caseId, { source: 'flows', q: '合成', offset: 0, limit: 30 }, new AbortController().signal)).rejects.toThrow('账户候选响应不完整');
  });
  it.each([
    { ...accountPage, items: null },
    { ...accountPage, case_id: 9007199254740993 },
    { ...accountPage, items: [null] },
    { ...accountPage, items: [{ ...row, account: 123 }] },
    { ...accountPage, items: [{ ...row, has_flows: 'yes' }] },
    { ...accountPage, page_size: 0 },
  ])('rejects malformed list receipt %# before exposing its rows', async body => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
    await expect(accountRegistryApi.list(caseId, { source: 'identity', q: '', page: 2, pageSize: 30 }, new AbortController().signal)).rejects.toThrow(/账户列表响应不完整/);
  });
  it.each([
    { ...accountProfile, in_totals: [{ currency: 'CNY', amount: 100.23 }] },
    { ...accountProfile, out_amt: 100.23 },
    { ...accountProfile, id_no: 123456 },
    { ...accountProfile, top_counterparties: null },
    { ...accountProfile, photo: { updated_at: 'synthetic', crop_cx: null, crop_cy: 0.5, crop_r: 0.5 } },
  ])('rejects malformed profile receipt %# without converting money or identifiers', async body => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status: 200 }));
    await expect(accountRegistryApi.profile(caseId, account, new AbortController().signal)).rejects.toThrow(/账户资料响应不完整/);
  });
});
