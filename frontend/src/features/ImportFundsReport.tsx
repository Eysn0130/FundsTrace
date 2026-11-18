import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { ImportReport, ImportReportIntegrity, ImportReportPeer } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsHostActionsV1, FundsReportQueryV1, FundsReportRecordsQueryV1 } from '../panel/host';
import { linkAbortSignal } from '../panel/cancellation';
import { displayOperationalTime, displayTime, formatMoney, formatReportMoney, reportMoneyUnit } from '../lib/format';
import { ImportCellValue, ImportTable } from './ImportTable';
import { ImportIcon } from './ImportTree';
import { ImportReportTrend, reportTimeBasis, trendPeriod, type TrendMode } from './ImportReportTrend';
import { ImportReportRecords } from './ImportReportRecords';
import { moneyUnits, plotRatio } from './report-geometry';
import { ImportReportAccountFilter } from './ImportReportAccountFilter';
import { ImportTrendFilter } from './ImportTrendFilter';

const chapters = [['integrity', '资料与核验'], ['accounts', '账户收支'], ['peers', '主要往来'], ['time', '时间变化'], ['relations', '关联明细'], ['method', '统计口径']] as const;
export type ImportReportChapter = typeof chapters[number][0];
const count = (value: number) => value.toLocaleString('zh-CN');
export function reportPeriod(first: string | null, last: string | null) {
  const day = (value: string | null) => value?.match(/^(\d{4})-(\d{2})-(\d{2})/)?.slice(1).join('') || '';
  const show = (value: string) => value ? `${value.slice(0,4)}年${value.slice(4,6)}月${value.slice(6,8)}日` : '—';
  const start = day(first), end = day(last);
  if (!start && !end) return '—';
  return `${show(start)}至${show(end)}`;
}
export function reportFileSize(value: number) {
  if (value < 1024) return `${value.toLocaleString('zh-CN')} B`;
  const unit = value < 1024 * 1024 ? 'KB' : 'MB', scale = unit === 'KB' ? 1024 : 1024 * 1024;
  return `${(value / scale).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} ${unit}`;
}

export function reportNarrative(data: ImportReport) {
  const scope = data.perspective === 'own_ledger' ? '本方账户' : '交易端点', overview = data.overview;
  const selected = overview?.selected_account || (data.accounts.total === 1 ? data.accounts.items[0] : undefined);
  const names = selected?.names.join('、');
  const lead = !overview ? '' : overview.account_count === 1 && selected
    ? `本表${overview.name_count > 1 ? `记载户名${count(overview.name_count)}个${selected.names[0] ? `（含“${selected.names[0]}”等）` : ''}` : names ? `记载户名“${names}”` : '户名未载明'}，涉及卡／账号标识${count(overview.number_count)}个；`
    : `本表记载户名${count(overview.name_count)}个，涉及${scope}${count(overview.account_count)}个、卡／账号标识${count(overview.number_count)}个；`;
  return `${lead}${overview ? '' : scope}${directionalNarrative(data.summary, data.currency)}${overview && overview.account_count > 1 && overview.unnamed_account_count ? `所涉账户中有${count(overview.unnamed_account_count)}个未载明户名。` : ''}`;
}

const directionalNarrative = (row: ImportReport['summary'], currency: string | null) => {
  const side = (direction: 'in' | 'out', label: string) => row[`${direction}_count`] === 0 ? `未见${label}记录`
    : `${label}${count(row[`${direction}_count`])}笔，${row[`${direction}_amount`] === null ? '金额未明确' : `合计${formatReportMoney(row[`${direction}_amount`], currency)}`}`;
  return `${side('in','进账')}；${side('out','出账')}，${row.net_amount === null ? '收支差额未明确' : `收支差额${formatReportMoney(row.net_amount, currency)}`}（${currency || '币种未明确'}）。`;
};

