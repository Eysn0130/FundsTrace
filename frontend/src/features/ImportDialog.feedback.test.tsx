import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { FeedbackSummary, ImportJob, ImportPreview, ImportSession } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsHostActionsV1 } from '../panel/host';
import { ImportDialog } from './ImportDialog';
import { ImportHistoryDialog } from './ImportHistoryDialog';

const feedback: FeedbackSummary = {
  profile: 'funds-trace-bank-feedback-v2', record_total: 6, transaction_total: 5, accepted: 2, review: 0, non_monetary: 1, unresolved: 1,
  non_transaction: 1, auxiliary: 1, reason_counts: { invalid_direction: 1 },
  currency_totals: { CNY: '9007199254740993.07', USD: '0.03' },
  review_examples: [], unresolved_examples: [{ member_path: 'synthetic/交易.txt', source_row: 4, reason_code: 'invalid_direction' }],
  members: [{ member_path: 'synthetic/交易.txt', role: 'transaction', sha256: 'a'.repeat(64), size_bytes: 40, encoding: 'gb18030', row_count: 5 },
    { member_path: 'synthetic/冻结.txt', role: 'freeze', sha256: 'b'.repeat(64), size_bytes: 20, encoding: 'gb18030', row_count: 1 }],
};
const preview: ImportPreview = {
  case_id: '1', import_id: 'SYN-IMPORT', filename: 'synthetic.zip', mode: 'B', columns: ['交易金额'], mapping: { amount: '交易金额' },
  self_accounts_in_file: [], stats: { total: 5, ok: 2, skipped: 3 }, feedback,
  preview: [{ trade_no: 'SYN-1', trade_time: '2026-01-01 10:00:00', payer_name: '', payer_account: 'SYN-A', payer_bank: '',
    payee_name: '', payee_account: '', payee_bank: '', amount: '9007199254740993.07', balance: null, currency: 'CNY',
    summary: '合成交易', channel: '', self_account: 'SYN-A', self_name: '合成本方',
    source_member: 'synthetic/交易.txt', source_row: 2, source_record_no: 1 }],
};
const result = (summary = feedback, ok = 2): NonNullable<ImportJob['result']> => ({ total: summary.transaction_total, ok, skipped: summary.transaction_total - summary.accepted, rows_dup: 0, batch_id: 'SYN-BATCH',
  bad_examples: [], self_accounts: [], self_accounts_count: 0, main_matched: [], accounts_in_batch: 0, main_detected: [],
  source_id: 'SYN-SOURCE', feedback: summary });
const clientFor = (value = preview): FundsCaseClientV1 => ({
  binding: { caseId: '1', scopeKey: 'synthetic', generation: 0 },
  stats: vi.fn(), flows: vi.fn(), preview: vi.fn(async () => value), repreview: vi.fn(async () => value),
  commit: vi.fn(async () => ({ case_id: '1', import_id: 'SYN-IMPORT', started: true })),
  job: vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: 'SYN-IMPORT', stage: 'done', done: value.stats.ok, total: 3, message: '完成', error: null, result: result(value.feedback!, value.stats.ok) })),
  imports: vi.fn(), sources: vi.fn(), cleanup: vi.fn(),
});
function show(client: FundsCaseClientV1, options: { signal?: AbortSignal; resume?: ImportSession; actions?: FundsHostActionsV1 } = {}) {
  return render(<ImportDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={options.signal ?? new AbortController().signal}
    resume={options.resume} actions={options.actions} onClose={vi.fn()} onImported={vi.fn()} />);
}
async function upload() {
  fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.zip', { type: 'application/zip' })] } });
  await screen.findByText('银行反馈 ZIP · 已自动识别');
}

