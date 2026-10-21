import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { Case } from '../../contracts/api-v1';
import { api as defaultApi, type FundsApi } from './lib/api';
import { Modal } from './components/Modal';
import { createHttpFundsSession } from './adapters/http';
import { createBrowserFundsActions } from './standalone/browserActions';
import { FUNDS_PANEL_VERSION, FundsPanel, type FundsCaseContextV1, type FundsHostActionsV1 } from './panel';

export function App({ api = defaultApi, actionsForCase }: { api?: FundsApi; actionsForCase?: (context: FundsCaseContextV1) => FundsHostActionsV1 }) {
  const [cases, setCases] = useState<Case[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [generation, setGeneration] = useState(0);
  const [casesLoading, setCasesLoading] = useState(true);
  const [caseSearch, setCaseSearch] = useState('');
  const [error, setError] = useState('');
  const [listReload, setListReload] = useState(0);
  const [creating, setCreating] = useState(false);
  const [caseName, setCaseName] = useState('');
  const [createError, setCreateError] = useState('');
  const [createBusy, setCreateBusy] = useState(false);
  const [connection, setConnection] = useState<{ binding: ReturnType<typeof createHttpFundsSession>; transport: FundsApi; actionsFactory: typeof actionsForCase } | null>(null);
  const nextBinding = useRef(0);
  const lifetime = useRef(new AbortController());
  const selected = cases.find(item => item.id === selectedId);
  const graphHref = selected ? `/?case_id=${encodeURIComponent(selected.id)}` : '#';
  useEffect(() => {
    if (!selected) { setConnection(null); return; }
    const context = { caseId: selected.id, name: selected.name };
    const binding = createHttpFundsSession({ context, scopeKey: `standalone:${selected.id}:${++nextBinding.current}`, generation, transport: api,
      actions: actionsForCase ? actionsForCase(context) : createBrowserFundsActions(context, api) });
    setConnection({ binding, transport: api, actionsFactory: actionsForCase });
    return binding.revoke;
  }, [api, selected?.id, selected?.name, generation, actionsForCase]);
  const binding = connection?.binding;
  const session = connection?.transport === api && connection.actionsFactory === actionsForCase && binding?.session.context.caseId === selectedId && binding.session.context.name === selected?.name && binding.session.generation === generation ? binding.session : null;
  useEffect(() => {
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
    return () => lifetime.current.abort();
  }, []);
  useEffect(() => {
    if (!selectedId) return;
    const url = new URL(window.location.href); url.searchParams.set('case_id', selectedId);
    window.history.replaceState(null, '', url);
  }, [selectedId]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setCasesLoading(true);
    api.cases(controller.signal).then(result => {
      if (!active) return;
      setCases(result.items);
      setSelectedId(current => {
        if (result.items.some(item => item.id === current)) return current;
        const requested = new URLSearchParams(window.location.search).get('case_id');
        return result.items.find(item => item.id === requested)?.id || result.items[0]?.id || '';
      });
      setError('');
    }).catch(reason => {
      if (!active || reason.name === 'AbortError') return;
      setError(reason.message);
    }).finally(() => { if (active) setCasesLoading(false); });
    return () => { active = false; controller.abort(); };
  }, [api, listReload]);
  const selectCase = (id: string) => {
    // Revoke before React renders a new scope, not after a stale click can run.
    binding?.revoke(); setSelectedId(id); setGeneration(value => value + 1);
  };
  const create = async (event: FormEvent) => {
    event.preventDefault(); if (!caseName.trim()) return;
    const signal = lifetime.current.signal;
    setCreateBusy(true); setCreateError('');
    try {
      const item = await api.createCase(caseName.trim(), signal);
      if (signal.aborted) return;
      setCases(current => [...current, item]); selectCase(item.id); setCreating(false); setCaseName('');
    } catch (reason) { if (!signal.aborted) setCreateError((reason as Error).message); }
    finally { if (!signal.aborted) setCreateBusy(false); }
  };
  return <div className="funds-trace app-shell">
    <aside className="sidebar">
      <a className="brand" href="/workspace/" aria-label="FundsTrace 工作台"><img src="/static/logo.png" alt="" width="35" height="35" /><span>FundsTrace<small>资金流水追踪</small></span></a>
      <div className="workspace-nav"><span className="nav-section">工作台</span><button className="nav-item active" onClick={() => selected && selectCase(selected.id)}><span aria-hidden="true">▤</span> 流水核对</button><a className={`nav-item ${!selected ? 'disabled' : ''}`} href={graphHref} aria-disabled={!selected}><span aria-hidden="true">◇</span> 关系图谱 <span className="nav-external">↗</span></a></div>
      <div className="case-section"><div className="section-heading"><span>案件</span><button className="icon-button" aria-label="新建案件" onClick={() => { setCreating(true); setCreateError(''); }}>＋</button></div><input className="case-filter" aria-label="筛选案件" placeholder="查找案件…" value={caseSearch} onChange={event => setCaseSearch(event.target.value)} /><div className="case-list" aria-label="案件列表">{casesLoading ? <p className="sidebar-hint">正在加载案件…</p> : cases.filter(item => item.name.toLowerCase().includes(caseSearch.toLowerCase())).map(item => <button key={item.id} className={`case-item ${selectedId === item.id ? 'selected' : ''}`} aria-pressed={selectedId === item.id} onClick={() => selectCase(item.id)}><span className="case-glyph" aria-hidden="true">▱</span><span>{item.name}</span></button>)}{!casesLoading && !cases.length && <p className="sidebar-hint">新建案件后开始核对。</p>}</div></div>
      <div className="sidebar-footer"><span className="local-dot" /> 本地工作区<span>按案件独立核对与追踪</span><a href="/license">版本与许可</a></div>

    </aside>
    <main className="standalone-main">
      {error && <div role="alert" className="error-banner">{error}<button onClick={() => setListReload(value => value + 1)}>重新加载案件</button></div>}
      <FundsPanel host={{ version: FUNDS_PANEL_VERSION, session }} />
    </main>
    {creating && <Modal title="新建案件" busy={createBusy} onClose={() => setCreating(false)}><form onSubmit={event => void create(event)} className="create-case-form"><label>案件名称<input autoFocus aria-label="案件名称" maxLength={200} value={caseName} onChange={event => setCaseName(event.target.value)} placeholder="输入便于识别的案件名称" /></label>{createError && <p role="alert" className="error-banner">{createError}</p>}<footer className="modal-footer"><button type="button" className="button secondary" disabled={createBusy} onClick={() => setCreating(false)}>取消</button><button className="button primary" disabled={createBusy || !caseName.trim()}>{createBusy ? '正在创建…' : '创建案件'}</button></footer></form></Modal>}
  </div>;
}
