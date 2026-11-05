const roles: Record<string, string> = {
  source_transaction_id: '主交易标识', independent_voucher_reference: '独立凭证', independent_voucher_type: '凭证种类', row_time_role_declaration: '本行来源的时间角色声明',
  trade_no: '交易编号候选', amount: '交易金额', direction: '收支方向', summary: '摘要', remark: '备注',
  source_slip_reference: '传票号', source_log_reference: '日志号',
  self_account: '本方账号', counter_account: '对手账号', account: '账号', card: '卡号',
};
const eventFields: Record<string, string> = { reference: '跨账簿事件参考号', issuer: '参考号机构', channel: '参考号渠道', business_scope: '参考号业务域' };
type PostingPairProof = {
  source_primary_reference: string; source_counterparty_bank: string; member_path: string; member_sha256: string;
  sides: { own_account: string; source_direction: string; before: string; after: string; difference: string; source_row: number; previous_source_row: number }[];
};
type SinglePostingProof = {
  source_primary_reference: string; own_account: string; own_card: string; currency: string; amount: string;
  member_path: string; member_sha256: string; source_rows: number[];
  balance_change: { before: string; after: string; difference: string; previous_source_row: number; basis: string };
};

export function SourceFieldEvidence({ details }: { details?: Record<string, string> }) {
  let times: { role: string; column: string; column_index: number; raw: string; wall_clock: string; offset: string | null; utc: string | null }[] = [];
  try {
    const value = JSON.parse(details?.source_time_evidence || 'null');
    if (value?.policy === 'source-time-roles-v1' && Array.isArray(value.values)) times = value.values.filter((item: typeof times[number]) =>
      item && ['transaction', 'posting', 'receipt', 'settlement', 'unspecified'].includes(item.role) && typeof item.column === 'string' && Number.isInteger(item.column_index)
      && typeof item.raw === 'string' && typeof item.wall_clock === 'string' && (item.offset === null || typeof item.offset === 'string')
      && (item.utc === null || typeof item.utc === 'string'));
  } catch { /* Historical records may have no time-role evidence. */ }
  let items: { role: string; column: string; column_index: number; raw: string }[] = [];
  try {
    const parsed: unknown = JSON.parse(details?.source_field_evidence || '[]');
    if (Array.isArray(parsed)) items = parsed.filter(item => item && typeof item.role === 'string'
      && typeof item.column === 'string' && Number.isInteger(item.column_index) && typeof item.raw === 'string');
  } catch { /* Old records may not have the new source excerpts. */ }
  let declaration: Record<string, string> | null = null;
  try {
    const value = JSON.parse(details?.event_reference_evidence || 'null');
    if (value && !Array.isArray(value) && value.policy === 'scoped-event-reference-v1' && Object.keys(value).length === 5
      && Object.keys(eventFields).every(key => typeof value[key] === 'string' && value[key].trim() === value[key]
        && value[key].length > 0 && Array.from(value[key]).length <= 128)) declaration = value;
  } catch { /* Missing or damaged historical declarations convey no claim. */ }
  let pair: PostingPairProof | null = null;
  try {
    const value = JSON.parse(details?.current_bank_posting_pair_proof || 'null');
    if (value?.policy === 'bank-feedback31-paired-postings-v1'
      && ['source_primary_reference', 'source_counterparty_bank', 'member_path', 'member_sha256'].every(key => typeof value[key] === 'string')
      && Array.isArray(value.sides) && value.sides.length === 2 && value.sides.every((side: PostingPairProof['sides'][number]) => side
        && ['own_account', 'source_direction', 'before', 'after', 'difference'].every(key => typeof side[key as keyof typeof side] === 'string')
        && Number.isInteger(side.source_row) && side.source_row > 0 && Number.isInteger(side.previous_source_row) && side.previous_source_row > 0)) pair = value;
  } catch { /* Only the current, complete proof is displayed. */ }
  const pendingPair = Boolean(details?.current_bank_posting_pair_pending);
  let single: SinglePostingProof | null = null;
  try {
    const value = JSON.parse(details?.current_bank_single_posting_proof || 'null');
    if (value?.policy === 'independent-native31-single-posting-v3'
      && ['source_primary_reference', 'own_account', 'own_card', 'currency', 'amount', 'member_path', 'member_sha256'].every(key => typeof value[key] === 'string')
      && Array.isArray(value.source_rows) && value.source_rows.length === 1 && Number.isInteger(value.source_rows[0]) && value.source_rows[0] > 0
      && value.balance_change?.basis === 'unique_preceding_source_time'
      && ['before', 'after', 'difference'].every(key => typeof value.balance_change[key] === 'string')
      && Number.isInteger(value.balance_change.previous_source_row) && value.balance_change.previous_source_row > 0) single = value;
  } catch { /* Missing or damaged current proof conveys no confirmation. */ }
  const singleContext = Boolean(details?.current_bank_single_posting_proof || details?.current_bank_single_posting_pending
    || details?.current_event_reason?.startsWith('single_posting_'));
  const pendingSingle = Boolean(details?.current_bank_single_posting_pending || singleContext && pendingPair
    || details?.current_event_state === 'unknown' && details?.current_event_reason?.startsWith('single_posting_'));
  const incomparable = details?.current_event_reason === 'time_basis_incomparable';
  const conflict = details?.current_event_state === 'conflict';
  if (pendingPair || pendingSingle || conflict || incomparable) single = null;
  if (single || pendingPair || pendingSingle || conflict || incomparable) pair = null;
  if (!items.length && !declaration && !pair && !single && !pendingPair && !pendingSingle && !times.length && !details?.source_time_evidence_limited && !details?.source_field_evidence_limited && !conflict && !incomparable) return null;
  return <details><summary>来源字段与校验依据</summary>{pendingPair && !singleContext && <p>双侧来源存在对应线索，关联依据不足；资金笔数和金额待核对。原流水、各自余额与资金范围保留。</p>}{pendingSingle && <p>独立过账证据尚不足；原始记录和余额保留，不据此确认资金交易笔数。</p>}{incomparable && <p>有区与未标时区的来源时间不可比较，尚不能确定是否同一笔；两份来源原值保留，累计金额待核对。</p>}{conflict && <p>{singleContext ? '当前过账证据存在矛盾，暂不计入有效资金统计；原始记录与原值保留。' : '同笔关联资料存在矛盾，暂不计入有效资金统计；原始记录与原值保留。'}</p>}<ul>{items.map((item, index) => <li key={index}>
    {roles[item.role] || item.role} · 第 {item.column_index} 列「{item.column}」 · 原值 <code>{JSON.stringify(item.raw)}</code>
  </li>)}</ul>{times.length > 0 && <><p>来源时间与角色；无偏移的时间不推定设备时区，同一瞬间不证明同笔交易。</p><ul>{times.map((item, index) => <li key={index}>
    {item.role === 'posting' ? '记账时间' : item.role === 'receipt' ? '受理时间' : item.role === 'settlement' ? '结算时间' : item.role === 'transaction' ? '交易时间' : '来源时间（角色未明确）'} · 第 {item.column_index} 列「{item.column}」 · 原值 <code>{JSON.stringify(item.raw)}</code>
    {' · 壁钟 '}<code>{JSON.stringify(item.wall_clock)}</code>{' · 偏移 '}{item.offset === null ? '未提供' : <code>{item.offset}</code>}
    {' · UTC瞬间 '}{item.utc === null ? '未确定' : <code>{item.utc}</code>}
  </li>)}</ul></>}{details?.source_time_evidence_limited && <p>时间字段仅显示部分原值，完整记录见原始文件。</p>}{declaration && <><p>来源声明的同笔关联依据</p><dl>{Object.entries(eventFields).map(([key, label]) => <div key={key}><dt>{label}</dt><dd><code>{JSON.stringify(declaration[key])}</code></dd></div>)}</dl></>}{pair && <>
    <p>双侧来源过账关联依据；不证明最终结算或最终权属。</p>
    <dl><div><dt>主流水号</dt><dd><code>{JSON.stringify(pair.source_primary_reference)}</code></dd></div>
      <div><dt>来源对手开户银行</dt><dd><code>{JSON.stringify(pair.source_counterparty_bank)}</code></dd></div>
      <div><dt>证明来源成员</dt><dd><code>{JSON.stringify(pair.member_path)}</code></dd></div>
      <div><dt>成员 SHA-256</dt><dd><code>{pair.member_sha256}</code></dd></div></dl>
    <p>同一来源成员中，双方原账号互指、收付相反、同币同精确时刻同额，且各自余额变化相符；主流水号不作为通用同笔键。</p>
    <ul>{pair.sides.map(side => <li key={side.own_account}>本方 <code>{JSON.stringify(side.own_account)}</code> · 来源第 {side.source_row} 行 · 来源收付 <code>{side.source_direction}</code>
      {' · 前余额 '}<code>{side.before}</code>（来源第 {side.previous_source_row} 行）{' → 本方原余额 '}<code>{side.after}</code>{' · 客观余额差 '}<code>{side.difference}</code></li>)}</ul>
  </>}{single && <>
    <p>本方独立过账依据；仅证明本方账簿变动，不证明外部结算或最终权属。</p>
    <dl><div><dt>主流水号</dt><dd><code>{JSON.stringify(single.source_primary_reference)}</code></dd></div>
      <div><dt>本方账号</dt><dd><code>{JSON.stringify(single.own_account)}</code></dd></div>
      <div><dt>本方原卡号</dt><dd><code>{JSON.stringify(single.own_card)}</code></dd></div>
      <div><dt>原币种及金额</dt><dd><code>{single.currency} {single.amount}</code></dd></div>
      <div><dt>证明来源成员</dt><dd><code>{JSON.stringify(single.member_path)}</code></dd></div>
      <div><dt>成员 SHA-256</dt><dd><code>{single.member_sha256}</code></dd></div></dl>
    <p>来源第 {single.source_rows[0]} 行；唯一前余额来自来源第 {single.balance_change.previous_source_row} 行。
      {' 前余额 '}<code>{single.balance_change.before}</code>{' → 本方原余额 '}<code>{single.balance_change.after}</code>
      {' · 客观余额差 '}<code>{single.balance_change.difference}</code></p>
  </>}{details?.source_field_evidence_limited && <p>这里只显示部分候选字段，完整原值见原始文件。</p>}</details>;
}
