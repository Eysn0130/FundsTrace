import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportPreview, ImportSession } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { ImportDialog } from './ImportDialog';

const policy = 'transaction-reference-v1' as const;
const label = '凭证号作为独立引用（不与交易流水号比较）';
const preview: ImportPreview = { case_id: '1', import_id: 'SYN-REFERENCE', filename: 'synthetic-reference.csv', mode: 'B',
  automatic: false, reference_policy_available: true, reference_policy: null,
  columns: ['交易流水号', '凭证号', '交易时间', '本方账号', '对手账号', '收支方向', '交易金额'],
  mapping: { trade_no: '交易流水号', amount: '交易金额' }, self_accounts_in_file: [],
  stats: { total: 1, ok: 0, skipped: 1 }, preview: [],
  table_summary: { profile: 'funds-trace-table-reference-policy-v1', record_total: 1, accepted: 0, unresolved: 1,
    non_monetary: 0, non_transaction: 0, auxiliary: 0, reason_counts: { conflicting_field: 1 }, currency_totals: {},
    members: [], unresolved_examples: [{ member_path: 'synthetic-reference.csv', source_row: 2, reason_code: 'conflicting_field' }] } };
const separated: ImportPreview = { ...preview, reference_policy: policy, stats: { total: 1, ok: 1, skipped: 0 },
  table_summary: { ...preview.table_summary!, accepted: 1, unresolved: 0, reason_counts: {}, currency_totals: { CNY: '12.34' } } };
function clientFor(): FundsCaseClientV1 {
  return { binding: { caseId: '1', scopeKey: 'synthetic-reference', generation: 0 }, stats: vi.fn(), flows: vi.fn(),
    preview: vi.fn(async () => preview), repreview: vi.fn(async () => separated),
    commit: vi.fn(async () => ({ case_id: '1', import_id: preview.import_id, started: true })),
    job: vi.fn(async () => ({ case_id: '1', import_id: preview.import_id, stage: 'done' as const, done: 1, total: 1,
      message: '完成', error: null, result: { total: 1, ok: 0, skipped: 1, rows_dup: 0, batch_id: 'SYN-BATCH',
        bad_examples: [], self_accounts: [], self_accounts_count: 0, main_matched: [], accounts_in_batch: 0, main_detected: [],
        table_summary: preview.table_summary } })), imports: vi.fn(), sources: vi.fn(), cleanup: vi.fn() };
}
function show(client: FundsCaseClientV1, resume?: ImportSession) {
  return render(<ImportDialog target={{ caseId: '1', name: '合成引用策略案件' }} client={client}
    scopeSignal={new AbortController().signal} resume={resume} onClose={vi.fn()} onImported={vi.fn()} />);
}
function upload() {
  fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], preview.filename)] } });
}

describe('table reference policy choice', () => {
  it('shows the supported choice before submission and lets the user continue with the default', async () => {
    const client = clientFor(); show(client); upload();
    const checkbox = await screen.findByRole('checkbox', { name: label });
    expect(checkbox.closest('details')).toBeNull();
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    expect(client.commit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '开始导入' }));
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledOnce();
    expect(client.repreview).not.toHaveBeenCalled();
  });

  it('repreviews an enabled policy and sends explicit null when the user reverses the choice', async () => {
    const client = clientFor();
    client.repreview = vi.fn().mockResolvedValueOnce(separated).mockResolvedValueOnce(preview);
    show(client); upload();
    fireEvent.click(await screen.findByLabelText(label));
    expect((screen.getByRole('button', { name: '开始导入' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect((screen.getByRole('button', { name: '开始导入' }) as HTMLButtonElement).disabled).toBe(false));
    expect(client.repreview).toHaveBeenNthCalledWith(1, preview.import_id,
      { mode: preview.mode, mapping: preview.mapping, reference_policy: policy }, expect.any(AbortSignal));
    expect((screen.getByLabelText(label) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByLabelText('表格自动识别与全量校验汇总').textContent).toContain('可靠识别流水 1 条');
    fireEvent.click(screen.getByLabelText(label));
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect((screen.getByRole('button', { name: '开始导入' }) as HTMLButtonElement).disabled).toBe(false));
    expect(client.repreview).toHaveBeenNthCalledWith(2, preview.import_id,
      { mode: preview.mode, mapping: preview.mapping, reference_policy: null }, expect.any(AbortSignal));
    expect((screen.getByLabelText(label) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByLabelText('表格自动识别与全量校验汇总').textContent).toContain('可靠识别流水 0 条');
    expect(client.commit).not.toHaveBeenCalled();
  });

  it.each(['ready', 'error'] as const)('restores the persisted active policy for a %s session without resubmitting', async stage => {
    const client = clientFor();
    const resume: ImportSession = { case_id: '1', import_id: preview.import_id, filename: preview.filename, surface: 'v1',
      mode: 'B', mapping: preview.mapping, stage, source_state: 'retained', source_available: true, recoverable: true,
      created_at: '2026-01-01T00:00:00Z', expires_at: null, error_code: null, attempt: stage === 'error' ? 1 : 0,
      request: { mode: 'B', mapping: preview.mapping, reference_policy: policy }, result: null };
    show(client, resume);
    const checkbox = await screen.findByRole('checkbox', { name: label });
    expect((checkbox as HTMLInputElement).checked).toBe(true);
    expect(client.repreview).toHaveBeenCalledWith(preview.import_id,
      { mode: 'B', mapping: preview.mapping, reference_policy: policy }, expect.any(AbortSignal));
    expect(client.commit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('映射交易金额'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect(client.repreview).toHaveBeenCalledTimes(2));
    expect(client.repreview).toHaveBeenLastCalledWith(preview.import_id,
      { mode: 'B', mapping: { ...preview.mapping, amount: '' }, reference_policy: policy }, expect.any(AbortSignal));
    expect(client.commit).not.toHaveBeenCalled();
  });

  it('hides the policy for an unsupported table preview', async () => {
    const client = clientFor(); client.preview = vi.fn(async () => ({ ...preview, reference_policy_available: false }));
    show(client); upload();
    await screen.findByRole('button', { name: '开始导入' });
    expect(screen.queryByLabelText(label)).toBeNull();
    expect(client.commit).not.toHaveBeenCalled();
  });
});
