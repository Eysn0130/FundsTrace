import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportJob, ImportPreview, ImportSession } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { ImportDialog } from './ImportDialog';
import { ImportHistoryDialog } from './ImportHistoryDialog';

const summary: NonNullable<ImportPreview['table_summary']> = { profile: 'funds-trace-table-v5', record_total: 3, accepted: 0, unresolved: 2,
  non_monetary: 1, non_transaction: 0, auxiliary: 0, reason_counts: { amount_unit_conflict: 1, direction_conflict: 1, zero_amount: 1 },
  currency_totals: {}, members: [], unresolved_examples: [{ member_path: 'synthetic.csv', source_row: 2, reason_code: 'amount_unit_conflict' }],
  documents: [{ member_path: 'synthetic.csv', sheet: '', records: 3 }],
  mapping_evidence: [{ field: 'amount', column: '交易金额（分）', unit: '分', unit_factor: '0.01' },
    { field: 'direction', column: '收支方向', unit: '元/默认基础单位', unit_factor: '1' },
    { field: 'trade_no', column: '交易流水号', basis: 'source_transaction_id' },
    { field: 'voucher_reference', column: '凭证号', basis: 'independent_voucher_reference' },
    { field: 'trade_time', column: '成交日期', time_format: 'DD/MM/YYYY', unit: '元/默认基础单位', unit_factor: '1', basis: 'explicit_time_format' }] };
const preview = { case_id: '1', import_id: 'SYN-AUTO', filename: 'synthetic.csv', mode: 'B', automatic: true,
  columns: ['交易时间', '本方账号', '对手账号', '交易金额（分）'], mapping: { amount: '交易金额（分）' },
  self_accounts_in_file: [], stats: { total: 3, ok: 0, skipped: 3 }, preview: [], table_summary: summary } satisfies ImportPreview & { automatic: boolean; table_summary: typeof summary };
const result = { total: 3, ok: 0, skipped: 3, rows_dup: 0, batch_id: 'SYN-BATCH', bad_examples: [], self_accounts: [],
  self_accounts_count: 0, main_matched: [], accounts_in_batch: 0, main_detected: [], source_id: 'SYN-SOURCE', table_summary: summary };
function clientFor(): FundsCaseClientV1 {
  return { binding: { caseId: '1', scopeKey: 'synthetic-auto', generation: 0 }, stats: vi.fn(), flows: vi.fn(),
    preview: vi.fn(async () => preview), repreview: vi.fn(async () => preview),
    commit: vi.fn(async () => ({ case_id: '1', import_id: preview.import_id, started: true })),
    job: vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: preview.import_id, stage: 'done', done: 0, total: 3, message: '完成', error: null, result })),
    imports: vi.fn(), sources: vi.fn(), cleanup: vi.fn() };
}
const session = (stage: 'ready' | 'done'): ImportSession => ({ case_id: '1', import_id: preview.import_id, filename: preview.filename, surface: 'v1', mode: 'B', mapping: preview.mapping,
  stage, source_state: 'retained', source_available: true, recoverable: stage === 'ready', created_at: '2026-01-01T00:00:00Z', expires_at: null,
  error_code: null, attempt: stage === 'done' ? 1 : 0, request: null, result: stage === 'done' ? result : null });
function show(client: FundsCaseClientV1, signal = new AbortController().signal, resume?: ImportSession, onImported = vi.fn()) {
  return render(<ImportDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={signal} resume={resume} onClose={vi.fn()} onImported={onImported} />);
}
function upload() { fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } }); }

