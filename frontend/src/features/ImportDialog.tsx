import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type DragEvent } from 'react';
import type { ImportPreview, ImportSession } from '../../../contracts/api-v1';
import { Modal } from '../components/Modal';
import { useFeedback } from '../feedback/Feedback';
import { linkAbortSignal } from '../panel/cancellation';
import type { FundsCaseClientV1, FundsCaseContextV1, FundsHostActionsV1 } from '../panel/host';
import { captureDroppedFiles } from '../lib/directory-files';
import { feedbackReason, unresolvedCount, ResolutionSummary, TableSummary, FeedbackSummary, ImportRecordEvidence } from './ImportDetails';
import { createImportWorkflow, type ImportState, type ImportWorkflow, type ImportItem } from './import-workflow.mjs';
import { buildImportTree, firstImportFile, importIcon, type ImportTreeNode } from './import-tree';
import { ImportIcon, ImportTree, importState, type ImportTreeDestination } from './ImportTree';
import { ImportDataPreview } from './ImportDataPreview';
import { ImportStepper } from './ImportStepper';
import { ImportReview } from './ImportReview';
import { ImportFundsReport, type ImportReportChapter } from './ImportFundsReport';
import { fieldNames, fieldsA, fieldsB, modeFields, importFieldRequirement } from './import-fields';
import { ImportFileName } from './ImportFileName';
import { ImportAppendControl, ImportFileMenu, type ImportFileAction, type ImportMenuAnchor } from './ImportFileMenu';
import { ImportCellValue, ImportTable } from './ImportTable';

const empty: ImportState = { items: [], selectedId: null, reading: false, running: false, stopped: false, error: '', dataVersion: 0, withdrawing: false };
const idleSnapshot = () => empty;
const idleSubscribe = () => () => {};
function findNode(nodes: ImportTreeNode[], id?: string): ImportTreeNode | undefined {
  if (!id) return;
  for (const node of nodes) { if (node.id === id) return node; const found = findNode(node.children, id); if (found) return found; }
}
function containingFile(nodes: ImportTreeNode[], id?: string, parent?: ImportTreeNode): ImportTreeNode | undefined {
  for (const node of nodes) {
    const file = node.kind === 'file' ? node : parent;
    if (node.id === id) return file || node;
    const found = containingFile(node.children, id, file); if (found) return found;
  }
}
function memberFile(node: ImportTreeNode, memberPath: string): ImportTreeNode | undefined {
  if (!node.memberPaths.includes(memberPath)) return;
  if (!node.children.length && !node.attachment && (node.kind === 'file' || node.kind === 'sheet')) return node;
  for (const child of node.children) {
    const found = memberFile(child, memberPath);
    if (found) return found;
  }
}
function documentsFor(item: ImportItem, node: ImportTreeNode) {
  const summary = item.job?.result?.table_summary ?? item.preview?.table_summary;
  return (summary?.documents ?? []).filter(document => document.role !== 'attachment' && (!node.path || node.memberPaths.includes(String(document.member_path))));
}
type HeaderAssistance = { header_row: number; header_rows: number[]; status: 'verified' | 'review'; columns: number[]; reason: string; suggestions?: unknown };
const headerSuggestionRoles = { payer_account: '付款方账号', payee_account: '收款方账号' } as const;
type HeaderSuggestion = { column_index: number; role: keyof typeof headerSuggestionRoles };
const headerReasons: Record<string, string> = {
  source_header_verified: '原始行列核验通过', ambiguous_source_header: '表头角色存在歧义',
  model_unavailable: '本地辅助暂不可用', model_budget: '达到本次辅助处理上限',
  proposal_rejected: '建议未通过原文核验', invalid_header_replay: '原保存的表头依据无法复核',
};
const sourcePosition = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
function headerSuggestions(value: unknown): HeaderSuggestion[] {
  return Array.isArray(value) ? value.filter((hint): hint is HeaderSuggestion => hint && sourcePosition(hint.column_index)
    && typeof hint.role === 'string' && Object.hasOwn(headerSuggestionRoles, hint.role)) : [];
}
function headerAssistanceFor(documents: Record<string, unknown>[]) {
  return documents.flatMap(document => Array.isArray(document.header_assistance)
    ? document.header_assistance.filter((entry): entry is HeaderAssistance => entry && sourcePosition(entry.header_row)
      && Array.isArray(entry.header_rows) && entry.header_rows.includes(entry.header_row) && entry.header_rows.every(sourcePosition)
      && (entry.status === 'verified' || entry.status === 'review') && typeof entry.reason === 'string'
      && Array.isArray(entry.columns) && entry.columns.every(sourcePosition)) : []);
}
const inspectorCollapseDistance = 72;

function ImportInspectorDivider({ onCollapse }: { onCollapse: () => void }) {
  const cancelDrag = useRef<(() => void) | null>(null);
  const collapse = useRef(onCollapse);
  collapse.current = onCollapse;
  useEffect(() => () => cancelDrag.current?.(), []);

  return <div className="ft-inspector-divider" aria-hidden="true" title="向右拖动，松开折叠右侧详情"
    onPointerDown={event => {
      const { currentTarget: divider, pointerId, clientX, clientY } = event;
      const doc = divider.ownerDocument, view = doc.defaultView;
      if (!view || !event.isPrimary || event.button !== 0 || event.pointerType === 'touch' || cancelDrag.current) return;
      event.preventDefault();
      const lifetime = new AbortController();
      const options = { capture: true, signal: lifetime.signal };
      const cancel = () => {
        cancelDrag.current = null;
        lifetime.abort();
        delete divider.dataset.dragging;
        delete divider.dataset.armed;
        divider.style.removeProperty('--ft-divider-offset');
      };
      const rightwardDistance = (point: PointerEvent) => {
        const dx = point.clientX - clientX;
        return dx >= 1.25 * Math.abs(point.clientY - clientY) ? dx : 0;
      };
      const cancelPointer = (point: PointerEvent) => { if (point.pointerId === pointerId) cancel(); };
      cancelDrag.current = cancel;
      divider.dataset.dragging = '';
      // The embedding frame still receives move and release events after the pointer leaves the divider.
      view.addEventListener('pointermove', point => {
        if (point.pointerId !== pointerId) return;
        if (!(point.buttons & 1)) { cancel(); return; }
        const distance = rightwardDistance(point);
        divider.style.setProperty('--ft-divider-offset', `${Math.min(distance, inspectorCollapseDistance)}px`);
        divider.toggleAttribute('data-armed', distance >= inspectorCollapseDistance);
      }, options);
      view.addEventListener('pointerup', point => {
        if (point.pointerId !== pointerId) return;
        cancel();
        if (point.button === 0 && rightwardDistance(point) >= inspectorCollapseDistance) collapse.current();
      }, options);
      view.addEventListener('pointercancel', cancelPointer, options);
      doc.documentElement.addEventListener('pointerleave', cancelPointer, { signal: lifetime.signal });
      view.addEventListener('blur', cancel, { signal: lifetime.signal });
      view.addEventListener('keydown', key => {
        if (key.key !== 'Escape') return;
        key.preventDefault();
        key.stopPropagation();
        cancel();
      }, options);
    }} />;
}