export function reportNarrativeDetails(data: ImportReport) {
  if (!data.overview || data.overview.account_count <= 1 || data.overview.name_count === 1) return [];
  return data.accounts.items.filter(row => !data.account || row.account === data.account).map(row => {
    const name = row.names.length > 1 ? `以${row.names[0]}等名称记载的账户` : row.names[0] || '户名未载明账户';
    const number = row.account_number || row.account_numbers.find(Boolean) || '卡／账号未载明';
    const sides = (['in','out'] as const).map(direction => {
      const label = direction === 'in' ? '进账' : '出账';
      return row[`${direction}_count`] === 0 ? `未见${label}记录` : `${label}${count(row[`${direction}_count`])}笔${row[`${direction}_amount`] === null ? '，金额未明确' : `计${formatReportMoney(row[`${direction}_amount`], row.currency)}`}`;
    });
    return `${name}（${number}）${sides.join('，')}`;
  });
}

export function reportAccountNarrative(data: ImportReport) {
  const details = reportNarrativeDetails(data);
  return details.length ? `其中，${details.join('；')}。` : '';
}

function Ranking({ title, rows, currency, direction, onSelect, interactive }: { title: string; rows: ImportReportPeer[]; currency: string | null; direction: 'in' | 'out'; onSelect: (row: ImportReportPeer) => void; interactive: boolean }) {
  const amounts = rows.map(row => row.amount === null ? null : moneyUnits(row.amount)), max = amounts.reduce<bigint>((a, b) => b !== null && b > a ? b : a, 0n);
  return <section className="ft-report-ranking" data-direction={direction}><h5>{title}前10 <span>实际 {rows.length}</span></h5>{rows.length ? <ol>{rows.map((row, index) => <li key={row.account}><button type="button" disabled={!interactive} onClick={() => onSelect(row)} aria-label={`${title}第${index + 1}，${row.names.join('、') || row.account_number}，${row.account_number}，${formatReportMoney(row.amount, currency)}，${row.count}笔`}>
    <span className="ft-report-rank">{index + 1}</span><span className="ft-report-peer-name" title={`${row.names.join('、')}\n${row.account_number}`}><span>{row.names.join('、') || '户名未提供'}</span><span>（{row.account_number.length > 10 ? `…${row.account_number.slice(-5)}` : row.account_number}）</span></span><span className="ft-report-peer-amount">{formatMoney(row.amount)} <small>（{row.count}笔）</small></span><span className="ft-report-rank-track"><span style={{ width: `${amounts[index] === null ? 0 : Math.max(0, Math.min(100, plotRatio(amounts[index]!, 0n, max) * 100))}%` }} /></span>
  </button></li>)}</ol> : <p className="ft-report-empty">当前范围没有{direction === 'in' ? '进账' : '出账'}记录</p>}</section>;
}

