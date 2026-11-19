import type { CaseStats, CleanupResult, CommitRequest, FlowPage, FlowSourceList, ImportJob, ImportPreview, ImportSourceMetadata, ImportRecordPage, ImportReviewPage, ImportReport, ImportReportIntegrity, ImportReportRecords, ImportReportAccountOptions, ImportSessionList, RepreviewRequest, WithdrawnImport } from '../../../contracts/api-v1';

export const FUNDS_PANEL_VERSION = 'funds-panel.v1' as const;

export type FundsCaseContextV1 = Readonly<{ caseId: string; name: string }>;
export type FundsScopeBindingV1 = Readonly<{ caseId: string; scopeKey: string; generation: number }>;
export type FundsFlowFiltersV1 = Readonly<{ account: string; kw: string; page: number }>;
export type FundsTrendSelectionV1 = Readonly<{ timelineSearch?: string; timelineScope?: 'all' | 'selected' | 'except'; timelineAccounts?: readonly string[] }>;
export type FundsReportQueryV1 = FundsTrendSelectionV1 & Readonly<{ memberPath: string; currency?: string; account?: string; peerAccount?: string; timelineGrain?: 'auto' | 'day' | 'month' | 'year'; timelinePage?: number; page?: number; accountsPage?: number; relationsPage?: number; expectedRevision?: string }>;
export type FundsReportRecordsQueryV1 = FundsReportQueryV1 & Readonly<{ expectedRevision: string; counterparty?: string; direction?: 'in' | 'out'; timeStart?: string; timeEnd?: string; timeBasis?: 'utc' | 'unqualified' }>;
export type FundsReportAccountQueryV1 = Readonly<{ memberPath: string; expectedRevision: string; currency: string; account?: string; search?: string; page?: number }>;
export type FundsReportDownloadV1 = FundsReportQueryV1 & Readonly<{ sourceId: string; filename: string; expectedRevision: string }>;
export type FundsImportReviewQueryV1 = Readonly<{ view: ImportReviewPage['view']; page: number; issueIndex?: number; sourceRow?: number }>;

/** Case-bound use cases. File is a browser pilot input, not an IPC contract. */
export interface FundsCaseClientV1 {
  readonly binding: FundsScopeBindingV1;
  stats(signal?: AbortSignal): Promise<CaseStats>;
  flows(filters: FundsFlowFiltersV1, signal?: AbortSignal): Promise<FlowPage>;
  preview(file: File, signal?: AbortSignal): Promise<ImportPreview>;
  previewDirectory?(files: File[], signal?: AbortSignal): Promise<ImportPreview>;
  repreview(importId: string, body: RepreviewRequest, signal?: AbortSignal): Promise<ImportPreview>;
  commit(importId: string, body: CommitRequest, signal?: AbortSignal): Promise<{ case_id: string; import_id: string; started: boolean }>;
  job(importId: string, signal?: AbortSignal): Promise<ImportJob>;
  importSourceMetadata?(sourceId: string, signal?: AbortSignal): Promise<ImportSourceMetadata>;
  withdrawImportSource?(sourceId: string, batchId: string, signal?: AbortSignal): Promise<WithdrawnImport>;
  imports(signal?: AbortSignal, importId?: string): Promise<ImportSessionList>;
  importRecords?(sourceId: string, page?: number, signal?: AbortSignal, memberPath?: string, sourceRow?: number): Promise<ImportRecordPage>;
  importReview?(sourceId: string, memberPath: string, query: FundsImportReviewQueryV1, signal?: AbortSignal): Promise<ImportReviewPage>;
  importFundsReport?(sourceId: string, query: FundsReportQueryV1, signal?: AbortSignal): Promise<ImportReport>;
  verifyImportSource?(sourceId: string, query: Readonly<{ memberPath: string; expectedRevision: string }>, signal?: AbortSignal): Promise<ImportReportIntegrity>;
  importReportRecords?(sourceId: string, query: FundsReportRecordsQueryV1, signal?: AbortSignal): Promise<ImportReportRecords>;
  importReportAccounts?(sourceId: string, query: FundsReportAccountQueryV1, signal?: AbortSignal): Promise<ImportReportAccountOptions>;
  sources(flowId: string, page?: number, signal?: AbortSignal): Promise<FlowSourceList>;
  cleanup(dryRun: boolean, signal?: AbortSignal): Promise<CleanupResult>;
}

export interface FundsHostActionsV1 {
  openGraph?(signal?: AbortSignal): void | Promise<void>;
  openLegacyImport?(importId: string, signal?: AbortSignal): void | Promise<void>;
  downloadSource?(source: Readonly<{ sourceId: string; filename: string }>, signal?: AbortSignal): Promise<void>;
  downloadImportReport?(source: Readonly<{ sourceId: string; filename: string }>, signal?: AbortSignal): Promise<void>;
  downloadFundsReport?(source: FundsReportDownloadV1, signal?: AbortSignal): Promise<void>;
  sourceCopy?(sourceId: string, action: 'open' | 'reveal', signal?: AbortSignal): Promise<{ icon?: string }>;
}

export interface FundsPanelSessionV1 {
  readonly context: FundsCaseContextV1;
  readonly scopeKey: string;
  readonly generation: number;
  readonly signal: AbortSignal;
  readonly client: FundsCaseClientV1;
  readonly actions?: FundsHostActionsV1;
}

export interface FundsPanelHostV1 {
  readonly version: typeof FUNDS_PANEL_VERSION;
  readonly session: FundsPanelSessionV1 | null;
}
