import { useEffect, useRef, useState } from 'react';
import type { ImportPreview, ImportRecordPage, PreviewFlow, TableSummary as TableSummaryData } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { formatMoney } from '../lib/format';
import { linkAbortSignal } from '../panel/cancellation';
import { SourceFieldEvidence } from './SourceFieldEvidence';
import { ImportDataPreview } from './ImportDataPreview';

import { fieldNames } from './import-fields';
const feedbackReasons: Record<string, string> = {
  unsupported_attachment: '附件原件已保留，格式未适配，内部记录数量与资金内容尚未解释', non_business_attachment: 'Finder元数据附件，原件保留，不生成资金流水',
    transaction_valid: '按来源方向与金额识别为资金流水', auxiliary_table: '辅助资料，未作为资金流水', feedback_only: '来源反馈，未包含资金流水',
  negative_reversal_text: '负数与冲正描述缺少一致的来源依据', negative_revocation_text: '负数与撤销描述缺少一致的来源依据',
  negative_reversal_revocation_text: '负数与冲正及撤销描述缺少一致的来源依据', negative_unknown: '来源未明确负号含义',
  missing_core: '缺少形成资金流水必需的信息', invalid_amount: '交易金额格式无效', amount_precision: '交易金额超过分精度',
  amount_overflow: '交易金额超出支持范围', zero_amount: '零金额，无资金变动', invalid_direction: '收付方向无法识别',
  unsupported_time_role: '只有记账、受理等其他时间，未提供交易时间；未计入资金统计和资金图',
  unsupported_timezone: '显式偏移和UTC瞬间已保留；尚未进入资金统计、资金图或跨无区时间排序',
  invalid_time: '交易时间无效', invalid_currency: '币种无法识别', unknown_status: '交易状态含义缺少适用的来源依据',
  invalid_balance: '交易余额格式无效', balance_precision: '交易余额超过分精度', balance_overflow: '交易余额超出支持范围',
  invalid_cell_type: '单元格类型无法识别',
  reversal_text_requires_review: '历史冲正或撤销记录缺少可确定资金效果的依据',
  own_identity_conflict: '同一来源记录的本方身份资料冲突，已保留且未重复计入金额',
  matched_cancellation: '已配对冲正/撤销，双方均不计有效资金',
  mapping_unknown: '关键字段尚未识别，未推定资金事实', mapping_conflict: '关键字段存在多个冲突解释',
  ambiguous_mapping: '关键字段无法唯一映射', missing_mapping: '缺少交易必需字段映射',
  amount_conflict: '金额字段相互冲突', amount_unit_conflict: '金额单位相互冲突', unknown_amount_unit: '金额单位无法确定',
  conflicting_unit: '金额单位声明相互矛盾',
  balance_amount_conflict: '本笔交易前后账面余额差与金额不一致',
  balance_direction_conflict: '本笔交易前后账面余额变化与收支方向矛盾',
  direction_conflict: '方向字段与金额含义冲突', ambiguous_direction: '资金方向无法唯一确定',
  ambiguous_date: '日期格式存在多种解释', field_conflict: '来源字段相互冲突',
  invalid_row_width: '记录列数与表头不一致', repeated_header: '重复表头，未作为交易',
  non_transaction: '说明或汇总记录，未作为交易', unsupported_document: '来源文件格式尚未适配',
  unknown_table: '表格结构尚不能唯一识别', missing_time: '缺少形成资金流水必需的交易时间', conflicting_time: '多个时间字段不一致',
  missing_amount: '缺少形成资金流水必需的交易金额', conflicting_amount: '多个金额字段换算后不一致', unknown_unit: '金额单位不能唯一确定',
  negative_amount: '来源未明确负号含义', missing_direction: '缺少可确定的收支方向',
  conflicting_direction: '方向与金额或收支分列矛盾', conflicting_income_expense: '收入与支出同时非零',
  missing_identity: '缺少必要的本方或双边身份', conflicting_field: '同一字段的多个候选列不一致',
  row_width: '来源记录列数与表头不同', column_count_mismatch: '来源记录列数与表头不同',
  blank_record: '空白记录，未作为交易', preamble: '表头前说明，未作为交易',
  failed_transaction: '来源明确标记交易未成功', unsafe_account_number: 'Excel数值账号可能已损失精度',
  currency_unit_conflict: '金额单位标注的币种与币种字段矛盾', unsupported_identity_format: 'Excel数字身份显示格式无法唯一解释',
  formula_value: '关键字段是未执行的公式，未推定计算结果',
  region_mapping_conflict: '当前表格区域的字段无法唯一对应',
  cancellation_structure: '来源结构不足以核实冲回关系', cancellation_missing_reference: '冲回缺少原交易引用',
  cancellation_reference_not_unique: '原交易引用不能唯一配对', cancellation_action_unproven: '缺少明确且无矛盾的冲正或撤销说明',
  cancellation_status_unproven: '冲回交易状态无法确定', cancellation_missing_scope: '缺少同账户同币种的核验依据',
  cancellation_original_invalid: '原交易未通过完整校验', cancellation_time_ambiguous: '交易先后关系不能唯一确定',
  cancellation_unlocated_record: '账户时间序列中存在无法定位的记录', cancellation_missing_balance: '缺少完整余额回补证据',
  cancellation_amount_mismatch: '原交易与冲回金额不匹配', cancellation_direction_mismatch: '原交易与冲回的记账方向不一致',
  cancellation_identity_conflict: '原交易与冲回身份字段存在冲突', cancellation_witness_untrusted: '前置余额缺少可靠交易依据',
  cancellation_balance_mismatch: '余额变化不能核实完整冲回', cancellation_validation_failed: '冲回证据未通过一致性校验',
  cancellation_historical_conflict: '冲回证据与本案历史流水冲突，未自动排除',
};
export const feedbackReason = (reason: string) => feedbackReasons[reason] || '其他自动分类结果，详见完整处理报告';
const feedbackRoles: Record<string, string> = { transaction: '交易明细', account: '账户信息', client: '客户资料', subaccount: '子账户', address: '地址', phone: '联系方式', task: '查询任务', freeze: '冻结信息' };
export const unresolvedCount = (feedback: NonNullable<ImportPreview['feedback']>) => (feedback.unresolved ?? 0) + (feedback.review ?? 0);

