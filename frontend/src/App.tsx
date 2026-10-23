import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import type { Case } from '../../contracts/api-v1';
import { api as defaultApi, ApiError, type FundsApi } from './lib/api';
import { AccountWorkspace } from './accounts/AccountWorkspace';
import type { AccountWorkspaceActions } from './accounts/types';
import { Modal } from './components/Modal';
import { EntityPicker, type EntityPickerSearch } from './components/EntityPicker';
import { createHttpFundsSession } from './adapters/http';
import { createBrowserFundsActions } from './standalone/browserActions';
import { FUNDS_PANEL_VERSION, FundsPanel, type FundsCaseContextV1, type FundsHostActionsV1 } from './panel';
import type { FundsQueryDraft } from './panel/FundsPanel';
import { createWorkspaceBridge, type PendingImport, type WorkspaceBridge, type WorkspaceContext } from './standalone/workspaceBridge';

export function App({ api = defaultApi, actionsForCase, embedded = new URLSearchParams(window.location.search).get('embedded') === '1' && window.parent !== window }: { api?: FundsApi; actionsForCase?: (context: FundsCaseContextV1) => FundsHostActionsV1; embedded?: boolean }) {
  const [surface, setSurface] = useState<'cases'|'accounts'>(new URLSearchParams(window.location.search).get('surface') === 'accounts' ? 'accounts' : 'cases');
  const [accountRevision, setAccountRevision] = useState(0);
  const [dataRevision, setDataRevision] = useState(0);
  const [localImports, setLocalImports] = useState<(PendingImport & { caseId: string })[]>([]);
  const [importError, setImportError] = useState('');
  const [checkingImport, setCheckingImport] = useState<string | null>(null);
  const notifiedImports = useRef(new Set<string>());
  const [cases, setCases] = useState<Case[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [generation, setGeneration] = useState(0);
  const [casesLoading, setCasesLoading] = useState(true);
  const [caseSearch, setCaseSearch] = useState('');
  const [caseQuery, setCaseQuery] = useState({ text: '', id: '' });
  const caseSearchComposing = useRef(false);
  const [error, setError] = useState('');
  const [listReload, setListReload] = useState(0);
  const [creating, setCreating] = useState(false);
  const [caseInfoOpen, setCaseInfoOpen] = useState(false);
  const [caseActionsOpen, setCaseActionsOpen] = useState(false);
  const caseActionsMenu = useRef<HTMLDetailsElement>(null);
  const caseActionsSummary = useRef<HTMLElement>(null);
  useEffect(() => { setCaseInfoOpen(false); setCaseActionsOpen(false); }, [selectedId, generation]);
  const [caseName, setCaseName] = useState('');
  const [createError, setCreateError] = useState('');
  const [createBusy, setCreateBusy] = useState(false);
  const [createdCase, setCreatedCase] = useState<Case | null>(null);
  const [uncertainCreate, setUncertainCreate] = useState<{ name: string; checked: boolean; matches: Case[] } | null>(null);
  const [hostContext, setHostContext] = useState<WorkspaceContext | null>(null);
  const [operationBusy, setOperationBusy] = useState(false);
  const bridge = useRef<WorkspaceBridge | null>(null);
  const queryDrafts = useRef(new Map<string, FundsQueryDraft>());
  const retainedCaseIds = hostContext?.retainedCaseIds ?? [];
  const pageActive = !embedded || hostContext?.active !== false;
  useEffect(() => { if (operationBusy || !pageActive || surface !== 'cases') setCaseActionsOpen(false); }, [operationBusy, pageActive, surface]);
  useEffect(() => {
    if (!caseActionsOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !caseActionsMenu.current?.contains(event.target)) setCaseActionsOpen(false);
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, [caseActionsOpen]);
  const [connection, setConnection] = useState<{ binding: ReturnType<typeof createHttpFundsSession>; transport: FundsApi; actionsFactory: typeof actionsForCase } | null>(null);
  const currentBinding = useRef<ReturnType<typeof createHttpFundsSession> | null>(null);
  const nextBinding = useRef(0);
  const lifetime = useRef(new AbortController());
  const selected = embedded ? hostContext ? { id: hostContext.caseId, name: hostContext.name, created_at: null } : undefined : cases.find(item => item.id === selectedId);
  useEffect(() => { setImportError(''); setCheckingImport(null); }, [selectedId, generation]);
  useEffect(() => {
    if (!embedded) return;
    const connection = createWorkspaceBridge({
      onContext: context => {
        if (currentBinding.current?.session.generation !== context.generation || currentBinding.current?.session.context.caseId !== context.caseId) currentBinding.current?.revoke();
        setHostContext(context); if (context.surface) setSurface(context.surface); setSelectedId(context.caseId); setGeneration(context.generation); setError('');
      },
      onCasesChanged: () => setListReload(value => value + 1),
      onAccountsChanged: () => setAccountRevision(value => value + 1),
      onDataChanged: () => { setDataRevision(value => value + 1); setAccountRevision(value => value + 1); },
    });
    bridge.current = connection;
    return () => { connection.dispose(); bridge.current = null; currentBinding.current?.revoke(); };
  }, [embedded]);
  useEffect(() => {
    if (!selected) { setConnection(null); return; }
    const context = { caseId: selected.id, name: selected.name };
    const isOwnContext = () => bridge.current?.context?.caseId === context.caseId && bridge.current.context.generation === generation;
    const ownBridge = () => {
      const owner = bridge.current;
      if (!owner || owner.context?.caseId !== context.caseId || owner.context.generation !== generation) throw new DOMException('案件工作区已切换。', 'AbortError');
      return owner;
    };
    const forgetImport = (importId: string) => setLocalImports(items => items.filter(item => item.caseId !== context.caseId || item.importId !== importId));
    const transport: FundsApi = embedded ? { ...api,
      commit: async (caseId, importId, body, signal) => {
        await ownBridge().request('import-acquire', { importId }, signal);
        setLocalImports(items => [...items.filter(item => item.caseId !== caseId || item.importId !== importId), { caseId, importId, phase: 'pending' }]);
        try {
          if (signal?.aborted) throw new DOMException('操作已中止。', 'AbortError');
          return await api.commit(caseId, importId, body, signal);
        } catch (reason) {
          if (!isOwnContext()) throw reason;
          const status = reason instanceof ApiError ? reason.status : undefined;
          if (status !== undefined && status >= 400 && status < 500) {
            try { await ownBridge().request('import-release', { importId, rejected: true }); forgetImport(importId); }
            catch { setImportError('提交已被拒绝，但宿主尚未核对记录；请核对原任务后再操作。'); }
          } else {
            setLocalImports(items => items.map(item => item.caseId === caseId && item.importId === importId ? { ...item, phase: 'unknown' } : item));
            setImportError('导入提交结果尚未确认。请核对原任务；核对只读取进度，不会重新提交。');
          }
          throw reason;
        }
      },
      job: async (caseId, importId, signal) => {
        const job = await api.job(caseId, importId, signal);
        if (job.case_id !== caseId || job.import_id !== importId) throw new Error('导入进度与当前案件不一致。');
        if (job.stage === 'done' || job.stage === 'error') {
          try {
            await ownBridge().request('import-release', { importId, jobId: job.import_id }, signal); forgetImport(importId);
            const key = `${caseId}:${generation}:${importId}`;
            if (job.stage === 'done' && !notifiedImports.current.has(key)) {
              await ownBridge().request('data-changed', {}, signal); notifiedImports.current.add(key);
            }
          } catch (reason) { if (!signal?.aborted && isOwnContext()) setImportError(`导入结果已读取，宿主核对或刷新尚未完成。${(reason as Error).message}`); }
        }
        return job;
      },
    } : api;
    const binding = createHttpFundsSession({ context, scopeKey: `standalone:${selected.id}:${++nextBinding.current}`, generation, transport,
      actions: actionsForCase ? actionsForCase(context) : embedded ? {
        ...createBrowserFundsActions(context, api),
        openGraph: signal => bridge.current?.request('open-graph', {}, signal),
        openLegacyImport: (_importId, signal) => bridge.current?.request('open-graph', {}, signal),
      } : createBrowserFundsActions(context, api) });
    currentBinding.current = binding;
    setConnection({ binding, transport: api, actionsFactory: actionsForCase });
    return binding.revoke;
  }, [api, selected?.id, selected?.name, generation, actionsForCase, embedded]);
  const binding = connection?.binding;
  const session = connection?.transport === api && connection.actionsFactory === actionsForCase && binding?.session.context.caseId === selectedId && binding.session.context.name === selected?.name && binding.session.generation === generation ? binding.session : null;
  useEffect(() => {
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
    return () => lifetime.current.abort();
  }, []);
  useEffect(() => {
    if (!selectedId || embedded) return;
    const url = new URL(window.location.href); url.searchParams.set('case_id', selectedId);
    window.history.replaceState(null, '', url);
  }, [selectedId, embedded]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setCasesLoading(true);
    api.cases(controller.signal).then(result => {
      if (!active) return;
      setCases(result.items);
      if (!embedded) setSelectedId(current => {
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
  }, [api, listReload, embedded]);
  const selectCase = async (id: string) => {
    if (id === selectedId) return;
    if (embedded) {
      if (!bridge.current) throw new Error('案件工作区尚未连接，请稍后重试。');
      await bridge.current.request('select-case', { caseId: id }, lifetime.current.signal);
      return;
    }
    // Revoke before React renders a new scope, not after a stale click can run.
    binding?.revoke(); setSelectedId(id); setGeneration(value => value + 1);
  };
  const create = async (event: FormEvent) => {
    event.preventDefault(); if (createBusy || uncertainCreate || !caseName.trim()) return;
    const signal = lifetime.current.signal;
    const startingContext = bridge.current?.context;
    let item = createdCase;
    let acknowledged = !!createdCase;
    setCreateBusy(true); setCreateError('');
    try {
      item = item ?? await api.createCase(caseName.trim(), signal);
      if (signal.aborted) return;
      if (!item || typeof item.id !== 'string' || !item.id || typeof item.name !== 'string') throw new Error('创建响应不完整。');
      acknowledged = true;
      const confirmed = item;
      setCreatedCase(item);
      setCases(current => [...current.filter(existing => existing.id !== confirmed.id), confirmed]);
      if (embedded && (startingContext?.caseId !== bridge.current?.context?.caseId || startingContext?.generation !== bridge.current?.context?.generation)) {
        setCreateError('案件已创建；当前工作区已切换，请从案件列表打开新案件。'); return;
      }
      await selectCase(item.id);
      if (signal.aborted) return;
      setCreating(false); setCaseName(''); setCreatedCase(null);
    } catch (reason) {
      if (!signal.aborted) {
        const rejected = reason instanceof ApiError && (reason.code === 'case_name_exists' || reason.code === 'invalid_request' || /^http_4(?:00|03|04|05|13)$/.test(reason.code));
        if (!acknowledged && !rejected) {
          setUncertainCreate({ name: caseName.trim(), checked: false, matches: [] });
          setCreateError(`无法确认案件是否已创建。${(reason as Error).message} 请先核对案件列表。`);
        } else setCreateError((reason as Error).message);
      }
    }
    finally { if (!signal.aborted) setCreateBusy(false); }
  };
  const checkUncertainCreate = async () => {
    if (!uncertainCreate || createBusy) return;
    const signal = lifetime.current.signal;
    setCreateBusy(true);
    try {
      const result = await api.cases(signal);
      if (signal.aborted) return;
      setCases(result.items);
      setUncertainCreate(current => current && { ...current, checked: true, matches: result.items.filter(item => item.name === current.name) });
      setCreateError('');
    } catch (reason) { if (!signal.aborted) setCreateError(`无法核对案件列表。${(reason as Error).message}`); }
    finally { if (!signal.aborted) setCreateBusy(false); }
  };
  const openCheckedCase = async (item: Case) => {
    setCreatedCase(item); setUncertainCreate(null); setCreateBusy(true); setCreateError('');
    try { await selectCase(item.id); if (!lifetime.current.signal.aborted) { setCreating(false); setCaseName(''); setCreatedCase(null); } }
    catch (reason) { if (!lifetime.current.signal.aborted) setCreateError((reason as Error).message); }
    finally { if (!lifetime.current.signal.aborted) setCreateBusy(false); }
  };
  const runOperation = async (action: 'import' | 'export' | 'delete') => {
    if (operationBusy) return;
    closeCaseActions(true);
    setOperationBusy(true); setError('');
    try {
      if (embedded && bridge.current) await bridge.current.request('case-action', { action }, lifetime.current.signal);
      else window.location.assign(`/?page=cases&case_id=${encodeURIComponent(selectedId)}&case_action=${action}`);
    } catch (reason) { if ((reason as Error).name !== 'AbortError') setError((reason as Error).message); }
    finally { if (!lifetime.current.signal.aborted) setOperationBusy(false); }
  };
  const chooseCase = async (id: string) => {
    closeCaseActions();
    setOperationBusy(true); setError('');
    try { await selectCase(id); }
    catch (reason) { if ((reason as Error).name !== 'AbortError') setError((reason as Error).message); }
    finally { if (!lifetime.current.signal.aborted) setOperationBusy(false); }
  };
  const pendingImports = [...new Map([...localImports.filter(item => item.caseId === selectedId), ...(hostContext?.pendingImports ?? [])].map(item => [item.importId, item])).values()];
  const checkImport = async (importId: string) => {
    if (!bridge.current || checkingImport) return;
    const owner = bridge.current, starting = owner.context;
    const current = () => starting?.caseId === owner.context?.caseId && starting?.generation === owner.context?.generation;
    setCheckingImport(importId); setImportError('');
    try {
      await owner.request('import-release', { importId, jobId: importId }, lifetime.current.signal);
      if (!current()) return;
      setLocalImports(items => items.filter(item => item.caseId !== selectedId || item.importId !== importId));
      await owner.request('data-changed', {}, lifetime.current.signal);
    } catch (reason) { if (current() && (reason as Error).name !== 'AbortError') setImportError((reason as Error).message); }
    finally { if (!lifetime.current.signal.aborted) setCheckingImport(null); }
  };
  const accountActions = useMemo<AccountWorkspaceActions>(() => {
    const request: WorkspaceBridge['request'] = (action,payload,signal) => {
      if (!bridge.current) return Promise.reject(new Error('账户工作区尚未连接，请稍后重试。'));
      return bridge.current.request(action,payload,signal);
    };
    return {
      mutate: (mutation,signal) => request('account-action',{action:'mutate',mutation},signal),
      reconcile: (operationId,signal) => request('account-action',{action:'reconcile',operationId},signal),
      openMainAccounts: signal => request('account-action',{action:'main-accounts'},signal),
      importIdentity: signal => request('account-action',{action:'import-identity'},signal),
      downloadTemplate: signal => request('account-action',{action:'download-template'},signal),
      viewFlows: (account,signal) => request('account-action',{action:'view-flows',account},signal),
    };
  }, []);
  function closeCaseActions(restoreFocus = false) {
    setCaseActionsOpen(false);
    if (restoreFocus) caseActionsSummary.current?.focus({ preventScroll: true });
  }
  const caseActions = <details ref={caseActionsMenu} className="case-actions" open={caseActionsOpen} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) closeCaseActions(); }} onKeyDown={event => { if (event.key === 'Escape' && caseActionsOpen) { event.preventDefault(); event.stopPropagation(); closeCaseActions(true); } }}><summary ref={caseActionsSummary} aria-disabled={operationBusy} aria-expanded={caseActionsOpen} onClick={event => { event.preventDefault(); if (!operationBusy) setCaseActionsOpen(value => !value); }}>案件操作 <img className="ui-icon" src="/static/ui-icons/chevron-down.svg" alt="" /></summary><div className="case-actions-menu"><button disabled={operationBusy} onClick={() => void runOperation('export')}><img className="ui-icon" src="/static/ui-icons/download.svg" alt="" />导出案件包</button><button className="danger-text" disabled={operationBusy} onClick={() => void runOperation('delete')}><img className="ui-icon" src="/static/ui-icons/trash.svg" alt="" />删除案件…</button><button disabled={operationBusy} onClick={() => { closeCaseActions(true); setCaseInfoOpen(true); }}><img className="ui-icon" src="/static/ui-icons/list.svg" alt="" />案件信息</button></div></details>;
  const normalizeCaseQuery = (value: string) => value.normalize('NFKC').trim().toLocaleLowerCase();
  const visibleCases = cases.filter(item => caseQuery.id ? item.id === caseQuery.id : [item.name, item.id].some(value => normalizeCaseQuery(value).includes(normalizeCaseQuery(caseQuery.text))));
  const searchCaseSuggestions: EntityPickerSearch = async ({ query, offset, limit, signal }) => {
    if (signal.aborted) throw new DOMException('操作已中止。', 'AbortError');
    const q = normalizeCaseQuery(query);
    const matches = cases.filter(item => [item.name, item.id].some(value => normalizeCaseQuery(value).includes(q)));
    const items = matches.slice(offset, offset + limit).map(item => {
      const matchedNumber = normalizeCaseQuery(item.id).includes(q);
      return { key: item.id, label: item.name, number: item.id, value: item.name, detail: item.created_at || undefined,
        matchedField: matchedNumber ? 'number' as const : 'label' as const, matchedText: matchedNumber ? item.id : item.name };
    });
    const hasMore = offset + items.length < matches.length;
    return { items, hasMore, nextOffset: hasMore ? offset + items.length : undefined };
  };
  const submitCaseSearch = () => {
    if (!pageActive || surface !== 'cases' || operationBusy || caseSearchComposing.current) return;
    setCaseQuery({ text: caseSearch, id: '' });
  };
  const caseSuggestionScope = pageActive && surface === 'cases' && !operationBusy && !casesLoading ? JSON.stringify(['cases', selectedId, generation, dataRevision, listReload, cases.length]) : null;
  return <>
    <div className="funds-trace standalone-surface" hidden={surface !== 'accounts'}>{selected ? <AccountWorkspace active={pageActive && surface === 'accounts'} context={{caseId:selected.id,name:selected.name,generation}} cases={cases} onSelectCase={selectCase} revision={accountRevision} actions={accountActions} unresolvedOperations={hostContext?.unresolvedOperations} /> : <div role="status">正在连接案件账户工作区…</div>}</div>
    <div hidden={surface !== 'cases'} className={`funds-trace app-shell cases-workspace${embedded ? ' embedded-workspace' : ''}`}>
    <aside className="sidebar" aria-label="案件导航">
      <div className="case-section"><div className="section-heading"><h2>案件</h2><span>{cases.length}</span><button className="button primary new-case-button" onClick={() => { setCreating(true); if (!uncertainCreate) { setCreateError(''); setCreatedCase(null); setCaseName(''); } }}><img className="ui-icon" src="/static/ui-icons/file-plus.svg" alt="" />新建案件</button></div><form className="case-filter-field" onSubmit={event => { event.preventDefault(); submitCaseSearch(); }}><EntityPicker className="case-filter" aria-label="筛选案件" placeholder="案件名称或 ID" value={caseSearch} onValueChange={setCaseSearch} scope={caseSuggestionScope} search={searchCaseSuggestions} kind="case" onSelect={item => { setCaseSearch(item.label); setCaseQuery({ text: '', id: item.key }); }} onSubmit={submitCaseSearch} onCompositionChange={value => { caseSearchComposing.current = value; }} disabled={operationBusy || !pageActive} /><button type="submit" className="case-filter-submit" aria-label="查询案件" disabled={operationBusy || !pageActive}><img className="ui-icon" src="/static/ui-icons/search.svg" alt="" /></button></form><div className="case-list" aria-label="案件列表">{casesLoading ? <p className="sidebar-hint">正在加载案件…</p> : visibleCases.map(item => <button key={item.id} className={`case-item ${selectedId === item.id ? 'selected' : ''}`} aria-label={item.name} aria-pressed={selectedId === item.id} disabled={operationBusy} onClick={() => void chooseCase(item.id)}><img className="case-glyph ui-icon" src="/static/ui-icons/folder.svg" alt="" /><span className="case-label">{item.name}<small>案件 ID {item.id} · {selectedId === item.id ? '当前案件' : retainedCaseIds.includes(item.id) ? '工作已保留 · 可继续' : '点击打开案件'}</small></span>{retainedCaseIds.includes(item.id) && <span className="retained-dot" title="工作已保留" />}</button>)}{!casesLoading && !cases.length && <p className="sidebar-hint">新建案件或导入案件包后开始核对。</p>}{!casesLoading && !!cases.length && !visibleCases.length && <p className="sidebar-hint">没有匹配的案件。</p>}</div></div>
      <div className="sidebar-footer"><button className="button secondary" disabled={operationBusy || (embedded && !hostContext)} onClick={() => void runOperation('import')}><img className="ui-icon" src="/static/ui-icons/upload.svg" alt="" />导入案件包</button><p>案件数据独立保存</p></div>
    </aside>
    <main className="standalone-main">
      <div className="case-context-bar"><span>案件与导入</span>{selected && <span className="current-case-label">当前案件 <strong>{selected.name}</strong></span>}</div>
      {error && <div role="alert" className="error-banner">{error}<button onClick={() => setListReload(value => value + 1)}>重新加载案件</button></div>}
      {pendingImports.length > 0 && <section aria-label="待核对导入" className="error-banner"><p>本案导入尚未结束或提交结果未确认；请核对原任务后继续写入。</p>{pendingImports.map(item => <button key={item.importId} disabled={!!checkingImport} onClick={() => void checkImport(item.importId)}>{checkingImport === item.importId ? '正在核对…' : '核对导入结果'} · {item.importId}</button>)}</section>}
      {importError && <p role="alert" className="error-banner">{importError}</p>}
      <FundsPanel active={pageActive && surface === 'cases'} revision={dataRevision} host={{ version: FUNDS_PANEL_VERSION, session }} workspace={{ initialDraft: queryDrafts.current.get(selectedId), onDraftChange: draft => { if (selectedId) queryDrafts.current.set(selectedId, draft); }, caseActions, retained: retainedCaseIds.includes(selectedId) }} />
    </main>
    {pageActive && surface === 'cases' && caseInfoOpen && selected && <Modal title="案件信息" onClose={() => setCaseInfoOpen(false)}><dl className="case-info"><dt>案件名称</dt><dd>{selected.name}</dd><dt>案件 ID</dt><dd className="mono">{selected.id}</dd><dt>创建时间</dt><dd>{cases.find(item => item.id === selected.id)?.created_at || '未提供'}</dd></dl><p className="case-info-note">本案件的流水、账户资料和画布独立保存。当前会话内保留的编辑现场，请在退出前保存画布。</p><footer className="modal-footer"><button className="button secondary" onClick={() => setCaseInfoOpen(false)}>关闭</button></footer></Modal>}
    {pageActive && surface === 'cases' && creating && <Modal title="新建案件" busy={createBusy} onClose={() => setCreating(false)}><form onSubmit={event => void create(event)} className="create-case-form"><label>案件名称<input autoFocus aria-label="案件名称" maxLength={200} disabled={!!createdCase || !!uncertainCreate} value={caseName} onChange={event => setCaseName(event.target.value)} placeholder="输入便于识别的案件名称" /></label>{uncertainCreate && <section className="uncertain-create" aria-label="核对新建案件结果"><p>创建结果尚未确认，请核对“{uncertainCreate.name}”。本次核对只读取案件列表。</p>{uncertainCreate.checked && (uncertainCreate.matches.length ? <><p>找到同名案件，请核对后选择打开：</p>{uncertainCreate.matches.map(item => <button key={item.id} type="button" className="button secondary" disabled={createBusy} onClick={() => void openCheckedCase(item)}>打开 {item.name} · ID {item.id}</button>)}</> : <p>当前列表未找到同名案件。可稍后再次核对，或确认后返回填写。</p>)}</section>}{createdCase && <p className="created-case-note">案件已创建，正在打开工作区。打开失败时可重试，或稍后从案件列表继续。</p>}{createError && <p role="alert" className="error-banner">{createError}</p>}<footer className="modal-footer"><button type="button" className="button secondary" disabled={createBusy} onClick={() => setCreating(false)}>取消</button>{uncertainCreate ? <><button type="button" className="button primary" disabled={createBusy} onClick={() => void checkUncertainCreate()}>{createBusy ? '正在核对…' : '核对案件列表'}</button>{uncertainCreate.checked && !uncertainCreate.matches.length && <button type="button" className="button secondary" disabled={createBusy} onClick={() => { setUncertainCreate(null); setCreateError(''); setCaseName(''); }}>已核对，重新填写</button>}</> : <button className="button primary" disabled={createBusy || !caseName.trim()}>{createBusy ? createdCase ? '正在打开…' : '正在创建…' : createdCase ? '打开已创建案件' : '创建案件'}</button>}</footer></form></Modal>}
  </div></>;
}
