import { useEffect, useRef, useState } from 'react';
import type { CleanupResult, ImportSession } from '../../../contracts/api-v1';
import { Modal } from '../components/Modal';
import { displayOperationalTime } from '../lib/format';
import type { FundsCaseClientV1, FundsCaseContextV1, FundsHostActionsV1 } from '../panel/host';
import { linkAbortSignal } from '../panel/cancellation';

const stages = { ready: '等待确认', parsing: '正在解析', importing: '正在写入', done: '已完成', error: '未完成' };

export function ImportHistoryDialog({ target, client, scopeSignal, actions, onClose, onOpen }: {
  target: FundsCaseContextV1; client: FundsCaseClientV1; scopeSignal: AbortSignal; actions?: FundsHostActionsV1; onClose: () => void; onOpen: (session: ImportSession) => void;
}) {
  const [items, setItems] = useState<ImportSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [cleanup, setCleanup] = useState<CleanupResult | null>(null);
  const [cleanBusy, setCleanBusy] = useState(false);
  const [cleanError, setCleanError] = useState('');
  const [actionError, setActionError] = useState('');
  const [actionBusy, setActionBusy] = useState('');
  const lifetime = useRef(new AbortController());
  useEffect(() => {
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
    const controller = lifetime.current;
    const unlink = linkAbortSignal(scopeSignal, controller);
    return () => { unlink(); controller.abort(); };
  }, [scopeSignal]);
  useEffect(() => {
    const controller = new AbortController(); let active = true; let timer: ReturnType<typeof setTimeout>;
    const unlink = linkAbortSignal(scopeSignal, controller);
    setLoading(true); setItems([]); setError('');
    const load = async () => {
      if (!active || controller.signal.aborted) return;
      try {
        const value = await client.imports(controller.signal);
        if (!active || controller.signal.aborted) return;
        if (value.case_id !== target.caseId || value.items.some(item => item.case_id !== target.caseId)) throw new Error('导入记录与当前案件不一致。');
        setItems(value.items); setError('');
        if (value.items.some(item => item.stage === 'parsing' || item.stage === 'importing')) timer = setTimeout(() => void load(), 1500);
      } catch (reason) { if (active && !controller.signal.aborted && (reason as Error).name !== 'AbortError') setError((reason as Error).message); }
      finally { if (active && !controller.signal.aborted) setLoading(false); }
    };
    void load();
    return () => { active = false; clearTimeout(timer); unlink(); controller.abort(); };
  }, [client, target.caseId, reload, scopeSignal]);
  const clean = async (dryRun: boolean) => {
    const signal = lifetime.current.signal;
    if (signal.aborted || scopeSignal.aborted) return;
    setCleanBusy(true); setCleanError('');
    try { const value = await client.cleanup(dryRun, signal); if (!signal.aborted) { setCleanup(value); if (!dryRun) setReload(value => value + 1); } }
    catch (reason) { if (!signal.aborted) setCleanError((reason as Error).message); }
    finally { if (!signal.aborted) setCleanBusy(false); }
  };
  const openLegacy = async (importId: string) => {
    if (!actions?.openLegacyImport) return;
    const signal = lifetime.current.signal;
    if (signal.aborted || scopeSignal.aborted) return;
    setActionBusy(importId); setActionError('');
    try { await actions.openLegacyImport(importId, signal); }
    catch (reason) { if (!signal.aborted) setActionError((reason as Error).message); }
    finally { if (!signal.aborted) setActionBusy(''); }
  };
  return <Modal title="导入记录" onClose={onClose} busy={cleanBusy}>
    <div className="modal-context">当前案件 <strong>{target.name}</strong><span>最近 100 条 · 仅本案</span></div>
    <div className="history-toolbar"><p className="muted">继续导入前会重新预览，不会自动写入。</p><button className="button secondary" disabled={loading} onClick={() => setReload(value => value + 1)}>刷新记录</button></div>
    {error && <div role="alert" className="error-banner">{error}<button onClick={() => setReload(value => value + 1)}>重试记录查询</button></div>}
    {actionError && <div role="alert" className="error-banner">{actionError}</div>}
    <div className="import-history" aria-busy={loading}>
      {loading && <p role="status" className="source-empty">正在加载本案导入记录…</p>}
      {!loading && !items.length && !error && <p className="source-empty">当前案件没有导入记录。</p>}
      {items.map(item => <article className="history-record" key={item.import_id}>
        <div className="history-description"><strong>{item.filename}</strong><p>{displayOperationalTime(item.created_at)} · {stages[item.stage]}{item.attempt > 0 ? ` · 已尝试 ${item.attempt} 次` : ''}</p>
          {item.result && (item.result.feedback ? <><p>自动处理完成 · 已保存 {item.result.feedback.record_total} 条记录</p><p>新增资金流水 {item.result.ok} 笔 · 重复 {item.result.rows_dup} 笔 · 已配对冲正/撤销 {item.result.feedback.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {item.result.feedback.non_monetary ?? 0} 条 · 含义未确定 {(item.result.feedback.unresolved ?? 0) + (item.result.feedback.review ?? 0)} 条 · 来源反馈 {item.result.feedback.non_transaction} 条 · 辅助资料 {item.result.feedback.auxiliary} 条</p>{(item.result.feedback.excluded_flows ?? 0) > 0 && <p className="source-note">本案另有 {item.result.feedback.excluded_flows} 笔历史留存流水已退出有效统计。</p>}<p className="source-note">含义未确定的记录已独立保留，不计入资金统计。</p></> : item.result.table_summary ? (() => {
            const summary = item.result.table_summary!;
            return <><p>自动处理完成 · 已保存 {summary.record_total} 条记录</p><p>新增资金流水 {summary.effective_added ?? item.result.ok} 笔 · 重复 {item.result.rows_dup} 笔 · 本次规则未接纳为流水 {summary.record_total - summary.accepted} 条（含义未确定 {summary.unresolved} 条） · 已配对冲正/撤销 {summary.cancelled ?? 0} 条（双方均不计有效资金）</p>{summary.effective_added != null && summary.effective_added !== item.result.ok && <p className="source-note">本次保留流水事实 {item.result.ok} 笔，其中新增有效资金流水 {summary.effective_added} 笔；已有配对依据的记录不重复进入统计。</p>}{(summary.excluded_flows ?? 0) > 0 && <p className="source-note">本案另有 {summary.excluded_flows} 笔历史留存流水已退出有效统计。</p>}<p className="source-note">未确定记录保留来源和原因。分析仅使用有效流水，未计入不代表金额为零或没有交易。</p></>;
          })() : <p>新增 {item.result.ok} 笔 · 重复 {item.result.rows_dup} 笔 · 跳过 {item.result.skipped} 行</p>)}
          {!item.source_available && <p className="source-note">{item.source_state === 'cleaned' ? '原始上传已清理' : '原始上传不可用'}</p>}
          {item.stage === 'error' && <p className="source-note">{item.recoverable ? '任务未完成，原始文件可用于重新预览。' : '任务未完成，当前无法继续；请检查原始文件。'}</p>}
        </div><div className="history-action">
          {item.surface === 'legacy' ? <div><button className="button secondary" disabled={!actions?.openLegacyImport || actionBusy === item.import_id} onClick={() => void openLegacy(item.import_id)}>在 G6 工作区查看 <span aria-hidden="true">↗</span></button>{!actions?.openLegacyImport && <p className="source-note">宿主未提供旧版工作区导航。</p>}</div>
            : item.stage === 'done' ? <button className="button secondary" onClick={() => onOpen(item)}>查看回执</button>
              : item.stage === 'parsing' || item.stage === 'importing' ? <button className="button secondary" onClick={() => onOpen(item)}>查看进度</button>
                : item.source_available && (item.stage === 'ready' || item.recoverable) ? <button className="button secondary" aria-label={`继续导入 ${item.filename}`} onClick={() => onOpen(item)}>继续导入</button> : <span className="muted">无法继续</span>}
        </div>
      </article>)}
    </div>
    <section className="cleanup-section" aria-label="上传留存清理"><div><h3>到期上传文件</h3><p className="muted">先预览本案清理范围；已用于流水追溯的文件受保护。</p></div><button className="button secondary" disabled={cleanBusy} onClick={() => void clean(true)}>预览清理</button>
      {cleanError && <div role="alert" className="error-banner">{cleanError}</div>}
      {cleanup && <div className="cleanup-result" role="status">{cleanup.dry_run ? `可清理 ${cleanup.eligible} 个 · 受保护 ${cleanup.protected} 个 · 待处理 ${cleanup.queued} 个` : `已清理 ${cleanup.deleted} 个 · 已缺失 ${cleanup.missing} 个 · 失败 ${cleanup.failed} 个`}
        {cleanup.dry_run && (cleanup.eligible > 0 || cleanup.queued > 0) && <button className="button secondary" disabled={cleanBusy} onClick={() => void clean(false)}>确认清理本案到期文件</button>}</div>}
    </section>
    <footer className="modal-footer"><button className="button primary" disabled={cleanBusy} onClick={onClose}>返回流水核对</button></footer>
  </Modal>;
}
