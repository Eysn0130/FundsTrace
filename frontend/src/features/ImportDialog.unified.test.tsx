import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportJob, ImportPreview, ImportRecordPage } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsHostActionsV1 } from '../panel/host';
import { ImportDialog } from './ImportDialog';

const summary: NonNullable<ImportPreview['table_summary']> = {
  profile: 'synthetic', record_total: 2, accepted: 1, unresolved: 1, non_monetary: 0, non_transaction: 0, auxiliary: 0,
  reason_counts: { invalid_time: 1 }, currency_totals: { CNY: '9999999999999999.99' },
  members: ['a.csv','b.xlsx'].map(name => ({ member_path: 'folder/' + name, role: 'table', row_count: 1, size_bytes: 100, encoding: 'utf-8', sha256: 'a'.repeat(64) })),
  documents: [{ member_path: 'folder/a.csv', header_row: 1, columns: ['交易时间','金额'] }, { member_path: 'folder/b.xlsx', header_row: 3, columns: ['成交日期','原金额'] }],
  mapping_evidence: [{ member_path: 'folder/a.csv', field: 'trade_time', column: '交易时间' }, { member_path: 'folder/b.xlsx', field: 'trade_time', column: '成交日期' }],
  unresolved_examples: [{ member_path: 'folder/b.xlsx', source_row: 128, reason_code: 'invalid_time' }],
};
const preview: ImportPreview = { case_id: '1', import_id: '0123456789abcdef', filename: 'synthetic.zip', mode: 'B', automatic: true,
  columns: ['交易时间','金额'], mapping: {}, self_accounts_in_file: [], stats: { total: 2, ok: 1, skipped: 1 }, preview: [], table_summary: summary };
