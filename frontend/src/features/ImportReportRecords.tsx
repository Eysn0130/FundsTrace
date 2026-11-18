import { useEffect, useState } from 'react';
import type { ImportReportRecords } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsReportRecordsQueryV1 } from '../panel/host';
import { linkAbortSignal } from '../panel/cancellation';
import { previewCells, previewHeadings, previewMoneyColumns, previewWidths } from './ImportDataPreview';
import { ImportCellValue, ImportTable } from './ImportTable';
import { ImportIcon } from './ImportTree';

export function ImportReportRecords({ client, scopeSignal, sourceId, query, label, onClose }: {
  client: FundsCaseClientV1; scopeSignal: AbortSignal; sourceId: string; query: FundsReportRecordsQueryV1; label: string; onClose: () => void;
}) {
  const [page, setPage] = useState(1), [reload, setReload] = useState(0);
  const [snapshot, setSnapshot] = useState<{ key: string; client: FundsCaseClientV1; data: ImportReportRecords }>(), [failure, setFailure] = useState<{ key: string; message: string }>();
  const key = JSON.stringify({ sourceId, ...query, page });
  const data = snapshot?.key === key && snapshot.client === client && !scopeSignal.aborted ? snapshot.data : undefined;
  const error = failure?.key === key ? failure.message : '';
  useEffect(() => {
    const controller = new AbortController(), unlink = linkAbortSignal(scopeSignal, controller); let active = true;
    client.importReportRecords!(sourceId, { ...query, page }, controller.signal).then(value => {
      if (!active || controller.signal.aborted) return;
      if (value.case_id !== client.binding.caseId || value.source_id !== sourceId || value.analysis_revision !== query.expectedRevision || value.items.some(row => row.member_path !== query.memberPath)) throw new Error('来源明细已变化，请刷新报告');
      setSnapshot({ key, client, data: value }); setFailure(undefined);
    }).catch(reason => { if (active && !controller.signal.aborted) setFailure({ key, message: reason.message }); });
    return () => { active = false; unlink(); controller.abort(); };
  }, [client, scopeSignal, key, reload]);
  if (scopeSignal.aborted) return null;
  return <section className="ft-report-drilldown" aria-label="统计来源明细">
    <div className="ft-section-heading"><h4>{label}</h4><button type="button" className="icon-button" aria-label="关闭统计来源明细" onClick={onClose}><ImportIcon name="x" /></button></div>
    <p className="ft-report-meta">本文件来源记录 · {data?.total ?? '—'} 条 · 同笔的来源记录逐条保留</p>
    <ImportTable fillToSlot autoSize className="ft-report-grid ft-report-records-grid" label="统计来源明细，可上下左右滚动" headings={previewHeadings} widths={previewWidths} moneyColumns={previewMoneyColumns}><tbody>
      {!data && <tr><td colSpan={previewHeadings.length} role={error ? 'alert' : 'status'}>{error || '正在读取来源明细…'}{error && <button type="button" className="ft-text-button" onClick={() => setReload(value => value + 1)}>重试</button>}</td></tr>}
      {data?.items.map(row => <tr key={row.record_no}>{row.preview ? previewCells({ ...row.preview, source_row: row.source_row }).map((value, index) => <td key={index} className={previewMoneyColumns.includes(index) ? 'ft-money' : undefined}><ImportCellValue>{value}</ImportCellValue></td>) : <><td>{row.source_row}</td><td colSpan={previewHeadings.length - 1}>当前记录预览不可用</td></>}</tr>)}
      {data && !data.items.length && <tr><td colSpan={previewHeadings.length}>当前统计条件下没有来源记录</td></tr>}
    </tbody></ImportTable>
    {data && data.total > data.page_size && <div className="ft-report-pagination"><button className="ft-text-button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span>{data.page} / {Math.ceil(data.total / data.page_size)}</span><button className="ft-text-button" disabled={page * data.page_size >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button></div>}
  </section>;
}
