import type { ImportJob, ImportPreview, ImportSession } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsCaseContextV1 } from '../panel/host';
import type { FundsFeedbackPort } from '../feedback/Feedback';

export type ImportPhase = 'queued' | 'uploading' | 'previewing' | 'ready' | 'committing' | 'polling' | 'done' | 'failed' | 'unknown' | 'skipped';
export interface ImportItem {
  id: string; filename: string; directory: boolean; phase: ImportPhase; importId: string | null;
  preview: ImportPreview | null; job: ImportJob | null; resume: ImportSession | null;
  mode: 'A' | 'B'; mapping: Record<string, string>; marked: string[]; selfAccounts: string[]; selfAccountsText: string;
  referencePolicy: boolean; dirty: boolean; error: string;
  referenceReview?: { message: string; positions?: [number, number] };
  materialGroupId?: string;
  sizeBytes?: number; processingMs?: number; loadingMembers?: boolean; queryingOnly?: boolean;
}
export interface ImportState { items: ImportItem[]; selectedId: string | null; reading: boolean; running: boolean; stopped: boolean; error: string; withdrawing: boolean; dataVersion: number }
export function hasFixedPdfLayout(preview: ImportPreview | null | undefined): boolean;
export interface ImportWorkflow {
  snapshot(): ImportState; subscribe(listener: () => void): () => void; start(): Promise<void>;
  upload(read: (signal: AbortSignal) => File[] | Promise<File[]>, directory?: boolean): Promise<void>;
  append(read: (signal: AbortSignal) => File[] | Promise<File[]>, directory?: boolean): Promise<void>;
  remove(id: string): boolean;
  withdraw(id: string, batchId: string): Promise<boolean>;
  select(id: string): void; stop(message?: string): void; dispose(): void;
  acceptAssisted(id: string, revision: string, value: ImportPreview): void;
  reviewReference(id: string): Promise<void>;
  edit(id: string, change: Partial<Pick<ImportItem, 'mode' | 'mapping' | 'marked' | 'selfAccounts' | 'selfAccountsText' | 'referencePolicy'>>): void;
  commit(id: string): Promise<void>; query(id: string): Promise<void>; repreview(id: string): Promise<void>; settled(importId: string): void;
}
export function createImportWorkflow(options: { target: FundsCaseContextV1; client: FundsCaseClientV1; signal: AbortSignal;
  resume?: ImportSession; surface?: 'v1' | 'legacy'; onImported?: () => void | Promise<void>; onTask?: FundsFeedbackPort['task'] }): ImportWorkflow;
