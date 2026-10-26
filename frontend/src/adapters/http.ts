import type { FundsApi } from '../lib/api';
import type { FundsCaseContextV1, FundsHostActionsV1, FundsPanelSessionV1, FundsScopeBindingV1 } from '../panel/host';

export class FundsSessionRevokedError extends Error {
  readonly code = 'session_revoked';
  constructor() { super('案件会话已撤销，请使用当前工作区。'); this.name = 'AbortError'; }
}

/** Bind once; revocation prevents a captured old callback from starting work. */
export function createHttpFundsSession({ context, scopeKey, generation, transport, actions = {} }: {
  context: FundsCaseContextV1; scopeKey: string; generation: number; transport: FundsApi; actions?: FundsHostActionsV1;
}): { session: FundsPanelSessionV1; revoke: () => void } {
  if (typeof context.caseId !== 'string' || !context.caseId || typeof context.name !== 'string' || typeof scopeKey !== 'string' || !scopeKey || !Number.isSafeInteger(generation) || generation < 0) throw new Error('案件会话绑定无效。');
  const binding: FundsScopeBindingV1 = Object.freeze({ caseId: context.caseId, scopeKey, generation });
  const boundContext = Object.freeze({ caseId: context.caseId, name: context.name });
  const lifetime = new AbortController();
  const ensureActive = (signal?: AbortSignal) => {
    if (lifetime.signal.aborted) throw new FundsSessionRevokedError();
    if (signal?.aborted) throw new DOMException('操作已中止。', 'AbortError');
  };
  const run = async <T>(signal: AbortSignal | undefined, operation: (combined: AbortSignal) => T | Promise<T>): Promise<T> => {
    ensureActive(signal);
    const controller = new AbortController();
    const abort = () => controller.abort();
    lifetime.signal.addEventListener('abort', abort, { once: true });
    signal?.addEventListener('abort', abort, { once: true });
    try {
      // The binding is rechecked before dispatch and after asynchronous return.
      ensureActive(signal);
      const result = await operation(controller.signal);
      ensureActive(signal);
      return result;
    } finally {
      lifetime.signal.removeEventListener('abort', abort);
      signal?.removeEventListener('abort', abort);
    }
  };
  const caseId = binding.caseId;
  const client: FundsPanelSessionV1['client'] = {
    binding,
    stats: signal => run(signal, combined => transport.stats(caseId, combined)),
    flows: (filters, signal) => run(signal, combined => transport.flows(caseId, filters, combined)),
    preview: (file, signal) => run(signal, combined => transport.preview(caseId, file, combined)),
    repreview: (importId, body, signal) => run(signal, combined => transport.repreview(caseId, importId, body, combined)),
    commit: (importId, body, signal) => run(signal, combined => transport.commit(caseId, importId, body, combined)),
    job: (importId, signal) => run(signal, combined => transport.job(caseId, importId, combined)),
    imports: signal => run(signal, combined => transport.imports(caseId, combined)),
    sources: (flowId, page = 1, signal) => run(signal, combined => transport.sources(caseId, flowId, page, combined)),
    cleanup: (dryRun, signal) => run(signal, combined => transport.cleanup(caseId, dryRun, combined)),
  };
  const previewDirectory = transport.previewDirectory;
  if (previewDirectory) client.previewDirectory = (files, signal) => run(signal, combined => previewDirectory(caseId, files, combined));
  const boundActions: FundsHostActionsV1 = {};
  const { openGraph, openLegacyImport, downloadSource, downloadImportReport } = actions;
  if (openGraph) boundActions.openGraph = signal => run(signal, combined => openGraph(combined));
  if (openLegacyImport) boundActions.openLegacyImport = (importId, signal) => run(signal, combined => openLegacyImport(importId, combined));
  if (downloadSource) boundActions.downloadSource = (source, signal) => run(signal, combined => downloadSource(source, combined));
  if (downloadImportReport) boundActions.downloadImportReport = (source, signal) => run(signal, combined => downloadImportReport(source, combined));
  return {
    session: Object.freeze({ context: boundContext, scopeKey, generation, signal: lifetime.signal, client: Object.freeze(client), actions: Object.freeze(boundActions) }),
    revoke: () => lifetime.abort(),
  };
}
