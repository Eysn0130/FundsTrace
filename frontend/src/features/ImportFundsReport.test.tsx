import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ImportReport } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { ImportFundsReport, reportAccountNarrative, reportNarrative, reportNarrativeDetails, reportPeriod } from './ImportFundsReport';
import { ImportReportTrend } from './ImportReportTrend';
import { ImportTrendFilter } from './ImportTrendFilter';

function report(overrides: Partial<ImportReport> = {}): ImportReport {
  return { case_id: '9007199254740993', source_id: 'synthetic-source', member_path: 'folder/one.csv', analysis_revision: 'revision-7', policy: 'import-member-funds-report-v1', generated_at: '2026-11-14T09:00:00Z',
    scope: { filename: 'synthetic.zip', member_path: 'folder/one.csv', member_role: 'table', source_sha256: 'a'.repeat(64), source_size_bytes: 1000, member_sha256: 'b'.repeat(64), member_size_bytes: 500, member_digest_kind: 'bytes', parser_version: 'synthetic', imported_at: '2026-11-13T09:00:00Z' },
    quality: { source_records: 120, dispositions: { imported: 119, unresolved: 1 }, linked_observations: 119, selected_transactions: 119, pending_observations: 119, unattributed_transactions: 0 }, perspective: 'own_ledger', currency: 'CNY', currencies: ['CNY'], account: null,
    summary: { in_count: 60, out_count: 59, in_amount: '9999999999999999.99', out_amount: '0.01', net_amount: '9999999999999999.98', transaction_count: 119, amount: '10000000000000000.00' },
    accounts: { items: [{ account: 'entity-A', account_number: '000SYN-A', account_numbers: ['000SYN-A'], names: ['合成主体'], cards: [], currency: 'CNY', in_count: 60, out_count: 59, in_amount: '9999999999999999.99', out_amount: '0.01', net_amount: '9999999999999999.98' }], total: 1, page: 1, page_size: 20 },
    top_in: [{ account: 'entity-B', account_number: '000SYN-B', names: ['合成对手'], count: 60, amount: '9999999999999999.99' }], top_out: [], timeline: [], timeline_grain: '日', periods: [], relations: { items: [], total: 0, page: 1, page_size: 20 },
    integrity: { status: 'registered', object_name: 'synthetic.zip', expected_sha256: 'a'.repeat(64), observed_sha256: null, checked_at: null, size_bytes: 1000 }, notes: ['同币种、按当前修订统计，来源重复不重复计资。'], ...overrides };
}
function client(data = report()) {
  return { binding: { caseId: data.case_id, scopeKey: 'report-synthetic', generation: 1 }, importFundsReport: vi.fn(async () => data) } as unknown as FundsCaseClientV1;
}
function props(value: FundsCaseClientV1, signal = new AbortController().signal) {
  return { client: value, scopeSignal: signal, sourceId: 'synthetic-source', memberPath: 'folder/one.csv', header: <h3>one.csv</h3>, chapter: 'integrity' as const, onChapter: vi.fn() };
}
afterEach(() => vi.restoreAllMocks());

