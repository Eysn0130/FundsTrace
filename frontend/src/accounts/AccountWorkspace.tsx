import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { Modal } from '../components/Modal';
import { EntityPicker, type EntityPickerItem, type EntityPickerSearch } from '../components/EntityPicker';
import { displayTime, formatMoney } from '../lib/format';
import { accountRegistryApi, isAccountPage, isAccountProfile, isAccountSuggestions } from './api';
import type { AccountContext, AccountMutation, AccountPage, AccountProfile, AccountRegistryApi, AccountSource, AccountWorkspaceActions, CurrencyTotal, PhotoCrop, UnresolvedAccountOperation } from './types';
import './styles.css';

export type AccountWorkspaceProps = {
  context: AccountContext;
  cases: ReadonlyArray<{ id: string; name: string }>;
  onSelectCase(id: string): Promise<void>;
  revision: number;
  actions: AccountWorkspaceActions;
  unresolvedOperations?: ReadonlyArray<UnresolvedAccountOperation>;
  active?: boolean;
  api?: AccountRegistryApi;
};
type Drafts = Map<string, string>;
type OperationError = Error & { code?: string; operationId?: string };
type CropPreview = { file: File | null; url: string; objectUrl: boolean; crop: PhotoCrop; ready: boolean; ratio: number; error: string };
const icon = (name: string) => <img className="aw-icon" src={`/static/ui-icons/${name}.svg`} alt="" />;
const normalizedIdentity = (value: string) => value.replace(/\s/g, '').toUpperCase();
const identityValid = (value: string) => /^(?:\d{15}|\d{17}[\dX])$/.test(value);
const draftKey = (caseId: string, account: string) => JSON.stringify([caseId, account]);
const operationLabel = (kind: AccountMutation['kind']) => ({ 'identity-set': '身份证号', 'photo-set': '照片上传', 'photo-clear': '照片移除', 'photo-crop': '照片裁剪', 'main-set': '主账号标记' })[kind];

export function AccountWorkspace(props: AccountWorkspaceProps) {
  // Only user input is retained here, never fetched profiles, transactions or photos.
  const drafts = useRef<Drafts>(new Map());
  return <div className="funds-trace account-workspace"><BoundAccountWorkspace key={JSON.stringify([props.context.caseId, props.context.generation])} {...props} drafts={drafts.current} /></div>;
}