const mappingRoleNames: Record<string, string> = { source_slip_reference: '传票号', source_log_reference: '日志号', independent_voucher_type: '凭证种类' };
const mappingBasis: Record<string, string> = { exact_header_alias: '按完整表头别名识别并校验', source_transaction_id: '使用来源交易流水号', independent_voucher_reference: '凭证号为独立引用，不与交易流水号判冲突', independent_voucher_type: '保留来源凭证种类', source_slip_reference: '独立保留来源传票号', source_log_reference: '独立保留来源日志号', summary: '保留来源摘要原文', remark: '独立保留来源备注原文', explicit_time_format: '明确的日期时间格式' };
export const mappingExplanation = (item: Record<string, string>) => [
  item.member_path, `${mappingRoleNames[item.basis] || fieldNames[item.field] || item.field || '字段'} ← ${item.column || '未识别'}`,
  item.time_format ? `时间格式：${item.time_format}` : '',
  item.unit && ['amount', 'balance', 'income_amt', 'expense_amt'].includes(item.field) ? `来源单位：${item.unit}` : '', item.unit_factor && ['amount', 'balance', 'income_amt', 'expense_amt'].includes(item.field) ? `金额单位换算系数：${item.unit_factor}` : '',
  item.candidate_count ? `候选列：${item.candidate_count}` : '', mappingBasis[item.basis] || item.basis,
].filter(Boolean).join(' · ');
const documentExplanation = (item: Record<string, unknown>) => [
  String(item.member_path || '来源表格'), item.sheet ? `工作表：${String(item.sheet)}` : '',
  item.header_row != null ? `表头第 ${String(item.header_row)} 行` : '未能唯一识别表头',
  item.row_count != null ? `来源记录 ${String(item.row_count)} 条` : '',
].filter(Boolean).join(' · ');

export function ResolutionSummary({ value, complete }: { value: NonNullable<ImportPreview['resolution']>; complete: boolean }) {
  if (!(value.unresolved_endpoints || value.conflicts)) return null;
  return <section className="resume-summary" aria-label="账户关联结果">
    <p><strong>{complete ? '账户关联已更新' : '账户资料关联预览'}</strong></p>
    <p>有 {value.unresolved_endpoints} 个账户标识尚未关联，{value.conflicts} 处资料冲突。已知卡号和原始记录继续保留，可在账户详情中核对。</p>
  </section>;
}

