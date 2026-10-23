import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AccountWorkspace, type AccountWorkspaceProps } from './AccountWorkspace';
import type { AccountPage, AccountProfile, AccountRegistryApi, AccountRow, AccountWorkspaceActions } from './types';

const accountA = '0009007199254740993'; const accountB = '0009007199254740994';
const context = { caseId: '1', name: '合成资料核对案', generation: 1 };
const cases = [{ id: '1', name: context.name }, { id: '2', name: '合成另一案件' }];
type Mutable<T> = { -readonly [K in keyof T]: T[K] };
const row = (account = accountA, overrides: Partial<AccountRow> = {}): AccountRow => ({ account, name: '合成同名主体', id_no: null, identity_updated_at: null, photo_at: null, is_main: false, main_source: null, main_marked_at: null, has_flows: true, ...overrides });
const profile = (account = accountA, caseId = '1', overrides: Partial<AccountProfile> = {}): AccountProfile => ({ ...row(account), case_id: caseId, photo: null, cnt: 5, in_cnt: 2, out_cnt: 3, in_totals: [{ currency: 'CNY', amount: '9999999999999999.99' }, { currency: 'USD', amount: '0.01' }], out_totals: [{ currency: 'CNY', amount: '100.23' }], in_amt: null, out_amt: '100.23', tmin: '2026-01-01 10:00:00', tmax: '2026-01-20 12:30:00', top_counterparties: [], ...overrides });
function fixture() {
  const api: Mutable<AccountRegistryApi> = {
    list: vi.fn(async (caseId, query) => ({ case_id: caseId, source: query.source, total: 2, page: query.page, page_size: query.pageSize, items: [row(), row(accountB)] })),
    profile: vi.fn(async (caseId, account) => profile(account, caseId)),
    photoUrl: (caseId, account, updatedAt, original) => `/photo/${original ? 'orig' : 'img'}?${new URLSearchParams({ case_id: caseId, account, v: updatedAt })}`,
  };
  const actions: Mutable<AccountWorkspaceActions> = { mutate: vi.fn(async () => {}), reconcile: vi.fn(async () => {}), openMainAccounts: vi.fn(async () => {}), importIdentity: vi.fn(async () => {}), downloadTemplate: vi.fn(async () => {}), viewFlows: vi.fn(async () => {}) };
  const props: AccountWorkspaceProps = { context, cases, revision: 0, actions, api, onSelectCase: vi.fn(async () => {}) };
  return { api, actions, props };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; }
const details = () => screen.getByRole('complementary', { name: '账户资料' });
async function ready() { await screen.findByLabelText('身份证号'); }
beforeEach(() => {
  let next = 0;
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, writable: true, value: vi.fn(() => `blob:synthetic-${++next}`) });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, writable: true, value: vi.fn() });
});
afterEach(() => vi.restoreAllMocks());