function BoundAccountWorkspace({ context, cases, onSelectCase, revision, actions, unresolvedOperations = [], active = true, api = accountRegistryApi, drafts }: AccountWorkspaceProps & { drafts: Drafts }) {
  const [source, setSource] = useState<AccountSource>('flows');
  const sourceId = useId();
  const sourceTabs = useRef<Partial<Record<AccountSource, HTMLButtonElement | null>>>({});
  const [searchDraft, setSearchDraft] = useState('');
  const [exactAccount, setExactAccount] = useState('');
  const composingSearch = useRef(false);
  const [query, setQuery] = useState<{ q: string; page: number; accountExact?: boolean }>({ q: '', page: 1 });
  const [pageData, setPageData] = useState<{ response: AccountPage; q: string; accountExact?: boolean } | null>(null);
  const page = pageData?.response.case_id === context.caseId && pageData.response.source === source && pageData.response.page === query.page && pageData.q === query.q && pageData.accountExact === query.accountExact ? pageData.response : null;
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [selectedAccount, setSelectedAccount] = useState('');
  const [profileValue, setProfile] = useState<AccountProfile | null>(null);
  const profile = profileValue?.case_id === context.caseId && profileValue.account === selectedAccount ? profileValue : null;
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState('');
  const [identity, setIdentity] = useState('');
  const [draftVersion, setDraftVersion] = useState(0);
  const [reload, setReload] = useState(0);
  const [status, setStatus] = useState('');
  const [actionError, setActionError] = useState('');
  const [busy, setBusy] = useState('');
  const [localUnknown, setLocalUnknown] = useState<UnresolvedAccountOperation[]>([]);
  const [clearIdentity, setClearIdentity] = useState(false);
  const [clearPhoto, setClearPhoto] = useState(false);
  const [discardCaseDrafts, setDiscardCaseDrafts] = useState(false);
  const [cropPreview, setCropPreview] = useState<CropPreview | null>(null);
  const busyMessage = busy.startsWith('reconcile:') ? '正在核对写入结果…' : ({
    'identity-set': clearIdentity ? '正在清除身份证号…' : '正在保存身份证号…', 'photo-set': '正在上传账户照片…', 'photo-clear': '正在移除账户照片…', 'photo-crop': '正在保存照片裁剪…', 'main-set': '正在更新主账号标记…',
    'select-case': '正在切换案件…', 'import-identity': '正在打开身份证对照表导入…', 'download-template': '正在下载身份证对照表模板…', 'main-accounts': '正在打开主账号管理…', 'view-flows': '正在打开账户流水…',
  } as Record<string, string>)[busy];
  const lifetime = useAbortLifetime('case');
  const selection = useAbortLifetime(selectedAccount);
  const lastProfileRevision = useRef<number | null>(null);
  const unknown = [...new Map([...localUnknown, ...unresolvedOperations].map(operation => [operation.operationId, operation])).values()];
  const writeBlocked = !active || !!busy || unknown.length > 0;
  const key = draftKey(context.caseId, selectedAccount);
  const dirty = !!profile && normalizedIdentity(identity) !== (profile.id_no || '');
  const caseDraftKeys = [...drafts.keys()].filter(value => JSON.parse(value)[0] === context.caseId);
  const caseHasDrafts = caseDraftKeys.length > 0;
  void draftVersion; // Input changes also update draft indicators on the account list.

  useEffect(() => () => { if (cropPreview?.objectUrl) URL.revokeObjectURL(cropPreview.url); }, [cropPreview?.url, cropPreview?.objectUrl]);
  useEffect(() => {
    if (!active) { setCropPreview(null); setClearIdentity(false); setClearPhoto(false); setDiscardCaseDrafts(false); }
  }, [active]);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController(); let current = true; let correctingPage = false;
    setListLoading(true); setListError(''); setPageData(null);
    api.list(context.caseId, { source, ...query, pageSize: 30 }, controller.signal).then(result => {
      if (!current || controller.signal.aborted) return;
      if (!isAccountPage(result) || result.case_id !== context.caseId || result.source !== source || result.page !== query.page || (query.accountExact && (result.total > 1 || result.items.some(item => item.account !== query.q)))) throw new Error('账户列表与当前案件不一致或响应不完整，请重新加载。');
      const lastPage = Math.max(1, Math.ceil(result.total / result.page_size));
      if (result.page > lastPage) {
        correctingPage = true;
        setQuery(current => current.q === query.q && current.page === query.page ? { ...current, page: lastPage } : current);
        return;
      }
      setPageData({ response: result, q: query.q, accountExact: query.accountExact });
      setSelectedAccount(current => result.items.some(item => item.account === current) ? current : result.items[0]?.account || '');
    }).catch(error => { if (current && !controller.signal.aborted) setListError(error.message); })
      .finally(() => { if (current && !controller.signal.aborted && !correctingPage) setListLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [api, context.caseId, source, query, revision, reload, active]);

  useEffect(() => {
    if (!active) return;
    setProfile(null); setProfileError(''); setProfileLoading(!!selectedAccount); setClearIdentity(false); setClearPhoto(false); setCropPreview(null);
    if (!selectedAccount) return;
    const controller = new AbortController(); let current = true;
    api.profile(context.caseId, selectedAccount, controller.signal).then(result => {
      if (!current || controller.signal.aborted || selection.signal.aborted) return;
      if (!isAccountProfile(result) || result.case_id !== context.caseId || result.account !== selectedAccount) throw new Error('账户资料与当前案件或账号不一致或响应不完整，请重新加载。');
      setProfile(result);
      const input = drafts.get(key);
      setIdentity(input ?? result.id_no ?? '');
      if (input !== undefined && normalizedIdentity(input) === (result.id_no || '')) drafts.delete(key);
      if (input !== undefined && lastProfileRevision.current !== null && lastProfileRevision.current !== revision) setStatus('资料已更新，未提交的身份证号已保留；请核对后保存或放弃修改。');
      lastProfileRevision.current = revision;
    }).catch(error => { if (current && !controller.signal.aborted && !selection.signal.aborted) setProfileError(error.message); })
      .finally(() => { if (current && !controller.signal.aborted && !selection.signal.aborted) setProfileLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [api, context.caseId, selectedAccount, selection, revision, reload, key, drafts, active]);

  function changeIdentity(value: string) {
    setIdentity(value); setStatus('');
    if (normalizedIdentity(value) === (profile?.id_no || '')) drafts.delete(key); else drafts.set(key, value);
    setDraftVersion(value => value + 1);
  }
  function abandonIdentity() { drafts.delete(key); setIdentity(profile?.id_no || ''); setDraftVersion(value => value + 1); setStatus('未提交的身份证号修改已放弃。'); }
  function abandonCaseDrafts() {
    if (writeBlocked) return;
    for (const draft of caseDraftKeys) drafts.delete(draft);
    setIdentity(profile?.id_no || ''); setDraftVersion(value => value + 1); setDiscardCaseDrafts(false);
    setStatus(`已放弃本案 ${caseDraftKeys.length} 个账户的未提交修改。`);
  }
  async function mutate(payload: AccountMutation): Promise<'saved' | 'unknown' | 'failed'> {
    if (writeBlocked || !profile || payload.account !== selectedAccount || selection.signal.aborted) return 'failed';
    const signal = selection.signal;
    setBusy(payload.kind); setActionError(''); setStatus('');
    try {
      await actions.mutate(payload, signal);
      if (signal.aborted || lifetime.signal.aborted) return 'failed';
      if (payload.kind === 'identity-set') { drafts.delete(key); setIdentity(payload.idNo); setDraftVersion(value => value + 1); }
      setStatus(`${operationLabel(payload.kind)}已更新。`); setReload(value => value + 1);
      return 'saved';
    } catch (reason) {
      if (signal.aborted || lifetime.signal.aborted) return 'failed';
      const error = reason as OperationError;
      if (error.code === 'write-unknown' && error.operationId) {
        setLocalUnknown(current => [...current.filter(item => item.operationId !== error.operationId), { operationId: error.operationId!, account: payload.account, kind: payload.kind }]);
        setActionError('写入结果尚未确认。请核对状态，核对前不能重复提交。'); return 'unknown';
      }
      setActionError(error.message || '操作失败，请重试。'); return 'failed';
    } finally { if (!lifetime.signal.aborted) setBusy(''); }
  }
  async function reconcile(operation: UnresolvedAccountOperation) {
    if (!active || busy || lifetime.signal.aborted) return;
    setBusy(`reconcile:${operation.operationId}`); setActionError('');
    try {
      await actions.reconcile(operation.operationId, lifetime.signal);
      if (lifetime.signal.aborted) return;
      setLocalUnknown(current => current.filter(item => item.operationId !== operation.operationId));
      setStatus('已核对写入结果，资料将重新加载。'); setReload(value => value + 1);
    } catch (reason) {
      if (lifetime.signal.aborted) return;
      const error = reason as OperationError;
      if (error.code === 'write-mismatch' || error.code === 'write-not-applied') {
        setLocalUnknown(current => current.filter(item => item.operationId !== operation.operationId));
        setActionError('已核对：资料尚未达到请求状态。请检查最新资料后重新确认操作。'); setReload(value => value + 1);
      } else setActionError(error.message || '仍无法确认写入结果，请稍后再次核对。');
    } finally { if (!lifetime.signal.aborted) setBusy(''); }
  }
  async function hostAction(name: string, action: (signal: AbortSignal) => void | Promise<void>, readOnly = false) {
    if (!active || busy || (!readOnly && unknown.length > 0) || lifetime.signal.aborted) return;
    setBusy(name); setActionError('');
    try { await action(lifetime.signal); if (!lifetime.signal.aborted) setReload(value => value + 1); }
    catch (error) { if (!lifetime.signal.aborted) setActionError((error as Error).message); }
    finally { if (!lifetime.signal.aborted) setBusy(''); }
  }
  async function copyAccount() {
    if (!profile || lifetime.signal.aborted) return;
    const account = profile.account;
    try {
      await navigator.clipboard.writeText(account);
      if (!lifetime.signal.aborted && !selection.signal.aborted) setStatus('账号已复制。');
    } catch {
      if (!lifetime.signal.aborted && !selection.signal.aborted) setActionError('无法访问剪贴板，请选择完整账号后手动复制。');
    }
  }
  function submitSearch() {
    if (!active || busy || composingSearch.current) return;
    setQuery({ q: exactAccount || searchDraft.trim(), page: 1, ...(exactAccount ? { accountExact: true } : {}) });
  }
  function search(event: FormEvent) { event.preventDefault(); submitSearch(); }
  function changeSearch(value: string) { setSearchDraft(value); setExactAccount(''); }
  function selectSuggestion(item: EntityPickerItem) {
    if (!active || busy || !item.account) return;
    setSearchDraft(item.account); setExactAccount(item.account);
    setQuery({ q: item.account, page: 1, accountExact: true });
  }
  const suggestionScope = active && !busy && api.suggestions ? JSON.stringify([context.caseId, context.generation, source, revision, reload]) : null;
  const searchSuggestions: EntityPickerSearch = async ({ query: q, offset, limit, signal }) => {
    if (!api.suggestions) throw new Error('当前账户数据源不支持候选读取。');
    const page = await api.suggestions(context.caseId, { source, q, offset, limit }, signal);
    if (!isAccountSuggestions(page) || page.case_id !== context.caseId || page.source !== source || page.query !== q.normalize('NFKC').trim() || page.items.length > limit
      || (page.has_more ? page.items.length !== limit || page.next_offset !== offset + page.items.length : page.next_offset !== null)) throw new Error('账户候选响应不完整或查询范围不一致，请重新输入。');
    return { items: page.items.map(item => ({ key: item.account, label: item.name || item.account, value: item.account, account: item.account, idNo: item.id_no || undefined,
      matchedField: item.matched_field === 'id' ? 'idNo' : item.matched_field === 'name' ? 'label' : 'account', matchedText: item.matched_text })), hasMore: page.has_more, nextOffset: page.next_offset ?? undefined };
  };
  function selectSource(next: AccountSource) {
    if (busy) return;
    setSource(next); setQuery({ q: '', page: 1 }); setSearchDraft(''); setExactAccount('');
  }
  function sourceKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (busy) return;
    const next = event.key === 'Home' ? 'flows' : event.key === 'End' ? 'identity'
      : ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) ? source === 'flows' ? 'identity' : 'flows' : null;
    if (!next) return;
    event.preventDefault(); selectSource(next); sourceTabs.current[next]?.focus();
  }
  function selectFile(file?: File) {
    if (!file || writeBlocked || !profile) return;
    setActionError('');
    if (!/\.(?:jpe?g|png|webp|bmp)$/i.test(file.name) || (file.type && !/^image\/(?:jpeg|png|webp|bmp|x-ms-bmp)$/i.test(file.type))) { setActionError('请选择 JPG、JPEG、PNG、WebP 或 BMP 图片。'); return; }
    if (file.size > 8 * 1024 * 1024) { setActionError('照片不能超过 8 MB。'); return; }
    setCropPreview({ file, url: URL.createObjectURL(file), objectUrl: true, crop: { cx: 0.5, cy: 0.5, r: 0.4 }, ready: false, ratio: 1, error: '' });
  }
  function recrop() {
    if (!profile?.photo || writeBlocked) return;
    setCropPreview({ file: null, url: api.photoUrl(context.caseId, profile.account, `${profile.photo.updated_at}:${revision}:${reload}`, true), objectUrl: false,
      crop: { cx: profile.photo.crop_cx, cy: profile.photo.crop_cy, r: profile.photo.crop_r }, ready: false, ratio: 1, error: '' });
  }
  async function confirmCrop() {
    if (!cropPreview?.ready || writeBlocked) return;
    const result = await mutate(cropPreview.file ? { kind: 'photo-set', account: selectedAccount, file: cropPreview.file, crop: cropPreview.crop } : { kind: 'photo-crop', account: selectedAccount, crop: cropPreview.crop });
    if (result !== 'failed' && !selection.signal.aborted) setCropPreview(null);
  }
  const totalPages = page ? Math.max(1, Math.ceil(page.total / page.page_size)) : 1;
  const visibleStatus = busyMessage && (clearIdentity || clearPhoto || cropPreview) ? '' : busyMessage || status;
  const progress = busyMessage && <p className="aw-status" role="status" aria-busy="true">{busyMessage}</p>;

  return <>
    <header className="aw-heading"><h1>账户与身份</h1><label className="aw-case-picker">当前案件<select aria-label="当前案件" aria-busy={busy === 'select-case'} value={context.caseId} disabled={writeBlocked} onChange={event => { void hostAction('select-case', () => onSelectCase(event.target.value)); }}>{!cases.some(item => item.id === context.caseId) && <option value={context.caseId}>{context.name}</option>}{cases.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><div className="aw-heading-actions"><button className="aw-button primary" aria-busy={busy === 'import-identity'} disabled={writeBlocked || caseHasDrafts} onClick={() => void hostAction('import-identity', actions.importIdentity)}>{icon('upload')}导入身份证对照表</button><button className="aw-text-button" aria-busy={busy === 'download-template'} disabled={!!busy} onClick={() => void hostAction('download-template', actions.downloadTemplate, true)}>{icon('download')}下载模板</button></div></header>
    {caseHasDrafts && <p className="aw-draft-note">有未保存的身份证号修改。导入对照表或管理主账号前，请保存或放弃修改。<button className="aw-text-button" disabled={writeBlocked} onClick={() => setDiscardCaseDrafts(true)}>放弃本案未提交修改</button></p>}
    {unknown.length > 0 && <div className="aw-unknown" role="alert"><strong>有 {unknown.length} 项写入结果尚未确认</strong><p>核对前暂停本案的新写入与案件切换；不会自动重放操作。</p>{unknown.map(operation => <div key={operation.operationId}><span className="mono">{operation.account}</span><span>{operationLabel(operation.kind)}</span><button className="aw-button" aria-busy={busy === `reconcile:${operation.operationId}`} disabled={!!busy} onClick={() => void reconcile(operation)}>核对状态</button></div>)}</div>}
    {actionError && <p className="aw-error" role="alert">{actionError}</p>}{visibleStatus && <p className="aw-status" role="status" aria-busy={!!busy}>{visibleStatus}</p>}
    <div className="aw-columns">
      <section className="aw-list" aria-label="账户列表"><div className="aw-list-toolbar"><div className="aw-tabs" role="tablist" aria-label="账户来源">{([{ value: 'flows', label: '本案流水账户' }, { value: 'identity', label: '身份证对照记录' }] as const).map(tab => <button key={tab.value} ref={node => { sourceTabs.current[tab.value] = node; }} id={`${sourceId}-${tab.value}`} role="tab" aria-selected={source === tab.value} aria-controls={`${sourceId}-panel`} tabIndex={source === tab.value ? 0 : -1} disabled={!!busy} onKeyDown={sourceKey} onClick={() => selectSource(tab.value)}>{tab.label}</button>)}</div><button className="aw-button" aria-busy={busy === 'main-accounts'} disabled={writeBlocked || caseHasDrafts} onClick={() => void hostAction('main-accounts', actions.openMainAccounts)}>{icon('settings')}主账号管理</button></div>
        <form className="aw-search" onSubmit={search}><div className="aw-search-field"><EntityPicker aria-label="搜索户名或账号" value={searchDraft} onValueChange={changeSearch} scope={suggestionScope} search={searchSuggestions} onSelect={selectSuggestion} onSubmit={submitSearch} kind="account" onCompositionChange={value => { composingSearch.current = value; }} placeholder="户名、账号或身份证号" disabled={!!busy || !active} /><button className="aw-search-submit" aria-label="查询账户" type="submit" disabled={!!busy || !active}>{icon('search')}</button></div>{query.q && <button type="button" className="aw-text-button" disabled={!!busy} onClick={() => { setSearchDraft(''); setExactAccount(''); setQuery({ q: '', page: 1 }); }}>重置</button>}</form>
        <div className="aw-table-scroll" id={`${sourceId}-panel`} role="tabpanel" aria-labelledby={`${sourceId}-${source}`} aria-busy={listLoading}>{listError ? <div className="aw-empty"><p role="alert">{listError}</p><button className="aw-button" onClick={() => setReload(value => value + 1)}>重新加载账户</button></div> : <><table className="aw-account-table"><thead><tr><th>户名与账号</th><th>身份资料</th><th>主账号来源</th><th>操作</th></tr></thead><tbody>{page?.items.map(item => <tr key={item.account} className={selectedAccount === item.account ? 'selected' : ''}><td><button className="aw-account-select" aria-label={`选择账户 ${item.account}`} aria-pressed={selectedAccount === item.account} disabled={!!busy} onClick={() => setSelectedAccount(item.account)}>{icon('user-search')}<span><strong>{item.name || '未提供户名'}</strong><small className="mono">{item.account}</small></span></button></td><td><span className="mono">{item.id_no || '未填写'}</span><small>{drafts.has(draftKey(context.caseId, item.account)) ? '有未提交修改' : item.has_flows ? '本案流水账户' : '仅身份对照记录'}</small></td><td>{item.is_main ? <span className="aw-main-label">是 · {item.main_source === 'auto' ? '自动' : '手动'}</span> : '否'}</td><td><button className="aw-text-button" disabled={!!busy} aria-label={`查看账户资料 ${item.account}`} onClick={() => setSelectedAccount(item.account)}>资料{icon('chevron-right')}</button></td></tr>)}</tbody></table>{listLoading ? <div className="aw-empty" role="status">正在加载当前案件账户…</div> : page && !page.items.length && <div className="aw-empty"><h2>{query.q ? '没有匹配的账户' : source === 'identity' ? '暂无身份证对照记录' : '暂无流水账户'}</h2><p>{query.q ? '调整户名或账号后重新查询。' : source === 'identity' ? '可导入对照表，或为流水账户补充身份证号。' : '导入流水后，本案涉及的账户会显示在这里。'}</p></div>}</>}</div>
        <footer className="aw-pagination"><span>{page ? `显示 ${page.total ? (page.page - 1) * page.page_size + 1 : 0}–${Math.min(page.page * page.page_size, page.total)} 条，共 ${page.total} 条` : '等待加载'}</span><div><button className="aw-button icon-only" aria-label="账户上一页" disabled={listLoading || !page || !!busy || query.page <= 1} onClick={() => setQuery(value => ({ ...value, page: value.page - 1 }))}>{icon('chevron-left')}</button><span>{page ? `第 ${page.page} / ${totalPages} 页` : listLoading ? '正在加载页码…' : '暂无页码'}</span><button className="aw-button icon-only" aria-label="账户下一页" disabled={listLoading || !page || !!busy || query.page >= totalPages} onClick={() => setQuery(value => ({ ...value, page: value.page + 1 }))}>{icon('chevron-right')}</button></div></footer>
      </section>

      <aside className="aw-profile" aria-label="账户资料"><h2>账户资料</h2>{profileLoading ? <div className="aw-empty" role="status">正在读取本案账户资料…</div> : profileError ? <div className="aw-empty"><p role="alert">{profileError}</p><button className="aw-button" onClick={() => setReload(value => value + 1)}>重新加载资料</button></div> : !profile ? <div className="aw-empty"><p>选择账户后查看资料。</p></div> : <>
        <dl className="aw-profile-identity"><div><dt>户名</dt><dd>{profile.name || '未提供户名'}</dd></div><div><dt>账号</dt><dd className="mono aw-profile-account"><span>{profile.account}</span><button className="aw-text-button" aria-label="复制完整账号" title="复制完整账号" onClick={() => void copyAccount()}>{icon('copy')}</button></dd></div></dl><p className="aw-profile-scope">{profile.has_flows ? '本案流水账户 · 无需上图即可维护资料' : '仅身份对照记录 · 本案暂无导入流水'}</p>
        <section className="aw-photo-section"><h3>账户头像</h3><div className="aw-photo-controls"><div className="aw-photo">{profile.photo ? <img src={api.photoUrl(context.caseId, profile.account, `${profile.photo.updated_at}:${revision}:${reload}`)} alt={`${profile.account} 账户照片`} /> : <>{icon('photo')}<span>尚未设置照片</span></>}</div><div><p>JPG / JPEG / PNG / WebP / BMP<br />大小不超过 8 MB</p><label className={`aw-button aw-file-label${writeBlocked ? ' disabled' : ''}`}>{icon('upload')}上传照片<input aria-label="选择账户照片" type="file" accept=".jpg,.jpeg,.png,.webp,.bmp,image/jpeg,image/png,image/webp,image/bmp" disabled={writeBlocked} onChange={event => { selectFile(event.target.files?.[0]); event.target.value = ''; }} /></label>{profile.photo && <div className="aw-photo-secondary"><button className="aw-text-button" disabled={writeBlocked} onClick={recrop}>重新裁剪</button><button className="aw-text-button danger" disabled={writeBlocked} onClick={() => setClearPhoto(true)}>移除照片</button></div>}</div></div></section>
        <section className="aw-identity-editor"><h3><label htmlFor="aw-identity-input">身份证号</label></h3><div><input id="aw-identity-input" aria-label="身份证号" className="mono" maxLength={32} value={identity} disabled={writeBlocked} onChange={event => changeIdentity(event.target.value)} placeholder="15 或 18 位身份证号" /><button className="aw-button" aria-busy={busy === 'identity-set'} disabled={writeBlocked || !dirty || !identityValid(normalizedIdentity(identity))} onClick={() => void mutate({ kind: 'identity-set', account: profile.account, idNo: normalizedIdentity(identity) })}>保存</button></div>{dirty && <p className="aw-editor-note">未提交的修改会随账户保留。<button className="aw-text-button" disabled={writeBlocked} onClick={abandonIdentity}>放弃修改</button></p>}{identity && !identityValid(normalizedIdentity(identity)) && <p className="aw-editor-note">请输入 15 位数字，或 17 位数字加数字 / X。</p>}{profile.id_no && <button className="aw-text-button danger" disabled={writeBlocked} onClick={() => setClearIdentity(true)}>清除身份证号…</button>}</section>
        <section className="aw-main-section"><div><h3>主账号标识</h3><span className={`aw-main-badge${profile.is_main ? ' active' : ''}`}>{profile.is_main ? `是 · ${profile.main_source === 'auto' ? '自动' : '手动'}` : '否'}</span><p>{profile.main_source === 'auto' ? '由已有自动识别规则标记；可明确改为手动。' : profile.is_main ? '已由人工确认标记。' : '尚未标记为主账号。'}{!profile.has_flows && !profile.is_main && <><br />主账号从本案流水账户设置。</>}{profile.main_marked_at && <><br />标记时间：{displayTime(profile.main_marked_at)}</>}</p></div><div className="aw-main-buttons">{profile.main_source === 'auto' && <button className="aw-button" aria-busy={busy === 'main-set'} disabled={writeBlocked} onClick={() => void mutate({ kind: 'main-set', account: profile.account, on: true })}>改为手动</button>}<button className="aw-button" aria-busy={busy === 'main-set'} disabled={writeBlocked || (!profile.has_flows && !profile.is_main)} onClick={() => void mutate({ kind: 'main-set', account: profile.account, on: !profile.is_main })}>{profile.is_main ? '取消主账号' : '设为主账号'}</button></div></section>
        <section className="aw-transactions"><div className="aw-section-heading"><h3>本案交易</h3><button className="aw-text-button" aria-busy={busy === 'view-flows'} disabled={!!busy || !profile.has_flows} onClick={() => void hostAction('view-flows', signal => actions.viewFlows(profile.account, signal))}>查看流水{icon('chevron-right')}</button></div><dl><div><dt>交易笔数</dt><dd>{profile.cnt}</dd></div><div><dt>收入笔数</dt><dd>{profile.in_cnt}</dd></div><div><dt>支出笔数</dt><dd>{profile.out_cnt}</dd></div><MoneyRows label="收入合计" totals={profile.in_totals} /><MoneyRows label="支出合计" totals={profile.out_totals} /><div><dt>最早交易时间</dt><dd className="mono">{displayTime(profile.tmin)}</dd></div><div><dt>最晚交易时间</dt><dd className="mono">{displayTime(profile.tmax)}</dd></div></dl>{!profile.has_flows && <p className="aw-editor-note">本案暂无该账户的导入流水；收支为 0，交易时间为空。</p>}<p className="aw-editor-note">按本案全部导入流水、分币种统计；不受当前画布或列表筛选影响。</p>{profile.top_counterparties.length > 0 && <details className="aw-counterparties"><summary>主要交易对手</summary>{profile.top_counterparties.map(item => <div key={item.acct}><strong>{item.name || '未提供户名'}</strong><span className="mono">{item.acct}</span><small>收入 {item.in_cnt} 笔 · 支出 {item.out_cnt} 笔</small></div>)}</details>}</section>
      </>}</aside>
    </div><footer className="aw-footnote">资料仅属于当前案件；同名账户按账号分别维护。</footer>

    {active && discardCaseDrafts && <Modal title="放弃本案未提交修改" busy={!!busy} onClose={() => setDiscardCaseDrafts(false)}><div className="aw-confirm"><p>放弃“{context.name}”中 {caseDraftKeys.length} 个账户的未提交身份证号修改？</p><p>此操作只清除本案的未提交输入，已保存资料和其他案件的未提交修改会保留。</p></div><footer className="modal-footer"><button className="aw-button" disabled={!!busy} onClick={() => setDiscardCaseDrafts(false)}>取消</button><button className="aw-button danger" disabled={writeBlocked} onClick={abandonCaseDrafts}>确认放弃本案修改</button></footer></Modal>}
    {active && clearIdentity && profile && <Modal title="清除身份证号" busy={!!busy} onClose={() => setClearIdentity(false)}><div className="aw-confirm"><p>清除账户 <span className="mono">{profile.account}</span> 已保存的身份证号？</p><p>此操作仅清除身份证号，不删除账户和流水。</p></div>{progress}<footer className="modal-footer"><button className="aw-button" disabled={!!busy} onClick={() => setClearIdentity(false)}>取消</button><button className="aw-button danger" aria-busy={busy === 'identity-set'} disabled={writeBlocked} onClick={() => { void mutate({ kind: 'identity-set', account: profile.account, idNo: '' }).then(result => { if (result !== 'failed') setClearIdentity(false); }); }}>确认清除身份证号</button></footer></Modal>}
    {active && clearPhoto && profile && <Modal title="移除账户照片" busy={!!busy} onClose={() => setClearPhoto(false)}><div className="aw-confirm"><p>移除账户 <span className="mono">{profile.account}</span> 的照片及裁剪信息？</p></div>{progress}<footer className="modal-footer"><button className="aw-button" disabled={!!busy} onClick={() => setClearPhoto(false)}>取消</button><button className="aw-button danger" aria-busy={busy === 'photo-clear'} disabled={writeBlocked} onClick={() => { void mutate({ kind: 'photo-clear', account: profile.account }).then(result => { if (result !== 'failed') setClearPhoto(false); }); }}>确认移除照片</button></footer></Modal>}
    {active && cropPreview && <Modal title={cropPreview.file ? '裁剪并上传账户照片' : '重新裁剪账户照片'} busy={!!busy} onClose={() => setCropPreview(null)}><div className="aw-crop"><p>账户 <span className="mono">{selectedAccount}</span> · 调整圆形选区后确认。</p><div className="aw-crop-image" style={{ width: `min(510px, ${45 * cropPreview.ratio}vh)` }}><img src={cropPreview.url} alt="照片裁剪预览" onLoad={event => { const image = event.currentTarget; setCropPreview(current => current && current.url === cropPreview.url ? { ...current, ready: true, ratio: image.naturalWidth && image.naturalHeight ? image.naturalWidth / image.naturalHeight : 1, error: '' } : current); }} onError={() => setCropPreview(current => current && current.url === cropPreview.url ? { ...current, ready: false, error: '无法读取照片，请选择有效的图片文件。' } : current)} /><span className="aw-crop-circle" aria-hidden="true" style={{ left: `${cropPreview.crop.cx * 100}%`, top: `${cropPreview.crop.cy * 100}%`, width: `${Math.min(1, 1 / cropPreview.ratio) * cropPreview.crop.r * 200}%` }} /></div>{cropPreview.error && <p role="alert" className="aw-error">{cropPreview.error}</p>}{(['cx', 'cy', 'r'] as const).map((field, index) => <label key={field}>{['水平位置', '垂直位置', '选区半径'][index]}<input type="range" aria-label={['裁剪水平位置', '裁剪垂直位置', '裁剪半径'][index]} min={field === 'r' ? 5 : 0} max="100" value={Math.round(cropPreview.crop[field] * 100)} disabled={!!busy} onChange={event => { const value = Number(event.target.value) / 100; setCropPreview(current => current && { ...current, crop: { ...current.crop, [field]: value } }); }} /><span>{Math.round(cropPreview.crop[field] * 100)}%</span></label>)}</div>{progress}<footer className="modal-footer"><button className="aw-button" disabled={!!busy} onClick={() => setCropPreview(null)}>取消</button><button className="aw-button primary" aria-busy={busy === 'photo-set' || busy === 'photo-crop'} disabled={writeBlocked || !cropPreview.ready} onClick={() => void confirmCrop()}>{cropPreview.file ? '确认裁剪并上传' : '确认保存裁剪'}</button></footer></Modal>}
  </>;
}

function MoneyRows({ label, totals }: { label: string; totals: CurrencyTotal[] }) {
  return totals.length ? totals.map(total => <div key={total.currency}><dt>{label}（{total.currency || '未标注币种'}）</dt><dd className="mono">{formatMoney(total.amount)}</dd></div>) : <div><dt>{label}</dt><dd className="mono">0.00</dd></div>;
}

function useAbortLifetime(key: string) {
  const scope = useMemo(() => {
    let controller = new AbortController();
    return { get signal() { return controller.signal; }, reset() { if (controller.signal.aborted) controller = new AbortController(); }, abort() { controller.abort(); } };
  }, [key]);
  useEffect(() => { scope.reset(); return () => scope.abort(); }, [scope]);
  return scope;
}
