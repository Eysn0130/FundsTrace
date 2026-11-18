import { afterEach, expect, it, vi } from 'vitest';
import type { FundsApi } from '../lib/api';
import { createBrowserFundsActions } from './browserActions';

afterEach(() => vi.unstubAllGlobals());

it('keeps the legacy import ID and case binding in browser navigation', async () => {
  const assign = vi.fn();
  vi.stubGlobal('window', { location: { assign } });
  const actions = createBrowserFundsActions({ caseId: '17', name: 'Synthetic case' }, {} as FundsApi);
  await actions.openLegacyImport!('0123456789abcdef0123456789abcdef');
  expect(assign).toHaveBeenCalledWith('/?case_id=17&import_id=0123456789abcdef0123456789abcdef');
  await actions.openGraph!();
  expect(assign).toHaveBeenLastCalledWith('/?case_id=17');
});
it('exposes funds report export only when supplied and forwards its exact selected scope', async () => {
  const document = vi.fn(async () => {});
  vi.stubGlobal('window', { location: { assign: vi.fn() } });
  const absent = createBrowserFundsActions({ caseId: '17', name: 'Synthetic case' }, {} as FundsApi);
  expect(absent.downloadFundsReport).toBeUndefined();
  const context = { caseId: '17', name: 'Synthetic case' };
  const actions = createBrowserFundsActions(context, { downloadFundsReport: document } as unknown as FundsApi);
  context.caseId = '18';
  const source = { sourceId: 'source', filename: 'synthetic.html', memberPath: 'nested/data.csv', expectedRevision: '11', account: '0001', currency: 'CNY', page: 2 }, signal = new AbortController().signal;
  await actions.downloadFundsReport!(source, signal);
  expect(document).toHaveBeenCalledWith('17', source, signal);
});
it('does not dispatch an OS open after the case-bound action is aborted during preparation', async () => {
  let finish!: (value: { ticket: string }) => void;
  const native = { prepare: vi.fn(() => new Promise<{ ticket: string }>(resolve => { finish = resolve; })), perform: vi.fn(), release: vi.fn(async () => {}) };
  const ownWindow: Record<string, unknown> = { FundsDesktopSources: native }; ownWindow.parent = ownWindow;
  vi.stubGlobal('window', ownWindow);
  const actions = createBrowserFundsActions({ caseId: '17', name: 'Synthetic case' }, {} as FundsApi), controller = new AbortController();
  const result = actions.sourceCopy!('0123456789ab', 'open', controller.signal);
  controller.abort(); finish({ ticket: 'synthetic-ticket' });
  await expect(result).rejects.toMatchObject({ name: 'AbortError' });
  expect(native.release).toHaveBeenCalledWith('synthetic-ticket'); expect(native.perform).not.toHaveBeenCalled();
});
