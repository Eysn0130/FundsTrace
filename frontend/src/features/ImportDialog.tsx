import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type DragEvent } from 'react';
import type { ImportPreview, ImportSession } from '../../../contracts/api-v1';
import { Modal } from '../components/Modal';
import { useFeedback } from '../feedback/Feedback';
import type { FundsCaseClientV1, FundsCaseContextV1, FundsHostActionsV1 } from '../panel/host';
import { captureDroppedFiles } from '../lib/directory-files';
import { feedbackReason, unresolvedCount, ResolutionSummary, TableSummary, FeedbackSummary, ImportRecordEvidence } from './ImportDetails';
import { createImportWorkflow, type ImportState, type ImportWorkflow, type ImportItem } from './import-workflow.mjs';
import { buildImportTree, firstImportFile, importIcon, type ImportTreeNode } from './import-tree';
import { ImportIcon, ImportProgress, ImportTree, importState } from './ImportTree';
import { ImportDataPreview } from './ImportDataPreview';
import { ImportStepper } from './ImportStepper';
import { ImportReview } from './ImportReview';
import { ImportFundsReport, type ImportReportChapter } from './ImportFundsReport';
import { fieldNames, fieldsA, fieldsB, modeFields, importFieldRequirement } from './import-fields';
import { ImportFileName } from './ImportFileName';
import { ImportFileMenu } from './ImportFileMenu';
import { ImportCellValue, ImportTable } from './ImportTable';

