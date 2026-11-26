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
    imports: (signal, importId) => run(signal, combined => transport.imports(caseId, combined, importId)),
    sources: (flowId, page = 1, signal) => run(signal, combined => transport.sources(caseId, flowId, page, combined)),
    cleanup: (dryRun, signal) => run(signal, combined => transport.cleanup(caseId, dryRun, combined)),
  };
  const previewDirectory = transport.previewDirectory;
  if (previewDirectory) client.previewDirectory = (files, signal) => run(signal, combined => previewDirectory(caseId, files, combined));
  const importModelTrial = transport.importModelTrial;
  if (importModelTrial) client.importModelTrial = (importId, body, signal) =>
    run(signal, combined => importModelTrial(caseId, importId, body, combined));
  const importReferenceTrial = transport.importReferenceTrial;
  if (importReferenceTrial) client.importReferenceTrial = (importId, body, signal) =>
    run(signal, combined => importReferenceTrial(caseId, importId, body, combined));
  const importColumnEvidence = transport.importColumnEvidence;
  if (importColumnEvidence) client.importColumnEvidence = (importId, body, signal) =>
    run(signal, combined => importColumnEvidence(caseId, importId, body, combined));
  const { intakeStatus, importIntake, importIntakePage, downloadIntakeMember } = transport;
  if (intakeStatus) client.intakeStatus = signal => run(signal, intakeStatus);
  if (importIntake) client.importIntake = (sourceId, signal) => run(signal, combined => importIntake(caseId, sourceId, combined));
  if (importIntakePage) client.importIntakePage = (sourceId, member, page, signal) =>
    run(signal, combined => importIntakePage(caseId, sourceId, member, page, combined));
  if (downloadIntakeMember) client.downloadIntakeMember = (sourceId, member, filename, signal) =>
    run(signal, combined => downloadIntakeMember(caseId, sourceId, member, filename, combined));
  const { importSourceMetadata, withdrawImportSource } = transport;
  if (importSourceMetadata) client.importSourceMetadata = (sourceId, signal) => run(signal, combined => importSourceMetadata(caseId, sourceId, combined));
  if (withdrawImportSource) client.withdrawImportSource = (sourceId, batchId, signal) => run(signal, combined => withdrawImportSource(caseId, sourceId, batchId, combined));
  const importRecords = transport.importRecords;
  if (importRecords) client.importRecords = (sourceId, page = 1, signal, memberPath, sourceRow, pdfPage) => run(signal, combined => {
    if (pdfPage !== undefined) return importRecords(caseId, sourceId, page, combined, memberPath, sourceRow, pdfPage);
    if (sourceRow !== undefined) return importRecords(caseId, sourceId, page, combined, memberPath, sourceRow);
    if (memberPath !== undefined) return importRecords(caseId, sourceId, page, combined, memberPath);
    return importRecords(caseId, sourceId, page, combined);
  });
  const { importReview, compareImportRows, importSignedAmounts } = transport;
  if (importReview) client.importReview = (sourceId, memberPath, query, signal) => run(signal, combined => importReview(caseId, sourceId, memberPath, query, combined));
  if (compareImportRows) client.compareImportRows = (sourceId, body, signal) =>
    run(signal, combined => compareImportRows(caseId, sourceId, body, combined));
  if (importSignedAmounts) client.importSignedAmounts = (sourceId, memberPath, signal) =>
    run(signal, combined => importSignedAmounts(caseId, sourceId, memberPath, combined));
  const { importFundsReport, verifyImportSource, importReportRecords, importReportAccounts } = transport;
  if (importFundsReport) client.importFundsReport = (sourceId, query, signal) => run(signal, combined => importFundsReport(caseId, sourceId, query, combined));
  if (verifyImportSource) client.verifyImportSource = (sourceId, query, signal) => run(signal, combined => verifyImportSource(caseId, sourceId, query, combined));
  if (importReportRecords) client.importReportRecords = (sourceId, query, signal) => run(signal, combined => importReportRecords(caseId, sourceId, query, combined));
  if (importReportAccounts) client.importReportAccounts = (sourceId, query, signal) => run(signal, combined => importReportAccounts(caseId, sourceId, query, combined));
  const boundActions: FundsHostActionsV1 = {};
  const { openGraph, openLegacyImport, downloadSource, downloadImportReport, downloadFundsReport, sourceCopy } = actions;
  if (openGraph) boundActions.openGraph = signal => run(signal, combined => openGraph(combined));
  if (openLegacyImport) boundActions.openLegacyImport = (importId, signal) => run(signal, combined => openLegacyImport(importId, combined));
  if (downloadSource) boundActions.downloadSource = (source, signal) => run(signal, combined => downloadSource(source, combined));
  if (downloadImportReport) boundActions.downloadImportReport = (source, signal) => run(signal, combined => downloadImportReport(source, combined));
  if (downloadFundsReport) boundActions.downloadFundsReport = (source, signal) => run(signal, combined => downloadFundsReport(source, combined));
  if (sourceCopy) boundActions.sourceCopy = (sourceId, action, signal) => run(signal, combined => sourceCopy(sourceId, action, combined));
  return {
    session: Object.freeze({ context: boundContext, scopeKey, generation, signal: lifetime.signal, client: Object.freeze(client), actions: Object.freeze(boundActions) }),
    revoke: () => lifetime.abort(),
  };
}
