import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ImportJob, ImportPreview, ImportSession, ImportReviewRecord } from '../../../contracts/api-v1';
import { api } from '../lib/api';
import { mountImport } from './import-entry';

const importId = '0123456789abcdef';
const preview: ImportPreview = { case_id: '1', import_id: importId, filename: 'synthetic.csv', mode: 'A', automatic: true,
  columns: ['合成金额'], mapping: { amount: '合成金额' }, self_accounts_in_file: [], stats: { total: 1, ok: 1, skipped: 0 }, preview: [] };
const job: ImportJob = { case_id: '1', import_id: importId, stage: 'done', done: 1, total: 1, error: null, message: '完成',
  result: { total: 1, ok: 1, skipped: 0, rows_dup: 0, batch_id: '1', bad_examples: [], self_accounts: [], self_accounts_count: 0,
    main_matched: [], main_detected: [], accounts_in_batch: 0, source_id: importId } };
const cleanups: Array<() => void> = [];
afterEach(() => { for (const cleanup of cleanups.splice(0)) act(cleanup); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount(overrides: Partial<Parameters<typeof mountImport>[1]> = {}) {
  const element = document.createElement('div'); element.className = 'funds-trace'; document.body.append(element);
  const controller = new AbortController(), options = { caseId: '1', name: '合成案件', generation: 1, signal: controller.signal,
    acquire: vi.fn(async () => {}), release: vi.fn(async () => {}), changed: vi.fn(async () => {}), feedback: vi.fn(() => true), close: vi.fn(), ...overrides };
  let view!: ReturnType<typeof mountImport>; await act(async () => { view = mountImport(element, options); });
  cleanups.push(() => { view.dispose(); element.remove(); });
  return { options, controller, view };
}
function transport() {
  return { preview: vi.spyOn(api, 'preview').mockResolvedValue(preview), commit: vi.spyOn(api, 'commit').mockResolvedValue({ case_id: '1', import_id: importId, started: true }),
    job: vi.spyOn(api, 'job').mockResolvedValue(job) };
}
function upload() { fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } }); }

