import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { createHttpFundsSession } from '../adapters/http';

afterEach(() => vi.unstubAllGlobals());
describe('HTTP failure status', () => {
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
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status, json: async () => ({ error: { code: 'synthetic_failure', message: '合成失败' } }) }));
    await expect(api.commit('1', '0123456789ab', { mode: 'A', mapping: {}, mark_accounts: [] })).rejects.toMatchObject({ code: 'synthetic_failure', status });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Disconnected')));
    await expect(api.commit('1', '0123456789ab', { mode: 'A', mapping: {}, mark_accounts: [] })).rejects.toMatchObject({ code: 'network_error', status: undefined });
  });
});
