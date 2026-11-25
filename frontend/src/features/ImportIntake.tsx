import { useEffect, useRef, useState } from 'react';
import type { IntakeMaterial, IntakeReceipt } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { feedbackReason, ImportRecordEvidence } from './ImportDetails';

const roles: Record<string, string> = {
  transaction: '交易流水', account_opening: '开户资料', account: '账户资料',
  account_register: '账户登记', registration: '登记资料', address: '地址资料',
  ledger_registration: '有效期账户登记',
  settlement_bridge: '清算对应表', settlement_scope: '清算范围说明', event_reference_scope: '跨账参考范围说明',
  description: '字段说明', notes: '说明', pdf: 'PDF', pdf_observation: 'PDF 来源观察', attachment: '附件', unknown: '未识别材料',
};
const states: Record<string, string> = {
  imported: '已入库', registered: '已登记', ready: '可提交', review: '待核', partial: '部分待核',
  unreadable: '材料无法读取', withdrawn: '批次已撤回', error: '处理失败',
};
const actions: Record<string, string> = {
  review_rows: '查看原行处理结果', review_page: '核对原件页', review_original: '查看原件',
  replace_original: '需补原件', replace_or_retry: '可换原件后重试', retry_import: '可重试导入',
  confirm_import: '可提交导入', confirm_registration: '可提交登记',
};
const reasons: Record<string, string> = {
  pdf_tool_unavailable: '现有提取未完成，未启用 OCR 复核',
  pdf_candidate_only: '需要完整账簿依据',
  pdf_ocr_failed: '文字提取失败', pdf_ocr_timeout: '文字提取超时', pdf_invalid: 'PDF 原件无法读取',
  pdf_book_scope_unproven: '原页观察可回查，账簿归属或余额口径待核',
  source_role_conflict: '材料列用途存在冲突',
};
const materialKey = (item: IntakeMaterial) => JSON.stringify([item.member_path, item.sheet, item.page, item.header_row, item.source_row]);

