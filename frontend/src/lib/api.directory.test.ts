import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
afterEach(() => vi.unstubAllGlobals());
describe('directory transport', () => {
  it('sends all bytes under their relative names to the case-bound directory endpoint', async () => {
    const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ case_id: '1' }) })); vi.stubGlobal('fetch', fetch);
    const file = new File(['synthetic'], '账户.csv'); Object.defineProperty(file, 'webkitRelativePath', { value: '资料/银行/账户.csv' });
    const signal = new AbortController().signal;
    await api.previewDirectory('1', [file, new File(['flow'], '资料/流水.csv')], signal);
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/v1/cases/1/imports/directory/preview'); expect(init.signal).toBe(signal);
    const sent = (init.body as FormData).getAll('files') as File[];
    expect(sent.map(value => value.name)).toEqual(['资料/银行/账户.csv', '资料/流水.csv']);
    expect(sent.map(value => value.size)).toEqual([9, 4]);
  });
});
