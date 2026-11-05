import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SourceFieldEvidence } from './SourceFieldEvidence';

const postingPair = {
  policy: 'bank-feedback31-paired-postings-v1', source_primary_reference: 'SYN-PAIR', source_counterparty_bank: '<SYN Bank>',
  member_path: 'source.csv', member_sha256: 'a'.repeat(64), sides: [
    { own_account: 'SYN-A', source_direction: '出', source_row: 3, previous_source_row: 2, before: '1000.00', after: '962.59', difference: '-37.41' },
    { own_account: 'SYN-B', source_direction: '进', source_row: 5, previous_source_row: 4, before: '2000.00', after: '2037.41', difference: '37.41' },
  ],
};
const singlePosting = {
  policy: 'independent-native31-single-posting-v3', source_primary_reference: 'SYN-SINGLE', own_account: '<SYN-A>', own_card: 'SYN-CARD',
  member_path: 'single.csv', member_sha256: 'b'.repeat(64), source_rows: [4], currency: 'CNY', amount: '12.34',
  balance_change: { before: '1000.00', after: '987.66', difference: '-12.34', previous_source_row: 3, basis: 'unique_preceding_source_time' },
  meaning: 'local_booked_movement_only; external_settlement_unproved',
};

describe('source role evidence', () => {
  it('shows a current single booked movement and its unique prior balance in the existing detail', () => {
    const { container } = render(<SourceFieldEvidence details={{ current_bank_single_posting_proof: JSON.stringify(singlePosting) }} />);
    for (const text of ['本方独立过账依据', '来源第 4 行', '来源第 3 行', '1000.00', '987.66', '-12.34', '不证明外部结算']) expect(container.textContent).toContain(text);
    expect(screen.getByText(JSON.stringify('<SYN-A>'))).toBeTruthy();
    expect(screen.queryByText(/双侧来源过账/)).toBeNull();
    expect(container.querySelector('syn-a')).toBeNull();
  });
  it.each<Record<string, string>>([
    { current_bank_single_posting_pending: 'single_posting_cross_book_candidate' },
    { current_event_state: 'unknown', current_event_reason: 'single_posting_primary_reused' },
    { current_event_state: 'conflict' },
  ])('suppresses an old single proof when current evidence no longer confirms it: %j', current => {
    const { container } = render(<SourceFieldEvidence details={{ current_bank_single_posting_proof: JSON.stringify(singlePosting), ...current }} />);
    expect(container.textContent).not.toContain('本方独立过账依据');
    expect(container.textContent).not.toContain('987.66');
    expect(container.textContent).not.toContain('双侧来源');
    expect(container.textContent).toContain('来源字段与校验依据');
  });
  it('removes withdrawn single proof and ignores malformed balance coordinates', () => {
    const { container, rerender } = render(<SourceFieldEvidence details={{ current_bank_single_posting_proof: JSON.stringify(singlePosting) }} />);
    expect(container.textContent).toContain('本方独立过账依据');
    rerender(<SourceFieldEvidence details={{}} />);
    expect(container.textContent).toBe('');
    rerender(<SourceFieldEvidence details={{ current_bank_single_posting_proof: JSON.stringify({ ...singlePosting, source_rows: [null] }) }} />);
    expect(container.textContent).toBe('');
  });
  it('shows unresolved bilateral linkage without reusing an earlier proof or a time reason', () => {
    const { container } = render(<SourceFieldEvidence details={{ current_bank_posting_pair_pending: 'bank_pair_independent_balance_witness_missing',
      current_bank_posting_pair_proof: JSON.stringify(postingPair) }} />);
    expect(container.textContent).toContain('资金笔数和金额待核对');
    expect(container.textContent).toContain('原流水、各自余额与资金范围保留');
    expect(screen.queryByText('主流水号')).toBeNull();
    expect(screen.queryByText(/有区与未标时区/)).toBeNull();
    expect(screen.queryByText(/双侧来源过账关联依据/)).toBeNull();
  });
  it('shows current bilateral balance proof with source roles and exact source rows', () => {
    const { container } = render(<SourceFieldEvidence details={{ current_bank_posting_pair_proof: JSON.stringify(postingPair) }} />);
    for (const text of ['主流水号', '来源对手开户银行', '证明来源成员', '成员 SHA-256', '"SYN-PAIR"', '"<SYN Bank>"', '1000.00', '962.59', '-37.41', '2000.00', '2037.41', '37.41']) expect(screen.getByText(text)).toBeTruthy();
    for (const text of ['来源第 3 行', '来源第 2 行', '来源第 5 行', '来源第 4 行', '不证明最终结算或最终权属']) expect(container.textContent).toContain(text);
    for (const label of ['参考号机构', '参考号渠道', '参考号业务域', '跨账簿事件参考号']) expect(screen.queryByText(label)).toBeNull();
    expect(container.querySelector('syn')).toBeNull();
  });
  it('removes withdrawn current proof while retaining the source primary descriptor', () => {
    const descriptor = { bank_posting_pair_evidence: JSON.stringify({ policy: postingPair.policy,
      source_primary_reference: 'SYN-PAIR', source_counterparty_bank: 'SYN Bank', independent_references: false }) };
    const { container, rerender } = render(<SourceFieldEvidence details={{ ...descriptor, current_bank_posting_pair_proof: JSON.stringify(postingPair) }} />);
    expect(screen.getByText('主流水号')).toBeTruthy();
    rerender(<SourceFieldEvidence details={descriptor} />);
    expect(container.textContent).toBe('');
  });
  it.each(['null', '{"policy":"bank-feedback31-paired-postings-v1","sides":[null,null]}'])('ignores incomplete current pair proof: %s', value => {
    const { container } = render(<SourceFieldEvidence details={{ current_bank_posting_pair_proof: value }} />);
    expect(container.textContent).toBe('');
  });
  it('keeps generic source time roles explicitly unspecified', () => {
    render(<SourceFieldEvidence details={{ source_time_evidence: JSON.stringify({ policy: 'source-time-roles-v1', values: [
      { role: 'unspecified', column: 'date', column_index: 1, raw: '2026-02-03', wall_clock: '2026-02-03', offset: null, utc: null },
    ] }) }} />);
    expect(screen.getByText(/来源时间（角色未明确）/)).toBeTruthy();
  });
  it('shows a source limit even when no time excerpt fits', () => {
    render(<SourceFieldEvidence details={{ source_time_evidence_limited: 'true' }} />);
    expect(screen.getByText('时间字段仅显示部分原值，完整记录见原始文件。')).toBeTruthy();
  });
  it('shows independent time roles and explicit instants without making an event claim', () => {
    const { container } = render(<SourceFieldEvidence details={{ source_time_evidence: JSON.stringify({
      policy: 'source-time-roles-v1', ordinary_time_role: null, values: [
        { role: 'transaction', column: '交易时间', column_index: 1, raw: '<SYN-TIME>', wall_clock: '2026-02-03T00:10:00.123456', offset: '+08:00', utc: '2026-02-02T16:10:00.123456Z' },
        { role: 'posting', column: '记账时间', column_index: 2, raw: '2026-02-04 01:02:03', wall_clock: '2026-02-04 01:02:03', offset: null, utc: null },
      ],
    }) }} />);
    expect(screen.getByText('2026-02-02T16:10:00.123456Z')).toBeTruthy();
    expect(screen.getByText('+08:00')).toBeTruthy();
    expect(screen.getByText(JSON.stringify('<SYN-TIME>'))).toBeTruthy();
    expect(container.textContent).toContain('无偏移的时间不推定设备时区');
    expect(container.textContent).toContain('记账时间');
    expect(container.querySelector('syn-time')).toBeNull();
  });

  it.each(['null', '[]', '{"policy":"other","values":[]}', '{"policy":"source-time-roles-v1","values":[null]}'])('ignores damaged time evidence: %s', value => {
    const { container } = render(<SourceFieldEvidence details={{ source_time_evidence: value }} />);
    expect(container.textContent).toBe('');
  });

  it.each([128, 129])('uses Unicode source values with a 128-code-point limit: %s', length => {
    const reference = 'X' + '😀'.repeat(length - 1);
    const { container } = render(<SourceFieldEvidence details={{ event_reference_evidence: JSON.stringify({
      policy: 'scoped-event-reference-v1', reference, issuer: 'SYN Issuer', channel: 'SYN Rail', business_scope: 'SYN Scope' }) }} />);
    if (length === 128) expect(screen.getByText(JSON.stringify(reference))).toBeTruthy();
    else expect(container.textContent).toBe('');
  });

  it.each([undefined, 'conflict'])('shows source-declared event scope separately from primary and voucher identifiers: %s', state => {
    render(<SourceFieldEvidence details={{
      source_field_evidence: JSON.stringify([{ role: 'source_transaction_id', column: '交易流水号', column_index: 1, raw: 'SYN-PRIMARY' },
        { role: 'independent_voucher_reference', column: '凭证号', column_index: 9, raw: 'SYN-VOUCHER' }]),
      event_reference_evidence: JSON.stringify({ policy: 'scoped-event-reference-v1', reference: 'SYN-HOLD-E1',
        issuer: 'Synthetic Holdout Issuer', channel: 'Synthetic Holdout Rail', business_scope: 'Synthetic Holdout Transfers' }),
      ...(state ? { current_event_state: state } : {}),
    }} />);
    expect(screen.getByText('来源声明的同笔关联依据')).toBeTruthy();
    for (const label of ['跨账簿事件参考号', '参考号机构', '参考号渠道', '参考号业务域']) expect(screen.getByText(label)).toBeTruthy();
    for (const value of ['SYN-HOLD-E1', 'Synthetic Holdout Issuer', 'Synthetic Holdout Rail', 'Synthetic Holdout Transfers', 'SYN-PRIMARY', 'SYN-VOUCHER']) {
      expect(screen.getByText(JSON.stringify(value))).toBeTruthy();
    }
    expect(screen.queryByText(/已确认同笔|已确认事件/)).toBeNull();
  });

  it('shows an event declaration without requiring primary excerpts and escapes its literal values', () => {
    const value = '<img src=x onerror="SYN">';
    const { container } = render(<SourceFieldEvidence details={{ event_reference_evidence: JSON.stringify({
      policy: 'scoped-event-reference-v1', reference: value, issuer: 'SYN Issuer', channel: 'SYN Rail', business_scope: 'SYN Scope' }) }} />);
    expect(screen.getByText(JSON.stringify(value))).toBeTruthy();
    expect(container.querySelector('img')).toBeNull();
  });

  it.each(['damaged', 'null', '[]', '{"policy":"other"}', '{"policy":"scoped-event-reference-v1","reference":"SYN"}'])('ignores incomplete or unknown event declarations: %s', value => {
    const { container } = render(<SourceFieldEvidence details={{ event_reference_evidence: value }} />);
    expect(container.textContent).toBe('');
  });

  it('shows independent roles, exact source columns and visible raw whitespace safely', () => {
    render(<SourceFieldEvidence details={{ source_field_evidence: JSON.stringify([
      { role: 'source_transaction_id', column: '交易流水号', column_index: 23, raw: 'SYN-PRIMARY\t' },
      { role: 'independent_voucher_reference', column: '凭证号', column_index: 26, raw: '<script>SYN</script>\t' },
      { role: 'summary', column: '摘要说明', column_index: 14, raw: 'Synthetic memo\t' },
      { role: 'remark', column: '备注', column_index: 28, raw: 'Synthetic independent remark\t' },
      { role: 'source_log_reference', column: '日志号', column_index: 24, raw: 'SYN-LOG' },
    ]) }} />);
    expect(screen.getByText(/主交易标识 · 第 23 列/)).toBeTruthy();
    expect(screen.getByText(/独立凭证 · 第 26 列/)).toBeTruthy();
    expect(screen.getByText(/摘要 · 第 14 列/)).toBeTruthy();
    expect(screen.getByText(/备注 · 第 28 列/)).toBeTruthy();
    expect(screen.getByText(/日志号 · 第 24 列/)).toBeTruthy();
    expect(screen.getByText('"SYN-PRIMARY\\t"')).toBeTruthy();
    expect(screen.getByText('"<script>SYN</script>\\t"')).toBeTruthy();
    expect(document.querySelector('script')).toBeNull();
  });
  it('keeps missing historical evidence empty without claiming a resolution', () => {
    const { container } = render(<SourceFieldEvidence details={{ source_field_evidence: 'damaged' }} />);
    expect(container.textContent).toBe('');
  });
  it('shows a current event conflict inside the existing evidence detail', () => {
    render(<SourceFieldEvidence details={{ current_event_state: 'conflict' }} />);
    expect(screen.getByText('同笔关联资料存在矛盾，暂不计入有效资金统计；原始记录与原值保留。')).toBeTruthy();
    expect(screen.getByText('来源字段与校验依据')).toBeTruthy();
  });
  it('declares bounded excerpts without substituting for the retained original', () => {
    render(<SourceFieldEvidence details={{ source_field_evidence_limited: 'true' }} />);
    expect(screen.getByText('这里只显示部分候选字段，完整原值见原始文件。')).toBeTruthy();
  });
});

it('explains incomparable time evidence without declaring a source contradiction', () => {
  render(<SourceFieldEvidence details={{ current_event_state: 'unknown', current_event_reason: 'time_basis_incomparable' }} />);
  expect(screen.getByText(/有区与未标时区的来源时间不可比较/)).not.toBeNull();
  expect(screen.queryByText(/同笔关联资料存在矛盾/)).toBeNull();
});
