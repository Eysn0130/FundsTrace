import { useEffect, useRef, useState } from 'react';
import type { ImportColumnEvidence, ImportLayoutAccess, ImportPreview, ModelTrialResult } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, ImportModelTrialRequest } from '../panel/host';

const roles: Readonly<Record<ImportModelTrialRequest['field'], string>> = {
  self_account: '本方账号', self_card: '本方卡号', self_name: '本方户名',
  counter_account: '对方账号', counter_card: '对方卡号', counter_name: '对方户名',
  payer_account: '付款账号', payer_name: '付款户名', payee_account: '收款账号', payee_name: '收款户名',
  own_direction_amount: '本账方向与发生额', balance: '本方原报余额',
};
const findings = { supported: '支持', refuted: '反证', unknown: '未知' };

function postingRoles(value: unknown) {
  if (!value || typeof value !== 'object') return;
  const {scope, direction_column, amount_column, rows, financial_admission} = value as Record<string, unknown>;
  if (scope !== 'source_only' || financial_admission !== false || typeof direction_column !== 'number'
    || typeof amount_column !== 'number' || !Array.isArray(rows) || !rows.length) return;
  const positions: string[] = [];
  for (const row of rows) {
    if (!row || typeof row !== 'object' || typeof row.source_row !== 'number'
      || (row.direction !== 'in' && row.direction !== 'out')) return;
    positions.push(`原第 ${row.source_row} 行${row.direction === 'in' ? '入账' : '出账'}`);
  }
  return `本账方向取自原第 ${direction_column} 列，原第 ${amount_column} 列是发生额。${positions.join('、')}。`;
}

export function layoutReviewIssueMessage(reason: string | undefined, code?: string) {
  const subject = code ? `用途码 ${code} ` : '该用途码';
  switch (reason) {
    case 'use_code_wrong_scope': return `${subject}的说明未覆盖当前表区，请核对适用的工作表和起止行列。`;
    case 'use_code_missing_definition': return `请补${subject}在当前表区的原始释义。`;
    case 'use_code_conflicting_definitions': return `${subject}在当前表区有相反定义，请核对原说明并提供更正依据。`;
    default: return undefined;
  }
}