describe('automatic table import', () => {
  it('keeps every existing file suffix and advertises XLS in the file picker', () => {
    show(clientFor());
    const input = screen.getByLabelText('选择流水文件') as HTMLInputElement;
    expect(input.accept.split(',')).toEqual(['.csv', '.tsv', '.txt', '.xlsx', '.xls', '.zip']);
    expect(screen.getByText('支持 UTF-8／GBK CSV、XLSX、XLS 和已知格式的银行反馈 ZIP')).toBeTruthy();
  });

  it('previews the same XLS File and completes one automatic commit without remapping', async () => {
    const client = clientFor(), imported = vi.fn();
    const file = new File(['synthetic XLS transport fixture'], 'synthetic.xls', { type: 'application/vnd.ms-excel' });
    const xlsPreview = { ...preview, filename: file.name };
    vi.mocked(client.preview).mockResolvedValue(xlsPreview);
    show(client, undefined, undefined, imported);
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [file] } });
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.preview).toHaveBeenCalledOnce();
    expect(vi.mocked(client.preview).mock.calls[0][0]).toBe(file);
    expect(client.preview).toHaveBeenCalledWith(file, expect.any(AbortSignal));
    expect(client.commit).toHaveBeenCalledOnce();
    expect(client.commit).toHaveBeenCalledWith(xlsPreview.import_id, {
      mode: xlsPreview.mode, mapping: xlsPreview.mapping, mark_accounts: [], self_accounts: [],
    }, expect.any(AbortSignal));
    expect(client.repreview).not.toHaveBeenCalled();
    expect(client.job).toHaveBeenCalledOnce();
    expect(imported).toHaveBeenCalledOnce();
    expect(screen.queryByRole('button', { name: '开始导入' })).toBeNull();
  });

  it.each(['commit receipt lost', 'first progress read failed'])('recovers an uncertain submission using GET only: %s', async failure => {
    const client = clientFor(), imported = vi.fn();
    const done: ImportJob = { case_id: '1', import_id: preview.import_id, stage: 'done', done: 0, total: 3, message: '完成', error: null, result };
    if (failure === 'commit receipt lost') client.commit = vi.fn(async () => { throw new Error('合成连接中断'); });
    else client.job = vi.fn().mockRejectedValueOnce(new Error('合成连接中断')).mockResolvedValue(done);
    show(client, undefined, undefined, imported); upload();
    await screen.findByRole('alert');
    const retry = await screen.findByRole('button', { name: '重新查询进度' });
    expect(client.commit).toHaveBeenCalledOnce();
    expect(imported).not.toHaveBeenCalled();
    expect((screen.getByLabelText('流水文件类型') as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByLabelText('映射交易金额') as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: '开始导入' })).toBeNull();
    expect(client.job).toHaveBeenCalledTimes(failure === 'commit receipt lost' ? 0 : 1);
    fireEvent.click(retry);
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledOnce();
    expect(client.repreview).not.toHaveBeenCalled();
    expect(client.job).toHaveBeenCalledTimes(failure === 'commit receipt lost' ? 1 : 2);
    expect(client.job).toHaveBeenLastCalledWith(preview.import_id, expect.any(AbortSignal));
    expect(imported).toHaveBeenCalledOnce();
    expect(screen.getByText(/已保存 3 条记录 · 新增资金流水/)).toBeTruthy();
  });

  it('unlocks an explicitly unstarted session without automatically resubmitting', async () => {
    const client = clientFor(), imported = vi.fn();
    client.commit = vi.fn().mockRejectedValueOnce(new Error('合成提交回执丢失'))
      .mockResolvedValue({ case_id: '1', import_id: preview.import_id, started: true });
    const done = await client.job(preview.import_id);
    client.job = vi.fn().mockRejectedValueOnce(Object.assign(new Error('合成尚未启动'), { code: 'import_job_not_found' })).mockResolvedValue(done);
    show(client, undefined, undefined, imported); upload();
    fireEvent.click(await screen.findByRole('button', { name: '重新查询进度' }));
    await screen.findByText('服务端确认该导入任务尚未开始，请核对预览后重新开始导入。');
    expect(client.commit).toHaveBeenCalledOnce();
    const start = screen.getByRole('button', { name: '开始导入' });
    expect((start as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByLabelText('流水文件类型') as HTMLSelectElement).disabled).toBe(false);
    expect(imported).not.toHaveBeenCalled();
    fireEvent.click(start);
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledTimes(2);
    expect(imported).toHaveBeenCalledOnce();
  });

  it('keeps an unrelated not-found response uncertain and permits another read only', async () => {
    const client = clientFor();
    const done = await client.job(preview.import_id);
    client.commit = vi.fn(async () => { throw new Error('合成提交回执丢失'); });
    client.job = vi.fn().mockRejectedValueOnce(Object.assign(new Error('合成未知404'), { code: 'import_not_found' })).mockResolvedValue(done);
    show(client); upload();
    fireEvent.click(await screen.findByRole('button', { name: '重新查询进度' }));
    await screen.findByText(/合成未知404/);
    expect(screen.queryByRole('button', { name: '开始导入' })).toBeNull();
    expect((screen.getByLabelText('映射交易金额') as HTMLSelectElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '重新查询进度' }));
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledOnce();
    expect(client.job).toHaveBeenCalledTimes(2);
  });

  it.each([true, false])('uses the authoritative failure recovery flag without replaying a write: %s', async recoverable => {
    const client = clientFor(), imported = vi.fn();
    client.commit = vi.fn(async () => { throw new Error('合成提交回执丢失'); });
    client.job = vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: preview.import_id, stage: 'error', done: 0, total: 3,
      message: '合成已回滚', error: '合成已回滚', recoverable, source_available: recoverable, result: null }));
    show(client, undefined, undefined, imported); upload();
    fireEvent.click(await screen.findByRole('button', { name: '重新查询进度' }));
    await screen.findByText('合成已回滚');
    expect(screen.queryByRole('button', { name: '重新查询进度' })).toBeNull();
    expect((screen.getByRole('button', { name: '开始导入' }) as HTMLButtonElement).disabled).toBe(true);
    if (recoverable) {
      fireEvent.click(screen.getByRole('button', { name: '重新预览后重试' }));
      await waitFor(() => expect((screen.getByRole('button', { name: '开始导入' }) as HTMLButtonElement).disabled).toBe(false));
      expect(client.repreview).toHaveBeenCalledOnce();
    } else expect(screen.queryByRole('button', { name: '重新预览后重试' })).toBeNull();
    expect(client.commit).toHaveBeenCalledOnce();
    expect(imported).not.toHaveBeenCalled();
  });

  it.each(['case_id', 'import_id'])('does not unlock or publish a mismatched recovery response: %s', async field => {
    const client = clientFor(), imported = vi.fn();
    const done = await client.job(preview.import_id);
    client.commit = vi.fn(async () => { throw new Error('合成提交回执丢失'); });
    client.job = vi.fn(async () => ({ ...done, [field]: 'SYN-WRONG-BINDING' }));
    show(client, undefined, undefined, imported); upload();
    fireEvent.click(await screen.findByRole('button', { name: '重新查询进度' }));
    await screen.findByText(/导入进度与当前案件不一致/);
    expect(screen.queryByRole('button', { name: '开始导入' })).toBeNull();
    expect(screen.getByRole('button', { name: '重新查询进度' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '自动处理完成' })).toBeNull();
    expect(imported).not.toHaveBeenCalled();
    expect(client.commit).toHaveBeenCalledOnce();
  });

  it('discards a delayed recovery response after the case lease is revoked', async () => {
    const client = clientFor(), imported = vi.fn(), controller = new AbortController();
    const done = await client.job(preview.import_id);
    let finish: (value: ImportJob) => void = () => {};
    client.commit = vi.fn(async () => { throw new Error('合成提交回执丢失'); });
    client.job = vi.fn(() => new Promise<ImportJob>(resolve => { finish = resolve; }));
    show(client, controller.signal, undefined, imported); upload();
    fireEvent.click(await screen.findByRole('button', { name: '重新查询进度' }));
    await waitFor(() => expect(client.job).toHaveBeenCalledOnce());
    act(() => controller.abort());
    expect(vi.mocked(client.job).mock.calls[0][1]?.aborted).toBe(true);
    await act(async () => finish(done));
    expect(screen.queryByRole('heading', { name: '自动处理完成' })).toBeNull();
    expect(imported).not.toHaveBeenCalled();
    expect(client.commit).toHaveBeenCalledOnce();
  });

  it('reports effective additions separately when a frozen import retains an already cancelled fact', async () => {
    const client = clientFor();
    const cancelled = { ...summary, cancelled: 2, excluded_flows: 1, effective_added: 0,
      reason_counts: { matched_cancellation: 2, cancellation_balance_mismatch: 1 } };
    const receipt = { ...result, ok: 1, table_summary: cancelled };
    const done = { ...session('done'), result: receipt };
    show(client, undefined, done);
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(screen.getByText(/已保存 3 条记录 · 新增资金流水/).textContent).toContain('新增资金流水 0 笔');
    expect(screen.getByText(/本次保留流水事实 1 笔，其中新增有效资金流水 0 笔/)).toBeTruthy();
    expect(screen.getByText('本案另有 1 笔历史留存流水已退出有效统计。')).toBeTruthy();
    expect(screen.getByText('余额变化不能核实完整冲回：1 条')).toBeTruthy();
    expect(client.commit).not.toHaveBeenCalled();
  });

  it('keeps the effective addition distinction in history receipts', async () => {
    const client = clientFor();
    client.imports = vi.fn(async () => ({ case_id: '1', items: [{ ...session('done'),
      result: { ...result, ok: 1, table_summary: { ...summary, effective_added: 0, cancelled: 2, excluded_flows: 1 } } }] }));
    render(<ImportHistoryDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={new AbortController().signal} onClose={vi.fn()} onOpen={vi.fn()} />);
    await screen.findByText('自动处理完成 · 已保存 3 条记录');
    expect(screen.getByText(/新增资金流水 0 笔 · 重复/).textContent).toContain('已配对冲正/撤销 2 条');
    expect(screen.getByText(/本次保留流水事实 1 笔，其中新增有效资金流水 0 笔/)).toBeTruthy();
    expect(screen.getByText('本案另有 1 笔历史留存流水已退出有效统计。')).toBeTruthy();
  });

  it('automatically saves all-unresolved records once and refreshes analysis inputs despite zero added flows', async () => {
    const client = clientFor(), imported = vi.fn(); show(client, undefined, undefined, imported); upload();
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledOnce(); expect(client.repreview).not.toHaveBeenCalled();
    expect(imported).toHaveBeenCalledOnce();
    expect(screen.getByText(/已保存 3 条记录 · 新增资金流水/).textContent).toContain('未计入资金流水 2 条');
    expect(screen.getByText('金额单位相互冲突：1 条')).toBeTruthy();
    expect(screen.getByText('方向字段与金额含义冲突：1 条')).toBeTruthy();
    expect(screen.getByText(/未计入不代表金额为零或没有发生交易/)).toBeTruthy();
    expect(screen.getByText(/自动处理已完成.*无需逐条确认.*不参与金额、交易笔数和关系统计/)).toBeTruthy();
    expect(screen.getByText('未计入记录的来源位置').closest('details')?.open).toBe(false);
    expect(screen.queryByText(summary.profile)).toBeNull();
    expect(screen.getByText('文件已处理，暂无可可靠计入的流水。')).toBeTruthy();
    expect(screen.getByText(/交易金额（分）.*0.01/)).toBeTruthy();
    expect(screen.getByText(/收支方向 ← 收支方向/).textContent).not.toContain('来源单位');
    expect(screen.getByText(/流水号 ← 交易流水号.*使用来源交易流水号/)).toBeTruthy();
    expect(screen.getByText(/凭证引用 ← 凭证号.*独立引用/)).toBeTruthy();
    expect(screen.getByText(/交易时间 ← 成交日期.*时间格式：DD\/MM\/YYYY.*明确的日期时间格式/).textContent).not.toContain('来源单位');
    expect(screen.queryByText(/跳过|等待用户确认|逐条审核/)).toBeNull();
  });

  it('explains exact validation failures without confusing missing bank data with unsupported semantics', async () => {
    const client = clientFor();
    const reasons = {
      conflicting_unit: '金额单位声明相互矛盾',
      balance_amount_conflict: '本笔交易前后账面余额差与金额不一致',
      balance_direction_conflict: '本笔交易前后账面余额变化与收支方向矛盾',
      missing_amount: '缺少形成资金流水必需的交易金额',
      missing_time: '缺少形成资金流水必需的交易时间',
      negative_amount: '来源未明确负号含义',
      unknown_status: '交易状态含义缺少适用的来源依据',
    };
    const source = { ...summary, record_total: 8, unresolved: 8, non_monetary: 0,
      reason_counts: Object.fromEntries([...Object.keys(reasons), 'unrecognized_<script>'].map(reason => [reason, 1])) };
    client.preview = vi.fn(async () => ({ ...preview, table_summary: source }));
    client.job = vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: preview.import_id, stage: 'done', done: 8, total: 8,
      message: '完成', error: null, result: { ...result, table_summary: source } }));
    show(client); upload();
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledOnce();
    expect(screen.getByRole('status').textContent).toContain('新增资金流水 0 笔');
    expect(screen.getByRole('status').textContent).toContain('未计入资金流水 8 条');
    for (const label of Object.values(reasons)) expect(screen.getByText(`${label}：1 条`)).toBeTruthy();
    expect(screen.getByText('其他自动分类结果，详见完整处理报告：1 条')).toBeTruthy();
    expect(screen.queryByText(/unrecognized_|待审核|无法导入|含义未确定|银行数据缺失/)).toBeNull();
  });

  it('never automatically commits a restored preview even if the response contains an automatic flag', async () => {
    const client = clientFor(); show(client, undefined, session('ready'));
    const start = await screen.findByRole('button', { name: '开始导入' });
    expect(client.repreview).toHaveBeenCalledOnce(); expect(client.commit).not.toHaveBeenCalled();
    expect((start as HTMLButtonElement).disabled).toBe(false);
    fireEvent.change(screen.getByLabelText('映射交易金额'), { target: { value: '' } });
    expect((start as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect(client.repreview).toHaveBeenCalledTimes(2));
    expect(client.commit).not.toHaveBeenCalled();
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(start); await screen.findByRole('heading', { name: '自动处理完成' });
    expect(client.commit).toHaveBeenCalledOnce();
  });

  it('does not auto-commit a delayed upload after its case lease is revoked', async () => {
    let finish: (value: ImportPreview) => void = () => {};
    const client = clientFor(); client.preview = vi.fn(() => new Promise<ImportPreview>(resolve => { finish = resolve; }));
    const controller = new AbortController(); show(client, controller.signal); upload();
    act(() => controller.abort());
    await act(async () => finish(preview));
    expect(client.commit).not.toHaveBeenCalled(); expect(client.job).not.toHaveBeenCalled();
  });

  it('rejects a mismatched case preview before automatic commit', async () => {
    const client = clientFor(); client.preview = vi.fn(async () => ({ ...preview, case_id: '2' })); show(client); upload();
    expect(await screen.findByRole('alert')).toBeTruthy(); expect(client.commit).not.toHaveBeenCalled();
  });

  it('keeps partial table dispositions visible in import history without calling them skipped', async () => {
    const client = clientFor(); client.imports = vi.fn(async () => ({ case_id: '1', items: [session('done')] }));
    render(<ImportHistoryDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={new AbortController().signal} onClose={vi.fn()} onOpen={vi.fn()} />);
    await screen.findByText('自动处理完成 · 已保存 3 条记录');
    expect(screen.getByText(/本次规则未接纳为流水 3 条（含义未确定 2 条）/)).toBeTruthy();
    expect(screen.queryByText(/跳过/)).toBeNull();
  });
});