export type ImportSourceFocus = Readonly<{ memberPath: string; sourceRow: number }>;

export function ImportDialog({ target, client, scopeSignal, resume, sourceFocus, actions, onClose, onImported, onOpenImport, onOpenNewImport, settlementHint, active = true, surface = 'v1' }: {
  target: FundsCaseContextV1; client: FundsCaseClientV1; scopeSignal: AbortSignal; resume?: ImportSession; actions?: FundsHostActionsV1;
  sourceFocus?: ImportSourceFocus;
  onClose: () => void; onImported: () => void; onOpenImport?: (importId: string) => void; onOpenNewImport?: () => void;
  settlementHint?: { importId: string; sequence: number }; active?: boolean; surface?: 'v1' | 'legacy';
}) {
  const port = useFeedback(), callbacks = useRef({ onImported, onOpenImport, port }); callbacks.current = { onImported, onOpenImport, port };
  const [connection, setConnection] = useState<{ flow: ImportWorkflow; client: FundsCaseClientV1; signal: AbortSignal; caseId: string } | null>(null), [selectedId, setSelectedId] = useState<string>();
  const flow = connection?.client === client && connection.signal === scopeSignal && connection.caseId === target.caseId ? connection.flow : null;
  const [recordFocus, setRecordFocus] = useState<{ itemId: string; memberPath: string; sourceRow?: number; sequence: number; review?: boolean }>();
  const [resultRequested, setShowResultDetails] = useState(false);
  const [collapsedInspector, setCollapsedInspector] = useState<{ retainedFile?: string } | null>(null);
  const [showFileMetadata, setShowFileMetadata] = useState(false);
  const [reportChapter, setReportChapter] = useState<ImportReportChapter>('integrity');
  const [expandedFlow, setExpandedFlow] = useState<ImportWorkflow | null>(null), [query, setQuery] = useState('');
  const search = useRef<HTMLInputElement>(null);
  const inspectorToggle = useRef<HTMLButtonElement>(null);
  const [downloading, setDownloading] = useState(''), [downloadError, setDownloadError] = useState(''), [dragging, setDragging] = useState(false);
  const fileDragDepth = useRef(0);
  const [nativeIcon, setNativeIcon] = useState('');
  const [menu, setMenu] = useState<{ nodeId: string; anchor: ImportMenuAnchor }>();
  const moreButton = useRef<HTMLButtonElement>(null), menuId = useId();
  const fileActionLifetime = useRef(new AbortController());
  const fileId = useId(), directoryId = useId(), inspectorId = useId();
  const fileInput = useRef<HTMLInputElement>(null), directoryInput = useRef<HTMLInputElement>(null);
  const handledSettlement = useRef(settlementHint?.sequence);
  const resultElement = useRef<HTMLElement>(null);
  const inspectorElement = useRef<HTMLDivElement>(null);
  const focusInspector = useRef(false);
  const body = useRef<HTMLDivElement>(null);
  const footer = useRef<HTMLElement>(null);
  useEffect(() => {
    const value = createImportWorkflow({ target, client, signal: scopeSignal, resume, surface,
      onImported: () => callbacks.current.onImported(), onTask: task => callbacks.current.port.task({ ...task, announce: false,
        actions: callbacks.current.onOpenImport ? [{ id: 'open-import', label: task.status === 'success' ? '查看结果' : '查看进度', run: () => callbacks.current.onOpenImport?.(task.importId!) }] : [] }) });
    setSelectedId(undefined); setRecordFocus(undefined); setShowResultDetails(false); setReportChapter('integrity'); setQuery('');
    setCollapsedInspector(null); setShowFileMetadata(false); setMenu(undefined);
    setConnection({ flow: value, client, signal: scopeSignal, caseId: target.caseId }); void value.start(); return value.dispose;
  }, [client, scopeSignal, target.caseId, resume?.import_id, surface]);
  const state = useSyncExternalStore(flow?.subscribe ?? idleSubscribe, flow?.snapshot ?? idleSnapshot, idleSnapshot);
  useLayoutEffect(() => { if (!active) flow?.stop('已离开导入进度；未启动文件未上传，已提交任务继续'); }, [active, flow]);
  useEffect(() => {
    if (settlementHint && handledSettlement.current !== settlementHint.sequence) { handledSettlement.current = settlementHint.sequence; flow?.settled(settlementHint.importId); }
  }, [flow, settlementHint]);
  const roots = useMemo(() => buildImportTree(state.items), [state.items]);
  const hasContent = !!resume || state.items.length > 0;
  const expanded = !!resume || hasContent || !!flow && expandedFlow === flow;
  useLayoutEffect(() => { if (hasContent && flow) setExpandedFlow(flow); }, [hasContent, flow]);
  useEffect(() => {
    if (!active || !expanded) return;
    const shortcut = (event: KeyboardEvent) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !event.altKey) { event.preventDefault(); search.current?.focus(); } };
    window.addEventListener('keydown', shortcut); return () => window.removeEventListener('keydown', shortcut);
  }, [active, expanded]);
  const root = roots.find(node => node.itemId === state.selectedId) ?? roots[0];
  const requestedSource = sourceFocus && !selectedId && resume?.case_id === target.caseId && resume.stage === 'done'
    && resume.result?.source_id === resume.import_id ? sourceFocus : undefined;
  const sourceItem = requestedSource && state.items.find(row => row.importId === resume?.import_id && row.phase === 'done');
  const sourceRoot = sourceItem && roots.find(node => node.itemId === sourceItem.id);
  const selected = findNode(roots, selectedId) ?? (requestedSource
    ? sourceRoot && memberFile(sourceRoot, requestedSource.memberPath) : root && firstImportFile(root));
  const item = state.items.find(value => value.id === selected?.itemId) ?? state.items[0];
  const attachment = !!selected?.attachment;
  const inspectorCollapsed = collapsedInspector !== null;
  const keepInspector = !collapsedInspector || (!!selected && collapsedInspector.retainedFile === selected.id);
  const toggleInspector = () => {
    if (scopeSignal.aborted) return;
    setMenu(undefined);
    inspectorToggle.current?.focus({ preventScroll: true });
    setCollapsedInspector(inspectorCollapsed ? null : { retainedFile: selected?.id });
    if (!inspectorCollapsed) setShowFileMetadata(true);
  };
  useLayoutEffect(() => {
    if (inspectorCollapsed || !showFileMetadata) return;
    // Keep the wide rows clipped at their existing size until the divider returns.
    const transition = body.current?.getAnimations?.().find(animation =>
      'transitionProperty' in animation && animation.transitionProperty === 'grid-template-columns');
    if (!transition) { setShowFileMetadata(false); return; }
    let current = true;
    const settle = () => { if (current) setShowFileMetadata(false); };
    void transition.finished.then(settle, settle);
    return () => { current = false; };
  }, [inspectorCollapsed, showFileMetadata]);
  const openFile = (node: ImportTreeNode, destination: ImportTreeDestination) => {
    if (scopeSignal.aborted) return;
    focusInspector.current = inspectorCollapsed;
    setSelectedId(node.id); flow?.select(node.itemId);
    setShowResultDetails(destination.kind === 'result'); setCollapsedInspector(null);
    setRecordFocus(previous => destination.kind === 'review' ? {
      itemId: node.itemId, memberPath: destination.memberPath, sourceRow: destination.sourceRow,
      sequence: (previous?.sequence ?? 0) + 1, review: true,
    } : undefined);
  };
  const openMenu = (node: ImportTreeNode, anchor: ImportMenuAnchor) => setMenu({ nodeId: node.id, anchor });
  const closeMenu = (restoreFocus = true) => {
    setMenu(undefined);
    if (restoreFocus && menu?.anchor.trigger.isConnected) menu.anchor.trigger.focus({ preventScroll: true });
  };
  useLayoutEffect(() => {
    const lifetime = new AbortController();
    fileActionLifetime.current = lifetime;
    const unlink = linkAbortSignal(scopeSignal, lifetime);
    setDownloading(''); setDownloadError(''); setNativeIcon('');
    return () => { unlink(); lifetime.abort(); };
  }, [active, flow, scopeSignal, selected?.id]);
  useLayoutEffect(() => {
    for (const element of [inspectorElement.current, ...inspectorElement.current?.querySelectorAll<HTMLElement>('.ft-mapping-scroll,.ft-preview-scroll') ?? []]) {
      if (element) { element.scrollTop = 0; element.scrollLeft = 0; }
    }
  }, [client, scopeSignal, item?.id, selected?.id]);
  useLayoutEffect(() => {
    const element = inspectorElement.current;
    if (inspectorCollapsed || !focusInspector.current || !element) return;
    focusInspector.current = false;
    element.scrollTop = 0; element.scrollLeft = 0; element.focus({ preventScroll: true });
  }, [inspectorCollapsed]);
  const preview = item?.preview, complete = item?.phase === 'done', result = item?.job?.result;
  const feedback = complete ? result?.feedback : preview?.feedback, summary = complete ? result?.table_summary : preview?.table_summary;
  const pdfReview = summary?.profile === 'pdf-review-v1';
  const showResultDetails = resultRequested && !attachment && !pdfReview;
  const resolution = complete ? result?.resolution : preview?.resolution;
  const classified = !!feedback || !!summary, busy = item && ['uploading', 'previewing', 'committing', 'polling'].includes(item.phase);
  const unknown = item?.phase === 'unknown' || !!item?.queryingOnly, pending = unknown || item?.phase === 'committing' || item?.phase === 'polling';
  const queuePending = state.reading || state.items.some(row => !['done', 'failed', 'skipped'].includes(row.phase));
  const processing = state.items.some(row => ['uploading', 'previewing', 'committing', 'polling'].includes(row.phase)) || state.running;
  const canShowResult = !attachment && !pdfReview && (complete || item?.phase === 'failed' || (!!preview && (classified || !!resolution) && !processing));
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
  const activeGroup = processing ? state.items.find(row => ['uploading', 'previewing', 'committing', 'polling'].includes(row.phase))?.materialGroupId : undefined;
  const steps = activeGroup ? roots.filter((_, index) => state.items[index].materialGroupId === activeGroup) : roots;
  const stepItems = activeGroup ? state.items.filter(row => row.materialGroupId === activeGroup) : state.items;
  const stepTotal = steps.every((node, index) => node.fileCount > 0 && (node.kind === 'file' || stepItems[index].preview || stepItems[index].job?.result))
    ? steps.reduce((total, node) => total + node.fileCount, 0) : undefined;
  const stepConfirmed = steps.reduce((total, node, index) => total + (['done', 'failed', 'skipped'].includes(stepItems[index].phase) ? node.fileCount || 1 : 0), 0);
  const inspectable = !attachment && !!selected && (selected.kind === 'sheet' || selected.kind === 'file' && selected.children.length === 0);
  const documents = item && selected && inspectable ? documentsFor(item, selected) : [];
  const pdfDocument = pdfReview ? documents[0] : undefined;
  const scannedPages = pdfDocument?.pdf_total_pages, candidatePages = pdfDocument?.pdf_candidate_pages;
  const showPdfCoverage = typeof scannedPages === 'number' && Number.isSafeInteger(scannedPages) && scannedPages > 0
    && typeof candidatePages === 'number' && Number.isSafeInteger(candidatePages) && candidatePages >= 0 && scannedPages >= candidatePages;
  const headerAssistance = headerAssistanceFor(documents);
  const headerAssistanceLimited = documents.some(document => document.header_assistance_limited === true);
  const evidence = inspectable ? (summary?.mapping_evidence ?? []).filter(row => !selected?.path || selected.memberPaths.includes(row.member_path)) : [];
  const samples = (preview?.preview ?? []).filter(row => !selected?.path || !row.source_member && selected.memberPaths.length <= 1 || selected.memberPaths.includes(row.source_member || ''));
  const examples = summary?.unresolved_examples ?? [...(feedback?.unresolved_examples ?? []), ...(feedback?.review_examples ?? [])];
  const selectedExamples = examples.filter(row => !selected?.path || selected.memberPaths.includes(row.member_path));
  const genericExamples = !classified ? (complete ? result?.bad_examples : preview?.stats.bad_examples) ?? [] : [];
  const originalAvailable = item?.job?.source_available ?? item?.resume?.source_available ?? false;
  const readonly = item?.phase !== 'ready' || state.running || !!preview?.feedback;
  const columns = documents.flatMap(document => Array.isArray(document.columns) ? document.columns.filter((column): column is string => typeof column === 'string') : []);
  const distinctColumns = inspectable ? [...new Set(columns.length ? columns : classified ? [] : preview?.columns ?? [])] : [];
  const mappingRows = evidence.length ? evidence : !classified && inspectable ? Object.entries(item?.mapping ?? {}).map(([field, column]) => ({ field, column, basis: '' } as Record<string, string>)) : [];
  const fieldRows = distinctColumns.map(column => ({ column, entries: [...new Map(mappingRows.filter(row => row.column === column).map(row => [row.field, row])).values()] }));
  const recognized = new Set(fieldRows.filter(row => row.entries.length > 0).map(row => row.column));
  const reviewMember = selected?.memberPaths.length===1 ? selected.memberPaths[0] : undefined;
  const reviewAvailable = !!(inspectable && complete && classified && result?.source_id && reviewMember && client.importReview);
  const reviewFocus = recordFocus?.review && recordFocus.itemId === item?.id && recordFocus.memberPath === reviewMember ? recordFocus : undefined;
  const exactSourceRow = requestedSource && reviewMember === requestedSource.memberPath ? requestedSource.sourceRow : undefined;
  const recordSourceRow = recordFocus && recordFocus.itemId === item?.id && selected?.memberPaths.includes(recordFocus.memberPath)
    ? recordFocus.sourceRow : exactSourceRow;
  const counts = selected?.dispositions ?? {}, countsKnown = complete && (!selected?.memberPaths.length || item.job?.member_results !== undefined);
  const imported = counts.imported ?? (!selected?.memberPaths.length ? result?.ok : undefined), duplicates = counts.duplicate ?? (!selected?.memberPaths.length ? result?.rows_dup : undefined);
  const needsReview = (counts.unresolved ?? 0) + (counts.review ?? 0), others = Object.entries(counts).filter(([key]) => !['imported','duplicate','unresolved','review','removed'].includes(key)).reduce((sum, [, value]) => sum + value, 0);
  const attachmentStatus = attachment && countsKnown ? importState(item, selected) : undefined;
  const attachmentReason = attachment && countsKnown && (needsReview ? 'unsupported_attachment' : counts.auxiliary ? 'non_business_attachment' : undefined);
  const unacceptedUnit = selected?.containsAttachments ? '项' : '行';
  const directOriginal = !!item && !item.directory && /\.(csv|tsv|txt|xls|xlsx|pdf)$/i.test(item.filename);
  const close = () => { fileActionLifetime.current.abort(); flow?.stop('已关闭进度；未启动文件未上传，已提交任务继续处理'); onClose(); };
  const fileAction = async (kind: ImportFileAction | 'report', sourceId: string, filename: string, fromMenu = false) => {
    const signal = fileActionLifetime.current.signal;
    if (!active || signal.aborted || downloading) return;
    setDownloading(kind); setDownloadError('');
    try {
      if (kind === 'open' || kind === 'reveal') {
        const info = await actions?.sourceCopy?.(sourceId, kind, signal);
        if (!fromMenu && !signal.aborted && info?.icon && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(info.icon)) setNativeIcon(info.icon);
      } else {
        const download = kind === 'report' ? actions?.downloadImportReport : actions?.downloadSource;
        await download?.({ sourceId, filename }, signal);
      }
    } catch (error) {
      if (!signal.aborted) {
        const message = (error as Error).message;
        if (fromMenu) port.notify({ operation: 'import', severity: 'error', title: message });
        else setDownloadError(message);
      }
    } finally { if (!signal.aborted) setDownloading(''); }
  };
  const selectedFileAction = (kind: 'download' | 'report' | 'open') => {
    const sourceId = result?.source_id;
    if (sourceId && item) void fileAction(kind, sourceId, kind === 'report' ? `feedback-${sourceId}-dispositions.json` : item.filename);
  };
  const canAppend = active && !resume && surface === 'v1';
  const intakeAvailable = canAppend && !!flow && !state.reading && !state.withdrawing;
  const receive = (read: (signal: AbortSignal) => File[] | Promise<File[]>, directory = false) => {
    if (!intakeAvailable || scopeSignal.aborted) return;
    if (expanded) void flow?.append(read, directory);
    else void flow?.upload(read, directory);
  };
  const isFileDrag = (event: DragEvent<HTMLElement>) => Array.from(event.dataTransfer.types ?? []).includes('Files')
    || Array.from(event.dataTransfer.items ?? []).some(entry => entry.kind === 'file') || !!event.dataTransfer.files?.length;
  const resetFileDrag = () => { fileDragDepth.current = 0; setDragging(false); };
  const dragEnter = (event: DragEvent<HTMLElement>) => {
    if (!intakeAvailable || !isFileDrag(event)) return;
    event.preventDefault();
    fileDragDepth.current++;
    setDragging(true);
  };
  const dragOver = (event: DragEvent<HTMLElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = intakeAvailable ? 'copy' : 'none';
    if (intakeAvailable) setDragging(true);
  };
  const dragLeave = (event: DragEvent<HTMLElement>) => {
    fileDragDepth.current = Math.max(0, fileDragDepth.current - 1);
    const next = event.relatedTarget;
    if (next instanceof Node ? !event.currentTarget.contains(next) : fileDragDepth.current === 0) resetFileDrag();
  };
  const dropFiles = (event: DragEvent<HTMLElement>) => {
    if (!isFileDrag(event)) return;
    event.preventDefault(); event.stopPropagation(); resetFileDrag();
    if (!intakeAvailable) return;
    const dropped = captureDroppedFiles(event.dataTransfer); receive(dropped.read, dropped.directory);
  };
  const modalDrop = (event: DragEvent<HTMLElement>) => {
    if (event.defaultPrevented) return;
    event.preventDefault();
    if (!expanded) dropFiles(event);
    else resetFileDrag();
  };
  const fileDropEvents = canAppend ? { onDragEnter: dragEnter, onDragOver: dragOver, onDragLeave: dragLeave, onDrop: dropFiles } : {};
  const fileHeading = item && <div className="ft-inspector-title"><ImportIcon name={importIcon(selected?.name || item.filename, selected?.kind)} className="ft-file-icon" />
    <div className="ft-file-title-copy"><h3><ImportFileName name={selected?.name || item.filename} path={selected?.path} /></h3>
      <div className="ft-file-outcomes" aria-label="当前文件导入结果">
        {item.job?.withdrawn ? <span>已撤回导入</span> : attachmentStatus ? <span data-tone={attachmentStatus.tone}>{attachmentStatus.label}</span> : countsKnown ? <>
          <span>已导入 {(imported ?? 0).toLocaleString('zh-CN')} 行</span><span>重复 {(duplicates ?? 0).toLocaleString('zh-CN')} 行</span><span data-tone={needsReview ? 'warning' : undefined}>待核对 {needsReview.toLocaleString('zh-CN')} {unacceptedUnit}</span>{others > 0 && <span>未计入 {others.toLocaleString('zh-CN')} {unacceptedUnit}</span>}{counts.removed ? <span>已移除 {counts.removed.toLocaleString('zh-CN')} 行</span> : null}
        </> : <span>{busy ? '正在识别与核对' : item.phase === 'failed' ? '导入失败' : item.phase === 'skipped' ? '已跳过' : item.loadingMembers ? '读取回执中' : complete || unknown ? '处置待核对' : preview ? '预览待确认' : '等待识别'}</span>}
      </div>
    </div>{directOriginal && actions?.sourceCopy && result?.source_id && originalAvailable && <button type="button" className="button secondary ft-open-copy" disabled={!!downloading} onClick={() => selectedFileAction('open')}><img src={nativeIcon || `/static/ui-icons/${importIcon(item.filename)}.svg`} width="18" height="18" alt="" />打开副本</button>}{selected && <button ref={moreButton} type="button" className="ft-more icon-button" aria-label="文件操作" aria-haspopup="menu"
      aria-expanded={menu?.anchor.trigger === moreButton.current && !!menu} aria-controls={menu ? menuId : undefined}
      onClick={event => menu?.anchor.trigger === event.currentTarget ? closeMenu() : openMenu(selected, { trigger: event.currentTarget })}><ImportIcon name="dots" /></button>}</div>;
  const menuNode = findNode(roots, menu?.nodeId);
  const menuItem = state.items.find(row => row.id === menuNode?.itemId);
  const menuRoot = roots.find(node => node.id === menuItem?.id);
  const menuPhysical = menuNode && (containingFile(roots, menuNode.id) ?? menuNode);
  const focusRow = (id?: string) => {
    requestAnimationFrame(() => {
      if (scopeSignal.aborted) return;
      const rows = body.current?.querySelectorAll<HTMLElement>('[data-import-node]');
      const row = (rows && [...rows].find(element => element.dataset.importNode === id))
        ?? body.current?.querySelector<HTMLElement>('[data-import-node][tabindex="0"]');
      (row ?? footer.current?.querySelector<HTMLButtonElement>('button:not(:disabled)'))?.focus({ preventScroll: true });
      row?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
    });
  };
  const focusAfterRemoval = (removedId: string) => {
    const index = roots.findIndex(node => node.id === removedId);
    focusRow(roots[index + 1]?.id ?? roots[index - 1]?.id);
  };
  const fundsReportMode = showResultDetails && !item?.job?.withdrawn && !!client.importFundsReport
    && (!complete || (inspectable && !!reviewMember && !!result?.source_id));
  const inspectorToggleLabel = inspectorCollapsed ? '展开右侧详情' : '折叠右侧详情';
  if (!active || scopeSignal.aborted) return null;
  return <Modal className={`ft-import-dialog ${expanded ? 'is-expanded' : 'is-entry'}${showResultDetails ? ' is-result-view' : ''}`} title="导入流水" compact dismissOutside={!item && !state.reading} onClose={close} onFileDrop={modalDrop}
    headerAction={expanded && <>
      <div className="ft-file-search"><ImportIcon name="search" /><input ref={search} type="search" aria-label="搜索导入文件" placeholder="搜索文件…" value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Escape' && query) { event.preventDefault(); event.stopPropagation(); setQuery(''); } }} /><kbd>Ctrl K</kbd></div>
      {item && <button ref={inspectorToggle} type="button" className="ft-inspector-toggle icon-button" aria-controls={inspectorId} aria-expanded={!inspectorCollapsed}
        aria-label={inspectorToggleLabel} title={inspectorToggleLabel} onClick={toggleInspector}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true" focusable="false">
          <rect x="3" y="5" width="18" height="14" rx="3" />
          <path d={inspectorCollapsed ? 'M17 8.5v7' : 'M15 5v14'} />
        </svg>
      </button>}
    </>}>
    {complete && <div className="ft-sr-only" role="status" aria-label="导入完成"><span>{classified ? '自动处理完成' : '流水已写入当前案件'}</span><p>{classified && <>已保存 {feedback?.record_total ?? summary?.record_total} 条记录 · </>}新增流水记录 {summary?.effective_added ?? result?.ok ?? 0} 条 · 重复 {result?.rows_dup ?? 0} 条 · {feedback ? <>已配对冲正/撤销 {feedback.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {feedback.non_monetary} 条 · 未计入资金流水 {unresolvedCount(feedback)} 条 · 来源反馈 {feedback.non_transaction} 条 · 辅助资料 {feedback.auxiliary} 条</> : summary ? <>未计入资金流水 {summary.unresolved} 条 · 已配对冲正/撤销 {summary.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {summary.non_monetary} 条 · 非交易记录 {summary.non_transaction} 条 · 辅助资料 {summary.auxiliary} 条</> : <>跳过 {result?.skipped ?? 0} 行</>}</p></div>}
    <input className="ft-file-input" ref={fileInput} id={fileId} aria-label="选择流水文件" aria-describedby={!expanded ? `${fileId}-hint ${fileId}-group` : undefined} tabIndex={-1} type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.zip,.rar,.pdf" disabled={!intakeAvailable} onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ''; receive(() => files); }} />
    {client.previewDirectory && <input className="ft-file-input" id={directoryId} tabIndex={-1} aria-label="选择整个文件夹" type="file" multiple ref={element => { directoryInput.current = element; element?.setAttribute('webkitdirectory', ''); }} disabled={!intakeAvailable} onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ''; receive(() => files, true); }} />}
    {expanded && !!state.items.length && <ImportStepper items={stepItems} processing={processing} total={stepTotal} confirmed={stepConfirmed} animateCompletion={resume?.stage !== 'done'} />}
    {state.error && <p className="error-banner" role="alert">{state.error}</p>}
    {!expanded ? <div className={`ft-drop-zone import-drop-area ${dragging && intakeAvailable ? 'is-dragging' : ''}`} {...fileDropEvents}><ImportIcon name={state.reading ? 'import-loader' : 'upload'} className={state.reading ? 'is-spinning' : ''} /><h3>{state.reading ? '正在读取所选文件…' : '拖入文件或文件夹'}</h3><p id={`${fileId}-hint`}>Excel · CSV · ZIP · RAR · PDF</p><div className="ft-file-actions"><button type="button" className="button primary" disabled={!intakeAvailable} onClick={() => fileInput.current?.click()}>选择文件</button>{client.previewDirectory && <button type="button" className="button secondary" disabled={!intakeAvailable} onClick={() => directoryInput.current?.click()}>选择文件夹</button>}</div><p id={`${fileId}-group`} className="ft-sr-only">多选文件顺序处理；需要资料相互关联时，请选择整个文件夹。</p></div>
      : item ? <div ref={body} className={`ft-import-body${inspectorCollapsed ? ' is-inspector-collapsed' : ''}${showFileMetadata ? ' has-file-metadata' : ''}`}>
        <aside className="ft-import-files" data-append-drop={dragging && intakeAvailable || undefined} {...fileDropEvents}>
          <div className="ft-import-files-content">
            <ImportTree roots={roots} items={state.items} selectedId={selected?.id} query={query} onClearSearch={() => setQuery('')} onOpen={openFile} onContext={openMenu}
              canReview={!!client.importReview} canLocateSource={!!client.importRecords}
              showMetadata={showFileMetadata} metadataActive={inspectorCollapsed} heading={<>
                <h3>导入文件</h3><div className="ft-counts ft-file-counts" aria-label="文件处理汇总">{fileSummary.map(({tone,label,count,icon})=><span key={label} data-tone={tone}><ImportIcon name={icon} />{label} {count ?? '—'}</span>)}</div>
                {state.items.some(row => row.phase === 'queued') && !state.stopped && <button className="ft-text-button" onClick={() => flow?.stop()}>停止后续文件</button>}
              </>} />
          </div>
        </aside>
        {!inspectorCollapsed && !showFileMetadata && <ImportInspectorDivider onCollapse={toggleInspector} />}
        <section id={inspectorId} className={`ft-inspector ${fundsReportMode ? 'is-report' : ''}`} aria-label="文件识别与预览，可上下滚动"
          inert={inspectorCollapsed} aria-hidden={inspectorCollapsed || undefined}>
          <div className="ft-inspector-content" ref={inspectorElement} tabIndex={0}>
          {keepInspector && (fundsReportMode ? complete && inspectable && reviewMember && result?.source_id ? <ImportFundsReport key={`${client.binding.scopeKey}:${client.binding.generation}:${result.source_id}:${reviewMember}`} client={client} scopeSignal={scopeSignal} sourceId={result.source_id} memberPath={reviewMember} dataVersion={state.dataVersion} header={fileHeading} actions={actions} chapter={reportChapter} onChapter={setReportChapter} /> : <div className="ft-report-unavailable">{fileHeading}<p>{!inspectable ? '选择文件或工作表，查看其资金统计报告。' : item.phase === 'failed' ? '此文件导入失败，没有可统计的资金记录。' : '此文件尚未完成导入，报告暂不可用。'}</p>{item.error && <p role="alert">{item.error}</p>}</div> : <>
          {fileHeading}
          {sourceRoot && !selected && <p role="status">当前导入回执中找不到所指定的来源成员。</p>}
          {showPdfCoverage && <p className="ft-receipt">在 {scannedPages} 页 PDF 中识别到 {candidatePages} 页的表格</p>}
          {attachment && <p role="note">{attachmentReason ? feedbackReason(attachmentReason) : '附件内容暂不可预览。'}</p>}

          {item.error && <div className={unknown || complete || item.phase === 'skipped' ? 'warning-banner' : 'error-banner'} role="alert"><span>{item.error}</span>{unknown && <p>导入结果暂无法确认，请重新查询进度，确认前不会再次提交。</p>}{unknown && item.importId && <button className="button secondary" onClick={() => void flow?.query(item.id)}>重新查询进度</button>}{item.phase === 'failed' && item.importId && (item.preview || item.resume?.source_available) && item.job?.recoverable !== false && <button className="button secondary" onClick={() => void flow?.repreview(item.id)}>{item.preview ? '重新预览后重试' : '重试恢复预览'}</button>}</div>}

          {!pdfReview && !showResultDetails && inspectable && (preview || mappingRows.length > 0 || headerAssistance.length > 0 || headerAssistanceLimited) && <section className="ft-mapping-section"><div className="ft-section-heading"><h4>字段映射</h4><p className="ft-header-detection">{documents.length ? documents.map((document, index) => <span key={index}>{document.header_row != null ? `表头第 ${String(document.header_row)} 行` : '表头未能唯一识别'}{index < documents.length - 1 ? '；' : ''}</span>) : classified ? '表头未识别' : '表头行未确定'}{distinctColumns.length > 0 && <> · 已识别 {recognized.size} / {distinctColumns.length} 个字段</>}</p>{item.dirty && <span>映射已修改</span>}</div>

            <ImportTable fillToSlot resizable={false} className="ft-mapping-scroll" label="字段映射，可上下左右滚动" headings={['原始表头','识别字段','状态']} widths={[234,234,228]}><tbody>{fieldRows.map(({ column, entries }, index) => <tr key={`${column}:${index}`}><td><ImportCellValue>{column}</ImportCellValue></td><td><ImportCellValue>{entries.map(row => fieldNames[row.field] || row.field).join('、') || '—'}</ImportCellValue></td><td data-tone={entries.length ? 'success' : 'muted'}><span className="ft-status-cell"><ImportIcon name={entries.length ? 'import-circle-check' : 'import-circle'} /><span>{entries.length ? '已识别' : '未映射'}</span></span></td></tr>)}</tbody></ImportTable>{!fieldRows.length && <p>来源未提供可展示的表头。</p>}
            {(headerAssistance.length > 0 || headerAssistanceLimited) && <details className="ft-mapping-edit">
              <summary>表头辅助依据{headerAssistance.length > 0 ? ` · ${headerAssistance.length} 处` : ''}</summary>
              <ul>{headerAssistance.map((entry, index) => <li key={`${entry.header_row}:${index}`}>
                原始第 {entry.header_rows.join('、')} 行{entry.columns.length > 0 ? ` · 第 ${entry.columns.join('、')} 列` : ''}
                {' · '}{entry.status === 'verified' ? '已核验' : '待核对'} · {headerReasons[entry.reason] || '查看当前来源表头'}
                {entry.status === 'review' && headerSuggestions(entry.suggestions).map(hint =>
                  <span key={hint.column_index}> · 模型建议核对第 {hint.column_index} 列（{headerSuggestionRoles[hint.role]}）</span>)}
              </li>)}</ul>
              {headerAssistanceLimited && <p>超出辅助处理范围的区域保留待核对。</p>}
            </details>}
            {!complete && (preview?.automatic !== true || !!item.resume || item.phase === 'ready') && !preview?.feedback && <details className="ft-mapping-edit"><summary>调整字段映射</summary><label className="mode-field">文件类型<select aria-label="流水文件类型" disabled={readonly} value={item.mode} onChange={event => { const mode = event.target.value as 'A' | 'B'; flow?.edit(item.id, { mode, mapping: Object.fromEntries(Object.entries(item.mapping).filter(([field]) => modeFields[mode].has(field))) }); }}><option value="A">双边流水（付款方 → 收款方）</option><option value="B">单账户流水（本方与交易对手）</option></select></label><div className="ft-mapping-scroll ft-mapping-controls" role="region" tabIndex={0} aria-label="调整映射，可上下左右滚动">{(item.mode === 'A' ? fieldsA : fieldsB).map(field => <label key={field}>{fieldNames[field]}<small>{importFieldRequirement(field, item.mode, item.mapping).note}</small><select aria-label={`映射${fieldNames[field]}`} disabled={readonly} value={item.mapping[field] || ''} onChange={event => flow?.edit(item.id, { mapping: { ...item.mapping, [field]: event.target.value } })}><option value="">不使用</option>{preview?.columns.map(column => <option key={column}>{column}</option>)}</select></label>)}</div><label className="ft-supplementary">补充本方账号<input type="text" aria-label="补充本方账号" value={item.selfAccountsText} disabled={readonly} placeholder="可选，逗号分隔" onChange={event => flow?.edit(item.id, { selfAccountsText: event.target.value })} /></label></details>}
            {preview?.reference_policy_available && !complete && <label className="ft-reference-choice"><input type="checkbox" checked={item.referencePolicy} disabled={readonly} onChange={event => flow?.edit(item.id, { referencePolicy: event.target.checked })} />凭证号作为独立引用（不与交易流水号比较）</label>}
            {item.dirty && <button className="button secondary" onClick={() => void flow?.repreview(item.id)}>按当前映射重新预览</button>}
          </section>}
          {!reviewAvailable && !showResultDetails && inspectable && (selectedExamples.length > 0 || genericExamples.length > 0) && <section className="ft-issues"><div className="ft-section-heading"><h4>识别问题（{selectedExamples.length + genericExamples.length}）</h4></div>{selectedExamples.map((row, index) => <div key={index} className="ft-issue"><ImportIcon name="import-circle-x" /><span>{!pdfReview && <>原始第 {row.source_row} 行 · </>}{feedbackReason(row.reason_code)}</span>{result?.source_id && <a href="#ft-source-records" onClick={event => { event.preventDefault(); setSelectedId(selected?.id); setShowResultDetails(false); setRecordFocus(previous => ({ itemId: item.id, memberPath: row.member_path, sourceRow: row.source_row, sequence: (previous?.sequence ?? 0) + 1 })); }}>查看来源行</a>}</div>)}{genericExamples.map((row, index) => <div key={`generic:${index}`} className="ft-issue"><ImportIcon name="import-circle-x" /><span>{row.source_row != null ? `原始第 ${row.source_row} 行` : row.row} · {feedbackReason(row.reason)}</span></div>)}</section>}
          {!showResultDetails && inspectable && (result?.source_id ? <div id="ft-source-records">{reviewAvailable ? <ImportReview key={`${client.binding.scopeKey}:${client.binding.generation}:${item.id}:${selected?.id}:${reviewFocus?.sequence ?? 0}:${exactSourceRow ?? ''}:${item.job?.withdrawn ?? false}`} client={client} scopeSignal={scopeSignal} sourceId={result.source_id} memberPath={reviewMember!} reserveIssueBar={countsKnown && needsReview > 0} startInReview={!!reviewFocus} startSourceRow={exactSourceRow} pdf={pdfReview} /> : <ImportRecordEvidence key={`${client.binding.scopeKey}:${client.binding.generation}:${item.id}:${selected?.id}:${recordFocus?.itemId === item.id ? recordFocus.sequence : 0}:${exactSourceRow ?? ''}`} compact initialRows={samples} filename={selected?.name || item.filename} caseId={target.caseId} openPreview focusRow={recordSourceRow} onClearFocus={() => { setSelectedId(selected?.id); setRecordFocus(undefined); }} sourceId={result.source_id} memberPath={selected?.memberPaths.length === 1 ? selected.memberPaths[0] : undefined} client={client} scopeSignal={scopeSignal} />}</div> : preview && !pdfReview && <ImportDataPreview rows={samples} filename={item.filename} feedback={!!feedback} />)}
          {!attachment && complete && <section hidden={!showResultDetails} className="ft-result-data" aria-label="处理结果" ref={resultElement}><h4>导入回执</h4><table><thead><tr><th>处理结果</th><th>{resume?.stage === 'done' ? '导入当时数量' : '数量'}</th></tr></thead><tbody><tr><td>新增流水记录</td><td>{summary?.effective_added ?? result?.ok ?? 0}</td></tr><tr><td>重复</td><td>{result?.rows_dup ?? 0}</td></tr><tr><td>未计入</td><td>{feedback ? unresolvedCount(feedback) : summary?.unresolved ?? result?.skipped ?? 0}</td></tr></tbody></table><p className="ft-receipt">批次 {result?.batch_id} · {item.filename}</p>
            {result?.source_id && inspectable && <ImportRecordEvidence caseId={target.caseId} sourceId={result.source_id} memberPath={selected?.memberPaths.length === 1 ? selected.memberPaths[0] : undefined} client={client} scopeSignal={scopeSignal} />}
          </section>}
          {!attachment && preview && !readonly && preview.self_accounts_in_file.length > 0 && <details className="ft-main-accounts"><summary>标记本方主账号</summary>{preview.self_accounts_in_file.map(account => <label key={account.account}><input type="checkbox" checked={item.marked.includes(account.account)} onChange={event => flow?.edit(item.id, { marked: event.target.checked ? [...item.marked, account.account] : item.marked.filter(value => value !== account.account) })} />{account.name || account.account}<small>{account.account}</small></label>)}</details>}
          {!attachment && (classified || resolution) && <details hidden={!showResultDetails} className="ft-full-summary" open><summary>完整处理统计与识别依据</summary>{feedback ? <FeedbackSummary feedback={feedback} complete={complete} uncertain={unknown} /> : summary ? <TableSummary summary={summary} complete={complete} uncertain={unknown} /> : null}{resolution && <ResolutionSummary value={resolution} complete={complete} />}
            {summary?.effective_added != null && summary.effective_added !== result?.ok && <p>本次保留来源流水记录 {result?.ok} 条，其中新增有效流水记录 {summary.effective_added} 条；已有配对依据的记录不重复进入统计。</p>}
            {complete && classified && <div className="ft-downloads"><button className="button secondary" disabled={!result?.source_id || !actions?.downloadImportReport || !!downloading} onClick={() => selectedFileAction('report')}>下载完整处理报告（JSON）</button><button className="button secondary" disabled={!result?.source_id || !actions?.downloadSource || !originalAvailable || !!downloading} onClick={() => selectedFileAction('download')}>{feedback ? '下载原始 ZIP' : '下载原始文件'}</button>{(!actions?.downloadImportReport || !actions?.downloadSource) && <p>宿主未提供{!actions?.downloadImportReport ? '处理报告' : ''}{!actions?.downloadImportReport && !actions?.downloadSource ? '和' : ''}{!actions?.downloadSource ? '原件' : ''}下载能力。</p>}{!originalAvailable && <p>{feedback ? '原始 ZIP 已不可用' : '原始文件已不可用'}；处理报告仍可用于追溯。</p>}</div>}
          </details>}
          {downloadError && <p role="alert">{downloadError}</p>}
          </>)}
          </div>
        </section></div> : <div className={`ft-drop-zone ${dragging && intakeAvailable ? 'is-dragging' : ''}`} {...fileDropEvents}>
          <p>暂无导入文件</p>{canAppend && <p>可拖入文件或文件夹</p>}
        </div>}
    {menu && menuNode && menuItem && menuRoot && menuPhysical && <ImportFileMenu key={menu.nodeId} id={menuId} anchor={menu.anchor}
      item={menuItem} node={menuNode} original={menuRoot} physicalFile={menuPhysical} documents={documentsFor(menuItem, menuPhysical)}
      client={client} scopeSignal={scopeSignal} actions={actions} onClose={closeMenu}
      fileActionBusy={!!downloading} onFileAction={(kind, sourceId, filename) => void fileAction(kind, sourceId, filename, true)}
      writeBusy={state.running || state.reading || state.withdrawing || state.items.some(row => !['done', 'ready', 'failed', 'skipped'].includes(row.phase))}
      onView={() => openFile(menuNode, { kind: 'preview' })}
      onLocate={() => { setQuery(''); openFile(menuRoot, { kind: 'preview' }); focusRow(menuRoot.id); }}
      onRemove={() => {
        if (flow?.remove(menuItem.id)) focusAfterRemoval(menuRoot.id);
      }}
      onQuery={() => void flow?.query(menuItem.id)} onWithdraw={async batchId => {
        if (await flow?.withdraw(menuItem.id, batchId)) {
          closeMenu(false);
          focusAfterRemoval(menuRoot.id);
          port.notify({ operation: 'import', severity: 'success', title: '已删除导入项' });
        }
      }} />}
    {expanded && <footer ref={footer} className="ft-import-footer">
      {canAppend && <div className="ft-import-append"><ImportAppendControl disabled={!intakeAvailable} reading={state.reading}
        onFiles={() => fileInput.current?.click()} onDirectory={client.previewDirectory ? () => directoryInput.current?.click() : undefined} /></div>}
      {!canAppend && resume && onOpenNewImport && <button type="button" className="button secondary" onClick={onOpenNewImport}><ImportIcon name="file-plus" />导入新资料</button>}
      <div className="ft-footer-actions">{pdfReview && complete && originalAvailable && actions?.downloadSource && <button type="button" className="button secondary" disabled={!!downloading} onClick={() => selectedFileAction('download')}>下载原始 PDF</button>}
        {pdfReview && complete && result?.source_id && actions?.downloadImportReport &&
          <button type="button" className="button secondary" disabled={!!downloading} onClick={() => selectedFileAction('report')}>下载识别记录</button>}
        {!pdfReview && state.items.length > 0 && <button className="button primary" disabled={!showResultDetails && !canShowResult} onClick={() => { setCollapsedInspector(null); setShowResultDetails(value => !value); }}>{showResultDetails ? '返回数据预览' : '查看处理结果'}</button>}<button className="button secondary" onClick={close}>{!queuePending ? '完成' : '关闭进度'}</button>{preview && !complete && !pending && !state.running && <button className="button primary" disabled={!!busy || state.running || state.withdrawing || item.dirty || !classified && preview.stats.ok === 0 || item.phase === 'failed'} onClick={() => void flow?.commit(item.id)}>{classified ? '开始导入' : '确认导入'}</button>}</div></footer>}
  </Modal>;
}
