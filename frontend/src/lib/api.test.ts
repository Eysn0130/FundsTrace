import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';

afterEach(() => vi.unstubAllGlobals());
describe('HTTP failure status', () => {
  it.each([422, 500])('preserves HTTP %i without classifying network failures as a rejection', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status, json: async () => ({ error: { code: 'synthetic_failure', message: '合成失败' } }) }));
    await expect(api.commit('1', '0123456789ab', { mode: 'A', mapping: {}, mark_accounts: [] })).rejects.toMatchObject({ code: 'synthetic_failure', status });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Disconnected')));
    await expect(api.commit('1', '0123456789ab', { mode: 'A', mapping: {}, mark_accounts: [] })).rejects.toMatchObject({ code: 'network_error', status: undefined });
  });
});
