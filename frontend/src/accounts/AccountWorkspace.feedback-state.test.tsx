import { StrictMode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AccountWorkspace } from './AccountWorkspace';
import type { AccountRegistryApi, AccountWorkspaceActions } from './types';
const a = 'SYNTHETIC-A', b = 'SYNTHETIC-B';
const row = (account: string) => ({ account, name: '合成账户', id_no: null, identity_updated_at: null, photo_at: null, is_main: false, main_source: null, main_marked_at: null, has_flows: true });
function fixture() {
  const api: AccountRegistryApi = { list: vi.fn(async (caseId, query) => ({ case_id: caseId, source: query.source, total: 2, page: 1, page_size: 30, items: [row(a), row(b)] })),
    profile: vi.fn(async (caseId, account) => ({ ...row(account), case_id: caseId, photo: null, cnt: 0, in_cnt: 0, out_cnt: 0, in_totals: [], out_totals: [], in_amt: null, out_amt: null, tmin: null, tmax: null, top_counterparties: [] })), photoUrl: vi.fn() };
  const actions: AccountWorkspaceActions = { mutate: vi.fn(async () => {}), reconcile: vi.fn(async () => {}), openMainAccounts: vi.fn(async () => {}), importIdentity: vi.fn(async () => {}), downloadTemplate: vi.fn(async () => {}), viewFlows: vi.fn(async () => {}) };
  return { api, actions };
}
describe('account feedback ownership', () => {
  it.each(['success', 'error'])('clears account A %s feedback when selecting B', async outcome => {
    const { api, actions } = fixture();
    if (outcome === 'error') vi.mocked(actions.mutate).mockRejectedValue(new Error('合成写入被拒绝'));
    render(<AccountWorkspace context={{ caseId: '1', name: '合成案件', generation: 1 }} cases={[{ id: '1', name: '合成案件' }]} revision={0} actions={actions} api={api} onSelectCase={vi.fn()} />);
    await screen.findByLabelText('身份证号'); fireEvent.click(screen.getByRole('button', { name: '设为主账号' }));
    const text = outcome === 'success' ? '主账号标记已更新。' : '合成写入被拒绝';
    await screen.findByText(text);
    fireEvent.click(screen.getByRole('button', { name: `选择账户 ${b}` }));
    await waitFor(() => expect(api.profile).toHaveBeenLastCalledWith('1', b, expect.any(AbortSignal)));
    expect(screen.queryByText(text)).toBeNull();
  });
  it('keeps a successful write distinct from a failed profile refresh', async () => {
    const { api, actions } = fixture();
    const original = vi.mocked(api.profile).getMockImplementation()!; let reads = 0;
    vi.mocked(api.profile).mockImplementation((...args) => ++reads === 1 ? original(...args) : Promise.reject(new Error('合成刷新中断')));
    render(<AccountWorkspace context={{ caseId: '1', name: '合成案件', generation: 1 }} cases={[{ id: '1', name: '合成案件' }]} revision={0} actions={actions} api={api} onSelectCase={vi.fn()} />);
    await screen.findByLabelText('身份证号'); fireEvent.click(screen.getByRole('button', { name: '设为主账号' }));
    expect(await screen.findByText('主账号标记已更新。')).toBeTruthy();
    expect(await screen.findByText('资料刷新失败：合成刷新中断')).toBeTruthy(); expect(actions.mutate).toHaveBeenCalledOnce();
  });
});


describe('independent account feedback preview', () => {
  it('keeps unknown state in the original account workspace under StrictMode', async () => {
    const { api, actions } = fixture();
    vi.mocked(actions.mutate).mockRejectedValue(Object.assign(new Error('合成未知结果'), { code: 'write-unknown', operationId: 'synthetic-operation' }));
    render(<StrictMode><AccountWorkspace context={{ caseId: '1', name: '合成案件', generation: 1 }} cases={[{ id: '1', name: '合成案件' }]} revision={0} actions={actions} api={api} onSelectCase={vi.fn()} /></StrictMode>);
    await screen.findByLabelText('身份证号'); fireEvent.click(screen.getByRole('button', { name: '设为主账号' }));
    expect(await screen.findByText(/有 1 项写入结果尚未确认/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '核对状态' })).toBeTruthy();
    expect(document.querySelector('.ff-entry,.ff-panel,.ff-dock')).toBeNull();
  });
});

describe('account uncertain-result presentation', () => {
  it('keeps a failed read-only reconciliation inside the persistent amber state', async () => {
    const { api, actions } = fixture();
    vi.mocked(actions.mutate).mockRejectedValue(Object.assign(new Error('合成未知结果'), { code: 'write-unknown', operationId: 'synthetic-operation' }));
    vi.mocked(actions.reconcile).mockRejectedValue(new Error('合成核对暂不可用'));
    render(<AccountWorkspace context={{ caseId: '1', name: '合成案件', generation: 1 }} cases={[{ id: '1', name: '合成案件' }]} revision={0} actions={actions} api={api} onSelectCase={vi.fn()} />);
    await screen.findByLabelText('身份证号'); fireEvent.click(screen.getByRole('button', { name: '设为主账号' }));
    await screen.findByText(/有 1 项写入结果尚未确认/); fireEvent.click(screen.getByRole('button', { name: '核对状态' }));
    const message = await screen.findByText('合成核对暂不可用');
    expect(message.closest('.aw-unknown')).toBeTruthy(); expect(document.querySelector('.aw-error')).toBeNull();
    expect(actions.mutate).toHaveBeenCalledOnce(); expect(actions.reconcile).toHaveBeenCalledOnce();
  });
});