const result = { total: 2, ok: 1, skipped: 1, rows_dup: 0, batch_id: '1', bad_examples: [], self_accounts: [], self_accounts_count: 0, main_matched: [], accounts_in_batch: 0, main_detected: [], source_id: preview.import_id, table_summary: summary };
function clientFor(): FundsCaseClientV1 {
  return { binding: { caseId: '1', scopeKey: 'unified', generation: 1 }, stats: vi.fn(), flows: vi.fn(), imports: vi.fn(), sources: vi.fn(), cleanup: vi.fn(),
    preview: vi.fn(async () => preview), repreview: vi.fn(async () => preview), commit: vi.fn(async () => ({ case_id: '1', import_id: preview.import_id, started: true })),
    job: vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: preview.import_id, stage: 'done', done: 2, total: 2, result, error: null, message: '完成',
      member_results: [{ member_path: 'folder/a.csv', dispositions: { imported: 1 } }, { member_path: 'folder/b.xlsx', dispositions: { unresolved: 1 } }] })) };
}
function mount(client: FundsCaseClientV1, controller = new AbortController(), files = [new File(['synthetic'], 'synthetic.zip')], actions?: FundsHostActionsV1) {
  const view = render(<ImportDialog client={client} scopeSignal={controller.signal} actions={actions} target={{ caseId: '1', name: '合成案件' }} onImported={vi.fn()} onClose={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files } });
  return { view, controller };
}
describe('the common import workspace', () => {
  it('opens a failed file in preview and lets the footer show its failure result', async () => {
    const client = clientFor();
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    client.preview = vi.fn().mockResolvedValueOnce(preview).mockRejectedValueOnce(Object.assign(new Error('合成损坏工作簿'), { status: 400, failureKind: 'file' }));
    mount(client, new AbortController(), [new File(['synthetic'], 'synthetic.zip'), new File(['broken'], 'broken.xlsx')]);
    await waitFor(() => expect(document.querySelector('.ft-file-counts')?.textContent).toContain('失败 1'));
    fireEvent.click(screen.getByRole('treeitem', { name: /a.csv/ }));
    expect(screen.getByRole('button', { name: '返回数据预览' })).toBeTruthy();
    fireEvent.click(screen.getByRole('treeitem', { name: /broken.xlsx/ }));
    expect(screen.getByRole('alert').textContent).toContain('合成损坏工作簿');
    const showResult = screen.getByRole('button', { name: '查看处理结果' }) as HTMLButtonElement;
    expect(showResult.disabled).toBe(false);
    fireEvent.click(showResult);
    expect(screen.getByText('此文件导入失败，没有可统计的资金记录。')).toBeTruthy();
    expect(client.importFundsReport).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '返回数据预览' }));
    expect(screen.getByRole('heading', { name: 'broken.xlsx' })).toBeTruthy();
    expect(document.querySelector('.ft-file-outcomes')?.textContent).toBe('导入失败');
    expect(screen.getByText('合成损坏工作簿')).toBeTruthy();
    fireEvent.contextMenu(screen.getByRole('treeitem', { name: /broken.xlsx/ }));
    fireEvent.click(screen.getByRole('menuitem', { name: '从列表移除' }));
    expect(screen.queryByRole('treeitem', { name: /broken.xlsx/ })).toBeNull();
    expect(screen.getByRole('treeitem', { name: /synthetic.zip/ })).toBeTruthy();
  });
  it('opens the selected member by its own result and preserves manual view switching and the tree', async () => {
    const client = clientFor();
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    mount(client); await screen.findByRole('status', { name: '导入完成' });
    const dialog = screen.getByRole('dialog', { name: '导入流水' });
    const tree = screen.getByRole('tree'), aside = tree.closest('aside');
    const scroller = tree.closest('.ft-tree-scroll')!;
    scroller.scrollTop = 73;
    expect(dialog.classList.contains('is-result-view')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '折叠右侧详情' }));
    fireEvent.click(within(tree).getByRole('treeitem', { name: /a\.csv/ }));
    expect(screen.getByRole('dialog', { name: '导入流水' })).toBe(dialog);
    expect(dialog.classList.contains('is-result-view')).toBe(true);
    expect(screen.getByRole('button', { name: '折叠右侧详情' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('navigation', { name: '报告目录' })).toBeTruthy();
    expect(screen.getByRole('tree')).toBe(tree);
    expect(tree.closest('aside')).toBe(aside);
    expect(scroller.scrollTop).toBe(73);
    await waitFor(() => expect(client.importFundsReport).toHaveBeenLastCalledWith(preview.import_id, { memberPath: 'folder/a.csv', page: 1 }, expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('treeitem', { name: /b.xlsx/ }));
    expect(screen.queryByRole('navigation', { name: '报告目录' })).toBeNull();
    expect(screen.getByRole('region', { name: '字段映射，可上下左右滚动' })).toBeTruthy();
    expect(client.importFundsReport).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    expect(screen.getByRole('navigation', { name: '报告目录' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '返回数据预览' })).toBeTruthy();
    await waitFor(() => expect(client.importFundsReport).toHaveBeenLastCalledWith(preview.import_id, { memberPath: 'folder/b.xlsx', page: 1 }, expect.any(AbortSignal)));
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索导入文件' }), { target: { value: 'b.xlsx' } });
    fireEvent.click(screen.getByRole('button', { name: '返回数据预览' }));
    expect(dialog.classList.contains('is-result-view')).toBe(false);
    expect(screen.queryByRole('navigation', { name: '报告目录' })).toBeNull();
    expect(screen.getByRole('searchbox', { name: '搜索导入文件' }).getAttribute('value')).toBe('b.xlsx');
    expect(screen.getByRole('tree')).toBe(tree);
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBeTruthy();
    expect(client.commit).toHaveBeenCalledOnce();
  });
  it.each<{ label: string; dispositions: Record<string, number>; resultView: boolean }>([
    { label: 'confirmed duplicate', dispositions: { duplicate: 1 }, resultView: true },
    { label: 'missing member disposition', dispositions: {}, resultView: false },
  ])('routes a completed file with $label appropriately', async ({ dispositions, resultView }) => {
    const client = clientFor(), job = client.job;
    client.job = vi.fn(async (id, signal) => ({ ...await job(id, signal), member_results: [
      { member_path: 'folder/a.csv', dispositions },
    ] }));
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    mount(client); await screen.findByRole('status', { name: '导入完成' });
    fireEvent.click(screen.getByRole('treeitem', { name: /a\.csv/ }));
    expect(screen.getByRole('dialog').classList.contains('is-result-view')).toBe(resultView);
    expect(client.importFundsReport).toHaveBeenCalledTimes(resultView ? 1 : 0);
  });
  it.each([0, 1])('routes a legacy receipt with %i invalid rows', async skipped => {
    const client = clientFor(), job = client.job;
    client.preview = vi.fn(async () => ({ ...preview, filename: 'legacy.csv', table_summary: null }));
    client.job = vi.fn(async (id, signal) => ({ ...await job(id, signal),
      result: { ...result, total: 1 + skipped, skipped, table_summary: null }, member_results: [],
    }));
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    mount(client, new AbortController(), [new File(['synthetic'], 'legacy.csv')]);
    await screen.findByRole('status', { name: '导入完成' });
    fireEvent.click(screen.getByRole('treeitem', { name: /legacy\.csv/ }));
    expect(screen.getByRole('dialog').classList.contains('is-result-view')).toBe(skipped === 0);
    if (skipped === 0) expect(screen.getByRole('heading', { name: '导入回执' })).toBeTruthy();
    expect(client.importFundsReport).not.toHaveBeenCalled();
  });
  it('keeps a parked report until a file is activated and cancels the old report when opening its review', async () => {
    const client = clientFor();
    const report = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    client.importFundsReport = report;
    mount(client);
    await screen.findByRole('status', { name: '导入完成' });
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    const catalog = screen.getByRole('navigation', { name: '报告目录' });
    const tree = screen.getByRole('tree', { name: '导入文件' });
    const toggle = screen.getByRole('button', { name: '折叠右侧详情' });
    expect(toggle.closest('header')).toBe(screen.getByRole('searchbox', { name: '搜索导入文件' }).closest('header'));
    const inspector = document.getElementById(toggle.getAttribute('aria-controls')!)!;
    const original = report.mock.calls[0][2]!;

    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: '展开右侧详情' })).toBe(toggle);
    expect(document.activeElement).toBe(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(inspector.hasAttribute('inert')).toBe(true);
    expect(screen.queryByRole('navigation', { name: '报告目录' })).toBeNull();
    const accepted = within(tree).getByRole('treeitem', { name: /a\.csv/, description: /数据大小 100 B，数据行数 1，处理摘要 1 条已导入/ });
    expect(within(accepted).getByText('完成', { exact: true }).closest('[data-tone]')?.getAttribute('data-tone')).toBe('success');
    expect(original.aborted).toBe(false);

    fireEvent.click(toggle);
    expect(inspector.hasAttribute('inert')).toBe(false);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('navigation', { name: '报告目录' })).toBe(catalog);
    expect(screen.queryByRole('region', { name: '导入文件明细，可横向滚动' })).toBeNull();
    expect(report).toHaveBeenCalledOnce();

    fireEvent.click(toggle);
    fireEvent.keyDown(within(tree).getByRole('treeitem', { name: /b\.xlsx/, description: /数据行数 1，处理摘要 1 条待核对 · 第 128 行 · 交易时间无效/ }), { key: 'Enter' });
    expect(report).toHaveBeenCalledOnce();
    expect(original.aborted).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBeTruthy();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    expect(report).toHaveBeenLastCalledWith(preview.import_id, { memberPath: 'folder/b.xlsx', page: 1 }, expect.any(AbortSignal));
    expect(screen.getByRole('tree')).toBe(tree);

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole('button', { name: '返回数据预览' }));
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '折叠右侧详情' })).toBe(toggle);
    expect(client.commit).toHaveBeenCalledOnce();
  });
  it.each(['数据预览', '处理结果'])('only collapses %s after an intentional rightward release on the divider', async mode => {
    const client = clientFor();
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    mount(client);
    await screen.findByRole('status', { name: '导入完成' });
    if (mode === '处理结果') fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    const tree = screen.getByRole('tree', { name: '导入文件' });
    const toggle = screen.getByRole('button', { name: '折叠右侧详情' });
    const divider = screen.getByTitle('向右拖动，松开折叠右侧详情');
    const origin = { pointerId: 1, pointerType: 'mouse', isPrimary: true, button: 0, buttons: 1, clientX: 240, clientY: 260 };
    const press = () => fireEvent.pointerDown(divider, origin);
    const move = (x: number, y = 0) => fireEvent.pointerMove(document.body, { ...origin, clientX: origin.clientX + x, clientY: origin.clientY + y });
    const release = (x: number, y = 0) => fireEvent.pointerUp(document.body, { ...origin, buttons: 0, clientX: origin.clientX + x, clientY: origin.clientY + y });

    fireEvent.pointerOver(divider);
    for (const [x, y] of [[0, 0], [71, 0], [-90, 0], [90, 110]]) {
      press(); move(x, y); release(x, y);
      expect(toggle.getAttribute('aria-expanded')).toBe('true');
    }
    press(); move(90);
    expect(divider.hasAttribute('data-armed')).toBe(true);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    move(12); release(12);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    press(); move(90);
    expect(fireEvent.keyDown(window, { key: 'Escape' })).toBe(false);
    release(90);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    press(); move(90);
    fireEvent.pointerCancel(document.body, origin);
    release(90);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    press(); move(90);
    fireEvent.pointerLeave(document.documentElement, origin);
    release(90);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    press(); move(72); release(72);
    expect(screen.getByRole('button', { name: '展开右侧详情' })).toBe(toggle);
    expect(document.activeElement).toBe(toggle);
    expect(screen.getByRole('tree', { name: '导入文件' })).toBe(tree);
    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: mode === '处理结果' ? '返回数据预览' : '查看处理结果' })).toBeTruthy();
  });
  it('replaces issue cards and source-row jumps with the shared scoped review table when the host supports it',async()=>{
    const client=clientFor();
    client.importReview=vi.fn<NonNullable<FundsCaseClientV1['importReview']>>(async(sourceId,memberPath,query)=>({case_id:'1',source_id:sourceId,member_path:memberPath,view:query.view,
      counts:{all:1,review:1,accepted:0},items:[{record_no:2,source_row:128,member_path:memberPath,record_key:'SYN-REVIEW',disposition:'unresolved',reason_code:'invalid_time',details:{},flow_id:null,preview:null,
        original:[{column:1,header:'成交日期',value:'合成坏日期',fields:['trade_time']}]}],total:1,page:1,page_size:50,issue:{index:1,record_no:2,source_row:128,reason_code:'invalid_time'}}));
    mount(client);await screen.findByRole('status',{name:'导入完成'});
    fireEvent.click(screen.getByRole('treeitem',{name:/b.xlsx/}));
    await screen.findByText('合成坏日期');
    expect(document.querySelector('.ft-issues')).toBeNull();expect(screen.queryByRole('link',{name:'查看来源行'})).toBeNull();
    expect(screen.getByRole('group',{name:'来源记录筛选'})).toBeTruthy();
    expect(client.importReview).toHaveBeenLastCalledWith(preview.import_id,'folder/b.xlsx',{view:'review',page:1,issueIndex:1},expect.any(AbortSignal));
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    fireEvent.click(screen.getByRole('button', { name: '返回数据预览' }));
    await screen.findByRole('button', { name: '待核对 1', pressed: true });
  });
  it('opens the selected member in the pending-only preview from the full-width processing summary', async () => {
    const client = clientFor();
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    client.importReview = vi.fn<NonNullable<FundsCaseClientV1['importReview']>>(async (sourceId, memberPath, query) => ({
      case_id: '1', source_id: sourceId, member_path: memberPath, view: query.view,
      counts: { all: 1, review: 1, accepted: 0 }, total: 1, page: 1, page_size: 50,
      issue: { index: 1, record_no: 2, source_row: 128, reason_code: 'invalid_time' },
      items: [{ record_no: 2, source_row: 128, member_path: memberPath, record_key: 'SYN-128', disposition: 'unresolved',
        reason_code: 'invalid_time', details: {}, flow_id: null,
        original: [{ column: 1, header: '成交日期', value: '合成无效日期', fields: ['trade_time'] }] }],
    }));
    mount(client);
    await screen.findByRole('status', { name: '导入完成' });
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    fireEvent.click(screen.getByRole('button', { name: '折叠右侧详情' }));
    const tree = screen.getByRole('tree', { name: '导入文件' });
    const details = screen.getByRole('region', { name: '导入文件明细，可横向滚动' });
    expect(within(details).getByText('数据大小', { exact: true })).toBeTruthy();
    expect(within(details).queryByText('待核对', { exact: true })).toBeNull();
    expect(within(details).getByText('导入时长', { exact: true })).toBeTruthy();
    expect(within(tree).getByRole('treeitem', { name: /synthetic\.zip/, description: /数据大小 9 B，数据行数 2，处理摘要 1 条待核对/ })).toBeTruthy();
    const row = within(tree).getByRole('treeitem', { name: /b\.xlsx/, description: /数据行数 1，处理摘要 1 条待核对 · 第 128 行 · 交易时间无效/ });
    expect(within(row).getByText('完成', { exact: true })).toBeTruthy();
    const review = within(row).getByRole('button', { name: /定位待核对/ });
    expect(review.textContent).toBe('1 条待核对 · 第 128 行 · 交易时间无效');
    fireEvent.click(review);

    expect(screen.getByRole('tree', { name: '导入文件' })).toBe(tree);
    expect(screen.getByRole('button', { name: '折叠右侧详情' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('button', { name: '查看处理结果' })).toBeTruthy();
    await screen.findByText('合成无效日期');
    expect(within(screen.getByRole('group', { name: '来源记录筛选' }))
      .getByRole('button', { name: '待核对 1', pressed: true })).toBeTruthy();
    expect(client.importReview).toHaveBeenLastCalledWith(preview.import_id, 'folder/b.xlsx',
      { view: 'review', page: 1, issueIndex: 1 }, expect.any(AbortSignal));
  });

  it('shows measured durations only on original files and opens the saved failure reason', async ({ onTestFinished }) => {
    let now = 0;
    const clock = vi.spyOn(performance, 'now').mockImplementation(() => now);
    onTestFinished(() => clock.mockRestore());
    const client = clientFor();
    client.preview = vi.fn().mockImplementationOnce(async () => { now += 1500; return preview; })
      .mockImplementationOnce(async () => {
        now += 23;
        throw Object.assign(new Error('合成损坏工作簿'), { status: 400, failureKind: 'file' });
      });
    mount(client, new AbortController(), [new File(['synthetic'], 'synthetic.zip'), new File(['broken'], 'broken.xlsx')]);
    await waitFor(() => expect(document.querySelector('.ft-file-counts')?.textContent).toContain('失败 1'));
    fireEvent.click(screen.getByRole('button', { name: '折叠右侧详情' }));
    const tree = within(screen.getByRole('tree', { name: '导入文件' }));
    expect(tree.getByRole('treeitem', { name: /synthetic\.zip/, description: /导入时长 1\.5 s/ })).toBeTruthy();
    expect(tree.getByRole('treeitem', { name: /b\.xlsx/, description: /导入时长 未单独计时/ })).toBeTruthy();
    const failedRow = tree.getByRole('treeitem', { name: /broken\.xlsx/, description: /数据大小 6 B，数据行数 未记录，处理摘要 文件处理失败，导入时长 23 ms/ });
    const failureStatus = within(failedRow).getByText('失败', { exact: true }).closest('[data-tone]');
    expect(failureStatus?.classList.contains('ft-tree-terminal')).toBe(true);
    expect(failureStatus?.getAttribute('data-tone')).toBe('danger');
    fireEvent.click(within(failedRow).getByRole('button', { name: /查看原因/ }));
    expect(screen.getByRole('heading', { name: 'broken.xlsx' })).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('合成损坏工作簿');
    expect(screen.getByRole('button', { name: '折叠右侧详情' })).toBeTruthy();
  });
  it('keeps one compact dialog through cancelled selection and grows only when actual content exists', async () => {
    const client = clientFor(); let finish!: (value: ImportPreview) => void;
    client.preview = vi.fn(() => new Promise<ImportPreview>(resolve => { finish = resolve; }));
    render(<ImportDialog client={client} scopeSignal={new AbortController().signal} target={{ caseId: '1', name: '合成案件' }} onImported={vi.fn()} onClose={vi.fn()} />);
    const dialog = screen.getByRole('dialog', { name: '导入流水' });
    expect(dialog.classList.contains('is-entry')).toBe(true);
    expect(document.querySelector('.ft-import-context')).toBeNull();
    expect(document.querySelector('.ft-import-footer')).toBeNull();
    expect(screen.queryByRole('list', { name: '导入步骤' })).toBeNull();
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [] } });
    await act(async () => {}); expect(client.preview).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.zip')] } });
    expect(dialog.classList.contains('is-expanded')).toBe(true);
    expect(document.querySelector('.ft-step-spinner')).toBeTruthy();
    expect(screen.getByRole('button', { name: '追加导入' }).closest('footer')).toBe(screen.getByRole('button', { name: '查看处理结果' }).closest('footer'));
    await act(async () => finish(preview));
    await screen.findByRole('status', { name: '导入完成' });
    expect(screen.getByRole('dialog', { name: '导入流水' })).toBe(dialog);
    expect(dialog.classList.contains('is-expanded')).toBe(true);
    expect(document.querySelector('.ft-step-connector[data-connector="2"] .ft-progress')?.classList.contains('is-complete')).toBe(true);
    expect(document.querySelector('.ft-step-connector[data-connector="2"] progress')?.getAttribute('value')).toBe('1');
    expect(document.querySelector('.ft-step-spinner')).toBeNull();
  });
  it('puts picker and dropped files straight into the existing tree and can remove an unstarted file', async () => {
    const client = clientFor(); let finish!: (value: ImportJob) => void;
    const job = await client.job(preview.import_id);
    client.job = vi.fn().mockImplementationOnce(() => new Promise<ImportJob>(resolve => { finish = resolve; })).mockResolvedValue(job);
    client.previewDirectory = vi.fn(async () => preview);
    mount(client);
    await waitFor(() => expect(client.job).toHaveBeenCalledOnce());
    const tree = screen.getByRole('tree'), append = screen.getByRole('button', { name: '追加导入' });
    const input = screen.getByLabelText<HTMLInputElement>('选择流水文件');
    const directory = screen.getByLabelText<HTMLInputElement>('选择整个文件夹');
    const choose = vi.spyOn(input, 'click'), chooseDirectory = vi.spyOn(directory, 'click');
    expect(append.parentElement?.querySelectorAll(':scope > button')).toHaveLength(1);
    fireEvent.keyDown(append, { key: 'ArrowDown' });
    const choices = screen.getByRole('menu', { name: '追加导入方式' });
    const chooseFiles = within(choices).getByRole('menuitem', { name: '选择文件…' });
    const chooseFolder = within(choices).getByRole('menuitem', { name: '选择文件夹…' });
    expect(document.activeElement).toBe(chooseFiles);
    fireEvent.keyDown(chooseFiles, { key: 'ArrowDown' }); expect(document.activeElement).toBe(chooseFolder);
    fireEvent.keyDown(chooseFolder, { key: 'Escape' }); expect(document.activeElement).toBe(append);
    fireEvent.click(append); fireEvent.click(screen.getByRole('menuitem', { name: '选择文件夹…' }));
    expect(chooseDirectory).toHaveBeenCalledOnce(); expect(choose).not.toHaveBeenCalled();
    fireEvent.click(append); fireEvent.click(screen.getByRole('menuitem', { name: '选择文件…' }));
    expect(choose).toHaveBeenCalledOnce();
    fireEvent.change(input, { target: { files: [new File(['synthetic'], 'queued.csv')] } });
    const queued = within(tree).getByRole('treeitem', { name: /queued.csv/ });
    expect(queued.textContent).toContain('排队中');
    fireEvent.keyDown(queued, { key: 'F10', shiftKey: true });
    fireEvent.click(screen.getByRole('menuitem', { name: '移出待导入项' }));
    expect(within(tree).queryByRole('treeitem', { name: /queued.csv/ })).toBeNull();
    const dropped = new File(['synthetic'], 'already-accepted.csv'), second = new File(['synthetic'], 'second-drop.csv');
    const dataTransfer = { types: ['Files'], files: [dropped, second], items: [], dropEffect: 'none' };
    const aside = tree.closest('aside')!, child = within(tree).getByRole('treeitem', { name: /synthetic.zip/ });
    fireEvent.dragOver(tree, { dataTransfer: { types: ['text/plain'], files: [], items: [] } });
    expect(aside.hasAttribute('data-append-drop')).toBe(false);
    fireEvent.dragEnter(tree, { dataTransfer }); fireEvent.dragEnter(child, { dataTransfer });
    fireEvent.dragLeave(tree, { dataTransfer, relatedTarget: null });
    expect(aside.getAttribute('data-append-drop')).toBe('true');
    fireEvent.dragLeave(child, { dataTransfer, relatedTarget: null });
    expect(aside.hasAttribute('data-append-drop')).toBe(false);
    fireEvent.dragOver(tree, { dataTransfer }); expect(dataTransfer.dropEffect).toBe('copy');
    fireEvent.drop(tree, { dataTransfer });
    expect(aside.hasAttribute('data-append-drop')).toBe(false);
    expect(await within(tree).findByRole('treeitem', { name: /already-accepted.csv/ })).toBeTruthy();
    expect(within(tree).getByRole('treeitem', { name: /second-drop.csv/ })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'a.csv' })).toBeTruthy();
    expect(client.preview).toHaveBeenCalledOnce();
    await act(async () => finish(job));
    await waitFor(() => expect(client.preview).toHaveBeenCalledTimes(3));
    expect(client.preview).toHaveBeenNthCalledWith(2, dropped, expect.any(AbortSignal));
    expect(client.preview).toHaveBeenLastCalledWith(second, expect.any(AbortSignal));
    expect(screen.getByRole('tree')).toBe(tree);
  });
  it('keeps context scope on the clicked member and confirms deleting only the named registered archive', async () => {
    const client = clientFor(), copy = vi.fn(async () => {}), baseline = await client.job(preview.import_id);
    const sha = 'f'.repeat(64);
    client.importSourceMetadata = vi.fn(async sourceId => ({ case_id: '1', source_id: sourceId, filename: 'synthetic.zip', size_bytes: 9,
      sha256: sha, registered_at: '2026-11-13 10:01:01', material_name: '20261113-1-1号检材', source_available: true, active_batch_id: '1', withdrawn: false }));
    client.withdrawImportSource = vi.fn(async (sourceId, batchId) => ({ case_id: '1', source_id: sourceId, batch_id: batchId }));
    client.job = vi.fn().mockResolvedValue(baseline);
    vi.stubGlobal('navigator', { clipboard: { writeText: copy } });
    try {
      mount(client); await screen.findByRole('status', { name: '导入完成' });
      const child = screen.getByRole('treeitem', { name: /b.xlsx/ });
      fireEvent.contextMenu(child, { clientX: 100, clientY: 120 });
      expect(screen.getByRole('heading', { name: 'a.csv' })).toBeTruthy();
      const menu = screen.getByRole('menu', { name: '文件操作' });
      expect(within(within(menu).getByRole('group', { name: '当前节点' })).queryByRole('menuitem', { name: /删除/ })).toBeNull();
      const originals = within(menu).getByRole('group', { name: '所属原件' });
      fireEvent.click(await within(originals).findByRole('menuitem', { name: '复制原件 SHA-256' }));
      expect(copy).toHaveBeenCalledWith(sha);
      expect(document.activeElement).toBe(child);
      fireEvent.contextMenu(child);
      fireEvent.click(await screen.findByRole('menuitem', { name: '删除所属压缩包…' }));
      const originalConfirmation = screen.getByRole('dialog', {
        name: '删除所属压缩包的导入？', description: /synthetic\.zip.*包含全部成员/,
      });
      fireEvent.click(within(originalConfirmation).getByRole('button', { name: '取消' }));
      const root = screen.getByRole('treeitem', { name: /synthetic.zip/ });
      fireEvent.keyDown(root, { key: 'F10', shiftKey: true });
      fireEvent.click(await screen.findByRole('menuitem', { name: '删除压缩包…' }));
      const confirmation = screen.getByRole('dialog', {
        name: '删除整个压缩包的导入？', description: /synthetic\.zip.*20261113-1-1号检材.*有其他来源支持的流水会保留/,
      });
      expect(document.activeElement).toBe(within(confirmation).getByRole('button', { name: '取消' }));
      expect(client.withdrawImportSource).not.toHaveBeenCalled();
      fireEvent.click(within(confirmation).getByRole('button', { name: '删除导入' }));
      await waitFor(() => expect(client.withdrawImportSource).toHaveBeenCalledWith(preview.import_id, '1', expect.any(AbortSignal)));
      await waitFor(() => expect(screen.queryByRole('treeitem', { name: /synthetic.zip/ })).toBeNull());
      expect(screen.getByText('暂无导入文件')).toBeTruthy();
      expect(screen.getByRole('button', { name: '追加导入' })).toBeTruthy();
    } finally { vi.unstubAllGlobals(); }
  });
  it('drops a folder as one import and retains its row on a failed delete', async () => {
    const client = clientFor(), file = new File(['synthetic'], '合成流水.csv'), folderId = '1234567890abcdef', batchId = '2';
    const first = await client.job(preview.import_id);
    const directoryReceipt = { ...first, import_id: folderId, result: { ...result, batch_id: batchId, source_id: folderId } };
    client.previewDirectory = vi.fn(async () => ({ ...preview, import_id: folderId, filename: '合成文件夹.zip' }));
    client.commit = vi.fn(async importId => ({ case_id: '1', import_id: importId, started: true }));
    client.job = vi.fn(async importId => importId === folderId ? directoryReceipt : first);
    client.importSourceMetadata = vi.fn(async sourceId => ({ case_id: '1', source_id: sourceId, filename: '合成文件夹.zip', size_bytes: 100,
      sha256: 'f'.repeat(64), registered_at: '2026-11-13 10:01:01', material_name: null, source_available: true, active_batch_id: batchId, withdrawn: false }));
    client.withdrawImportSource = vi.fn().mockRejectedValueOnce(new Error('合成删除失败')).mockResolvedValue({ case_id: '1', source_id: folderId, batch_id: batchId });
    mount(client); await screen.findByRole('status', { name: '导入完成' });
    const tree = screen.getByRole('tree');
    const readEntries = vi.fn().mockImplementationOnce((done: (files: object[]) => void) => done([{
      name: file.name, isFile: true, file: (loaded: (value: File) => void) => loaded(file),
    }])).mockImplementation((done: (files: object[]) => void) => done([]));
    const entry = { name: '合成文件夹', isDirectory: true, createReader: () => ({ readEntries }) };
    const dataTransfer = { types: ['Files'], files: [], items: [{ kind: 'file', getAsFile: () => null, webkitGetAsEntry: () => entry }] };
    fireEvent.dragEnter(tree, { dataTransfer });
    expect(tree.closest('aside')?.hasAttribute('data-append-drop')).toBe(true);
    fireEvent.drop(tree, { dataTransfer });
    await waitFor(() => expect(client.previewDirectory).toHaveBeenCalledOnce());
    expect(vi.mocked(client.previewDirectory).mock.calls[0][0].map(picked => picked.name)).toEqual(['合成文件夹/合成流水.csv']);
    await waitFor(() => expect(client.job).toHaveBeenCalledWith(folderId, expect.any(AbortSignal)));
    const folder = await within(tree).findByRole('treeitem', { name: /合成文件夹/ });
    fireEvent.contextMenu(folder);
    fireEvent.click(await screen.findByRole('menuitem', { name: '删除文件夹…' }));
    const confirm = screen.getByRole('dialog', {
      name: '删除整个文件夹的导入？', description: /合成文件夹.*原件、历史回执和有其他来源支持的流水会保留/,
    });
    fireEvent.click(within(confirm).getByRole('button', { name: '删除导入' }));
    expect((await within(confirm).findByRole('alert')).textContent).toContain('合成删除失败');
    expect(folder.isConnected).toBe(true);
    fireEvent.click(within(confirm).getByRole('button', { name: '删除导入' }));
    await waitFor(() => expect(within(tree).queryByRole('treeitem', { name: /合成文件夹/ })).toBeNull());
    expect(within(tree).getByRole('treeitem', { name: /synthetic.zip/ })).toBeTruthy();
    expect(client.withdrawImportSource).toHaveBeenLastCalledWith(folderId, batchId, expect.any(AbortSignal));
  });
  it('filters complete names through ancestors without changing totals or the inspected file and restores folds when cleared', async () => {
    const client = clientFor(); mount(client); await screen.findByRole('status', { name: '导入完成' });
    fireEvent.click(screen.getByRole('treeitem', { name: /b.xlsx/ }));
    const heading = screen.getByRole('heading', { name: 'b.xlsx' });
    fireEvent.click(screen.getByRole('button', { name: '收起folder' }));
    expect(screen.queryByRole('treeitem', { name: /b.xlsx/ })).toBeNull();
    expect(document.querySelector('[data-selected-descendant="true"]')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBe(heading);
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索导入文件' }), { target: { value: 'b.xlsx' } });
    expect(screen.getByRole('treeitem', { name: /b.xlsx/ })).toBeTruthy();
    expect(screen.queryByRole('treeitem', { name: /a.csv/ })).toBeNull();
    expect(document.querySelector('.ft-file-counts')?.textContent).toBe('完成 2');
    expect(document.querySelector('.ft-overall')).toBeNull();
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索导入文件' }), { target: { value: '' } });
    expect(screen.queryByRole('treeitem', { name: /b.xlsx/ })).toBeNull();
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBe(heading);
    expect(client.preview).toHaveBeenCalledOnce(); expect(client.commit).toHaveBeenCalledOnce();
  });
  it('keeps header metrics under mapping and hides file properties behind a keyboard menu', async () => {
    mount(clientFor(), undefined, undefined, { downloadSource: vi.fn() });
    await screen.findByRole('status', { name: '导入完成' });
    expect(document.querySelector('.ft-file-outcomes')?.textContent).toBe('已导入 1 行重复 0 行待核对 0 行');
    expect(document.querySelector('.ft-mapping-section .ft-header-detection')?.textContent).toContain('表头第 1 行');
    expect(screen.queryByRole('dialog', { name: '文件信息' })).toBeNull();
    const menu = screen.getByRole('button', { name: '文件操作' }); fireEvent.click(menu);
    expect(screen.getByRole('menuitem', { name: '另存所属压缩包…' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: '文件信息' }));
    const info = screen.getByRole('dialog', { name: '文件信息' });
    expect(within(info).getByText('文件大小').nextSibling?.textContent).toBe('100 B');
    expect(within(info).getByText('处理用时（本次）').nextSibling?.textContent).toBe('—');
    expect(within(info).getByText('原始行数').nextSibling?.textContent).toBe('—');
    fireEvent.keyDown(info, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: '文件信息' })).toBeNull();
    expect(document.activeElement).toBe(menu); expect(screen.getByRole('dialog', { name: '导入流水' })).toBeTruthy();
  });
  it('keeps the reference inspector free of receipt cards and opens details only through the existing action', async () => {
    const client = clientFor(); mount(client); await screen.findByRole('status', { name: '导入完成' });
    expect(document.querySelector('.ft-result')).toBeNull();
    expect(document.querySelector('.ft-overall .ft-counts')).toBeNull();
    const fileList = screen.getByRole('tree', { name: '导入文件' }).closest('aside')!;
    expect(within(fileList).getByRole('heading', { name: '导入文件' })).toBeTruthy();
    expect(document.querySelector('.ft-file-counts')?.textContent).toBe('完成 2');
    expect((document.querySelector('.ft-result-data') as HTMLElement).hidden).toBe(true);
    expect((document.querySelector('.ft-full-summary') as HTMLElement).hidden).toBe(true);
    expect(document.querySelector('.ft-import-footer > span')).toBeNull();
    expect(document.querySelector('.ft-mapping-section .ft-section-heading')?.textContent).toMatch(/^字段映射表头第 1 行/);
    expect(document.querySelector('.ft-file-title-copy .ft-file-outcomes')).toBeTruthy();
    const mapping = screen.getByRole('region', {name:'字段映射，可上下左右滚动'});
    expect(within(mapping).queryAllByRole('separator')).toHaveLength(0);
    expect(within(mapping).getByText('已识别').classList.contains('ft-sr-only')).toBe(false);
    expect(within(mapping).getByText('未映射').classList.contains('ft-sr-only')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    expect((document.querySelector('.ft-result-data') as HTMLElement).hidden).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '返回数据预览' }));
    expect((document.querySelector('.ft-result-data') as HTMLElement).hidden).toBe(true);
    expect(screen.getByRole('region', { name: '字段映射，可上下左右滚动' })).toBeTruthy();
    expect(client.commit).toHaveBeenCalledOnce();
  });
  it('copies the physical workbook filename and reports whole-file metadata while inspecting a sheet', async () => {
    const documents = [1,2].map(n=>({member_path:`worksheets/000${n}`,sheet:n===1?'流水明细':'账户资料',header_row:1,columns:['金额'],source_rows:2,row_count:1}));
    const bookSummary = {...summary, record_total:2,accepted:2,unresolved:0,documents,unresolved_examples:[],mapping_evidence:[],members:documents.map(d=>({member_path:d.member_path,role:'table',row_count:1,size_bytes:100,encoding:'xlsx',sha256:'a'.repeat(64)}))};
    const book = {...preview,filename:'synthetic-book.xlsx',table_summary:bookSummary}, client=clientFor(), copy=vi.fn(async()=>{});
    client.preview=vi.fn(async()=>book); client.job=vi.fn(async(): Promise<ImportJob>=>({case_id:'1',import_id:preview.import_id,stage:'done',done:2,total:2,message:'完成',error:null,result:{...result,ok:2,skipped:0,table_summary:bookSummary},member_results:documents.map(d=>({member_path:d.member_path,dispositions:{imported:1}}))}));
    vi.stubGlobal('navigator',{clipboard:{writeText:copy}});
    try {
      render(<ImportDialog client={client} scopeSignal={new AbortController().signal} target={{caseId:'1',name:'合成案件'}} onImported={vi.fn()} onClose={vi.fn()} />);
      fireEvent.change(screen.getByLabelText('选择流水文件'),{target:{files:[new File(['synthetic'],'synthetic-book.xlsx')]}});
      await screen.findByRole('status',{name:'导入完成'}); expect(screen.getByRole('heading',{name:'流水明细'})).toBeTruthy();
      fireEvent.click(screen.getByRole('button',{name:'文件操作'})); fireEvent.click(screen.getByRole('menuitem',{name:'复制原件文件名'})); await waitFor(()=>expect(copy).toHaveBeenCalledWith('synthetic-book.xlsx'));
      fireEvent.click(screen.getByRole('button',{name:'文件操作'})); fireEvent.click(screen.getByRole('menuitem',{name:'文件信息'}));
      const info=screen.getByRole('dialog',{name:'文件信息'}); expect(within(info).getByText('整份文件')).toBeTruthy();
      expect(within(info).getByText('文件大小').nextSibling?.textContent).toBe('9 B'); expect(within(info).getByText('原始行数').nextSibling?.textContent).toBe('4');
    } finally { vi.unstubAllGlobals(); }
  });
  it('opens the exact original issue row without guessing its page and can return to the file records', async () => {
    const client = clientFor();
    client.importRecords = vi.fn(async (_id, _page, _signal, memberPath, sourceRow): Promise<ImportRecordPage> => ({ case_id: '1', source_id: preview.import_id, page: 1, page_size: 50, total: 1,
      items: [{ member_path: memberPath!, source_row: sourceRow ?? 2, record_no: 999, record_key: 'SYN-FOCUS', disposition: 'unresolved', reason_code: 'invalid_time', flow_id: null,
        details: { source_field_evidence: JSON.stringify([{ role: 'amount', column: '原金额', column_index: 1, raw: '000001.2300' }]) } }] }));
    mount(client); await screen.findByRole('status', { name: '导入完成' });
    fireEvent.click(screen.getByRole('treeitem', { name: /b.xlsx/ }));
    fireEvent.click(screen.getByRole('link', { name: '查看来源行' }));
    await waitFor(() => expect(client.importRecords).toHaveBeenCalledWith(preview.import_id, 1, expect.any(AbortSignal), 'folder/b.xlsx', 128));
    await screen.findByText('已定位原始第 128 行');
    expect(document.querySelector('.ft-record-scroll article[data-focused="true"] details')?.hasAttribute('open')).toBe(true);
    expect(screen.getByText('"000001.2300"')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '查看该文件全部来源记录' }));
    await waitFor(() => expect(client.importRecords).toHaveBeenLastCalledWith(preview.import_id, 1, expect.any(AbortSignal), 'folder/b.xlsx'));
  });
  it('keeps the overall workflow active when the selected file finishes before the next upload', async () => {
    const client = clientFor(); let reads = 0;
    client.preview = vi.fn(async () => ++reads === 1 ? preview : new Promise<ImportPreview>(() => {}));
    render(<ImportDialog client={client} scopeSignal={new AbortController().signal} target={{ caseId: '1', name: '合成案件' }} onImported={vi.fn()} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.zip'), new File(['synthetic'], 'next.csv')] } });
    await waitFor(() => expect(client.preview).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('button', { name: '关闭进度' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^完成$/ })).toBeNull();
    expect(document.querySelector('.ft-step-connector[data-connector="2"] .ft-progress')?.classList.contains('is-active')).toBe(true);
    expect(document.querySelector('.ft-step-connector[data-connector="2"] progress')?.getAttribute('value')).toBe(String(2 / 3));
    expect(document.querySelector('.ft-overall')).toBeNull();
    expect(document.querySelector('[aria-current="step"]')?.textContent).toContain('识别与导入');
  });
  it('keeps file completion separate from the root record progress without inventing member percentages', async () => {
    const client = clientFor(), controller = new AbortController();
    client.job = vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: preview.import_id, stage: 'importing', done: 5, total: 10, result: null, error: null, message: '写入中' }));
    render(<ImportDialog client={client} scopeSignal={controller.signal} target={{ caseId: '1', name: '合成案件' }} onImported={vi.fn()} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.zip'), new File(['synthetic'], 'next.csv')] } });
    await waitFor(() => expect(screen.getByRole('progressbar', { name: 'synthetic.zip处理进度' }).getAttribute('value')).toBe('0.5'));
    expect(screen.getByRole('progressbar', { name: '导入处理进度' }).getAttribute('value')).toBe('0');
    expect(document.querySelector('.ft-step-spinner')?.getAttribute('style')).toContain('import-loader.svg');
    expect(screen.getByRole('progressbar', { name: 'synthetic.zip处理进度' }).getAttribute('value')).toBe('0.5');
    expect(screen.getByRole('progressbar', { name: 'a.csv处理进度' }).hasAttribute('value')).toBe(false);
    act(() => controller.abort());
  });
  it('collapses a parent while preserving its true descendant count and selects the correct file header', async () => {
    mount(clientFor()); await screen.findByRole('status', { name: '导入完成' });
    const tree = screen.getByRole('tree', { name: '导入文件' });
    expect(within(tree).getAllByRole('treeitem')).toHaveLength(4);
    fireEvent.click(within(tree).getByRole('button', { name: '收起folder' }));
    expect(within(tree).getAllByRole('treeitem')).toHaveLength(2);
    const folder = within(tree).getByRole('treeitem', { name: /folder/ });
    expect(within(folder).getByText(/^2 个文件(?: ·.*)?$/)).toBeTruthy();
    fireEvent.click(within(tree).getByRole('button', { name: '展开folder' }));
    fireEvent.click(within(tree).getByRole('treeitem', { name: /b.xlsx/ }));
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBeTruthy();
    expect(within(document.querySelector('.ft-header-detection') as HTMLElement).getByText('表头第 3 行')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: '字段映射，可上下左右滚动' })).getByText('成交日期')).toBeTruthy();
    expect(document.querySelector('.ft-issues')?.textContent).toContain('原始第 128 行');
    fireEvent.click(within(tree).getByRole('treeitem', { name: /folder/ }));
    expect(screen.getByRole('region', { name: '字段映射，可上下左右滚动' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBeTruthy();
    expect(within(folder).getByText(/^2 个文件/)).toBeTruthy();
  });
  it('reads the selected file page, keeps exact values, and aborts that read when the binding ends', async () => {
    const client = clientFor(); let finish!: (value: ImportRecordPage) => void;
    const records = vi.fn<NonNullable<FundsCaseClientV1['importRecords']>>(() => new Promise<ImportRecordPage>(resolve => { finish = resolve; }));
    client.importRecords = records;
    const { controller } = mount(client); await screen.findByRole('status', { name: '导入完成' });
    fireEvent.click(screen.getByRole('treeitem', { name: /b.xlsx/ }));
    await waitFor(() => expect(client.importRecords).toHaveBeenLastCalledWith(preview.import_id, 1, expect.any(AbortSignal), 'folder/b.xlsx', 128));
    const signal = records.mock.lastCall![2]!;
    act(() => controller.abort()); expect(signal.aborted).toBe(true);
    await act(async () => finish({ case_id: '1', source_id: preview.import_id, total: 1, page: 1, page_size: 50,
      items: [{ record_no: 1, source_row: 128, member_path: 'folder/b.xlsx', record_key: 'synthetic', reason_code: 'invalid_time', disposition: 'unresolved', details: { source_field_evidence: '[]' }, flow_id: null }] }));
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('keeps both mapping and financial preview keyboard-scrollable and escapes untrusted source labels', async () => {
    const client = clientFor(), label = '<img src=x onerror=alert(1)>';
    client.preview = vi.fn(async () => ({ ...preview, filename: 'safe.csv', automatic: false, table_summary: null, columns: [label], mapping: { amount: label }, preview: [{
      trade_time: '2026-01-01', trade_no: '0001', payer_name: label, payer_account: '00001', payer_bank: '', payee_name: '合成乙', payee_account: '00002', payee_bank: '',
      amount: '9999999999999999.99', balance: null, currency: 'CNY', summary: '', channel: '', self_account: '00001', self_name: '' }] }));
    mount(client); await screen.findByRole('button', { name: '确认导入' });
    expect(screen.getByRole('region', { name: '字段映射，可上下左右滚动' }).tabIndex).toBe(0);
    expect(screen.getByRole('region', { name: '数据预览，可上下左右滚动' }).tabIndex).toBe(0);
    expect(screen.getByText('9,999,999,999,999,999.99')).toBeTruthy(); expect(within(screen.getByRole('region', { name: '数据预览，可上下左右滚动' })).getAllByText('00001')).toHaveLength(2);
    expect(document.querySelector('[onerror]')).toBeNull(); expect(screen.getAllByText(label).length).toBeGreaterThan(0);
  });
  it.each(['file', 'unknown'])('does not invent totals or active enumeration after a %s rejection', async kind => {
    const client = clientFor(); client.preview = vi.fn(async () => { throw Object.assign(new Error('合成压缩包损坏'), { status: 400, failureKind: kind }); });
    mount(client); await screen.findByRole('alert');
    expect(screen.getByRole('treeitem', { name: /synthetic.zip/ }).textContent).toContain(kind === 'file' ? '失败' : '结果待核对');
    expect(screen.queryByText(/正在识别文件结构/)).toBeNull();
    expect(document.querySelector('.ft-step-connector[data-connector="2"] progress')?.hasAttribute('value')).toBe(false);
    expect(document.querySelector('.ft-step-spinner')).toBeNull();
    expect(client.commit).not.toHaveBeenCalled();
  });
});

it('counts only displayed header columns when a later region uses different aliases', async () => {
  const client = clientFor(), multi = { ...summary, mapping_evidence: [
    { member_path: 'folder/a.csv', field: 'trade_time', column: '交易时间', header_row: '1' },
    { member_path: 'folder/a.csv', field: 'amount', column: '金额', header_row: '1' },
    { member_path: 'folder/a.csv', field: 'trade_time', column: '成交日期', header_row: '128' },
    { member_path: 'folder/a.csv', field: 'amount', column: '原金额', header_row: '128' },
  ] };
  client.preview = vi.fn(async () => ({ ...preview, table_summary: multi }));
  const oldJob = client.job; client.job = vi.fn(async id => ({ ...await oldJob(id), result: { ...result, table_summary: multi } }));
  mount(client); await screen.findByRole('status', { name: '导入完成' });
  expect(document.querySelector('.ft-header-detection')?.textContent).toContain('已识别 2 / 2 个字段');
  expect(within(screen.getByRole('region', { name: '字段映射，可上下左右滚动' })).getAllByRole('row')).toHaveLength(3);
});

it('stops unstarted files when the case surface is hidden while letting the submitted file settle', async () => {
  const client = clientFor(), scope = new AbortController(), imported = vi.fn();
  let finish!: (value: Awaited<ReturnType<FundsCaseClientV1['commit']>>) => void;
  client.commit = vi.fn(() => new Promise<Awaited<ReturnType<FundsCaseClientV1['commit']>>>(resolve => { finish = resolve; }));
  const props = { client, scopeSignal: scope.signal, target: { caseId: '1', name: '合成案件' }, onImported: imported, onClose: vi.fn() };
  const view = render(<ImportDialog {...props} />);
  fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'first.zip'), new File(['synthetic'], 'second.csv')] } });
  await waitFor(() => expect(client.commit).toHaveBeenCalledOnce());
  view.rerender(<ImportDialog {...props} active={false} />); expect(screen.queryByRole('dialog')).toBeNull();
  await act(async () => finish({ case_id: '1', import_id: preview.import_id, started: true }));
  await waitFor(() => expect(imported).toHaveBeenCalledOnce());
  expect(client.preview).toHaveBeenCalledOnce(); expect(client.commit).toHaveBeenCalledOnce();
  view.rerender(<ImportDialog {...props} />);
  expect(screen.getByText('跳过 · 未上传')).toBeTruthy();
});

it('prevents a later file drop from navigating away or silently starting another import', async () => {
  const client = clientFor(); mount(client); await screen.findByRole('status', { name: '导入完成' });
  const drop = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(drop, 'dataTransfer', { value: { types: ['Files'], files: [new File(['synthetic'], 'later.csv')], items: [] } });
  fireEvent(screen.getByRole('dialog'), drop);
  expect(drop.defaultPrevented).toBe(true); expect(client.preview).toHaveBeenCalledOnce(); expect(client.commit).toHaveBeenCalledOnce();
});
