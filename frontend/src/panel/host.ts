import type { CaseStats, CleanupResult, CommitRequest, FlowPage, FlowSourceList, ImportJob, ImportPreview, ImportSessionList, RepreviewRequest } from '../../../contracts/api-v1';

export const FUNDS_PANEL_VERSION = 'funds-panel.v1' as const;

export type FundsCaseContextV1 = Readonly<{ caseId: string; name: string }>;
export type FundsScopeBindingV1 = Readonly<{ caseId: string; scopeKey: string; generation: number }>;
export type FundsFlowFiltersV1 = Readonly<{ account: string; kw: string; page: number }>;

/** Case-bound use cases. File is a browser pilot input, not an IPC contract. */
export interface FundsCaseClientV1 {
  readonly binding: FundsScopeBindingV1;
  stats(signal?: AbortSignal): Promise<CaseStats>;
  flows(filters: FundsFlowFiltersV1, signal?: AbortSignal): Promise<FlowPage>;
  preview(file: File, signal?: AbortSignal): Promise<ImportPreview>;
  repreview(importId: string, body: RepreviewRequest, signal?: AbortSignal): Promise<ImportPreview>;
  commit(importId: string, body: CommitRequest, signal?: AbortSignal): Promise<{ case_id: string; import_id: string; started: boolean }>;
  job(importId: string, signal?: AbortSignal): Promise<ImportJob>;
  imports(signal?: AbortSignal): Promise<ImportSessionList>;
  sources(flowId: string, page?: number, signal?: AbortSignal): Promise<FlowSourceList>;
  cleanup(dryRun: boolean, signal?: AbortSignal): Promise<CleanupResult>;
}

export interface FundsHostActionsV1 {
  openGraph?(signal?: AbortSignal): void | Promise<void>;
  openLegacyImport?(importId: string, signal?: AbortSignal): void | Promise<void>;
  downloadSource?(source: Readonly<{ sourceId: string; filename: string }>, signal?: AbortSignal): Promise<void>;
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
