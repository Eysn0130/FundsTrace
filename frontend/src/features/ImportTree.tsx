import { useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { formatFileSize, formatImportDuration } from '../lib/format';
import type { ImportItem } from './import-workflow.mjs';
import { filterImportTree, flattenImportTree, importIcon, type ImportTreeNode } from './import-tree';
import { ImportFileName } from './ImportFileName';
import { feedbackReason } from './ImportDetails';
import type { ImportMenuAnchor } from './ImportFileMenu';

export function ImportIcon({ name, className = '' }: { name: string; className?: string }) {
  if (name.startsWith('import-file-')) return <img className={`ft-icon ft-document-icon ${className}`} src={`/static/ui-icons/${name}.svg`} alt="" aria-hidden="true" draggable={false} />;
  return <span className={`ft-icon ${className}`} style={{ maskImage: `url('/static/ui-icons/${name}.svg')` }} aria-hidden="true" />;
}
export function ImportProgress({ value, visualValue = value, label, active = false, compact = false, complete = false }: { value?: number; visualValue?: number; label: string; active?: boolean; compact?: boolean; complete?: boolean }) {
  return <div className={`ft-progress ${compact ? 'is-compact' : ''} ${active ? 'is-active' : ''} ${complete ? 'is-complete' : ''} ${visualValue === undefined ? 'is-indeterminate' : ''}`}>
    <progress aria-label={label} {...(value === undefined ? {} : { max: 1, value })} />
    <span className="ft-progress-track" aria-hidden="true"><span className="ft-progress-fill" style={visualValue === undefined ? undefined : { transform: `scaleX(${Math.max(0, Math.min(visualValue, 1))})` }} /></span>
  </div>;
}
export function importState(item: ImportItem, node?: ImportTreeNode) {
  if (item.job?.withdrawn) return { label: '已撤回', icon: 'import-circle-minus', tone: 'muted', active: false };
  if (item.phase === 'failed') return { label: '失败', icon: 'import-circle-x', tone: 'danger', active: false };
  if (item.phase === 'unknown' || item.queryingOnly) return { label: '结果待核对', icon: 'import-clock', tone: 'warning', active: false };
  if (item.phase === 'skipped') return { label: '跳过 · 未上传', icon: 'import-circle-minus', tone: 'muted', active: false };
  if (item.phase === 'done') {
    if (item.loadingMembers && node?.memberPaths.length) return { label: '读取中', icon: 'import-loader', tone: 'muted', active: false };
    if (node?.memberPaths.length && item.job?.member_results === undefined) return { label: '成员处置待核对', icon: 'import-clock', tone: 'warning', active: false };
    const counts = node?.dispositions ?? {}, unresolved = (counts.unresolved ?? 0) + (counts.review ?? 0);
    if (node?.attachment) {
      const retained = !unresolved && counts.auxiliary;
      return { label: retained ? '附件已保留' : '附件待核对', icon: retained ? 'import-circle-check' : 'import-clock',
        tone: retained ? 'success' : 'warning', active: false };
    }
    return { label: unresolved ? `完成 · 待核对 ${unresolved} 条` : counts.imported ? `已导入 ${counts.imported} 条` : counts.removed ? `已移除 ${counts.removed} 条` : counts.duplicate ? `重复 ${counts.duplicate} 条` : '处理完成',
      icon: unresolved ? 'import-clock' : 'import-circle-check', tone: unresolved ? 'warning' : 'success', active: false };
  }
  if (item.phase === 'ready') return { label: '待确认', icon: 'import-clock', tone: 'accent', active: false };
  if (item.phase === 'queued') return { label: '排队中', icon: 'import-clock', tone: 'muted', active: false };
  return { label: item.phase === 'uploading' || item.phase === 'previewing' ? '识别中' : item.job?.stage === 'importing' ? '导入中' : '识别中', icon: 'import-loader', tone: 'accent', active: true };
}
const metadataHeadings = ['数据大小', '数据行数', '处理摘要', '导入时长'];
const notIncluded = ['non_monetary', 'cancelled', 'non_transaction', 'auxiliary', 'removed'];
const countText = (value?: number) => value?.toLocaleString('zh-CN') ?? '—';

export type ImportTreeDestination = { kind: 'result' } | { kind: 'preview' } | { kind: 'review'; memberPath: string; sourceRow?: number };
type ReviewMetadata = { summary?: string; tone?: 'warning' | 'danger'; destination?: ImportTreeDestination };

function reviewMetadata(item: ImportItem, node: ImportTreeNode, canReview: boolean, canLocateSource: boolean): ReviewMetadata {
  if (item.job?.withdrawn) return { summary: '导入已撤回', destination: { kind: 'preview' } };
  if (item.phase === 'failed') return { summary: '文件处理失败', tone: 'danger', destination: item.error ? { kind: 'preview' } : undefined };
  const receipt = item.phase === 'done' && !item.loadingMembers && !item.queryingOnly ? item.job?.result : undefined;
  const counts = receipt && item.job?.member_results !== undefined && node.memberPaths.length ? node.dispositions : undefined;
  const legacy = node.id === item.id && !node.memberPaths.length && !receipt?.table_summary && !receipt?.feedback ? receipt : undefined;
  const pending = counts ? (counts.unresolved ?? 0) + (counts.review ?? 0) : legacy?.skipped;
  if (node.attachment) {
    if (pending) return { tone: 'warning', summary: '附件格式未适配', destination: { kind: 'preview' } };
    return counts?.auxiliary ? { summary: '非业务附件 · 原件已保留' } : {};
  }
  const pendingSummary = pending ? `${countText(pending)} 条待核对` : '';
  if (pending && counts && !node.children.length) {
    const table = receipt?.table_summary, feedback = receipt?.feedback;
    const memberPath = node.memberPaths.length === 1 ? node.memberPaths[0] : undefined;
    // The receipt stores a bounded set of examples. The scoped preview
    // independently locates the first issue still available for that member.
    const example = memberPath && !counts?.removed ? [
      ...(table?.unresolved_examples ?? feedback?.unresolved_examples ?? []), ...(feedback?.review_examples ?? []),
    ].filter(row => row.member_path === memberPath).sort((left, right) => left.source_row - right.source_row)[0] : undefined;
    return {
      tone: 'warning', summary: example ? `${pendingSummary} · ${table?.profile === 'pdf-review-v1' ? '' : `第 ${example.source_row} 行 · `}${feedbackReason(example.reason_code)}` : pendingSummary,
      destination: receipt?.source_id && memberPath && (canReview && !!(table || feedback) || canLocateSource && !!example)
        ? { kind: 'review', memberPath, sourceRow: example?.source_row } : undefined,
    };
  }
  const imported = counts?.imported ?? legacy?.ok;
  const duplicate = counts?.duplicate ?? legacy?.rows_dup;
  const other = counts && notIncluded.reduce((sum, key) => sum + (counts[key] ?? 0), 0);
  const summary = [
    pendingSummary, imported ? `${countText(imported)} 条已导入` : '',
    duplicate ? `${countText(duplicate)} 条重复` : '', other ? `${countText(other)} 条未计入` : '',
  ].filter(Boolean).join(' · ');
  const confirmed = legacy || counts && (node.dataRows === 0 || Object.keys(counts).length > 0);
  return { summary: summary || undefined, tone: pending ? 'warning' : undefined,
    destination: confirmed && !pending ? { kind: 'result' } : undefined };
}

export function ImportTree({ roots, items, selectedId, onOpen, onContext, query = '', onClearSearch, heading,
  showMetadata = false, metadataActive = showMetadata, canReview = false, canLocateSource = false }: {
  roots: ImportTreeNode[]; items: ImportItem[]; selectedId?: string;
  onOpen: (node: ImportTreeNode, destination: ImportTreeDestination) => void;
  onContext?: (node: ImportTreeNode, anchor: ImportMenuAnchor) => void;
  query?: string; onClearSearch?: () => void; heading?: ReactNode;
  showMetadata?: boolean; metadataActive?: boolean; canReview?: boolean; canLocateSource?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(new Set<string>()), [focus, setFocus] = useState<string>();
  const elements = useRef(new Map<string, HTMLDivElement>());
  const scroll = useRef<HTMLDivElement>(null), metadataId = useId();
  useLayoutEffect(() => { if (!showMetadata && scroll.current) scroll.current.scrollLeft = 0; }, [showMetadata]);
  const filtered = filterImportTree(roots, query), effectiveCollapsed = query.trim() ? new Set<string>() : collapsed;
  const visible = flattenImportTree(filtered, effectiveCollapsed), focused = visible.find(row => row.node.id === focus)?.node.id ?? visible.find(row => row.node.id === selectedId)?.node.id ?? visible[0]?.node.id;
  const deepestLevel = visible.reduce((level, row) => Math.max(level, row.level), 0);
  const visibleIndex = new Map(visible.map((row, index) => [row.node.id, index])), itemsById = new Map(items.map(item => [item.id, item]));
  const openFile = (node: ImportTreeNode) => {
    const { destination } = reviewMetadata(itemsById.get(node.itemId)!, node, canReview, canLocateSource);
    onOpen(node, destination ?? { kind: 'preview' });
  };
  const descendants = (node: ImportTreeNode, id?: string): boolean => node.children.some(child => child.id === id || descendants(child, id));
  const toggle = (id: string) => {
    if (query.trim()) return;
    const branch = visible.find(row => row.node.id === id)?.node;
    if (branch && descendants(branch, focus)) { setFocus(id); elements.current.get(id)?.focus(); }
    setCollapsed(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  };
  const move = (id?: string) => { if (id) { setFocus(id); elements.current.get(id)?.focus(); elements.current.get(id)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }); } };
  const context = (node: ImportTreeNode, trigger: HTMLElement, point?: ImportMenuAnchor['point']) => {
    if (!onContext) return;
    trigger.focus({ preventScroll: true });
    const branch = node.children.length && !query.trim() ? { open: !effectiveCollapsed.has(node.id), toggle: () => toggle(node.id) } : undefined;
    onContext(node, { trigger, point, branch });
  };
  const keyboard = (event: KeyboardEvent<HTMLDivElement>, index: number) => {
    if (event.target !== event.currentTarget) return;
    const row = visible[index], node = row.node;
    if (onContext && (event.key === 'ContextMenu' || event.shiftKey && event.key === 'F10')) {
      event.preventDefault(); context(node, event.currentTarget); return;
    }
    if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowDown') move(visible[Math.min(index + 1, visible.length - 1)]?.node.id);
    else if (event.key === 'ArrowUp') move(visible[Math.max(0, index - 1)]?.node.id);
    else if (event.key === 'Home') move(visible[0]?.node.id);
    else if (event.key === 'End') move(visible.at(-1)?.node.id);
    else if (event.key === 'ArrowRight' && node.children.length) { if (effectiveCollapsed.has(node.id)) toggle(node.id); else move(node.children[0].id); }
    else if (event.key === 'ArrowLeft') { if (node.children.length && !effectiveCollapsed.has(node.id)) toggle(node.id); else move(row.parentId); }
    else if (event.key === 'Enter' || event.key === ' ') { if (node.children.length) toggle(node.id); else openFile(node); }
  };
  const renderNodes = (nodes: ImportTreeNode[], level = 1, ancestorLines: boolean[] = []) => nodes.map((node, sibling) => {
    const last = sibling === nodes.length - 1, index = visibleIndex.get(node.id) ?? -1, isOpen = !effectiveCollapsed.has(node.id);
    const item = itemsById.get(node.itemId)!, state = importState(item, node);
    const finished = ['done', 'failed', 'skipped'].includes(item.phase);
    const uncertain = item.phase === 'unknown' || item.queryingOnly;
    const progress = finished ? 1 : node.id === item.id && item.job?.stage === 'importing' && item.job.total > 0 ? item.job.done / item.job.total : undefined;
    const metadata = showMetadata && index >= 0 ? reviewMetadata(item, node, canReview, canLocateSource) : undefined;
    const descriptionId = metadata ? `${metadataId}-${index}` : undefined;
    const dataSize = metadata ? formatFileSize(node.sizeBytes) : undefined;
    const original = node.id === item.id;
    const elapsed = metadata && original && (item.phase === 'done' || item.phase === 'failed') ? item.processingMs : undefined;
    const duration = formatImportDuration(elapsed);
    const durationHelp = !original ? '成员未单独计时' : elapsed === undefined ? '未记录用时' : '本次上传、识别与导入确认用时';
    const action = metadata?.destination?.kind !== 'result' ? metadata?.destination : undefined;
    const actionLabel = action?.kind === 'preview' ? '查看原因' : '定位待核对';
    const completed = item.phase === 'done' && (!node.memberPaths.length || !item.loadingMembers && item.job?.member_results !== undefined);
    const failed = item.phase === 'failed';
    const showTerminal = metadata && !item.job?.withdrawn && (completed || failed);
    const childCount = node.children.length ? (node.kind === 'file' ? `${node.children.length} 个工作表` : `${node.fileCount} 个文件`) : '';
    const groupStatus = node.kind === 'file' || item.phase === 'done' ? '' : ` · ${state.active ? '处理中' : state.label}`;
    const stateText = item.job?.withdrawn ? state.label : childCount ? childCount + groupStatus
      : (node.kind === 'archive' || node.kind === 'folder') && !node.fileCount ? `— 个文件 · ${state.label}`
        : state.label.replace(/^完成 · 待核对 (\d+) 条$/, '$1 待核对').replace(/^已导入 /, '');
    return <div key={node.id} role="none"><div ref={element => { if (element) elements.current.set(node.id, element); else elements.current.delete(node.id); }}
      role="treeitem" aria-level={level} aria-selected={node.id === selectedId} aria-expanded={node.children.length ? isOpen : undefined} aria-describedby={metadataActive ? descriptionId : undefined}
      aria-haspopup={onContext ? 'menu' : undefined} data-import-node={node.id}
      data-complete={item.phase === 'done' || undefined}
      tabIndex={node.id === focused ? 0 : -1} className={`ft-tree-row ${node.children.length ? 'is-branch' : 'is-leaf'}`} data-last={last} data-tone={state.tone} data-selected-descendant={!isOpen && descendants(node, selectedId) || undefined} style={{ '--tree-level': level, '--tree-indent': `${(level - 1) * 24 + (!node.children.length && level > 1 ? 24 : 0)}px`, '--tree-elbow': `${!node.children.length ? -32 : -8}px` } as CSSProperties}
      onFocus={() => setFocus(node.id)} onKeyDown={event => keyboard(event, index)} onClick={() => node.children.length ? toggle(node.id) : openFile(node)}
      onContextMenu={onContext ? event => { event.preventDefault(); event.stopPropagation(); context(node, event.currentTarget, { x: event.clientX, y: event.clientY }); } : undefined}>
      {level > 1 && <span className="ft-tree-guides" aria-hidden="true"><span className="ft-tree-elbow" />{ancestorLines.slice(1).map((continues, ancestor) => continues ? <span key={ancestor} className="ft-tree-ancestor" style={{ left: `${ancestor * 24 + 16 - ((level - 1) * 24 + (!node.children.length ? 24 : 0))}px` }} /> : null)}</span>}
      <div className="ft-tree-line"><div className="ft-tree-label">{node.children.length ? <button type="button" className="ft-tree-toggle" tabIndex={-1} aria-label={`${isOpen ? '收起' : '展开'}${node.name}`} onClick={event => { event.stopPropagation(); toggle(node.id); }}><ImportIcon name="chevron-right" className={isOpen ? 'is-expanded' : ''} /></button> : node.kind === 'folder' || node.kind === 'archive' || level === 1 ? <span className="ft-tree-toggle" /> : null}
        <ImportIcon name={importIcon(node.name, node.kind)} className={`ft-file-icon ${node.kind === 'sheet' || /\.(xls|xlsx|csv)$/i.test(node.name) ? 'is-spreadsheet' : ''}`} />
        <ImportFileName className="ft-tree-name" name={node.name} path={node.path || node.name} />
        <span className="ft-tree-state">{showTerminal ? <>
          <span className="ft-tree-terminal" data-tone={failed ? 'danger' : 'success'} aria-hidden={!metadataActive || undefined} title={state.label}>
            <ImportIcon name={failed ? 'import-circle-x' : 'import-circle-check'} />{failed ? '失败' : '完成'}
          </span>
          {childCount && <span className="ft-tree-state-text">{childCount}</span>}
        </> : <>
          <ImportIcon name={state.icon} className={state.icon === 'import-loader' ? 'is-spinning' : ''} />
          <span className="ft-tree-state-text" title={state.label}>{stateText}</span>
        </>}</span></div>
        {metadata && <>
          <div className="ft-tree-values" inert={!metadataActive}>
            <span aria-hidden="true" data-unknown={node.sizeBytes === undefined || undefined}
              title={node.sizeBytes === undefined ? '未记录数据大小' : `${countText(node.sizeBytes)} B`}>{dataSize}</span>
            <span aria-hidden="true" data-unknown={node.dataRows === undefined || undefined}>{countText(node.dataRows)}</span>
            {action ? <button type="button" className="ft-text-button ft-tree-summary ft-tree-review-action"
              title={`${actionLabel} · ${metadata.summary}`} data-tone={metadata.tone}
              tabIndex={node.id === focused ? 0 : -1} aria-label={`${actionLabel} · ${node.path || node.name}`}
              onClick={event => { event.stopPropagation(); onOpen(node, action); }}>{metadata.summary}</button>
              : <span aria-hidden="true" className="ft-tree-summary" title={metadata.summary}
                data-unknown={!metadata.summary || undefined} data-tone={metadata.tone}>{metadata.summary ?? '—'}</span>}
            <span aria-hidden="true" className="ft-tree-duration" title={durationHelp}
              data-unknown={elapsed === undefined || undefined}>{duration}</span>
          </div>
          <span id={descriptionId} className="ft-sr-only" aria-hidden="true">
            {`数据大小 ${node.sizeBytes === undefined ? '未记录' : dataSize}，数据行数 ${node.dataRows === undefined ? '未记录' : countText(node.dataRows)}`}
            {metadata.summary && `，处理摘要 ${metadata.summary}`}
            {`，导入时长 ${elapsed !== undefined ? duration : original ? '未记录' : '未单独计时'}`}
          </span>
        </>}
      </div>
      {(node.children.length > 0 || state.active || finished || uncertain) && <div className="ft-tree-meta"><ImportProgress compact label={`${node.name}处理进度`} active={state.active} value={uncertain ? undefined : progress} visualValue={progress} /></div>}
    </div>{node.children.length > 0 && <div className={`ft-tree-children ${isOpen ? 'is-open' : ''}`} aria-hidden={!isOpen} inert={!isOpen}><div role="group">{renderNodes(node.children, level + 1, [...ancestorLines, !last])}</div></div>}</div>;
  });
  return <div ref={scroll} className={`ft-tree-scroll${showMetadata ? ' has-metadata' : ''}`}
    style={{ '--ft-tree-leading-min': `${148 + deepestLevel * 24}px` } as CSSProperties}
    role={metadataActive ? 'region' : undefined} aria-label={metadataActive ? '导入文件明细，可横向滚动' : undefined} tabIndex={metadataActive ? 0 : undefined}>
    <div className="ft-tree-content">
      {heading && <header className="ft-tree-header">
        <div className="ft-section-heading">{heading}</div>
        {showMetadata && <div className="ft-tree-values" aria-hidden="true">{metadataHeadings.map((label, index) =>
          <span key={label} className={index === 2 ? 'ft-tree-summary' : undefined}>{label}</span>
        )}</div>}
      </header>}
      <div role="tree" aria-label="导入文件">{renderNodes(filtered)}</div>
      {!filtered.length && <div className="ft-search-empty">未找到文件 <button className="ft-text-button" onClick={onClearSearch}>清空搜索</button></div>}
    </div>
  </div>;
}