const empty: ImportState = { items: [], selectedId: null, reading: false, running: false, stopped: false, error: '' };
const idleSnapshot = () => empty;
const idleSubscribe = () => () => {};
function findNode(nodes: ImportTreeNode[], id?: string): ImportTreeNode | undefined {
  for (const node of nodes) { if (node.id === id) return node; const found = findNode(node.children, id); if (found) return found; }
}
function containingFile(nodes: ImportTreeNode[], id?: string, parent?: ImportTreeNode): ImportTreeNode | undefined {
  for (const node of nodes) {
    const file = node.kind === 'file' ? node : parent;
    if (node.id === id) return file || node;
    const found = containingFile(node.children, id, file); if (found) return found;
  }
}
function documentsFor(item: ImportItem, node: ImportTreeNode) {
  const summary = item.job?.result?.table_summary ?? item.preview?.table_summary;
  return (summary?.documents ?? []).filter(document => !node.path || node.memberPaths.includes(String(document.member_path)));
}
function knownDocumentTotal(documents: Record<string, unknown>[], key: string) {
  return documents.length && documents.every(document => typeof document[key] === 'number') ? documents.reduce((sum, document) => sum + (document[key] as number), 0).toLocaleString('zh-CN') : '—';
}
export function ImportDialog({ target, client, scopeSignal, resume, actions, onClose, onImported, onOpenImport, settlementHint, active = true, surface = 'v1' }: {
  target: FundsCaseContextV1; client: FundsCaseClientV1; scopeSignal: AbortSignal; resume?: ImportSession; actions?: FundsHostActionsV1;
  onClose: () => void; onImported: () => void; onOpenImport?: (importId: string) => void;
  settlementHint?: { importId: string; sequence: number }; active?: boolean; surface?: 'v1' | 'legacy';
}) {
  const port = useFeedback(), callbacks = useRef({ onImported, onOpenImport, port }); callbacks.current = { onImported, onOpenImport, port };
  const [connection, setConnection] = useState<{ flow: ImportWorkflow; client: FundsCaseClientV1; signal: AbortSignal; caseId: string } | null>(null), [selectedId, setSelectedId] = useState<string>();
  const flow = connection?.client === client && connection.signal === scopeSignal && connection.caseId === target.caseId ? connection.flow : null;
  const [recordFocus, setRecordFocus] = useState<{ itemId: string; memberPath: string; sourceRow: number; sequence: number }>();
  const [showResultDetails, setShowResultDetails] = useState(false);
  const [reportChapter, setReportChapter] = useState<ImportReportChapter>('integrity');
  const [expandedFlow, setExpandedFlow] = useState<ImportWorkflow | null>(null), [query, setQuery] = useState('');
  const search = useRef<HTMLInputElement>(null);
  const [downloading, setDownloading] = useState(''), [downloadError, setDownloadError] = useState(''), [dragging, setDragging] = useState(false);
  const [nativeIcon, setNativeIcon] = useState('');
  const fileActionLifetime = useRef(new AbortController());
  const fileId = useId(), directoryId = useId();
  const fileInput = useRef<HTMLInputElement>(null), directoryInput = useRef<HTMLInputElement>(null);
  const handledSettlement = useRef(settlementHint?.sequence);
  const resultElement = useRef<HTMLElement>(null);
  const inspectorElement = useRef<HTMLElement>(null);
  useEffect(() => {
    const value = createImportWorkflow({ target, client, signal: scopeSignal, resume, surface,
      onImported: () => callbacks.current.onImported(), onTask: task => callbacks.current.port.task({ ...task, announce: false,
        actions: callbacks.current.onOpenImport ? [{ id: 'open-import', label: task.status === 'success' ? '查看结果' : '查看进度', run: () => callbacks.current.onOpenImport?.(task.importId!) }] : [] }) });
    setSelectedId(undefined); setRecordFocus(undefined); setShowResultDetails(false); setReportChapter('integrity'); setQuery(''); setConnection({ flow: value, client, signal: scopeSignal, caseId: target.caseId }); void value.start(); return value.dispose;
  }, [client, scopeSignal, target.caseId, resume?.import_id, surface]);
  const state = useSyncExternalStore(flow?.subscribe ?? idleSubscribe, flow?.snapshot ?? idleSnapshot, idleSnapshot);
  useLayoutEffect(() => { if (!active) flow?.stop('已离开导入进度；未启动文件未上传，已提交任务继续'); }, [active, flow]);
  useEffect(() => {
    if (settlementHint && handledSettlement.current !== settlementHint.sequence) { handledSettlement.current = settlementHint.sequence; flow?.settled(settlementHint.importId); }
  }, [flow, settlementHint]);
  const roots = useMemo(() => buildImportTree(state.items), [state.items]);
  const hasContent = !!resume || state.items.length > 1 || state.items.some(row => !!row.preview || !!row.job?.result);
  const expanded = !!resume || hasContent || !!flow && expandedFlow === flow;
  useLayoutEffect(() => { if (hasContent && flow) setExpandedFlow(flow); }, [hasContent, flow]);
  useEffect(() => {
    if (!active || !expanded) return;
    const shortcut = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !event.altKey) { event.preventDefault(); search.current?.focus(); } };
    window.addEventListener('keydown', shortcut); return () => window.removeEventListener('keydown', shortcut);
  }, [active, expanded]);
  const root = roots.find(node => node.itemId === state.selectedId) ?? roots[0];
  const selected = findNode(roots, selectedId) ?? (root ? firstImportFile(root) : undefined);
  const item = state.items.find(value => value.id === selected?.itemId) ?? state.items[0];
  useEffect(() => { const lifetime = new AbortController(); fileActionLifetime.current = lifetime; setDownloading(''); setDownloadError(''); return () => lifetime.abort(); }, [client, scopeSignal, selected?.id]);
  useLayoutEffect(() => {
    for (const element of [inspectorElement.current, ...inspectorElement.current?.querySelectorAll<HTMLElement>('.ft-mapping-scroll,.ft-preview-scroll') ?? []]) {
      if (element) { element.scrollTop = 0; element.scrollLeft = 0; }
    }
  }, [client, scopeSignal, item?.id, selected?.id]);
  const preview = item?.preview, complete = item?.phase === 'done', result = item?.job?.result;
  const feedback = complete ? result?.feedback : preview?.feedback, summary = complete ? result?.table_summary : preview?.table_summary;
  const resolution = complete ? result?.resolution : preview?.resolution;
  const classified = !!feedback || !!summary, busy = item && ['uploading', 'previewing', 'committing', 'polling'].includes(item.phase);
  const unknown = item?.phase === 'unknown' || !!item?.queryingOnly, pending = unknown || item?.phase === 'committing' || item?.phase === 'polling';
  const queuePending = state.items.some(row => !['done', 'failed', 'skipped'].includes(row.phase));
  const processing = state.items.some(row => ['uploading', 'previewing', 'committing', 'polling'].includes(row.phase))
    || (state.reading || state.running) && (queuePending || state.items.length === 0);
  const allKnown = state.items.length > 0 && roots.every((node, index) => node.fileCount > 0 && (node.kind === 'file' || state.items[index].preview || state.items[index].job?.result));
  const totalFiles = roots.reduce((total, node) => total + (node.fileCount || 1), 0);
  const fileCounts = (phase: string) => roots.reduce((total, node, index) => total + (state.items[index].phase === phase ? node.fileCount || 1 : 0), 0);
  const processedFiles = fileCounts('done') + fileCounts('failed') + fileCounts('skipped');
  const anyUnknown = state.items.some(row => row.phase === 'unknown' || row.queryingOnly);
  const fileSummary = [
    { tone:'success', label:'完成', count:fileCounts('done'), icon:'import-circle-check' },
    { tone:'danger', label:'失败', count:fileCounts('failed'), icon:'import-circle-x' },
    { tone:'warning', label:'跳过', count:fileCounts('skipped'), icon:'import-circle-minus' },
    { tone:anyUnknown ? 'warning' : 'muted', label:anyUnknown ? '待核对' : '待处理', count:!queuePending ? 0 : allKnown ? totalFiles - processedFiles : undefined, icon:'import-clock' },
  ].filter(entry => entry.count !== 0);
  const progressLabel = pending ? '流水导入进度' : '文件上传与解析进度';
  const inspectable = !!selected && (selected.kind === 'sheet' || selected.kind === 'file' && selected.children.length === 0);
  const documents = item && selected && inspectable ? documentsFor(item, selected) : [];
  const evidence = inspectable ? (summary?.mapping_evidence ?? []).filter(row => !selected?.path || selected.memberPaths.includes(row.member_path)) : [];
  const samples = (preview?.preview ?? []).filter(row => !selected?.path || !row.source_member && selected.memberPaths.length <= 1 || selected.memberPaths.includes(row.source_member || ''));
  const examples = summary?.unresolved_examples ?? [...(feedback?.unresolved_examples ?? []), ...(feedback?.review_examples ?? [])];
  const selectedExamples = examples.filter(row => !selected?.path || selected.memberPaths.includes(row.member_path));
  const genericExamples = !classified ? (complete ? result?.bad_examples : preview?.stats.bad_examples) ?? [] : [];
  const originalAvailable = item?.resume?.source_available !== false;
  const readonly = item?.phase !== 'ready' || state.running || !!preview?.feedback;
  const columns = documents.flatMap(document => Array.isArray(document.columns) ? document.columns.filter((column): column is string => typeof column === 'string') : []);
  const distinctColumns = inspectable ? [...new Set(columns.length ? columns : classified ? [] : preview?.columns ?? [])] : [];
  const mappingRows = evidence.length ? evidence : !classified && inspectable ? Object.entries(item?.mapping ?? {}).map(([field, column]) => ({ field, column, basis: '' } as Record<string, string>)) : [];
  const fieldRows = distinctColumns.map(column => ({ column, entries: [...new Map(mappingRows.filter(row => row.column === column).map(row => [row.field, row])).values()] }));
  const recognized = new Set(fieldRows.filter(row => row.entries.length > 0).map(row => row.column));
  const reviewMember = selected?.memberPaths.length===1 ? selected.memberPaths[0] : undefined;
  const reviewAvailable = !!(complete && classified && result?.source_id && reviewMember && client.importReview);
  const counts = selected?.dispositions ?? {}, countsKnown = complete && (!selected?.memberPaths.length || item.job?.member_results !== undefined);
  const imported = counts.imported ?? (!selected?.memberPaths.length ? result?.ok : undefined), duplicates = counts.duplicate ?? (!selected?.memberPaths.length ? result?.rows_dup : undefined);
  const needsReview = (counts.unresolved ?? 0) + (counts.review ?? 0), others = Object.entries(counts).filter(([key]) => !['imported','duplicate','unresolved','review','removed'].includes(key)).reduce((sum, [, value]) => sum + value, 0);
  const physicalFile = containingFile(roots, selected?.id), fileDocuments = item && physicalFile ? documentsFor(item, physicalFile) : [];
  const member = summary?.members?.find(row => row.member_path === physicalFile?.path);
  const sizeBytes = !physicalFile?.path ? item?.sizeBytes : member && !['xlsx','xls'].includes(member.encoding) && !/\.xlsx?$/i.test(physicalFile?.name || '') && physicalFile?.kind === 'file' ? member.size_bytes : undefined;
  const sizeText = sizeBytes === undefined ? '—' : sizeBytes < 1024 ? `${sizeBytes} B` : `${(sizeBytes / (sizeBytes < 1024 * 1024 ? 1024 : 1024 * 1024)).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} ${sizeBytes < 1024 * 1024 ? 'KB' : 'MB'}`;
  const directOriginal = !!item && !item.directory && !/\.(zip|rar)$/i.test(item.filename);
  useEffect(() => { setNativeIcon(''); }, [item?.id, selected?.id, client, scopeSignal]);
  const close = () => { flow?.stop('已关闭进度；未启动文件未上传，已提交任务继续处理'); onClose(); };
  const download = async (kind: 'report' | 'original') => {
    const sourceId = result?.source_id, action = kind === 'report' ? actions?.downloadImportReport : actions?.downloadSource;
    if (!sourceId || !action || downloading || scopeSignal.aborted) return;
    const signal = fileActionLifetime.current.signal;
    setDownloading(kind); setDownloadError('');
    try { await action({ sourceId, filename: kind === 'report' ? `feedback-${sourceId}-dispositions.json` : item.filename }, signal); }
    catch (failure) { if (!scopeSignal.aborted && !signal.aborted) setDownloadError((failure as Error).message); }
    finally { if (!scopeSignal.aborted && !signal.aborted) setDownloading(''); }
  };
  const copyFileName = async () => { const signal = fileActionLifetime.current.signal; try { await navigator.clipboard.writeText(physicalFile?.name || item!.filename); } catch { if (!signal.aborted && !scopeSignal.aborted) setDownloadError('未能复制文件名，请重试。'); } };
  const nativeCopy = async (action: 'open' | 'reveal') => {
    if (!result?.source_id || !actions?.sourceCopy || downloading || !directOriginal) return;
    const signal = fileActionLifetime.current.signal;
    setDownloading(action); setDownloadError('');
    try { const info = await actions.sourceCopy(result.source_id, action, signal); if (!signal.aborted && !scopeSignal.aborted && info.icon && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(info.icon)) setNativeIcon(info.icon); }
    catch (error) { if (!signal.aborted && !scopeSignal.aborted) setDownloadError((error as Error).message); }
    finally { if (!signal.aborted && !scopeSignal.aborted) setDownloading(''); }
  };
  const upload = (read: (signal: AbortSignal) => File[] | Promise<File[]>, directory = false) => { setSelectedId(undefined); setShowResultDetails(false); void flow?.upload(read, directory); };
  const modalDrop = (event: DragEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;
    event.preventDefault();
    if (state.items.length || state.reading) { port.notify({ operation: 'import', severity: 'warning', title: '请先关闭当前进度，再选择下一批文件' }); return; }
    const directory = Array.from(event.dataTransfer.items ?? []).some(entry => entry.webkitGetAsEntry?.()?.isDirectory);
    const dropped = captureDroppedFiles(event.dataTransfer); upload(signal => dropped.read(signal), directory);
  };
  const fileHeading = item && <div className="ft-inspector-title"><ImportIcon name={importIcon(selected?.name || item.filename, selected?.kind)} className="ft-file-icon" /><div className="ft-file-title-copy"><h3><ImportFileName name={selected?.name || item.filename} path={selected?.path} /></h3><div className="ft-file-outcomes" aria-label="当前文件导入结果">{countsKnown ? <><span>已导入 {(imported ?? 0).toLocaleString('zh-CN')} 行</span><span>重复 {(duplicates ?? 0).toLocaleString('zh-CN')} 行</span><span data-tone={needsReview ? 'warning' : undefined}>待核对 {needsReview.toLocaleString('zh-CN')} 行</span>{others > 0 && <span>未计入 {others.toLocaleString('zh-CN')} 行</span>}{counts.removed ? <span>已移除 {counts.removed.toLocaleString('zh-CN')} 行</span> : null}</> : <span>{busy ? '正在识别与核对' : item.phase === 'failed' ? '导入失败' : item.phase === 'skipped' ? '已跳过' : item.loadingMembers ? '读取回执中' : complete || unknown ? '处置待核对' : preview ? '预览待确认' : '等待识别'}</span>}</div></div>{directOriginal && actions?.sourceCopy && result?.source_id && originalAvailable && <button type="button" className="button secondary ft-open-copy" disabled={!!downloading} onClick={() => void nativeCopy('open')}><img src={nativeIcon || `/static/ui-icons/${importIcon(item.filename)}.svg`} width="18" height="18" alt="" />打开副本</button>}<ImportFileMenu key={`${item.id}:${selected?.id}`} commands={[
            { label: selected?.path && /\.(zip|rar)$/i.test(item.filename) || item.directory ? '另存所属压缩包…' : '另存原件…', icon: 'file-plus', disabled: !result?.source_id || !actions?.downloadSource || !originalAvailable || !!downloading, run: () => void download('original') },
            ...(directOriginal && actions?.sourceCopy ? [{ label: '显示副本位置', icon: 'folder', disabled: !result?.source_id || !originalAvailable || !!downloading, run: () => void nativeCopy('reveal') }] : []),
            { label: '复制文件名', icon: 'copy', run: () => void copyFileName() },
          ]} information={<dl className="ft-file-information">{selected?.kind === 'sheet' && physicalFile?.kind === 'file' && <><dt>范围</dt><dd>整份文件</dd></>}<dt>{item.directory && !selected?.path ? '所选文件合计' : '文件大小'}</dt><dd>{sizeText}</dd><dt>处理用时（本次）</dt><dd>{!physicalFile?.path && complete && item.processingMs !== undefined ? `${(item.processingMs / 1000).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} s` : '—'}</dd><dt>原始行数</dt><dd>{knownDocumentTotal(fileDocuments, 'source_rows')}</dd><dt>数据记录</dt><dd>{fileDocuments.length ? knownDocumentTotal(fileDocuments, 'row_count') : summary && !physicalFile?.path ? summary.record_total.toLocaleString('zh-CN') : member?.row_count?.toLocaleString('zh-CN') ?? '—'}</dd></dl>} /></div>;
  const fundsReportMode = showResultDetails && !!client.importFundsReport;
  if (!active || scopeSignal.aborted) return null;
  return <Modal className={`ft-import-dialog ${expanded ? 'is-expanded' : 'is-entry'}`} title="导入流水" compact dismissOutside={!item && !state.reading} onClose={close} onFileDrop={modalDrop} headerAction={expanded && <div className="ft-file-search"><ImportIcon name="search" /><input ref={search} type="search" aria-label="搜索导入文件" placeholder="搜索文件…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Escape' && query) { event.preventDefault(); event.stopPropagation(); setQuery(''); } }} /><kbd>Ctrl K</kbd></div>}>
    {complete && <div className="ft-sr-only" role="status" aria-label="导入完成"><span>{classified ? '自动处理完成' : '流水已写入当前案件'}</span><p>{classified && <>已保存 {feedback?.record_total ?? summary?.record_total} 条记录 · </>}新增流水记录 {summary?.effective_added ?? result?.ok ?? 0} 条 · 重复 {result?.rows_dup ?? 0} 条 · {feedback ? <>已配对冲正/撤销 {feedback.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {feedback.non_monetary} 条 · 未计入资金流水 {unresolvedCount(feedback)} 条 · 来源反馈 {feedback.non_transaction} 条 · 辅助资料 {feedback.auxiliary} 条</> : summary ? <>未计入资金流水 {summary.unresolved} 条 · 已配对冲正/撤销 {summary.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {summary.non_monetary} 条 · 非交易记录 {summary.non_transaction} 条 · 辅助资料 {summary.auxiliary} 条</> : <>跳过 {result?.skipped ?? 0} 行</>}</p></div>}
    <input className="ft-file-input" ref={fileInput} id={fileId} aria-label="选择流水文件" aria-describedby={!item ? `${fileId}-hint ${fileId}-group` : undefined} tabIndex={-1} type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.zip,.rar" disabled={state.reading || state.running} onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ''; upload(() => files); }} />
    {client.previewDirectory && <input className="ft-file-input" id={directoryId} tabIndex={-1} aria-label="选择整个文件夹" type="file" multiple ref={element => { directoryInput.current = element; element?.setAttribute('webkitdirectory', ''); }} disabled={state.reading || state.running} onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ''; upload(() => files, true); }} />}
    {expanded && <ImportStepper items={state.items} processing={processing} total={allKnown ? totalFiles : undefined} confirmed={processedFiles} animateCompletion={resume?.stage !== 'done'} />}
    {state.error && <p className="error-banner" role="alert">{state.error}</p>}
    {!expanded && !item ? <div className={`ft-drop-zone import-drop-area ${dragging ? 'is-dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={event => {
      event.preventDefault(); setDragging(false);
      const directory = Array.from(event.dataTransfer.items ?? []).some(entry => entry.webkitGetAsEntry?.()?.isDirectory);
      const dropped = captureDroppedFiles(event.dataTransfer); upload(signal => dropped.read(signal), directory);
    }}><ImportIcon name={state.reading ? 'import-loader' : 'upload'} className={state.reading ? 'is-spinning' : ''} /><h3>{state.reading ? '正在读取所选文件…' : '拖入文件或文件夹'}</h3><p id={`${fileId}-hint`}>Excel · CSV · ZIP · RAR</p><div className="ft-file-actions"><button type="button" className="button primary" disabled={state.reading || state.running} onClick={() => fileInput.current?.click()}>选择文件</button>{client.previewDirectory && <button type="button" className="button secondary" disabled={state.reading || state.running} onClick={() => directoryInput.current?.click()}>选择文件夹</button>}</div><p id={`${fileId}-group`} className="ft-sr-only">多选文件顺序处理；需要资料相互关联时，请选择整个文件夹。</p></div>
      : !expanded && item ? <section className="ft-entry-progress"><ImportIcon name={busy ? 'import-loader' : unknown ? 'import-clock' : 'import-circle-x'} className={busy ? 'is-spinning' : ''} /><ImportFileName name={item.filename} /><p>{sizeText}</p><ImportProgress label={progressLabel} active={!!busy} />{item.error && <p className="ft-entry-error" role="alert">{item.error}</p>}{!busy && <div className="ft-file-actions">{unknown && item.importId ? <button className="button secondary" onClick={() => void flow?.query(item.id)}>重新查询进度</button> : !item.importId && <button type="button" className="button primary" onClick={() => fileInput.current?.click()}>重新选择文件</button>}</div>}</section>
      : item && <div className="ft-import-body"><aside className="ft-import-files"><div className="ft-section-heading"><h3>导入文件</h3><div className="ft-counts ft-file-counts" aria-label="文件处理汇总">{fileSummary.map(({tone,label,count,icon})=><span key={label} data-tone={tone}><ImportIcon name={icon} />{label} {count ?? '—'}</span>)}</div>{state.running && state.items.length > 1 && !state.stopped && <button className="ft-text-button" onClick={() => flow?.stop()}>停止后续文件</button>}</div><ImportTree roots={roots} items={state.items} selectedId={selected?.id} query={query} onClearSearch={() => setQuery('')} onSelect={node => { setSelectedId(node.id); setRecordFocus(undefined); flow?.select(node.itemId); }} /></aside>
        <section className={`ft-inspector ${fundsReportMode ? 'is-report' : ''}`} ref={inspectorElement} tabIndex={0} aria-label="文件识别与预览，可上下滚动">
          {fundsReportMode ? complete && inspectable && reviewMember && result?.source_id ? <ImportFundsReport key={`${client.binding.scopeKey}:${client.binding.generation}:${result.source_id}:${reviewMember}`} client={client} scopeSignal={scopeSignal} sourceId={result.source_id} memberPath={reviewMember} header={fileHeading} actions={actions} chapter={reportChapter} onChapter={setReportChapter} /> : <div className="ft-report-unavailable">{fileHeading}<p>{!inspectable ? '选择文件或工作表，查看其资金统计报告。' : item.phase === 'failed' ? '此文件导入失败，没有可统计的资金记录。' : '此文件尚未完成导入，报告暂不可用。'}</p>{item.error && <p role="alert">{item.error}</p>}</div> : <>
          {fileHeading}


          {item.error && <div className={unknown || complete || item.phase === 'skipped' ? 'warning-banner' : 'error-banner'} role="alert"><span>{item.error}</span>{!item.importId && !busy && <button type="button" className="button secondary" onClick={() => fileInput.current?.click()}>重新选择文件</button>}{unknown && <p>导入结果暂无法确认，请重新查询进度，确认前不会再次提交。</p>}{unknown && item.importId && <button className="button secondary" onClick={() => void flow?.query(item.id)}>重新查询进度</button>}{item.phase === 'failed' && item.importId && (item.preview || item.resume?.source_available) && item.job?.recoverable !== false && <button className="button secondary" onClick={() => void flow?.repreview(item.id)}>{item.preview ? '重新预览后重试' : '重试恢复预览'}</button>}</div>}

          {!showResultDetails && inspectable && (preview || mappingRows.length > 0) && <section className="ft-mapping-section"><div className="ft-section-heading"><h4>字段映射</h4><p className="ft-header-detection">{documents.length ? documents.map((document, index) => <span key={index}>{document.header_row != null ? `表头第 ${String(document.header_row)} 行` : '表头未能唯一识别'}{index < documents.length - 1 ? '；' : ''}</span>) : classified ? '表头未识别' : '表头行未确定'}{distinctColumns.length > 0 && <> · 已识别 {recognized.size} / {distinctColumns.length} 个字段</>}</p>{item.dirty && <span>映射已修改</span>}</div>

            <ImportTable fillToSlot resizable={false} className="ft-mapping-scroll" label="字段映射，可上下左右滚动" headings={['原始表头','识别字段','状态']} widths={[234,234,228]}><tbody>{fieldRows.map(({ column, entries }, index) => <tr key={`${column}:${index}`}><td><ImportCellValue>{column}</ImportCellValue></td><td><ImportCellValue>{entries.map(row => fieldNames[row.field] || row.field).join('、') || '—'}</ImportCellValue></td><td data-tone={entries.length ? 'success' : 'muted'}><span className="ft-status-cell"><ImportIcon name={entries.length ? 'import-circle-check' : 'import-circle'} /><span>{entries.length ? '已识别' : '未映射'}</span></span></td></tr>)}</tbody></ImportTable>{!fieldRows.length && <p>来源未提供可展示的表头。</p>}
            {!complete && (preview?.automatic !== true || !!item.resume || item.phase === 'ready') && !preview?.feedback && <details className="ft-mapping-edit"><summary>调整字段映射</summary><label className="mode-field">文件类型<select aria-label="流水文件类型" disabled={readonly} value={item.mode} onChange={event => { const mode = event.target.value as 'A' | 'B'; flow?.edit(item.id, { mode, mapping: Object.fromEntries(Object.entries(item.mapping).filter(([field]) => modeFields[mode].has(field))) }); }}><option value="A">双边流水（付款方 → 收款方）</option><option value="B">单账户流水（本方与交易对手）</option></select></label><div className="ft-mapping-scroll ft-mapping-controls" role="region" tabIndex={0} aria-label="调整映射，可上下左右滚动">{(item.mode === 'A' ? fieldsA : fieldsB).map(field => <label key={field}>{fieldNames[field]}<small>{importFieldRequirement(field, item.mode, item.mapping).note}</small><select aria-label={`映射${fieldNames[field]}`} disabled={readonly} value={item.mapping[field] || ''} onChange={event => flow?.edit(item.id, { mapping: { ...item.mapping, [field]: event.target.value } })}><option value="">不使用</option>{preview?.columns.map(column => <option key={column}>{column}</option>)}</select></label>)}</div><label className="ft-supplementary">补充本方账号<input type="text" aria-label="补充本方账号" value={item.selfAccountsText} disabled={readonly} placeholder="可选，逗号分隔" onChange={event => flow?.edit(item.id, { selfAccountsText: event.target.value })} /></label></details>}
            {preview?.reference_policy_available && !complete && <label className="ft-reference-choice"><input type="checkbox" checked={item.referencePolicy} disabled={readonly} onChange={event => flow?.edit(item.id, { referencePolicy: event.target.checked })} />凭证号作为独立引用（不与交易流水号比较）</label>}
            {item.dirty && <button className="button secondary" onClick={() => void flow?.repreview(item.id)}>按当前映射重新预览</button>}
          </section>}
          {!reviewAvailable && !showResultDetails && inspectable && (selectedExamples.length > 0 || genericExamples.length > 0) && <section className="ft-issues"><div className="ft-section-heading"><h4>识别问题（{selectedExamples.length + genericExamples.length}）</h4></div>{selectedExamples.map((row, index) => <div key={index} className="ft-issue"><ImportIcon name="import-circle-x" /><span>原始第 {row.source_row} 行 · {feedbackReason(row.reason_code)}</span>{result?.source_id && <a href="#ft-source-records" onClick={event => { event.preventDefault(); setSelectedId(selected?.id); setShowResultDetails(false); setRecordFocus(previous => ({ itemId: item.id, memberPath: row.member_path, sourceRow: row.source_row, sequence: (previous?.sequence ?? 0) + 1 })); }}>查看来源行</a>}</div>)}{genericExamples.map((row, index) => <div key={`generic:${index}`} className="ft-issue"><ImportIcon name="import-circle-x" /><span>{row.source_row != null ? `原始第 ${row.source_row} 行` : row.row} · {feedbackReason(row.reason)}</span></div>)}</section>}
          {!showResultDetails && inspectable && (result?.source_id ? <div id="ft-source-records">{reviewAvailable ? <ImportReview key={`${client.binding.scopeKey}:${client.binding.generation}:${item.id}:${selected?.id}`} client={client} scopeSignal={scopeSignal} sourceId={result.source_id} memberPath={reviewMember!} /> : <ImportRecordEvidence key={`${client.binding.scopeKey}:${client.binding.generation}:${item.id}:${selected?.id}:${recordFocus?.itemId === item.id ? recordFocus.sequence : 0}`} compact initialRows={samples} filename={selected?.name || item.filename} caseId={target.caseId} openPreview focusRow={recordFocus?.itemId === item.id && selected?.memberPaths.includes(recordFocus.memberPath) ? recordFocus.sourceRow : undefined} onClearFocus={() => setRecordFocus(undefined)} sourceId={result.source_id} memberPath={selected?.memberPaths.length === 1 ? selected.memberPaths[0] : undefined} client={client} scopeSignal={scopeSignal} />}</div> : preview && <ImportDataPreview rows={samples} filename={item.filename} feedback={!!feedback} />)}
          {complete && <section hidden={!showResultDetails} className="ft-result-data" aria-label="处理结果" ref={resultElement}><h4>导入回执</h4><table><thead><tr><th>处理结果</th><th>{resume?.stage === 'done' ? '导入当时数量' : '数量'}</th></tr></thead><tbody><tr><td>新增流水记录</td><td>{summary?.effective_added ?? result?.ok ?? 0}</td></tr><tr><td>重复</td><td>{result?.rows_dup ?? 0}</td></tr><tr><td>未计入</td><td>{feedback ? unresolvedCount(feedback) : summary?.unresolved ?? result?.skipped ?? 0}</td></tr></tbody></table><p className="ft-receipt">批次 {result?.batch_id} · {item.filename}</p>
            {result?.source_id && inspectable && <ImportRecordEvidence caseId={target.caseId} sourceId={result.source_id} memberPath={selected?.memberPaths.length === 1 ? selected.memberPaths[0] : undefined} client={client} scopeSignal={scopeSignal} />}
          </section>}
          {preview && !readonly && preview.self_accounts_in_file.length > 0 && <details className="ft-main-accounts"><summary>标记本方主账号</summary>{preview.self_accounts_in_file.map(account => <label key={account.account}><input type="checkbox" checked={item.marked.includes(account.account)} onChange={event => flow?.edit(item.id, { marked: event.target.checked ? [...item.marked, account.account] : item.marked.filter(value => value !== account.account) })} />{account.name || account.account}<small>{account.account}</small></label>)}</details>}
          {(classified || resolution) && <details hidden={!showResultDetails} className="ft-full-summary" open><summary>完整处理统计与识别依据</summary>{feedback ? <FeedbackSummary feedback={feedback} complete={complete} uncertain={unknown} /> : summary ? <TableSummary summary={summary} complete={complete} uncertain={unknown} /> : null}{resolution && <ResolutionSummary value={resolution} complete={complete} />}
            {summary?.effective_added != null && summary.effective_added !== result?.ok && <p>本次保留来源流水记录 {result?.ok} 条，其中新增有效流水记录 {summary.effective_added} 条；已有配对依据的记录不重复进入统计。</p>}
            {complete && classified && <div className="ft-downloads"><button className="button secondary" disabled={!result?.source_id || !actions?.downloadImportReport || !!downloading} onClick={() => void download('report')}>下载完整处理报告（JSON）</button><button className="button secondary" disabled={!result?.source_id || !actions?.downloadSource || !originalAvailable || !!downloading} onClick={() => void download('original')}>{feedback ? '下载原始 ZIP' : '下载原始文件'}</button>{(!actions?.downloadImportReport || !actions?.downloadSource) && <p>宿主未提供{!actions?.downloadImportReport ? '处理报告' : ''}{!actions?.downloadImportReport && !actions?.downloadSource ? '和' : ''}{!actions?.downloadSource ? '原件' : ''}下载能力。</p>}{!originalAvailable && <p>{feedback ? '原始 ZIP 已不可用' : '原始文件已不可用'}；处理报告仍可用于追溯。</p>}</div>}
          </details>}
          {downloadError && <p role="alert">{downloadError}</p>}
          </>}
        </section></div>}
    {expanded && <footer className="ft-import-footer"><div className="ft-footer-actions">{(state.items.length > 0) && <button className="button primary" disabled={!showResultDetails && !complete && (!preview || !classified && !resolution || processing)} onClick={() => { setShowResultDetails(value => !value); setRecordFocus(undefined); }}>{showResultDetails ? '返回数据预览' : '查看处理结果'}</button>}<button className="button secondary" onClick={close}>{!queuePending ? '完成' : '关闭进度'}</button>{preview && !complete && !pending && !state.running && <button className="button primary" disabled={!!busy || state.running || item.dirty || !classified && preview.stats.ok === 0 || item.phase === 'failed'} onClick={() => void flow?.commit(item.id)}>{classified ? '开始导入' : '确认导入'}</button>}</div></footer>}
  </Modal>;
}
