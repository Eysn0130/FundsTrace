import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';
import { createFeedbackStore, type FeedbackAction, type FeedbackItem, type FeedbackNotificationInput, type FeedbackScope, type FeedbackStore, type FeedbackTaskInput } from '../../../static/feedback-core.mjs';
import '../../../static/feedback.css';

export type FeedbackOperation = 'import' | 'account' | 'case' | 'canvas' | 'download' | 'analysis';
type Metadata = { operation?: FeedbackOperation; importId?: string };
export type FundsFeedbackEvent = Readonly<{ id: string; kind: 'toast' | 'task'; operation: FeedbackOperation; status?: FeedbackTaskInput['status']; severity?: FeedbackNotificationInput['severity']; title: string; detail?: string; progress?: { done: number; total: number }; importId?: string }>;
export interface FundsFeedbackPort {
  notify(input: Omit<FeedbackNotificationInput, 'scope'> & Metadata): void;
  task(input: Omit<FeedbackTaskInput, 'scope'> & Metadata): void;
}
const emptyPort: FundsFeedbackPort = { notify() {}, task() {} };
const FeedbackContext = createContext<FundsFeedbackPort>(emptyPort);
export const useFeedback = () => useContext(FeedbackContext);
export function FeedbackProvider({ port, children }: { port: FundsFeedbackPort; children: ReactNode }) { return <FeedbackContext.Provider value={port}>{children}</FeedbackContext.Provider>; }

/** A transport port captures one session; feedback never acquires or releases business leases. */
export function createFeedbackChannel({ scope, signal, publish }: { scope: FeedbackScope; signal: AbortSignal; publish?: (event: FundsFeedbackEvent) => boolean }) {
  const store = createFeedbackStore();
  const binding = store.bind(scope);
  let disposed = false;
  const current = () => !disposed && !signal.aborted;
  const port: FundsFeedbackPort = {
    notify(input) {
      if (!current()) return;
      const id = input.id ?? crypto.randomUUID();
      if (publish) publish({ id, kind: 'toast', operation: input.operation ?? 'analysis', severity: input.severity, title: input.title, detail: input.detail, importId: input.importId });
      else store.notify({ ...input, id, scope: binding });
    },
    task(input) {
      if (!current()) return;
      if (publish) publish({ id: input.id, kind: 'task', operation: input.operation ?? 'analysis', status: input.status, title: input.title, detail: input.detail, progress: input.progress ?? undefined, importId: input.importId });
      else store.task({ ...input, scope: binding });
    },
  };
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    // Revoke the visible binding while subscribers still exist, then release it.
    store.bind(null); store.dispose(); signal.removeEventListener('abort', dispose);
  };
  signal.addEventListener('abort', dispose, { once: true });
  if (signal.aborted) dispose();
  return { port, store, dispose };
}

export function FeedbackBoundary({ port, scope, signal, children }: { port?: FundsFeedbackPort; scope: FeedbackScope; signal: AbortSignal; children: ReactNode }) {
  const [local, setLocal] = useState<ReturnType<typeof createFeedbackChannel> | null>(null);
  const guardedPort = useMemo<FundsFeedbackPort | undefined>(() => port ? {
    notify: input => { if (!signal.aborted) port.notify(input); },
    task: input => { if (!signal.aborted) port.task(input); },
  } : undefined, [port, signal]);
  useEffect(() => {
    if (port || signal.aborted) { setLocal(null); return; }
    const channel = createFeedbackChannel({ scope, signal }); setLocal(channel);
    return channel.dispose;
  }, [port, scope.caseId, scope.generation, signal]);
  return <FeedbackProvider port={guardedPort ?? local?.port ?? emptyPort}>{children}{!port && local && !signal.aborted && <FeedbackViewport store={local.store} />}</FeedbackProvider>;
}

const taskLabel = (status?: FeedbackTaskInput['status']) => ({ queued: '等待处理', running: '进行中', unknown: '结果待核对', success: '已完成', warning: '需留意', error: '未完成', cancelled: '已取消' })[status ?? 'running'];
export function FeedbackViewport({ store }: { store: FeedbackStore }) {
  const snapshot = useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot);
  const [expanded, setExpanded] = useState(true);
  useEffect(() => {
    const visibility = () => document.hidden ? store.pause('document-hidden') : store.resume('document-hidden');
    document.addEventListener('visibilitychange', visibility); visibility();
    return () => { document.removeEventListener('visibilitychange', visibility); store.resume('document-hidden'); };
  }, [store]);
  const actions = (item: FeedbackItem) => item.actions.map(action => <button key={action.id} className="ff-action" type="button" onClick={() => store.runAction(item.id, action.id)}>{action.label}</button>);
  const activeCount = snapshot.tasks.filter(item => ['queued', 'running'].includes(item.status ?? '')).length;
  const unknownCount = snapshot.tasks.filter(item => item.status === 'unknown').length;
  return <div className="funds-feedback ff-root">
    <div className="ff-toast-stack" aria-label="操作消息" onMouseEnter={() => store.pause('pointer')} onMouseLeave={() => store.resume('pointer')} onFocus={() => store.pause('focus')} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) store.resume('focus'); }}>{snapshot.toasts.map(item => <article key={item.id} className="ff-toast" data-status={item.severity} role={item.severity === 'error' ? 'alert' : 'status'}><div className="ff-content"><strong className="ff-title">{item.title}</strong>{item.detail && <p className="ff-detail">{item.detail}</p>}<div className="ff-actions">{actions(item)}</div></div><button className="ff-close" type="button" aria-label={`关闭消息：${item.title}`} onClick={() => store.dismiss(item.id)}>×</button></article>)}</div>
    {snapshot.tasks.length > 0 && <section className="ff-dock" data-expanded={expanded} aria-label="持续任务"><div className="ff-dock-head"><button type="button" className="ff-dock-entry" aria-expanded={expanded} onClick={() => { store.revealTasks(); setExpanded(value => !value); }}><span className="ff-dock-title">任务 · {snapshot.tasks.length}</span><span className="ff-summary">{activeCount} 项进行中{unknownCount > 0 ? ` · ${unknownCount} 项结果待核对` : ''}</span></button><button type="button" className="ff-dock-toggle" aria-expanded={expanded} onClick={() => { if (!expanded) store.revealTasks(); setExpanded(value => !value); }}>{expanded ? '收起任务' : '展开任务'}</button></div><div className="ff-dock-body" hidden={!expanded}><div className="ff-task-list">{snapshot.tasks.filter(item => !item.dismissed).map(item => <article key={item.id} className="ff-task" data-status={item.status}><div className="ff-content"><div className="ff-item-heading"><strong className="ff-title">{item.title}</strong><span className="ff-state">{taskLabel(item.status)}</span></div>{item.detail && <p className="ff-detail">{item.detail}</p>}{['queued', 'running'].includes(item.status ?? '') && <progress className="ff-progress" aria-label={`${item.title}进度`} {...(item.progress && item.progress.total > 0 ? { value: item.progress.done, max: item.progress.total } : {})} />}<div className="ff-actions">{actions(item)}{!['queued', 'running', 'unknown'].includes(item.status ?? '') && <button className="ff-action" type="button" onClick={() => store.dismiss(item.id)}>关闭记录</button>}</div></div></article>)}</div></div></section>}
  </div>;
}
export type { FeedbackAction };
