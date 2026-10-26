import { useEffect, useId, useRef, useState } from 'react';
import type { ImportPreview, ImportJob, ImportSession, TableSummary as TableSummaryData } from '../../../contracts/api-v1';
import { Modal } from '../components/Modal';
import { useFeedback } from '../feedback/Feedback';
import type { FundsCaseClientV1, FundsCaseContextV1, FundsHostActionsV1 } from '../panel/host';
import { formatMoney, displayTime } from '../lib/format';
import { linkAbortSignal, waitForNextPoll } from '../panel/cancellation';

const fieldNames: Record<string, string> = {
  trade_no: '流水号', trade_time: '交易时间', time_part: '交易时刻', _time2: '时间补充', amount: '交易金额', balance: '交易后余额', currency: '币种',
  payer_name: '付款户名', payer_account: '付款账号', payer_bank: '付款开户行', payee_name: '收款户名', payee_account: '收款账号', payee_bank: '收款开户行',
  self_account: '本方账号', self_name: '本方户名', counter_account: '对手账号', counter_name: '对手户名', counter_bank: '对手开户行',
  direction: '收支方向', income_amt: '收入金额', expense_amt: '支出金额', summary: '摘要', channel: '渠道', status: '交易状态', voucher_reference: '凭证引用',
};
const fieldsA = ['trade_time', '_time2', 'payer_account', 'payer_name', 'payee_account', 'payee_name', 'amount', 'trade_no', 'balance', 'currency', 'self_account', 'self_name', 'summary'];
const fieldsB = ['trade_time', '_time2', 'self_account', 'self_name', 'counter_account', 'counter_name', 'direction', 'amount', 'income_amt', 'expense_amt', 'trade_no', 'balance', 'currency', 'summary'];
const modeFields = {
  A: new Set([...fieldsA, 'payer_bank', 'payee_bank', 'self_name', 'channel', '_time2']),
  B: new Set([...fieldsB, 'counter_bank', 'channel', '_time2']),
};
const feedbackReasons: Record<string, string> = {
  transaction_valid: '按来源方向与金额识别为资金流水', auxiliary_table: '辅助资料，未作为资金流水', feedback_only: '来源反馈，未包含资金流水',
  negative_reversal_text: '负数与冲正描述缺少一致的来源依据', negative_revocation_text: '负数与撤销描述缺少一致的来源依据',
  negative_reversal_revocation_text: '负数与冲正及撤销描述缺少一致的来源依据', negative_unknown: '来源未明确负号含义',
  missing_core: '缺少形成资金流水必需的信息', invalid_amount: '交易金额格式无效', amount_precision: '交易金额超过分精度',
  amount_overflow: '交易金额超出支持范围', zero_amount: '零金额，无资金变动', invalid_direction: '收付方向无法识别',
  invalid_time: '交易时间无效', invalid_currency: '币种无法识别', unknown_status: '交易状态含义缺少适用的来源依据',
  invalid_balance: '交易余额格式无效', balance_precision: '交易余额超过分精度', balance_overflow: '交易余额超出支持范围',
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
const feedbackReason = (reason: string) => feedbackReasons[reason] || '其他自动分类结果，详见完整处理报告';
const feedbackRoles: Record<string, string> = { transaction: '交易明细', account: '账户信息', client: '客户资料', subaccount: '子账户', address: '地址', phone: '联系方式', task: '查询任务', freeze: '冻结信息' };
const unresolvedCount = (feedback: NonNullable<ImportPreview['feedback']>) => (feedback.unresolved ?? 0) + (feedback.review ?? 0);

const mappingBasis: Record<string, string> = { exact_header_alias: '按完整表头别名识别并校验', source_transaction_id: '使用来源交易流水号', independent_voucher_reference: '凭证号为独立引用，不与交易流水号判冲突', explicit_time_format: '明确的日期时间格式' };
const mappingExplanation = (item: Record<string, string>) => [
  item.member_path, `${fieldNames[item.field] || item.field || '字段'} ← ${item.column || '未识别'}`,
  item.time_format ? `时间格式：${item.time_format}` : '',
  item.unit && ['amount', 'balance', 'income_amt', 'expense_amt'].includes(item.field) ? `来源单位：${item.unit}` : '', item.unit_factor && ['amount', 'balance', 'income_amt', 'expense_amt'].includes(item.field) ? `金额单位换算系数：${item.unit_factor}` : '',
  item.candidate_count ? `候选列：${item.candidate_count}` : '', mappingBasis[item.basis] || item.basis,
].filter(Boolean).join(' · ');
const documentExplanation = (item: Record<string, unknown>) => [
  String(item.member_path || '来源表格'), item.sheet ? `工作表：${String(item.sheet)}` : '',
  item.header_row != null ? `表头第 ${String(item.header_row)} 行` : '未能唯一识别表头',
  item.row_count != null ? `来源记录 ${String(item.row_count)} 条` : '',
].filter(Boolean).join(' · ');

function TableSummary({ summary, complete = false, uncertain = false }: { summary: TableSummaryData; complete?: boolean; uncertain?: boolean }) {
  const evidence = summary.mapping_evidence ?? [], documents = summary.documents ?? [], examples = summary.unresolved_examples ?? [];
  return <section className="resume-summary" aria-label="表格自动识别与全量校验汇总">
    <p><strong>表格自动识别 · 全量校验</strong></p>
    <p>来源记录 {summary.record_total} 条 · 可靠识别流水 {summary.accepted} 条 · 已配对冲正/撤销 {summary.cancelled ?? 0} 条（双方均不计有效资金） · 未计入资金流水 {summary.unresolved} 条 · 无资金变动 {summary.non_monetary} 条 · 非交易记录 {summary.non_transaction} 条 · 辅助资料 {summary.auxiliary} 条</p>
    <p>{complete ? '自动处理已完成，未计入资金流水的记录已' : uncertain ? '处理结果待核实。未计入资金流水的记录按规则' : '未计入资金流水的记录按规则'}保留来源和原因，无需逐条确认；这些记录不参与金额、交易笔数和关系统计。</p>
    {(summary.cancelled ?? 0) > 0 && <p>已配对记录保留来源和核验依据，双方退出有效资金统计；退款、退货或仅有负号的记录不据此排除。</p>}
    {complete && (summary.excluded_flows ?? 0) > 0 && <p>本案另有 {summary.excluded_flows} 笔历史留存流水已退出有效统计。</p>}
    {summary.unresolved > 0 && <p role="note">分析仅使用可靠识别的有效流水，可能无法反映完整资金流向、金额和关联关系。未计入不代表金额为零或没有发生交易。</p>}
    {summary.accepted === 0 && <p>文件{complete ? '已处理' : '可保存处理结果'}，暂无可可靠计入的流水。</p>}
    {Object.keys(summary.currency_totals).length > 0 && <><p>可靠识别流水金额合计（按币种，含重复；非本次新增金额或净结算金额）：</p><ul>{Object.entries(summary.currency_totals).map(([currency, amount]) => <li key={currency} className="mono">{formatMoney(amount)} {currency || '未标注币种'}</li>)}</ul></>}
    {evidence.length > 0 && <details open><summary>字段映射、金额单位与方向依据（共 {evidence.length} 项）</summary><ul>{evidence.slice(0, 50).map((item, index) => <li key={index}>{mappingExplanation(item)}</li>)}</ul>{evidence.length > 50 && <p>显示前50项，完整依据见处理报告。</p>}</details>}
    {Object.keys(summary.reason_counts).length > 0 && <details open><summary>全量校验与未计入原因</summary><ul>{Object.entries(summary.reason_counts).map(([reason, count]) => <li key={reason}>{feedbackReason(reason)}：{count} 条</li>)}</ul></details>}
    {examples.length > 0 && <details><summary>未计入记录的来源位置</summary><ul>{examples.map((row, index) => <li key={index}>{row.member_path} · 原始第 {row.source_row} 行 · {feedbackReason(row.reason_code)}</li>)}</ul></details>}
    {documents.length > 0 && <details><summary>已检查的来源文件与工作表（共 {documents.length} 项）</summary><ul>{documents.slice(0, 50).map((item, index) => <li key={index}>{documentExplanation(item)}</li>)}</ul>{documents.length > 50 && <p>显示前50项，完整清单见处理报告。</p>}</details>}
  </section>;
}

function FeedbackSummary({ feedback, complete = false, uncertain = false }: { feedback: NonNullable<ImportPreview['feedback']>; complete?: boolean; uncertain?: boolean }) {
  const examples = [...(feedback.unresolved_examples ?? []), ...(feedback.review_examples ?? [])].slice(0, 10);
  return <section className="resume-summary" aria-label="银行反馈包识别与处置汇总">
    <p><strong>银行反馈 ZIP · 已自动识别</strong></p><p className="muted">{complete ? '按固定规则自动分类并保存来源与处理结果' : '按固定规则自动分类'}，字段映射与流水模式不可修改。</p>
    <p>原始记录 {feedback.record_total} 条 · 交易明细表记录 {feedback.transaction_total} 条 · 来源已接纳流水 {feedback.accepted} 条 · 已配对冲正/撤销 {feedback.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {feedback.non_monetary ?? 0} 条 · 未计入资金流水 <strong>{unresolvedCount(feedback)}</strong> 条 · 来源反馈 {feedback.non_transaction} 条 · 辅助资料 {feedback.auxiliary} 条</p>
    <p>{complete ? '自动处理已完成，未计入资金流水的记录已' : uncertain ? '处理结果待核实。未计入资金流水的记录按规则' : '未计入资金流水的记录按规则'}保留来源和原因，无需逐条确认；这些记录不参与金额、交易笔数和关系统计。</p>
    <p className="muted">仅按完整来源信息唯一配对冲正/撤销；配对双方保留来源记录，不计入有效资金统计。普通退款按来源记账方向与金额处理，不因摘要含“退款”自动排除。</p>
    {unresolvedCount(feedback) > 0 && <p role="note">分析仅使用可靠识别的有效流水，可能无法反映完整资金流向、金额和关联关系。未计入不代表金额为零或没有发生交易。</p>}
    {complete && (feedback.excluded_flows ?? 0) > 0 && <p>本案另有 {feedback.excluded_flows} 笔历史留存流水已退出有效统计。</p>}
    {Object.entries(feedback.currency_totals).length ? <><p>来源已接纳流水金额合计（按币种，含重复记录，已排除配对冲正/撤销双方；非本次新增金额或净结算金额）：</p><ul>{Object.entries(feedback.currency_totals).map(([currency, amount]) => <li key={currency} className="mono">{formatMoney(amount)} {currency || '未标注币种'}</li>)}</ul></> : <p>当前没有来源已接纳流水金额。</p>}
    {Object.entries(feedback.reason_counts).length > 0 && <details open><summary>自动分类依据</summary><ul>{Object.entries(feedback.reason_counts).map(([reason, count]) => <li key={reason}>{feedbackReason(reason)}：{count} 条</li>)}</ul></details>}
    {examples.length > 0 && <details><summary>未计入记录的来源示例（前 {examples.length} 条）</summary><ul>{examples.map((row, index) => <li key={index}>{row.member_path} · 原始第 {row.source_row} 行 · {feedbackReason(row.reason_code)}</li>)}</ul></details>}
    <details><summary>包内来源文件与校验信息</summary><div className="preview-wrap"><table className="preview-table"><thead><tr><th>来源文件</th><th>记录类型</th><th>记录数</th><th>字节数</th><th>编码</th><th>SHA-256</th></tr></thead><tbody>{feedback.members.map(member => <tr key={member.member_path}><td>{member.member_path}</td><td>{feedbackRoles[member.role] || member.role}</td><td>{member.row_count}</td><td>{member.size_bytes}</td><td>{member.encoding || '—'}</td><td className="mono">{member.sha256}</td></tr>)}</tbody></table></div></details>
  </section>;
}

export function ImportDialog({ target, client, scopeSignal, resume, actions, onClose, onImported, onOpenImport, settlementHint, active = true }: { target: FundsCaseContextV1; client: FundsCaseClientV1; scopeSignal: AbortSignal; resume?: ImportSession; actions?: FundsHostActionsV1; onClose: () => void; onImported: () => void; onOpenImport?: (importId: string) => void; settlementHint?: { importId: string; sequence: number }; active?: boolean }) {
  const feedbackPort = useFeedback();
  const fileId = useId();
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<'A' | 'B'>('A');
  const [marked, setMarked] = useState<string[]>(resume?.request?.mark_accounts ?? []);
  const [selfAccounts] = useState(resume?.request?.self_accounts ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [job, setJob] = useState<ImportJob | null>(null);
  const [dirty, setDirty] = useState(false);
  const [polling, setPolling] = useState(false);
  const [awaitingStatus, setAwaitingStatus] = useState(false);
  const [downloading, setDownloading] = useState('');
  const alive = useRef(true);
  const lifetime = useRef(new AbortController());
  const operation = useRef(0);
  // Hints already present at mount belong to a previous view or attempt.
  const handledSettlement = useRef(settlementHint?.sequence);
  const observingSettlement = useRef(false);
  const selectionInitialized = useRef(resume?.request?.mark_accounts != null);
  const valid = (token: number) => alive.current && token === operation.current && !lifetime.current.signal.aborted && !scopeSignal.aborted;
  const acceptPreview = (value: ImportPreview) => {
    if (value.case_id !== target.caseId || (resume && value.import_id !== resume.import_id)) throw new Error('预览与当前案件导入记录不一致。');
    setPreview(value); setMapping(value.mapping); setMode(value.mode); setDirty(false);
    if (!selectionInitialized.current) { setMarked(value.self_accounts_in_file.map(account => account.account)); selectionInitialized.current = true; }
  };
  const poll = async (importId: string, token: number, signal = lifetime.current.signal) => {
    if (!valid(token) || signal.aborted) return;
    setPolling(true);
    try {
      while (valid(token)) {
        const current = await client.job(importId, signal);
        if (!valid(token)) return;
        if (current.case_id !== target.caseId || current.import_id !== importId) throw new Error('导入进度与当前案件不一致。');
        setJob(current);
        setAwaitingStatus(false); setError('');
        if (current.stage === 'error') { setError(current.error || '导入失败，未完成写入。'); return; }
        if (current.stage === 'done') { onImported(); return; }
        await waitForNextPoll(300, signal);
      }
    } catch (reason) {
      if (valid(token)) {
        // Only this case-bound protocol code confirms a ready session. A
        // generic 404, network error or mismatched response proves no outcome.
        if ((reason as { code?: string })?.code === 'import_job_not_found') {
          setJob(null); setAwaitingStatus(false);
          setError('服务端确认该导入任务尚未开始，请核对预览后重新开始导入。');
        } else {
          setAwaitingStatus(true);
          setError(`进度暂无法确认：${(reason as Error).message}。请重新查询进度，确认前不会再次提交。`);
        }
      }
    }
    finally { if (valid(token)) setPolling(false); }
  };
  const restorePreview = async () => {
    if (!resume || awaitingStatus || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = ++operation.current;
    setBusy(true); setError(''); setJob(null);
    try {
      const value = await client.repreview(resume.import_id, { mode: resume.request?.mode ?? resume.mode, mapping: resume.request?.mapping ?? resume.mapping }, lifetime.current.signal);
      if (valid(token)) acceptPreview(value);
    } catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  useEffect(() => {
    alive.current = true;
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
    const controller = lifetime.current;
    const unlink = linkAbortSignal(scopeSignal, controller);
    if (resume && !controller.signal.aborted) {
      if (resume.case_id !== target.caseId || resume.surface !== 'v1') setError('导入记录与当前案件工作区不一致。');
      else if (resume.stage === 'done' || resume.stage === 'parsing' || resume.stage === 'importing') {
        const current: ImportJob = { case_id: target.caseId, import_id: resume.import_id, stage: resume.stage, done: resume.stage === 'done' ? resume.result?.ok ?? 0 : 0, total: resume.result?.total ?? 0, message: resume.stage === 'done' ? '完成' : '正在查询进度…', error: null, result: resume.result };
        setJob(current);
        if (resume.stage !== 'done') void poll(resume.import_id, ++operation.current);
      } else if (resume.source_available && (resume.stage === 'ready' || resume.recoverable)) void restorePreview();
      else setError('原始上传文件不可用于恢复，请重新上传。');
    }
    return () => { alive.current = false; operation.current += 1; unlink(); controller.abort(); };
  }, [scopeSignal]);
  const upload = async (file: File | undefined) => {
    if (!file || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = ++operation.current;
    setBusy(true); setError(''); setJob(null);
    selectionInitialized.current = false;
    try {
      const value = await client.preview(file, lifetime.current.signal);
      if (!valid(token)) return;
      acceptPreview(value);
      // Only the response to this new upload can authorize the one automatic
      // commit. Restored previews never restart a write on mount or retry.
      if (value.automatic === true) await startCommit(value, token, value.mode, value.mapping,
        value.self_accounts_in_file.map(account => account.account));
    }
    catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  const repreview = async () => {
    if (!preview || awaitingStatus || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = ++operation.current;
    setBusy(true); setError(''); setJob(null);
    try { const value = await client.repreview(preview.import_id, preview.feedback ? { mode: preview.mode, mapping: preview.mapping } : { mode, mapping }, lifetime.current.signal); if (valid(token)) acceptPreview(value); }
    catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  const startCommit = async (value: ImportPreview, token: number, selectedMode: 'A' | 'B', selectedMapping: Record<string, string>, selectedAccounts: string[]) => {
    if (!valid(token)) return;
    observingSettlement.current = false;
    // A rejected POST promise cannot establish whether the server accepted it.
    // Keep the preview's import ID and permit only reads until status is known.
    setBusy(true); setError(''); setJob(null); setAwaitingStatus(true);
    try {
      const started = await client.commit(value.import_id, { mode: value.feedback ? value.mode : selectedMode, mapping: value.feedback ? value.mapping : selectedMapping, mark_accounts: selectedAccounts, self_accounts: value.feedback ? [] : selfAccounts }, lifetime.current.signal);
      if (!valid(token)) return;
      if (started.case_id !== target.caseId || started.import_id !== value.import_id) throw new Error('导入确认与当前案件不一致。');
      setBusy(false);
      await poll(value.import_id, token);
    } catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  const commit = async () => {
    if (!preview || dirty || busy || polling || awaitingStatus || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    await startCommit(preview, ++operation.current, mode, mapping, marked);
  };
  const complete = job?.stage === 'done';
  const activeJob = awaitingStatus || job?.stage === 'parsing' || job?.stage === 'importing';
  const working = busy || polling;
  const importId = job?.import_id ?? preview?.import_id ?? resume?.import_id;
  useEffect(() => {
    if (!settlementHint || settlementHint.sequence === handledSettlement.current) return;
    handledSettlement.current = settlementHint.sequence;
    if (settlementHint.importId !== importId || !importId || scopeSignal.aborted || lifetime.current.signal.aborted || job?.stage === 'done' || job?.stage === 'error') return;
    // A verified host hint only starts a fresh read. It does not establish an
    // outcome, replay a write, or leave old request callbacks in control.
    const token = ++operation.current;
    observingSettlement.current = true;
    setBusy(false); setPolling(false); setError(''); setAwaitingStatus(true);
    void poll(importId, token);
  }, [settlementHint?.sequence, settlementHint?.importId, importId, job?.stage, scopeSignal]);
  useEffect(() => {
    if (!importId || scopeSignal.aborted || (!awaitingStatus && !job)) return;
    if (observingSettlement.current && job?.stage !== 'done' && job?.stage !== 'error') return;
    const status = job?.stage === 'done' ? 'success' : job?.stage === 'error' ? 'error' : awaitingStatus && !working ? 'unknown' : 'running';
    feedbackPort.task({ id: `import:${importId}`, operation: 'import', importId, status,
      title: status === 'success' ? '流水导入已完成' : status === 'unknown' ? '导入结果待核对' : status === 'error' ? '流水导入未完成' : '正在导入流水',
      detail: status === 'unknown' ? '只查询原任务进度，确认前不会再次提交。' : status === 'error' ? '任务未完成，请打开原任务核对结果。' : status === 'success' ? '处理结果已保存，可打开原任务查看。' : '关闭进度后任务仍继续。',
      progress: job && job.total > 0 ? { done: job.done, total: job.total } : null, announce: false,
      actions: onOpenImport ? [{ id: 'open-import', label: status === 'success' ? '查看结果' : '查看进度', run: () => onOpenImport(importId) }] : [] });
  }, [feedbackPort, importId, job, awaitingStatus, working, scopeSignal, onOpenImport]);
  const feedback = complete ? job.result?.feedback : preview?.feedback;
  const tableSummary = complete ? job.result?.table_summary : preview?.table_summary;
  const classified = !!feedback || !!tableSummary;
  const originalAvailable = !resume || resume.source_available;
  const download = async (kind: 'report' | 'original') => {
    const sourceId = job?.result?.source_id, action = kind === 'report' ? actions?.downloadImportReport : actions?.downloadSource;
    if (!sourceId || !action || (kind === 'original' && !originalAvailable) || downloading || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = operation.current;
    setDownloading(kind); setError('');
    try { await action({ sourceId, filename: kind === 'report' ? `feedback-${sourceId}-dispositions.json` : preview?.filename ?? resume?.filename ?? 'bank-feedback.zip' }, lifetime.current.signal); }
    catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setDownloading(''); }
  };
  return active ? <Modal title="导入流水" onClose={onClose} busy={busy}>
    <div className="modal-context">导入到 <strong>{target.name}</strong><span>当前案件内去重</span></div>
    <ol className="import-steps" aria-label="导入步骤"><li className={complete && classified ? 'done' : !preview ? 'active' : 'done'}>1 <span>选择文件</span></li><li className={preview && !job ? 'active' : ''}>2 <span>{classified ? '自动识别与校验' : '映射与预览'}</span></li><li className={job ? 'active' : ''}>3 <span>{classified ? '处理结果' : '核对结果'}</span></li></ol>
    {error && <div role="alert" className={awaitingStatus ? 'warning-banner' : 'error-banner'}><span>{error}</span>{awaitingStatus && <p>导入结果暂无法确认，请重新查询进度，确认前不会再次提交。</p>}{!working && activeJob ? <button onClick={() => { setError(''); void poll(resume?.import_id ?? preview!.import_id, ++operation.current); }}>重新查询进度</button> : !working && job?.stage === 'error' && job.recoverable !== false ? <button onClick={() => void (preview ? repreview() : restorePreview())}>重新预览后重试</button> : !working && resume && !preview && !complete && <button onClick={() => void restorePreview()}>重试恢复预览</button>}</div>}
    {complete ? <>
      <div className="import-complete" role="status"><span className="success-symbol">✓</span><h3>{classified ? '自动处理完成' : '流水已写入当前案件'}</h3>
        <p>{classified && <>已保存 {feedback?.record_total ?? tableSummary!.record_total} 条记录 · </>}{classified ? '新增资金流水' : '新增'} <strong>{tableSummary?.effective_added ?? job.result?.ok ?? 0}</strong> 笔 · 重复 {job.result?.rows_dup ?? 0} 笔 · {feedback ? <>已配对冲正/撤销 {feedback.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {feedback.non_monetary ?? 0} 条 · 未计入资金流水 {unresolvedCount(feedback)} 条 · 来源反馈 {feedback.non_transaction} 条 · 辅助资料 {feedback.auxiliary} 条</> : tableSummary ? <>未计入资金流水 {tableSummary.unresolved} 条 · 已配对冲正/撤销 {tableSummary.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {tableSummary.non_monetary} 条 · 非交易记录 {tableSummary.non_transaction} 条 · 辅助资料 {tableSummary.auxiliary} 条</> : <>跳过 {job.result?.skipped ?? 0} 行</>}</p>
        {tableSummary?.effective_added != null && tableSummary.effective_added !== job.result?.ok && <p>本次保留流水事实 {job.result?.ok ?? 0} 笔，其中新增有效资金流水 {tableSummary.effective_added} 笔；已有配对依据的记录不重复进入统计。</p>}
        <p className="muted">批次 {job.result?.batch_id} · {preview?.filename ?? resume?.filename}</p>
      </div>
      {classified && <>{feedback ? <FeedbackSummary feedback={feedback} complete /> : <TableSummary summary={tableSummary!} complete />}<div className="resume-summary" aria-label={feedback ? '反馈包文件下载' : '导入来源与处理报告下载'}><p>自动分类与来源记录已保存。完整处理报告包含全部记录、来源行号与分类依据，可用于追溯。</p>
        <button className="button secondary" disabled={!job.result?.source_id || !actions?.downloadImportReport || !!downloading} onClick={() => void download('report')}>下载完整处理报告（JSON）</button>
        <button className="button secondary" disabled={!job.result?.source_id || !actions?.downloadSource || !originalAvailable || !!downloading} onClick={() => void download('original')}>{feedback ? '下载原始 ZIP' : '下载原始文件'}</button>
        {(!actions?.downloadImportReport || !actions?.downloadSource) && <p className="muted">宿主未提供{!actions?.downloadImportReport ? '处理报告' : ''}{!actions?.downloadImportReport && !actions?.downloadSource ? '和' : ''}{!actions?.downloadSource ? '原件' : ''}下载能力。</p>}
        {!originalAvailable && <p className="muted">{feedback ? '原始 ZIP 已不可用' : '原始文件已不可用'}；处理报告仍可用于追溯。</p>}{downloading && <p role="status">正在准备下载…</p>}
      </div></>}
    </>
      : !preview ? resume ? <div className="resume-summary"><strong>{resume.filename}</strong><p className="muted">{working ? activeJob ? '正在查询原任务，关闭窗口后任务仍继续。' : '正在用原始上传与已保存映射重新预览…' : '请核对任务状态后继续。'}</p></div>
        : <label className="file-drop" htmlFor={fileId} aria-busy={working}><span className="upload-symbol">↑</span><strong>{busy ? '正在解析文件…' : '选择 CSV、Excel 或银行反馈 ZIP'}</strong><span id={`${fileId}-hint`}>支持 UTF-8／GBK CSV、XLSX、XLS 和已知格式的银行反馈 ZIP</span><input id={fileId} aria-label="选择流水文件" aria-describedby={`${fileId}-hint`} type="file" accept=".csv,.tsv,.txt,.xlsx,.xls,.zip" disabled={working} onChange={event => void upload(event.target.files?.[0])} /></label>
      : <>
        <div className="file-summary"><strong>{preview.filename}</strong><span>{preview.feedback ? <>原始记录 {preview.feedback.record_total} 条 · 来源已接纳流水 {preview.feedback.accepted} 条 · 已配对冲正/撤销 {preview.feedback.cancelled ?? 0} 条（双方均不计有效资金） · 无资金变动 {preview.feedback.non_monetary ?? 0} 条 · 未计入资金流水 {unresolvedCount(preview.feedback)} 条 · 来源反馈 {preview.feedback.non_transaction} 条 · 辅助资料 {preview.feedback.auxiliary} 条</> : tableSummary ? <>来源记录 {tableSummary.record_total} 条 · 可靠识别流水 {tableSummary.accepted} 条 · 未计入资金流水 {tableSummary.unresolved} 条</> : <>{preview.stats.total} 行 · 可导入 {preview.stats.ok} 行 · 跳过 {preview.stats.skipped} 行</>}</span></div>
        {tableSummary && <TableSummary summary={tableSummary} uncertain={awaitingStatus} />}
        {preview.feedback ? <FeedbackSummary feedback={preview.feedback} uncertain={awaitingStatus} /> : <><label className="mode-field">文件类型<select aria-label="流水文件类型" value={mode} disabled={working || activeJob} onChange={event => { const next = event.target.value as 'A' | 'B'; setMode(next); setMapping(value => Object.fromEntries(Object.entries(value).filter(([field]) => modeFields[next].has(field)))); setDirty(true); }}><option value="A">双边流水（付款方 → 收款方）</option><option value="B">单账户流水（本方与交易对手）</option></select></label>
        <details className="mapping"><summary>{tableSummary ? '高级：调整解释' : '检查字段映射'} <span>{dirty ? '已修改，请重新预览' : '已自动识别，可调整'}</span></summary><div className="mapping-grid">{(mode === 'A' ? fieldsA : fieldsB).map(field => <label key={field}>{fieldNames[field]}<select aria-label={`映射${fieldNames[field]}`} value={mapping[field] || ''} disabled={working || activeJob} onChange={event => { setMapping(value => ({ ...value, [field]: event.target.value })); setDirty(true); }}><option value="">不使用</option>{preview.columns.map(column => <option key={column}>{column}</option>)}</select></label>)}</div></details>
        {dirty && <button className="button secondary" onClick={() => void repreview()} disabled={working || activeJob}>按当前映射重新预览</button>}</>}
        <div className="preview-wrap"><table className="preview-table"><caption>规范化预览 · 前 {preview.preview.length} 行</caption><thead><tr>{preview.feedback && <th>来源文件／原始行</th>}<th>交易时间</th><th>付款方</th><th>收款方</th><th className="align-right">金额</th></tr></thead><tbody>{preview.preview.map((row, index) => <tr key={index}>{preview.feedback && <td>{row.source_member || '—'}<small>原始第 {row.source_row ?? '—'} 行 · 记录 {row.source_record_no ?? '—'}</small></td>}<td className="mono">{displayTime(row.trade_time)}</td><td>{row.payer_name || (preview.feedback ? row.payer_account ? '户名未提供' : '未知对手' : '—')}<small className="mono">{row.payer_account || (preview.feedback ? '账号缺失' : '')}</small></td><td>{row.payee_name || (preview.feedback ? row.payee_account ? '户名未提供' : '未知对手' : '—')}<small className="mono">{row.payee_account || (preview.feedback ? '账号缺失' : '')}</small></td><td className="align-right mono">{formatMoney(row.amount)} <small>{row.currency}</small></td></tr>)}</tbody></table></div>
        {preview.self_accounts_in_file.length > 0 && <fieldset className="main-accounts"><legend>标记本方主账号</legend>{preview.self_accounts_in_file.map(account => <label key={account.account}><input type="checkbox" checked={marked.includes(account.account)} disabled={working || activeJob} onChange={event => setMarked(value => event.target.checked ? [...value, account.account] : value.filter(item => item !== account.account))} />{account.name || account.account} <span className="mono">{account.account}</span></label>)}</fieldset>}
      </>}
    {working && !complete && <div role="status" className="import-progress"><progress aria-label={job ? '流水导入进度' : '文件上传与解析进度'} {...(job && job.total > 0 ? { max: job.total, value: job.done } : {})} /><span>{job?.message || '正在处理…'} {job && job.total > 0 ? `${job.done} / ${job.total}` : ''}</span></div>}
    <footer className="modal-footer"><button className="button secondary" disabled={busy} onClick={onClose}>{complete ? classified ? '完成' : '返回流水核对' : activeJob ? '关闭进度' : '取消'}</button>{preview && !complete && !activeJob && <button className="button primary" disabled={working || dirty || (!classified && preview.stats.ok === 0) || job?.stage === 'error'} onClick={() => void commit()}>{working ? '正在导入…' : classified ? '开始导入' : '确认导入'}</button>}</footer>
  </Modal> : null;
}
