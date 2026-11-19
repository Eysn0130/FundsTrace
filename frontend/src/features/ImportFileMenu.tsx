import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { ImportSourceMetadata } from '../../../contracts/api-v1';
import { useFeedback } from '../feedback/Feedback';
import { displayOperationalTime, formatFileSize, formatImportDuration } from '../lib/format';
import type { FundsCaseClientV1, FundsHostActionsV1 } from '../panel/host';
import { ImportIcon, importState } from './ImportTree';
import type { ImportTreeNode } from './import-tree';
import type { ImportItem } from './import-workflow.mjs';

export type ImportMenuAnchor = {
  trigger: HTMLElement;
  point?: { x: number; y: number };
  branch?: { open: boolean; toggle: () => void };
};
export type ImportFileAction = 'download' | 'open' | 'reveal';
interface Command { label: string; icon: string; run: () => void; disabled?: boolean; danger?: boolean; stay?: boolean }

function ImportActionPopover({ id, anchor, label, headingId, descriptionId, role = 'menu', placement, children, onClose }: {
  id: string; anchor: ImportMenuAnchor; label: string; role?: 'menu' | 'dialog'; placement?: 'above';
  headingId?: string; descriptionId?: string;
  children: ReactNode; onClose: (restoreFocus?: boolean) => void;
}) {
  const panel = useRef<HTMLDivElement>(null), close = useRef(onClose); close.current = onClose;
  useLayoutEffect(() => {
    const pop = panel.current!;
    pop.showPopover?.();
    const dismiss = (event: Event) => {
      if (!pop.contains(event.target as Node) && !anchor.trigger.contains(event.target as Node)) close.current(false);
    };
    const moved = (event: Event) => {
      if (!pop.contains(event.target as Node)) close.current(false);
    };
    const resize = () => close.current();
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('focusin', dismiss);
    document.addEventListener('scroll', moved, true);
    window.addEventListener('resize', resize);
    return () => {
      pop.hidePopover?.();
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('focusin', dismiss);
      document.removeEventListener('scroll', moved, true);
      window.removeEventListener('resize', resize);
    };
  }, [anchor]);
  useLayoutEffect(() => {
    const pop = panel.current!, rect = anchor.trigger.getBoundingClientRect();
    const above = placement === 'above', left = anchor.point?.x ?? (above ? rect.left : rect.right - pop.offsetWidth);
    const top = anchor.point?.y ?? (above ? rect.top - pop.offsetHeight - 5 : rect.bottom + 5);
    pop.style.left = `${Math.max(8, Math.min(left, window.innerWidth - pop.offsetWidth - 8))}px`;
    pop.style.top = `${Math.max(8, Math.min(top, window.innerHeight - pop.offsetHeight - 8))}px`;
  });
  useLayoutEffect(() => { panel.current?.querySelector<HTMLElement>('button:not(:disabled)')?.focus({ preventScroll: true }); }, [role, label, anchor]);

  return <div ref={panel} id={id} className={`ft-file-popover${role === 'dialog' ? ' is-info' : ''}${placement === 'above' ? ' is-append' : ''}`}
    popover="manual" role={role} aria-label={headingId ? undefined : label} aria-labelledby={headingId} aria-describedby={descriptionId} onKeyDown={event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose(); }
      else if (role === 'menu' && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const controls = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
        const index = controls.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? controls.length - 1
          : (index + (event.key === 'ArrowUp' ? -1 : 1) + controls.length) % controls.length;
        controls[next]?.focus();
      } else if (role === 'menu' && event.key === 'Tab') onClose();
    }}>{children}</div>;
}

