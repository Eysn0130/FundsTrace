import { createContext, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
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

/** Task progress remains in its owning operation; this viewport renders short messages only. */
export function FeedbackViewport({ store }: { store: FeedbackStore }) {
  const snapshot = useSyncExternalStore(store.subscribe, store.snapshot, store.snapshot);
  const [modalBlocked, setModalBlocked] = useState(() => !!document.querySelector('dialog[open]'));
  const modalBlockedRef = useRef(modalBlocked), root = useRef<HTMLDivElement>(null);
  const blocked = () => modalBlockedRef.current || !!document.querySelector('dialog[open]');
  useEffect(() => {
    const visibility = () => document.hidden ? store.pause('document-hidden') : store.resume('document-hidden');
    const modal = (event?: Event) => {
      const detail: unknown = (event as CustomEvent | undefined)?.detail;
      const open = typeof detail === 'object' && detail !== null && 'open' in detail && typeof detail.open === 'boolean'
        ? detail.open : !!document.querySelector('dialog[open]');
      modalBlockedRef.current = open; setModalBlocked(open);
      if (open) { store.pause('modal'); store.resume('pointer'); store.resume('focus'); }
      else store.resume('modal');
    };
    const observer = new MutationObserver(() => modal());
    observer.observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open'], childList: true });
    document.addEventListener('visibilitychange', visibility); window.addEventListener('funds:feedback-modal', modal);
    visibility(); modal();
    return () => {
      observer.disconnect(); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('funds:feedback-modal', modal);
      for (const reason of ['document-hidden', 'modal', 'pointer', 'focus']) store.resume(reason);
    };
  }, [store]);
  useEffect(() => { if (!root.current?.contains(document.activeElement)) store.resume('focus'); }, [snapshot.toasts, store]);
  const actions = (item: FeedbackItem) => item.actions.map(action => <button key={action.id} className="ff-action" type="button" disabled={modalBlocked} onClick={() => { if (!blocked()) store.runAction(item.id, action.id); }}>{action.label}</button>);
  const label = { success: '成功', info: '提示', warning: '注意', error: '错误' };
  return <div ref={root} className="funds-feedback ff-root" hidden={modalBlocked} inert={modalBlocked}>
    <div className="ff-toast-stack" aria-label="操作消息" onMouseEnter={() => store.pause('pointer')} onMouseLeave={() => store.resume('pointer')} onFocus={() => store.pause('focus')} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) store.resume('focus'); }}>{snapshot.toasts.map(item => <article key={item.id} className="ff-toast" data-status={item.severity} role={item.severity === 'error' ? 'alert' : 'status'}><div className="ff-content"><div className="ff-item-heading"><span className="ff-state">{item.severity === 'success' && <img className="ff-icon" src="/static/ui-icons/check.svg" alt="" />}{label[item.severity ?? 'info']}</span><strong className="ff-title">{item.title}</strong></div>{item.detail && <p className="ff-detail">{item.detail}</p>}<div className="ff-actions">{actions(item)}</div></div><button className="ff-close" type="button" disabled={modalBlocked} aria-label={`关闭消息：${item.title}`} onClick={() => { if (!blocked()) store.dismiss(item.id); }}><img className="ff-icon" src="/static/ui-icons/x.svg" alt="" /></button></article>)}</div>
  </div>;
}
export type { FeedbackAction };
