import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StrictMode } from 'react';
import type { CaseStats, Flow, FlowPage, FundsProjection, ImportJob, ImportSession } from '../../../contracts/api-v1';
import { FundsPanel } from './FundsPanel';
import { FUNDS_PANEL_VERSION, type FundsCaseClientV1, type FundsHostActionsV1, type FundsPanelSessionV1 } from './host';

const stats = (id: string): CaseStats => ({ case_id: id, flows: 1, accounts: 2, batches: 1, amount_total: '9999999999999999.99', currency: 'CNY', amount_totals: [{ currency: 'CNY', amount: '9999999999999999.99' }] });
const flow = (id: string, tradeNo = `HOST-${id}`): Flow & { amount: string } => ({ case_id: id, id: '9007199254740993', batch_id: '9007199254740994', trade_no: tradeNo, trade_time: '2026-01-01 10:00:00', payer_account: '000HOST-A', payer_name: 'Synthetic host sender', payer_bank: '', payee_account: '000HOST-B', payee_name: 'Synthetic host recipient', payee_bank: '', amount: '9999999999999999.99', balance: null, currency: 'CNY', summary: 'Synthetic host record', channel: '', self_account: '', self_name: '' });
const page = (id: string, tradeNo?: string): FlowPage => ({ case_id: id, items: [flow(id, tradeNo)], total: 1, page: 1, page_size: 50 });
const mapping = { trade_time: '交易时间', payer_account: '付款账号', payee_account: '收款账号', amount: '金额' };
const record = (id: string, stage: ImportSession['stage'] = 'importing', surface: ImportSession['surface'] = 'v1'): ImportSession => ({ case_id: id, import_id: '0123456789abcdef0123456789abcdef', surface, filename: 'host-synthetic.csv', mode: 'A', mapping, stage, source_state: 'retained', created_at: '2026-01-01T10:00:00Z', expires_at: null, error_code: null, attempt: 1, source_available: true, recoverable: true, request: { mode: 'A', mapping, mark_accounts: [], self_accounts: [] }, result: null });

function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: Error) => void; const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; }

function session(id = '1', scopeKey = 'host-scope-one', generation = 0, overrides: Partial<FundsCaseClientV1> = {}, actions: FundsHostActionsV1 = {}) {
  const controller = new AbortController();
  const client: FundsCaseClientV1 = {
    binding: { caseId: id, scopeKey, generation }, stats: async () => stats(id), flows: async () => page(id),
    preview: async () => ({ case_id: id, import_id: record(id).import_id, filename: 'host-synthetic.csv', mode: 'A', columns: Object.values(mapping), mapping, stats: { total: 1, ok: 1, skipped: 0 }, preview: [flow(id)], self_accounts_in_file: [] }),
    repreview: async () => { throw new Error('Synthetic preview not requested'); },
    commit: async () => ({ case_id: id, import_id: record(id).import_id, started: true }),
    job: async () => ({ case_id: id, import_id: record(id).import_id, stage: 'importing', done: 0, total: 1, message: '处理中', result: null, error: null }),
    imports: async () => ({ case_id: id, items: [] }),
    sources: async (flowId, page = 1) => ({ case_id: id, flow_id: flowId, items: [], total: 0, page, page_size: 50 }),
    cleanup: async dryRun => ({ dry_run: dryRun, eligible: 0, deleted: 0, missing: 0, failed: 0, protected: 0, queued: 0 }),
    ...overrides,
  };
  const value: FundsPanelSessionV1 = { context: { caseId: id, name: `合成宿主案件${id}` }, scopeKey, generation, signal: controller.signal, client, actions };
  return { value, client, controller };
}
const host = (value: FundsPanelSessionV1 | null) => ({ version: FUNDS_PANEL_VERSION, session: value });

// Invoke the actual rendered callback to test the entry guard independently of DOM disabled handling.
function renderedClick(button: HTMLButtonElement) {
  const key = Object.keys(button).find(name => name.startsWith('__reactProps$'));
  if (!key) throw new Error('Rendered React button props are unavailable');
  const props = (button as unknown as Record<string, { onClick?: () => void }>)[key];
  if (typeof props.onClick !== 'function') throw new Error('Rendered button click handler is unavailable');
  return props.onClick;
}

