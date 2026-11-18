import { useEffect, useId, useRef, useState } from 'react';
import type { ImportReport, ImportReportAccounts } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { linkAbortSignal } from '../panel/cancellation';
import { ImportIcon } from './ImportTree';

/** Uses bounded account pages; selecting a peer scope never changes the account table page. */
export function ImportReportAccountFilter({ data, client, sourceId, scopeSignal, selected, onChange }: {
  data: ImportReport; client: FundsCaseClientV1; sourceId: string; scopeSignal: AbortSignal;
  selected?: string; onChange: (account?: string) => void;
}) {
  const [open, setOpen] = useState(false), [page, setPage] = useState(1);
  const [snapshot, setSnapshot] = useState<{ page: number; value: ImportReportAccounts }>(), [failure, setFailure] = useState('');
  const root = useRef<HTMLSpanElement>(null), trigger = useRef<HTMLButtonElement>(null), id = useId();
  const options = data.accounts.page === page ? data.accounts : snapshot?.page === page ? snapshot.value : undefined;
  const account = data.peer_selected_account || data.accounts.items.find(row => row.account === selected);
  const label = selected ? account?.account_number || '所选账户' : '全部';
  const fullLabel = selected ? `${account?.names.join('、') || '户名未登记'} · ${account?.account_number || selected}` : '全部（当前文件、币种）';
  useEffect(() => {
    if (!open || data.accounts.page === page) return;
    const controller = new AbortController(), unlink = linkAbortSignal(scopeSignal, controller);
    setSnapshot(undefined); setFailure('');
    client.importFundsReport!(sourceId, { memberPath: data.member_path, ...(data.currency !== null ? { currency: data.currency } : {}), expectedRevision: data.analysis_revision, accountsPage: page }, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.case_id !== client.binding.caseId || value.source_id !== sourceId || value.member_path !== data.member_path || value.analysis_revision !== data.analysis_revision) throw new Error('账户列表范围已变更，请重新打开');
      setSnapshot({ page, value: value.accounts });
    }).catch(error => { if (!controller.signal.aborted) setFailure(error.message); });
    return () => { unlink(); controller.abort(); };
  }, [open, page, data, client, sourceId, scopeSignal]);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"][aria-checked="true"]')?.focus();
    const outside = (event: PointerEvent) => { if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);
  const close = () => { setOpen(false); trigger.current?.focus(); };
  const choose = (value?: string) => { close(); onChange(value); };
  const pages = Math.max(1, Math.ceil(data.accounts.total / data.accounts.page_size));
  return <span className="ft-report-account-filter" ref={root} onKeyDown={event => {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); close(); }
    if (!open && ['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); setOpen(true); return; }
    if (!open || !['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const items = [...(root.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]:not(:disabled)') || [])], index = items.indexOf(document.activeElement as HTMLButtonElement);
    items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length]?.focus();
  }}>
    <button type="button" ref={trigger} className="ft-report-filter-button" aria-label={`主要往来本方账户：${label}`} title={fullLabel} aria-haspopup="menu" aria-expanded={open} aria-controls={id} onClick={() => setOpen(value => !value)}><span>{label}</span><ImportIcon name="chevron-down" /></button>
    {open && <span className="ft-report-filter-menu" id={id} role="menu" aria-label="主要往来账户范围">
      <button type="button" role="menuitemradio" aria-checked={!selected} onClick={() => choose()}>全部</button>
      {options?.items.map(row => <button key={row.account} type="button" role="menuitemradio" aria-checked={selected === row.account} title={`${row.names.join('、') || '户名未登记'} · ${row.account_number}`} onClick={() => choose(row.account)}><span>{row.names.join('、') || '户名未登记'}</span><small>{row.account_number}</small></button>)}
      {!options && <span className="ft-report-filter-status" role={failure ? 'alert' : 'status'}>{failure || '正在读取账户…'}</span>}
      {pages > 1 && <span className="ft-report-filter-pages"><button type="button" disabled={page <= 1} aria-label="上一页账户选项" onClick={() => setPage(value => value - 1)}><ImportIcon name="chevron-left"/></button><span>{page} / {pages}</span><button type="button" disabled={page >= pages} aria-label="下一页账户选项" onClick={() => setPage(value => value + 1)}><ImportIcon name="chevron-right"/></button></span>}
    </span>}
  </span>;
}