describe('file funds report', () => {
  it('uses whole-scope overview before paged account detail and avoids repeating a single holder', () => {
    const data = report({ overview: { name_count: 25, account_count: 26, number_count: 28, unnamed_account_count: 1, selected_account: null } });
    const second = { ...data.accounts.items[0], account: 'entity-C', account_number: '000SYN-C', account_numbers: ['000SYN-C'], names: ['合成第二户名'], in_amount: '0.01' };
    data.accounts = { ...data.accounts, total: 26, items: [...data.accounts.items, second] };
    expect(reportNarrative(data)).toContain('本表记载户名25个，涉及本方账户26个、卡／账号标识28个');
    expect(reportNarrative(data)).toContain('9,999,999,999,999,999.99');
    expect(reportNarrativeDetails(data)).toHaveLength(2);
    expect(reportNarrativeDetails(data)[1]).toContain('合成第二户名（000SYN-C）');
    data.overview = { ...data.overview!, name_count: 1, unnamed_account_count: 0 };
    expect(reportNarrativeDetails(data)).toEqual([]);
    const single = report({ overview: { name_count: 1, account_count: 1, number_count: 2, unnamed_account_count: 0, selected_account: null } });
    expect(reportNarrative(single)).toContain('记载户名“合成主体”，涉及卡／账号标识2个');
    expect(reportNarrative(single).match(/进账60笔/g)).toHaveLength(1);
    expect(reportNarrativeDetails(single)).toEqual([]);
    single.account = second.account; single.overview!.selected_account = second;
    expect(reportNarrative(single)).toContain('记载户名“合成第二户名”');
    expect(reportNarrative(single)).not.toContain('记载户名“合成主体”');
    single.overview!.name_count = 25;
    expect(reportNarrative(single)).toContain('记载户名25个（含“合成第二户名”等）');
  });
  it('renders and copies account detail as one continuous paragraph with a single introduction', async () => {
    const data=report({overview:{name_count:2,account_count:2,number_count:2,unnamed_account_count:0,selected_account:null}});
    data.accounts={...data.accounts,total:2,items:[...data.accounts.items,{...data.accounts.items[0],account:'entity-C',account_number:'000SYN-C',account_numbers:['000SYN-C'],names:['合成第二户名'],in_count:0,in_amount:'0.00',out_count:1,out_amount:'0.01',net_amount:'-0.01'}]};
    const detail=reportAccountNarrative(data),copy=vi.fn().mockResolvedValue(undefined);
    expect(detail.match(/其中/g)).toHaveLength(1);
    expect(detail).toContain('；合成第二户名（000SYN-C）未见进账记录，出账1笔计0.01元。');
    expect(detail).toContain('9,999,999,999,999,999.99');
    expect(detail).not.toContain('收支差额'); expect(detail).not.toContain('（CNY）'); expect(detail).not.toContain('\n');
    vi.stubGlobal('navigator',{clipboard:{writeText:copy}});
    try {
      render(<ImportFundsReport {...props(client(data))}/>);
      await screen.findByRole('region',{name:'账户资金收支，可上下左右滚动'});
      expect(document.querySelectorAll('.ft-report-narrative')).toHaveLength(2);
      expect(document.querySelector('.ft-report-account-narrative')?.textContent).toBe(detail);
      fireEvent.click(screen.getByRole('button',{name:'复制统计说明'}));
      await screen.findByRole('button',{name:'已复制'});
      expect(copy.mock.calls[0][0]).toContain(`${reportNarrative(data)}\n${detail}\n\n${data.notes[0]}`);
      expect(copy.mock.calls[0][0]).not.toContain(data.analysis_revision);
    } finally {vi.unstubAllGlobals();}
  });
  it('keeps missing names and unknown money explicit without turning them into zero amounts', () => {
    const data=report({overview:{name_count:2,account_count:3,number_count:3,unnamed_account_count:1,selected_account:null}});
    data.accounts.items=[{...data.accounts.items[0],names:[],in_count:1,in_amount:null,out_count:0,out_amount:'0.00',net_amount:null}];
    data.summary={...data.summary,in_count:1,in_amount:null,out_count:0,out_amount:'0.00',net_amount:null};
    expect(reportAccountNarrative(data)).toContain('户名未载明账户（000SYN-A）进账1笔，金额未明确，未见出账记录');
    expect(reportAccountNarrative(data)).not.toContain('0.00');
    expect(reportNarrative(data)).toContain('收支差额未明确');
    expect(reportNarrative(data)).toContain('所涉账户中有1个未载明户名');
    data.accounts.items[0].names=['合成名称一','合成名称二'];
    expect(reportAccountNarrative(data)).toContain('以合成名称一等名称记载的账户');
  });
  it.each([
    ['CNY', '元', '125.00元'],
    ['JPY', 'JPY', '125.00 JPY'],
    ['', '币种未明确', '125.00'],
  ])('uses the report currency %s in its narrative, tables, ranking and copy', async (currency, unit, amount) => {
    const data = report({
      currency, currencies: [currency],
      overview: { name_count: 2, account_count: 2, number_count: 2, unnamed_account_count: 0, selected_account: null },
    });
    const account = { ...data.accounts.items[0], currency, in_count: 1, out_count: 1, in_amount: '125.00', out_amount: null, net_amount: null };
    data.accounts = { ...data.accounts, total: 2, items: [account] };
    data.summary = { ...data.summary, in_count: 1, out_count: 1, in_amount: '125.00', out_amount: null, net_amount: null };
    data.top_in[0] = { ...data.top_in[0], count: 1, amount: '125.00' };
    const copy = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText: copy } });
    try {
      render(<ImportFundsReport {...props(client(data))} />);
      await screen.findByRole('region', { name: '账户资金收支，可上下左右滚动' });
      expect(document.querySelector('.ft-report-narrative')?.textContent).toContain(`合计${amount}；出账1笔，金额未明确，收支差额未明确`);
      expect(document.querySelector('.ft-report-account-narrative')?.textContent).toContain(`进账1笔计${amount}，出账1笔，金额未明确`);
      for (const name of ['账户资金收支，可上下左右滚动', '账户往来关联，可上下左右滚动']) {
        expect(within(screen.getByRole('region', { name })).getByRole('columnheader', { name: label => label.startsWith(`进账金额（${unit}）`) })).toBeTruthy();
      }
      expect(screen.getByRole('button', { name: `进账来源第1，合成对手，000SYN-B，${amount}，1笔` })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: '复制统计说明' }));
      await screen.findByRole('button', { name: '已复制' });
      expect(copy.mock.calls[0][0]).toContain(`合计${amount}；出账1笔，金额未明确`);
    } finally { vi.unstubAllGlobals(); }
  });
  it('integrates readable methods without a duplicate trace panel and copies the same paragraphs', async () => {
    const paragraphs=[{title:'资料范围',text:'所选资料登记120条来源记录，当前范围纳入119笔交易。'},{title:'归集方法',text:'同笔交易的重复来源不重复计数。'},{title:'结果说明',text:'收支差额不等同账户余额。'},{title:'留存复核',text:'文件指纹复核不替代交易真实性核实。'}];
    const data=report({method_paragraphs:paragraphs,notes:['仅在追溯记录保留的原始技术规则']}),copy=vi.fn().mockResolvedValue(undefined);
    data.scope.parser_version='synthetic-parser-v7';
    vi.stubGlobal('navigator',{clipboard:{writeText:copy}});
    try {
      render(<ImportFundsReport {...props(client(data))}/>);
      const method=(await screen.findByRole('heading',{name:'六、统计口径与来源'})).closest('section')!;
      expect([...method.querySelectorAll('.ft-method-paragraph')].map(p=>p.textContent)).toEqual(paragraphs.map(p=>`${p.title}。${p.text}`));
      expect(method.querySelector('details')).toBeNull();
      expect(screen.queryByText('查看来源与处理记录')).toBeNull();
      expect(screen.queryByText('完整统计规则')).toBeNull();
      for (const technical of [data.policy,data.analysis_revision,data.scope.parser_version!,data.notes[0]]) expect(document.body.textContent).not.toContain(technical);
      const material=screen.getByRole('region',{name:'检材资料，可上下左右滚动'});
      expect(material.textContent).toContain(data.scope.filename);
      expect(material.textContent).toContain(data.integrity.expected_sha256!);
      expect(within(material).getByRole('button',{name:'查看完整SHA-256指纹'})).toBeTruthy();
      fireEvent.click(screen.getByRole('button',{name:'复制统计说明'}));
      await screen.findByRole('button',{name:'已复制'});
      expect(copy.mock.calls[0][0]).toContain(paragraphs.map(p=>`${p.title}。${p.text}`).join('\n\n'));
      expect(copy.mock.calls[0][0]).not.toContain(data.notes[0]);
      expect(copy.mock.calls[0][0]).not.toContain(data.analysis_revision);
    } finally {vi.unstubAllGlobals();}
  });
  it('filters only peer rankings and uses the selected own account for source drilldown', async () => {
    const data = report(), value = client(data);
    value.importFundsReport = vi.fn(async (_source, query) => ({ ...data, account: query.account ?? null, peer_account: query.peerAccount ?? null, peer_selected_account: query.peerAccount ? data.accounts.items[0] : null }));
    value.importReportRecords = vi.fn(async (_source, query) => ({ case_id: value.binding.caseId, source_id: 'synthetic-source', analysis_revision: query.expectedRevision, items: [], total: 0, page: 1, page_size: 50 }));
    render(<ImportFundsReport {...props(value)}/>);
    fireEvent.click(await screen.findByRole('button', { name: '主要往来本方账户：全部' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /合成主体.*000SYN-A/ }));
    await screen.findByRole('button', { name: '主要往来本方账户：000SYN-A' });
    expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', { memberPath: 'folder/one.csv', page: 1, expectedRevision: 'revision-7', peerAccount: 'entity-A' }, expect.any(AbortSignal));
    expect(screen.queryByText(/按对手账号归集/)).toBeNull();
    expect(screen.getByText(/本方账户进账60笔/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /进账来源第1/ }));
    await waitFor(() => expect(value.importReportRecords).toHaveBeenCalledWith('synthetic-source', expect.objectContaining({ account: 'entity-A', counterparty: 'entity-B', direction: 'in' }), expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: '关闭统计来源明细' }));
    fireEvent.click(screen.getByRole('button', { name: '000SYN-A' }));
    await waitFor(() => expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', expect.objectContaining({ account: 'entity-A' }), expect.any(AbortSignal)));
    fireEvent.click(await screen.findByRole('button', { name: '主要往来本方账户：000SYN-A' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: '全部' }));
    await screen.findByRole('button', { name: '主要往来本方账户：全部' });
    fireEvent.click(screen.getByRole('button', { name: /进账来源第1/ }));
    await waitFor(() => expect(value.importReportRecords).toHaveBeenLastCalledWith('synthetic-source', expect.objectContaining({ account: undefined, counterparty: 'entity-B', direction: 'in' }), expect.any(AbortSignal)));
  });
  it('pages bounded peer filter options without changing the account table or whole-scope query', async () => {
    const data = report({ accounts: { ...report().accounts, total: 21 } }), value = client(data);
    const last = { ...data.accounts.items[0], account: 'last-account', account_number: '000SYN-LAST', names: ['合成末页户名'] };
    value.importFundsReport = vi.fn(async (_source, query) => query.accountsPage === 2 ? { ...data, accounts: { ...data.accounts, page: 2, items: [last] } } : data);
    render(<ImportFundsReport {...props(value)}/>);
    fireEvent.click(await screen.findByRole('button', { name: '主要往来本方账户：全部' }));
    fireEvent.click(screen.getByRole('button', { name: '下一页账户选项' }));
    await screen.findByRole('menuitemradio', { name: /合成末页户名/ });
    expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', { memberPath: 'folder/one.csv', currency: 'CNY', expectedRevision: 'revision-7', accountsPage: 2 }, expect.any(AbortSignal));
    const table = screen.getByRole('region', { name: '账户资金收支，可上下左右滚动' });
    expect(within(table).getByText('合成主体')).toBeTruthy();
    expect(within(table).queryByText('合成末页户名')).toBeNull();
    fireEvent.keyDown(screen.getByRole('menuitemradio', { name: '全部' }), { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '主要往来本方账户：全部' }));
  });
  it('changes calendar grain and pages time buckets without changing the full period or table pages', async () => {
    const data = report({ periods: [{ first_time: '2025-11-13 09:00:00', last_time: '2026-11-13 23:59:00', time_basis: 'unqualified' }], timeline_mode: 'auto', timeline_pagination: { total: 121, page: 1, page_size: 120 } }), value = client(data);
    value.importFundsReport = vi.fn(async (_source, query) => ({ ...data, timeline_mode: query.timelineGrain ?? 'auto', timeline_grain: query.timelineGrain === 'month' ? '月' : '日', timeline_pagination: { total: 121, page: query.timelinePage ?? 1, page_size: 120 } }));
    render(<ImportFundsReport {...props(value)}/>);
    fireEvent.change(await screen.findByRole('combobox', { name: '时间粒度' }), { target: { value: 'month' } });
    await waitFor(() => expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', { memberPath: 'folder/one.csv', page: 1, expectedRevision: 'revision-7', timelineGrain: 'month', timelinePage: 1 }, expect.any(AbortSignal)));
    fireEvent.click(await screen.findByRole('button', { name: '后一段' }));
    await waitFor(() => expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', expect.objectContaining({ timelineGrain: 'month', timelinePage: 2 }), expect.any(AbortSignal)));
    expect(await screen.findByText('2025年11月13日至2026年11月13日')).toBeTruthy();
    expect((screen.getByRole('combobox', { name: '时间粒度' }) as HTMLSelectElement).value).toBe('month');
  });
  it('shows calendar dates only in the report period without interpreting timezone or removing source precision', () => {
    expect(reportPeriod('2026-11-13 09:00:26.123456','2026-11-13 09:31:26.123456')).toBe('2026年11月13日至2026年11月13日');
    expect(reportPeriod('2026-11-13 23:59:00Z','2026-11-14 00:01:00Z')).toBe('2026年11月13日至2026年11月14日');
    expect(reportPeriod(null,null)).toBe('—');
  });
  it('shows one retained original in the material table, with its own name, size, record count and fingerprint', async () => {
    const data=report();data.scope={...data.scope,material_name:'20261114-1-2号检材',source_record_total:160};
    render(<ImportFundsReport {...props(client(data))}/>);
    const material=await screen.findByRole('region',{name:'检材资料，可上下左右滚动'});
    expect(within(material).getAllByRole('columnheader').map(cell=>cell.textContent)).toEqual(['检材名称','文件名','文件大小','数据行数','SHA256 校验值']);
    expect(within(material).getByText('20261114-1-2号检材')).toBeTruthy();
    expect(within(material).getByText('synthetic.zip')).toBeTruthy();expect(within(material).getByText('1,000 B')).toBeTruthy();
    expect(within(material).getByText('160')).toBeTruthy();expect(within(material).queryByText('one.csv')).toBeNull();
    expect(within(material).getAllByRole('separator')).toHaveLength(5);
    expect(document.querySelector('.ft-report-integrity')).toBeNull();
  });
  it('keeps exact cents, account identity, bounded rank count and truthful unverified hash', async () => {
    render(<ImportFundsReport {...props(client())} />);
    await screen.findByRole('region', { name: '账户资金收支，可上下左右滚动' });
    expect(reportNarrative(report())).toContain('9,999,999,999,999,999.99');
    expect(screen.getByText(/接收指纹已登记/)).toBeTruthy();
    expect(screen.queryByText('留存字节与登记指纹一致')).toBeNull();
    expect(screen.getByText('实际 1')).toBeTruthy();
    expect(screen.getByRole('button', { name: '000SYN-A' }).querySelector('[data-full-value]')?.getAttribute('data-full-value')).toContain('另列卡号：未单列');
    expect(document.querySelector('.ft-report-peer-name > span:last-child')?.textContent).toContain('000SYN-B');
    expect(screen.queryByText('实际 10')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '查看完整SHA-256指纹' }));
    expect((await screen.findByRole('dialog', { name: '完整内容' })).textContent).toContain('a'.repeat(64));
    expect(screen.getByRole('dialog', { name: '完整内容' }).textContent).toContain('本次复核值：未执行');
    const outline = screen.getByRole('navigation', { name: '报告目录' });
    expect(within(outline).getAllByRole('link')).toHaveLength(6);
    const scroll = vi.fn(); document.querySelector('.ft-report-content')!.scrollTo = scroll;
    fireEvent.click(within(outline).getByRole('link', { name: '关联明细' }));
    expect(scroll).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth' }));
  });
  it('verifies and exports the exact selected member and revision through bound capabilities', async () => {
    const data = report(), value = client(data), download = vi.fn(async () => {});
    value.verifyImportSource = vi.fn<NonNullable<FundsCaseClientV1['verifyImportSource']>>(async () => ({ ...data.integrity, status: 'match', observed_sha256: 'a'.repeat(64), checked_at: '2026-11-14T09:10:00Z' }));
    render(<ImportFundsReport {...props(value)} actions={{ downloadFundsReport: download }} />);
    fireEvent.click(await screen.findByRole('button', { name: '复核留存' }));
    await screen.findByText('留存字节与登记指纹一致');
    expect(value.verifyImportSource).toHaveBeenCalledWith('synthetic-source', { memberPath: 'folder/one.csv', expectedRevision: 'revision-7' }, expect.any(AbortSignal));
    fireEvent.click(screen.getByRole('button', { name: '导出简报' }));
    await waitFor(() => expect(download).toHaveBeenCalledWith(expect.objectContaining({ sourceId: 'synthetic-source', memberPath: 'folder/one.csv', expectedRevision: 'revision-7', currency: 'CNY' }), expect.any(AbortSignal)));
  });
  it('distinguishes an executed mismatch from an unexecuted check', async () => {
    const data = report(), value = client(data);
    value.verifyImportSource = vi.fn<NonNullable<FundsCaseClientV1['verifyImportSource']>>(async () => ({ ...data.integrity, status: 'mismatch', observed_sha256: null, checked_at: '2026-11-14T09:10:00Z' }));
    render(<ImportFundsReport {...props(value)} />);
    fireEvent.click(await screen.findByRole('button', { name: '复核留存' }));
    await screen.findByText('留存复核不一致');
    fireEvent.click(screen.getByRole('button', { name: '查看完整SHA-256指纹' }));
    const text = (await screen.findByRole('dialog', { name: '完整内容' })).textContent;
    expect(text).toContain('未取得（复核未通过）'); expect(text).not.toContain('未执行');
  });
  it('opens only source-bounded rank records without altering the file summary', async () => {
    const value = client(); value.importReportRecords = vi.fn(async (_source, query) => ({ case_id: value.binding.caseId, source_id: 'synthetic-source', analysis_revision: query.expectedRevision, items: [], total: 0, page: 1, page_size: 50 }));
    render(<ImportFundsReport {...props(value)} />);
    const rank = await screen.findByRole('button', { name: /进账来源第1/ }); rank.focus(); fireEvent.click(rank);
    await waitFor(() => expect(value.importReportRecords).toHaveBeenCalledWith('synthetic-source', { memberPath: 'folder/one.csv', expectedRevision: 'revision-7', currency: 'CNY', counterparty: 'entity-B', direction: 'in', page: 1 }, expect.any(AbortSignal)));
    expect(screen.getByText(/本方账户进账60笔/)).toBeTruthy();
    const scroll = vi.fn(); document.querySelector('.ft-report-content')!.scrollTo = scroll;
    fireEvent.click(screen.getByRole('button', { name: '关闭统计来源明细' }));
    expect(screen.queryByRole('region', { name: '统计来源明细' })).toBeNull();
    expect(document.activeElement).toBe(rank); expect(scroll).toHaveBeenCalledOnce();
  });
  it('keeps same-revision trend context and invalidates the entire old report on a new data version', async () => {
    const original = report(), value = client(original);
    let resolveTrend!: (data: ImportReport) => void, resolveRefresh!: (data: ImportReport) => void;
    value.importFundsReport = vi.fn<NonNullable<FundsCaseClientV1['importFundsReport']>>()
      .mockResolvedValueOnce(original)
      .mockImplementationOnce(() => new Promise(resolve => { resolveTrend = resolve; }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveRefresh = resolve; }));
    value.verifyImportSource = vi.fn(async () => { throw new Error('旧修订复核失败'); });
    const drill = vi.fn<NonNullable<FundsCaseClientV1['importReportRecords']>>(() => new Promise(() => {}));
    value.importReportRecords = drill;
    const downloadFundsReport = vi.fn(async () => {}), reportProps = { ...props(value), actions: { downloadFundsReport } };
    const view = render(<ImportFundsReport {...reportProps} dataVersion={1} />);
    const grain = await screen.findByRole<HTMLSelectElement>('combobox', { name: '时间粒度' });
    fireEvent.change(grain, { target: { value: 'month' } });
    expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', expect.objectContaining({ expectedRevision: 'revision-7', timelineGrain: 'month' }), expect.any(AbortSignal));
    expect(screen.getByText(/合计9,999,999,999,999,999.99元/)).toBeTruthy();
    expect(grain.disabled).toBe(true);
    await act(async () => resolveTrend({ ...original, timeline_mode: 'month' }));

    fireEvent.click(screen.getByRole('button', { name: '复核留存' }));
    expect(await screen.findByText('旧修订复核失败')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /进账来源第1/ }));
    expect(drill).toHaveBeenCalledOnce();
    const drillSignal = drill.mock.calls[0][2];
    view.rerender(<ImportFundsReport {...reportProps} dataVersion={2} />);
    expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', expect.objectContaining({ expectedRevision: undefined, timelineGrain: 'month' }), expect.any(AbortSignal));
    expect(screen.getByText('正在汇总本文件资金统计…')).toBeTruthy();
    expect(screen.queryByText(/合计9,999,999,999,999,999.99元/)).toBeNull();
    expect(screen.queryByRole('button', { name: '复核留存' })).toBeNull();
    expect(screen.queryByRole('button', { name: /进账来源第1/ })).toBeNull();
    expect(screen.getByRole<HTMLButtonElement>('button', { name: '导出简报' }).disabled).toBe(true);
    expect(drillSignal?.aborted).toBe(true);

    await act(async () => resolveRefresh(report({ analysis_revision: 'revision-8', summary: { ...original.summary, in_amount: '12.34' } })));
    expect(screen.getByText(/合计12.34元/)).toBeTruthy();
    expect(screen.queryByText('旧修订复核失败')).toBeNull();
    expect(screen.getByRole<HTMLButtonElement>('button', { name: '复核留存' }).disabled).toBe(false);
  });
  it('preserves the unlabelled currency group in drilldown and export', async () => {
    const value = client(report({ currency: '', currencies: ['', 'CNY'] })), download = vi.fn(async () => {});
    value.importReportRecords = vi.fn(async (_source, query) => ({ case_id: value.binding.caseId, source_id: 'synthetic-source', analysis_revision: query.expectedRevision, items: [], total: 0, page: 1, page_size: 50 }));
    render(<ImportFundsReport {...props(value)} actions={{ downloadFundsReport: download }} />);
    await screen.findByRole('option', { name: '币种未明确' });
    fireEvent.click(screen.getByRole('button', { name: /进账来源第1/ }));
    await waitFor(() => expect(value.importReportRecords).toHaveBeenCalledWith('synthetic-source', expect.objectContaining({ currency: '' }), expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole('button', { name: '导出简报' }));
    await waitFor(() => expect(download).toHaveBeenCalledWith(expect.objectContaining({ currency: '' }), expect.any(AbortSignal)));
  });
  it('pages relationships independently while retaining the single account table', async () => {
    const data = report({ relations: { items: [], total: 21, page: 1, page_size: 20 } }), value = client(data);
    value.importFundsReport = vi.fn(async (_source, query) => ({ ...data, relations: { ...data.relations, page: query.relationsPage ?? 1 } }));
    render(<ImportFundsReport {...props(value)} />);
    fireEvent.click(await screen.findByRole('button', { name: '下一页' }));
    await waitFor(() => expect(value.importFundsReport).toHaveBeenLastCalledWith('synthetic-source', { memberPath: 'folder/one.csv', page: 1, relationsPage: 2, expectedRevision: 'revision-7' }, expect.any(AbortSignal)));
    await screen.findByText('2 / 2');
    expect(screen.getByText('合成主体')).toBeTruthy();
    expect(screen.queryByText('当前页没有账户资金记录')).toBeNull();
  });
  it('drops old files and late replies on same-case rebinding, and clears on revoke', async () => {
    const first = client(); let finish!: (value: ImportReport) => void;
    first.importFundsReport = vi.fn(() => new Promise<ImportReport>(resolve => { finish = resolve; }));
    const controller = new AbortController(), second = client(report({ summary: { ...report().summary, in_amount: '12.34' } }));
    const view = render(<ImportFundsReport {...props(first)} />);
    view.rerender(<ImportFundsReport {...props(second, controller.signal)} />);
    await screen.findByText(/合计12.34元/);
    await act(async () => finish(report()));
    expect(screen.queryByText(/合计9,999,999,999,999,999.99元/)).toBeNull();
    act(() => controller.abort());
    expect(screen.queryByRole('heading', { name: '资金统计分析报告' })).toBeNull();
  });
  it('rejects scope mismatch and reports retry errors instead of stale amounts', async () => {
    render(<ImportFundsReport {...props(client(report({ member_path: 'another.csv' })))} />);
    await screen.findByRole('alert');
    expect(screen.queryByRole('region', { name: '账户资金收支，可上下左右滚动' })).toBeNull();
    expect(screen.getByRole('button', { name: '刷新报告' })).toBeTruthy();
  });
  it('keeps the report title and actions in place while waiting for the first valid report', async () => {
    const value = client();
    let resolve!: (data: ImportReport) => void;
    value.importFundsReport = vi.fn(() => new Promise<ImportReport>(ready => { resolve = ready; }));
    const downloadFundsReport = vi.fn().mockResolvedValue(undefined);
    const reportProps = props(value);
    render(<ImportFundsReport {...reportProps} actions={{ downloadFundsReport }} />);
    const title = screen.getByRole('heading', { name: '资金统计分析报告' });
    const exportButton = screen.getByRole<HTMLButtonElement>('button', { name: '导出简报' });
    expect(exportButton.disabled).toBe(true);
    expect(screen.getByRole('status').textContent).toBe('正在汇总本文件资金统计…');
    expect(screen.queryByRole('region', { name: '账户资金收支，可上下左右滚动' })).toBeNull();
    fireEvent.click(screen.getByRole('link', { name: '主要往来' }));
    expect(reportProps.onChapter).toHaveBeenLastCalledWith('peers');

    await act(async () => resolve(report()));
    expect(screen.getByRole('heading', { name: '资金统计分析报告' })).toBe(title);
    expect(screen.getByRole('button', { name: '导出简报' })).toBe(exportButton);
    expect(exportButton.disabled).toBe(false);
    expect(screen.queryByText('正在汇总本文件资金统计…')).toBeNull();
    expect(screen.getByRole('region', { name: '账户资金收支，可上下左右滚动' })).toBeTruthy();
    expect(reportProps.onChapter).toHaveBeenLastCalledWith('peers');
  });
  it('charts the supplied periods and preserves exact hover amounts with keyboard access', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    render(<ImportReportTrend currency="CNY" grain="5分钟" buckets={[{ start: '2026-11-13 09:00:00', end: '2026-11-13 09:05:00', time_basis: 'unqualified', in_count: 1, out_count: 0, in_amount: '9999999999999999.99', out_amount: '0.00', net_amount: '9999999999999999.99' }]} />);
    fireEvent.keyDown(screen.getByRole('group', { name: /资金变化图/ }), { key: 'ArrowRight' });
    expect(screen.getByRole('status').textContent).toContain('9,999,999,999,999,999.99');
    expect(screen.getByTitle('来源壁钟时间（未标时区）')).toBeTruthy();
    expect(screen.getByText('收支笔次')).toBeTruthy();
    expect(screen.getByText(/按原始记载日期/)).toBeTruthy();
  });
  it('keeps the expanded trend table readable with the full minimum width of large money', async () => {
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null);
    let selected:Node|undefined,measurements=0;
    vi.spyOn(document,'createRange').mockImplementation(()=>({selectNodeContents:(node:Node)=>{selected=node;},getBoundingClientRect:()=>{measurements++;return {width:(selected?.textContent||'').length*8};}} as unknown as Range));
    vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockImplementation(function(this:HTMLElement){return this.classList.contains('ft-grid-scroll')?880:0;});
    vi.stubGlobal('getComputedStyle',()=>({paddingLeft:'10px',paddingRight:'10px',borderLeftWidth:'0px',borderRightWidth:'0px',getPropertyValue:()=>''}));
    try {
      render(<ImportReportTrend currency="CNY" grain="5分钟" buckets={[{start:'2026-11-13 09:00:00',end:'2026-11-13 09:05:00',time_basis:'unqualified',in_count:1,out_count:0,in_amount:'9999999999999999.99',out_amount:'0.00',net_amount:'9999999999999999.99'}]}/>);
      const details=document.querySelector('details')!;expect(details.open).toBe(true);
      act(()=>{details.open=true;fireEvent(details,new Event('toggle'));});
      await waitFor(()=>expect(Number(screen.getByRole('separator',{name:'调整进账金额（元）列宽'}).getAttribute('aria-valuenow'))).toBeGreaterThanOrEqual('9,999,999,999,999,999.99'.length*8+20));
    } finally {vi.unstubAllGlobals();}
  });
  it('links a pinned month, exact table, chart modes and scoped direction drill without selecting a gap as a neighbour',()=>{
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null);
    const rows:ImportReport['timeline']=[
      {start:'2026-01-01 00:00:00',end:'2026-02-01 00:00:00',time_basis:'unqualified',in_amount:'10.01',out_amount:'0.00',net_amount:'10.01',in_count:1,out_count:0},
      {start:'2026-02-01 00:00:00',end:'2026-03-01 00:00:00',time_basis:'unqualified',in_amount:'30000.03',out_amount:'4000.04',net_amount:'25999.99',in_count:1,out_count:1},
      {start:'2026-03-01 00:00:00',end:'2026-04-01 00:00:00',time_basis:'unqualified',has_records:false,in_amount:null,out_amount:null,net_amount:null,in_count:0,out_count:0}];
    const drill=vi.fn(),view=render(<ImportReportTrend buckets={rows} grain="月" currency="CNY" onOpenRecords={drill}/>);
    fireEvent.click(screen.getByRole('button',{name:'选择2026年2月'}));
    expect(screen.getByRole('status').textContent).toContain('25,999.99');
    expect(document.querySelector('tr[data-point="1"]')?.getAttribute('aria-selected')).toBe('true');
    view.rerender(<ImportReportTrend buckets={rows} grain="月" currency="CNY" mode="line" onOpenRecords={drill}/>);
    expect(screen.getByRole('status').textContent).toContain('30,000.03');
    fireEvent.click(within(screen.getByRole('region',{name:'时间变化明细'})).getByRole('button',{name:'4,000.04'}));
    expect(drill).toHaveBeenLastCalledWith(rows[1],'out');
    fireEvent.keyDown(screen.getByRole('group',{name:/资金变化图/}),{key:'End'});
    expect(screen.getByRole('status').textContent).toContain('2026年3月');
    expect(screen.getByRole('status').textContent).toContain('本次资料未见纳入记录');
    expect(screen.getByRole('status').textContent).not.toContain('30,000.03');
    fireEvent.keyDown(screen.getByRole('group',{name:/资金变化图/}),{key:'Escape'});
    expect(screen.queryByRole('status')).toBeNull();
    expect(within(screen.getByRole('region',{name:'时间变化明细'})).queryByText('2026年3月')).toBeNull();
  });
  it('hits the actual empty calendar cell with the pointer instead of snapping to a distant transaction',()=>{
    const context=new Proxy({}, {get:(_target,key)=>key==='measureText'?()=>({width:40}):()=>{}}) as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(context);
    vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(800);
    vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(280);
    vi.spyOn(HTMLCanvasElement.prototype,'getBoundingClientRect').mockReturnValue({left:0,top:0,width:800,height:280,right:800,bottom:280,x:0,y:0,toJSON:()=>({})});
    const rows:ImportReport['timeline']=Array.from({length:12},(_,i)=>({start:`2026-${String(i+1).padStart(2,'0')}-01 00:00:00`,end:i===11?'2027-01-01 00:00:00':`2026-${String(i+2).padStart(2,'0')}-01 00:00:00`,time_basis:'unqualified',has_records:i===0||i===11,in_amount:i===0?'100000.01':i===11?'700.07':null,out_amount:i===0||i===11?'0.00':null,net_amount:i===0?'100000.01':i===11?'700.07':null,in_count:i===0||i===11?1:0,out_count:0}));
    render(<ImportReportTrend buckets={rows} grain="月" currency="CNY"/>);
    fireEvent.pointerMove(screen.getByRole('group',{name:/资金变化图/}),{clientX:420,clientY:60});
    const text=screen.getByRole('status').textContent;
    expect(text).toContain('本次资料未见纳入记录');expect(text).not.toContain('100,000.01');expect(text).not.toContain('700.07');
  });
  it('keeps account-filter edits local until applying, supports source-name search, reset and cancel',async()=>{
    const data=report(),value=client(data),change=vi.fn();
    value.importReportAccounts=vi.fn(async(_source,query)=>({case_id:data.case_id,source_id:data.source_id,member_path:data.member_path,analysis_revision:data.analysis_revision,items:data.accounts.items.filter(row=>!query.search||row.names.join('').includes(query.search)),total:1,page:query.page||1,page_size:20}));
    render(<div className="ft-report-content"><ImportTrendFilter data={data} client={value} sourceId={data.source_id} scopeSignal={new AbortController().signal} onChange={change}/></div>);
    fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));
    fireEvent.click(await screen.findByRole('checkbox',{name:'选择账户000SYN-A'}));
    expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'应用'}));
    expect(change).toHaveBeenCalledWith({timelineScope:'except',timelineAccounts:['entity-A'],timelineSearch:''});
    fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));
    fireEvent.change(screen.getByRole('textbox',{name:'搜索并筛选户名或账卡号'}),{target:{value:'合成主体'}});
    await waitFor(()=>expect(value.importReportAccounts).toHaveBeenLastCalledWith(data.source_id,expect.objectContaining({search:'合成主体',expectedRevision:data.analysis_revision}),expect.any(AbortSignal)));
    fireEvent.scroll(document.querySelector('.ft-report-content')!);
    expect(screen.getByRole('textbox',{name:'搜索并筛选户名或账卡号'})).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'应用'}));
    expect(change).toHaveBeenLastCalledWith({timelineScope:'all',timelineAccounts:[],timelineSearch:'合成主体'});
    fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));fireEvent.click(screen.getByRole('button',{name:'重置'}));
    expect(change).toHaveBeenLastCalledWith({timelineScope:'all',timelineAccounts:[],timelineSearch:''});
    const calls=change.mock.calls.length;
    fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));fireEvent.keyDown(screen.getByRole('textbox'),{key:'Escape'});
    expect(screen.queryByRole('dialog',{name:'时间变化账户筛选'})).toBeNull();expect(change).toHaveBeenCalledTimes(calls);
    fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));
    fireEvent.blur(screen.getByRole('textbox'),{relatedTarget:document.body});
    expect(screen.queryByRole('dialog',{name:'时间变化账户筛选'})).toBeNull();expect(change).toHaveBeenCalledTimes(calls);
  });

  it('labels foreign currency amounts explicitly in the trend table and tooltip',()=>{
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null);
    render(<ImportReportTrend currency="USD" grain="月" buckets={[{start:'2026-11-01 00:00:00Z',end:'2026-12-01 00:00:00Z',time_basis:'utc',in_amount:'0.00',out_amount:'19.99',net_amount:'-19.99',in_count:0,out_count:1}]}/>);
    expect(within(screen.getByRole('region',{name:'时间变化明细'})).getByRole('columnheader',{name:/出账金额（USD）/})).toBeTruthy();
    fireEvent.keyDown(screen.getByRole('group',{name:/资金变化图/}),{key:'Home'});
    expect(screen.getByRole('status').textContent).toContain('19.99 USD');
    expect(screen.getByRole('status').textContent).not.toContain(' 元');
  });

  it('keeps keyboard inspection through pointer leave and prevents Escape from cancelling the containing dialog',()=>{
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null);
    const outer=vi.fn();render(<div onKeyDown={outer}><ImportReportTrend currency="CNY" grain="月" onOpenRecords={vi.fn()} buckets={[{start:'2026-02-01 00:00:00',end:'2026-03-01 00:00:00',time_basis:'unqualified',in_amount:'30000.03',out_amount:'4000.04',net_amount:'25999.99',in_count:1,out_count:1}]}/></div>);
    const plot=screen.getByRole('group',{name:/资金变化图/});plot.focus();fireEvent.keyDown(plot,{key:'Home'});fireEvent.pointerLeave(plot);
    expect(screen.getByRole('status').textContent).toContain('25,999.99');
    const button=within(screen.getByRole('status')).getByRole('button',{name:'查看本期明细'});button.focus();outer.mockClear();
    const event=new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true});fireEvent(button,event);
    expect(event.defaultPrevented).toBe(true);expect(outer).not.toHaveBeenCalled();expect(document.activeElement).toBe(plot);expect(screen.queryByRole('status')).toBeNull();
  });

  it('plots one transaction once even when it has an incoming and outgoing account side',()=>{
    const text=vi.fn(),dots=vi.fn();const context=new Proxy({fillText:text,arc:dots},{get:(target,key)=>Reflect.get(target,key)??(()=>{})}) as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(context);vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(800);vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(280);
    const bucket={start:'2026-02-01 00:00:00',end:'2026-03-01 00:00:00',time_basis:'unqualified' as const,in_amount:'3.00',out_amount:'3.00',net_amount:'0.00',in_count:1,out_count:1,transaction_count:1};
    render(<ImportReportTrend currency="CNY" grain="月" buckets={[bucket]}/>);
    const one=text.mock.calls.find(args=>args[0]==='1');expect(one).toBeTruthy();expect(dots.mock.calls[0][1]).toBe(one![2]-4);
    fireEvent.keyDown(screen.getByRole('group',{name:/资金变化图/}),{key:'Home'});expect(screen.getByRole('status').textContent).toContain('本期交易 1 笔');
  });
  it('maps pointer positions through a scaled canvas to the correct calendar cell',()=>{
    const context=new Proxy({},{get:()=>()=>{}}) as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(context);vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(800);vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(280);
    vi.spyOn(HTMLCanvasElement.prototype,'getBoundingClientRect').mockReturnValue({left:0,top:0,width:400,height:140,right:400,bottom:140,x:0,y:0,toJSON:()=>({})});
    const rows:ImportReport['timeline']=Array.from({length:12},(_,i)=>({start:`2026-${String(i+1).padStart(2,'0')}-01 00:00:00`,end:i===11?'2027-01-01 00:00:00':`2026-${String(i+2).padStart(2,'0')}-01 00:00:00`,time_basis:'unqualified',has_records:i===0||i===11,in_amount:i===0?'100000.01':i===11?'700.07':null,out_amount:i===0||i===11?'0.00':null,net_amount:i===0?'100000.01':i===11?'700.07':null,in_count:i===0||i===11?1:0,out_count:0}));
    render(<ImportReportTrend currency="CNY" grain="月" buckets={rows}/>);fireEvent.pointerMove(screen.getByRole('group',{name:/资金变化图/}),{clientX:370,clientY:60});
    expect(screen.getByRole('status').textContent).toContain('2026年12月');expect(screen.getByRole('status').textContent).toContain('700.07');
  });
  it('does not draw phantom outgoing bars for equivalent zero amount strings',()=>{
    const rects=vi.fn();const context=new Proxy({fillRect:rects},{get:(target,key)=>Reflect.get(target,key)??(()=>{})}) as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(context);vi.spyOn(HTMLElement.prototype,'clientWidth','get').mockReturnValue(800);vi.spyOn(HTMLElement.prototype,'clientHeight','get').mockReturnValue(280);
    const rows:ImportReport['timeline']=['0','0.0','-0.00'].map((zero,i)=>({start:`2026-0${i+1}-01 00:00:00`,end:`2026-0${i+2}-01 00:00:00`,time_basis:'unqualified',in_amount:'1.00',out_amount:zero,net_amount:'1.00',in_count:1,out_count:0}));
    render(<ImportReportTrend currency="CNY" grain="月" buckets={rows} mode="bar"/>);expect(rects).toHaveBeenCalledTimes(3);
  });
  it('labels the full time-domain total while the chart switches domains independently',async()=>{
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null);
    const data=report({summary:{in_count:2,out_count:0,in_amount:'30.00',out_amount:'0.00',net_amount:'30.00',transaction_count:2,amount:'30.00'},periods:[{first_time:'2026-01-01 00:00:00',last_time:'2026-01-01 00:00:00',time_basis:'unqualified'},{first_time:'2026-01-01 00:00:00Z',last_time:'2026-01-01 00:00:00Z',time_basis:'utc'}],timeline_grain:'月',timeline:[{start:'2026-01-01 00:00:00',end:'2026-02-01 00:00:00',time_basis:'unqualified',in_amount:'10.00',out_amount:'0.00',net_amount:'10.00',in_count:1,out_count:0},{start:'2026-01-01 00:00:00Z',end:'2026-02-01 00:00:00Z',time_basis:'utc',in_amount:'20.00',out_amount:'0.00',net_amount:'20.00',in_count:1,out_count:0}]});
    render(<ImportFundsReport {...props(client(data))}/>);await screen.findByRole('region',{name:'账户资金收支，可上下左右滚动'});
    expect(screen.queryByText(/全时间域合计/)).not.toBeNull();fireEvent.click(screen.getByRole('button',{name:'选择2026年1月'}));fireEvent.change(screen.getByRole('combobox',{name:'趋势时间域'}),{target:{value:'utc'}});
    expect(document.querySelector('.ft-report-chart-tip')).toBeNull();const table=screen.getByRole('region',{name:'时间变化明细'});expect(within(table).getAllByText('20.00',{exact:true})).toHaveLength(2);expect(within(table).queryByText('10.00',{exact:true})).toBeNull();expect(document.querySelector('.ft-trend-summary')?.textContent).toContain('30.00');
  });
  it('preserves explicit account selections across pages without applying them early',async()=>{
    const data=report(),value=client(data),change=vi.fn();const choices=Array.from({length:25},(_,i)=>({...data.accounts.items[0],account:`SYN-ID-${i+1}`,account_number:`SYN-${String(i+1).padStart(2,'0')}`,names:['合成同名公司']}));
    value.importReportAccounts=vi.fn(async(_source,query)=>({case_id:data.case_id,source_id:data.source_id,member_path:data.member_path,analysis_revision:data.analysis_revision,items:choices.slice(((query.page||1)-1)*20,(query.page||1)*20),total:25,page:query.page||1,page_size:20}));
    render(<ImportTrendFilter data={data} client={value} sourceId={data.source_id} scopeSignal={new AbortController().signal} onChange={change}/>);fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));await screen.findByRole('checkbox',{name:'选择账户SYN-01'});fireEvent.click(screen.getByRole('checkbox',{name:/^全部账户/}));fireEvent.click(screen.getByRole('checkbox',{name:'选择本页合成同名公司账户'}));fireEvent.click(screen.getByRole('button',{name:'下一页筛选账户'}));await screen.findByRole('checkbox',{name:'选择账户SYN-25'});fireEvent.click(screen.getByRole('checkbox',{name:'选择本页合成同名公司账户'}));expect(change).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'应用'}));expect(change.mock.calls[0][0].timelineAccounts).toEqual(choices.map(row=>row.account));expect(change.mock.calls[0][0].timelineScope).toBe('selected');
  });

  it('shows mixed parent and global checkboxes when only part of a holder group is selected',async()=>{
    const data=report(),value=client(data),change=vi.fn();const choices=['SYN-A1','SYN-A2'].map(account=>({...data.accounts.items[0],account,account_number:account,account_numbers:[account],names:['合成同名公司']}));
    value.importReportAccounts=vi.fn(async()=>({case_id:data.case_id,source_id:data.source_id,member_path:data.member_path,analysis_revision:data.analysis_revision,items:choices,total:2,page:1,page_size:20}));
    render(<ImportTrendFilter data={data} client={value} sourceId={data.source_id} scopeSignal={new AbortController().signal} onChange={change}/>);fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));fireEvent.click(await screen.findByRole('checkbox',{name:'选择账户SYN-A1'}));
    const parent=screen.getByRole('checkbox',{name:'选择本页合成同名公司账户'}) as HTMLInputElement,all=screen.getByRole('checkbox',{name:/^全部账户/}) as HTMLInputElement;
    expect(parent.indeterminate).toBe(true);expect(parent.getAttribute('aria-checked')).toBe('mixed');expect(all.indeterminate).toBe(true);expect(change).not.toHaveBeenCalled();
    fireEvent.click(parent);expect(parent.indeterminate).toBe(false);expect(all.indeterminate).toBe(false);expect(parent.checked).toBe(true);expect(all.checked).toBe(true);expect((screen.getByRole('checkbox',{name:'选择账户SYN-A1'}) as HTMLInputElement).checked).toBe(true);
  });

  it('collapses a holder group without dropping selected or mixed account state',async()=>{
    const data=report(),value=client(data),change=vi.fn(),choices=['SYN-A1','SYN-A2'].map(account=>({...data.accounts.items[0],account,account_number:account,account_numbers:[account],names:['合成同名公司']}));
    value.importReportAccounts=vi.fn(async()=>({case_id:data.case_id,source_id:data.source_id,member_path:data.member_path,analysis_revision:data.analysis_revision,items:choices,total:2,page:1,page_size:20}));
    render(<ImportTrendFilter data={data} client={value} sourceId={data.source_id} scopeSignal={new AbortController().signal} onChange={change}/>);fireEvent.click(screen.getByRole('button',{name:'时间变化账户筛选：全部账户'}));await screen.findByRole('checkbox',{name:'选择账户SYN-A1'});
    fireEvent.click(screen.getByRole('button',{name:'收起合成同名公司账户'}));expect(screen.queryByRole('checkbox',{name:'选择账户SYN-A1'})).toBeNull();expect(change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('checkbox',{name:'选择本页合成同名公司账户'}));fireEvent.click(screen.getByRole('button',{name:'展开合成同名公司账户'}));expect((screen.getByRole('checkbox',{name:'选择账户SYN-A1'}) as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole('checkbox',{name:'选择账户SYN-A1'}));fireEvent.click(screen.getByRole('button',{name:'收起合成同名公司账户'}));expect((screen.getByRole('checkbox',{name:'选择本页合成同名公司账户'}) as HTMLInputElement).indeterminate).toBe(true);fireEvent.click(screen.getByRole('button',{name:'应用'}));expect(change).toHaveBeenCalledWith({timelineScope:'except',timelineAccounts:['SYN-A2'],timelineSearch:''});
  });

});