describe('FundsPanel host isolation', () => {
  it('shows unknown direction with the supplied own account and no transfer arrow', async () => {
    const item: Flow = { ...flow('1'), payer_account: '', payer_name: '', payee_account: '', payee_name: '',
      raw_self_account: 'SYN-OWN', amount: '12.34', source_ledger: {
        policy: 'source-ledger-unoriented-v1', signed_amount: '12.34', source_direction: '', self_account: 'SYN-OWN',
        direction: 'unknown', economic_effect: 'unknown', economic_amount: null, reported_status: 'success',
        source_id: 'SYN-SOURCE', member_path: 'SYN.csv', source_row: 2,
        balance_change: { status: 'unknown', difference: null, before: null, after: null, basis: null,
          reason: 'direction_unknown', source_id: 'SYN-SOURCE', previous_source_row: null } } };
    const bound = session('1', 'unknown-direction', 0, { flows: async () => ({ ...page('1'), items: [item] }) });
    render(<FundsPanel host={host(bound.value)} />);
    const button = await screen.findByRole('button', { name: '核对流水 HOST-1' });
    const row = button.closest('tr')!;
    expect(row.textContent).toContain('方向未提供 · 本方 SYN-OWN');
    expect(row.textContent).toContain('资金影响未确定');
    expect(row.querySelector('.flow-arrow')?.textContent).toBe('—');
    fireEvent.click(button);
    expect(within(await screen.findByRole('dialog', { name: '流水核对' })).getByText(/方向未提供 · 本方 SYN-OWN/)).toBeTruthy();
  });

  it.each(['confirmed_event', 'observation'] as const)('keeps the null balance description accurate for %s', async granularity => {
    const item = { ...flow('1'), granularity, balance: null };
    const bound = session('1', 'balance-context', 0, { flows: async () => ({ ...page('1'), items: [item] }) });
    render(<FundsPanel host={host(bound.value)} />);
    fireEvent.click(await screen.findByRole('button', { name: '核对流水 HOST-1' }));
    const dialog = await screen.findByRole('dialog', { name: '流水核对' });
    expect(within(dialog).getByText('交易后余额').nextElementSibling?.textContent)
      .toBe(granularity === 'confirmed_event' ? '见各来源流水' : '未提供');
    expect(item.balance).toBeNull();
  });

  it('keeps the Chinese file selection area associated with the native input and locks it during parsing', async () => {
    const pending = deferred<Awaited<ReturnType<FundsCaseClientV1['preview']>>>();
    const bound = session('1', 'upload-label', 0, { preview: vi.fn(() => pending.promise) });
    render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    const input = screen.getByLabelText<HTMLInputElement>('选择流水文件'), area = input.labels![0];
    expect(area.htmlFor).toBe(input.id); expect(area.textContent).toContain('选择 CSV、Excel、ZIP 或 RAR');
    const descriptions = input.getAttribute('aria-describedby')!.split(/\s+/).map(id => document.getElementById(id)?.textContent);
    expect(descriptions).toEqual(['支持 UTF-8／GBK CSV、XLSX、XLS 和已知格式的银行反馈 ZIP／RAR',
      '普通文件每次选择一个；批量独立文件请使用画布工作台的多文件导入。']);
    expect(input.accept).toBe('.csv,.tsv,.txt,.xlsx,.xls,.zip,.rar'); input.focus(); expect(document.activeElement).toBe(input);
    const file = new File(['synthetic'], '中文合成流水.csv', { type: 'text/csv' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(input.disabled).toBe(true); expect(area.getAttribute('aria-busy')).toBe('true');
    expect(area.textContent).toContain('正在解析文件…');
    expect(bound.client.preview).toHaveBeenCalledWith(file, expect.any(AbortSignal));
    await act(async () => pending.reject(new Error('合成解析失败')));
    expect(input.disabled).toBe(false); expect(area.getAttribute('aria-busy')).toBe('false');
  });

  it('refreshes on reactivation and data revisions while retaining applied filters and unapplied drafts', async () => {
    const bound = session(); const flows = vi.spyOn(bound.client, 'flows');
    const { rerender } = render(<FundsPanel host={host(bound.value)} active revision={0} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: 'APPLIED-ACCOUNT' } });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(flows).toHaveBeenLastCalledWith({ account: 'APPLIED-ACCOUNT', kw: '', page: 1 }, expect.any(AbortSignal)));
    fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: 'UNSUBMITTED-DRAFT' } });
    const before = flows.mock.calls.length;
    rerender(<FundsPanel host={host(bound.value)} active={false} revision={1} />);
    expect(flows).toHaveBeenCalledTimes(before);
    rerender(<FundsPanel host={host(bound.value)} active revision={1} />);
    await waitFor(() => expect(flows).toHaveBeenCalledTimes(before + 1));
    expect(flows).toHaveBeenLastCalledWith({ account: 'APPLIED-ACCOUNT', kw: '', page: 1 }, expect.any(AbortSignal));
    expect((screen.getByLabelText('账号筛选') as HTMLInputElement).value).toBe('UNSUBMITTED-DRAFT');
    rerender(<FundsPanel host={host(bound.value)} active revision={2} />);
    await waitFor(() => expect(flows).toHaveBeenCalledTimes(before + 2));
  });

  it('rejects a read that finishes after the panel is hidden', async () => {
    const pending = deferred<FlowPage>();
    const bound = session('1', 'active-scope', 0, { flows: () => pending.promise });
    const { rerender } = render(<FundsPanel host={host(bound.value)} active />);
    rerender(<FundsPanel host={host(bound.value)} active={false} />);
    await act(async () => pending.resolve(page('1', 'STALE-HIDDEN')));
    expect(screen.queryByRole('button', { name: '核对流水 STALE-HIDDEN' })).toBeNull();
    bound.client.flows = async () => page('1', 'FRESH-RETURN');
    rerender(<FundsPanel host={host(bound.value)} active />);
    await screen.findByRole('button', { name: '核对流水 FRESH-RETURN' });
  });

  it.each([
    { trigger: '导入记录', title: '导入记录' },
    { trigger: '导入流水', title: '导入流水' },
    { trigger: '核对流水 HOST-1', title: '流水核对' },
  ])('removes the $title dialog while inactive without revoking the session or losing the query draft', async ({ trigger, title }) => {
    const bound = session(); const { container, rerender } = render(<FundsPanel host={host(bound.value)} active />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: '保留面板草稿' } });
    fireEvent.click(screen.getByRole('button', { name: trigger }));
    const dialog = await screen.findByRole('dialog', { name: title });
    rerender(<FundsPanel host={host(bound.value)} active={false} />);
    expect(container.querySelector('dialog[open]')).toBeNull(); expect(dialog.isConnected).toBe(false); expect(bound.controller.signal.aborted).toBe(false);
    rerender(<FundsPanel host={host(bound.value)} active />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    expect((screen.getByLabelText('账号筛选') as HTMLInputElement).value).toBe('保留面板草稿');
    if (title !== '流水核对') await screen.findByRole('dialog', { name: title });
    else expect(container.querySelector('dialog[open]')).toBeNull();
  });

  it('preserves a new upload preview, mode, edited mapping and main-account choices across inactive round trips', async () => {
    const bound = session(), parse = bound.client.preview;
    bound.client.preview = vi.fn(async (file, signal) => ({ ...await parse(file, signal), self_accounts_in_file: [{ account: '000HOST-A', name: '合成主账号', count: 1 }] }));
    const repreview = vi.spyOn(bound.client, 'repreview'), commit = vi.spyOn(bound.client, 'commit');
    const { container, rerender } = render(<FundsPanel host={host(bound.value)} active />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'host-synthetic.csv')] } });
    await screen.findByRole('button', { name: '确认导入' });
    fireEvent.click(screen.getByRole('checkbox', { name: /合成主账号/ }));
    fireEvent.change(screen.getByLabelText('流水文件类型'), { target: { value: 'B' } });
    fireEvent.change(screen.getByLabelText('映射本方账号'), { target: { value: '付款账号' } });
    for (let round = 0; round < 2; round++) {
      rerender(<FundsPanel host={host(bound.value)} active={false} />);
      expect(container.querySelector('dialog[open]')).toBeNull();
      rerender(<FundsPanel host={host(bound.value)} active />);
      expect(screen.queryByText('host-synthetic.csv')).not.toBeNull();
      expect((screen.getByLabelText('流水文件类型') as HTMLSelectElement).value).toBe('B');
      expect((screen.getByLabelText('映射本方账号') as HTMLSelectElement).value).toBe('付款账号');
      expect((screen.getByRole('checkbox', { name: /合成主账号/ }) as HTMLInputElement).checked).toBe(false);
      expect((screen.getByRole('button', { name: '确认导入' }) as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByRole('button', { name: '按当前映射重新预览' })).toBeTruthy();
    }
    expect(bound.client.preview).toHaveBeenCalledOnce(); expect(repreview).not.toHaveBeenCalled(); expect(commit).not.toHaveBeenCalled();
  });

  it('uses only the injected current-case client and does not change standalone URL state', async () => {
    const before = window.location.href;
    const fetch = vi.spyOn(globalThis, 'fetch');
    const bound = session(); const statistics = vi.spyOn(bound.client, 'stats');
    const { container } = render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    expect(statistics).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(container.querySelector('.funds-trace.funds-panel > section.main-workspace')).toBeTruthy();
    expect(container.querySelector('main')).toBeNull();
    expect(container.querySelector('.sidebar')).toBeNull();
    expect(screen.queryByRole('button', { name: '新建案件' })).toBeNull();
    fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: '000HOST-A' } });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    expect(window.location.href).toBe(before);
    expect(fetch).not.toHaveBeenCalled();
    fetch.mockRestore();
  });

  it.each([{ scopeKey: 'host-scope-two', generation: 0 }, { scopeKey: 'host-scope-one', generation: 1 }])('clears dialogs and late responses when the same case is rebound to %o', async next => {
    const oldStats = deferred<CaseStats>(); const oldPage = deferred<FlowPage>();
    const oldSignals: AbortSignal[] = [];
    const first = session('1', 'host-scope-one', 0, {
      stats: signal => { oldSignals.push(signal!); return oldStats.promise; },
      flows: (_filters, signal) => { oldSignals.push(signal!); return oldPage.promise; },
    });
    const second = session('1', next.scopeKey, next.generation, { flows: async () => page('1', 'NEW-SCOPE') });
    const { rerender } = render(<FundsPanel host={host(first.value)} />);
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    expect(screen.getByRole('dialog', { name: '导入流水' })).toBeTruthy();
    rerender(<FundsPanel host={host(second.value)} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(oldSignals.every(signal => signal.aborted)).toBe(true);
    await screen.findByRole('button', { name: '核对流水 NEW-SCOPE' });
    await act(async () => { oldStats.resolve(stats('1')); oldPage.resolve(page('1', 'OLD-SCOPE')); });
    expect(screen.queryByRole('button', { name: '核对流水 OLD-SCOPE' })).toBeNull();
    expect(screen.getByRole('button', { name: '核对流水 NEW-SCOPE' })).toBeTruthy();
  });

  it('clears data and dialogs when the host revokes the current context signal', async () => {
    const bound = session(); const { container } = render(<FundsPanel host={host(bound.value)} />);
    fireEvent.click(await screen.findByRole('button', { name: '核对流水 HOST-1' }));
    expect(screen.getByRole('dialog')).toBeTruthy();
    await act(async () => bound.controller.abort());
    expect(screen.getByRole('heading', { name: '案件会话已撤销' })).toBeTruthy();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(container.textContent).not.toContain('999,999,999');
  });

  it('aborts in-flight progress and stops polling on context removal without claiming server cancellation', async () => {
    const pending = deferred<ImportJob>(); const signals: AbortSignal[] = [];
    const job = vi.fn((_id: string, signal?: AbortSignal) => { signals.push(signal!); return pending.promise; });
    const bound = session('1', 'poll-scope', 0, { imports: async () => ({ case_id: '1', items: [record('1')] }), job });
    const { rerender } = render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.click(screen.getByRole('button', { name: '导入记录' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看进度' }));
    await waitFor(() => expect(job).toHaveBeenCalledOnce());
    rerender(<FundsPanel host={host(null)} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(signals[0].aborted).toBe(true);
    await act(async () => { pending.resolve({ case_id: '1', import_id: record('1').import_id, stage: 'importing', done: 1, total: 2, message: '仍在服务端运行', result: null, error: null }); await new Promise(resolve => setTimeout(resolve, 350)); });
    expect(job).toHaveBeenCalledOnce();
    expect(screen.getByRole('heading', { name: '未选择案件' })).toBeTruthy();
    expect(screen.queryByText('仍在服务端运行')).toBeNull();
  });

  it('keeps a new automatic import running while hidden and refreshes on reactivation without another write', async () => {
    const pending = deferred<ImportJob>();
    const table = { profile: 'funds-trace-table-v5', record_total: 1, accepted: 0, unresolved: 1,
      non_monetary: 0, non_transaction: 0, auxiliary: 0, reason_counts: { missing_amount: 1 },
      currency_totals: {}, members: [], unresolved_examples: [], documents: [], mapping_evidence: [] };
    const commit = vi.fn(async () => ({ case_id: '1', import_id: record('1').import_id, started: true }));
    const job = vi.fn(() => pending.promise), statistics = vi.fn(async () => stats('1'));
    const bound = session('1', 'automatic-retained', 0, { stats: statistics, commit, job,
      preview: async () => ({ case_id: '1', import_id: record('1').import_id, filename: 'host-synthetic.csv', mode: 'A',
        columns: Object.values(mapping), mapping, automatic: true, table_summary: table,
        stats: { total: 1, ok: 0, skipped: 1 }, preview: [], self_accounts_in_file: [] }) });
    const { rerender } = render(<FundsPanel host={host(bound.value)} active />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'host-synthetic.csv')] } });
    await waitFor(() => expect(job).toHaveBeenCalledOnce());
    rerender(<FundsPanel host={host(bound.value)} active={false} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    await act(async () => pending.resolve({ case_id: '1', import_id: record('1').import_id, stage: 'done', done: 1, total: 1,
      message: '完成', error: null, result: { total: 1, ok: 0, skipped: 1, rows_dup: 0, batch_id: 'SYN-BATCH',
        bad_examples: [], self_accounts: [], self_accounts_count: 0, main_matched: [], accounts_in_batch: 0, main_detected: [], table_summary: table } }));
    expect(screen.queryByRole('dialog')).toBeNull(); expect(statistics).toHaveBeenCalledOnce();
    rerender(<FundsPanel host={host(bound.value)} active />);
    await screen.findByRole('heading', { name: '自动处理完成' });
    await waitFor(() => expect(statistics).toHaveBeenCalledTimes(2));
    expect(commit).toHaveBeenCalledOnce(); expect(job).toHaveBeenCalledOnce();
    expect(screen.getByText(/已保存 1 条记录 · 新增流水记录/).textContent).toContain('新增流水记录 0 条');
  });

  it('does not start polling after an old in-flight commit returns following unmount', async () => {
    const pending = deferred<{ case_id: string; import_id: string; started: boolean }>();
    let commitSignal: AbortSignal | undefined;
    const job = vi.fn();
    const bound = session('1', 'write-scope', 0, { commit: (_id, _body, signal) => { commitSignal = signal; return pending.promise; }, job });
    const { unmount } = render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'host-synthetic.csv')] } });
    fireEvent.click(await screen.findByRole('button', { name: '确认导入' }));
    await waitFor(() => expect(commitSignal).toBeTruthy());
    unmount(); expect(commitSignal?.aborted).toBe(true);
    await act(async () => { pending.resolve({ case_id: '1', import_id: record('1').import_id, started: true }); });
    expect(job).not.toHaveBeenCalled();
  });

  it('disables missing host navigation and download capabilities with an explanation', async () => {
    const bound = session('1', 'no-actions', 0, {
      imports: async () => ({ case_id: '1', items: [record('1', 'error', 'legacy')] }),
      sources: async flowId => ({ case_id: '1', flow_id: flowId, total: 1, page: 1, page_size: 50, items: [{ source_id: '0123456789abcdef0123456789abcdef', filename: 'host-original.csv', sha256: 'a'.repeat(64), size_bytes: 10, mode: 'A', mapping, sheet: '', row_kind: 'csv_record', source_row: 2, parser_version: 'synthetic', disposition: 'imported', origin: 'upload', imported_at: '2026-01-01T10:00:00Z', source_available: true }] }),
    });
    render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    expect((screen.getByRole('button', { name: /打开 G6 图谱/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('宿主未提供图谱导航能力。')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '导入记录' }));
    const legacy = await screen.findByRole('button', { name: /在 G6 工作区查看/ });
    expect((legacy as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('宿主未提供旧版工作区导航。')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '返回流水核对' }));
    fireEvent.click(await screen.findByRole('button', { name: '核对流水 HOST-1' }));
    const download = await screen.findByRole('button', { name: /下载原始文件/ });
    expect((download as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('宿主未提供原件下载能力。')).toBeTruthy();
  });

  it('changes only graph availability while navigation is pending without replacing the session or query draft', async () => {
    const openGraph = vi.fn(async (_signal?: AbortSignal) => {}), onDraftChange = vi.fn();
    const bound = session('1', 'navigation-presentation', 0, {}, { openGraph });
    const statistics = vi.spyOn(bound.client, 'stats'), flows = vi.spyOn(bound.client, 'flows');
    const readyWorkspace = { retained: true, navigationPending: false, onDraftChange };
    const pendingWorkspace = { ...readyWorkspace, navigationPending: true };
    const { rerender } = render(<FundsPanel host={host(bound.value)} workspace={readyWorkspace} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    const account = screen.getByLabelText<HTMLInputElement>('账号筛选');
    const keyword = screen.getByLabelText<HTMLInputElement>('摘要或流水号');
    const button = screen.getByRole<HTMLButtonElement>('button', { name: /进入画图/ });
    fireEvent.change(account, { target: { value: 'PENDING-ACCOUNT-DRAFT' } });
    fireEvent.change(keyword, { target: { value: 'PENDING-KEYWORD-DRAFT' } });
    const readSignal = statistics.mock.calls[0][0], draftCalls = onDraftChange.mock.calls.length;
    expect(button.disabled).toBe(false);
    rerender(<FundsPanel host={host(bound.value)} workspace={pendingWorkspace} />);
    expect(screen.getByLabelText('账号筛选')).toBe(account);
    expect(screen.getByLabelText('摘要或流水号')).toBe(keyword);
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(openGraph).not.toHaveBeenCalled();
    expect(screen.getByRole<HTMLButtonElement>('button', { name: /导入流水/ }).disabled).toBe(false);
    expect(statistics).toHaveBeenCalledOnce(); expect(flows).toHaveBeenCalledOnce();
    expect(readSignal?.aborted).toBe(false); expect(bound.controller.signal.aborted).toBe(false);
    expect(onDraftChange).toHaveBeenCalledTimes(draftCalls);
    rerender(<FundsPanel host={host(bound.value)} workspace={readyWorkspace} />);
    expect(button.disabled).toBe(false);
    expect(account.value).toBe('PENDING-ACCOUNT-DRAFT'); expect(keyword.value).toBe('PENDING-KEYWORD-DRAFT');
    expect(statistics).toHaveBeenCalledOnce(); expect(flows).toHaveBeenCalledOnce();
    expect(readSignal?.aborted).toBe(false); expect(bound.controller.signal.aborted).toBe(false);
    expect(onDraftChange).toHaveBeenCalledTimes(draftCalls);
    await act(async () => { fireEvent.click(button); });
    expect(openGraph).toHaveBeenCalledOnce(); expect(openGraph.mock.calls[0][0]?.aborted).toBe(false);
  });

  it('guards the rendered graph action callback during navigation even when DOM disabled handling is bypassed', async () => {
    const openGraph = vi.fn(async (_signal?: AbortSignal) => {});
    const bound = session('1', 'navigation-entry-guard', 0, {}, { openGraph });
    const pendingWorkspace = { retained: true, navigationPending: true };
    const { rerender } = render(<FundsPanel host={host(bound.value)} workspace={pendingWorkspace} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    const button = screen.getByRole<HTMLButtonElement>('button', { name: /进入画图/ });
    await act(async () => renderedClick(button)());
    expect(openGraph).not.toHaveBeenCalled();
    const readyWorkspace = { ...pendingWorkspace, navigationPending: false };
    rerender(<FundsPanel host={host(bound.value)} workspace={readyWorkspace} />);
    await act(async () => renderedClick(button)());
    expect(openGraph).toHaveBeenCalledOnce(); expect(openGraph.mock.calls[0][0]?.aborted).toBe(false);
  });

  it('keeps an in-flight graph action busy and alive through navigation presentation changes', async () => {
    const gate = deferred<void>(); let received: AbortSignal | undefined;
    const openGraph = vi.fn((signal?: AbortSignal) => { received = signal; return gate.promise; });
    const bound = session('1', 'navigation-existing-action', 0, {}, { openGraph });
    const statistics = vi.spyOn(bound.client, 'stats'), flows = vi.spyOn(bound.client, 'flows');
    const readyWorkspace = { retained: true, navigationPending: false };
    const pendingWorkspace = { ...readyWorkspace, navigationPending: true };
    const { rerender } = render(<FundsPanel host={host(bound.value)} workspace={readyWorkspace} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    const button = screen.getByRole<HTMLButtonElement>('button', { name: /进入画图/ });
    fireEvent.click(button);
    expect(openGraph).toHaveBeenCalledOnce(); expect(button.disabled).toBe(true); expect(received?.aborted).toBe(false);
    rerender(<FundsPanel host={host(bound.value)} workspace={pendingWorkspace} />);
    expect(button.disabled).toBe(true); expect(received?.aborted).toBe(false);
    rerender(<FundsPanel host={host(bound.value)} workspace={readyWorkspace} />);
    expect(button.disabled).toBe(true); expect(received?.aborted).toBe(false);
    fireEvent.click(button); expect(openGraph).toHaveBeenCalledOnce();
    expect(statistics).toHaveBeenCalledOnce(); expect(flows).toHaveBeenCalledOnce();
    await act(async () => gate.resolve());
    expect(button.disabled).toBe(false);
    await act(async () => { fireEvent.click(button); });
    expect(openGraph).toHaveBeenCalledTimes(2);
  });

  it('preserves graph action errors across flag-only navigation updates and supports hosts without the flag', async () => {
    const openGraph = vi.fn(async () => { throw new Error('合成当前导航失败'); });
    const bound = session('1', 'navigation-error-presentation', 0, {}, { openGraph });
    const legacyWorkspace = { retained: true }, pendingWorkspace = { ...legacyWorkspace, navigationPending: true };
    const { rerender } = render(<FundsPanel host={host(bound.value)} workspace={legacyWorkspace} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    const button = screen.getByRole<HTMLButtonElement>('button', { name: /进入画图/ });
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toBe('合成当前导航失败');
    rerender(<FundsPanel host={host(bound.value)} workspace={pendingWorkspace} />);
    expect(screen.getByRole('alert')).toBe(alert); expect(alert.textContent).toBe('合成当前导航失败');
    const readyWorkspace = { ...legacyWorkspace, navigationPending: false };
    rerender(<FundsPanel host={host(bound.value)} workspace={readyWorkspace} />);
    expect(screen.getByRole('alert')).toBe(alert); expect(alert.textContent).toBe('合成当前导航失败');
    expect(button.disabled).toBe(false); expect(openGraph).toHaveBeenCalledOnce();
    expect(bound.controller.signal.aborted).toBe(false);
  });

  it('shows host action errors but discards a late error after the case changes', async () => {
    const pending = deferred<void>();
    const first = session('1', 'actions-one', 0, {}, { openGraph: () => pending.promise });
    const second = session('2', 'actions-two');
    const { rerender } = render(<FundsPanel host={host(first.value)} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.click(screen.getByRole('button', { name: /打开 G6 图谱/ }));
    rerender(<FundsPanel host={host(second.value)} />);
    await act(async () => pending.reject(new Error('旧宿主导航错误')));
    expect(screen.queryByText('旧宿主导航错误')).toBeNull();
    const failing = session('2', 'actions-three', 0, {}, { openGraph: async () => { throw new Error('当前宿主导航失败'); } });
    rerender(<FundsPanel host={host(failing.value)} />);
    fireEvent.click(screen.getByRole('button', { name: /打开 G6 图谱/ }));
    expect((await screen.findByRole('alert')).textContent).toContain('当前宿主导航失败');
  });

  it('immediately aborts the signal supplied to a non-HTTP host action before React cleanup', async () => {
    const gate = deferred<void>(); let received: AbortSignal | undefined; let sideEffects = 0;
    const openGraph = vi.fn((signal?: AbortSignal) => {
      received = signal;
      return gate.promise.then(() => { if (!signal?.aborted) sideEffects += 1; });
    });
    const bound = session('1', 'non-http-host', 0, {}, { openGraph });
    const { unmount } = render(<StrictMode><FundsPanel host={host(bound.value)} /></StrictMode>);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    const button = screen.getByRole('button', { name: /打开 G6 图谱/ });
    fireEvent.click(button);
    expect(received?.aborted).toBe(false);
    await act(async () => {
      bound.controller.abort();
      // No await/rerender/cleanup between host revocation and this assertion.
      expect(received?.aborted).toBe(true);
      button.click();
      expect(openGraph).toHaveBeenCalledOnce();
      gate.resolve();
      await Promise.resolve();
    });
    expect(sideEffects).toBe(0);
    unmount(); button.click(); expect(openGraph).toHaveBeenCalledOnce();
  });

  it('does not dispatch the next host query when revocation happens inside the preceding query', async () => {
    const flows = vi.fn(async () => page('1'));
    const bound = session('1', 'query-revocation', 0, { flows });
    let received: AbortSignal | undefined;
    bound.client.stats = async signal => { received = signal; bound.controller.abort(); return stats('1'); };
    render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('heading', { name: '案件会话已撤销' });
    expect(received?.aborted).toBe(true);
    expect(flows).not.toHaveBeenCalled();
  });

  it('does not dispatch an enabled captured UI action after revocation or unmount', async () => {
    const openGraph = vi.fn(); const bound = session('1', 'old-handler', 0, {}, { openGraph });
    const { unmount } = render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    const button = screen.getByRole('button', { name: /打开 G6 图谱/ });
    expect((button as HTMLButtonElement).disabled).toBe(false);
    act(() => { bound.controller.abort(); button.click(); });
    expect(openGraph).not.toHaveBeenCalled();
    unmount(); button.click(); expect(openGraph).not.toHaveBeenCalled();
  });
});

describe('selected transaction summaries in existing panel', () => {
  it.each([true, false])('counts confirmed pairs once and keeps unresolved records, confirmed=%s', async exact => {
    const funds: FundsProjection = { policy:'evidence-bounded-funds-v1', status:exact ? 'exact' as const : 'bounded' as const, observation_count:2,
      confirmed_event_count:exact ? 1 : 0, unresolved_observation_count:exact ? 0 : 2, count:{lower:exact ? 1 : 0, upper:exact ? 1 : 2},
      totals:[{currency:'CNY',amount:exact ? '100.00' : null,lower:exact ? '100.00' : '0.00',upper:exact ? '100.00' : '200.00'}] };
    const total = exact ? '100.00' : '200.00';
    const statistics = vi.fn(async () => ({...stats('1'),flows:exact ? 1 : 2,amount_total:total,amount_totals:[{currency:'CNY',amount:total}],funds}));
    const flows = vi.fn(async () => ({...page('1'),funds, items:[{...flow('1'),amount:'100.00',balance:'73.25'}]}));
    const bound = session('1', 'funds-projection', 0, {stats:statistics, flows});
    const sources = vi.spyOn(bound.client,'sources');
    render(<FundsPanel host={host(bound.value)} />);
    await screen.findByRole('button', {name:'核对流水 HOST-1'});
    const summary = screen.getByLabelText('案件汇总');
    expect(summary.textContent).toContain(exact ? '1 笔' : '2 笔');
    expect(summary.textContent).toContain(`${total} CNY`);
    expect(summary.textContent).not.toMatch(/待核/);
    expect(screen.queryByLabelText('事件关联口径')).toBeNull();
    expect(within(screen.getByLabelText('流水明细')).getByRole('heading', {level:2}).textContent).toBe('交易明细 1');
    expect(statistics).toHaveBeenCalledTimes(1); expect(flows).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', {name:/本金|来源份额/})).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'核对流水 HOST-1'}));
    expect(await screen.findByRole('dialog',{name:'流水核对'})).toBeTruthy();
    expect(screen.getByText('73.25')).toBeTruthy();
    await waitFor(() => expect(sources).toHaveBeenCalledWith('9007199254740993',1,expect.any(AbortSignal)));
    expect(statistics).toHaveBeenCalledTimes(1); expect(flows).toHaveBeenCalledTimes(1);
  });
});

describe('FundsPanel feedback ports', () => {
  it.each(['ready', 'error'] as const)('reads and displays an exact older %s task without automatically previewing or writing', async stage => {
    const older = { ...record('1', stage), filename: `synthetic-older-${stage}.csv` };
    const recent = { ...record('1', 'done'), import_id: 'fedcba9876543210fedcba9876543210', filename: 'synthetic-recent.csv' };
    const imports = vi.fn(async (_signal: AbortSignal, importId?: string) => ({ case_id: '1', items: importId ? [older] : [recent] }));
    const bound = session('1', 'older-task', 0, { imports });
    const preview = vi.spyOn(bound.client, 'preview'), repreview = vi.spyOn(bound.client, 'repreview');
    const commit = vi.spyOn(bound.client, 'commit'), job = vi.spyOn(bound.client, 'job');
    render(<FundsPanel host={host(bound.value)} workspace={{ openImportRequest: { importId: older.import_id, sequence: 1 } }} />);
    const dialog = await screen.findByRole('dialog', { name: '导入记录' });
    expect(await within(dialog).findByText(older.filename)).toBeTruthy();
    expect(within(dialog).getByRole('button', { name: `继续导入 ${older.filename}` })).toHaveProperty('disabled', false);
    expect(imports).toHaveBeenCalledWith(expect.any(AbortSignal), older.import_id);
    expect(imports.mock.calls.every(([, importId]) => importId === older.import_id)).toBe(true);
    expect(preview).not.toHaveBeenCalled(); expect(repreview).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled(); expect(job).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: '查看最近导入记录' }));
    expect(await screen.findByText(recent.filename)).toBeTruthy();
    expect(screen.queryByText(older.filename)).toBeNull();
    expect(imports.mock.calls.at(-1)).toHaveLength(1);
    expect(commit).not.toHaveBeenCalled(); expect(repreview).not.toHaveBeenCalled();
  });

  it.each(['missing', 'foreign', 'different', 'legacy'] as const)('does not substitute another record for an exact %s task', async kind => {
    const original = record('1', 'ready');
    const unexpected = { ...original, filename: 'SYN-DO-NOT-PUBLISH.csv',
      case_id: kind === 'foreign' ? '2' : '1',
      import_id: kind === 'different' ? 'fedcba9876543210fedcba9876543210' : original.import_id,
      surface: kind === 'legacy' ? 'legacy' as const : 'v1' as const };
    const imports = vi.fn(async () => ({ case_id: '1', items: kind === 'missing' ? [] : [unexpected] }));
    const bound = session('1', 'exact-task-boundary', 0, { imports });
    const preview = vi.spyOn(bound.client, 'preview'), commit = vi.spyOn(bound.client, 'commit');
    render(<FundsPanel host={host(bound.value)} workspace={{ openImportRequest: { importId: original.import_id, sequence: 1 } }} />);
    const dialog = await screen.findByRole('dialog', { name: '导入记录' });
    if (kind === 'foreign') await within(dialog).findByText('导入记录与当前案件不一致。');
    else await within(dialog).findByText('未找到可在此核对的本案导入记录。');
    expect(screen.queryByText(unexpected.filename)).toBeNull();
    expect(preview).not.toHaveBeenCalled(); expect(commit).not.toHaveBeenCalled();
  });

  it.each([true, false])('routes an unknown original import through its optional host port (%s)', hosted => {
    const port = { notify: vi.fn(), task: vi.fn() };
    const bound = session('1', 'feedback-scope', 1, { commit: vi.fn(async () => { throw new Error('合成提交回执丢失'); }) });
    const view = render(<FundsPanel host={host(bound.value)} feedback={hosted ? port : undefined} />);
    return (async () => {
      await screen.findByRole('button', { name: '核对流水 HOST-1' });
      fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
      fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } });
      fireEvent.click(await screen.findByRole('button', { name: '确认导入' }));
      await screen.findByText('合成提交回执丢失');
      if (hosted) {
        await waitFor(() => expect(port.task).toHaveBeenCalledWith(expect.objectContaining({ id: `import:${record('1').import_id}`, status: 'unknown' })));
        expect(document.querySelector('.ff-dock')).toBeNull();
      } else {
        expect(await screen.findByText(/导入结果暂无法确认/)).toBeTruthy();
        expect(screen.getByRole('button', { name: '重新查询进度' })).toBeTruthy();
        expect(document.querySelector('.ff-entry,.ff-panel,.ff-dock')).toBeNull();
      }
      fireEvent.click(screen.getByRole('button', { name: '关闭进度' }));
      expect(screen.queryByRole('dialog', { name: '导入流水' })).toBeNull();
      expect(bound.client.commit).toHaveBeenCalledOnce();
      act(() => bound.controller.abort());
      expect(screen.queryByText('导入结果待核对')).toBeNull();
      view.unmount();
    })();
  });
});