/** File tree is outside this component. Both the outline and its scroll root belong to the right pane. */
export function ImportFundsReport({ client, scopeSignal, sourceId, memberPath, header, actions, chapter, onChapter }: {
  client: FundsCaseClientV1; scopeSignal: AbortSignal; sourceId: string; memberPath: string; header: ReactNode; actions?: FundsHostActionsV1;
  chapter: ImportReportChapter; onChapter: (value: ImportReportChapter) => void;
}) {
  const [query, setQuery] = useState<FundsReportQueryV1>({ memberPath, page: 1 }), [reload, setReload] = useState(0);
  const [trendMode,setTrendMode]=useState<TrendMode>('combined');
  const [snapshot, setSnapshot] = useState<{ client: FundsCaseClientV1; key: string; baseKey: string; data: ImportReport }>(), [failure, setFailure] = useState<{ key: string; message: string }>();
  const [verification, setVerification] = useState<{ revision: string; value: ImportReportIntegrity }>(), [checking, setChecking] = useState(false);
  const [actionError, setActionError] = useState(''), [copyState, setCopyState] = useState(''), [exporting, setExporting] = useState(false);
  const [drill, setDrill] = useState<{ query: FundsReportRecordsQueryV1; label: string; anchor: HTMLElement | null }>();
  const content = useRef<HTMLDivElement>(null), drillElement = useRef<HTMLDivElement>(null), lifetime = useRef<AbortController>(new AbortController()), sectionRefs = useRef(new Map<string, HTMLElement>());
  const navigateTarget = useRef<ImportReportChapter | undefined>(chapter), id = useId(), key = JSON.stringify(query);
  const baseKey=JSON.stringify({...query,timelineScope:undefined,timelineAccounts:undefined,timelineSearch:undefined,timelineGrain:undefined,timelinePage:undefined,expectedRevision:undefined});
  const data = snapshot?.client === client && (snapshot.key === key || snapshot.baseKey===baseKey && failure?.key!==key) && !scopeSignal.aborted ? snapshot.data : undefined;
  const timeUpdating=!!data&&snapshot?.key!==key;
  const accountNarrative = data ? reportAccountNarrative(data) : '';
  const moneyUnit = reportMoneyUnit(data?.currency ?? null);
  const methodParagraphs = data?.method_paragraphs?.length ? data.method_paragraphs : data?.notes.map(text => ({ title: '', text })) || [];
  const error = failure?.key === key ? failure.message : '';
  const integrity = data && verification?.revision === data.analysis_revision ? verification.value : data?.integrity;
  useEffect(() => {
    const controller = new AbortController(), unlink = linkAbortSignal(scopeSignal, controller); lifetime.current = controller; let active = true;
    setChecking(false); setExporting(false); setCopyState('');
    const revoke = () => { setSnapshot(undefined); setFailure(undefined); setDrill(undefined); };
    scopeSignal.addEventListener('abort', revoke, { once: true });
    client.importFundsReport!(sourceId, query, controller.signal).then(value => {
      if (!active || controller.signal.aborted) return;
      if (value.case_id !== client.binding.caseId || value.source_id !== sourceId || value.member_path !== memberPath || query.expectedRevision && value.analysis_revision !== query.expectedRevision) throw new Error('报告范围或修订与当前文件不一致，请刷新');
      setSnapshot({ client, key, baseKey, data: value }); setFailure(undefined);
    }).catch(reason => { if (active && !controller.signal.aborted) setFailure({ key, message: reason.message }); });
    return () => { active = false; scopeSignal.removeEventListener('abort', revoke); unlink(); controller.abort(); };
  }, [client, scopeSignal, sourceId, memberPath, key, baseKey, reload]);
  const jump = (value: ImportReportChapter, smooth = true) => {
    const parent = content.current, section = sectionRefs.current.get(value);
    if (!parent || !section) return;
    const headingHeight = parent.querySelector('.ft-inspector-title')?.getBoundingClientRect().height || 0;
    const top = value === 'integrity' ? 0 : parent.scrollTop + section.getBoundingClientRect().top - parent.getBoundingClientRect().top - headingHeight - 18;
    parent.scrollTo?.({ top, behavior: smooth && !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'smooth' : 'auto' });
    onChapter(value);
  };
  useLayoutEffect(() => { if (data && navigateTarget.current) { jump(navigateTarget.current, false); navigateTarget.current = undefined; } }, [data]);
  useLayoutEffect(() => { if (drill) { const parent = content.current, element = drillElement.current; if (parent && element) parent.scrollTo?.({ top: Math.max(0, parent.scrollTop + element.getBoundingClientRect().top - parent.getBoundingClientRect().top - (parent.querySelector('.ft-inspector-title')?.getBoundingClientRect().height || 0) - 18), behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); } }, [drill]);
  const changeQuery = (patch: Partial<FundsReportQueryV1>, target = chapter) => {
    if (!data) return;
    navigateTarget.current = target; setDrill(undefined); setActionError('');
    setQuery(current => ({ ...current, ...patch, ...('account' in patch || 'currency' in patch ? {timelineScope:undefined,timelineAccounts:undefined,timelineSearch:undefined,timelinePage:1}:{}), expectedRevision: data.analysis_revision }));
  };
  const openRecords = (label: string, patch: Partial<FundsReportRecordsQueryV1> = {}) => {
    if (!data || !client.importReportRecords) return;
    setDrill({ label, anchor: document.activeElement as HTMLElement | null, query: { memberPath, expectedRevision: data.analysis_revision, ...(data.currency !== null ? { currency: data.currency } : {}), ...(query.account ? { account: query.account } : {}), ...patch } });
  };
  const closeDrill = () => {
    const anchor = drill?.anchor, parent = content.current; setDrill(undefined);
    if (anchor?.isConnected && parent) {
      parent.scrollTo?.({ top: Math.max(0, parent.scrollTop + anchor.getBoundingClientRect().top - parent.getBoundingClientRect().top - (parent.querySelector('.ft-inspector-title')?.getBoundingClientRect().height || 0) - 26), behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
      anchor.focus({ preventScroll: true });
    }
  };
  const verify = async () => {
    if (!data || !client.verifyImportSource || checking) return;
    const signal = lifetime.current.signal; setChecking(true); setActionError('');
    try { const value = await client.verifyImportSource(sourceId, { memberPath, expectedRevision: data.analysis_revision }, signal); if (!signal.aborted) setVerification({ revision: data.analysis_revision, value }); }
    catch (reason) { if (!signal.aborted) setActionError((reason as Error).message); }
    finally { if (!signal.aborted) setChecking(false); }
  };
  const copy = async () => {
    if (!data) return;
    const signal = lifetime.current.signal;
    try { await navigator.clipboard.writeText(`${data.scope.filename}\n${memberPath}\n${[reportNarrative(data), accountNarrative].filter(Boolean).join('\n')}\n\n${methodParagraphs.map(p => `${p.title ? `${p.title}。` : ''}${p.text}`).join('\n\n')}`); if (!signal.aborted) setCopyState('已复制'); }
    catch { if (!signal.aborted) setCopyState('复制失败'); }
  };
  const download = async () => {
    if (!data || !actions?.downloadFundsReport || exporting) return;
    const signal = lifetime.current.signal; setExporting(true); setActionError('');
    try { await actions.downloadFundsReport({ sourceId, filename: `${data.scope.filename}-资金统计简报.html`, memberPath, expectedRevision: data.analysis_revision, currency: query.currency ?? data.currency ?? undefined, account: query.account, peerAccount: query.peerAccount, timelineGrain: query.timelineGrain, timelinePage: query.timelinePage, timelineScope:query.timelineScope,timelineAccounts:query.timelineAccounts,timelineSearch:query.timelineSearch, page: query.page, accountsPage: query.accountsPage, relationsPage: query.relationsPage }, signal); }
    catch (reason) { if (!signal.aborted) setActionError((reason as Error).message); }
    finally { if (!signal.aborted) setExporting(false); }
  };
  if (scopeSignal.aborted) return null;
  const section = (name: ImportReportChapter) => ({ id: `${id}-${name}`, ref: (element: HTMLElement | null) => { if (element) sectionRefs.current.set(name, element); else sectionRefs.current.delete(name); } });
  const status = { registered: '接收指纹已登记 · 本次留存复核待执行', unavailable: '原件或基准不可用', match: '留存字节与登记指纹一致', mismatch: '留存复核不一致' };
  const hash = integrity?.expected_sha256;
  const hashValue = integrity ? `${integrity.object_name}\nSHA-256登记值：${integrity.expected_sha256 || '未登记'}\n本次复核值：${integrity.observed_sha256 || (integrity.checked_at ? '未取得（复核未通过）' : '未执行')}\n${status[integrity.status]}${integrity.checked_at ? `\n复核时间：${displayOperationalTime(integrity.checked_at)}` : ''}` : '核验状态不可用';
  const pagination = (table: 'accounts' | 'relations') => {
    if (!data || data[table].total <= data[table].page_size) return null;
    const current = data[table], pages = Math.ceil(current.total / current.page_size), property = table === 'accounts' ? 'accountsPage' : 'relationsPage';
    return <div className="ft-report-pagination" aria-label={table === 'accounts' ? '账户收支分页' : '关联明细分页'}><span>共 {count(current.total)} 项</span><button className="ft-text-button" disabled={current.page <= 1} onClick={() => changeQuery({ [property]: current.page - 1 })}>上一页</button><span>{current.page} / {pages}</span><button className="ft-text-button" disabled={current.page >= pages} onClick={() => changeQuery({ [property]: current.page + 1 })}>下一页</button></div>;
  };
  return <div className="ft-report-layout">
    <nav className="ft-report-outline" aria-label="报告目录"><h4>报告目录</h4>{chapters.map(([name, label]) => <a key={name} href={`#${id}-${name}`} aria-current={chapter === name ? 'location' : undefined} onClick={event => { event.preventDefault(); jump(name); }}>{label}</a>)}</nav>
    <div className="ft-report-content" ref={content} onScroll={() => {
      const parent = content.current; if (!parent || !data) return;
      const line = parent.getBoundingClientRect().top + (parent.querySelector('.ft-inspector-title')?.getBoundingClientRect().height || 0) + 45;
      let visible: ImportReportChapter = 'integrity';
      for (const [name] of chapters) if ((sectionRefs.current.get(name)?.getBoundingClientRect().top ?? Infinity) <= line) visible = name;
      if (parent.scrollTop + parent.clientHeight >= parent.scrollHeight - 2) visible = 'method';
      if (visible !== chapter) onChapter(visible);
    }}>
      {header}
      {!data && <div className="ft-report-loading" role={error ? 'alert' : 'status'}><ImportIcon name={error ? 'import-circle-alert' : 'import-loader'} className={error ? '' : 'is-spinning'} /><span>{error || '正在汇总本文件资金统计…'}</span>{error && <button className="button secondary" onClick={() => { setFailure(undefined); setQuery({ memberPath, page: 1 }); setReload(value => value + 1); }}>刷新报告</button>}</div>}
      {data && <>
        <div className="ft-report-document-title"><h3>资金统计分析报告</h3><div className="ft-report-document-actions"><button type="button" className="button secondary" disabled={timeUpdating} onClick={() => void copy()}><ImportIcon name="copy" />{copyState || '复制统计说明'}</button>{actions?.downloadFundsReport && <button type="button" className="button secondary" disabled={exporting||timeUpdating} onClick={() => void download()}><ImportIcon name="download" />{exporting ? '正在导出…' : '导出简报'}</button>}</div></div>
        {(data.currencies.length > 1 || query.account) && <div className="ft-report-scope">{data.currencies.length > 1 ? <label>币种<select aria-label="报告币种" value={data.currency || ''} onChange={event => changeQuery({ currency: event.target.value, account: undefined, peerAccount: undefined, timelinePage: 1, page: 1, accountsPage: 1, relationsPage: 1 })}>{data.currencies.map(value => <option key={value} value={value}>{value || '币种未明确'}</option>)}</select></label> : null}{query.account && <><span className="ft-report-current-account" title={`${data.overview?.selected_account?.names.join('、') || ''} · ${data.overview?.selected_account?.account_number || query.account}`}>当前账户：{data.overview?.selected_account?.account_number || query.account}</span><button className="ft-text-button" onClick={() => changeQuery({ account: undefined, timelinePage: 1, page: 1, relationsPage: 1 })}>全部{data.perspective === 'own_ledger' ? '本方账户' : '交易端点'}</button></>}</div>}

        {data.quality.unattributed_transactions > 0 && <p className="ft-report-meta">另有 {data.quality.unattributed_transactions} 笔未取得本方角色，不计本方收支。</p>}
        <section {...section('integrity')} className="ft-report-section"><h4>一、资料与核验 <span className="ft-report-meta">{data.perspective === 'own_ledger' ? '本方账簿' : '交易端点'} · 来源 {count(data.quality.source_records)} 条 · 关联 {count(data.quality.selected_transactions)} 笔</span></h4>
          <ImportTable fillToSlot autoSize textMaxWidth={220} className="ft-report-grid ft-material-grid" label="检材资料，可上下左右滚动" headings={['检材名称','文件名','文件大小','数据行数','SHA256 校验值']} widths={[200,240,100,100,240]}><tbody><tr>
            <td data-fit-text><ImportCellValue fullValue={data.scope.material_origin_name ? `${data.scope.material_name || '未登记'}\n原检材名称：${data.scope.material_origin_name}` : data.scope.material_name ? undefined : '历史原件尚未登记检材编号'}>{data.scope.material_name || '—'}</ImportCellValue></td>
            <td data-fit-text><ImportCellValue>{data.scope.filename}</ImportCellValue></td>
            <td><ImportCellValue fullValue={`${data.scope.source_size_bytes.toLocaleString('zh-CN')} B`}>{reportFileSize(data.scope.source_size_bytes)}</ImportCellValue></td>
            <td><ImportCellValue fullValue="所属留存文件中的登记数据记录，含重复及未计入资金的记录">{data.scope.source_record_total == null ? '—' : count(data.scope.source_record_total)}</ImportCellValue></td>
            <td data-fit-text data-fit-extra={client.verifyImportSource ? '32' : '0'}><div className="ft-material-hash" data-tone={integrity?.status === 'mismatch' ? 'danger' : integrity?.status === 'match' ? 'success' : undefined}><button type="button" className="ft-report-cell-button ft-report-hash" aria-label="查看完整SHA-256指纹" onClick={event => event.currentTarget.querySelector<HTMLElement>('.ft-cell-value')?.focus()}><ImportCellValue fullValue={hashValue}>{hash || '—'}</ImportCellValue></button>{client.verifyImportSource && <button type="button" className="ft-material-verify" aria-label="复核留存" title={integrity ? status[integrity.status] : '核验状态不可用'} disabled={checking} onClick={() => void verify()}><ImportIcon name={checking ? 'import-loader' : integrity?.status === 'match' ? 'import-circle-check' : integrity?.status === 'mismatch' || integrity?.status === 'unavailable' ? 'import-circle-alert' : 'import-circle'} className={checking ? 'is-spinning' : ''}/></button>}</div><span className="ft-sr-only" role="status">{integrity ? status[integrity.status] : '核验状态不可用'}</span></td>
          </tr></tbody></ImportTable>
        </section>
        {actionError && <p className="ft-report-action-error" role="alert">{actionError}</p>}
        <section {...section('accounts')} className="ft-report-section"><h4>二、{data.perspective === 'own_ledger' ? '账户资金收支' : '交易端点收支'}</h4><p className="ft-report-narrative">{reportNarrative(data)}</p>{accountNarrative && <p className="ft-report-narrative ft-report-account-narrative">{accountNarrative}</p>}{data.overview && data.overview.account_count > data.accounts.page_size && !data.account && <p className="ft-report-meta">分项说明随下表分页展示，总述统计完整范围。</p>}<div className="ft-report-caption">表1 {data.perspective === 'own_ledger' ? '本方账户' : '交易端点'}收支（{query.account ? '全文件，' : ''}{moneyUnit}）</div>
          <ImportTable fillToSlot textMaxWidth={160} className="ft-report-grid" label="账户资金收支，可上下左右滚动" headings={['户名', '卡 / 账号', `进账金额（${moneyUnit}）`, '笔数', `出账金额（${moneyUnit}）`, '笔数', `收支差额（${moneyUnit}）`]} widths={[190, 220, 175, 65, 175, 65, 160]} moneyColumns={[2, 4, 6]} autoSize><tbody>{data.accounts.items.map(row => <tr key={row.account}><td data-fit-text><ImportCellValue>{row.names.join('、') || '户名未提供'}</ImportCellValue></td><td data-fit-text><button className="ft-report-cell-button" onClick={() => changeQuery({ account: row.account, timelinePage: 1, page: 1, relationsPage: 1 })}><ImportCellValue fullValue={`来源卡/账号：${row.account_numbers.join('、')}\n另列卡号：${row.cards.join('、') || '未单列'}`}>{row.account_number}</ImportCellValue></button></td><td className="ft-money"><button className="ft-report-cell-button" disabled={!client.importReportRecords || !row.in_count} onClick={() => openRecords(`${row.account_number} · 进账来源`, { account: row.account, direction: 'in' })}><ImportCellValue>{formatMoney(row.in_amount)}</ImportCellValue></button></td><td>{row.in_count}</td><td className="ft-money"><button className="ft-report-cell-button" disabled={!client.importReportRecords || !row.out_count} onClick={() => openRecords(`${row.account_number} · 出账来源`, { account: row.account, direction: 'out' })}><ImportCellValue>{formatMoney(row.out_amount)}</ImportCellValue></button></td><td>{row.out_count}</td><td className="ft-money"><ImportCellValue>{formatMoney(row.net_amount)}</ImportCellValue></td></tr>)}{!data.accounts.items.length && <tr><td colSpan={7}>当前页没有账户资金记录</td></tr>}</tbody></ImportTable>{pagination('accounts')}
        </section>
        <section {...section('peers')} className="ft-report-section"><h4>三、主要往来<ImportReportAccountFilter data={data} client={client} sourceId={sourceId} scopeSignal={scopeSignal} selected={query.peerAccount} onChange={peerAccount => changeQuery({ peerAccount }, 'peers')}/></h4><div className="ft-report-rankings"><Ranking currency={data.currency} interactive={!!client.importReportRecords} title="进账来源" rows={data.top_in} direction="in" onSelect={row => openRecords(`${row.account_number} · 进账来源`, { account: query.peerAccount, counterparty: row.account, direction: 'in' })} /><Ranking currency={data.currency} interactive={!!client.importReportRecords} title="出账去向" rows={data.top_out} direction="out" onSelect={row => openRecords(`${row.account_number} · 出账去向`, { account: query.peerAccount, counterparty: row.account, direction: 'out' })} /></div></section>
        <section {...section('time')} className="ft-report-section ft-trend-section" aria-busy={timeUpdating}>
          <h4>四、资金进出变化<span className="ft-trend-toolbar">
            <ImportTrendFilter data={data} client={client} sourceId={sourceId} scopeSignal={scopeSignal} disabled={timeUpdating} onChange={selection=>changeQuery({...selection,timelinePage:1},'time')}/>
            <span className="ft-trend-modes" role="group" aria-label="图表类型">{([['bar','柱状'],['line','折线'],['combined','同屏']] as const).map(([mode,label])=><button type="button" key={mode} disabled={timeUpdating} aria-pressed={trendMode===mode} onClick={()=>setTrendMode(mode)}>{label}</button>)}</span>
            <label className="ft-report-grain"><span className="ft-sr-only">时间粒度</span><select disabled={timeUpdating} aria-label="时间粒度" value={data.timeline_mode || query.timelineGrain || 'auto'} onChange={event => changeQuery({ timelineGrain: event.target.value as FundsReportQueryV1['timelineGrain'], timelinePage: 1 }, 'time')}><option value="auto">自动</option><option value="year">年</option><option value="month">月</option><option value="day">日</option></select></label>
          </span></h4>
          {(data.timeline_scope?.periods??data.periods).map(period=><p className="ft-report-period" key={period.time_basis}>{reportPeriod(period.first_time,period.last_time)}{(data.timeline_scope?.periods??data.periods).length>1?` · ${reportTimeBasis(period.time_basis)}`:''}</p>)}
          <div className="ft-trend-summary"><span>仅本章 · {data.timeline_scope&&(data.timeline_scope.mode!=='all'||data.timeline_scope.search)?`已选 ${data.timeline_scope.account_count} 个账户`:'全部账户'}{(data.timeline_scope?.periods??data.periods).length>1?' · 全时间域合计':''}</span>{data.timeline_scope?.search&&<span className="ft-trend-search-scope" title={data.timeline_scope.search}>匹配：{data.timeline_scope.search}</span>}{(['in','out'] as const).map(direction=>{const value=data.timeline_scope?.summary??data.summary;return <span key={direction} data-series={direction}>{direction==='in'?'进账':'出账'} <strong>{formatMoney(value[`${direction}_amount`])}</strong> {moneyUnit} · {count(value[`${direction}_count`])} 笔</span>;})}{timeUpdating&&<span role="status">正在更新本章…</span>}</div>
          {!!data.timeline_scope?.undated_transactions&&<p className="ft-report-meta">另有 {count(data.timeline_scope.undated_transactions)} 笔未取得可归集日期。</p>}
          <ImportReportTrend buckets={data.timeline} grain={data.timeline_grain} currency={data.currency} mode={trendMode} disabled={timeUpdating} onOpenRecords={client.importReportRecords?(bucket,direction)=>openRecords(`${trendPeriod(bucket,data.timeline_grain)}${direction==='in'?'进账':direction==='out'?'出账':''}明细`,{timeStart:bucket.start,timeEnd:bucket.end,timeBasis:bucket.time_basis,timelineScope:query.timelineScope,timelineAccounts:query.timelineAccounts,timelineSearch:query.timelineSearch,...(direction?{direction}:{})}):undefined}/>
          {data.timeline_pagination&&data.timeline_pagination.total>data.timeline_pagination.page_size&&<div className="ft-report-pagination" aria-label="时间变化分页"><span>共 {count(data.timeline_pagination.total)} 个日历时段</span><button type="button" className="ft-text-button" disabled={timeUpdating||data.timeline_pagination.page<=1} onClick={()=>changeQuery({timelinePage:data.timeline_pagination!.page-1},'time')}>前一段</button><span>{data.timeline_pagination.page} / {Math.ceil(data.timeline_pagination.total/data.timeline_pagination.page_size)}</span><button type="button" className="ft-text-button" disabled={timeUpdating||data.timeline_pagination.page*data.timeline_pagination.page_size>=data.timeline_pagination.total} onClick={()=>changeQuery({timelinePage:data.timeline_pagination!.page+1},'time')}>后一段</button></div>}
        </section>
        <section {...section('relations')} className="ft-report-section"><h4>五、账户往来关联</h4><div className="ft-report-caption">表2 账户往来关联（{data.perspective === 'own_ledger' ? '本方' : '端点'}视角，{moneyUnit}）</div>
          <ImportTable fillToSlot textMaxWidth={160} className="ft-report-grid" label="账户往来关联，可上下左右滚动" headings={['本方卡/账号', '对手户名', '对手卡/账号', `进账金额（${moneyUnit}）`, '笔数', `出账金额（${moneyUnit}）`, '笔数', '首次往来', '末次往来']} widths={[190, 190, 220, 175, 65, 175, 65, 220, 220]} moneyColumns={[3, 5]} autoSize><tbody>{data.relations.items.map(row => <tr key={`${row.account}:${row.counterparty}:${row.time_basis}`}><td data-fit-text><ImportCellValue>{row.account_number}</ImportCellValue></td><td data-fit-text><ImportCellValue>{row.names.join('、') || '户名未提供'}</ImportCellValue></td><td data-fit-text><ImportCellValue>{row.counterparty_number || '对手标识未载明'}</ImportCellValue></td><td className="ft-money"><button className="ft-report-cell-button" disabled={!client.importReportRecords || !row.in_count} onClick={() => openRecords(`${row.account_number} ← ${row.counterparty_number || '对手标识未载明'}`, { account: row.account, counterparty: row.counterparty, direction: 'in' })}><ImportCellValue>{formatMoney(row.in_amount)}</ImportCellValue></button></td><td>{row.in_count}</td><td className="ft-money"><button className="ft-report-cell-button" disabled={!client.importReportRecords || !row.out_count} onClick={() => openRecords(`${row.account_number} → ${row.counterparty_number || '对手标识未载明'}`, { account: row.account, counterparty: row.counterparty, direction: 'out' })}><ImportCellValue>{formatMoney(row.out_amount)}</ImportCellValue></button></td><td>{row.out_count}</td><td><ImportCellValue>{displayTime(row.first_time)}</ImportCellValue></td><td><ImportCellValue>{displayTime(row.last_time)}</ImportCellValue></td></tr>)}{!data.relations.items.length && <tr><td colSpan={9}>当前页没有往来关联</td></tr>}</tbody></ImportTable>{pagination('relations')}
        </section>

        {drill && <div ref={drillElement}><ImportReportRecords key={JSON.stringify(drill.query)} sourceId={sourceId} query={drill.query} label={drill.label} client={client} scopeSignal={scopeSignal} onClose={closeDrill} /></div>}
        <section {...section('method')} className="ft-report-section ft-report-method"><h4>六、统计口径与来源</h4>
          {methodParagraphs.map((paragraph,index) => <p className="ft-method-paragraph" key={index}>{paragraph.title && <strong>{paragraph.title}。</strong>}{paragraph.text}</p>)}
        </section>
      </>}
    </div>
  </div>;
}
