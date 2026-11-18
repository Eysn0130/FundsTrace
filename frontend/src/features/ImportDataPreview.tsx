import type { PreviewFlow } from '../../../contracts/api-v1';
import { displayTime, formatMoney } from '../lib/format';
import { ImportCellValue, ImportTable } from './ImportTable';

export const previewHeadings = ['行号','交易时间','交易类型','交易对手','付款户名','付款账号','收款户名','收款账号','金额','币种','余额','摘要','流水号','付款开户行','收款开户行','交易类型/渠道','本方账号','本方户名'];
export const previewWidths = [96,238,130,208,208,220,208,220,220,90,220,240,220,200,200,150,220,200];
export const previewMoneyColumns = [8, 10];

export function ImportDataPreview({ rows, filename, heading = '数据预览', note, ariaLabel = '数据预览，可上下左右滚动' }: {
  rows: PreviewFlow[]; filename: string; feedback?: boolean; heading?: string; note?: string; ariaLabel?: string;
}) {
  const sourceRows = rows.flatMap(row => row.source_row != null && row.source_row > 0 ? [row.source_row] : []);
  const range = sourceRows.length ? `（第 ${Math.min(...sourceRows)}–${Math.max(...sourceRows)} 行）` : '';
  return <section className="ft-preview-section"><div className="ft-section-heading"><h4>{heading}{range}</h4>{note && <span>{note}</span>}</div>
    <ImportTable autoSize className="ft-preview-scroll" label={ariaLabel} headings={previewHeadings} widths={previewWidths} moneyColumns={previewMoneyColumns}><tbody>{rows.map((row, index) => <tr key={index}>{previewCells(row).map((value,column)=><td key={column} className={previewMoneyColumns.includes(column) ? 'ft-money' : undefined} title={column===0 ? `${row.source_member || filename} · 原始第 ${row.source_row ?? '—'} 行${row.source_record_no != null ? ` · 记录 ${row.source_record_no}` : ''}` : undefined}><ImportCellValue>{value}</ImportCellValue></td>)}</tr>)}</tbody></ImportTable>
  </section>;
}

export function previewCells(row: PreviewFlow) {
  const outgoing = !!row.self_account && row.self_account === row.payer_account && row.self_account !== row.payee_account;
  const incoming = !!row.self_account && row.self_account === row.payee_account && row.self_account !== row.payer_account;
  const counterparty = outgoing ? row.payee_name || row.payee_account : incoming ? row.payer_name || row.payer_account : '';
  return [row.source_row ?? '—', displayTime(row.trade_time), outgoing ? '转出' : incoming ? '转入' : '—', counterparty || '—',
    row.payer_name,row.payer_account,row.payee_name,row.payee_account,formatMoney(row.amount),row.currency,
    row.balance == null ? '—' : formatMoney(row.balance),row.summary,row.trade_no,row.payer_bank,row.payee_bank,row.channel,row.self_account,row.self_name];
}
