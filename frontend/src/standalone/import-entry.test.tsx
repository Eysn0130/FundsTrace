import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ImportJob, ImportLayoutReviewIssue, ImportPreview, ImportSession } from '../../../contracts/api-v1';
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
  it('carries the rule-prepared physical revision into fresh automatic commit', async () => {
    const t = transport();
    const revision = 'fedcba9876543210fedcba9876543210';
    t.preview.mockResolvedValue({ ...preview, layout_applied: true, layout_review: {
      revision, members: [{ member_path: 'table.csv', sheet: '', source_rows: 3, header_rows: [1] }],
    } });
    await mount();
    upload();
    await screen.findByRole('status', { name: '导入完成' });
    expect(t.commit).toHaveBeenCalledWith('1', importId, expect.objectContaining({ ready_revision: revision }), expect.any(AbortSignal));
  });

  it('binds ordinary confirmation of the verified direction preview to its revision', async () => {
    const t = transport();
    const revision = '9c6cfc34552149d38b54dac2c47f4e8a';
    t.preview.mockResolvedValue({ ...preview, mode: 'B', automatic: false, layout_review: {
      revision, members: [{ member_path: 'table.csv', sheet: '', source_rows: 3, header_rows: [1] }],
    }, table_summary: { profile: 'funds-trace-table-v104', record_total: 1, accepted: 1, unresolved: 0,
      non_monetary: 0, non_transaction: 0, auxiliary: 0, reason_counts: {}, currency_totals: {}, members: [] } });
    await mount();
    upload();
    const confirm = await screen.findByRole('button', { name: '开始导入' });
    expect(t.commit).not.toHaveBeenCalled();
    fireEvent.click(confirm);
    await screen.findByRole('status', { name: '导入完成' });
    expect(t.commit).toHaveBeenCalledWith('1', importId,
      expect.objectContaining({ mode: 'B', mapping: preview.mapping, ready_revision: revision }), expect.any(AbortSignal));
  });

  it('commits a verified PDF without adding a default main-account mark', async () => {
    const t = transport();
    t.preview.mockResolvedValue({ ...preview, filename: 'verified.pdf', columns: [], mapping: {},
      self_accounts_in_file: [{ account: 'SYN-LEDGER', name: '', count: 1 }],
      table_summary: { profile: 'pdf-text-ledger-v1', record_total: 1, accepted: 1, unresolved: 0,
        non_monetary: 0, non_transaction: 0, auxiliary: 0, reason_counts: {}, currency_totals: { CNY: '10.00' }, members: [] },
    });
    await mount();
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'verified.pdf')] } });
    await screen.findByRole('status', { name: '导入完成' });
    expect(t.commit).toHaveBeenCalledWith('1', importId,
      expect.objectContaining({ mapping: {}, mark_accounts: [], self_accounts: [] }), expect.any(AbortSignal));
  });

  it('keeps the trial import preview open for original evidence before any write', async () => {
    const t = transport();
    t.preview.mockResolvedValue({ ...preview, layout_model_trial: true, layout_review: {
      revision: 'synthetic-revision', members: [{ member_path: 'table.csv', sheet: '', source_rows: 3, header_rows: [1] }],
    } });
    const h = await mount();
    upload();
    await screen.findByText('隔离试用 · 查表头');
    expect(t.commit).not.toHaveBeenCalled();
    expect(h.options.acquire).not.toHaveBeenCalled();
  });

  it.each([
    ['use_code_wrong_scope', /核对适用的工作表和起止行列/, [5]],
    ['use_code_missing_definition', /请补用途码 B 在当前表区的原始释义/, [3]],
    ['use_code_conflicting_definitions', /核对原说明并提供更正依据/, [5, 6]],
  ] satisfies [ImportLayoutReviewIssue['reason'], RegExp, number[]][])(
    'shows %s directly in the pending import card', async (reason, message, rows) => {
      const t = transport();
      const review = vi.spyOn(api, 'importModelTrial');
      t.preview.mockResolvedValue({ ...preview,
        layout_model_trial: true,
        layout_review: { revision: 'synthetic-use-code', members: [
          { member_path: 'worksheets/0001', sheet: '流水', source_rows: 4, header_rows: [1] },
          { member_path: 'worksheets/0002', sheet: '字段说明', source_rows: 6, header_rows: [1] },
        ] },
        layout_review_issue: { reason, code: 'B', member_path: 'worksheets/0001', header_row: 1, last_row: 4,
          notes: rows.map(source_row => ({ member_path: 'worksheets/0002', source_row })) },
      });
      await mount();
      upload();
      const status = (await screen.findByText(message)).closest('[role="status"]');
      expect(status?.textContent).toMatch(/流水\s*第 1–4 行/);
      expect(status?.textContent).toMatch(new RegExp(`字段说明\\s*第 ${rows.join('、')} 行`));
      expect(review).not.toHaveBeenCalled();
      expect(t.commit).not.toHaveBeenCalled();
    },
  );

  it('shows the original header location beyond the current page without preparing a write', async () => {
    const t = transport();
    const revision = 'wide-header-revision';
    t.preview.mockResolvedValue({ ...preview, layout_model_trial: true, layout_review: {
      revision, members: [{ member_path: 'table.csv', sheet: '', source_rows: 3, header_rows: [1] }],
    } });
    vi.spyOn(api, 'importModelTrial').mockResolvedValue({
      state: 'pending', reason: 'source_header_role', first_calls: 0, recheck_calls: 0,
      header_role: { scope: 'source_header_only', field: 'counter_account', member_path: 'table.csv',
        header_row: 1, width: 43, matches: [{ column: 20, heading: '对方账号' }] },
      packet: { domain: 'import', question: '', packet_sha256: 'a'.repeat(64), records: [], candidates: [], gaps: [],
        facts: { case_id: '1', import_id: importId, revision, field: 'counter_account', column_start: 1,
          header_row: 1, member_path: 'table.csv', support_member_path: null, more_columns: true } },
    });
    await mount();
    upload();
    const disclosure = await screen.findByText('隔离试用 · 查表头');
    disclosure.closest('details')!.open = true;
    fireEvent.change(screen.getByLabelText('待核字段'), { target: { value: 'counter_account' } });
    fireEvent.click(screen.getByRole('button', { name: '定位字段原列' }));
    await screen.findByText('原表头的对方账号位于第 20 列「对方账号」。本表有效流水仍待核。');
    expect(t.commit).not.toHaveBeenCalled();
  });

  it('commits the rule-prepared revision from the trial field review', async () => {
    const t = transport();
    const revision = 'original-layout';
    const checkedRevision = 'checked-layout';
    const members = [{ member_path: 'table.csv', sheet: '', source_rows: 3, header_rows: [1] }];
    t.preview.mockResolvedValue({ ...preview, layout_model_trial: true, layout_review: { revision, members } });
    vi.spyOn(api, 'importModelTrial').mockResolvedValue({
      state: 'rule', reason: 'rule_evidence', first_calls: 0, recheck_calls: 0,
      packet: {
        domain: 'import', question: '', packet_sha256: 'a'.repeat(64), records: [], candidates: [], gaps: [],
        facts: { case_id: '1', import_id: importId, revision, field: 'self_account', column_start: 1,
          header_row: 1, member_path: 'table.csv', support_member_path: null },
      },
      prepared_preview: { ...preview, layout_applied: true, layout_review: { revision: checkedRevision, members },
        layout_rule_evidence: { source_sha256: 'b'.repeat(64), regions: [
          { member_path: 'table.csv', header_row: 1, last_row: 3, column: 2, basis: 'source_role_statement',
            note: { member_path: 'table.csv', source_row: 1 } },
        ] } },
    });
    await mount();
    upload();
    const disclosure = await screen.findByText('隔离试用 · 查表头');
    disclosure.closest('details')!.open = true;
    fireEvent.click(screen.getByRole('button', { name: '定位字段原列' }));
    await waitFor(() => expect(screen.queryByText('隔离试用 · 查表头')).toBeNull());
    expect(t.commit).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /^(开始|确认)导入$/ }));
    await screen.findByRole('status', { name: '导入完成' });
    expect(t.commit).toHaveBeenCalledWith('1', importId, expect.objectContaining({ ready_revision: checkedRevision }), expect.any(AbortSignal));
  });

  it('keeps checked evidence visible when the complete layout cannot be admitted', async () => {
    const t = transport();
    const revision = 'synthetic-unadmitted';
    const members = [{ member_path: 'table.csv', sheet: '', source_rows: 3, header_rows: [1] }];
    t.preview.mockResolvedValue({ ...preview, layout_model_trial: true, layout_review: { revision, members } });
    vi.spyOn(api, 'importModelTrial').mockResolvedValue({
      state: 'pending', reason: 'layout_not_admitted', first_calls: 2, recheck_calls: 1,
      packet: {
        domain: 'import', question: '', packet_sha256: 'a'.repeat(64), candidates: [], gaps: [],
        facts: { case_id: '1', import_id: importId, revision, field: 'self_account', column_start: 1,
          header_row: 1, member_path: 'table.csv', support_member_path: null },
        records: [
          { id: 'C2', raw: { heading: '合成字段' }, structured: {}, locator: { source_row: 1, column: 2 } },
          { id: 'N3', raw: { cells: ['合成说明'] }, structured: {}, locator: { source_row: 2, member_path: '字段说明' } },
        ],
        selection: { status: 'review_only', choice: 'C2', refs: ['C2', 'N3'], gap: null },
      },
    });
    await mount();
    upload();
    const disclosure = await screen.findByText('隔离试用 · 查表头');
    disclosure.closest('details')!.open = true;
    fireEvent.click(screen.getByRole('button', { name: '定位字段原列' }));
    await screen.findByText('已找到原列和说明，请核对其作用范围和同文件的其他表区。');
    expect(screen.getByText('原表第 1 行 · 第 2 列')).toBeTruthy();
    expect(screen.getByText('字段说明 · 说明原行 2')).toBeTruthy();
    expect(t.commit).not.toHaveBeenCalled();
  });

  it.each([
    ['source_role_conflict', '同一表区的字段用途有冲突，请核对原说明。'],
    ['field_note_wrong_header_row', '现有说明指向数据行，请补对应当前表头原列的说明。'],
    ['insufficient_evidence', '请补适用于当前表头的字段用途说明。'],
  ])('reports the required original evidence for %s', async (reason, message) => {
    const t = transport();
    const revision = 'synthetic-source-note';
    const members = [{ member_path: 'table.csv', sheet: '', source_rows: 3, header_rows: [1] }];
    t.preview.mockResolvedValue({ ...preview, layout_model_trial: true, layout_review: { revision, members } });
    vi.spyOn(api, 'importModelTrial').mockResolvedValue({
      state: 'pending', reason, first_calls: 0, recheck_calls: 0,
      packet: {
        domain: 'import', question: '', packet_sha256: 'a'.repeat(64), records: [], candidates: [], gaps: [],
        facts: { case_id: '1', import_id: importId, revision, field: 'self_account', column_start: 1,
          header_row: 1, member_path: 'table.csv', support_member_path: null },
      },
    });
    await mount();
    upload();
    const disclosure = await screen.findByText('隔离试用 · 查表头');
    disclosure.closest('details')!.open = true;
    fireEvent.click(screen.getByRole('button', { name: '定位字段原列' }));
    await screen.findByText(message);
    expect(t.commit).not.toHaveBeenCalled();
  });

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
});
