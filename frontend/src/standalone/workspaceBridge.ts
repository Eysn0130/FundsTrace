import type { AccountMutation, UnresolvedAccountOperation } from '../accounts/types';
export const WORKSPACE_CHANNEL = 'fundstrace:workspace' as const;
export const WORKSPACE_VERSION = 1 as const;

export type PendingImport = Readonly<{ importId: string; phase: 'pending' | 'unknown' }>;
export type WorkspaceContext = Readonly<{ caseId: string; name: string; generation: number; retainedCaseIds: string[]; surface?: 'cases' | 'accounts'; active?: boolean; unresolvedOperations?: UnresolvedAccountOperation[]; pendingImports?: PendingImport[] }>;
type Requests = {
  'select-case': { caseId: string };
  'open-graph': Record<string, never>;
  'import-acquire': { importId: string };
  'import-release': { importId: string; jobId?: string; rejected?: boolean };
  'data-changed': Record<string, never>;
  'case-action': { action: 'import' | 'export' | 'delete' };
  'account-action': { action: 'main-accounts' | 'import-identity' | 'download-template' } | { action: 'view-flows'; account: string } | { action: 'mutate'; mutation: AccountMutation } | { action: 'reconcile'; operationId: string };
};
export interface WorkspaceBridge {
  readonly context: WorkspaceContext | null;
  request<A extends keyof Requests>(action: A, payload: Requests[A], signal?: AbortSignal): Promise<void>;
  dispose(): void;
}

const abortError = () => new DOMException('案件工作区已切换。', 'AbortError');
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const identifier = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
function validContext(value: Record<string, unknown>): value is Record<string, unknown> & WorkspaceContext {
  return identifier(value.caseId) && typeof value.name === 'string' && Number.isSafeInteger(value.generation) && (value.generation as number) >= 0
    && Array.isArray(value.retainedCaseIds) && value.retainedCaseIds.every(identifier)
    && (value.surface === undefined || value.surface === 'cases' || value.surface === 'accounts')
    && (value.active === undefined || typeof value.active === 'boolean')
    && (value.pendingImports === undefined || Array.isArray(value.pendingImports) && value.pendingImports.every(item => record(item) && identifier(item.importId) && ['pending', 'unknown'].includes(String(item.phase))))
    && (value.unresolvedOperations === undefined || Array.isArray(value.unresolvedOperations) && value.unresolvedOperations.every(item => record(item) && identifier(item.operationId) && identifier(item.account) && ['identity-set','photo-set','photo-clear','photo-crop','main-set'].includes(String(item.kind))));
}

/** Only the same-origin, direct parent may bind this case workspace. */
export function createWorkspaceBridge({ onContext, onCasesChanged, onAccountsChanged, onDataChanged, target = window }: {
  onContext: (context: WorkspaceContext) => void;
  onCasesChanged: () => void;
  onAccountsChanged?: () => void;
  onDataChanged?: () => void;
  target?: Window;
}): WorkspaceBridge {
  const parent = target.parent;
  const origin = target.location.origin;
  let context: WorkspaceContext | null = null;
  const instanceId = crypto.randomUUID();
  let sequence = 0;
  let disposed = false;
  const pending = new Map<string, { context: WorkspaceContext; selectedCase?: string; resolve: () => void; reject: (error: Error) => void; cleanup: () => void }>();
  const send = (message: Record<string, unknown>) => parent.postMessage({ channel: WORKSPACE_CHANNEL, version: WORKSPACE_VERSION, ...message }, origin);
  const settle = (id: string, error?: Error) => {
    const item = pending.get(id);
    if (!item) return;
    pending.delete(id); item.cleanup();
    if (error) item.reject(error); else item.resolve();
  };
  const receive = (event: MessageEvent) => {
    if (disposed || event.source !== parent || event.origin !== origin || !record(event.data)) return;
    const data = event.data;
    if (data.channel !== WORKSPACE_CHANNEL || data.version !== WORKSPACE_VERSION) return;
    if (data.type === 'context' && validContext(data)) {
      if (context && (data.generation < context.generation || (data.generation === context.generation && (data.caseId !== context.caseId || data.name !== context.name)))) return;
      const next = { caseId: data.caseId, name: data.name, generation: data.generation, retainedCaseIds: [...data.retainedCaseIds], surface: data.surface, active: data.active, unresolvedOperations: data.unresolvedOperations?.map(item => ({...item})), pendingImports: data.pendingImports?.map(item => ({...item})) };
      const changed = !context || next.generation !== context.generation || next.caseId !== context.caseId || next.name !== context.name;
      context = next;
      if (changed) {
        for (const [id, item] of pending) settle(id, item.selectedCase === next.caseId ? undefined : abortError());
      }
      onContext(next);
    } else if (data.type === 'result' && identifier(data.requestId) && typeof data.ok === 'boolean' && (data.error === undefined || typeof data.error === 'string')) {
      const item = pending.get(data.requestId);
      if (!item || item.context.caseId !== context?.caseId || item.context.generation !== context.generation) return;
      if (data.ok && item.selectedCase && item.selectedCase !== context.caseId) return; // The parent context, not an acknowledgement, selects a case.
      settle(data.requestId, data.ok ? undefined : Object.assign(new Error(typeof data.error === 'string' ? data.error : '操作未完成，请重试。'), { code: typeof data.code === 'string' ? data.code : undefined, operationId: typeof data.operationId === 'string' ? data.operationId : undefined }));
    } else if (data.type === 'cases-changed') onCasesChanged();
    else if (data.type === 'accounts-changed' && data.caseId === context?.caseId && data.generation === context?.generation) onAccountsChanged?.();
    else if (data.type === 'data-changed' && data.caseId === context?.caseId && data.generation === context?.generation) onDataChanged?.();
  };
  target.addEventListener('message', receive);
  send({ type: 'ready' });
  return {
    get context() { return context; },
    request(action, payload, signal) {
      if (disposed || !context || signal?.aborted) return Promise.reject(abortError());
      const binding = context;
      const requestId = `workspace-${instanceId}-${++sequence}`;
      return new Promise<void>((resolve, reject) => {
        const abort = () => settle(requestId, abortError());
        pending.set(requestId, { context: binding, selectedCase: action === 'select-case' ? (payload as Requests['select-case']).caseId : undefined,
          resolve, reject, cleanup: () => signal?.removeEventListener('abort', abort) });
        signal?.addEventListener('abort', abort, { once: true });
        try { send({ type: 'request', requestId, caseId: binding.caseId, generation: binding.generation, action, payload }); }
        catch (error) { settle(requestId, error instanceof Error ? error : new Error('无法连接案件工作区。')); }
      });
    },
    dispose() {
      disposed = true; target.removeEventListener('message', receive);
      for (const id of pending.keys()) settle(id, abortError());
    },
  };
}
