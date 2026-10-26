import { useEffect, useState, useRef, type FormEvent, type ReactNode } from 'react';
import type { CaseStats, Flow, FlowPage, ImportSession } from '../../../contracts/api-v1';
import { formatMoney, displayTime } from '../lib/format';
import { Modal } from '../components/Modal';
import { ImportDialog } from '../features/ImportDialog';
import { ImportHistoryDialog } from '../features/ImportHistoryDialog';
import { FlowSources } from '../features/FlowSources';
import { FUNDS_PANEL_VERSION, type FundsPanelHostV1, type FundsPanelSessionV1 } from './host';
import { linkAbortSignal, runUnlessAborted } from './cancellation';

type Filters = { account: string; kw: string; page: number };
const emptyFilters: Filters = { account: '', kw: '', page: 1 };
export type FundsQueryDraft = Readonly<{ filters: Filters; account: string; keyword: string }>;
type WorkspacePresentation = { initialDraft?: FundsQueryDraft; onDraftChange?: (draft: FundsQueryDraft) => void; caseActions?: ReactNode; retained?: boolean; navigationPending?: boolean };

export function FundsPanel({ host, workspace, active = true, revision = 0 }: { host: FundsPanelHostV1; workspace?: WorkspacePresentation; active?: boolean; revision?: number }) {
  const session = host.session;
  const binding = session?.client.binding;
  const valid = host.version === FUNDS_PANEL_VERSION && session && binding?.caseId === session.context.caseId && binding.scopeKey === session.scopeKey && binding.generation === session.generation;
  return <div className="funds-trace funds-panel">{valid ? <SessionBoundary key={JSON.stringify([session.context.caseId, session.scopeKey, session.generation])} session={session} workspace={workspace} active={active} revision={revision} />
    : <section className="main-workspace"><div className="empty-state"><h2>{session ? '案件会话绑定无效' : '未选择案件'}</h2><p>请由宿主提供当前案件会话。</p></div></section>}</div>;
}

function SessionBoundary({ session, workspace, active, revision }: { session: FundsPanelSessionV1; workspace?: WorkspacePresentation; active: boolean; revision: number }) {
  const [revoked, setRevoked] = useState(session.signal.aborted);
  useEffect(() => {
    const abort = () => setRevoked(true);
    if (session.signal.aborted) abort();
    session.signal.addEventListener('abort', abort, { once: true });
    return () => session.signal.removeEventListener('abort', abort);
  }, [session.signal]);
  return revoked || session.signal.aborted ? <section className="main-workspace"><div className="empty-state"><h2>案件会话已撤销</h2><p>请由宿主重新绑定当前案件。</p></div></section> : <BoundFundsPanel session={session} workspace={workspace} active={active} revision={revision} />;
}

