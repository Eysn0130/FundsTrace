import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { ImportJob, ImportPreview, ImportSession } from '../../../contracts/api-v1';
import { api, ApiError, type FundsApi } from '../lib/api';
import { createHttpFundsSession } from '../adapters/http';
import { createBrowserFundsActions } from './browserActions';
import { createFeedbackChannel, FeedbackProvider, type FundsFeedbackEvent } from '../feedback/Feedback';
import { ImportDialog } from '../features/ImportDialog';

interface CanvasImportOptions {
  caseId: string; name: string; generation: number; signal: AbortSignal; resume?: ImportSession;
  acquire(importId: string, surface: 'v1' | 'legacy'): Promise<void>;
  release(importId: string, jobId?: string, rejected?: boolean): Promise<void>;
  changed(): Promise<void>; feedback(event: FundsFeedbackEvent): boolean; close(): void;
}
function legacyApi(caseId: string): FundsApi {
  async function request<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`/api/import/${path}`, body === undefined ? { signal } : { method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const value = await response.json();
    if (!response.ok) throw new ApiError(response.headers.get('X-FundsTrace-Import-Error-Code') || 'request_failed', value.detail || '导入请求失败，请核对原任务。', response.status, response.headers.get('X-FundsTrace-Import-Failure-Kind') || 'unknown');
    if (value.case_id !== undefined && value.case_id !== caseId) throw new Error('导入回执与当前案件不一致。');
    return { ...value, case_id: caseId } as T;
  }
  return { ...api,
    repreview: (_id, importId, body, signal) => request<ImportPreview>('repreview', { ...body, case_id: caseId, import_id: importId }, signal),
    commit: (_id, importId, body, signal) => request('commit', { ...body, case_id: caseId, import_id: importId,
      self_accounts: body.self_accounts?.join(',') ?? '', mark_accounts: body.mark_accounts?.join(',') ?? '' }, signal),
    job: (_id, importId, signal) => request<ImportJob>(`progress?${new URLSearchParams({ case_id: caseId, import_id: importId })}`, undefined, signal),
  };
}
export function mountImport(element: HTMLElement, options: CanvasImportOptions) {
  const surface = options.resume?.surface ?? 'v1', source = surface === 'legacy' ? legacyApi(options.caseId) : api;
  const notified = new Set<string>();
  const transport: FundsApi = { ...source,
    commit: async (caseId, importId, body, signal) => {
      await options.acquire(importId, surface);
      try { return await source.commit(caseId, importId, body, signal); }
      catch (failure) {
        if (failure instanceof ApiError && failure.status !== undefined && failure.status >= 400 && failure.status < 500) {
          // Releasing still requires the parent's independent durable read.
          try { await options.release(importId, undefined, true); } catch { /* The parent keeps an unresolved lease. */ }
        }
        throw failure;
      }
    },
    job: async (caseId, importId, signal) => {
      const value = await source.job(caseId, importId, signal);
      if (value.case_id !== caseId || value.import_id !== importId) throw new Error('导入进度与当前案件不一致。');
      if (value.stage === 'done' || value.stage === 'error') {
        try {
          await options.release(importId, value.import_id);
          if (value.stage === 'done' && !notified.has(importId) && !signal?.aborted) { notified.add(importId); await options.changed(); }
        } catch {
          if (!signal?.aborted) options.feedback({ id: 'import-refresh:' + importId, kind: 'toast', operation: 'import', severity: 'warning',
            title: '导入回执已读取，界面刷新尚未完成', detail: '可以重新查询当前案件，原任务不会再次提交。' });
        }
      }
      return value;
    },
  };
  const binding = createHttpFundsSession({ context: { caseId: options.caseId, name: options.name }, scopeKey: `canvas-import:${options.caseId}:${options.generation}`,
    generation: options.generation, transport, actions: createBrowserFundsActions({ caseId: options.caseId, name: options.name }, api) });
  const revoke = () => binding.revoke(); options.signal.addEventListener('abort', revoke, { once: true });
  const channel = createFeedbackChannel({ scope: { caseId: options.caseId, generation: options.generation }, signal: binding.session.signal, publish: options.feedback });
  const root = createRoot(element); let settle: (id: string) => void = () => {};
  function View() {
    const [hint, setHint] = useState<{ importId: string; sequence: number }>();
    settle = importId => setHint(value => ({ importId, sequence: (value?.sequence ?? 0) + 1 }));
    return <FeedbackProvider port={channel.port}><ImportDialog target={binding.session.context} client={binding.session.client} scopeSignal={binding.session.signal}
      resume={options.resume} surface={surface} settlementHint={hint} actions={binding.session.actions} onClose={options.close} onImported={() => {}}
      /></FeedbackProvider>;
  }
  root.render(<View />);
  return { settled: (id: string) => { if (!options.signal.aborted) settle(id); }, dispose() { options.signal.removeEventListener('abort', revoke); binding.revoke(); channel.dispose(); root.unmount(); } };
}