describe('the canvas import host', () => {
  it('keeps a durable completion when refreshing the surrounding canvas fails', async () => {
    const t = transport(), h = await mount({ changed: vi.fn(async () => { throw new Error('Synthetic list refresh failure'); }) });
    upload(); await screen.findByRole('status', { name: '导入完成' });
    expect(h.options.acquire).toHaveBeenCalledWith(importId, 'v1');
    expect(h.options.release).toHaveBeenCalledWith(importId, importId);
    expect(h.options.feedback).toHaveBeenCalledWith(expect.objectContaining({ kind: 'toast', severity: 'warning', title: '导入回执已读取，界面刷新尚未完成' }));
    expect(screen.queryByText('结果待核对')).toBeNull(); expect(t.commit).toHaveBeenCalledOnce();
  });
  it('does not send a write when the case lease cannot be acquired', async () => {
    const t = transport(); await mount({ acquire: vi.fn(async () => { throw new Error('Synthetic unresolved writer'); }) });
    upload(); await screen.findByRole('alert');
    expect(t.commit).not.toHaveBeenCalled(); expect(t.job).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '重新查询进度' })).toBeTruthy();
  });
  it('recovers an existing legacy operation through its original API and never auto-commits recovery', async () => {
    const t = transport(); const bodies: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/repreview')) return { ok: true, json: async () => preview };
      if (url.endsWith('/commit')) { bodies.push(JSON.parse(init?.body as string)); return { ok: true, json: async () => ({ import_id: importId }) }; }
      if (url.startsWith('/api/import/progress?')) return { ok: true, json: async () => job };
      throw new Error('Unexpected synthetic legacy path');
    });
    vi.stubGlobal('fetch', fetcher);
    const resume: ImportSession = { case_id: '1', import_id: importId, filename: preview.filename, surface: 'legacy', mode: 'A', mapping: preview.mapping,
      stage: 'ready', source_state: 'retained', source_available: true, recoverable: true, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 0, request: { mode: 'A', mapping: preview.mapping, mark_accounts: [], self_accounts: ['0001','0002'] }, result: null };
    const h = await mount({ resume });
    await screen.findByRole('button', { name: '确认导入' }); expect(bodies).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '确认导入' }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual(expect.objectContaining({ case_id: '1', import_id: importId, mark_accounts: '', self_accounts: '0001,0002' }));
    await screen.findByRole('status', { name: '导入完成' });
    expect(h.options.acquire).toHaveBeenCalledWith(importId, 'legacy'); expect(t.commit).not.toHaveBeenCalled(); expect(t.preview).not.toHaveBeenCalled();
  });

  it('opens the exact nested source and focuses the physical row returned on a later page', async () => {
    const memberPath = '资料/开户.csv', sourceRow = 105;
    const record: ImportReviewRecord = { record_no: 208, member_path: memberPath, source_row: sourceRow, record_key: 'SYN-OPENING',
      disposition: 'auxiliary', reason_code: 'auxiliary_table', details: {}, flow_id: null, preview: null,
      original: [{ column: 1, header: '卡号', fields: [], value: '9900123400000001' }, { column: 2, header: '户名', fields: [], value: '合成甲' }] };
    const result: NonNullable<ImportJob['result']> = { ...job.result!, table_summary: {
      profile: 'synthetic', record_total: 106, accepted: 0, unresolved: 0, non_monetary: 0, non_transaction: 0, auxiliary: 106,
      reason_counts: { auxiliary_table: 106 }, currency_totals: {}, members: ['资料/first.csv', memberPath].map(member_path => ({
        member_path, role: 'auxiliary', sha256: 'a'.repeat(64), size_bytes: 96, encoding: 'utf-8', row_count: 106,
      })), documents: [{ member_path: memberPath, header_row: 2, columns: ['卡号', '户名'], header_assistance: [
        { header_row: 2, header_rows: [1, 2], status: 'verified', columns: [1, 2], reason: 'source_header_verified' },
        { header_row: 33, header_rows: [33], status: 'review', columns: [], reason: 'ambiguous_source_header', suggestions: [
          { column_index: 3, role: 'payer_account' }, { column_index: 7, role: 'synthetic_invalid_role' },
        ] },
      ] }],
    } };
    const resume: ImportSession = { case_id: '1', import_id: importId, filename: '合成资料.zip', surface: 'v1', mode: 'A', mapping: {},
      stage: 'done', source_state: 'retained', source_available: true, recoverable: false, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 1, request: null, result };
    const t = transport(); t.job.mockResolvedValue({ ...job, result });
    const review = vi.spyOn(api, 'importReview').mockResolvedValue({ case_id: '1', source_id: importId, member_path: memberPath,
      view: 'all', counts: { all: 106, review: 0, accepted: 0 }, items: [record], total: 106, page: 3, page_size: 50, issue: null });
    await mount({ resume, sourceFocus: { memberPath, sourceRow } });
    await screen.findByText('合成甲');
    const row = screen.getByRole('row', { name: '原始第 105 行' });
    await waitFor(() => expect(document.activeElement).toBe(row));
    expect(row.getAttribute('data-focused')).toBe('true');
    expect(screen.getByText('3 / 3')).toBeTruthy();
    expect(review).toHaveBeenCalledExactlyOnceWith('1', importId, memberPath, { view: 'all', page: 1, sourceRow }, expect.any(AbortSignal));
    fireEvent.click(screen.getByText('表头辅助依据 · 2 处'));
    expect(screen.getByText(/原始第 1、2 行 · 第 1、2 列 · 已核验/)).toBeTruthy();
    expect(screen.getByText(/原始第 33 行 · 待核对 · 表头角色存在歧义/)).toBeTruthy();
    expect(screen.getByText('· 模型建议核对第 3 列（付款方账号）')).toBeTruthy();
    expect(screen.queryByText(/模型建议核对第 7 列/)).toBeNull();
    expect(t.commit).not.toHaveBeenCalled();
  });

  it('shows the retained opening declaration without a local original or import session', async () => {
    const memberPath = '资料/开户.csv', sourceRow = 4;
    const details = { resolution_evidence: JSON.stringify({ role: 'account', card: '9900123400000001', name: '合成甲' }) };
    const record = { record_no: 7, member_path: memberPath, source_row: sourceRow, record_key: 'SYN-ORIGINAL-ROW',
      disposition: 'auxiliary' as const, reason_code: 'auxiliary_table', details, flow_id: null, preview: null };
    const records = vi.spyOn(api, 'importRecords').mockResolvedValue({ case_id: '1', source_id: importId, total: 1,
      page: 1, page_size: 50, items: [record] });
    const h = await mount({ sourceFocus: { sourceId: importId, memberPath, sourceRow } });
    expect(await screen.findByRole('dialog', { name: '开户来源' })).toBeTruthy();
    expect(screen.getByText('原件不可用，可核对案件内保留的来源记录。')).toBeTruthy();
    expect(await screen.findByText('合成甲')).toBeTruthy();
    expect(screen.getByText('9900123400000001')).toBeTruthy();
    expect(records).toHaveBeenCalledWith('1', importId, 1, expect.any(AbortSignal), memberPath, sourceRow);
    act(() => h.controller.abort());
    expect(screen.queryByText('合成甲')).toBeNull();
  });
});