describe('settlement hint lifetime', () => {
  it('never reopens a closed dialog or applies a previous hint to an explicit new attempt using the same import ID', async () => {
    const bound = session('1', 'settlement-lifetime', 1, {
      commit: vi.fn(async () => { throw new Error('合成提交未确认'); }),
      job: vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: record('1').import_id, stage: 'error', done: 0, total: 1, message: '失败', error: '合成最新任务确认失败', result: null })),
    });
    const view = render(<FundsPanel host={host(bound.value)} workspace={{}} />);
    await screen.findByRole('button', { name: '核对流水 HOST-1' });
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } });
    fireEvent.click(await screen.findByRole('button', { name: '确认导入' })); await screen.findByText('合成提交未确认');
    fireEvent.click(screen.getByRole('button', { name: '关闭进度' }));
    const hint = { importId: record('1').import_id, sequence: 1 };
    view.rerender(<FundsPanel host={host(bound.value)} workspace={{ importSettlement: hint }} />);
    expect(screen.queryByRole('dialog', { name: '导入流水' })).toBeNull(); expect(bound.client.job).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } });
    fireEvent.click(await screen.findByRole('button', { name: '确认导入' })); await screen.findByText('合成提交未确认');
    expect(bound.client.commit).toHaveBeenCalledTimes(2); expect(bound.client.job).not.toHaveBeenCalled();
    view.rerender(<FundsPanel host={host(bound.value)} workspace={{ importSettlement: { ...hint, sequence: 2 } }} />);
    await screen.findByText('合成最新任务确认失败');
    expect(bound.client.job).toHaveBeenCalledOnce(); expect(bound.client.commit).toHaveBeenCalledTimes(2);
    view.unmount();
  });
});
