import type { FundsApi } from '../lib/api';
import type { FundsCaseContextV1, FundsHostActionsV1 } from '../panel/host';

declare global {
  interface Window {
    FundsDesktopSources?: {
      prepare(source: { caseId: string; sourceId: string }): Promise<{ ticket: string; icon?: string }>;
      perform(command: { ticket: string; action: 'open' | 'reveal' }): Promise<void>;
      release(ticket: string): Promise<void>;
    };
  }
}

/** Browser navigation belongs to the standalone shell, never to FundsPanel. */
export function createBrowserFundsActions(context: FundsCaseContextV1, transport: FundsApi): FundsHostActionsV1 {
  const caseId = context.caseId, graphPath = `/?case_id=${encodeURIComponent(caseId)}`;
  const downloadFundsReport = transport.downloadFundsReport;
  return {
    openGraph: () => { window.location.assign(graphPath); },
    openLegacyImport: importId => { window.location.assign(`${graphPath}&import_id=${encodeURIComponent(importId)}`); },
    downloadSource: (source, signal) => transport.downloadSource(caseId, source.sourceId, source.filename, signal),
    downloadImportReport: (source, signal) => transport.downloadImportReport(caseId, source.sourceId, source.filename, signal),
    ...(downloadFundsReport ? { downloadFundsReport: (source, signal) => downloadFundsReport(caseId, source, signal) } satisfies Pick<FundsHostActionsV1, 'downloadFundsReport'> : {}),
    ...(window.FundsDesktopSources && window.parent === window ? { sourceCopy: async (sourceId: string, action: 'open' | 'reveal', signal?: AbortSignal) => {
      const native = window.FundsDesktopSources!;
      if (signal?.aborted) throw new DOMException('案件已切换', 'AbortError');
      const prepared = await native.prepare({ caseId, sourceId });
      if (signal?.aborted) { await native.release(prepared.ticket); throw new DOMException('案件已切换', 'AbortError'); }
      try { await native.perform({ ticket: prepared.ticket, action }); return { icon: prepared.icon }; }
      catch (error) { await native.release(prepared.ticket); throw error; }
    } } : {}),
  };
}