describe('bank feedback ZIP import dialog', () => {
  it('uses the same one-upload automatic commit for fixed bank feedback profiles', async () => {
    const client = clientFor({ ...preview, automatic: true } as ImportPreview);
    show(client); fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.zip')] } });
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledOnce(); expect(client.repreview).not.toHaveBeenCalled();
    expect(client.commit).toHaveBeenCalledWith('SYN-IMPORT', { mode: 'B', mapping: preview.mapping, mark_accounts: [], self_accounts: [] }, expect.any(AbortSignal));
  });
  it('accepts ZIP and shows fixed mapping, exact currencies and original row metadata without inventing counterparties', async () => {
    const client = clientFor(); show(client);
    expect(screen.getByLabelText('选择流水文件').getAttribute('accept')).toContain('.zip');
    await upload();
    expect(screen.queryByLabelText('流水文件类型')).toBeNull();
    expect(screen.queryByText('检查字段映射')).toBeNull();
    expect(screen.getByText(/字段映射与流水模式不可修改/)).toBeTruthy();
    expect(screen.getByText('9,007,199,254,740,993.07 CNY')).toBeTruthy();
    expect(screen.getByText('0.03 USD')).toBeTruthy();
    expect(screen.getByText('收付方向无法识别：1 条')).toBeTruthy();
    expect(screen.queryByText('invalid_direction')).toBeNull();
    expect(screen.getByText('原始第 2 行 · 记录 1')).toBeTruthy();
    expect(screen.getByText('户名未提供')).toBeTruthy();
    expect(screen.getByText('未知对手')).toBeTruthy();
    expect(screen.getByText('账号缺失')).toBeTruthy();
    expect(screen.getByText('冻结信息')).toBeTruthy();
    expect(screen.getByText(/来源已接纳流水金额合计.*非本次新增金额或净结算金额/)).toBeTruthy();
    expect(screen.getByText(/自动处理会正常完成，未计入资金流水的记录会保留来源和原因，无需逐条确认/)).toBeTruthy();
    expect(screen.getByText(/未计入不代表金额为零或没有发生交易/)).toBeTruthy();
    expect(screen.getByText('未计入记录的来源示例（前 1 条）').closest('details')?.open).toBe(false);
    expect(screen.queryByText(feedback.profile)).toBeNull();
    expect(screen.queryByText(/待复核|需复核|保存处置结果/)).toBeNull();
    expect(screen.queryByText(/跳过/)).toBeNull();
    expect(client.repreview).not.toHaveBeenCalled();
  });

  it('completes an entirely unresolved source without financial adjudication and keeps its report and original ZIP available', async () => {
    const summary = { ...feedback, record_total: 3, transaction_total: 3, accepted: 0, non_monetary: 0, unresolved: 3, non_transaction: 0, auxiliary: 0, currency_totals: {} };
    const value = { ...preview, feedback: summary, preview: [], stats: { total: 3, ok: 0, skipped: 3 } };
    const client = clientFor(value), report = vi.fn(async () => {}), original = vi.fn(async () => {});
    show(client, { actions: { downloadImportReport: report, downloadSource: original } }); await upload();
    const save = screen.getByRole('button', { name: '开始导入' });
    expect((save as HTMLButtonElement).disabled).toBe(false); fireEvent.click(save);
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(screen.queryByText('流水已写入当前案件')).toBeNull();
    expect(screen.queryByText(/跳过/)).toBeNull();
    expect(screen.getByText(/已保存 3 条记录 · 新增资金流水/)).toBeTruthy();
    expect(screen.getByText(/自动处理已完成，未计入资金流水的记录已保留来源和原因，无需逐条确认/)).toBeTruthy();
    expect(screen.queryByText(/待复核|需复核|保存处置结果/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '下载完整处理报告（JSON）' }));
    await waitFor(() => expect(report).toHaveBeenCalledWith({ sourceId: 'SYN-SOURCE', filename: 'feedback-SYN-SOURCE-dispositions.json' }, expect.any(AbortSignal)));
    await waitFor(() => expect((screen.getByRole('button', { name: '下载原始 ZIP' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '下载原始 ZIP' }));
    await waitFor(() => expect(original).toHaveBeenCalledWith({ sourceId: 'SYN-SOURCE', filename: 'synthetic.zip' }, expect.any(AbortSignal)));
    expect(client.commit).toHaveBeenCalledWith('SYN-IMPORT', { mode: 'B', mapping: preview.mapping, mark_accounts: [], self_accounts: [] }, expect.any(AbortSignal));
    expect(client.commit).toHaveBeenCalledOnce();
    expect(client.repreview).not.toHaveBeenCalled();
  });

  it('automatically saves both sides of a paired cancellation without adding effective funds or presenting a review gate', async () => {
    const summary: FeedbackSummary = { ...feedback, profile: 'funds-trace-bank-feedback-v3', record_total: 2, transaction_total: 2,
      accepted: 0, cancelled: 2, excluded_flows: 0, non_monetary: 0, unresolved: 0, non_transaction: 0, auxiliary: 0,
      reason_counts: { matched_cancellation: 2 }, currency_totals: {}, unresolved_examples: [], members: [{ ...feedback.members[0], row_count: 2 }] };
    const client = clientFor({ ...preview, feedback: summary, preview: [], stats: { total: 2, ok: 0, skipped: 2 } });
    show(client); await upload();
    expect(screen.getAllByText(/已配对冲正\/撤销 2 条（双方均不计有效资金）/).length).toBeGreaterThan(0);
    expect(screen.getByText('已配对冲正/撤销，双方均不计有效资金：2 条')).toBeTruthy();
    expect(screen.queryByText(/不自动抵扣或推定配对|待复核|需复核/)).toBeNull();
    expect(screen.queryByText(/历史留存流水已退出有效统计/)).toBeNull();
    const save = screen.getByRole('button', { name: '开始导入' });
    expect((save as HTMLButtonElement).disabled).toBe(false); fireEvent.click(save);
    await screen.findByRole('heading', { name: '自动处理完成' });
    const status = screen.getByRole('status');
    expect(status.textContent).toContain('已保存 2 条记录 · 新增资金流水 0 笔 · 重复 0 笔');
    expect(status.textContent).toContain('已配对冲正/撤销 2 条（双方均不计有效资金）');
    expect(status.textContent).toContain('无资金变动 0 条 · 未计入资金流水 0 条');
    expect(screen.queryByText(/历史留存流水已退出有效统计/)).toBeNull();
    expect(client.commit).toHaveBeenCalledOnce();
  });

  it('distinguishes zero new flows from retained historical flows excluded by a completed cancellation', async () => {
    const summary: FeedbackSummary = { ...feedback, profile: 'funds-trace-bank-feedback-v3', record_total: 8, transaction_total: 7,
      cancelled: 2, excluded_flows: 1, members: [{ ...feedback.members[0], row_count: 7 }, feedback.members[1]] };
    const source: ImportSession = { case_id: '1', import_id: 'SYN-IMPORT', filename: 'synthetic.zip', surface: 'v1', mode: 'B', mapping: preview.mapping,
      stage: 'done', source_state: 'retained', source_available: true, recoverable: false, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 1, request: null, result: result(summary, 0) };
    show(clientFor(), { resume: source });
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(screen.getByRole('status').textContent).toContain('新增资金流水 0 笔');
    expect(screen.getByText('本案另有 1 笔历史留存流水已退出有效统计。')).toBeTruthy();
    expect(screen.getByText(/普通退款按来源记账方向与金额处理/)).toBeTruthy();
  });

  it('disables missing host download actions and aborts an in-flight report when the case binding is revoked', async () => {
    const source: ImportSession = { case_id: '1', import_id: 'SYN-IMPORT', filename: 'synthetic.zip', surface: 'v1', mode: 'B', mapping: preview.mapping,
      stage: 'done', source_state: 'retained', source_available: true, recoverable: false, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 1, request: null, result: result() };
    const controller = new AbortController(); let reject: (reason: Error) => void = () => {};
    const report = vi.fn((_source, signal?: AbortSignal) => new Promise<void>((_resolve, no) => { reject = no; expect(signal?.aborted).toBe(false); }));
    show(clientFor(), { signal: controller.signal, resume: source, actions: { downloadImportReport: report } });
    expect((await screen.findByRole('button', { name: '下载原始 ZIP' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('宿主未提供原件下载能力。')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '下载完整处理报告（JSON）' }));
    await waitFor(() => expect(report).toHaveBeenCalledOnce());
    act(() => controller.abort());
    expect(report.mock.calls[0][1]?.aborted).toBe(true);
    await act(async () => { reject(new Error('stale synthetic failure')); });
    expect(screen.queryByText('stale synthetic failure')).toBeNull();
  });

  it('keeps the report available when a historical original ZIP is unavailable', async () => {
    const source: ImportSession = { case_id: '1', import_id: 'SYN-IMPORT', filename: 'synthetic.zip', surface: 'v1', mode: 'B', mapping: preview.mapping,
      stage: 'done', source_state: 'cleaned', source_available: false, recoverable: false, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 1, request: null, result: result() };
    show(clientFor(), { resume: source, actions: { downloadImportReport: vi.fn(async () => {}), downloadSource: vi.fn(async () => {}) } });
    expect((await screen.findByRole('button', { name: '下载原始 ZIP' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '下载完整处理报告（JSON）' }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText('原始 ZIP 已不可用；处理报告仍可用于追溯。')).toBeTruthy();
  });

  it('separates a fully duplicate write from the source accepted amounts and keeps classification counts explicit', async () => {
    const client = clientFor();
    client.job = vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: 'SYN-IMPORT', stage: 'done', done: 2, total: 2, message: '完成', error: null,
      result: { ...result(), ok: 0, rows_dup: 2 } }));
    show(client); await upload(); fireEvent.click(screen.getByRole('button', { name: '开始导入' }));
    await screen.findByRole('heading', { name: '自动处理完成' });
    const status = screen.getByText(/已保存 6 条记录 · 新增资金流水/);
    expect(status.textContent).toContain('新增资金流水 0 笔 · 重复 2 笔');
    expect(status.textContent).toContain('无资金变动 1 条 · 未计入资金流水 1 条 · 来源反馈 1 条 · 辅助资料 1 条');
    expect(screen.getByText('9,007,199,254,740,993.07 CNY')).toBeTruthy();
    expect(screen.getByText(/配对双方保留来源记录/)).toBeTruthy();
  });

  it('shows retained historical v1 review records as financially unresolved in history without presenting them as skipped', async () => {
    const historical = { ...feedback, profile: 'funds-trace-bank-feedback-v1', non_monetary: undefined, unresolved: undefined, review: 2,
      review_examples: [{ member_path: 'synthetic/交易.txt', source_row: 4, reason_code: 'negative_unknown' }], unresolved_examples: undefined };
    const record: ImportSession = { case_id: '1', import_id: 'SYN-IMPORT', filename: 'synthetic.zip', surface: 'v1', mode: 'B', mapping: preview.mapping,
      stage: 'done', source_state: 'retained', source_available: true, recoverable: false, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 1, request: null, result: result(historical) };
    const client = clientFor(); client.imports = vi.fn(async () => ({ case_id: '1', items: [record] }));
    render(<ImportHistoryDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={new AbortController().signal} onClose={vi.fn()} onOpen={vi.fn()} />);
    const article = (await screen.findByText('synthetic.zip')).closest('article')!;
    expect(within(article).getByText(/自动处理完成 · 已保存 6 条记录/)).toBeTruthy();
    expect(within(article).getByText(/无资金变动 0 条 · 含义未确定 2 条/)).toBeTruthy();
    expect(within(article).getByText(/原始记录已独立保留|记录已独立保留/)).toBeTruthy();
    expect(within(article).queryByText(/跳过|待复核|需复核/)).toBeNull();
    expect(within(article).getByRole('button', { name: '查看回执' })).toBeTruthy();
  });

  it('shows cancellation and historical exclusion counts separately in import history', async () => {
    const summary: FeedbackSummary = { ...feedback, profile: 'funds-trace-bank-feedback-v3', record_total: 8, transaction_total: 7,
      cancelled: 2, excluded_flows: 1, members: [{ ...feedback.members[0], row_count: 7 }, feedback.members[1]] };
    const record: ImportSession = { case_id: '1', import_id: 'SYN-IMPORT', filename: 'synthetic.zip', surface: 'v1', mode: 'B', mapping: preview.mapping,
      stage: 'done', source_state: 'retained', source_available: true, recoverable: false, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 1, request: null, result: result(summary, 0) };
    const client = clientFor(); client.imports = vi.fn(async () => ({ case_id: '1', items: [record] }));
    render(<ImportHistoryDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={new AbortController().signal} onClose={vi.fn()} onOpen={vi.fn()} />);
    const article = (await screen.findByText('synthetic.zip')).closest('article')!;
    expect(within(article).getByText(/新增资金流水 0 笔 · 重复 0 笔 · 已配对冲正\/撤销 2 条（双方均不计有效资金）/)).toBeTruthy();
    expect(within(article).getByText('本案另有 1 笔历史留存流水已退出有效统计。')).toBeTruthy();
    expect(within(article).queryByText(/跳过|待复核|需复核/)).toBeNull();
  });
});
