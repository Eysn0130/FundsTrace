import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());
describe('HTTP failure status', () => {
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