export function TableSummary({ summary, complete = false, uncertain = false }: { summary: TableSummaryData; complete?: boolean; uncertain?: boolean }) {
  const evidence = summary.mapping_evidence ?? [], documents = summary.documents ?? [], examples = summary.unresolved_examples ?? [];
  return <section className="resume-summary" aria-label="表格自动识别与全量校验汇总">
    <p><strong>表格自动识别 · 全量校验</strong></p>
    <p>来源记录 {summary.record_total} 条 · 可靠识别流水 {summary.accepted} 条 · 已配对冲正/撤销 {summary.cancelled ?? 0} 条（双方均不计有效资金） · 未计入资金流水 {summary.unresolved} 条 · 无资金变动 {summary.non_monetary} 条 · 非交易记录 {summary.non_transaction} 条 · 辅助资料 {summary.auxiliary} 条</p>
    {(summary.reason_counts.unsupported_attachment ?? 0) > 0 && <p role="note">有 {summary.reason_counts.unsupported_attachment} 份附件格式尚未适配，已保留原件。每份按一个来源条目登记，内部交易数量与金额未知，不能将此结果理解为整个目录已完整解析。</p>}
    <p>{complete ? '自动处理已完成，未计入资金流水的记录已' : uncertain ? '处理结果待核实。未计入资金流水的记录按规则' : '未计入资金流水的记录按规则'}保留来源和原因，无需逐条确认；这些记录不参与金额、交易笔数和关系统计。</p>
    {(summary.cancelled ?? 0) > 0 && <p>已配对记录保留来源和核验依据，双方退出有效资金统计；退款、退货或仅有负号的记录不据此排除。</p>}
    {complete && (summary.excluded_flows ?? 0) > 0 && <p>本案有 {summary.excluded_flows} 条留存来源流水未计入有效统计。</p>}
    {summary.unresolved > 0 && <p role="note">分析仅使用可靠识别的有效流水，可能无法反映完整资金流向、金额和关联关系。未计入不代表金额为零或没有发生交易。</p>}
    {summary.accepted === 0 && <p>文件{complete ? '已处理' : '可保存处理结果'}，暂无可可靠计入的流水。</p>}
    {Object.keys(summary.currency_totals).length > 0 && <><p>可靠识别流水金额合计（按币种，含重复；非本次新增金额或净结算金额）：</p><ul>{Object.entries(summary.currency_totals).map(([currency, amount]) => <li key={currency} className="mono">{formatMoney(amount)} {currency || '未标注币种'}</li>)}</ul></>}
    {evidence.length > 0 && <details open><summary>字段映射、金额单位与方向依据（共 {evidence.length} 项）</summary><ul>{evidence.slice(0, 50).map((item, index) => <li key={index}>{mappingExplanation(item)}</li>)}</ul>{evidence.length > 50 && <p>显示前50项，完整依据见处理报告。</p>}</details>}
    {Object.keys(summary.reason_counts).length > 0 && <details open><summary>全量校验与未计入原因</summary><ul>{Object.entries(summary.reason_counts).map(([reason, count]) => <li key={reason}>{feedbackReason(reason)}：{count} 条</li>)}</ul></details>}
    {examples.length > 0 && <details><summary>未计入记录的来源位置</summary><ul>{examples.map((row, index) => <li key={index}>{row.member_path} · 原始第 {row.source_row} 行 · {feedbackReason(row.reason_code)}<SourceFieldEvidence details={row.details} /></li>)}</ul></details>}
    {documents.length > 0 && <details><summary>已检查的来源文件与工作表（共 {documents.length} 项）</summary><ul>{documents.slice(0, 50).map((item, index) => <li key={index}>{documentExplanation(item)}</li>)}</ul>{documents.length > 50 && <p>显示前50项，完整清单见处理报告。</p>}</details>}
  </section>;
}