export function ImportAppendControl({ disabled, reading, onFiles, onDirectory }: {
  disabled: boolean; reading: boolean; onFiles: () => void; onDirectory?: () => void;
}) {
  const [anchor, setAnchor] = useState<ImportMenuAnchor>();
  const menuId = useId();
  const close = (restoreFocus = true) => {
    setAnchor(undefined);
    if (restoreFocus) anchor?.trigger.focus({ preventScroll: true });
  };
  const choose = (openPicker: () => void) => { close(); openPicker(); };
  useEffect(() => { if (disabled) setAnchor(undefined); }, [disabled]);

  return <>
    <button type="button" className="button secondary" disabled={disabled} aria-haspopup={onDirectory ? 'menu' : undefined}
      aria-expanded={onDirectory ? !!anchor : undefined} aria-controls={anchor ? menuId : undefined}
      onClick={event => { if (!onDirectory) onFiles(); else if (anchor) close(); else setAnchor({ trigger: event.currentTarget }); }}
      onKeyDown={event => {
        if (onDirectory && event.key === 'ArrowDown') { event.preventDefault(); setAnchor({ trigger: event.currentTarget }); }
      }}>
      <ImportIcon name={reading ? 'import-loader' : 'file-plus'} className={reading ? 'is-spinning' : ''} />
      {reading ? '正在读取…' : '追加导入'}{onDirectory && <ImportIcon name="chevron-up" className="ft-append-chevron" />}
    </button>
    {!disabled && anchor && onDirectory && <ImportActionPopover id={menuId} anchor={anchor} label="追加导入方式" placement="above" onClose={close}>
      <button type="button" role="menuitem" tabIndex={-1} onClick={() => choose(onFiles)}><ImportIcon name="file-plus" />选择文件…</button>
      <button type="button" role="menuitem" tabIndex={-1} onClick={() => choose(onDirectory)}><ImportIcon name="folder-plus" />选择文件夹…</button>
    </ImportActionPopover>}
  </>;
}