function BoundFundsPanel({ session, workspace, active, revision }: { session: FundsPanelSessionV1; workspace?: WorkspacePresentation; active: boolean; revision: number }) {
  const { context, client, actions } = session;
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [stats, setStats] = useState<CaseStats | null>(null);
  const [page, setPage] = useState<FlowPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState<Filters>(() => workspace?.initialDraft?.filters ?? emptyFilters);
  const [accountDraft, setAccountDraft] = useState(() => workspace?.initialDraft?.account ?? '');
  const [keywordDraft, setKeywordDraft] = useState(() => workspace?.initialDraft?.keyword ?? '');
  const [selectedFlow, setSelectedFlow] = useState<Flow | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [resumeSession, setResumeSession] = useState<ImportSession | undefined>();
  const [returnToHistory, setReturnToHistory] = useState(false);
  const [graphError, setGraphError] = useState('');
  const [graphBusy, setGraphBusy] = useState(false);
  const lifetime = useRef(new AbortController());
  const onDraftChange = useRef(workspace?.onDraftChange);
  onDraftChange.current = workspace?.onDraftChange;
  useEffect(() => { onDraftChange.current?.({ filters: { ...filters }, account: accountDraft, keyword: keywordDraft }); }, [filters, accountDraft, keywordDraft]);
  useEffect(() => {
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
    const controller = lifetime.current;
    const unlink = linkAbortSignal(session.signal, controller);
    return () => { unlink(); controller.abort(); };
  }, [session.signal]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); let live = true;
    const unlink = linkAbortSignal(session.signal, controller);
    if (controller.signal.aborted) return unlink;
    setPage(null); setStats(null); setSelectedFlow(null); setLoading(true); setError('');
    Promise.all([runUnlessAborted(controller.signal, () => client.stats(controller.signal)), runUnlessAborted(controller.signal, () => client.flows(filters, controller.signal))])
      .then(([nextStats, nextPage]) => {
        if (!live || controller.signal.aborted) return;
        if (nextStats.case_id !== context.caseId || nextPage.case_id !== context.caseId || nextPage.items.some(row => row.case_id !== context.caseId)) throw new Error('返回的案件与当前案件不一致，请重新加载。');
        setStats(nextStats); setPage(nextPage);
      }).catch(reason => { if (live && !controller.signal.aborted && reason.name !== 'AbortError') setError(reason.message); })
      .finally(() => { if (live && !controller.signal.aborted) setLoading(false); });
    return () => { live = false; unlink(); controller.abort(); };
  }, [client, context.caseId, filters, reload, session.signal, active, revision]);
  const openImport = () => { setImportOpen(true); setResumeSession(undefined); setReturnToHistory(false); setHistoryOpen(false); };
  const closeImport = () => {
    if (returnToHistory) setHistoryOpen(true);
    setImportOpen(false); setResumeSession(undefined); setReturnToHistory(false); setReload(value => value + 1);
  };
  const search = (event: FormEvent) => { event.preventDefault(); setFilters({ account: accountDraft.trim(), kw: keywordDraft.trim(), page: 1 }); };
  const openGraph = async () => {
    if (!actions?.openGraph || workspace?.navigationPending) return;
    const signal = lifetime.current.signal;
    if (signal.aborted || session.signal.aborted) return;
    setGraphBusy(true); setGraphError('');
    try { await actions.openGraph(signal); }
    catch (reason) { if (!signal.aborted) setGraphError((reason as Error).message); }
    finally { if (!signal.aborted) setGraphBusy(false); }
  };
  const totals = stats?.amount_totals || [];
  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1;
  return <>
    <section className="main-workspace" aria-label="资金流水面板">

      {!workspace && <div className="topline"><span>案件工作区 <span className="breadcrumb">/</span> 流水核对</span><span className="local-label">本地数据</span></div>}
      <header className="workspace-heading"><div><p className="eyebrow">{workspace ? '当前案件' : '流水核对'}</p><h1>{context.name}</h1><p className="description">{workspace ? <>案件 ID：<span className="mono">{context.caseId}</span>{workspace.retained && <span className="work-retained">工作已保留</span>}</> : '核对交易明细，再进入关系图谱追踪资金流向。'}</p></div><div className="heading-actions">{!workspace && <button className="button secondary" onClick={() => setHistoryOpen(true)}>导入记录</button>}<button className="button primary" onClick={openImport}>{workspace ? <img className="ui-icon" src="/static/ui-icons/upload.svg" alt="" /> : <span aria-hidden="true">＋</span>} 导入流水</button><button className="button secondary" disabled={!actions?.openGraph || graphBusy || workspace?.navigationPending} onClick={() => void openGraph()}>{workspace ? '进入画图' : '打开 G6 图谱'} {workspace ? <img className="ui-icon" src="/static/ui-icons/chevron-right.svg" alt="" /> : <span aria-hidden="true">↗</span>}</button>{workspace?.caseActions}</div></header>
      {!actions?.openGraph && <p className="muted">宿主未提供图谱导航能力。</p>}
      {error && <div role="alert" className="error-banner">{error}<button onClick={() => setReload(value => value + 1)}>重新加载</button></div>}
      {graphError && <div role="alert" className="error-banner">{graphError}</div>}
      <section className="case-facts" aria-label="案件汇总"><div><span>流水笔数</span><strong>{stats ? stats.flows.toLocaleString('zh-CN') : '—'}</strong></div><div><span>涉及账户</span><strong>{stats ? stats.accounts.toLocaleString('zh-CN') : '—'}</strong></div><div><span>导入批次</span><strong>{stats ? stats.batches.toLocaleString('zh-CN') : '—'}</strong></div><div className="money-fact"><span>交易金额合计 <small>按币种</small></span><strong>{!stats ? '—' : !totals.length ? '0.00' : totals.map(total => <span key={total.currency} className="currency-total">{formatMoney(total.amount)} <small>{total.currency || '未标注币种'}</small></span>)}</strong></div></section>
      {stats?.event_summary && <section aria-label="事件关联口径" className="workspace-footnote"><strong>有据关联事件 {stats.event_summary.confirmed_events} 笔</strong> · 已关联事件金额：{stats.event_summary.confirmed_amount_totals.length ? stats.event_summary.confirmed_amount_totals.map(total => `${formatMoney(total.amount)} ${total.currency}`).join('；') : '0.00'}。未确定记录 {stats.event_summary.unknown_entries} 笔，冲突记录 {stats.event_summary.conflict_entries} 笔。{stats.event_summary.note}</section>}
      {workspace && <div className="case-workspace-tabs"><span className="active">流水核对</span><button onClick={() => setHistoryOpen(true)}>导入记录</button><span className="case-tabs-hint">核对原始流水与来源</span></div>}
      <section className="transactions" aria-label="流水明细">
        <div className="table-heading"><h2>交易明细 <span>{page?.total.toLocaleString('zh-CN') ?? '—'}</span></h2><span className="muted">{stats?.resolution ? '已接纳账务记录口径' : '原始流水口径'}</span></div>
        <form className="filters" onSubmit={search}><label><span className="sr-only">账号筛选</span><input aria-label="账号筛选" placeholder="付款或收款账号" value={accountDraft} onChange={event => setAccountDraft(event.target.value)} /></label><label className="keyword-filter"><span className="sr-only">摘要或流水号</span><input aria-label="摘要或流水号" placeholder="搜索摘要或流水号…" value={keywordDraft} onChange={event => setKeywordDraft(event.target.value)} /></label><button className="button secondary" disabled={loading}>查询</button>{(filters.account || filters.kw) && <button type="button" className="text-button" onClick={() => { setAccountDraft(''); setKeywordDraft(''); setFilters(emptyFilters); }}>清除筛选</button>}</form>
        <div className="table-scroll" tabIndex={0} aria-label="可横向滚动的流水明细" aria-busy={loading}><table className="flow-table"><thead><tr><th>交易时间</th><th>付款方</th><th className="flow-arrow" aria-label="流向" /><th>收款方</th><th className="align-right">交易金额</th><th>摘要</th><th><span className="sr-only">核对</span></th></tr></thead><tbody>{page?.items.map(row => <tr key={row.id}><td className="mono time-cell">{displayTime(row.trade_time)}<small>{row.trade_no || '无流水号'}</small></td><td>{row.payer_name || '未标注户名'}<small className="mono">{row.payer_display_account || row.payer_account}</small></td><td className="flow-arrow" aria-hidden="true">→</td><td>{row.payee_name || '未标注户名'}<small className="mono">{row.payee_display_account || row.payee_account}</small></td><td className="align-right money-cell mono">{formatMoney(row.amount)}<small>{row.currency || '未标注币种'}</small></td><td className="summary-cell" title={row.summary}>{row.summary || '—'}</td><td><button className="row-action" aria-label={`核对流水 ${row.trade_no || row.id}`} onClick={() => setSelectedFlow(row)}>核对 <span aria-hidden="true">↗</span></button></td></tr>)}</tbody></table>
        {loading ? <div role="status" className="empty-state"><span className="loading-mark" />正在加载当前案件流水…</div> : page && !page.items.length ? <div className="empty-state"><span className="empty-symbol" aria-hidden="true">▤</span><h3>{filters.account || filters.kw ? '没有符合筛选条件的流水' : '当前案件还没有流水'}</h3><p>{filters.account || filters.kw ? '调整账号或关键词后重新查询。' : '导入银行或支付机构流水，先检查字段映射和金额预览。'}</p>{!filters.account && !filters.kw && <button className="button primary" onClick={openImport}>导入第一份流水</button>}</div> : null}</div>
        <footer className="table-footer"><span>{page ? `显示 ${page.total ? (page.page - 1) * page.page_size + 1 : 0}–${Math.min(page.page * page.page_size, page.total)} 笔，共 ${page.total.toLocaleString('zh-CN')} 笔` : '等待加载'}</span><div><button className="page-button" aria-label="上一页" disabled={loading || filters.page <= 1} onClick={() => setFilters(value => ({ ...value, page: value.page - 1 }))}>←</button><span>第 {filters.page} / {totalPages} 页</span><button className="page-button" aria-label="下一页" disabled={loading || filters.page >= totalPages} onClick={() => setFilters(value => ({ ...value, page: value.page + 1 }))}>→</button></div></footer>
      </section>
      <footer className="workspace-footnote">{stats?.resolution ? '图谱与本页使用同一账户关联和账务记录口径；画布只汇总已上图且两端标识完整的记录。事件关联另列，原观察及本方余额保留，账务记录合计不等于本金。' : '金额保留原始精度，图谱展示去重与本页流水统计分别计算。'}</footer>

    </section>
    {active && historyOpen && <ImportHistoryDialog target={context} client={client} scopeSignal={session.signal} actions={actions} onClose={() => { setHistoryOpen(false); setReload(value => value + 1); }} onOpen={session => { setImportOpen(true); setResumeSession(session); setReturnToHistory(true); setHistoryOpen(false); }} />}
    {importOpen && <ImportDialog key={resumeSession?.import_id ?? 'new'} target={context} client={client} scopeSignal={session.signal} resume={resumeSession} actions={actions} active={active} onClose={closeImport} onImported={() => setReload(value => value + 1)} />}
    {active && selectedFlow && <Modal title="流水核对" onClose={() => setSelectedFlow(null)}><dl className="flow-details">{Object.entries({ '所属案件': context.name, '流水 ID': selectedFlow.id, '批次 ID': selectedFlow.batch_id, '交易流水号': selectedFlow.trade_no, '交易时间': displayTime(selectedFlow.trade_time), '付款户名': selectedFlow.payer_name, '付款账号／卡号': selectedFlow.payer_display_account || selectedFlow.payer_account, '来源付款标识': selectedFlow.raw_payer_account || selectedFlow.payer_account, '收款户名': selectedFlow.payee_name, '收款账号／卡号': selectedFlow.payee_display_account || selectedFlow.payee_account, '来源收款标识': selectedFlow.raw_payee_account || selectedFlow.payee_account, '交易金额': `${formatMoney(selectedFlow.amount)} ${selectedFlow.currency}`, '交易后余额': selectedFlow.balance === null ? '未提供' : formatMoney(selectedFlow.balance), '摘要': selectedFlow.summary, '渠道': selectedFlow.channel }).map(([label, value]) => <div key={label}><dt>{label}</dt><dd className={label.includes('金额') || label.includes('ID') || label.includes('账号') ? 'mono' : ''}>{value || '—'}</dd></div>)}</dl><FlowSources key={`${selectedFlow.case_id}:${selectedFlow.id}`} caseId={selectedFlow.case_id} flowId={selectedFlow.id} client={client} scopeSignal={session.signal} actions={actions} /><footer className="modal-footer"><button className="button primary" onClick={() => setSelectedFlow(null)}>核对完成</button></footer></Modal>}
  </>;
}