export function ImportIntake({ client, scopeSignal, sourceId, memberPaths, blocked = false }: {
  client: FundsCaseClientV1; scopeSignal: AbortSignal; sourceId: string; memberPaths?: readonly string[]; blocked?: boolean;
}) {
  const [snapshot, setSnapshot] = useState<{client:FundsCaseClientV1;receipt:IntakeReceipt}>();
  const receipt = !scopeSignal.aborted && snapshot?.client === client && snapshot.receipt.source_id === sourceId
    ? snapshot.receipt : undefined;
  const [error, setError] = useState('');
  const [openPage, setOpenPage] = useState<{ key: string; number: number; url: string }>();
  const [openRecords, setOpenRecords] = useState<string>();
  const [busy, setBusy] = useState(false);
  const action = useRef<AbortController | null>(null);
  useEffect(() => {
    if (!client.importIntake) return;
    const controller = new AbortController();
    void client.importIntake(sourceId, controller.signal).then(value => {
      if (!controller.signal.aborted && !scopeSignal.aborted && value.source_id === sourceId
        && value.case_id === client.binding.caseId) setSnapshot({client, receipt:value});
    }, cause => {
      if (!controller.signal.aborted && !scopeSignal.aborted)
        setError(cause instanceof Error ? cause.message : '材料记录暂无法读取。');
    });
    return () => { controller.abort(); action.current?.abort(); };
  }, [client, scopeSignal, sourceId]);

  async function run(item: IntakeMaterial, showPage: boolean) {
    if (scopeSignal.aborted || action.current) return;
    const controller = new AbortController();
    action.current = controller;
    setBusy(true); setError('');
    const current = () => !controller.signal.aborted && !scopeSignal.aborted;
    try {
      const page = item.page || 1;
      if (showPage && client.importIntakePage) {
        setOpenPage(undefined);
        const url = await client.importIntakePage(sourceId, item.member_path, page, controller.signal);
        if (current()) {
          setOpenPage({ key: materialKey(item), number: page, url });
          if (item.records_url && client.importRecords) setOpenRecords(materialKey(item));
        }
      } else if (client.downloadIntakeMember) {
        await client.downloadIntakeMember(sourceId, item.member_path, item.member_path.split('/').pop() || item.member_path, controller.signal);
      }
    } catch (cause) {
      if (current()) setError(cause instanceof Error ? cause.message : '原件暂无法读取。');
    } finally {
      if (current()) { setBusy(false); action.current = null; }
    }
  }

  const shown = receipt?.items.filter(item => !memberPaths?.length || memberPaths.includes(item.member_path)) || [];
  const fieldNotes = receipt?.source_field_notes?.filter(note => !memberPaths?.length || memberPaths.includes(note.member_path));
  return <section aria-label="材料分类与原件" className="ft-mapping-section">
    {!!fieldNotes?.length && <ul aria-label="原表字段核对">{fieldNotes.map((note, index) => {
      const rows = note.rows ?? [];
      return <li key={`${note.member_path}:${note.header_row}:${index}`}>{note.text}
        {' '}表头第 {note.header_row} 行{note.columns.length > 0 && <>、第 {note.columns.join('、')} 列</>}
        {!!rows.length && <>，样值第 {rows.slice(0, 6).join('、')} 行{rows.length > 6 && `等 ${rows.length} 行`}</>}。</li>;
    })}</ul>}
    <details>
      <summary>材料分类与原件</summary>
      <p role="status">{busy ? '正在读取原件…' : error || (!receipt ? '正在读取材料分类…' : '')}</p>
      {receipt && !shown.length && <p>当前选项没有材料分类记录。</p>}
      {shown.length > 0 && <ul>{shown.map(item => {
        const key = materialKey(item), page = openPage?.key === key ? openPage : undefined;
        const pdf = item.member_path.toLowerCase().endsWith('.pdf');
        const conflict = blocked && item.status === 'ready';
        return <li key={key}>
          <strong>{item.sheet || item.member_path}</strong> · {roles[item.role] ?? item.role} · {conflict ? '字段冲突，待核' : states[item.status] ?? item.status}
          {item.page != null && <> · 第 {item.page} 页</>}
          {item.header_row != null && <> · 表头第 {item.header_row} 行{item.last_row != null && `，至第 ${item.last_row} 行`}</>}
          <p>{item.accepted > 0 && <>{receipt?.stage === 'done' ? '已办理流水' : '预览候选流水'} {item.accepted} 条。 </>}
            {item.auxiliary > 0 && <>辅助资料 {item.auxiliary} 项。 </>}
            {item.unresolved > 0 && <>待核 {item.unresolved} 项。 </>}
            {item.reason && (reasons[item.reason] || feedbackReason(item.reason))} {conflict ? '需核对原列用途' : actions[item.next_action]}</p>
          {item.original_url && client.downloadIntakeMember && <button type="button" className="button secondary" onClick={() => void run(item, false)} disabled={busy}>下载成员原件</button>}
          {item.original_url && client.importIntakePage && pdf && <button type="button" className="button secondary" onClick={() => void run(item, true)} disabled={busy}>查看 PDF 原页</button>}
          {item.records_url && client.importRecords && <button type="button" className="button secondary" aria-expanded={openRecords === key}
            onClick={() => setOpenRecords(openRecords === key ? undefined : key)}>查看原行处理结果</button>}
          {page && <figure><img src={page.url} alt={`${item.member_path} 第 ${page.number} 页原件`} style={{ maxWidth: '100%' }} />
            <figcaption>原件第 {page.number} 页</figcaption></figure>}
          {openRecords === key && <ImportRecordEvidence key={key} compact={!pdf} openPreview pdfPage={item.page ?? undefined}
            caseId={client.binding.caseId} sourceId={sourceId} memberPath={item.member_path} client={client} scopeSignal={scopeSignal} />}
        </li>;
      })}</ul>}
    </details>
  </section>;
}