export function ImportFileMenu({ id, anchor, node, original, physicalFile, item, client, actions, scopeSignal, writeBusy, fileActionBusy,
  onClose, onView, onLocate, onRemove, onQuery, onWithdraw, onFileAction, documents }: {
  id: string; anchor: ImportMenuAnchor; node: ImportTreeNode; original: ImportTreeNode; physicalFile: ImportTreeNode;
  item: ImportItem; client: FundsCaseClientV1; actions?: FundsHostActionsV1; scopeSignal: AbortSignal; writeBusy: boolean; fileActionBusy: boolean;
  onClose: (restoreFocus?: boolean) => void; onView: () => void; onLocate: () => void; onRemove: () => void;
  onQuery: () => void; onWithdraw: (batchId: string) => Promise<void>;
  onFileAction: (action: ImportFileAction, sourceId: string, filename: string) => void; documents: Record<string, unknown>[];
}) {
  const port = useFeedback();
  const lifetime = useRef<AbortController | null>(null);
  const [mode, setMode] = useState<'menu' | 'info' | 'withdraw'>('menu');
  const [metadata, setMetadata] = useState<ImportSourceMetadata>();
  const [metadataProblem, setMetadataProblem] = useState('');
  const [failure, setFailure] = useState('');
  const [deleting, setDeleting] = useState(false);
  const result = item.phase === 'done' ? item.job?.result : undefined, sourceId = result?.source_id;
  const isOriginal = node.id === original.id;
  const isArchive = item.directory || original.kind === 'archive';
  const member = !isOriginal && node.kind === 'file' && node.memberPaths.length === 1 ? (result?.table_summary ?? result?.feedback)?.members.find(
    value => value.member_path === node.memberPaths[0] && !['xls', 'xlsx'].includes(value.encoding)) : undefined;
  const sourceName = metadata?.filename || (item.directory ? `${item.filename}.zip` : item.filename);
  const sourceAvailable = metadata?.source_available ?? item.job?.source_available ?? item.resume?.source_available ?? false;
  const nativeCopy = !!sourceId && !!actions?.sourceCopy && !isArchive && /\.(csv|tsv|txt|xls|xlsx|pdf)$/i.test(sourceName);
  const withdrawn = item.job?.withdrawn || metadata?.withdrawn;
  const originalKind = original.kind === 'folder' ? '文件夹' : isArchive ? '压缩包' : '文件';
  const canWithdraw = !withdrawn && !!client.withdrawImportSource && !!metadata?.active_batch_id
    && metadata.active_batch_id === result?.batch_id;
  const canRemove = isOriginal && !item.queryingOnly && (['queued', 'ready', 'failed', 'skipped'].includes(item.phase) || item.phase === 'done' && item.job?.withdrawn);
  const loadingMetadata = !!sourceId && !!client.importSourceMetadata && !metadata && !metadataProblem;

  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller;
    const abort = () => controller.abort();
    scopeSignal.addEventListener('abort', abort, { once: true });
    if (scopeSignal.aborted) controller.abort();
    if (sourceId && client.importSourceMetadata && !controller.signal.aborted) {
      void client.importSourceMetadata(sourceId, controller.signal).then(value => {
        if (controller.signal.aborted) return;
        if (value.case_id !== client.binding.caseId || value.source_id !== sourceId) throw new Error('原件信息与当前案件不一致。');
        setMetadata(value);
      }).catch(error => { if (!controller.signal.aborted) setMetadataProblem((error as Error).message); });
    }
    return () => { scopeSignal.removeEventListener('abort', abort); controller.abort(); };
  }, [client, scopeSignal, sourceId]);

  const copy = (value: string, label: string) => {
    void (async () => {
      try {
        await navigator.clipboard.writeText(value);
        if (!scopeSignal.aborted) port.notify({ operation: 'import', severity: 'success', title: `已复制${label}` });
      } catch {
        if (!scopeSignal.aborted) port.notify({ operation: 'import', severity: 'error', title: `未能复制${label}` });
      }
    })();
  };
  const fileAction = (kind: ImportFileAction) => { if (sourceId) onFileAction(kind, sourceId, sourceName); };
  const confirm = async () => {
    if (!canWithdraw || writeBusy || deleting || !metadata?.active_batch_id) return;
    const signal = lifetime.current?.signal;
    setFailure(''); setDeleting(true);
    try {
      await onWithdraw(metadata.active_batch_id);
      if (!signal?.aborted) onClose();
    } catch (error) {
      if (!signal?.aborted) setFailure((error as Error).message);
    } finally {
      if (!signal?.aborted) setDeleting(false);
    }
  };
  const deleteCommand: Command | undefined = canWithdraw ? {
    label: isOriginal ? `删除${originalKind}…` : `删除所属${originalKind}…`,
    icon: 'trash', disabled: writeBusy, danger: true, stay: true, run: () => setMode('withdraw'),
  } : undefined;
  const current: Command[] = [
    ...(anchor.branch ? [{ label: anchor.branch.open ? '收起成员' : '展开成员', icon: 'folder', run: anchor.branch.toggle }] : []),
    ...(!anchor.branch || !isOriginal ? [{ label: node.kind === 'sheet' ? '查看工作表' : '查看数据预览', icon: 'eye', run: onView }] : []),
    ...(item.phase === 'unknown' && item.importId ? [{ label: '查询原任务进度', icon: 'import-clock', run: onQuery }] : []),
    { label: node.kind === 'sheet' ? '复制表名' : node.kind === 'folder' ? '复制文件夹名称' : '复制文件名', icon: 'copy',
      run: () => copy(node.name, node.kind === 'sheet' ? '表名' : '名称') },
    ...(node.path ? [{ label: '复制资料内路径', icon: 'link', run: () => copy(node.path, '资料内路径') }] : []),
    ...(member?.sha256 ? [{ label: '复制成员 SHA-256', icon: 'hash', run: () => copy(member.sha256, '成员 SHA-256') }] : []),
  ];
  const file: Command[] = [
    ...(!isOriginal ? [{ label: '查看所属原件', icon: 'import-file-type-generic', run: onLocate }] : []),
    ...(nativeCopy ? [
      { label: node.kind === 'sheet' ? '打开所属工作簿副本' : '打开原件副本', icon: 'external-open', disabled: !sourceAvailable || fileActionBusy, run: () => fileAction('open') },
      { label: '显示副本位置', icon: 'folder', disabled: !sourceAvailable || fileActionBusy, run: () => fileAction('reveal') },
    ] : []),
    ...(sourceId && actions?.downloadSource ? [{
      label: item.directory ? '另存目录留存 ZIP…' : isArchive ? isOriginal ? '另存压缩包…' : '另存所属压缩包…' : '另存原件…',
      icon: 'download', disabled: !sourceAvailable || fileActionBusy, run: () => fileAction('download'),
    }] : []),
    ...(!isOriginal ? [{ label: '复制原件文件名', icon: 'copy', run: () => copy(original.name, '原件文件名') }] : []),
    ...(metadata?.sha256 ? [{ label: '复制原件 SHA-256', icon: 'hash', run: () => copy(metadata.sha256, '原件 SHA-256') }] : []),
    ...(!isOriginal && deleteCommand ? [deleteCommand] : []),
  ];
  const danger: Command[] = [];
  if (canRemove) danger.push({ label: item.phase === 'queued' ? '移出待导入项' : '从列表移除', icon: 'trash', run: onRemove, danger: true });
  if (isOriginal && deleteCommand) danger.push(deleteCommand);
  const show = (command: Command) => <button key={command.label} type="button" role="menuitem" tabIndex={-1} data-danger={command.danger || undefined}
    disabled={command.disabled} onClick={() => { if (!command.stay) onClose(); command.run(); }}><ImportIcon name={command.icon} />{command.label}</button>;
  const field = (label: string, value: ReactNode) => <><dt>{label}</dt><dd>{value}</dd></>;
  const total = (key: string) => documents.length && documents.every(document => typeof document[key] === 'number')
    ? documents.reduce((sum, document) => sum + (document[key] as number), 0).toLocaleString('zh-CN') : '—';
  const size = formatFileSize(isOriginal ? metadata?.size_bytes ?? original.sizeBytes : physicalFile.sizeBytes);
  const status = withdrawn ? '已撤回' : importState(item, node).label;
  const scope = !isOriginal ? '所属' : isArchive ? '整个' : '';
  const title = `删除${scope}${originalKind}的导入？`;
  const titleId = `${id}-title`, targetId = `${id}-target`, impactId = `${id}-impact`;

  return <ImportActionPopover id={id} anchor={anchor} role={mode === 'menu' ? 'menu' : 'dialog'}
    label={mode === 'menu' ? '文件操作' : mode === 'info' ? '文件信息' : title} onClose={onClose}
    headingId={mode === 'withdraw' ? titleId : undefined} descriptionId={mode === 'withdraw' ? `${targetId} ${impactId}` : undefined}>
    {mode === 'menu' ? <>
      <div className="ft-menu-caption" aria-hidden="true" title={node.name}>{node.name}</div>
      <div role="group" aria-label="当前节点">{!isOriginal && <div className="ft-menu-scope" aria-hidden="true">当前节点</div>}{current.map(show)}</div>
      {file.length > 0 && <><div className="ft-menu-divider" role="separator" /><div role="group" aria-label="所属原件">
        {!isOriginal && <div className="ft-menu-scope" aria-hidden="true">所属原件</div>}{file.map(show)}
      </div></>}
      <div className="ft-menu-divider" role="separator" />
      {show({ label: '文件信息', icon: 'info-circle', stay: true, run: () => setMode('info') })}
      {loadingMetadata && <div className="ft-menu-scope" role="status">正在读取原件登记信息…</div>}
      {danger.length > 0 && <><div className="ft-menu-divider" role="separator" />{danger.map(show)}</>}
    </> : mode === 'info' ? <>
      <button type="button" className="ft-menu-back" onClick={() => setMode('menu')}><ImportIcon name="chevron-left" />文件信息</button>
      <dl className="ft-file-information">
        {field(node.kind === 'sheet' ? '工作表' : '名称', node.name)}
        {field('处理状态', status)}
        {node.path && field('资料内路径', node.path)}
        {node.kind === 'sheet' && physicalFile.kind === 'file' && field('范围', '整份文件')}
        {field(item.directory && isOriginal && !metadata ? '所选文件合计' : '文件大小', size)}
        {field('处理用时（本次）', formatImportDuration(isOriginal && ['done', 'failed'].includes(item.phase) ? item.processingMs : undefined))}
        {field('原始行数', total('source_rows'))}
        {field('数据记录', physicalFile.dataRows?.toLocaleString('zh-CN') ?? total('row_count'))}
        {member?.sha256 && field('成员 SHA-256', member.sha256)}
        {metadata && <>
          {!isOriginal && field('所属原件', metadata.filename)}
          {metadata.material_name && field('检材编号', metadata.material_name)}
          {field('原件 SHA-256', metadata.sha256)}
          {field('登记时间', displayOperationalTime(metadata.registered_at))}
          {field('原件留存', metadata.source_available ? '已登记，可读取' : '原件暂不可读取')}
        </>}
      </dl>
      {loadingMetadata && <p className="ft-menu-scope" role="status">正在读取原件登记信息…</p>}
      {(metadataProblem || item.error) && <p className="ft-menu-scope" role="status">{metadataProblem || item.error}</p>}
    </> : <div className="ft-withdraw-content">
      <h3 id={titleId}>{title}</h3>
      <div id={targetId} className="ft-withdraw-target">
        <strong>{original.name}</strong>
        {metadata?.material_name && <small>{metadata.material_name}</small>}
      </div>
      <p id={impactId} className="ft-withdraw-impact">
        {!isOriginal && isArchive && '包含全部成员。'}原件、历史回执和有其他来源支持的流水会保留。
      </p>
      {failure && <p role="alert">{failure}</p>}
      <div className="ft-withdraw-actions">
        <button type="button" onClick={() => onClose()} disabled={deleting}>取消</button>
        <button type="button" data-danger onClick={() => void confirm()} disabled={writeBusy || deleting || !canWithdraw}>{deleting ? '正在删除…' : '删除导入'}</button>
      </div>
    </div>}
  </ImportActionPopover>;
}
