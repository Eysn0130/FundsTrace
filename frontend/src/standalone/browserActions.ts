import type { FundsApi } from '../lib/api';
import type { FundsCaseContextV1, FundsHostActionsV1 } from '../panel/host';

/** Browser navigation belongs to the standalone shell, never to FundsPanel. */
export function createBrowserFundsActions(context: FundsCaseContextV1, transport: FundsApi): FundsHostActionsV1 {
  const graphPath = `/?case_id=${encodeURIComponent(context.caseId)}`;
  return {
    openGraph: () => { window.location.assign(graphPath); },
    openLegacyImport: () => { window.location.assign(graphPath); },
    downloadSource: (source, signal) => transport.downloadSource(context.caseId, source.sourceId, source.filename, signal),
  };
}
