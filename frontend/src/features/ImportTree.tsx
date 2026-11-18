import { useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import type { ImportItem } from './import-workflow.mjs';
import { filterImportTree, flattenImportTree, importIcon, type ImportTreeNode } from './import-tree';
import { ImportFileName } from './ImportFileName';

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
  if (item.phase === 'failed') return { label: '失败', icon: 'import-circle-x', tone: 'danger', active: false };
  if (item.phase === 'unknown' || item.queryingOnly) return { label: '结果待核对', icon: 'import-clock', tone: 'warning', active: false };
  if (item.phase === 'skipped') return { label: '跳过 · 未上传', icon: 'import-circle-minus', tone: 'muted', active: false };
  if (item.phase === 'done') {
    if (item.loadingMembers && node?.memberPaths.length) return { label: '读取中', icon: 'import-loader', tone: 'muted', active: false };
    if (node?.memberPaths.length && item.job?.member_results === undefined) return { label: '成员处置待核对', icon: 'import-clock', tone: 'warning', active: false };
    const counts = node?.dispositions ?? {}, unresolved = (counts.unresolved ?? 0) + (counts.review ?? 0);
    return { label: unresolved ? `完成 · 待核对 ${unresolved} 条` : counts.imported ? `已导入 ${counts.imported} 条` : counts.removed ? `已移除 ${counts.removed} 条` : counts.duplicate ? `重复 ${counts.duplicate} 条` : '处理完成',
      icon: unresolved ? 'import-clock' : 'import-circle-check', tone: unresolved ? 'warning' : 'success', active: false };
  }
  if (item.phase === 'ready') return { label: '待确认', icon: 'import-clock', tone: 'accent', active: false };
  if (item.phase === 'queued') return { label: '等待识别', icon: 'import-clock', tone: 'muted', active: false };
  return { label: item.phase === 'uploading' || item.phase === 'previewing' ? '识别中' : item.job?.stage === 'importing' ? '导入中' : '识别中', icon: 'import-loader', tone: 'accent', active: true };
}
export function ImportTree({ roots, items, selectedId, onSelect, query = '', onClearSearch }: { roots: ImportTreeNode[]; items: ImportItem[]; selectedId?: string; onSelect: (node: ImportTreeNode) => void; query?: string; onClearSearch?: () => void }) {
  const [collapsed, setCollapsed] = useState(new Set<string>()), [focus, setFocus] = useState<string>();
  const elements = useRef(new Map<string, HTMLDivElement>());
  const filtered = filterImportTree(roots, query), effectiveCollapsed = query.trim() ? new Set<string>() : collapsed;
  const visible = flattenImportTree(filtered, effectiveCollapsed), focused = visible.find(row => row.node.id === focus)?.node.id ?? visible.find(row => row.node.id === selectedId)?.node.id ?? visible[0]?.node.id;
  const visibleIndex = new Map(visible.map((row, index) => [row.node.id, index])), itemsById = new Map(items.map(item => [item.id, item]));
  const descendants = (node: ImportTreeNode, id?: string): boolean => node.children.some(child => child.id === id || descendants(child, id));
  const toggle = (id: string) => {
    if (query.trim()) return;
    const branch = visible.find(row => row.node.id === id)?.node;
    if (branch && descendants(branch, focus)) { setFocus(id); elements.current.get(id)?.focus(); }
    setCollapsed(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  };
  const move = (id?: string) => { if (id) { setFocus(id); elements.current.get(id)?.focus(); elements.current.get(id)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }); } };
  const keyboard = (event: KeyboardEvent, index: number) => {
    const row = visible[index], node = row.node;
    if (!['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Enter', ' '].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'ArrowDown') move(visible[Math.min(index + 1, visible.length - 1)]?.node.id);
    else if (event.key === 'ArrowUp') move(visible[Math.max(0, index - 1)]?.node.id);
    else if (event.key === 'Home') move(visible[0]?.node.id);
    else if (event.key === 'End') move(visible.at(-1)?.node.id);
    else if (event.key === 'ArrowRight' && node.children.length) { if (effectiveCollapsed.has(node.id)) toggle(node.id); else move(node.children[0].id); }
    else if (event.key === 'ArrowLeft') { if (node.children.length && !effectiveCollapsed.has(node.id)) toggle(node.id); else move(row.parentId); }
    else if (event.key === 'Enter' || event.key === ' ') { if (node.children.length) toggle(node.id); else onSelect(node); }
  };
  const renderNodes = (nodes: ImportTreeNode[], level = 1, ancestorLines: boolean[] = []) => nodes.map((node, sibling) => {
    const last = sibling === nodes.length - 1, index = visibleIndex.get(node.id) ?? -1, isOpen = !effectiveCollapsed.has(node.id);
    const item = itemsById.get(node.itemId)!, state = importState(item, node);
    const finished = ['done', 'failed', 'skipped'].includes(item.phase);
    const uncertain = item.phase === 'unknown' || item.queryingOnly;
    const progress = finished ? 1 : node.id === item.id && item.job?.stage === 'importing' && item.job.total > 0 ? item.job.done / item.job.total : undefined;
    return <div key={node.id} role="none"><div ref={element => { if (element) elements.current.set(node.id, element); else elements.current.delete(node.id); }}
      role="treeitem" aria-level={level} aria-selected={node.id === selectedId} aria-expanded={node.children.length ? isOpen : undefined}
      tabIndex={node.id === focused ? 0 : -1} className={`ft-tree-row ${node.children.length ? 'is-branch' : 'is-leaf'}`} data-last={last} data-tone={state.tone} data-selected-descendant={!isOpen && descendants(node, selectedId) || undefined} style={{ '--tree-level': level, '--tree-indent': `${(level - 1) * 24 + (!node.children.length && level > 1 ? 24 : 0)}px`, '--tree-elbow': `${!node.children.length ? -32 : -8}px` } as CSSProperties}
      onFocus={() => setFocus(node.id)} onKeyDown={event => keyboard(event, index)} onClick={() => node.children.length ? toggle(node.id) : onSelect(node)}>
      {level > 1 && <span className="ft-tree-guides" aria-hidden="true"><span className="ft-tree-elbow" />{ancestorLines.slice(1).map((continues, ancestor) => continues ? <span key={ancestor} className="ft-tree-ancestor" style={{ left: `${ancestor * 24 + 16 - ((level - 1) * 24 + (!node.children.length ? 24 : 0))}px` }} /> : null)}</span>}
      <div className="ft-tree-line">{node.children.length ? <button type="button" className="ft-tree-toggle" tabIndex={-1} aria-label={`${isOpen ? '收起' : '展开'}${node.name}`} onClick={event => { event.stopPropagation(); toggle(node.id); }}><ImportIcon name="chevron-right" className={isOpen ? 'is-expanded' : ''} /></button> : node.kind === 'folder' || node.kind === 'archive' || level === 1 ? <span className="ft-tree-toggle" /> : null}
        <ImportIcon name={importIcon(node.name, node.kind)} className={`ft-file-icon ${node.kind === 'sheet' || /\.(xls|xlsx|csv)$/i.test(node.name) ? 'is-spreadsheet' : ''}`} />
        <ImportFileName className="ft-tree-name" name={node.name} path={node.path || node.name} /><span className="ft-tree-state"><ImportIcon name={state.icon} className={state.icon === 'import-loader' ? 'is-spinning' : ''} /><span className="ft-tree-state-text" title={state.label}>{node.children.length ? node.kind === 'file' ? `${node.children.length} 个工作表` : `${node.fileCount} 个文件${state.active ? ' · 处理中' : item.phase === 'done' ? '' : ` · ${state.label}`}` : (node.kind === 'archive' || node.kind === 'folder') && !node.fileCount ? `— 个文件 · ${state.label}` : state.label.replace(/^完成 · 待核对 (\d+) 条$/, '$1 待核对').replace(/^已导入 /, '')}</span></span></div>
      {(node.children.length > 0 || state.active || finished || uncertain) && <div className="ft-tree-meta"><ImportProgress compact label={`${node.name}处理进度`} active={state.active} value={uncertain ? undefined : progress} visualValue={progress} /></div>}
    </div>{node.children.length > 0 && <div className={`ft-tree-children ${isOpen ? 'is-open' : ''}`} aria-hidden={!isOpen} inert={!isOpen}><div role="group">{renderNodes(node.children, level + 1, [...ancestorLines, !last])}</div></div>}</div>;
  });
  return <div className="ft-tree-scroll" role="tree" aria-label="导入文件">{renderNodes(filtered)}{!filtered.length && <div className="ft-search-empty">未找到文件 <button className="ft-text-button" onClick={onClearSearch}>清空搜索</button></div>}</div>;
}
