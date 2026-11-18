import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportJob, ImportPreview, ImportRecordPage } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
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
function mount(client: FundsCaseClientV1, controller = new AbortController(), files = [new File(['synthetic'], 'synthetic.zip')]) {
  const view = render(<ImportDialog client={client} scopeSignal={controller.signal} target={{ caseId: '1', name: '合成案件' }} onImported={vi.fn()} onClose={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files } });
  return { view, controller };
}
describe('the common import workspace', () => {
  it('keeps return-to-preview enabled after selecting a failed file in report mode', async () => {
    const client = clientFor();
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    client.preview = vi.fn().mockResolvedValueOnce(preview).mockRejectedValueOnce(Object.assign(new Error('合成损坏工作簿'), { status: 400, failureKind: 'file' }));
    mount(client, new AbortController(), [new File(['synthetic'], 'synthetic.zip'), new File(['broken'], 'broken.xlsx')]);
    await waitFor(() => expect(document.querySelector('.ft-file-counts')?.textContent).toContain('失败 1'));
    fireEvent.click(screen.getByRole('treeitem', { name: /a.csv/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    fireEvent.click(screen.getByRole('treeitem', { name: /broken.xlsx/ }));
    const back = screen.getByRole('button', { name: '返回数据预览' });
    expect((back as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(back);
    expect(screen.getByRole('heading', { name: 'broken.xlsx' })).toBeTruthy();
    expect(document.querySelector('.ft-file-outcomes')?.textContent).toBe('导入失败');
    expect(screen.getByText('合成损坏工作簿')).toBeTruthy();
  });
  it('switches only the right view and keeps report mode while inspecting another member', async () => {
    const client = clientFor();
    client.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>(() => new Promise(() => {}));
    mount(client); await screen.findByRole('status', { name: '导入完成' });
    const tree = screen.getByRole('tree'), aside = tree.closest('aside');
    tree.scrollTop = 73;
    fireEvent.click(screen.getByRole('button', { name: '查看处理结果' }));
    expect(screen.getByRole('navigation', { name: '报告目录' })).toBeTruthy();
    expect(screen.getByRole('tree')).toBe(tree);
    expect(tree.closest('aside')).toBe(aside);
    expect(tree.scrollTop).toBe(73);
    fireEvent.click(screen.getByRole('treeitem', { name: /b.xlsx/ }));
    expect(screen.getByRole('navigation', { name: '报告目录' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '返回数据预览' })).toBeTruthy();
    await waitFor(() => expect(client.importFundsReport).toHaveBeenLastCalledWith(preview.import_id, { memberPath: 'folder/b.xlsx', page: 1 }, expect.any(AbortSignal)));
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索导入文件' }), { target: { value: 'b.xlsx' } });
    fireEvent.click(screen.getByRole('button', { name: '返回数据预览' }));
    expect(screen.queryByRole('navigation', { name: '报告目录' })).toBeNull();
    expect(screen.getByRole('searchbox', { name: '搜索导入文件' }).getAttribute('value')).toBe('b.xlsx');
    expect(screen.getByRole('tree')).toBe(tree);
    expect(screen.getByRole('heading', { name: 'b.xlsx' })).toBeTruthy();
    expect(client.commit).toHaveBeenCalledOnce();
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
    expect(client.importReview).toHaveBeenLastCalledWith(preview.import_id,'folder/b.xlsx',{view:'all',page:1,issueIndex:1},expect.any(AbortSignal));
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
    expect(dialog.classList.contains('is-entry')).toBe(true);
    expect(document.querySelector('.ft-entry-progress .is-spinning')).toBeTruthy();
    await act(async () => finish(preview));
    await screen.findByRole('status', { name: '导入完成' });
    expect(screen.getByRole('dialog', { name: '导入流水' })).toBe(dialog);
    expect(dialog.classList.contains('is-expanded')).toBe(true);
    expect(document.querySelector('.ft-step-connector[data-connector="2"] .ft-progress')?.classList.contains('is-complete')).toBe(true);
    expect(document.querySelector('.ft-step-connector[data-connector="2"] progress')?.getAttribute('value')).toBe('1');
    expect(document.querySelector('.ft-step-spinner')).toBeNull();
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
    mount(clientFor()); await screen.findByRole('status', { name: '导入完成' });
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
    expect(document.querySelector('.ft-import-files > .ft-section-heading > h3')?.textContent).toBe('导入文件');
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
      fireEvent.click(screen.getByRole('button',{name:'文件操作'})); fireEvent.click(screen.getByRole('menuitem',{name:'复制文件名'})); await waitFor(()=>expect(copy).toHaveBeenCalledWith('synthetic-book.xlsx'));
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
    await waitFor(() => expect(client.importRecords).toHaveBeenCalledWith(preview.import_id, 1, expect.any(AbortSignal), 'folder/b.xlsx'));
    const signal = records.mock.calls[0][2]!;
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
    expect(document.querySelector('.ft-counts')).toBeNull();
    expect(document.querySelector('.ft-import-dialog')?.classList.contains('is-entry')).toBe(true);
    expect(screen.queryByText(/正在识别文件结构/)).toBeNull();
    expect(document.querySelector('.ft-entry-progress progress')?.hasAttribute('value')).toBe(false);
    expect(document.querySelector('.ft-entry-progress .is-spinning')).toBeNull();
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
