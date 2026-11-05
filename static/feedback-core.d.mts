export type FeedbackSeverity = 'success' | 'info' | 'warning' | 'error';
export type FeedbackTaskStatus = 'queued' | 'running' | 'unknown' | 'success' | 'warning' | 'error' | 'cancelled';
export interface FeedbackScope {
  readonly caseId?: string | number | null;
  readonly canvasId?: string | number | null;
  readonly generation?: string | number | null;
  readonly canvasGeneration?: string | number | null;
  readonly binding?: number;
}
export interface FeedbackAction { id: string; label: string; run: () => unknown }
export interface FeedbackNotificationInput {
  id?: string; dedupeKey?: string; scope?: FeedbackScope | null;
  severity: FeedbackSeverity; title: string; detail?: string; duration?: number; actions?: FeedbackAction[];
}
export interface FeedbackTaskInput {
  id: string; scope?: FeedbackScope | null; status: FeedbackTaskStatus; title: string; detail?: string;
  progress?: { done: number; total: number } | null; actions?: FeedbackAction[]; announce?: boolean;
}
export interface FeedbackItem {
  readonly id: string; readonly kind: 'notification' | 'task'; readonly title: string; readonly detail: string;
  readonly scope: FeedbackScope | null; readonly createdAt: number; readonly updatedAt: number;
  readonly actions: readonly Readonly<{ id: string; label: string }>[];
  readonly severity?: FeedbackSeverity; readonly status?: FeedbackTaskStatus; readonly count?: number;
  readonly progress?: Readonly<{ done: number; total: number }> | null; readonly dismissed?: boolean;
}
export interface FeedbackSnapshot {
  readonly scope: FeedbackScope;
  readonly toasts: readonly FeedbackItem[]; readonly tasks: readonly FeedbackItem[]; readonly activity: readonly FeedbackItem[];
}
export interface FeedbackStore {
  bind(scope: FeedbackScope | null): FeedbackScope;
  notify(input: FeedbackNotificationInput): string | false;
  task(input: FeedbackTaskInput): string | false;
  dismiss(id: string): boolean;
  revealTasks(): void;
  runAction(itemId: string, actionId: string): unknown;
  pause(reason?: string): void;
  resume(reason?: string): void;
  subscribe(listener: () => void): () => void;
  snapshot(): FeedbackSnapshot;
  dispose(): void;
}
export function createFeedbackStore(options?: {
  now?: () => number;
  schedule?: (callback: () => void, delay: number) => unknown;
  cancel?: (timer: unknown) => void;
}): FeedbackStore;