export function ImportModelTrial({ client, scopeSignal, importId, layout, memberPath, registeredDirectionReview = false, registeredBalanceReview = false, onPrepared }: {
  client: FundsCaseClientV1;
  scopeSignal: AbortSignal;
  importId: string;
  layout: ImportLayoutAccess;
  memberPath?: string;
  registeredDirectionReview?: boolean;
  registeredBalanceReview?: boolean;
  onPrepared?: (value: ImportPreview) => void;
}) {
  const initial = layout.members.find(item => item.member_path === memberPath) ?? layout.members[0];
  const [member, setMember] = useState(initial?.member_path ?? '');
  const [row, setRow] = useState(String(initial?.header_rows[0] ?? 1));
  const [start, setStart] = useState('1');
  const [role, setRole] = useState<ImportModelTrialRequest['field']>('self_account');
  const [support, setSupport] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ModelTrialResult>();
  const [columnEvidence, setColumnEvidence] = useState<ImportColumnEvidence>();
  const [error, setError] = useState('');
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(), []);

  const invalidate = () => {
    active.current?.abort(); active.current = null;
    setBusy(false); setResult(undefined); setColumnEvidence(undefined); setError('');
  };
  const memberInfo = layout.members.find(item => item.member_path === member);
  const header = Number(row), column = Number(start);
  const valid = !!memberInfo && Number.isInteger(header) && header >= 1 && header <= memberInfo.source_rows
    && Number.isInteger(column) && column >= 1;
  const canCheckPostingRoles = layout.members.length === 1 && (
    (member === 'table.csv' && memberInfo?.header_rows.length === 0)
    || (registeredDirectionReview && memberInfo?.header_rows.length === 1 && memberInfo.header_rows[0] === 1)
  );
  const sourceOnly = role === 'own_direction_amount';
  const balance = role === 'balance';
  const special = sourceOnly || balance;
  const canCheckBalance = registeredBalanceReview && layout.members.length === 1 && member === 'table.csv'
    && memberInfo?.header_rows.length === 1 && memberInfo.header_rows[0] === 1;
  const runValid = valid && (!sourceOnly || (canCheckPostingRoles && header === 1))
    && (!balance || (canCheckBalance && header === 1));
  async function sample() {
    if (!valid || !client.importColumnEvidence || scopeSignal.aborted || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setColumnEvidence(undefined); setError(''); setBusy(true);
    const current = () => active.current === controller && !controller.signal.aborted && !scopeSignal.aborted;
    try {
      const value = await client.importColumnEvidence(importId, {
        revision: layout.revision, member_path: member, header_row: header, column_start: column,
      }, controller.signal);
      if (!current()) return;
      if (value.case_id !== client.binding.caseId || value.import_id !== importId || value.revision !== layout.revision
        || value.member_path !== member || value.header_row !== header) {
        setError('表区已变化，请重新预览。');
        return;
      }
      setColumnEvidence(value);
    } catch (failure) {
      if (current()) setError(failure instanceof Error ? failure.message : '无法读取当前表区。');
    } finally {
      if (current()) { active.current = null; setBusy(false); }
    }
  }
  async function run() {
    if (!runValid || !client.importModelTrial || scopeSignal.aborted || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setResult(undefined); setError(''); setBusy(true);
    const current = () => active.current === controller && !controller.signal.aborted && !scopeSignal.aborted;
    try {
      const scope = { revision: layout.revision, member_path: member, header_row: header };
      const value = await client.importModelTrial(importId, special ? { ...scope, field: role } : {
        ...scope, field: role, column_start: column,
        ...(support && support !== member ? { support_member_path: support } : {}),
        ...(onPrepared ? { prepare_layout: true } : {}),
      }, controller.signal);
      if (!current()) return;
      if (value.packet.facts.case_id !== client.binding.caseId || value.packet.facts.import_id !== importId
        || value.packet.facts.revision !== layout.revision || value.packet.facts.member_path !== member
        || value.packet.facts.header_row !== header || value.packet.facts.field !== role
        || (!special && (value.packet.facts.column_start !== column
          || value.packet.facts.support_member_path !== (support || null)))) {
        setError('原件核对范围已变化，请重新预览。');
        return;
      }
      setResult(value);
      if (!sourceOnly && value.prepared_preview) onPrepared?.(value.prepared_preview);
    } catch (failure) {
      if (current()) setError(failure instanceof Error ? failure.message : '本地辅助暂不可用。');
    } finally {
      if (current()) { active.current = null; setBusy(false); }
    }
  }
  if (!initial) return null;
  const packet = result?.packet;
  const selected = packet?.records.find(record => record.id === packet.selection?.choice)
    ?? (result?.state === 'rule' ? packet?.records.find(record => record.structured.role_check === 'supported')
      : undefined);
  const fields = selected ? [selected.raw.heading, ...(Array.isArray(selected.raw.cells) ? selected.raw.cells : [])] : [];
  const notes = packet?.records.filter(record => packet.selection?.refs.includes(record.id) && record.id.startsWith('N')) || [];
  const checks = result?.state === 'proposal' || result?.state === 'pending' ? result.column_checks || [] : [];
  const issueMessage = layoutReviewIssueMessage(result?.reason);
  const headerRole = result?.header_role;
  const headerColumns = headerRole?.matches.map(match => `第 ${match.column} 列「${match.heading}」`).join('、');
  const posting = sourceOnly ? postingRoles(packet?.facts.source_posting_roles) : undefined;
  const status = busy ? '正在核对当前表区…' : error || (posting ? '已核对原列和原行。整区入账仍待核。'
    : sourceOnly && result?.state === 'proposal' ? '当前原列核证结果暂不可用。'
    : balance && result?.reason === 'balance_outside_mappable_layout' ? '当前表区仍按已有来源范围核对。'
    : balance && result?.reason === 'balance_not_proved' ? '原表暂未能独立核证本方余额栏，请回查账户及余额原列。'
    : result?.state === 'rule' ? '规则已有依据。'
    : result?.state === 'proposal' ? '已核证原列和说明，待确认导入映射。'
    : result?.reason === 'source_role_conflict' ? '同一表区的字段用途有冲突，请核对原说明。'
    : result?.reason === 'field_note_wrong_header_row' ? '现有说明指向数据行，请补对应当前表头原列的说明。'
    : issueMessage ? issueMessage
    : headerRole && headerColumns ? (headerRole.matches.length === 1
      ? `原表头的${roles[headerRole.field]}位于${headerColumns}。本表有效流水仍待核。`
      : `多列都声明为${roles[headerRole.field]}，请核对${headerColumns}。本表有效流水仍待核。`)
    : result?.reason === 'insufficient_evidence' ? '请补适用于当前表头的字段用途说明。'
    : result?.reason === 'layout_not_admitted' ? '已找到原列和说明，请核对其作用范围和同文件的其他表区。'
    : sourceOnly && result?.reason === 'source_posting_citations_unverified' ? '所引原列或原行未通过核证，请查看原表。'
    : sourceOnly && result?.reason === 'source_posting_scope_incomplete' ? '当前原表范围未能完整回查，字段用途待核。'
    : sourceOnly && result?.reason === 'source_posting_roles_unproved' ? '现有原表不足以核证本账方向和发生额。'
    : sourceOnly && result?.reason === 'source_posting_already_registered' ? '当前表区已登记，请查看原有字段映射。'
    : result?.state === 'pending' ? '证据不足，保留待核。'
    : result?.state === 'unavailable' ? '本机模型暂不可用，可继续手动核对。' : '');
  return <section className="ft-mapping-section" aria-label="隔离试用辅助">
    <details>
      <summary>隔离试用 · 查表头</summary>
      {layout.members.length > 1 && <label>原件表区 <select value={member} onChange={event => {
        const next = layout.members.find(item => item.member_path === event.target.value);
        invalidate(); setMember(event.target.value); setRow(String(next?.header_rows[0] ?? 1)); setStart('1'); setSupport('');
      }}>{layout.members.map(item => <option key={item.member_path} value={item.member_path}>{item.sheet || item.member_path}</option>)}</select></label>}
      <label>原表头行 <input type="number" min={1} max={memberInfo?.source_rows} value={row}
        onChange={event => { invalidate(); setRow(event.target.value); }} /></label>
      <label>起始列 <input type="number" min={1} value={start}
        onChange={event => { invalidate(); setStart(event.target.value); }} /></label>
      <label>待核字段 <select value={role} onChange={event => {
        const next = event.target.value as typeof role;
        invalidate(); setRole(next);
        if (next === 'own_direction_amount' || next === 'balance') setRow('1');
      }}>
        {Object.entries(roles).map(([key, label]) => (key !== 'own_direction_amount' || canCheckPostingRoles)
          && (key !== 'balance' || canCheckBalance)
          && <option key={key} value={key}>{label}</option>)}</select></label>
      {layout.members.length > 1 && <label>字段说明页 <select value={support} onChange={event => { invalidate(); setSupport(event.target.value); }}>
        <option value="">未指定</option>{layout.members.filter(item => item.member_path !== member)
          .map(item => <option key={item.member_path} value={item.member_path}>{item.sheet || item.member_path}</option>)}</select></label>}
      <button type="button" className="button secondary" onClick={() => void run()} disabled={!runValid || busy}>
        {sourceOnly ? '核对本账方向与发生额' : balance ? '核对本方余额原列' : '定位字段原列'}
      </button>
      {client.importColumnEvidence && <button type="button" className="button secondary" onClick={() => void sample()}
        disabled={!valid || header === memberInfo?.source_rows || busy}>查看全列统计和抽样</button>}
      <p role="status">{status}</p>
      {posting && <p>{posting}</p>}
      {columnEvidence && <section aria-label="全列统计和抽样">
        <p>{columnEvidence.coverage.complete
          ? `已检查原表第 ${columnEvidence.coverage.first_row} 至 ${columnEvidence.coverage.last_row} 行，共 ${columnEvidence.coverage.scanned_rows} 行。`
          : '当前区段未能全量检查，保留待核。'}</p>
        {columnEvidence.columns.map(item => <details key={item.column}>
          <summary>第 {item.column} 列 · {item.heading || '空表头'} · 非空 {item.nonempty} 行{item.leading_zero > 0 && `，带前导零 ${item.leading_zero} 行`}</summary>
          <ul>{item.samples.map(row => <li key={row.source_row}>第 {row.source_row} 行 · {row.value || '空'}{row.excerpt && '…'}</li>)}</ul>
        </details>)}
      </section>}
      {!!checks.length && <ul aria-label="逐列核证结果">{checks.map(check => {
        const original = packet?.records.find(record => record.id === check.column_ref);
        const note = packet?.records.find(record => record.id === check.note_ref);
        if (!original) return null;
        return <li key={check.column_ref}>第 {String(original.locator.column)} 列 · {String(original.raw.heading)} · {findings[check.finding]}
          {note && <> · {String(note.locator.sheet || note.locator.member_path)} 说明原行 {String(note.locator.source_row)}</>}
        </li>;
      })}</ul>}
      {packet?.facts.more_columns === true && <p>当前查看第 {column} 至 {column + 15} 列。还有原列可查看。</p>}
      {selected && <details open>
        <summary>原表第 {String(selected.locator.source_row ?? '—')} 行 · 第 {String(selected.locator.column ?? '—')} 列</summary>
        <p>{fields.map(field => typeof field === 'string' && field ? field : '—').join(' · ')}</p>
      </details>}
      {notes.map(note => <details key={note.id}>
        <summary>{String(note.locator.sheet || note.locator.member_path)} · 说明原行 {String(note.locator.source_row)}</summary>
        <p>{(Array.isArray(note.raw.cells) ? note.raw.cells : []).filter(cell => typeof cell === 'string' && cell).join(' · ')}</p>
      </details>)}
    </details>
  </section>;
}
