import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import type { Case, CaseStats, Flow, FlowPage, ImportPreview, ImportJob, ImportSession, ImportSessionList, FlowSource, FlowSourceList } from '../../contracts/api-v1';
import { App } from './App';
import { ApiError, type FundsApi } from './lib/api';
import { WORKSPACE_CHANNEL } from './standalone/workspaceBridge';

const cases: Case[] = [{ id: '1', name: '合成案件甲', created_at: null }, { id: '2', name: '合成案件乙', created_at: null }];
const stats = (id: string): CaseStats => ({ case_id: id, flows: 1, accounts: 2, batches: 1, amount_total: '9999999999999999.99', currency: 'CNY', amount_totals: [{ currency: 'CNY', amount: '9999999999999999.99' }] });
const row = (id: string): Flow => ({ id: '9007199254740993', case_id: id, batch_id: '9007199254740994', trade_no: `SYN-${id}`, trade_time: '2026-01-01 10:00:00', payer_name: `Synthetic ${id}`, payer_account: '000SYN-A', payer_bank: '', payee_name: 'Synthetic recipient', payee_account: '000SYN-B', payee_bank: '', amount: '9999999999999999.99', balance: null, currency: 'CNY', summary: 'Synthetic transfer', channel: '', self_account: '', self_name: '' });
const page = (id: string, items: Flow[] = [row(id)]): FlowPage => ({ case_id: id, items, total: items.length, page: 1, page_size: 50 });
const preview: ImportPreview = { case_id: '1', import_id: '0123456789ab', filename: 'synthetic.csv', mode: 'A', columns: ['交易时间', '付款账号', '收款账号', '交易金额'], mapping: { trade_time: '交易时间', payer_account: '付款账号', payee_account: '收款账号', amount: '交易金额' }, stats: { total: 1, ok: 1, skipped: 0 }, preview: [row('1')], self_accounts_in_file: [] };
const completed: ImportJob = { case_id: '1', import_id: preview.import_id, stage: 'done', done: 1, total: 1, message: '完成', error: null, result: { total: 1, ok: 1, skipped: 0, rows_dup: 0, batch_id: '10', bad_examples: [], self_accounts: [], self_accounts_count: 0, main_matched: [], accounts_in_batch: 2, main_detected: [] } };
const session = (overrides: Partial<ImportSession> = {}): ImportSession => ({ case_id: '1', import_id: preview.import_id, surface: 'v1', filename: preview.filename, mode: preview.mode, mapping: preview.mapping, stage: 'error', source_state: 'retained', created_at: '2026-01-01 10:00:00', expires_at: '2026-02-01 10:00:00', error_code: 'interrupted', attempt: 1, source_available: true, recoverable: true, request: { mode: preview.mode, mapping: preview.mapping, mark_accounts: [], self_accounts: ['SYN-DECLARED'] }, result: null, ...overrides });
const source = (overrides: Partial<FlowSource> = {}): FlowSource => ({ source_id: '0123456789abcdef0123456789abcdef', filename: 'source.csv', sha256: 'a'.repeat(64), size_bytes: 128, mode: 'A', mapping: preview.mapping, sheet: '', row_kind: 'csv_record', source_row: 6, parser_version: 'synthetic-v1', disposition: 'imported', origin: 'upload', imported_at: '2026-01-01 10:00:00', source_available: true, ...overrides });

function fakeApi(): FundsApi {
  return {
    cases: async () => ({ items: cases }), createCase: async name => ({ id: '3', name, created_at: null }),
    stats: async id => stats(id), flows: async id => page(id),
    preview: async () => preview, repreview: async () => preview,
    commit: async () => ({ case_id: '1', import_id: preview.import_id, started: true }), job: async () => completed,
    imports: async id => ({ case_id: id, items: [] }),
    sources: async (id, flowId, page = 1) => ({ case_id: id, flow_id: flowId, items: [], total: 0, page, page_size: 50 }),
    downloadSource: async () => {}, downloadImportReport: async () => {},
    cleanup: async (_id, dryRun = true) => ({ dry_run: dryRun, eligible: 0, deleted: 0, missing: 0, failed: 0, protected: 0, queued: 0 }),
  };
}

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }

function embeddedHost() {
  const messages = vi.spyOn(window, 'postMessage').mockImplementation(() => {});
  const send = (data: Record<string, unknown>) => act(() => window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, source: window, data: { channel: WORKSPACE_CHANNEL, version: 1, ...data } })));
  const bind = (extra = {}) => send({ type: 'context', caseId: '1', name: '合成案件甲', generation: 1, retainedCaseIds: [], active: true, ...extra });
  const requests = () => messages.mock.calls.map(call => call[0]).filter(message => message.type === 'request');
  const answer = async (action: string, ok = true, error?: string) => {
    await waitFor(() => expect(requests().at(-1)?.action).toBe(action));
    const request = requests().at(-1)!; send({ type: 'result', requestId: request.requestId, ok, error }); return request;
  };
  const upload = async () => {
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv', { type: 'text/csv' })] } });
    fireEvent.click(await screen.findByRole('button', { name: '确认导入' }));
  };
  return { messages, send, bind, requests, answer, upload, close: () => messages.mockRestore() };
}