describe('AccountWorkspace', () => {
  it('keeps same-name accounts separate and shows exact multi-currency totals without requiring graph membership', async () => {
    const h = fixture(); render(<AccountWorkspace {...h.props} />); await ready();
    expect(screen.getAllByRole('button', { name: /^选择账户 / })).toHaveLength(2);
    expect(within(details()).getByText('9,999,999,999,999,999.99')).toBeTruthy();
    expect(within(details()).getByText('0.01')).toBeTruthy();
    expect(within(details()).getByText('收入合计（USD）')).toBeTruthy();
    expect(within(details()).getByText('2026-01-01 10:00:00')).toBeTruthy();
    expect(within(details()).getByText(/无需上图即可维护资料/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: `选择账户 ${accountB}` })); await ready();
    await waitFor(() => expect(within(details()).getByText(accountB)).toBeTruthy());
    expect(h.api.profile).toHaveBeenLastCalledWith('1', accountB, expect.any(AbortSignal));
  });

  it('queries, pages and switches sources using case-bound GET filters', async () => {
    const h = fixture(); h.api.list = vi.fn(async (caseId, query) => ({ case_id: caseId, source: query.source, total: 61, page: query.page, page_size: 30, items: [row()] }));
    render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('搜索户名或账号'), { target: { value: '合成同名' } });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));
    await waitFor(() => expect(h.api.list).toHaveBeenLastCalledWith('1', { source: 'flows', q: '合成同名', page: 1, pageSize: 30 }, expect.any(AbortSignal)));
    await waitFor(() => expect((screen.getByRole('button', { name: '账户下一页' }) as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByRole('button', { name: '账户下一页' }));
    await waitFor(() => expect(h.api.list).toHaveBeenLastCalledWith('1', { source: 'flows', q: '合成同名', page: 2, pageSize: 30 }, expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('tab', { name: '身份证对照记录' }));
    await waitFor(() => expect(h.api.list).toHaveBeenLastCalledWith('1', { source: 'identity', q: '', page: 1, pageSize: 30 }, expect.any(AbortSignal)));
  });

  it('navigates source tabs with arrows, Home and End and exposes one named tab panel', async () => {
    const h = fixture(); render(<AccountWorkspace {...h.props} />); await ready();
    const flows = screen.getByRole('tab', { name: '本案流水账户' }), identity = screen.getByRole('tab', { name: '身份证对照记录' });
    expect(flows.tabIndex).toBe(0); expect(identity.tabIndex).toBe(-1);
    expect(flows.getAttribute('aria-controls')).toBe(screen.getByRole('tabpanel', { name: '本案流水账户' }).id);
    fireEvent.change(screen.getByLabelText('搜索户名或账号'), { target: { value: '未提交检索' } });
    flows.focus(); fireEvent.keyDown(flows, { key: 'ArrowRight' });
    await waitFor(() => expect(h.api.list).toHaveBeenLastCalledWith('1', { source: 'identity', q: '', page: 1, pageSize: 30 }, expect.any(AbortSignal)));
    expect(document.activeElement).toBe(identity); expect(identity.tabIndex).toBe(0); expect(flows.tabIndex).toBe(-1);
    expect(identity.getAttribute('aria-controls')).toBe(screen.getByRole('tabpanel', { name: '身份证对照记录' }).id);
    expect((screen.getByLabelText('搜索户名或账号') as HTMLInputElement).value).toBe('');
    fireEvent.keyDown(identity, { key: 'ArrowRight' }); expect(document.activeElement).toBe(flows);
    fireEvent.keyDown(flows, { key: 'End' }); expect(document.activeElement).toBe(identity);
    fireEvent.keyDown(identity, { key: 'Home' }); expect(document.activeElement).toBe(flows);
    fireEvent.keyDown(flows, { key: 'ArrowLeft' }); expect(document.activeElement).toBe(identity);
  });

  it('announces a delayed save, keeps tabs locked and reconciles an unknown result without reporting success or replaying', async () => {
    const h = fixture(), write = deferred<void>(), check = deferred<void>();
    h.actions.mutate = vi.fn(async () => { await write.promise; throw Object.assign(new Error('合成响应中断'), { code: 'write-unknown', operationId: 'delayed-identity' }); });
    h.actions.reconcile = vi.fn(() => check.promise);
    render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    const save = screen.getByRole('button', { name: '保存' }); fireEvent.click(save);
    expect(save.getAttribute('aria-busy')).toBe('true');
    expect(screen.getByRole('status').textContent).toBe('正在保存身份证号…');
    expect(screen.getByRole('status').getAttribute('aria-busy')).toBe('true');
    const flows = screen.getByRole('tab', { name: '本案流水账户' });
    fireEvent.keyDown(flows, { key: 'ArrowRight' });
    expect(flows.getAttribute('aria-selected')).toBe('true');
    expect((screen.getByRole('tab', { name: '身份证对照记录' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText('身份证号已更新。')).toBeNull();
    await act(async () => write.resolve()); await screen.findByText(/有 1 项写入结果尚未确认/);
    expect(save.getAttribute('aria-busy')).toBe('false'); expect((save as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByText('身份证号已更新。')).toBeNull(); expect(h.actions.mutate).toHaveBeenCalledOnce();
    const reconcile = screen.getByRole('button', { name: '核对状态' }); fireEvent.click(reconcile);
    expect(reconcile.getAttribute('aria-busy')).toBe('true');
    expect(screen.getByRole('status').textContent).toBe('正在核对写入结果…');
    await act(async () => check.resolve()); await screen.findByText('已核对写入结果，资料将重新加载。');
    expect(h.actions.mutate).toHaveBeenCalledOnce(); expect(h.actions.reconcile).toHaveBeenCalledWith('delayed-identity', expect.any(AbortSignal));
  });

  it('announces the actual host action while its response is pending', async () => {
    const h = fixture(), download = deferred<void>(); h.actions.downloadTemplate = vi.fn(() => download.promise);
    render(<AccountWorkspace {...h.props} />); await ready();
    const trigger = screen.getByRole('button', { name: '下载模板' }); fireEvent.click(trigger);
    expect(trigger.getAttribute('aria-busy')).toBe('true');
    expect(screen.getByRole('status').textContent).toBe('正在下载身份证对照表模板…');
    expect((screen.getByRole('button', { name: '主账号管理' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => download.resolve()); await ready();
    expect(trigger.getAttribute('aria-busy')).toBe('false');
    expect(screen.queryByText('正在下载身份证对照表模板…')).toBeNull();
  });

  it('reloads the valid last page when clearing the only identity record on page two reduces the total to thirty', async () => {
    const h = fixture(); let removed = false;
    const correctedPage = deferred<AccountPage>();
    const firstThirty = Array.from({ length: 30 }, (_, index) => row(`SYN-IDENT-${index + 1}`, { id_no: '00000020000101000X' }));
    h.api.list = vi.fn(async (caseId, query) => {
      if (query.source === 'flows') return { case_id: caseId, source: query.source, total: 1, page: 1, page_size: 30, items: [row()] };
      if (removed && query.page === 1) return correctedPage.promise;
      return { case_id: caseId, source: query.source, total: removed ? 30 : 31, page: query.page, page_size: 30, items: query.page === 1 ? firstThirty : removed ? [] : [row(accountA, { id_no: '00000020000101000X' })] };
    });
    h.api.profile = async (caseId, account) => profile(account, caseId, { id_no: removed && account === accountA ? null : '00000020000101000X' });
    h.actions.mutate = vi.fn(async payload => { if (payload.kind === 'identity-set' && !payload.idNo) removed = true; });
    render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.click(screen.getByRole('tab', { name: '身份证对照记录' }));
    await screen.findByText('显示 1–30 条，共 31 条');
    fireEvent.click(screen.getByRole('button', { name: '账户下一页' }));
    await screen.findByText('显示 31–31 条，共 31 条'); await ready();
    fireEvent.click(screen.getByRole('button', { name: '清除身份证号…' }));
    fireEvent.click(screen.getByRole('button', { name: '确认清除身份证号' }));
    await waitFor(() => expect(h.api.list).toHaveBeenLastCalledWith('1', { source: 'identity', q: '', page: 1, pageSize: 30 }, expect.any(AbortSignal)));
    expect(screen.queryByText('显示 31–30 条，共 30 条')).toBeNull();
    expect(screen.queryByText('第 2 / 1 页')).toBeNull();
    expect(screen.queryByRole('heading', { name: '暂无身份证对照记录' })).toBeNull();
    await act(async () => correctedPage.resolve({ case_id: '1', source: 'identity', total: 30, page: 1, page_size: 30, items: firstThirty }));
    await screen.findByText('显示 1–30 条，共 30 条');
    expect(screen.getByText('第 1 / 1 页')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /^选择账户 / })).toHaveLength(30);
  });

  it('shows identity-only accounts with zero funds and disables unsupported main-account creation', async () => {
    const h = fixture(); h.api.list = async (caseId, query) => ({ case_id: caseId, source: query.source, total: 1, page: 1, page_size: 30, items: [row(accountA, { has_flows: false, id_no: '00000020000101000X' })] });
    h.api.profile = async () => profile(accountA, '1', { has_flows: false, id_no: '00000020000101000X', cnt: 0, in_cnt: 0, out_cnt: 0, in_totals: [], out_totals: [], in_amt: '0.00', out_amt: '0.00', tmin: null, tmax: null });
    render(<AccountWorkspace {...h.props} />); await ready();
    expect(within(details()).getAllByText('0.00')).toHaveLength(2);
    expect(within(details()).getByText(/本案暂无该账户的导入流水/)).toBeTruthy();
    expect(within(details()).getByText(/主账号从本案流水账户设置/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '设为主账号' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '查看流水' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000101000X');
  });

  it('saves identity input as a string and requires explicit confirmation to clear the saved identity', async () => {
    const h = fixture(); let idNo = '';
    h.api.profile = async () => profile(accountA, '1', { id_no: idNo });
    h.actions.mutate = vi.fn(async mutation => { if (mutation.kind === 'identity-set') idNo = mutation.idNo; });
    render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000x' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenCalledWith({ kind: 'identity-set', account: accountA, idNo: '00000020000101000X' }, expect.any(AbortSignal)));
    await screen.findByRole('button', { name: '清除身份证号…' });
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '' } });
    expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '清除身份证号…' }));
    expect(h.actions.mutate).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '确认清除身份证号' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenLastCalledWith({ kind: 'identity-set', account: accountA, idNo: '' }, expect.any(AbortSignal)));
  });

  it('retains unsaved identity drafts across account, source and A–B–A case switches', async () => {
    const h = fixture(); const { rerender } = render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    fireEvent.click(screen.getByRole('button', { name: `选择账户 ${accountB}` }));
    await waitFor(() => expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe(''));
    fireEvent.click(screen.getByRole('tab', { name: '身份证对照记录' }));
    fireEvent.click(await screen.findByRole('button', { name: `选择账户 ${accountA}` }));
    await waitFor(() => expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000101000X'));
    rerender(<AccountWorkspace {...h.props} context={{ caseId: '2', name: cases[1].name, generation: 2 }} />); await ready();
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('');
    rerender(<AccountWorkspace {...h.props} context={{ ...context, generation: 3 }} />); await ready();
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000101000X');
  });

  it('refreshes authoritative bases on revision changes while preserving dirty input', async () => {
    const h = fixture(); const { rerender } = render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    h.api.profile = vi.fn(async () => profile(accountA, '1', { id_no: '00000020000202000X' }));
    rerender(<AccountWorkspace {...h.props} revision={1} />); await ready();
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000101000X');
    expect(await screen.findByText(/资料已更新，未提交的身份证号已保留/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '放弃修改' }));
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000202000X');
  });

  it('allows confirmed case-wide draft discard when the edited account disappears and preserves another case draft', async () => {
    const h = fixture(); let visible = true;
    h.api.list = vi.fn(async (caseId, query) => ({ case_id: caseId, source: query.source, total: caseId === '1' && !visible ? 0 : 1, page: query.page, page_size: 30, items: caseId === '1' && !visible ? [] : [row()] }));
    const { rerender } = render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    rerender(<AccountWorkspace {...h.props} context={{ caseId: '2', name: cases[1].name, generation: 2 }} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000202000X' } });
    rerender(<AccountWorkspace {...h.props} context={{ ...context, generation: 3 }} />); await ready();
    visible = false; rerender(<AccountWorkspace {...h.props} context={{ ...context, generation: 3 }} revision={1} />);
    await screen.findByRole('heading', { name: '暂无流水账户' });
    expect(screen.queryByLabelText('身份证号')).toBeNull();
    expect((screen.getByRole('button', { name: '主账号管理' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '放弃本案未提交修改' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: '放弃本案未提交修改' })).getByRole('button', { name: '取消' }));
    expect((screen.getByRole('button', { name: '导入身份证对照表' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '放弃本案未提交修改' }));
    fireEvent.click(screen.getByRole('button', { name: '确认放弃本案修改' }));
    expect(screen.queryByRole('button', { name: '放弃本案未提交修改' })).toBeNull();
    expect((screen.getByRole('button', { name: '主账号管理' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: '导入身份证对照表' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '主账号管理' }));
    await waitFor(() => expect(h.actions.openMainAccounts).toHaveBeenCalledOnce());
    expect(h.actions.mutate).not.toHaveBeenCalled();
    rerender(<AccountWorkspace {...h.props} context={{ caseId: '2', name: cases[1].name, generation: 4 }} />); await ready();
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000202000X');
  });

  it('disables case-wide draft discard during busy or unresolved operations, including its confirmation', async () => {
    const h = fixture(); const download = deferred<void>();
    h.actions.downloadTemplate = vi.fn(() => download.promise);
    const { rerender } = render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    fireEvent.click(screen.getByRole('button', { name: '下载模板' }));
    expect((screen.getByRole('button', { name: '放弃本案未提交修改' }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => download.resolve()); await ready();
    fireEvent.click(screen.getByRole('button', { name: '放弃本案未提交修改' }));
    rerender(<AccountWorkspace {...h.props} unresolvedOperations={[{ operationId: 'pending-main', account: accountA, kind: 'main-set' }]} />);
    expect((screen.getByRole('button', { name: '确认放弃本案修改' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '确认放弃本案修改' }));
    expect(screen.getByRole('dialog', { name: '放弃本案未提交修改' })).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '取消' }));
    expect((screen.getByRole('button', { name: '放弃本案未提交修改' }) as HTMLButtonElement).disabled).toBe(true);
    expect(h.actions.mutate).not.toHaveBeenCalled();
  });

  it('delegates import, template, main-account ledger and flow navigation and guards bulk operations with dirty input', async () => {
    const h = fixture(); render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.click(screen.getByRole('button', { name: '导入身份证对照表' })); await waitFor(() => expect(h.actions.importIdentity).toHaveBeenCalledWith(expect.any(AbortSignal))); await ready();
    fireEvent.click(screen.getByRole('button', { name: '下载模板' })); await waitFor(() => expect(h.actions.downloadTemplate).toHaveBeenCalledWith(expect.any(AbortSignal))); await ready();
    fireEvent.click(screen.getByRole('button', { name: '主账号管理' })); await waitFor(() => expect(h.actions.openMainAccounts).toHaveBeenCalledWith(expect.any(AbortSignal))); await ready();
    fireEvent.click(screen.getByRole('button', { name: '查看流水' })); await waitFor(() => expect(h.actions.viewFlows).toHaveBeenCalledWith(accountA, expect.any(AbortSignal))); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '未提交输入' } });
    expect((screen.getByRole('button', { name: '主账号管理' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '导入身份证对照表' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('delegates automatic-to-manual conversion and cancelling a main account', async () => {
    const h = fixture(); let main = true; let mainSource: 'auto' | 'manual' | null = 'auto';
    h.api.profile = async () => profile(accountA, '1', { is_main: main, main_source: mainSource });
    h.actions.mutate = vi.fn(async mutation => { if (mutation.kind === 'main-set') { main = mutation.on; mainSource = main ? 'manual' : null; } });
    render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.click(screen.getByRole('button', { name: '改为手动' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenCalledWith({ kind: 'main-set', account: accountA, on: true }, expect.any(AbortSignal)));
    await ready(); fireEvent.click(screen.getByRole('button', { name: '取消主账号' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenLastCalledWith({ kind: 'main-set', account: accountA, on: false }, expect.any(AbortSignal)));
  });

  it('blocks duplicate unknown writes, reconciles read-only and reports a confirmed mismatch without replaying', async () => {
    const h = fixture(); h.actions.mutate = vi.fn(async () => { throw Object.assign(new Error('响应中断'), { code: 'write-unknown', operationId: 'op-identity-1' }); });
    h.actions.reconcile = vi.fn(async () => { throw Object.assign(new Error('未达到请求状态'), { code: 'write-mismatch' }); });
    render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await screen.findByText(/有 1 项写入结果尚未确认/);
    expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '保存' })); expect(h.actions.mutate).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '核对状态' }));
    await screen.findByText(/已核对：资料尚未达到请求状态/); await ready();
    expect(h.actions.reconcile).toHaveBeenCalledWith('op-identity-1', expect.any(AbortSignal));
    expect(h.actions.mutate).toHaveBeenCalledOnce();
    expect((screen.getByRole('button', { name: '保存' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('retains identity input after a definite write failure and retries only on another user action', async () => {
    const h = fixture(); let attempt = 0;
    h.actions.mutate = vi.fn(async () => { if (++attempt === 1) throw new Error('合成写入已拒绝'); });
    render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await screen.findByText('合成写入已拒绝');
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000101000X');
    expect(h.actions.mutate).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenCalledTimes(2));
  });

  it('honors parent-retained unknown operations after reopening the page, including other accounts', async () => {
    const h = fixture(); render(<AccountWorkspace {...h.props} unresolvedOperations={[{ account: accountB, kind: 'photo-set', operationId: 'host-photo-unknown' }]} />); await ready();
    expect(screen.getByText(/有 1 项写入结果尚未确认/)).toBeTruthy();
    expect((screen.getByLabelText('当前案件') as HTMLSelectElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: '设为主账号' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '核对状态' }));
    await waitFor(() => expect(h.actions.reconcile).toHaveBeenCalledWith('host-photo-unknown', expect.any(AbortSignal)));
    expect(h.actions.mutate).not.toHaveBeenCalled();
  });

  it('rejects oversized or unsupported images and writes a photo only after image load and crop confirmation', async () => {
    const h = fixture(), upload = deferred<void>(); h.actions.mutate = vi.fn(() => upload.promise);
    render(<AccountWorkspace {...h.props} />); await ready();
    const choose = screen.getByLabelText('选择账户照片');
    fireEvent.change(choose, { target: { files: [new File([new Uint8Array(8 * 1024 * 1024 + 1)], 'large.png', { type: 'image/png' })] } });
    expect(screen.getByText('照片不能超过 8 MB。')).toBeTruthy();
    fireEvent.change(choose, { target: { files: [new File(['synthetic'], 'bad.gif', { type: 'image/gif' })] } });
    expect(screen.getByText(/请选择 JPG、JPEG/)).toBeTruthy();
    const file = new File(['synthetic-pixels'], 'synthetic.jpeg', { type: 'image/jpeg' });
    fireEvent.change(choose, { target: { files: [file] } });
    expect(h.actions.mutate).not.toHaveBeenCalled();
    expect((screen.getByRole('button', { name: '确认裁剪并上传' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.load(screen.getByAltText('照片裁剪预览'));
    fireEvent.change(screen.getByLabelText('裁剪水平位置'), { target: { value: '35' } });
    fireEvent.change(screen.getByLabelText('裁剪半径'), { target: { value: '28' } });
    fireEvent.click(screen.getByRole('button', { name: '确认裁剪并上传' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenCalledWith({ kind: 'photo-set', account: accountA, file, crop: { cx: 0.35, cy: 0.5, r: 0.28 } }, expect.any(AbortSignal)));
    expect(within(screen.getByRole('dialog', { name: '裁剪并上传账户照片' })).getByRole('status').textContent).toBe('正在上传账户照片…');
    expect(screen.getByRole('button', { name: '确认裁剪并上传' }).getAttribute('aria-busy')).toBe('true');
    await act(async () => upload.resolve());
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-1'));
  });

  it('recrops and removes an existing photo through callbacks and refreshes same-second photo URLs', async () => {
    const h = fixture(); let hasPhoto = true;
    h.api.profile = async () => profile(accountA, '1', { photo: hasPhoto ? { updated_at: '2026-01-01 10:00:00', crop_cx: 0.45, crop_cy: 0.55, crop_r: 0.3 } : null });
    h.actions.mutate = vi.fn(async mutation => { if (mutation.kind === 'photo-clear') hasPhoto = false; });
    render(<AccountWorkspace {...h.props} />); await ready();
    const before = screen.getByAltText(`${accountA} 账户照片`).getAttribute('src');
    fireEvent.click(screen.getByRole('button', { name: '重新裁剪' })); fireEvent.load(screen.getByAltText('照片裁剪预览'));
    fireEvent.click(screen.getByRole('button', { name: '确认保存裁剪' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenCalledWith({ kind: 'photo-crop', account: accountA, crop: { cx: 0.45, cy: 0.55, r: 0.3 } }, expect.any(AbortSignal)));
    await ready(); expect(screen.getByAltText(`${accountA} 账户照片`).getAttribute('src')).not.toBe(before);
    fireEvent.click(screen.getByRole('button', { name: '移除照片' })); expect(h.actions.mutate).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '确认移除照片' }));
    await waitFor(() => expect(h.actions.mutate).toHaveBeenLastCalledWith({ kind: 'photo-clear', account: accountA }, expect.any(AbortSignal)));
  });

  it('releases selected photo bytes and object URLs when the case is switched', async () => {
    const h = fixture(); const { rerender } = render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('选择账户照片'), { target: { files: [new File(['synthetic'], 'synthetic.webp', { type: 'image/webp' })] } });
    expect(screen.getByRole('dialog')).toBeTruthy();
    rerender(<AccountWorkspace {...h.props} context={{ caseId: '2', name: cases[1].name, generation: 2 }} />); await ready();
    expect(screen.queryByRole('dialog')).toBeNull(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-1'); expect(h.actions.mutate).not.toHaveBeenCalled();
  });

  it('closes top-layer photo previews when inactive while retaining identity drafts and unresolved operations', async () => {
    const h = fixture(); const { rerender } = render(<AccountWorkspace {...h.props} />); await ready();
    fireEvent.change(screen.getByLabelText('身份证号'), { target: { value: '00000020000101000X' } });
    fireEvent.change(screen.getByLabelText('选择账户照片'), { target: { files: [new File(['synthetic'], 'synthetic.png', { type: 'image/png' })] } });
    expect(screen.getByRole('dialog', { name: '裁剪并上传账户照片' })).toBeTruthy();
    const unresolved = [{ operationId: 'retained-main-write', account: accountB, kind: 'main-set' as const }];
    rerender(<AccountWorkspace {...h.props} active={false} unresolvedOperations={unresolved} />);
    expect(screen.queryByRole('dialog')).toBeNull(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-1');
    rerender(<AccountWorkspace {...h.props} active unresolvedOperations={unresolved} />); await ready();
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).value).toBe('00000020000101000X');
    expect((screen.getByLabelText('身份证号') as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/有 1 项写入结果尚未确认/)).toBeTruthy();
    expect(h.actions.mutate).not.toHaveBeenCalled();
  });

  it('aborts hidden-page reads and ignores their late replies before reading fresh data on reactivation', async () => {
    const h = fixture(); const old = deferred<AccountProfile>(); let firstSignal: AbortSignal | undefined;
    h.api.profile = vi.fn((_caseId, _account, signal) => { firstSignal = signal; return old.promise; });
    const { rerender } = render(<AccountWorkspace {...h.props} />);
    await waitFor(() => expect(h.api.profile).toHaveBeenCalledOnce());
    rerender(<AccountWorkspace {...h.props} active={false} />);
    expect(firstSignal?.aborted).toBe(true);
    await act(async () => old.resolve(profile(accountA, '1', { name: '隐藏后过期资料' })));
    expect(within(details()).queryByText('隐藏后过期资料')).toBeNull();
    h.api.profile = vi.fn(async () => profile(accountA, '1', { name: '重新读取的最新资料' }));
    rerender(<AccountWorkspace {...h.props} active />); await ready();
    expect(within(details()).getByText('重新读取的最新资料')).toBeTruthy();
    expect(within(details()).queryByText('隐藏后过期资料')).toBeNull();
  });

  it('releases a photo preview on unknown writes and prevents a second photo submission before reconciliation', async () => {
    const h = fixture(); h.actions.mutate = vi.fn(async () => { throw Object.assign(new Error('合成上传回执中断'), { code: 'write-unknown', operationId: 'photo-uncertain' }); });
    render(<AccountWorkspace {...h.props} />); await ready();
    const choose = screen.getByLabelText('选择账户照片'); choose.focus();
    fireEvent.change(choose, { target: { files: [new File(['synthetic'], 'synthetic.bmp', { type: 'image/bmp' })] } });
    fireEvent.load(screen.getByAltText('照片裁剪预览'));
    fireEvent.click(screen.getByRole('button', { name: '确认裁剪并上传' }));
    await screen.findByText(/有 1 项写入结果尚未确认/);
    expect(screen.queryByRole('dialog')).toBeNull(); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-1');
    expect((screen.getByLabelText('选择账户照片') as HTMLInputElement).disabled).toBe(true);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '下载模板' }));
    expect(h.actions.mutate).toHaveBeenCalledOnce();
  });

  it('ignores old profile replies after A–B–A case changes and supports StrictMode effect replay', async () => {
    const h = fixture(); const old = deferred<AccountProfile>(); let calls = 0;
    h.api.profile = vi.fn(async (caseId, account) => { if (caseId === '1' && ++calls === 1) return old.promise; return profile(account, caseId, { name: '最新合成主体' }); });
    const { rerender } = render(<AccountWorkspace {...h.props} />);
    await waitFor(() => expect(h.api.profile).toHaveBeenCalledOnce());
    rerender(<AccountWorkspace {...h.props} context={{ caseId: '2', name: cases[1].name, generation: 2 }} />); await ready();
    rerender(<AccountWorkspace {...h.props} context={{ ...context, generation: 3 }} />); await ready();
    await act(async () => old.resolve(profile(accountA, '1', { name: '过期合成主体' })));
    expect(within(details()).queryByText('过期合成主体')).toBeNull(); expect(within(details()).getByText('最新合成主体')).toBeTruthy();
  });

  it('does not show stale profile facts on errors and retries a failed profile read', async () => {
    const h = fixture(); let failed = true;
    h.api.profile = vi.fn(async (caseId, account) => { if (failed) throw new Error('合成账户资料读取失败'); return profile(account, caseId); });
    render(<StrictMode><AccountWorkspace {...h.props} /></StrictMode>);
    await screen.findByText('合成账户资料读取失败');
    expect(within(details()).queryByText('9,999,999,999,999,999.99')).toBeNull();
    failed = false; fireEvent.click(screen.getByRole('button', { name: '重新加载资料' })); await ready();
    expect(within(details()).getByText('9,999,999,999,999,999.99')).toBeTruthy();
  });

  it('rejects foreign-case list and profile responses', async () => {
    const h = fixture(); h.api.profile = async () => profile(accountA, '2');
    render(<AccountWorkspace {...h.props} />);
    expect(await screen.findByText(/账户资料与当前案件或账号不一致/)).toBeTruthy();
    expect(screen.queryByLabelText('身份证号')).toBeNull();
  });

  it('rejects a foreign-case list before it can select or read an account', async () => {
    const h = fixture(); h.api.list = async (_caseId, query) => ({ case_id: '2', source: query.source, total: 1, page: 1, page_size: 30, items: [row()] });
    render(<AccountWorkspace {...h.props} />);
    expect(await screen.findByText(/账户列表与当前案件不一致/)).toBeTruthy();
    expect(h.api.profile).not.toHaveBeenCalled(); expect(screen.queryByRole('button', { name: `选择账户 ${accountA}` })).toBeNull();
  });
});
