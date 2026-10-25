import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportJob, ImportPreview, ImportSession } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { FeedbackProvider, type FundsFeedbackPort } from '../feedback/Feedback';
import { ImportDialog } from './ImportDialog';
const preview: ImportPreview = { case_id: '1', import_id: '0123456789ab', filename: 'synthetic.csv', mode: 'A', columns: [], mapping: {}, self_accounts_in_file: [], stats: { total: 1, ok: 1, skipped: 0 }, preview: [] };
const job: ImportJob = { case_id: '1', import_id: preview.import_id, stage: 'importing', done: 0, total: 0, message: '正在写入', error: null, result: null };
const resume: ImportSession = { case_id: '1', import_id: preview.import_id, filename: preview.filename, surface: 'v1', mode: 'A', mapping: {}, stage: 'importing', source_state: 'retained', source_available: true, recoverable: false, created_at: '2026-01-01T00:00:00Z', expires_at: null, error_code: null, attempt: 1, request: null, result: null };
function clientFor(): FundsCaseClientV1 {
  return { binding: { caseId: '1', scopeKey: 'feedback-test', generation: 1 }, stats: vi.fn(), flows: vi.fn(), preview: vi.fn(async () => preview), repreview: vi.fn(), commit: vi.fn(async () => { throw new Error('合成连接中断'); }), job: vi.fn(async () => job), imports: vi.fn(), sources: vi.fn(), cleanup: vi.fn() };
}
describe('import feedback state', () => {
  it('keeps an uncertain submit amber, closes observation, and reopens the original ID using reads only', async () => {
    const client = clientFor(), close = vi.fn(), open = vi.fn();
    const feedback: FundsFeedbackPort = { notify: vi.fn(), task: vi.fn() };
    const signal = new AbortController().signal;
    const view = render(<FeedbackProvider port={feedback}><ImportDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={signal} onClose={close} onImported={vi.fn()} onOpenImport={open} /></FeedbackProvider>);
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], preview.filename)] } });
    fireEvent.click(await screen.findByRole('button', { name: '确认导入' }));
    const alert = await screen.findByRole('alert'); expect(alert.className).toBe('warning-banner');
    expect(document.querySelector('.error-banner')).toBeNull();
    expect(client.commit).toHaveBeenCalledOnce(); expect(client.job).not.toHaveBeenCalled();
    await waitFor(() => expect(feedback.task).toHaveBeenCalledWith(expect.objectContaining({ id: `import:${preview.import_id}`, importId: preview.import_id, status: 'unknown', announce: false })));
    const event = vi.mocked(feedback.task).mock.calls.at(-1)![0];
    fireEvent.click(screen.getByRole('button', { name: '关闭进度' })); expect(close).toHaveBeenCalledOnce();
    event.actions![0].run(); expect(open).toHaveBeenCalledWith(preview.import_id);
    view.unmount();
    const reopened = render(<ImportDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={signal} resume={resume} onClose={close} onImported={vi.fn()} />);
    await waitFor(() => expect(client.job).toHaveBeenCalledWith(preview.import_id, expect.any(AbortSignal)));
    expect(client.commit).toHaveBeenCalledOnce(); expect(client.repreview).not.toHaveBeenCalled();
    const progress = screen.getByRole('progressbar', { name: '流水导入进度' }); expect(progress.hasAttribute('value')).toBe(false);
    reopened.unmount();
  });
  it('labels an upload without a known total as indeterminate', async () => {
    const client = clientFor(); let resolve!: (value: ImportPreview) => void;
    client.preview = vi.fn(() => new Promise<ImportPreview>(done => { resolve = done; }));
    const view = render(<ImportDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={new AbortController().signal} onClose={vi.fn()} onImported={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], preview.filename)] } });
    const progress = screen.getByRole('progressbar', { name: '文件上传与解析进度' });
    expect(progress.hasAttribute('value')).toBe(false); expect(progress.hasAttribute('max')).toBe(false);
    await act(async () => resolve(preview)); view.unmount();
  });
});

describe('classified import outcome wording', () => {
  it.each(['table', 'feedback'] as const)('keeps the %s summary uncertain after its automatic commit receipt is lost', async kind => {
    const client = clientFor();
    const classified: ImportPreview = { ...preview, automatic: true, stats: { total: 3, ok: 1, skipped: 2 },
      ...(kind === 'table' ? { table_summary: { profile: 'synthetic-table', record_total: 3, accepted: 1, unresolved: 1, non_monetary: 1, non_transaction: 0, auxiliary: 0, reason_counts: {}, currency_totals: {}, members: [] } }
        : { feedback: { profile: 'synthetic-feedback', record_total: 3, transaction_total: 3, accepted: 1, review: 0, unresolved: 1, non_monetary: 1, non_transaction: 0, auxiliary: 0, reason_counts: {}, currency_totals: {}, members: [], review_examples: [] } }) };
    vi.mocked(client.preview).mockResolvedValue(classified);
    render(<ImportDialog target={{ caseId: '1', name: '合成案件' }} client={client} scopeSignal={new AbortController().signal} onClose={vi.fn()} onImported={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], preview.filename)] } });
    await screen.findByRole('button', { name: '重新查询进度' });
    expect(screen.getByText(/^处理结果待核实。/)).toBeTruthy();
    expect(screen.queryByText(/会正常完成|自动处理已完成/)).toBeNull();
    expect(screen.getByRole('alert').className).toBe('warning-banner');
    expect(client.commit).toHaveBeenCalledOnce(); expect(client.job).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '开始导入' })).toBeNull();
  });
});