export function FeedbackSummary({ feedback, complete = false, uncertain = false }: { feedback: NonNullable<ImportPreview['feedback']>; complete?: boolean; uncertain?: boolean }) {
  const examples = [...(feedback.unresolved_examples ?? []), ...(feedback.review_examples ?? [])].slice(0, 10);
  return <section className="resume-summary" aria-label="银行反馈包识别与处置汇总">
    <p><strong>银行反馈 ZIP · 已自动识别</strong></p><p className="muted">{complete ? '按固定规则自动分类并保存来源与处理结果' : '按固定规则自动分类'}，字段映射与流水模式不可修改。</p>
    <p>原始记录 {feedback.record_total} 条 · 交易明细表记录 {feedback.transaction_total} 条 · 来源已接纳流水 {feedback.accepted} 条 · 已配对冲正/撤销 {feedback.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {feedback.non_monetary ?? 0} 条 · 未计入资金流水 <strong>{unresolvedCount(feedback)}</strong> 条 · 来源反馈 {feedback.non_transaction} 条 · 辅助资料 {feedback.auxiliary} 条</p>
    <p>{complete ? '自动处理已完成，未计入资金流水的记录已' : uncertain ? '处理结果待核实。未计入资金流水的记录按规则' : '未计入资金流水的记录按规则'}保留来源和原因，无需逐条确认；这些记录不参与金额、交易笔数和关系统计。</p>
    <p className="muted">仅按完整来源信息唯一配对冲正/撤销；配对双方保留来源记录，不计入有效资金统计。普通退款按来源记账方向与金额处理，不因摘要含“退款”自动排除。</p>
    {unresolvedCount(feedback) > 0 && <p role="note">分析仅使用可靠识别的有效流水，可能无法反映完整资金流向、金额和关联关系。未计入不代表金额为零或没有发生交易。</p>}
    {complete && (feedback.excluded_flows ?? 0) > 0 && <p>本案有 {feedback.excluded_flows} 条留存来源流水未计入有效统计。</p>}
    {Object.entries(feedback.currency_totals).length ? <><p>来源已接纳流水金额合计（按币种，含重复记录，已排除配对冲正/撤销双方；非本次新增金额或净结算金额）：</p><ul>{Object.entries(feedback.currency_totals).map(([currency, amount]) => <li key={currency} className="mono">{formatMoney(amount)} {currency || '未标注币种'}</li>)}</ul></> : <p>当前没有来源已接纳流水金额。</p>}
    {Object.entries(feedback.reason_counts).length > 0 && <details open><summary>自动分类依据</summary><ul>{Object.entries(feedback.reason_counts).map(([reason, count]) => <li key={reason}>{feedbackReason(reason)}：{count} 条</li>)}</ul></details>}
    {examples.length > 0 && <details><summary>未计入记录的来源示例（前 {examples.length} 条）</summary><ul>{examples.map((row, index) => <li key={index}>{row.member_path} · 原始第 {row.source_row} 行 · {feedbackReason(row.reason_code)}</li>)}</ul></details>}
    <details><summary>包内来源文件与校验信息</summary><div className="preview-wrap"><table className="preview-table"><thead><tr><th>来源文件</th><th>记录类型</th><th>记录数</th><th>字节数</th><th>编码</th><th>SHA-256</th></tr></thead><tbody>{feedback.members.map(member => <tr key={member.member_path}><td>{member.member_path}</td><td>{feedbackRoles[member.role] || member.role}</td><td>{member.row_count}</td><td>{member.size_bytes}</td><td>{member.encoding || '—'}</td><td className="mono">{member.sha256}</td></tr>)}</tbody></table></div></details>
  </section>;
}

export function ImportRecordEvidence({ caseId, sourceId, client, scopeSignal, memberPath, openPreview = false, focusRow, onClearFocus, compact = false, initialRows = [], filename = '' }: { caseId: string; sourceId: string; client: FundsCaseClientV1; scopeSignal: AbortSignal; memberPath?: string; openPreview?: boolean; focusRow?: number; onClearFocus?: () => void; compact?: boolean; initialRows?: PreviewFlow[]; filename?: string }) {
  const [open, setOpen] = useState(false), [page, setPage] = useState(1), [reload, setReload] = useState(0);
  const [data, setData] = useState<ImportRecordPage | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(false);
  const recordsElement = useRef<HTMLDivElement>(null), recordPage = focusRow === undefined ? page : 1;
  useEffect(() => { if ((compact || openPreview || focusRow !== undefined) && !scopeSignal.aborted) setOpen(true); }, [compact, openPreview, focusRow, scopeSignal]);
  useEffect(() => { if (data && focusRow !== undefined && !scopeSignal.aborted) { recordsElement.current?.scrollIntoView?.({ block: 'nearest' }); recordsElement.current?.focus({ preventScroll: true }); } }, [data, focusRow, scopeSignal]);
  useEffect(() => {
    if (!open || !client.importRecords || scopeSignal.aborted) return;
    const controller = new AbortController(); let active = true;
    const unlink = linkAbortSignal(scopeSignal, controller);
    const revoke = () => { setData(null); setOpen(false); };
    scopeSignal.addEventListener('abort', revoke, { once: true });
    setData(null); setError(''); setLoading(true);
    (focusRow !== undefined ? client.importRecords(sourceId, 1, controller.signal, memberPath, focusRow) : memberPath === undefined ? client.importRecords(sourceId, recordPage, controller.signal) : client.importRecords(sourceId, recordPage, controller.signal, memberPath)).then(value => {
      if (!active || controller.signal.aborted) return;
      if (value.case_id !== caseId || value.source_id !== sourceId || memberPath !== undefined && value.items.some(item => item.member_path !== memberPath)
        || focusRow !== undefined && value.items.some(item => item.source_row !== focusRow)) throw new Error('来源记录与当前案件、文件或定位行不一致。');
      setData(value);
    }).catch(reason => { if (active && !controller.signal.aborted && reason.name !== 'AbortError') setError(reason.message); })
      .finally(() => { if (active && !controller.signal.aborted) setLoading(false); });
    return () => { active = false; scopeSignal.removeEventListener('abort', revoke); unlink(); controller.abort(); };
  }, [client, caseId, sourceId, scopeSignal, memberPath, recordPage, focusRow, open, reload]);
  if (scopeSignal.aborted) return null;
  if (!client.importRecords) return compact ? <ImportDataPreview rows={initialRows} filename={filename} /> : null;
  if (compact && focusRow === undefined) return <>
    <ImportDataPreview rows={data && data.items.some(row => Object.hasOwn(row, 'preview')) ? data.items.flatMap(row => row.preview ? [row.preview] : []) : initialRows} filename={filename} />
    {error && <p role="alert">{error}<button className="ft-text-button" onClick={() => setReload(value => value + 1)}>重试</button></p>}

  </>;
  return <details open={open} className={compact ? 'ft-focused-evidence' : undefined} onToggle={event => setOpen(event.currentTarget.open)}><summary>{compact ? '来源行' : '来源记录与关联依据'}</summary>
    {!compact && <p>以下为当前保留的来源记录与关联资料，导入当时统计不变。</p>}
    {focusRow !== undefined && <p className="ft-focused-row" role="status">{data?.items.length ? '已定位原始第' : '定位原始第'} {focusRow} 行{onClearFocus && <button className="ft-text-button" onClick={onClearFocus}>查看该文件全部来源记录</button>}</p>}
    {loading && <p role="status">正在加载来源记录…</p>}
    {error && <p role="alert">{error}<button onClick={() => setReload(value => value + 1)}>重试来源记录</button></p>}
    {data && data.items.some(row => row.preview) && <ImportDataPreview rows={data.items.flatMap(row => row.preview ? [row.preview] : [])}
      filename={memberPath || '当前来源'} heading="来源行数据预览" note="当前留存流水值 · 原始值与处置见下方" ariaLabel="来源行数据预览，可上下左右滚动" />}
    {data && <div className="ft-record-scroll" ref={recordsElement} role="region" tabIndex={0} aria-label="来源记录预览，可上下左右滚动">{data.items.map(row => <article key={row.record_no} data-focused={row.source_row === focusRow}><p>{row.member_path} · 原始第 {row.source_row} 行 · {feedbackReason(row.reason_code)}</p><SourceFieldEvidence details={row.details} expanded={row.source_row === focusRow} /></article>)}</div>}
    {data && data.total === 0 && <p>未保留可查询的来源记录。</p>}
    {data && data.total > data.page_size && <div className="source-pagination"><button aria-label="来源记录上一页" disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span>第 {data.page} / {Math.ceil(data.total / data.page_size)} 页</span><button aria-label="来源记录下一页" disabled={loading || page * data.page_size >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button></div>}
  </details>;
}
