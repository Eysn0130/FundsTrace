/* Read-only presentation of the backend's evidence assessment, in existing views. */
(function (root) {
  'use strict';
  const money = () => root.FundsMoney;
  function displayCount(funds) {
    return funds?.count && funds.count.lower === funds.count.upper ? `${money().count(funds.count.lower)} 笔` : '—';
  }
  function displayTotals(funds) {
    if (!funds?.totals?.length) return funds?.status === 'exact' ? '0.00' : '—';
    return funds.totals.map(row => row.amount == null ? (row.currency ? `${row.currency} —` : '—')
      : money().currency(row.amount, row.currency)).join(' / ');
  }
  function count(funds) {
    if (!funds?.count) return '事件数待定';
    const { lower, upper } = funds.count;
    return lower === upper ? `${money().count(lower)} 个事件`
      : `${money().count(lower)}–${upper == null ? '?' : money().count(upper)} 个事件（待核）`;
  }
  function totals(funds) {
    if (!funds?.totals?.length) return funds?.status === 'exact' ? '0.00' : '金额待定';
    return funds.totals.map(row => {
      const unit = row.currency || '币种待定';
      if (row.amount != null) return money().currency(row.amount, unit);
      const lo = money().format(row.lower);
      return row.upper == null ? `${unit} ≥${lo}（上界未知）`
        : `${unit} ${lo}–${money().format(row.upper)}（待核）`;
    }).join(' / ');
  }
  function principal(value) {
    if (!value) return '';
    if (value.source_candidates?.length) {
      return '逐笔入账的独立条件份额（不认定实际来源，上界不可相加）。\n' + value.source_candidates.map(candidate =>
        `入账记录 #${candidate.source_flow_id}\n${principal(candidate)}`).join('\n');
    }
    const reasons = (value.assessment || []).filter(item => item.status !== 'established').map(item => item.message).filter(Boolean);
    const lines = [value.status === 'ok' || value.status === 'determined' ? '账面规则下的条件范围。' : '账面规则范围：—。'];
    const limitationText = {ledger_coverage_unknown: '账簿覆盖完整性未确定', credit_availability_unknown: '透支或授信条件未确定', funds_availability_unknown: '资金实际可用性未确定'};
    for (const limitation of value.limitations || []) reasons.push(limitationText[limitation] || limitation);
    if (reasons.length) lines.push([...new Set(reasons)].join('；') + '。');
    for (const item of value.targets || []) {
      const range = item.min == null || item.max == null ? '未知'
        : `${money().format(item.min)}–${money().format(item.max)}`;
      lines.push(`出账 ${money().currency(item.amount, value.currency)}：条件范围 ${range}；FIFO ${item.fifo == null ? '证据不足' : money().format(item.fifo)}。`);
    }
    if (value.targets?.length) lines.push('范围基于账面记录和规则条件；各笔上界不可相加，FIFO 是规则分配。');
    return lines.join('\n');
  }
  function tracking(detail) {
    const values = (detail || []).flatMap(item => item.principal?.source_candidates?.length
      ? item.principal.source_candidates : item.principal ? [item.principal] : []);
    const known = values.filter(value => ['ok', 'determined'].includes(value.status) && value.targets?.length);
    if (!known.length) return '';
    const targets = known.flatMap(value => value.targets.map(target => ({ ...target, currency: value.currency })));
    if (targets.length === 1) {
      const target = targets[0];
      return `；账面条件份额 ${target.currency} ${money().format(target.min)}–${money().format(target.max)}，FIFO ${money().format(target.fifo)}（规则分配，非实际来源认定）`;
    }
    return `；已计算 ${targets.length} 项独立账面条件份额，详情见原“前后流水”（各项上界不可相加）`;
  }
  root.FundsFacts = Object.freeze({ count, totals, displayCount, displayTotals, principal, tracking });
})(typeof globalThis === 'undefined' ? self : globalThis);
