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
