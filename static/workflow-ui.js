/* Import UI orchestration only. The parser, durable task and graph stay owned by
 * their existing services; a view session never grants a stale response access. */
(function () {
  'use strict';

  let active = null, serial = 0, previewScroll = null;
  // Preserve the deep link before case selection updates its URL.
  const navigation = new URL(window.location.href).searchParams;
  let restored = false;
  // A case switch or a new view session cannot revoke a submitted server job.
  // Leases are per attempt so an old read cannot release a later retry.
  const importLeases = new Set();
  const inCase = session => isCurrentCase(session.context, false);
  const ownsView = session => active === session && inCase(session);
  const visible = session => ownsView(session) && !session.closed && $('#importModal').style.display !== 'none';
  const text = error => String(error?.message || error || '请求失败').slice(0, 200);
  const pending = session => session.committing || session.polling || session.recovering;

  const feedbackReasons = {
    unsupported_attachment: '附件原件已保留，格式未适配，内部记录数量与资金内容尚未解释', non_business_attachment: 'Finder元数据附件，原件保留，不生成资金流水',
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
  const feedbackReason = reason => feedbackReasons[reason] || '其他自动分类结果，详见完整处理报告';
  const feedbackRoles = { transaction: '交易明细', account: '账户信息', client: '客户资料', subaccount: '子账户', address: '地址', phone: '联系方式', task: '查询任务', freeze: '冻结信息' };
  const mappingNames = { trade_no: '流水号', trade_time: '交易时间', time_part: '交易时刻', _time2: '时间补充', amount: '交易金额', balance: '交易后余额', currency: '币种',
    payer_name: '付款户名', payer_account: '付款账号', payer_bank: '付款开户行', payee_name: '收款户名', payee_account: '收款账号', payee_bank: '收款开户行',
    self_account: '本方账号', self_name: '本方户名', counter_account: '对手账号', counter_name: '对手户名', counter_bank: '对手开户行',
    direction: '收支方向', income_amt: '收入金额', expense_amt: '支出金额', summary: '摘要', channel: '渠道', status: '交易状态', voucher_reference: '凭证引用' };
  const mappingBasis = { exact_header_alias: '按完整表头别名识别并校验', source_transaction_id: '使用来源交易流水号', independent_voucher_reference: '凭证号为独立引用，不与交易流水号判冲突', explicit_time_format: '明确的日期时间格式' };
  const unresolvedCount = feedback => (feedback.unresolved || 0) + (feedback.review || 0);
  const feedbackCounts = feedback => `已配对冲正/撤销 ${fmtCount(feedback.cancelled ?? 0)} 条（双方均不计有效资金） · 无资金变动 ${fmtCount(feedback.non_monetary || 0)} 条 · 未计入资金流水 ${fmtCount(unresolvedCount(feedback))} 条 · 来源反馈 ${fmtCount(feedback.non_transaction)} 条 · 辅助资料 ${fmtCount(feedback.auxiliary)} 条`;
  const tableCounts = summary => `可靠识别流水 ${fmtCount(summary.accepted)} 条 · 已配对冲正/撤销 ${fmtCount(summary.cancelled ?? 0)} 条（双方均不计有效资金） · 未计入资金流水 ${fmtCount(summary.unresolved)} 条 · 无资金变动 ${fmtCount(summary.non_monetary)} 条 · 非交易记录 ${fmtCount(summary.non_transaction)} 条 · 辅助资料 ${fmtCount(summary.auxiliary)} 条`;

  const effectiveAdded = result => result.table_summary?.effective_added ?? result.ok;
  const retainedFacts = result => result.table_summary?.effective_added != null && result.table_summary.effective_added !== result.ok
    ? `本次保留来源流水记录 ${fmtCount(result.ok)} 条，其中新增有效流水记录 ${fmtCount(result.table_summary.effective_added)} 条；已有配对依据的记录不重复进入统计。` : '';

  function tableHtml(summary, complete = false) {
    const reasons = Object.entries(summary.reason_counts || {}).map(([reason, count]) => `<li>${esc(feedbackReason(reason))}：${fmtCount(count)} 条</li>`).join('');
    const evidence = (summary.mapping_evidence || []).slice(0, 50).map(item => `<li>${esc(item.member_path || '')} · ${esc(mappingNames[item.field] || item.field || '字段')} ← ${esc(item.column || '未识别')}${item.time_format ? ` · 时间格式：${esc(item.time_format)}` : ''}${item.unit && ['amount', 'balance', 'income_amt', 'expense_amt'].includes(item.field) ? ` · 来源单位：${esc(item.unit)}` : ''}${item.unit_factor && ['amount', 'balance', 'income_amt', 'expense_amt'].includes(item.field) ? ` · 金额单位换算系数：${esc(item.unit_factor)}` : ''}${item.candidate_count ? ` · 候选列：${esc(item.candidate_count)}` : ''}${mappingBasis[item.basis] ? ` · ${esc(mappingBasis[item.basis])}` : ''}</li>`).join('');
    const examples = (summary.unresolved_examples || []).map(row => `<li>${esc(row.member_path)} · 原始第 ${fmtCount(row.source_row)} 行 · ${esc(feedbackReason(row.reason_code))}</li>`).join('');
    const totals = Object.entries(summary.currency_totals || {}).map(([currency, amount]) => `<li>${moneyHtml(amount, currency)}</li>`).join('');
    const documents = (summary.documents || []).slice(0, 50).map(item => `<li>${esc(item.member_path || '来源表格')}${item.sheet ? ` · 工作表：${esc(item.sheet)}` : ''}${item.header_row != null ? ` · 表头第 ${fmtCount(item.header_row)} 行` : ' · 未能唯一识别表头'}${item.row_count != null ? ` · 来源记录 ${fmtCount(item.row_count)} 条` : ''}</li>`).join('');
    return `${summary.reason_counts?.unsupported_attachment ? `<p class="warn">${fmtCount(summary.reason_counts.unsupported_attachment)} 份附件格式尚未适配，原件已保留。每份按一个来源条目登记，内部记录数量和金额未知，不能视为整个目录已完整解析。</p>` : ''}<p><strong>表格自动识别 · 全量校验</strong></p><p>来源记录 ${fmtCount(summary.record_total)} 条 · ${tableCounts(summary)}</p>`
      + `<p>${complete ? '自动处理已完成，未计入资金流水的记录已' : '自动处理会正常完成，未计入资金流水的记录会'}保留来源和原因，无需逐条确认；这些记录不参与金额、交易笔数和关系统计。</p>`
      + ((summary.cancelled ?? 0) > 0 ? '<p>已配对记录保留来源和核验依据，双方退出有效资金统计；退款、退货或仅有负号的记录不据此排除。</p>' : '')
      + (complete && (summary.excluded_flows ?? 0) > 0 ? `<p>本案另有 ${fmtCount(summary.excluded_flows)} 笔历史留存流水已退出有效统计。</p>` : '')
      + (summary.unresolved > 0 ? '<p>分析仅使用可靠识别的有效流水，可能无法反映完整资金流向、金额和关联关系。未计入不代表金额为零或没有发生交易。</p>' : '')
      + (summary.accepted === 0 ? `<p>文件${complete ? '已处理' : '可保存处理结果'}，暂无可可靠计入的流水。</p>` : '')
      + (totals ? `<p>可靠识别流水金额合计（按币种，含重复；非本次新增金额或净结算金额）：</p><ul>${totals}</ul>` : '')
      + (evidence ? `<details open><summary>字段映射、金额单位与方向依据（共 ${fmtCount(summary.mapping_evidence.length)} 项）</summary><ul>${evidence}</ul>${summary.mapping_evidence.length > 50 ? '<p>显示前50项，完整依据见处理报告。</p>' : ''}</details>` : '')
      + (reasons ? `<details open><summary>全量校验与未计入原因</summary><ul>${reasons}</ul></details>` : '')
      + (examples ? `<details><summary>未计入记录的来源位置</summary><ul>${examples}</ul></details>` : '')
      + (documents ? `<details><summary>已检查的来源文件与工作表（共 ${fmtCount(summary.documents.length)} 项）</summary><ul>${documents}</ul>${summary.documents.length > 50 ? '<p>显示前50项，完整清单见处理报告。</p>' : ''}</details>` : '');
  }

  function feedbackHtml(feedback, complete = false) {
    const counts = `原始记录 ${fmtCount(feedback.record_total)} 条 · 交易明细表记录 ${fmtCount(feedback.transaction_total)} 条 · 来源已接纳流水 ${fmtCount(feedback.accepted)} 条 · ${feedbackCounts(feedback)}`;
    const reasons = Object.entries(feedback.reason_counts || {}).map(([reason, count]) => `<li>${esc(feedbackReason(reason))}：${fmtCount(count)} 条</li>`).join('');
    const totals = Object.entries(feedback.currency_totals || {}).map(([currency, amount]) => `<li>${moneyHtml(amount, currency)}</li>`).join('');
    const sourceExamples = [...(feedback.unresolved_examples || []), ...(feedback.review_examples || [])].slice(0, 10);
    const examples = sourceExamples.map(row => `<li>${esc(row.member_path)} · 原始第 ${fmtCount(row.source_row)} 行 · ${esc(feedbackReason(row.reason_code))}</li>`).join('');
    const members = (feedback.members || []).map(member => `<tr><td>${esc(member.member_path)}</td><td>${esc(feedbackRoles[member.role] || member.role)}</td><td>${fmtCount(member.row_count)}</td><td>${fmtCount(member.size_bytes)}</td><td>${esc(member.encoding || '—')}</td><td class="mono">${esc(member.sha256)}</td></tr>`).join('');
    return `<p><strong>银行反馈 ZIP · 已自动识别</strong></p><p>按固定规则自动分类并保存来源与处理结果，字段映射与流水模式不可修改。</p><p>${counts}</p>`
      + `<p>${complete ? '自动处理已完成，未计入资金流水的记录已' : '自动处理会正常完成，未计入资金流水的记录会'}保留来源和原因，无需逐条确认；这些记录不参与金额、交易笔数和关系统计。</p>`
      + '<p>仅按完整来源信息唯一配对冲正/撤销；配对双方保留来源记录，不计入有效资金统计。普通退款按来源记账方向与金额处理，不因摘要含“退款”自动排除。</p>'
      + (unresolvedCount(feedback) > 0 ? '<p>分析仅使用可靠识别的有效流水，可能无法反映完整资金流向、金额和关联关系。未计入不代表金额为零或没有发生交易。</p>' : '')
      + (complete && (feedback.excluded_flows ?? 0) > 0 ? `<p>本案另有 ${fmtCount(feedback.excluded_flows)} 笔历史留存流水已退出有效统计。</p>` : '')
      + (totals ? `<p>来源已接纳流水金额合计（按币种，含重复记录，已排除配对冲正/撤销双方；非本次新增金额或净结算金额）：</p><ul>${totals}</ul>` : '<p>当前没有来源已接纳流水金额。</p>')
      + (reasons ? `<details open><summary>自动分类依据</summary><ul>${reasons}</ul></details>` : '')
      + (examples ? `<details><summary>未计入记录的来源示例（前 ${sourceExamples.length} 条）</summary><ul>${examples}</ul></details>` : '')
      + (members ? `<details><summary>包内来源文件与校验信息</summary><div class="tbl-wrap"><table class="tbl"><thead><tr><th>来源文件</th><th>记录类型</th><th>记录数</th><th>字节数</th><th>编码</th><th>SHA-256</th></tr></thead><tbody>${members}</tbody></table></div></details>` : '');
  }

  function resolutionHtml(value, complete = false) {
    if (!value || !(value.unresolved_endpoints || value.conflicts)) return '';
    return `<section aria-label="账户关联结果"><p><strong>${complete ? '账户关联已更新' : '账户资料关联预览'}</strong></p>`
      + `<p>有 ${fmtCount(value.unresolved_endpoints)} 个账户标识尚未关联，${fmtCount(value.conflicts)} 处资料冲突。已知卡号和原始记录继续保留，可在账户详情中核对。</p></section>`;
  }

  function renderPreview(result) {
    const feedback = result.feedback, summary = result.table_summary, classified = feedback || summary;
    renderImport(classified ? { ...result, stats: { ...result.stats,
      bad_examples: (result.stats.bad_examples || []).map(example => ({ ...example, reason: feedbackReason(example.reason) })) } } : result);
    $('#impFeedback').hidden = !classified;
    $('#impFeedback').setAttribute('aria-label', feedback ? '银行反馈包识别与处置汇总' : '表格自动识别与全量校验汇总');
    $('#impFeedback').innerHTML = (feedback ? feedbackHtml(feedback) : summary ? tableHtml(summary) : '') + resolutionHtml(result.resolution);
    for (const id of ['impMappingSection', 'impMode', 'impSelfLine']) $('#' + id).hidden = !!classified;
    if (!classified) return;
    $('#impPreviewNote').textContent = `前 ${result.preview.length} 条来源已接纳流水 · 原始成员与行号`;
    $('#impPreviewHead').innerHTML = '<tr><th>来源文件／原始行</th><th>交易时间</th><th>付款方</th><th>收款方</th><th class="amt">金额</th><th>摘要</th></tr>';
    $('#impTbody').innerHTML = result.preview.map(row => `<tr><td>${esc(row.source_member || '—')}<br>原始第 ${fmtCount(row.source_row ?? '—')} 行 · 记录 ${fmtCount(row.source_record_no ?? '—')}</td><td>${esc(fmtTime(row.trade_time))}</td><td>${esc(row.payer_name || (row.payer_account ? '户名未提供' : '未知对手'))}<br><span class="mono">${esc(row.payer_account || '账号缺失')}</span></td><td>${esc(row.payee_name || (row.payee_account ? '户名未提供' : '未知对手'))}<br><span class="mono">${esc(row.payee_account || '账号缺失')}</span></td><td class="amt">${moneyHtml(row.amount, row.currency)}</td><td>${esc(row.summary || '—')}</td></tr>`).join('');
    $('#impStats').textContent = feedback ? `来源已接纳流水 ${fmtCount(feedback.accepted)} 条 · 已配对冲正/撤销 ${fmtCount(feedback.cancelled ?? 0)} 条（双方均不计有效资金） · 未计入资金流水 ${fmtCount(unresolvedCount(feedback))} 条` : tableCounts(summary);
  }

  async function downloadResult(kind) {
    const session = active, sourceId = session?.result?.source_id;
    if (!session || !visible(session) || !sourceId || session.downloading) return;
    session.downloading = true; controls(session); $('#impDownloadStatus').textContent = '正在准备下载…';
    const path = '/api/v1/cases/' + encodeURIComponent(session.context.caseId) + '/sources/' + encodeURIComponent(sourceId) + (kind === 'report' ? '/report' : '/file');
    try {
      const response = await fetch(path);
      if (!response.ok) throw new Error('无法下载文件，请稍后重试');
      const blob = await response.blob();
      if (!visible(session)) return;
      const url = URL.createObjectURL(blob);
      try {
        const link = document.createElement('a'); link.href = url;
        link.download = kind === 'report' ? `feedback-${sourceId}-dispositions.json` : session.filename;
        document.body.appendChild(link); link.click(); link.remove();
      } finally { URL.revokeObjectURL(url); }
      if (visible(session)) $('#impDownloadStatus').textContent = '下载已准备。';
    } catch (error) { if (visible(session)) $('#impDownloadStatus').textContent = text(error); }
    finally { session.downloading = false; if (visible(session)) controls(session); }
  }

  async function request(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let message = await response.text();
      try { message = JSON.parse(message).detail || message; } catch (_) { /* plain text */ }
      const error = new Error(String(message).slice(0, 200)); error.status = response.status; throw error;
    }
    return response.json();
  }

  function releaseSettledLease(lease) {
    // A live same-case completion still owns its list/graph publication. A
    // detached callback cannot publish and needs only its terminal receipt.
    if (lease?.terminal && !(inCase(lease.session) && (lease.session.committing || lease.session.polling))) importLeases.delete(lease);
  }

  function releaseTerminal(lease, progress) {
    if (!lease || !['done', 'error'].includes(progress?.stage)
      || progress.import_id && progress.import_id !== lease.importId) return false;
    lease.terminal = true; releaseSettledLease(lease); return true;
  }

  async function canWrite(context) {
    if (context?.caseId == null) return false;
    const leases = [...importLeases].filter(lease => String(lease.context.caseId) === String(context.caseId));
    for (const lease of leases) {
      if (!importLeases.has(lease)) continue;
      // Progress may still report the old ready state while POST is arriving.
      // An unsettled commit must never be unlocked by that read.
      if (lease.requestPending) return false;
      if (lease.terminal) {
        releaseSettledLease(lease);
        if (importLeases.has(lease)) return false;
        continue;
      }
      try {
        if (!lease.probe) lease.probe = request('/api/import/progress?import_id=' + encodeURIComponent(lease.importId)
          + '&case_id=' + encodeURIComponent(lease.context.caseId)).then(progress => {
          releaseTerminal(lease, progress);
        }).finally(() => { lease.probe = null; });
        await lease.probe;
      } catch (_) { return false; }
      if (importLeases.has(lease)) return false;
    }
    return ![...importLeases].some(lease => String(lease.context.caseId) === String(context.caseId));
  }
  window.FundsImportWorkflow = Object.freeze({ canWrite, restore });

  function recovery(session, message, retry = false) {
    if (!visible(session)) return;
    $('#impRecovery').hidden = !message; $('#impRecoveryText').textContent = message || '';
    $('#impRetryProgress').hidden = !retry;
  }

  function flow(session) {
    const working = !session.result && (session.previewing || session.committing || session.polling);
    const feedback = session.preview?.feedback || session.result?.feedback || session.preview?.table_summary || session.result?.table_summary;
    let values = ['current', 'pending', 'pending'], status = '选择流水文件';
    if (session.preview || session.phase !== 'file') { values = ['complete', 'current', 'pending']; status = feedback ? '已自动识别格式与记录类型' : '核对字段与数据预览'; }
    if (session.phase === 'confirm') { values = ['complete', 'complete', working ? 'working' : 'current']; status = '正在导入'; }
    if (session.previewing) { values[session.phase === 'file' ? 0 : 1] = 'working'; status = session.phase === 'file' ? '正在上传或解析文件' : '正在重新识别字段'; }
    if (session.dirty && !working) status = '字段已修改，重新识别后可导入';
    if (session.errorStage) { values[{ file: 0, fields: 1, confirm: 2 }[session.errorStage]] = 'error'; status = session.errorStage === 'fields' ? '重新识别未完成' : session.errorStage === 'file' ? '文件未就绪' : '导入未完成'; }
    if (session.recovering && !session.polling && !session.committing) { values[2] = 'warning'; status = '结果待确认，重新读取进度'; }
    if (session.result) { values = ['complete', 'complete', 'complete']; status = feedback ? '自动处理完成，全部记录已保存' : '导入完成'; }
    const labels = feedback ? ['文件', '自动识别', '处理结果'] : ['文件', '字段', '确认'], names = { pending: '未开始', current: feedback ? '已就绪' : '待核对', working: '处理中', complete: '已完成', error: '未完成', warning: '结果待确认' };
    const fieldsLabel = $('#impFlowFieldsLabel'), resultLabel = $('#impFlowConfirmLabel');
    if (fieldsLabel) fieldsLabel.textContent = labels[1];
    if (resultLabel) resultLabel.textContent = labels[2];
    ['impFlowFile', 'impFlowFields', 'impFlowConfirm'].forEach((id, index) => {
      const node = $('#' + id), value = values[index];
      node.setAttribute('data-state', value); node.setAttribute('aria-label', labels[index] + '：' + names[value]);
      if (['current', 'working', 'error', 'warning'].includes(value)) node.setAttribute('aria-current', 'step');
      else node.removeAttribute('aria-current');
    });
    $('#impFlowStatus').textContent = status;
    $('#importModal').setAttribute('aria-busy', String(working));
  }

  function controls(session) {
    if (!ownsView(session)) return;
    const busy = !session.result && (pending(session) || session.previewing), complete = !!session.result;
    const classified = !!session.preview?.feedback || !!session.preview?.table_summary, fixed = classified;
    $('#impCommit').disabled = busy || !session.preview || session.dirty || (!classified && session.preview.stats?.ok === 0);
    $('#impCommit').textContent = classified ? '开始导入' : '确认导入';
    $('#impRe').disabled = busy || !session.preview || fixed;
    $('#impRe').hidden = !session.preview || complete || fixed; $('#impCommit').hidden = !session.preview || complete;
    $$('#impMapping select,input[name=impMode],#impMainSel .mk-acct,#impSelfAccts').forEach(node => { node.disabled = busy; });
    if (fixed) { $$('#impMapping select').forEach(node => { node.disabled = true; }); $$('input[name=impMode]').forEach(node => { node.disabled = true; }); $('#impSelfAccts').disabled = true; }
    const fileBusy = !session.result && (pending(session) || (session.previewing && session.phase !== 'file'));
    $('#impFile').disabled = fileBusy;
    if ($('#impDirectory')) $('#impDirectory').disabled = fileBusy;
    $('#impChangeFile').disabled = busy;
    $('#impRetryProgress').disabled = session.polling;
    $('#impCancel').textContent = complete ? '完成' : busy ? '关闭' : '取消';
    $('#impResult').hidden = !complete;
    if (complete) {
      $('#impStep1').style.display = 'none'; $('#impStep2').style.display = 'none'; $('#impStats').textContent = '';
      $('#impResultTitle').textContent = session.result.feedback || session.result.table_summary ? '自动处理完成' : '导入完成';
      $('#impResultText').textContent = session.result.feedback
        ? `已保存 ${fmtCount(session.result.feedback.record_total)} 条记录 · 新增流水记录 ${fmtCount(session.result.ok)} 条 · 重复 ${fmtCount(session.result.rows_dup || 0)} 条 · ${feedbackCounts(session.result.feedback)}`
        : session.result.table_summary ? `已保存 ${fmtCount(session.result.table_summary.record_total)} 条记录 · 新增流水记录 ${fmtCount(effectiveAdded(session.result))} 条 · 重复 ${fmtCount(session.result.rows_dup || 0)} 条 · 未计入资金流水 ${fmtCount(session.result.table_summary.unresolved)} 条 · 已配对冲正/撤销 ${fmtCount(session.result.table_summary.cancelled ?? 0)} 条（双方均不计有效资金） · 无资金变动 ${fmtCount(session.result.table_summary.non_monetary)} 条 · 非交易记录 ${fmtCount(session.result.table_summary.non_transaction)} 条 · 辅助资料 ${fmtCount(session.result.table_summary.auxiliary)} 条`
          : `新增流水记录 ${fmtCount(session.result.ok)} 条 · 重复 ${fmtCount(session.result.rows_dup || 0)} 条 · 跳过 ${fmtCount(session.result.skipped || 0)} 行`;
    }
    const feedback = session.result?.feedback, summary = session.result?.table_summary;
    $('#impResultFeedback').hidden = !feedback && !summary;
    $('#impResultFeedback').style.textAlign = 'left'; $('#impResultFeedback').style.marginTop = '24px';
    $('#impResultFeedback').innerHTML = (feedback ? feedbackHtml(feedback, true) : summary ? tableHtml(summary, true) + (retainedFacts(session.result) ? `<p>${esc(retainedFacts(session.result))}</p>` : '') : '') + resolutionHtml(session.result?.resolution, true);
    $('#impResultDownloads').hidden = (!feedback && !summary) || !session.result?.source_id;
    $('#impDownloadOriginal').textContent = feedback ? '下载原始 ZIP' : '下载原始文件';
    $('#impDownloadReport').disabled = $('#impDownloadOriginal').disabled = !!session.downloading;
    if (session.dirty) $('#impPreviewNote').textContent = '映射已变更 · 预览待更新';
    flow(session); previewScroll?.sync();
  }

  function newSession() {
    const session = { id: ++serial, context: caseContext(), closed: false, preview: null, result: null, mappingDraft: {},
      request: 0, phase: 'file', dirty: false, errorStage: null,
      previewing: false, committing: false, polling: false, recovering: false, importId: null };
    active = session; state.imp = null;
    $('#impSelfAccts').value = ''; $('#impFile').value = ''; $('#impStats').textContent = '';
    if ($('#impDirectory')) $('#impDirectory').value = '';
    $('#impMapping').innerHTML = ''; $('#impMainSel').innerHTML = ''; $('#impFeedback').hidden = true; $('#impDownloadStatus').textContent = '';
    $('#impPreviewScroll').scrollLeft = 0; $('#impContent').scrollTop = 0;
    $('#impStep1').style.display = 'block'; $('#impStep2').style.display = 'none'; hidePbar();
    const current = state.cases.find(item => item.id === session.context.caseId);
    $('#impCaseTag').textContent = current?.name || String(session.context.caseId);
    $('#impCaseName').textContent = current?.name || '当前案件';
    $('#importModal').style.display = 'flex'; recovery(session, ''); controls(session); return session;
  }

  function open() {
    // A submitted or uncertain task retains its identity. Completion remains
    // visible until explicitly dismissed so the check transition is observable.
    if (active && inCase(active) && (pending(active) || active.result) && !active.dismissed) {
      active.closed = false; $('#importModal').style.display = 'flex'; controls(active);
      if (active.recovering) recovery(active, '进度暂不可用，导入可能仍在进行。请重新读取进度；此操作不会再次提交导入。', true);
      return;
    }
    newSession();
  }

  function close() {
    if (active) { active.closed = true; if (active.result) active.dismissed = true; }
    $('#importModal').style.display = 'none';
  }

  function restoreAccounts(selection) {
    if (!selection) return;
    if (Array.isArray(selection.self_accounts)) $('#impSelfAccts').value = selection.self_accounts.join(',');
    // [] is the user's explicit choice to mark none. Only null/missing retains
    // the preview's default, which is to select the detected source accounts.
    if (Array.isArray(selection.mark_accounts)) {
      const selected = new Set(selection.mark_accounts);
      $$('#impMainSel .mk-acct').forEach(node => { node.checked = selected.has(node.value); });
    }
  }

  async function restore() {
    if (restored) return;
    restored = true;
    const importId = navigation.get('import_id'), requestedCase = navigation.get('case_id');
    if (importId) {
      const url = new URL(window.location.href);
      if (url.searchParams.get('import_id') === importId) {
        url.searchParams.delete('import_id');
        window.history.replaceState(null, '', url);
      }
    }
    const context = caseContext(), intent = serial;
    if (importId && requestedCase !== String(context.caseId)) return;
    const current = () => isCurrentCase(context, false) && serial === intent;
    let session = null;
    try {
      const result = await request('/api/v1/cases/' + encodeURIComponent(context.caseId) + '/imports');
      if (!current()) return;
      if (result.case_id !== String(context.caseId)) throw new Error('导入记录与当前案件不匹配');
      const items = result.items.filter(item => item.case_id === String(context.caseId) && item.surface === 'legacy');
      const item = importId ? items.find(item => item.import_id === importId)
        : items.find(item => ['parsing', 'importing'].includes(item.stage)
          || item.stage === 'error' && item.error_code === 'import_interrupted');
      if (!item) {
        if (importId) { session = newSession(); recovery(session, '无法恢复这条导入记录，请在当前案件的导入记录中核对后重试。'); }
        return;
      }
      session = newSession(); session.importId = item.import_id; session.filename = item.filename;
      if (['parsing', 'importing', 'done'].includes(item.stage)) {
        session.phase = 'confirm';
        session.lease = { context: { ...context }, session, importId: item.import_id, requestPending: false, probe: null };
        importLeases.add(session.lease);
        setPbarBusy('正在读取已提交任务的进度…');
        await poll(session);
        return;
      }
      if (!item.source_available) throw new Error('原上传文件已清理或不存在，请重新选择文件');
      session.phase = 'fields'; session.previewing = true; controls(session); setPbarBusy('正在恢复原文件预览，请核对后确认导入…');
      const saved = item.request || item;
      const preview = await request('/api/import/repreview', { method: 'POST', ...jsonOpts({
        case_id: context.caseId, import_id: item.import_id, mode: saved.mode, mapping: saved.mapping }) });
      if (!visible(session)) return;
      if (preview.import_id !== item.import_id) throw new Error('预览回执与导入记录不匹配');
      session.preview = preview; session.mappingDraft = { ...preview.mapping }; session.dirty = false;
      state.imp = preview; session.filename = preview.filename; renderPreview(preview); restoreAccounts(item.request); hidePbar();
      recovery(session, preview.feedback || preview.table_summary
        ? '已恢复原文件和上次处理依据。请核对处理结果，确认后才会再次导入。'
        : '已恢复原文件和上次提交选项。请核对映射与主账号，确认后才会再次导入。');
    } catch (error) {
      if (session) {
        if (visible(session)) { hidePbar(); recovery(session, '无法恢复导入：' + text(error)); }
      } else if (current()) {
        if (importId) { session = newSession(); recovery(session, '无法恢复导入：' + text(error)); }
        else toast('无法读取导入恢复记录：' + text(error), 'err');
      }
    } finally {
      if (session && ownsView(session)) { session.previewing = false; controls(session); }
    }
  }

  async function upload(directory = false) {
    const session = active, files = Array.from($(directory ? '#impDirectory' : '#impFile').files || []);
    if (!session || !visible(session) || pending(session) || (session.previewing && session.phase !== 'file') || !files.length) return;
    // A newer file or directory selection supersedes an unfinished upload;
    // its request sequence also retires the earlier response and progress.
    const sequence = ++session.request;
    session.phase = 'file'; session.errorStage = null; session.result = null; session.dirty = false;
    session.previewing = true; session.preview = null; state.imp = null;
    controls(session); recovery(session, ''); setPbarBusy('正在上传并解析文件…');
    const current = () => visible(session) && sequence === session.request;
    try {
      const result = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/import/' + (directory ? 'directory/' : '') + 'preview?case_id=' + encodeURIComponent(session.context.caseId));
        xhr.upload.onprogress = event => {
          if (!current() || !event.lengthComputable) return;
          if (event.loaded >= event.total) setPbarBusy('上传完成，正在解析文件…');
          else setPbar(event.loaded / event.total, `上传文件 ${Math.round(event.loaded / event.total * 100)}%`);
        };
        xhr.onload = () => {
          let result; try { result = JSON.parse(xhr.responseText); } catch (_) { /* invalid receipt */ }
          if (xhr.status === 200 && result) resolve(result);
          else reject(new Error(result?.detail || `解析失败（HTTP ${xhr.status}）`));
        };
        xhr.onerror = () => reject(new Error('网络错误，请重新选择文件'));
        xhr.onabort = () => reject(new Error('文件上传已取消，请重新选择文件'));
        const form = new FormData();
        if (directory) for (const file of files) form.append('files', file, file.webkitRelativePath || file.name);
        else form.append('file', files[0]);
        xhr.send(form);
      });
      if (!current()) return;
      session.preview = result; session.filename = result.filename; session.mappingDraft = { ...result.mapping }; state.imp = result; session.phase = 'fields'; renderPreview(result); hidePbar();
      if (result.automatic === true) {
        // Only a fresh upload response can trigger this write. Repreview and
        // progress recovery retain their explicit, task-bound continuation.
        session.previewing = false;
        await commit();
      }
    } catch (error) {
      if (current()) { session.errorStage = 'file'; hidePbar(); recovery(session, '文件预览失败：' + text(error) + '。可重新选择文件。'); }
    } finally {
      if (current()) { session.previewing = false; controls(session); }
    }
  }

  function mappingChanged(modeChanged = false) {
    const session = active;
    if (!session || !visible(session) || !session.preview || session.preview.feedback || session.preview.table_summary || pending(session) || session.previewing) return;
    session.dirty = true; session.errorStage = null; session.phase = 'fields';
    const { mode } = collectMapping();
    $$('#impMapping select').forEach(node => { session.mappingDraft[node.dataset.field] = node.value; });
    if (modeChanged) {
      const marked = new Set($$('#impMainSel .mk-acct').filter(node => node.checked).map(node => node.value));
      renderImport({ ...session.preview, mode, mapping: session.mappingDraft });
      $$('#impMainSel .mk-acct').forEach(node => { node.checked = marked.has(node.value); });
    } else if (typeof refreshImportMappingState === 'function') refreshImportMappingState();
    recovery(session, ''); controls(session);
  }

  async function repreview() {
    const session = active;
    if (!session || !visible(session) || !session.preview || session.preview.feedback || session.preview.table_summary || pending(session) || session.previewing) return;
    const sequence = ++session.request, { mode, mapping } = collectMapping();
    const marked = new Map($$('#impMainSel .mk-acct').map(node => [node.value, node.checked]));
    session.phase = 'fields'; session.errorStage = null; session.dirty = true; session.previewing = true;
    controls(session); recovery(session, ''); setPbarBusy('正在重新识别字段…');
    try {
      const result = await request('/api/import/repreview', { method: 'POST', ...jsonOpts({
        import_id: session.preview.import_id, mode, mapping, case_id: session.context.caseId }) });
      if (!visible(session) || sequence !== session.request) return;
      session.preview = result; session.mappingDraft = { ...session.mappingDraft, ...result.mapping };
      session.dirty = false; state.imp = result; renderPreview(result);
      $$('#impMainSel .mk-acct').forEach(node => { if (marked.has(node.value)) node.checked = marked.get(node.value); });
    } catch (error) {
      if (visible(session) && sequence === session.request) {
        session.errorStage = 'fields'; recovery(session, '重新识别失败：' + text(error) + '。请检查映射后重试。');
      }
    } finally {
      if (ownsView(session) && sequence === session.request) { session.previewing = false; hidePbar(); controls(session); }
    }
  }

  async function poll(session) {
    if (session.polling || !inCase(session)) return;
    session.polling = true; session.recovering = false; session.errorStage = null; session.phase = 'confirm';
    recovery(session, ''); controls(session);
    try {
      while (inCase(session)) {
        const progress = await request('/api/import/progress?import_id=' + encodeURIComponent(session.importId)
          + '&case_id=' + encodeURIComponent(session.context.caseId));
        releaseTerminal(session.lease, progress);
        if (!inCase(session)) return;
        if (progress.stage === 'done') {
          const result = progress.result;
          if (!result || result.ok == null) throw new Error('导入完成回执不完整，请重新读取进度');
          session.result = result; session.preview = null;
          let message = result.feedback
            ? `自动处理完成：已保存 ${fmtCount(result.feedback.record_total)} 条记录 · 新增流水记录 ${fmtCount(result.ok)} 条 · 重复 ${fmtCount(result.rows_dup || 0)} 条 · ${feedbackCounts(result.feedback)}`
            : result.table_summary ? `自动处理完成：新增流水记录 ${fmtCount(effectiveAdded(result))} 条 · 重复 ${fmtCount(result.rows_dup || 0)} 条 · 未计入资金流水 ${fmtCount(result.table_summary.unresolved)} 条，原因见处理结果。`
              : `导入完成：新增流水记录 ${fmtCount(result.ok)} 条，跳过 ${fmtCount(result.skipped || 0)} 行，重复 ${fmtCount(result.rows_dup || 0)} 条`;
          if (result.main_detected?.length) message += result.table_summary ? `已标记本方主账号 ${fmtCount(result.main_detected.length)} 个。` : `；已标记本方主账号：${result.main_detected.map(item => item.name || item.account.slice(-6)).join('、')}`;
          if (visible(session)) { hidePbar(); $('#impResultText').textContent = message; controls(session); }
          toast(message);
          // Refresh failures cannot relabel a durable completed import as failed.
          try { if(window.FundsAppNavigation) await window.FundsAppNavigation.dataChanged(); else await refreshAll(); } catch (_) { if (visible(session)) recovery(session, '导入已完成，列表刷新失败。关闭后可重新查询。'); }
          return;
        }
        if (progress.stage === 'error') {
          session.errorStage = 'confirm';
          if (visible(session)) { hidePbar(); recovery(session, '导入失败：' + (progress.error || '任务未完成') + '。请检查文件或映射后，手动确认重试。'); }
          if (inCase(session)) toast(progress.error || '导入失败', 'err'); return;
        }
        if (visible(session)) {
          if (progress.stage === 'importing' && progress.total) setPbar(progress.done / progress.total, `写入流水 ${fmtCount(progress.done)} / ${fmtCount(progress.total)} 笔`);
          else setPbarBusy(progress.message || '正在解析文件…');
        }
        await new Promise(resolve => setTimeout(resolve, 300));
      }
    } catch (error) {
      if (!inCase(session)) return;
      session.recovering = error.status !== 404;
      if (!session.recovering) session.errorStage = 'confirm';
      if (visible(session)) { hidePbar(); recovery(session, session.recovering
        ? '进度读取失败：' + text(error) + '。导入可能仍在进行，请重新读取进度；不会再次提交导入。'
        : '未找到已启动的导入任务：' + text(error) + '。请检查文件或映射后，手动确认重试。', session.recovering); }
    } finally { session.polling = false; releaseSettledLease(session.lease); controls(session); }
  }

  async function commit() {
    const session = active;
    if (!session || !visible(session) || !session.preview || session.dirty || (!session.preview.feedback && !session.preview.table_summary && session.preview.stats?.ok === 0) || pending(session) || session.previewing) return;
    if (window.FundsDataToolsActions?.blocked(session.context) || window.FundsMainAccountsActions?.blocked(session.context)) {
      recovery(session, '该案件的资料更新尚未结束。请先完成或核对数据工具、主账号标记操作，再提交流水导入。');
      return;
    }
    // Automatic sources must retain every mapping admitted by their preview,
    // including source-only fields absent from the historical mapping matrix.
    const { mode, mapping } = session.preview.feedback || session.preview.table_summary ? session.preview : collectMapping();
    const payload = { import_id: session.preview.import_id, mode, mapping, case_id: session.context.caseId,
      self_accounts: $('#impSelfAccts').value.trim(),
      mark_accounts: $$('#impMainSel .mk-acct').filter(node => node.checked).map(node => node.value).join(',') };
    session.committing = true; session.phase = 'confirm'; session.errorStage = null; session.importId = payload.import_id;
    const lease = session.lease = { context: { ...session.context }, session, importId: payload.import_id, requestPending: true, probe: null };
    importLeases.add(lease);
    controls(session); recovery(session, ''); setPbarBusy('正在提交导入…');
    try {
      const result = await request('/api/import/commit', { method: 'POST', ...jsonOpts(payload) });
      lease.requestPending = false;
      if (!inCase(session)) return;
      if (result.import_id !== session.importId) throw new Error('导入回执不匹配，请重新读取进度确认');
      await poll(session);
    } catch (error) {
      lease.requestPending = false;
      if (error.status >= 400 && error.status < 500) importLeases.delete(lease);
      if (!inCase(session)) return;
      session.recovering = !error.status || error.status >= 500;
      if (!session.recovering) session.errorStage = 'confirm';
      if (visible(session)) { hidePbar(); recovery(session, session.recovering
        ? '导入提交结果暂无法确认：' + text(error) + '。请重新读取进度；不会再次提交导入。'
        : '导入未提交：' + text(error) + '。请检查文件或映射后重试。', session.recovering); }
    } finally { lease.requestPending = false; session.committing = false; releaseSettledLease(lease); controls(session); }
  }

  function bindDetailRequests() {
    let drawerRequest = 0, drawerBinding = null;
    const stale = {};
    const implicitFocus = node => !node || node === document.body || node === document.documentElement || node.isConnected === false;
    function captureDrawerFocus(binding) {
      const focused = document.activeElement;
      if ($('#drawer').contains(focused)) return { id: focused.id, node: focused };
      return implicitFocus(focused) ? binding?.focusIntent || null : null;
    }
    function restoreDrawerFocus(focus) {
      if (!focus || !implicitFocus(document.activeElement)) return;
      const match = focus.id ? document.getElementById(focus.id) : null;
      const target = match && $('#drawer').contains(match) && !match.disabled ? match : $('#btnCloseDrawer');
      target?.focus();
    }
    const originalApi = api;
    api = async function (path, options = {}) {
      // These GETs belong exclusively to the existing node/edge detail views.
      // All other requests retain their original adapter and feedback contract.
      const detail = /^\/api\/accounts\/[^/]+\/profile\?/.test(path)
        || path.startsWith('/api/main-accounts?')
        || (path.startsWith('/api/flows?') && /[?&]ids=/.test(path));
      if (!detail || (options.method && options.method !== 'GET')) return originalApi(path, options);
      const token = drawerRequest, context = caseContext();
      let result;
      try { result = await request(path, options); }
      catch (error) {
        if (token !== drawerRequest || !isCurrentCase(context)) throw stale;
        throw error;
      }
      if (token !== drawerRequest || !isCurrentCase(context)) throw stale;
      if (/^\/api\/accounts\/[^/]+\/profile\?/.test(path) && drawerBinding?.token === token) drawerBinding.profile = result;
      return result;
    };

    function bindNodeActions(binding) {
      // The account workspace owns its writes, draft and photo lock together.
      if (typeof nodeWorkspace !== 'undefined' && nodeWorkspace) return;
      const { context, account, item, profile } = binding;
      const current = () => binding === drawerBinding && binding.token === drawerRequest && isCurrentCase(context);
      const node = state.lastGraph.nodes.find(value => value.account === account) || {};
      const selectors = ['#ndFocusApply', '#ndIdSave', '#ndPhotoClr', '#ndToggleMain'];
      const buttons = selectors.map(selector => $(selector));
      const write = (path, body, method = 'POST') => request(path, { method,
        ...jsonOpts({ case_id: context.caseId, account, ...body }) });

      function action(selector, failure, run) {
        $(selector).onclick = async () => {
          if (!current() || binding.busy) return;
          binding.busy = true;
          binding.focusIntent = captureDrawerFocus(binding);
          const disabled = buttons.map(button => button.disabled);
          buttons.forEach(button => { button.disabled = true; });
          try { await run(current); }
          catch (error) { if (current()) toast(failure + '：' + text(error), 'err'); }
          finally {
            if (current()) {
              binding.busy = false;
              buttons.forEach((button, index) => { button.disabled = disabled[index]; });
            }
          }
        };
      }

      action('#ndFocusApply', '设置失败', async current => {
        const scale = Number($('#ndFocusScale').value) || 1;
        const result = await write('/api/focus-nodes/set', { on: scale > 1, scale });
        if (!current()) return;
        toast(result.on ? `该节点已重点展示（放大 ${result.scale} 倍）` : '已取消重点展示，节点恢复正常大小');
        state.forceRebuild = true;
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndIdSave', '保存失败', async current => {
        const value = $('#ndIdInput').value.trim();
        await write('/api/account-ids/set', { id_no: value });
        if (!current()) return;
        const graphNode = state.lastGraph.nodes.find(value => value.account === account);
        if (graphNode) graphNode.id_no = value || null;
        toast(value ? '身份证号已保存并更新到图谱' : '已清除该账号身份证号');
        renderGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndPhotoClr', '清除失败', async current => {
        await write('/api/account-photos/clear', {});
        if (!current()) return;
        toast('已清除照片，节点恢复圆点');
        state.forceRebuild = true;
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndToggleMain', '主账号标记失败', async current => {
        const isMain = node.is_main === 1, name = profile?.name || node.name || '';
        await write('/api/main-accounts', { name }, isMain ? 'DELETE' : 'POST');
        if (!current()) return;
        toast(isMain ? '已取消主账号标记' : '已标记为主账号，同户名的其他账号也会自动带标');
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
    }

    for (const [name, original] of [['showNodeDrawer', showNodeDrawer], ['showEdgeDrawer', showEdgeDrawer]]) {
      const wrapped = async function (...args) {
        if (name === 'showNodeDrawer') args[1] = state.graph?.findById?.(args[0]) || args[1];
        const previous = drawerBinding;
        const focus = name === 'showNodeDrawer' && previous?.account === args[0]
          && isCurrentCase(previous.context)
          && (previous.token === drawerRequest || previous.preserveFocus && implicitFocus(document.activeElement))
          ? captureDrawerFocus(previous) : null;
        if (previous) previous.preserveFocus = false;
        const token = ++drawerRequest, context = caseContext();
        const binding = drawerBinding = { token, context, account: args[0], item: args[1], profile: null, busy: false,
          focusIntent: focus, preserveFocus: false };
        try {
          const result = await original.apply(this, args);
          if (name === 'showNodeDrawer' && token === drawerRequest && isCurrentCase(context)) {
            bindNodeActions(binding); restoreDrawerFocus(focus);
          }
          return result;
        }
        catch (error) {
          if (error === stale || token !== drawerRequest || !isCurrentCase(context)) return;
          $('#drawerBody').textContent = '详情加载失败：' + text(error) + '。请再次查看详情重试。';
        }
      };
      if (name === 'showNodeDrawer') showNodeDrawer = wrapped; else showEdgeDrawer = wrapped;
    }
    const originalClose = closeDrawer;
    closeDrawer = function (...args) {
      const binding = drawerBinding;
      if (binding) {
        const preserve = args[0] === false && binding.token === drawerRequest && isCurrentCase(binding.context);
        binding.focusIntent = preserve ? captureDrawerFocus(binding) : null;
        // A graph redraw removes the focused control before the same account is
        // reopened. Only that next request may carry its implicit blur forward.
        binding.preserveFocus = preserve && !!binding.focusIntent;
      }
      drawerRequest += 1;
      return originalClose.apply(this, args);
    };
    $('#btnCloseDrawer').onclick = () => {
      closeDrawer(false, { keepSelection: true });
      $('#graphContainer').focus({ preventScroll: true });
    };

    const originalContext = loadContext;
    loadContext = async function (...args) {
      if (!state.ctx || state.ctx.closed || state.ctx.phase === 'tracking') return;
      const context = caseContext(), selection = state.ctx = { ...state.ctx };
      try { return await originalContext.apply(this, args); }
      catch (error) {
        if (!contextIsCurrent(selection, context)) return;
        selection.phase = 'error';
        const row = document.createElement('tr'), cell = document.createElement('td');
        cell.colSpan = 8; cell.className = 'context-empty'; cell.textContent = '流水明细加载失败：' + text(error) + '。';
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'btn small'; retry.textContent = '重新加载明细';
        retry.onclick = () => { if (contextIsCurrent(selection, context)) return loadContext(); };
        cell.append(retry); row.append(cell); $('#ctxTbody').replaceChildren(row);
        $('#ctxPos').textContent = '流水明细读取失败';
        $('#ctxSuggest').textContent = '匹配线索暂不可用';
        syncContextSelection();
      }
    };

    const status = $('#ctxTrackStatus');
    $('#ctxTrack').onclick = async () => {
      const selection = state.ctx, context = selection?.context || caseContext();
      if (!contextIsCurrent(selection, context) || selection.phase !== 'ready' || selection.submission) return;
      const ids = $$('#ctxTbody .ctx-ck').filter(node => node.checked && !node.disabled).map(node => Number(node.dataset.id));
      if (!ids.length) { syncContextSelection(); return; }
      const submission = selection.submission = {};
      const current = () => contextIsCurrent(selection, context) && selection.submission === submission;
      selection.phase = 'tracking'; status.textContent = '正在上图…'; syncContextSelection();
      try {
        const applied = await trackFlows(ids);
        if (!current()) return;
        if (applied === true) { closeContext(); }
        else status.textContent = '本次上图未完成，已保留勾选。请等待当前画布操作完成后重试。';
      } catch (error) {
        if (current()) status.textContent = '上图失败：' + text(error) + '。勾选已保留，可重试。';
      } finally {
        if (current()) {
          selection.phase = 'ready'; selection.submission = null; syncContextSelection();
          if (document.activeElement === document.body) $('#ctxTrack').focus();
        }
      }
    };
  }

  window.addEventListener('DOMContentLoaded', () => {
    const notice = document.createElement('div');
    notice.id = 'impRecovery'; notice.className = 'import-recovery'; notice.hidden = true;
    notice.setAttribute('role', 'status');
    const message = document.createElement('span'); message.id = 'impRecoveryText';
    const retry = document.createElement('button'); retry.id = 'impRetryProgress'; retry.type = 'button';
    retry.className = 'btn small'; retry.textContent = '重新读取进度'; retry.hidden = true;
    retry.onclick = () => { if (active?.recovering) return poll(active); };
    notice.append(message, retry); $('#impPbar').after(notice);
    $('#btnImport').onclick = open; $('#btnImport2').onclick = open;
    $('#impClose').onclick = close; $('#impCancel').onclick = close;
    $('#impFile').onchange = () => upload(false);
    if ($('#impDirectory')) $('#impDirectory').onchange = () => upload(true);
    $('#impRe').onclick = repreview; $('#impCommit').onclick = commit;
    const reportButton = $('#impDownloadReport'), originalButton = $('#impDownloadOriginal');
    if (reportButton) reportButton.onclick = () => downloadResult('report');
    if (originalButton) originalButton.onclick = () => downloadResult('original');
    $('#impChangeFile').onclick = () => { if (active && !pending(active) && !active.previewing) { newSession(); $('#impFile').click(); } };
    $('#impMapping').addEventListener('change', () => mappingChanged());
    $$('input[name=impMode]').forEach(input => { input.onchange = () => mappingChanged(true); });
    if (typeof bindTableHorizontalScroll === 'function') previewScroll = bindTableHorizontalScroll({
      viewport: $('#impPreviewScroll'), range: $('#impHorizontalPosition'), left: $('#impScrollLeft'), right: $('#impScrollRight'), table: $('#impPreviewTable') });
    // The shared modal layer owns focus, Escape and genuine backdrop gestures.
    bindDetailRequests();
  });
})();