describe('FundsTrace case workspace', () => {
  it('presents host navigation pending without recreating the same-case session, requests or query draft', async () => {
    const h = embeddedHost(), api = fakeApi();
    const stats = vi.spyOn(api, 'stats'), flows = vi.spyOn(api, 'flows');
    const openGraph = vi.fn(), actionsForCase = vi.fn(() => ({ openGraph }));
    try {
      render(<App api={api} actionsForCase={actionsForCase} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      const input = screen.getByLabelText('账号筛选');
      const graph = screen.getByRole('button', { name: '进入画图' }) as HTMLButtonElement;
      const statsSignal = stats.mock.calls[0][1], flowSignal = flows.mock.calls[0][2];
      expect(graph.disabled).toBe(false); // A host that omits the new field remains usable.
      fireEvent.change(input, { target: { value: 'UNSUBMITTED-NAVIGATION-DRAFT' } });
      for (const navigationPending of [true, false, true, false]) {
        await act(async () => h.bind({ navigationPending }));
        expect(graph.disabled).toBe(navigationPending);
        expect(screen.getByLabelText('账号筛选')).toBe(input);
        expect((input as HTMLInputElement).value).toBe('UNSUBMITTED-NAVIGATION-DRAFT');
        expect(stats).toHaveBeenCalledOnce(); expect(flows).toHaveBeenCalledOnce();
        expect(actionsForCase).toHaveBeenCalledOnce();
        expect(statsSignal?.aborted).toBe(false); expect(flowSignal?.aborted).toBe(false);
        expect(screen.queryByRole('heading', { name: '案件会话已撤销' })).toBeNull();
        if (navigationPending) {
          fireEvent.click(graph); expect(openGraph).not.toHaveBeenCalled();
          expect((screen.getByRole('button', { name: '导入流水' }) as HTMLButtonElement).disabled).toBe(false);
        }
      }
      fireEvent.click(graph); await waitFor(() => expect(openGraph).toHaveBeenCalledOnce());
    } finally { h.close(); }
  });

  it('accepts a pending target context as case-selection confirmation without an old-bound result', async () => {
    const h = embeddedHost(), api = fakeApi();
    try {
      render(<App api={api} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      fireEvent.click(screen.getByRole('button', { name: '合成案件乙' }));
      await waitFor(() => expect(h.requests().at(-1)?.action).toBe('select-case'));
      const selection = h.requests().at(-1)!;
      h.bind({ navigationPending: true });
      h.bind({ caseId: '2', name: '合成案件乙', generation: 2, navigationPending: true });
      await screen.findByRole('button', { name: '核对流水 SYN-2' });
      await waitFor(() => expect((screen.getByRole('button', { name: '合成案件甲' }) as HTMLButtonElement).disabled).toBe(false));
      expect((screen.getByRole('button', { name: '进入画图' }) as HTMLButtonElement).disabled).toBe(true);
      h.send({ type: 'result', requestId: selection.requestId, ok: false, error: '旧绑定结果不能恢复旧案件' });
      expect(screen.queryByText('旧绑定结果不能恢复旧案件')).toBeNull();
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('合成案件乙');
      h.bind({ caseId: '2', name: '合成案件乙', generation: 2, navigationPending: false });
      expect((screen.getByRole('button', { name: '进入画图' }) as HTMLButtonElement).disabled).toBe(false);
      expect(h.requests().filter(request => request.action === 'select-case')).toHaveLength(1);
    } finally { h.close(); }
  });

  it('keeps a current operation error visible when the idle context precedes its result', async () => {
    const h = embeddedHost();
    try {
      render(<App api={fakeApi()} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      fireEvent.click(screen.getByRole('button', { name: '合成案件乙' }));
      await waitFor(() => expect(h.requests().at(-1)?.action).toBe('select-case'));
      const request = h.requests().at(-1)!;
      h.bind({ navigationPending: true }); h.bind({ navigationPending: false });
      h.send({ type: 'result', requestId: request.requestId, ok: false, error: '合成导航操作失败' });
      await screen.findByText('合成导航操作失败');
      await act(async () => { await Promise.resolve(); });
      expect(screen.getByRole('alert').textContent).toContain('合成导航操作失败');
      expect(h.requests().filter(message => message.action === 'select-case')).toHaveLength(1);
    } finally { h.close(); }
  });

  it('filters a case candidate without switching the current case until its existing open button is clicked', async () => {
    const api = fakeApi(); api.stats = vi.fn(api.stats);
    render(<App api={api} />); await screen.findByRole('button', { name: '核对流水 SYN-1' });
    const input = screen.getByLabelText('筛选案件');
    vi.spyOn(input, 'getBoundingClientRect').mockReturnValue({ x: 20, y: 100, left: 20, top: 100, right: 320, bottom: 132, width: 300, height: 32, toJSON: () => ({}) });
    // jsdom cannot render the browser's native popover top layer.
    input.parentElement?.querySelectorAll<HTMLElement>('[popover]').forEach(layer => Object.defineProperties(layer, {
      showPopover: { value: () => { layer.style.display = 'block'; } }, hidePopover: { value: () => { layer.style.display = 'none'; } },
    }));
    input.focus(); fireEvent.input(input, { target: { value: '合成案件' } });
    const option = await screen.findByRole('option', { name: /合成案件乙.*2/ });
    expect(screen.getByRole('button', { name: '合成案件甲' })).toBeTruthy();
    fireEvent.click(option);
    expect(screen.queryByRole('button', { name: '合成案件甲' })).toBeNull();
    expect(screen.getByRole('button', { name: '核对流水 SYN-1' })).toBeTruthy();
    expect(api.stats).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '合成案件乙' }));
    await screen.findByRole('button', { name: '核对流水 SYN-2' });
    expect(api.stats).toHaveBeenLastCalledWith('2', expect.any(AbortSignal));
  });

  it('executes a free case keyword with its SVG button while IME draft input never filters or opens a case', async () => {
    const api = fakeApi(); api.stats = vi.fn(api.stats);
    render(<App api={api} />); await screen.findByRole('button', { name: '核对流水 SYN-1' });
    const input = screen.getByLabelText('筛选案件');
    fireEvent.compositionStart(input); fireEvent.input(input, { target: { value: '乙' } }); fireEvent.submit(input.closest('form')!);
    expect(screen.getByRole('button', { name: '合成案件甲' })).toBeTruthy(); expect(api.stats).toHaveBeenCalledOnce();
    fireEvent.compositionEnd(input); fireEvent.click(screen.getByRole('button', { name: '查询案件' }));
    expect(screen.queryByRole('button', { name: '合成案件甲' })).toBeNull(); expect(screen.getByRole('button', { name: '合成案件乙' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '核对流水 SYN-1' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
    expect(screen.getByRole('textbox', { name: '案件名称' })).toBeTruthy();
  });

  it('closes the case actions menu on Escape, outside click, focus departure and case change', async () => {
    const { container } = render(<App api={fakeApi()} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    const menu = container.querySelector<HTMLDetailsElement>('.case-actions')!, summary = menu.querySelector('summary')!;
    fireEvent.click(summary); expect(menu.open).toBe(true);
    fireEvent.keyDown(within(menu).getByRole('button', { name: '案件信息' }), { key: 'Escape' });
    expect(menu.open).toBe(false); expect(document.activeElement).toBe(summary);
    fireEvent.click(summary); fireEvent.pointerDown(screen.getByLabelText('筛选案件')); expect(menu.open).toBe(false);
    fireEvent.click(summary); fireEvent.blur(menu, { relatedTarget: screen.getByLabelText('筛选案件') }); expect(menu.open).toBe(false);
    fireEvent.click(summary); fireEvent.click(screen.getByRole('button', { name: '合成案件乙' }));
    await screen.findByRole('button', { name: '核对流水 SYN-2' }); expect(container.querySelector<HTMLDetailsElement>('.case-actions')!.open).toBe(false);
  });

  it('closes the case menu before opening case information and returns focus to its visible summary', async () => {
    const { container } = render(<App api={fakeApi()} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    const menu = container.querySelector<HTMLDetailsElement>('.case-actions')!, summary = menu.querySelector('summary')!;
    fireEvent.click(summary); fireEvent.click(within(menu).getByRole('button', { name: '案件信息' }));
    expect(menu.open).toBe(false);
    fireEvent.click(within(screen.getByRole('dialog', { name: '案件信息' })).getByRole('button', { name: '关闭' }));
    expect(document.activeElement).toBe(summary);
  });

  it('closes the case menu after dispatch and prevents reopening during the host operation', async () => {
    const h = embeddedHost();
    try {
      const { container } = render(<App api={fakeApi()} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      const menu = container.querySelector<HTMLDetailsElement>('.case-actions')!, summary = menu.querySelector('summary')!;
      fireEvent.click(summary); fireEvent.click(within(menu).getByRole('button', { name: '导出案件包' }));
      await waitFor(() => expect(h.requests().at(-1)?.action).toBe('case-action'));
      expect(menu.open).toBe(false); fireEvent.click(summary); expect(menu.open).toBe(false);
      expect(summary.getAttribute('aria-disabled')).toBe('true');
      await h.answer('case-action');
      await waitFor(() => expect(summary.getAttribute('aria-disabled')).toBe('false'));
      fireEvent.click(summary); expect(menu.open).toBe(true);
      await act(async () => h.bind({ generation: 2 }));
      expect(container.querySelector<HTMLDetailsElement>('.case-actions')!.open).toBe(false);
    } finally { h.close(); }
  });

  it('returns keyboard focus to the case menu summary when the host rejects an action', async () => {
    const h = embeddedHost();
    try {
      const { container } = render(<App api={fakeApi()} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      const menu = container.querySelector<HTMLDetailsElement>('.case-actions')!, summary = menu.querySelector('summary')!;
      fireEvent.click(summary); const action = within(menu).getByRole('button', { name: '导出案件包' }); action.focus(); fireEvent.click(action);
      await h.answer('case-action', false, '合成导出被拒绝'); await screen.findByText('合成导出被拒绝');
      expect(menu.open).toBe(false); expect(document.activeElement).toBe(summary);
    } finally { h.close(); }
  });

  it.each(['新建案件', '案件信息'])('unmounts the %s modal while the cases surface is hidden and preserves its draft on return', async title => {
    const h = embeddedHost();
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ case_id: '1', source: 'flows', total: 0, page: 1, page_size: 30, items: [] }));
    try {
      const { container } = render(<App api={fakeApi()} embedded />); h.bind({ surface: 'cases' });
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: '保留查询草稿' } });
      if (title === '新建案件') {
        fireEvent.click(screen.getByRole('button', { name: title }));
        fireEvent.change(screen.getByLabelText('案件名称'), { target: { value: '保留新建草稿' } });
      } else {
        fireEvent.click(container.querySelector('.case-actions summary')!); fireEvent.click(screen.getByRole('button', { name: title }));
      }
      for (const hidden of [{ surface: 'accounts' }, { active: false }]) {
        const dialog = screen.getByRole('dialog', { name: title });
        await act(async () => h.bind(hidden));
        expect(container.querySelector('dialog[open]')).toBeNull(); expect(dialog.isConnected).toBe(false);
        await act(async () => h.bind({ surface: 'cases', active: true }));
        await screen.findByRole('dialog', { name: title });
        expect((screen.getByLabelText('账号筛选') as HTMLInputElement).value).toBe('保留查询草稿');
        if (title === '新建案件') expect((screen.getByLabelText('案件名称') as HTMLInputElement).value).toBe('保留新建草稿');
      }
    } finally { fetch.mockRestore(); h.close(); }
  });

  it('releases a hidden busy create modal without aborting or resending the in-flight create', async () => {
    const h = embeddedHost(), api = fakeApi(), pending = deferred<Case>(); let createSignal: AbortSignal | undefined;
    api.createCase = vi.fn((_name, signal) => { createSignal = signal; return pending.promise; });
    try {
      const { container } = render(<App api={api} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
      fireEvent.change(screen.getByLabelText('案件名称'), { target: { value: '合成延迟创建' } });
      fireEvent.click(screen.getByRole('button', { name: '创建案件' }));
      h.bind({ active: false }); expect(container.querySelector('dialog[open]')).toBeNull(); expect(createSignal?.aborted).toBe(false);
      h.bind({ active: true });
      expect((screen.getByRole('button', { name: '正在创建…' }) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByLabelText('案件名称') as HTMLInputElement).value).toBe('合成延迟创建');
      await act(async () => pending.resolve({ id: '3', name: '合成延迟创建', created_at: null }));
      await h.answer('select-case', false, '合成打开尚未确认'); await screen.findByText('合成打开尚未确认');
      expect(api.createCase).toHaveBeenCalledOnce(); expect(createSignal?.aborted).toBe(false);
    } finally { h.close(); }
  });

  it('keeps the sent import alive while hidden and retains its completed receipt without another POST', async () => {
    const h = embeddedHost(), api = fakeApi(), pending = deferred<{ case_id: string; import_id: string; started: boolean }>();
    let commitSignal: AbortSignal | undefined;
    api.commit = vi.fn((_caseId, _importId, _body, signal) => { commitSignal = signal; return pending.promise; }); const job = vi.spyOn(api, 'job');
    try {
      const { container } = render(<App api={api} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await h.answer('import-acquire'); await waitFor(() => expect(api.commit).toHaveBeenCalledOnce());
      h.bind({ active: false }); expect(container.querySelector('dialog[open]')).toBeNull(); expect(commitSignal?.aborted).toBe(false);
      await act(async () => pending.resolve({ case_id: '1', import_id: preview.import_id, started: true }));
      await h.answer('import-release'); await h.answer('data-changed');
      expect(container.querySelector('dialog[open]')).toBeNull();
      await act(async () => h.bind({ active: true })); await screen.findByText('流水已写入当前案件');
      expect(screen.queryByRole('region', { name: '待核对导入' })).toBeNull();
      expect(api.commit).toHaveBeenCalledOnce(); expect(job).toHaveBeenCalledOnce(); expect(commitSignal?.aborted).toBe(false);
      expect(h.requests().filter(request => request.action === 'import-acquire')).toHaveLength(1);
      expect(screen.getByText(`批次 ${completed.result!.batch_id} · ${preview.filename}`)).toBeTruthy();
    } finally { h.close(); }
  });

  it('refreshes after hidden host reactivation and current data changes without losing filter drafts', async () => {
    const h = embeddedHost(), api = fakeApi(); const flows = vi.spyOn(api, 'flows');
    try {
      render(<App api={api} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: 'UNSUBMITTED' } });
      const before = flows.mock.calls.length;
      h.bind({ active: false }); h.send({ type: 'data-changed', caseId: '1', generation: 1 });
      expect(flows).toHaveBeenCalledTimes(before);
      h.bind({ active: true }); await waitFor(() => expect(flows).toHaveBeenCalledTimes(before + 1));
      expect((screen.getByLabelText('账号筛选') as HTMLInputElement).value).toBe('UNSUBMITTED');
      h.send({ type: 'data-changed', caseId: '1', generation: 0 }); expect(flows).toHaveBeenCalledTimes(before + 1);
      h.send({ type: 'data-changed', caseId: '1', generation: 1 }); await waitFor(() => expect(flows).toHaveBeenCalledTimes(before + 2));
    } finally { h.close(); }
  });

  it('requires parent acquisition before POST and publishes completion only after terminal release', async () => {
    const h = embeddedHost(), api = fakeApi(); const commit = vi.spyOn(api, 'commit');
    try {
      render(<App api={api} embedded />); h.bind(); await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await waitFor(() => expect(h.requests().at(-1)?.action).toBe('import-acquire'));
      expect(commit).not.toHaveBeenCalled(); await h.answer('import-acquire');
      await waitFor(() => expect(commit).toHaveBeenCalledOnce());
      const release = await h.answer('import-release'); expect(release.payload).toEqual({ importId: preview.import_id, jobId: preview.import_id });
      await h.answer('data-changed'); await screen.findByText('流水已写入当前案件');
      expect(commit).toHaveBeenCalledOnce();
    } finally { h.close(); }
  });

  it.each([undefined, 500])('keeps an unknown submission locked and explicitly reconciles without another POST (status %s)', async status => {
    const h = embeddedHost(), api = fakeApi(); api.commit = vi.fn(async () => { throw new ApiError('synthetic_failure', '合成提交未确认', status); });
    try {
      render(<App api={api} embedded />); h.bind(); await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await h.answer('import-acquire'); await screen.findByText('合成提交未确认');
      expect(h.requests().filter(request => request.action === 'import-release')).toHaveLength(0);
      fireEvent.click(screen.getByRole('button', { name: '关闭进度' }));
      const notice = screen.getByRole('region', { name: '待核对导入' });
      fireEvent.click(within(notice).getByRole('button', { name: /核对导入结果/ }));
      await h.answer('import-release', false, '合成任务仍在运行'); await screen.findByText('合成任务仍在运行');
      expect(api.commit).toHaveBeenCalledOnce();
      fireEvent.click(within(screen.getByRole('region', { name: '待核对导入' })).getByRole('button', { name: /核对导入结果/ }));
      await h.answer('import-release'); await h.answer('data-changed');
      await waitFor(() => expect(screen.queryByRole('region', { name: '待核对导入' })).toBeNull());
      expect(api.commit).toHaveBeenCalledOnce();
    } finally { h.close(); }
  });

  it('releases a known 4xx only through the parent rejection verification', async () => {
    const h = embeddedHost(), api = fakeApi(); api.commit = vi.fn(async () => { throw new ApiError('invalid_request', '合成请求被拒绝', 422); });
    try {
      render(<App api={api} embedded />); h.bind(); await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await h.answer('import-acquire');
      const release = await h.answer('import-release'); expect(release.payload).toEqual({ importId: preview.import_id, rejected: true });
      await screen.findByText('合成请求被拒绝'); fireEvent.click(screen.getByRole('button', { name: '关闭进度' }));
      expect(screen.queryByRole('region', { name: '待核对导入' })).toBeNull(); expect(api.commit).toHaveBeenCalledOnce();
    } finally { h.close(); }
  });

  it('never dispatches a POST from an acquisition acknowledgement after A–B–A rebinding', async () => {
    const h = embeddedHost(), api = fakeApi(); const commit = vi.spyOn(api, 'commit');
    try {
      render(<App api={api} embedded />); h.bind(); await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await waitFor(() => expect(h.requests().at(-1)?.action).toBe('import-acquire'));
      const request = h.requests().at(-1)!;
      h.bind({ caseId: '2', name: '合成案件乙', generation: 2 }); h.bind({ generation: 3 });
      h.send({ type: 'result', requestId: request.requestId, ok: true });
      await screen.findByRole('button', { name: '核对流水 SYN-1' }); expect(commit).not.toHaveBeenCalled();
    } finally { h.close(); }
  });

  it('restores each case query draft and page while loading fresh authoritative rows', async () => {
    const api = fakeApi();
    api.flows = vi.fn(async (id, filters) => ({ ...page(id), total: 120, page: filters.page }));
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: '000SYN-A' } });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(api.flows).toHaveBeenLastCalledWith('1', { account: '000SYN-A', kw: '', page: 1 }, expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    await waitFor(() => expect(api.flows).toHaveBeenLastCalledWith('1', { account: '000SYN-A', kw: '', page: 2 }, expect.any(AbortSignal)));
    fireEvent.change(screen.getByLabelText('摘要或流水号'), { target: { value: '尚未提交的关键词' } });
    fireEvent.click(screen.getByRole('button', { name: '合成案件乙' }));
    await screen.findByRole('button', { name: '核对流水 SYN-2' });
    expect((screen.getByLabelText('账号筛选') as HTMLInputElement).value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: '合成案件甲' }));
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    expect((screen.getByLabelText('账号筛选') as HTMLInputElement).value).toBe('000SYN-A');
    expect((screen.getByLabelText('摘要或流水号') as HTMLInputElement).value).toBe('尚未提交的关键词');
    expect(api.flows).toHaveBeenLastCalledWith('1', { account: '000SYN-A', kw: '', page: 2 }, expect.any(AbortSignal));
  });

  it('keeps the current case and its work when creating a new case fails', async () => {
    const api = fakeApi(); api.createCase = async () => { throw new ApiError('invalid_request', '合成创建失败'); };
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.change(screen.getByLabelText('账号筛选'), { target: { value: '未提交的账号' } });
    fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
    fireEvent.change(screen.getByRole('textbox', { name: '案件名称' }), { target: { value: '失败案件' } });
    fireEvent.click(screen.getByRole('button', { name: '创建案件' }));
    await screen.findByText('合成创建失败');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('合成案件甲');
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect((screen.getByLabelText('账号筛选') as HTMLInputElement).value).toBe('未提交的账号');
  });

  it('checks the case list after an uncertain create result and opens only after explicit selection', async () => {
    const api = fakeApi(); let written = false;
    api.cases = vi.fn(async () => ({ items: written ? [...cases, { id: '3', name: '响应丢失案件', created_at: null }] : cases }));
    api.createCase = vi.fn(async () => { written = true; throw new ApiError('network_error', '合成连接中断'); });
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
    fireEvent.change(screen.getByLabelText('案件名称'), { target: { value: '响应丢失案件' } });
    fireEvent.click(screen.getByRole('button', { name: '创建案件' }));
    await screen.findByText(/无法确认案件是否已创建/);
    const dialog = screen.getByRole('dialog', { name: '新建案件' });
    fireEvent.submit(dialog.querySelector('form')!);
    expect(api.createCase).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '核对案件列表' }));
    const open = await screen.findByRole('button', { name: '打开 响应丢失案件 · ID 3' });
    expect(api.createCase).toHaveBeenCalledOnce();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('合成案件甲');
    fireEvent.click(open);
    await screen.findByRole('button', { name: '核对流水 SYN-3' });
    expect(api.createCase).toHaveBeenCalledOnce();
  });

  it('preserves uncertain-create protection when the dialog is reopened and requires a read check before returning to input', async () => {
    const api = fakeApi(); api.createCase = vi.fn(async () => { throw new ApiError('network_error', '合成连接中断'); });
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
    fireEvent.change(screen.getByLabelText('案件名称'), { target: { value: '待核对案件' } });
    fireEvent.click(screen.getByRole('button', { name: '创建案件' }));
    await screen.findByText(/无法确认案件是否已创建/);
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
    expect(screen.queryByRole('button', { name: '创建案件' })).toBeNull();
    expect(screen.queryByRole('button', { name: '已核对，重新填写' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '核对案件列表' }));
    fireEvent.click(await screen.findByRole('button', { name: '已核对，重新填写' }));
    expect((screen.getByLabelText('案件名称') as HTMLInputElement).value).toBe('');
    expect((screen.getByRole('button', { name: '创建案件' }) as HTMLButtonElement).disabled).toBe(true);
    expect(api.createCase).toHaveBeenCalledOnce();
  });

  it('uses parent-controlled case selection and retains the old case until a new-case context arrives', async () => {
    const messages = vi.spyOn(window, 'postMessage').mockImplementation(() => {});
    const api = fakeApi(); const flows = vi.spyOn(api, 'flows'); const create = vi.spyOn(api, 'createCase');
    const send = (data: Record<string, unknown>) => act(() => window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, source: window, data: { channel: WORKSPACE_CHANNEL, version: 1, ...data } })));
    try {
      render(<App api={api} embedded />);
      await screen.findByRole('button', { name: '合成案件甲' });
      expect(flows).not.toHaveBeenCalled();
      send({ type: 'context', caseId: '1', name: '合成案件甲', generation: 1, retainedCaseIds: ['1'] });
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
      fireEvent.change(screen.getByRole('textbox', { name: '案件名称' }), { target: { value: '合成案件丙' } });
      fireEvent.click(screen.getByRole('button', { name: '创建案件' }));
      await waitFor(() => expect(messages.mock.calls.at(-1)?.[0]).toMatchObject({ type: 'request', caseId: '1', generation: 1, action: 'select-case', payload: { caseId: '3' } }));
      let request = messages.mock.calls.at(-1)![0];
      send({ type: 'result', requestId: request.requestId, ok: false, error: '合成保留失败' });
      await screen.findByText('合成保留失败');
      fireEvent.click(screen.getByRole('button', { name: '打开已创建案件' }));
      await waitFor(() => expect(messages.mock.calls.at(-1)?.[0].requestId).not.toBe(request.requestId));
      request = messages.mock.calls.at(-1)![0];
      expect(create).toHaveBeenCalledOnce();
      send({ type: 'result', requestId: request.requestId, ok: true });
      expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('合成案件甲');
      expect(screen.getByRole('dialog', { name: '新建案件' })).toBeTruthy();
      send({ type: 'context', caseId: '3', name: '合成案件丙', generation: 2, retainedCaseIds: ['1'] });
      await screen.findByRole('button', { name: '核对流水 SYN-3' });
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(screen.getByRole('button', { name: '合成案件甲' }).textContent).toContain('工作已保留');
    } finally { messages.mockRestore(); }
  });

  it('displays exact money and bigint identity in the verification dialog', async () => {
    render(<App api={fakeApi()} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(screen.getByRole('button', { name: '核对流水 SYN-1' }));
    const dialog = screen.getByRole('dialog', { name: '流水核对' });
    expect(within(dialog).getByText('9007199254740993')).toBeTruthy();
    expect(within(dialog).getByText('9007199254740994')).toBeTruthy();
    expect(within(dialog).getByText('9,999,999,999,999,999.99 CNY')).toBeTruthy();
    expect(within(dialog).getByText('未提供')).toBeTruthy();
  });

  it('discards a previous case response even when the transport ignores cancellation', async () => {
    const oldStats = deferred<CaseStats>(); const oldPage = deferred<FlowPage>();
    const api = fakeApi();
    api.stats = id => id === '1' ? oldStats.promise : Promise.resolve(stats(id));
    api.flows = id => id === '1' ? oldPage.promise : Promise.resolve(page(id));
    render(<App api={api} />);
    await screen.findByRole('button', { name: /合成案件乙/ });
    fireEvent.click(screen.getByRole('button', { name: /合成案件乙/ }));
    await screen.findByRole('button', { name: '核对流水 SYN-2' });
    await act(async () => { oldStats.resolve(stats('1')); oldPage.resolve(page('1')); });
    expect(screen.queryByRole('button', { name: '核对流水 SYN-1' })).toBeNull();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('合成案件乙');
    expect(new URLSearchParams(window.location.search).get('case_id')).toBe('2');
  });

  it('rejects a mismatched case result rather than displaying its rows', async () => {
    const api = fakeApi(); api.flows = async () => page('2');
    render(<App api={api} />);
    expect((await screen.findByRole('alert')).textContent).toContain('返回的案件与当前案件不一致');
    expect(screen.queryByRole('button', { name: '核对流水 SYN-2' })).toBeNull();
  });

  it('rejects a foreign-case row even when its page envelope has the current case ID', async () => {
    const api = fakeApi(); api.flows = async () => page('1', [row('2')]);
    render(<App api={api} />);
    expect((await screen.findByRole('alert')).textContent).toContain('返回的案件与当前案件不一致');
    expect(screen.queryByRole('button', { name: '核对流水 SYN-2' })).toBeNull();
  });

  it('creates a case through an HTML dialog without native prompt', async () => {
    const api = fakeApi(); const create = vi.spyOn(api, 'createCase');
    render(<App api={api} />);
    await screen.findByRole('button', { name: /合成案件甲/ });
    fireEvent.click(screen.getByRole('button', { name: '新建案件' }));
    fireEvent.change(screen.getByRole('textbox', { name: '案件名称' }), { target: { value: '合成案件丙' } });
    fireEvent.click(screen.getByRole('button', { name: '创建案件' }));
    await waitFor(() => expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('合成案件丙'));
    expect(create).toHaveBeenCalledWith('合成案件丙', expect.any(AbortSignal));
    expect(new URLSearchParams(window.location.search).get('case_id')).toBe('3');
  });

  it('creates a fresh scope when the standalone host actions are replaced for the same case', async () => {
    const api = fakeApi(); const firstGraph = vi.fn(); const nextGraph = vi.fn();
    const firstActions = () => ({ openGraph: firstGraph }); const nextActions = () => ({ openGraph: nextGraph });
    const { rerender } = render(<App api={api} actionsForCase={firstActions} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入流水' }));
    expect(screen.getByRole('dialog', { name: '导入流水' })).toBeTruthy();
    rerender(<App api={api} actionsForCase={nextActions} />);
    expect(screen.queryByRole('dialog')).toBeNull();
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(screen.getByRole('button', { name: /进入画图/ }));
    await waitFor(() => expect(nextGraph).toHaveBeenCalledOnce());
    expect(firstGraph).not.toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: '案件会话已撤销' })).toBeNull();
  });

  it('requires a fresh preview after mapping changes and commits only the bound case', async () => {
    const api = fakeApi(); const commit = vi.spyOn(api, 'commit'); const repreview = vi.spyOn(api, 'repreview');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } });
    await screen.findByText('synthetic.csv');
    const select = screen.getByLabelText('映射付款账号');
    fireEvent.change(select, { target: { value: '收款账号' } });
    expect((screen.getByRole('button', { name: '确认导入' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect((screen.getByRole('button', { name: '确认导入' }) as HTMLButtonElement).disabled).toBe(false));
    expect(repreview).toHaveBeenCalledWith('1', preview.import_id, expect.objectContaining({ mapping: expect.objectContaining({ payer_account: '收款账号' }) }), expect.any(AbortSignal));
    fireEvent.click(screen.getByRole('button', { name: '确认导入' }));
    await screen.findByText('流水已写入当前案件');
    expect(commit).toHaveBeenCalledWith('1', preview.import_id, expect.objectContaining({ mark_accounts: [] }), expect.any(AbortSignal));
  });

  it.each(['A', 'B'] as const)('removes incompatible mapping keys when switching from mode %s', async initialMode => {
    const api = fakeApi();
    const mapping = initialMode === 'A' ? preview.mapping : { trade_time: '交易时间', amount: '交易金额', self_account: '付款账号', counter_account: '收款账号', direction: '交易金额' };
    api.preview = async () => ({ ...preview, mode: initialMode, mapping });
    const repreview = vi.spyOn(api, 'repreview');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } });
    await screen.findByText('synthetic.csv');
    const next = initialMode === 'A' ? 'B' : 'A';
    fireEvent.change(screen.getByLabelText('流水文件类型'), { target: { value: next } });
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect(repreview).toHaveBeenCalledOnce());
    const submitted = repreview.mock.calls[0][2];
    expect(submitted.mode).toBe(next);
    for (const key of next === 'A' ? ['counter_account', 'direction'] : ['payer_account', 'payee_account']) expect(submitted.mapping).not.toHaveProperty(key);
    expect(submitted.mapping).toHaveProperty('trade_time', '交易时间');
  });

  it('allows clearing an automatically detected supplementary time column', async () => {
    const api = fakeApi();
    api.preview = async () => ({ ...preview, columns: [...preview.columns, '时分'], mapping: { ...preview.mapping, _time2: '时分' } });
    const repreview = vi.spyOn(api, 'repreview');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } });
    await screen.findByText('synthetic.csv');
    fireEvent.change(screen.getByLabelText('映射时间补充'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect(repreview).toHaveBeenCalledWith('1', preview.import_id, expect.objectContaining({ mapping: expect.objectContaining({ _time2: '' }) }), expect.any(AbortSignal)));
  });

  it('resumes only after preview and preserves frozen empty marks and declared accounts', async () => {
    const api = fakeApi(); api.imports = async id => ({ case_id: id, items: [session()] });
    api.repreview = async () => ({ ...preview, self_accounts_in_file: [{ account: 'SYN-MAIN', name: 'Synthetic main', count: 1 }] });
    const commit = vi.spyOn(api, 'commit'); const repreview = vi.spyOn(api, 'repreview');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入记录' }));
    fireEvent.click(await screen.findByRole('button', { name: '继续导入 synthetic.csv' }));
    const checkbox = await screen.findByRole('checkbox', { name: /Synthetic main/ }) as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(commit).not.toHaveBeenCalled();
    expect(repreview).toHaveBeenCalledWith('1', preview.import_id, { mode: preview.mode, mapping: preview.mapping }, expect.any(AbortSignal));
    fireEvent.change(screen.getByLabelText('映射付款账号'), { target: { value: '收款账号' } });
    fireEvent.click(screen.getByRole('button', { name: '按当前映射重新预览' }));
    await waitFor(() => expect(repreview).toHaveBeenCalledTimes(2));
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '确认导入' }));
    await screen.findByText('流水已写入当前案件');
    expect(commit).toHaveBeenCalledWith('1', preview.import_id, expect.objectContaining({ mark_accounts: [], self_accounts: ['SYN-DECLARED'] }), expect.any(AbortSignal));
  });

  it('discards late import history after switching cases', async () => {
    const api = fakeApi(); const oldHistory = deferred<ImportSessionList>();
    api.imports = id => id === '1' ? oldHistory.promise : Promise.resolve({ case_id: id, items: [session({ case_id: id, filename: 'case-two.csv' })] });
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入记录' }));
    fireEvent.click(screen.getByRole('button', { name: /合成案件乙/ }));
    await screen.findByRole('button', { name: '核对流水 SYN-2' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入记录' }));
    await screen.findByText('case-two.csv');
    await act(async () => { oldHistory.resolve({ case_id: '1', items: [session({ filename: 'late-case-one.csv' })] }); });
    expect(screen.queryByText('late-case-one.csv')).toBeNull();
    expect(screen.getByText('case-two.csv')).toBeTruthy();
  });

  it('offers an explicit retry when restoring a preview fails without automatically importing', async () => {
    const api = fakeApi(); api.imports = async id => ({ case_id: id, items: [session()] });
    let attempts = 0;
    api.repreview = async () => { if (++attempts === 1) throw new Error('合成预览错误'); return preview; };
    const commit = vi.spyOn(api, 'commit');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入记录' }));
    fireEvent.click(await screen.findByRole('button', { name: '继续导入 synthetic.csv' }));
    await screen.findByText('合成预览错误');
    fireEvent.click(screen.getByRole('button', { name: '重试恢复预览' }));
    await screen.findByRole('button', { name: '确认导入' });
    expect(attempts).toBe(2);
    expect(commit).not.toHaveBeenCalled();
  });

  it('downloads an automatic import report through the current case browser action', async () => {
    const api = fakeApi();
    const table = { profile: 'funds-trace-table-v5', record_total: 1, accepted: 0, unresolved: 1,
      non_monetary: 0, non_transaction: 0, auxiliary: 0, reason_counts: { missing_amount: 1 },
      currency_totals: {}, members: [], unresolved_examples: [], documents: [], mapping_evidence: [] };
    api.preview = vi.fn(async () => ({ ...preview, automatic: true, table_summary: table, stats: { total: 1, ok: 0, skipped: 1 }, preview: [] }));
    api.job = vi.fn(async () => ({ ...completed, result: { ...completed.result!, ok: 0, skipped: 1,
      source_id: 'SYN-REPORT-SOURCE', table_summary: table } }));
    const commit = vi.spyOn(api, 'commit'), report = vi.spyOn(api, 'downloadImportReport');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(screen.getByRole('button', { name: '导入流水' }));
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } });
    await screen.findByRole('heading', { name: '自动处理完成' });
    expect(commit).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '下载完整处理报告（JSON）' }));
    await waitFor(() => expect(report).toHaveBeenCalledWith('1', 'SYN-REPORT-SOURCE',
      'feedback-SYN-REPORT-SOURCE-dispositions.json', expect.any(AbortSignal)));
  });

  it('pages source records without pretending package metadata has a downloadable file', async () => {
    const api = fakeApi();
    api.sources = async (id, flowId, page = 1) => ({ case_id: id, flow_id: flowId, total: 52, page, page_size: 50, items: page === 1 ? [source()] : [source({ filename: 'package.xlsx', sheet: 'Synthetic sheet', row_kind: 'worksheet_row', origin: 'package', source_available: true, disposition: 'duplicate' }), source({ source_id: 'abcdef0123456789abcdef0123456789', filename: 'unavailable.csv', source_available: false })] });
    const sources = vi.spyOn(api, 'sources'); const downloadSource = vi.spyOn(api, 'downloadSource');
    render(<App api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: '核对流水 SYN-1' }));
    const download = await screen.findByRole('button', { name: /下载原始文件/ });
    fireEvent.click(download);
    await waitFor(() => expect(downloadSource).toHaveBeenCalledWith('1', '0123456789abcdef0123456789abcdef', 'source.csv', expect.any(AbortSignal)));
    expect(screen.getByText('CSV 第 6 条逻辑记录（含表头和空记录）')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '来源下一页' }));
    await screen.findByText('package.xlsx');
    expect(screen.getByText('工作表「Synthetic sheet」 · 第 6 行')).toBeTruthy();
    expect(screen.getByText('重复记录关联')).toBeTruthy();
    expect(screen.getByText('案件包携带的来源元数据；包内不含原始上传文件。')).toBeTruthy();
    expect(screen.getByText('原始上传文件当前不可用，保留来源元数据。')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /下载原始文件/ })).toBeNull();
    expect(sources).toHaveBeenLastCalledWith('1', '9007199254740993', 2, expect.any(AbortSignal));
  });

  it('discards a late source response when a different case opens its ledger', async () => {
    const api = fakeApi(); const oldSources = deferred<FlowSourceList>();
    api.sources = (id, flowId) => id === '1' ? oldSources.promise : Promise.resolve({ case_id: id, flow_id: flowId, items: [], total: 0, page: 1, page_size: 50 });
    render(<App api={api} />);
    fireEvent.click(await screen.findByRole('button', { name: '核对流水 SYN-1' }));
    fireEvent.click(screen.getByRole('button', { name: /合成案件乙/ }));
    fireEvent.click(await screen.findByRole('button', { name: '核对流水 SYN-2' }));
    await screen.findByText('未记录来源。历史流水无法据此还原原始文件或源行位置。');
    await act(async () => { oldSources.resolve({ case_id: '1', flow_id: row('1').id, items: [source({ filename: 'late-source.csv' })], total: 1, page: 1, page_size: 50 }); });
    expect(screen.queryByText('late-source.csv')).toBeNull();
    expect(screen.queryByRole('button', { name: /下载原始文件/ })).toBeNull();
  });

  it('previews cleanup and requires a separate explicit action to execute it for this case', async () => {
    const api = fakeApi(); api.cleanup = async (_id, dryRun = true) => ({ dry_run: dryRun, eligible: 1, deleted: dryRun ? 0 : 1, missing: 0, failed: 0, protected: 2, queued: 0 });
    const cleanup = vi.spyOn(api, 'cleanup');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入记录' }));
    fireEvent.click(screen.getByRole('button', { name: '预览清理' }));
    await screen.findByText('可清理 1 个 · 受保护 2 个 · 待处理 0 个');
    expect(cleanup).toHaveBeenCalledExactlyOnceWith('1', true, expect.any(AbortSignal));
    fireEvent.click(screen.getByRole('button', { name: '确认清理本案到期文件' }));
    await screen.findByText('已清理 1 个 · 已缺失 0 个 · 失败 0 个');
    expect(cleanup).toHaveBeenLastCalledWith('1', false, expect.any(AbortSignal));
  });

  it('routes legacy import records to the same-case G6 surface', async () => {
    const api = fakeApi(); api.imports = async id => ({ case_id: id, items: [session({ surface: 'legacy' })] });
    const repreview = vi.spyOn(api, 'repreview'); const commit = vi.spyOn(api, 'commit'); const navigate = vi.fn();
    render(<App api={api} actionsForCase={context => ({ openLegacyImport: importId => navigate(context.caseId, importId) })} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入记录' }));
    const button = await screen.findByRole('button', { name: /在 G6 工作区查看/ });
    fireEvent.click(button);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('1', preview.import_id));
    expect(screen.queryByRole('button', { name: '继续导入 synthetic.csv' })).toBeNull();
    expect(repreview).not.toHaveBeenCalled(); expect(commit).not.toHaveBeenCalled();
  });

  it('opens an active task as progress without automatically committing again', async () => {
    const api = fakeApi(); api.imports = async id => ({ case_id: id, items: [session({ stage: 'importing' })] });
    const pending = deferred<ImportJob>(); api.job = () => pending.promise;
    const commit = vi.spyOn(api, 'commit');
    render(<App api={api} />);
    await screen.findByRole('button', { name: '核对流水 SYN-1' });
    fireEvent.click(within(screen.getByRole('main')).getByRole('button', { name: '导入记录' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看进度' }));
    expect(screen.getByRole('button', { name: '关闭进度' })).toBeTruthy();
    expect(commit).not.toHaveBeenCalled();
    await act(async () => { pending.resolve(completed); });
    await screen.findByText('流水已写入当前案件');
    expect(commit).not.toHaveBeenCalled();
  });
});

describe('parent-owned import feedback', () => {
  it('opens a closed original progress task on a scoped host command without any new POST', async () => {
    const h = embeddedHost(), api = fakeApi();
    const commit = vi.spyOn(api, 'commit'), repreview = vi.spyOn(api, 'repreview');
    const imports = vi.spyOn(api, 'imports').mockResolvedValue({ case_id: '1', items: [session({ stage: 'importing', recoverable: false })] });
    const job = vi.spyOn(api, 'job').mockResolvedValue({ ...completed, stage: 'importing', done: 0, total: 0, result: null });
    try {
      const view = render(<App api={api} embedded />); h.bind();
      await screen.findByRole('button', { name: '核对流水 SYN-1' });
      h.send({ type: 'feedback-open-import', caseId: '1', generation: 0, importId: preview.import_id });
      expect(imports).not.toHaveBeenCalled();
      h.send({ type: 'feedback-open-import', caseId: '1', generation: 1, importId: preview.import_id });
      await screen.findByRole('dialog', { name: '导入流水' });
      await waitFor(() => expect(job).toHaveBeenCalledWith('1', preview.import_id, expect.any(AbortSignal)));
      fireEvent.click(screen.getByRole('button', { name: '关闭进度' }));
      expect(screen.queryByRole('dialog', { name: '导入流水' })).toBeNull();
      h.send({ type: 'feedback-open-import', caseId: '1', generation: 1, importId: preview.import_id });
      await screen.findByRole('dialog', { name: '导入流水' });
      expect(commit).not.toHaveBeenCalled(); expect(repreview).not.toHaveBeenCalled();
      expect(h.messages.mock.calls.some(([message]) => message.type === 'feedback' && message.event?.importId === preview.import_id)).toBe(true);
      expect(document.querySelector('.ff-dock')).toBeNull();
      view.unmount();
    } finally { h.close(); }
  });
});

describe('verified parent import settlement', () => {
  it('clears only the original local unknown banner after a trusted current parent settlement without posting again', async () => {
    const h = embeddedHost(), api = fakeApi();
    api.commit = vi.fn(async () => { throw new ApiError('synthetic_lost_receipt', '合成提交回执丢失'); });
    try {
      render(<App api={api} embedded />); h.bind(); await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await h.answer('import-acquire'); await screen.findByText('合成提交回执丢失');
      fireEvent.click(screen.getByRole('button', { name: '关闭进度' }));
      h.bind({ pendingImports: [] });
      expect(screen.getByRole('region', { name: '待核对导入' })).toBeTruthy();
      expect(screen.getByText(/导入提交结果尚未确认/)).toBeTruthy();
      for (const message of [
        { caseId: '2', generation: 1, importId: preview.import_id },
        { caseId: '1', generation: 0, importId: preview.import_id },
        { caseId: '1', generation: 1, importId: 'abcdefabcdef' },
      ]) {
        h.send({ type: 'feedback-import-settled', ...message });
        expect(screen.getByRole('region', { name: '待核对导入' })).toBeTruthy();
        expect(screen.getByText(/导入提交结果尚未确认/)).toBeTruthy();
      }
      const requests = h.requests().length;
      h.send({ type: 'feedback-import-settled', caseId: '1', generation: 1, importId: preview.import_id });
      await waitFor(() => expect(screen.queryByRole('region', { name: '待核对导入' })).toBeNull());
      expect(screen.queryByText(/导入提交结果尚未确认/)).toBeNull();
      expect(h.requests()).toHaveLength(requests); expect(api.commit).toHaveBeenCalledOnce();
    } finally { h.close(); }
  });
});

describe('late local import transport replies', () => {
  it('does not revive a local unknown banner when the parent already verified and settled the original import', async () => {
    const h = embeddedHost(), api = fakeApi();
    let reject!: (reason: Error) => void;
    api.commit = vi.fn(() => new Promise<Awaited<ReturnType<FundsApi['commit']>>>((_resolve, fail) => { reject = fail; }));
    try {
      render(<App api={api} embedded />); h.bind(); await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await h.answer('import-acquire'); await waitFor(() => expect(api.commit).toHaveBeenCalledOnce());
      h.send({ type: 'feedback-import-settled', caseId: '1', generation: 1, importId: preview.import_id });
      expect(screen.queryByRole('region', { name: '待核对导入' })).toBeNull();
      await act(async () => reject(new Error('合成本地回执迟到失败')));
      await screen.findByText('流水已写入当前案件');
      expect(screen.queryByText('合成本地回执迟到失败')).toBeNull();
      expect(screen.queryByRole('button', { name: '重新查询进度' })).toBeNull();
      expect(screen.queryByRole('region', { name: '待核对导入' })).toBeNull();
      expect(screen.queryByText(/导入提交结果尚未确认/)).toBeNull();
      expect(api.commit).toHaveBeenCalledOnce();
    } finally { h.close(); }
  });
});


describe('settled original import result observation', () => {
  it.each(['done', 'error'] as const)('reads the real %s receipt into an already-open unknown dialog without another POST or release request', async stage => {
    const h = embeddedHost(), api = fakeApi();
    api.commit = vi.fn(async () => { throw new Error('合成POST回执丢失'); });
    api.job = vi.fn(async (): Promise<ImportJob> => stage === 'done' ? completed : { ...completed, stage: 'error', error: '合成服务端确认失败', result: null });
    try {
      render(<App api={api} embedded />); h.bind(); await screen.findByRole('button', { name: '核对流水 SYN-1' });
      await h.upload(); await h.answer('import-acquire'); await screen.findByText('合成POST回执丢失');
      const requests = h.requests().length, feedbackBefore = h.messages.mock.calls.length;
      h.send({ type: 'feedback-import-settled', caseId: '1', generation: 1, importId: preview.import_id });
      if (stage === 'done') await screen.findByText('流水已写入当前案件');
      else await screen.findByText('合成服务端确认失败');
      expect(api.job).toHaveBeenCalledWith('1', preview.import_id, expect.any(AbortSignal));
      expect(api.commit).toHaveBeenCalledOnce(); expect(h.requests()).toHaveLength(requests);
      expect(h.messages.mock.calls.slice(feedbackBefore).filter(([message]) => message.type === 'feedback' && message.event?.kind === 'task').every(([message]) => ['success', 'error'].includes(message.event.status))).toBe(true);
      expect(screen.queryByText('合成POST回执丢失')).toBeNull();
      expect(screen.queryByRole('button', { name: '重新查询进度' })).toBeNull();
      expect(screen.queryByRole('region', { name: '待核对导入' })).toBeNull();
    } finally { h.close(); }
  });
});
