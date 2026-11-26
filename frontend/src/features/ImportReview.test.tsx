import {act,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {expect,it,vi} from 'vitest';
import type {ImportReviewPage,ImportReviewRecord,ImportRowComparison} from '../../../contracts/api-v1';
import type {FundsCaseClientV1} from '../panel/host';
import {ImportReview} from './ImportReview';

const original=(value:string,field:string,column=1)=>({column,header:field==='amount'?'交易金额':'交易时间',value,fields:[field]});
const row=(number:number,reason:string,value:string):ImportReviewRecord=>({record_no:number-1,source_row:number,member_path:'table.csv',record_key:`SYN-${number}`,disposition:'unresolved',reason_code:reason,details:{},flow_id:null,preview:null,
  original:[original(value,reason==='invalid_amount'?'amount':'trade_time'),{column:9,header:'合成附注',value:'SYNTHETIC FULL VALUE',fields:[]}]});
const first=row(7,'invalid_time',' 原始坏日期 '),second=row(62,'invalid_amount','原始金额无法解析');
const good:ImportReviewRecord={...row(2,'transaction_valid','2026-11-13 09:00:00'),disposition:'imported',original:[original('2026-11-13 09:00:00','trade_time'),original('9999999999999999.99','amount',2)]};
function page(view:ImportReviewPage['view']='all',index=1):ImportReviewPage {
  const target=index===2?second:first;
  return {case_id:'1',source_id:'SYN-SOURCE',member_path:'table.csv',view,counts:{all:70,review:2,accepted:68},
    items:view==='accepted'?[good]:view==='review'?[first,second]:index===2?[second]:[good,first],
    total:view==='all'?70:view==='review'?2:68,page:view==='all'&&index===2?2:1,page_size:50,
    issue:view==='accepted'?null:{index,record_no:target.record_no,source_row:target.source_row,reason_code:target.reason_code},original_error:null};
}
function client():FundsCaseClientV1 {
  return {binding:{caseId:'1',scopeKey:'review',generation:1},stats:vi.fn(),flows:vi.fn(),preview:vi.fn(),repreview:vi.fn(),commit:vi.fn(),job:vi.fn(),imports:vi.fn(),sources:vi.fn(),cleanup:vi.fn(),
    importReview:vi.fn(async(_source,_member,query)=>page(query.view,query.issueIndex||1))};
}

it('reports matching original observations while funds remain pending', async () => {
  const own = client();
  own.compareImportRows = vi.fn(async (): Promise<ImportRowComparison> => ({
    case_id: '1',
    source_id: 'SYN-SOURCE',
    member_path: 'table.csv',
    scope: 'selected_source_rows',
    status: 'pending',
    reason: 'different_ledger',
    rows: [
      { record_no: good.record_no, source_row: good.source_row, own_account_column: 2, direction_column: 6, direction: 'out' },
      { record_no: first.record_no, source_row: first.source_row, own_account_column: 2, direction_column: 6, direction: 'in' },
    ],
    event_attribution: 'unknown',
    observation_correspondence: {
      own_card_column: 1, counterparty_identifier_column: 7, reference_column: 21,
      status_column: 16, time_column: 3, amount_column: 4, currency_column: 13,
    },
  }));
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE"
    memberPath="table.csv" allowCompare />);
  fireEvent.click(await screen.findByRole('checkbox', { name: '选择原始第 2 行核对双侧' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '选择原始第 7 行核对双侧' }));
  fireEvent.click(screen.getByRole('button', { name: '核对所选两行是否互为双侧' }));

  const status = screen.getByRole('status', { name: '双侧核对结果' });
  await waitFor(() => expect(status.textContent).toContain('可回查两条原行及各自余额，交易是否成功仍待核'));
  expect(status.textContent).toContain('原始第 2 行');
  expect(status.textContent).toContain('原始第 7 行');
  expect(screen.getByText(/对手账卡号第 7 列/).textContent).toContain('交易状态见第 16 列');
  expect(own.commit).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: '清空选择' }));
  expect(status.textContent).toBe('');
});

it('shows account reciprocity and missing linkage as a pending source result', async () => {
  const own = client();
  own.compareImportRows = vi.fn(async (): Promise<ImportRowComparison> => ({
    case_id: '1', source_id: 'SYN-SOURCE', member_path: 'table.csv',
    scope: 'selected_source_rows', status: 'pending', reason: 'source_account_text_pending',
    event_attribution: 'unknown',
    rows: [
      {record_no: good.record_no, source_row: 2, own_account_column: 2, direction_column: 9, direction: 'out'},
      {record_no: first.record_no, source_row: 7, own_account_column: 2, direction_column: 9, direction: 'in'},
    ],
    account_pair_lead: {
      kind: 'reciprocal_accounts', own_account_column: 2, counterparty_identifier_column: 10, balance_column: 8,
      empty_reference_columns: [20, 24, 25, 27],
      observations: [
        {source_row: 2, direction: 'out', balance_witness_rows: [3, 4], whole_member_candidate_count: 1},
        {source_row: 7, direction: 'in', balance_witness_rows: [6, 8], whole_member_candidate_count: 1},
      ],
      registration_context: {
        source_role_only: true, complete: true, different_name: true, different_document: true,
        matches: [2, 7].map((source_row, offset) => ({
          posting_source_row: source_row, source_id: 'SYN-REGISTRY', member_path: 'table.csv', source_row: offset + 4,
          account_column: 4, card_column: 3, name_column: 1, document_column: 2, currency_column: 8,
          opening_column: 5, closing_column: 13, last_transaction_column: 18, branch_column: 16,
          balance_column: 6, available_column: 7, card_matches: offset === 0 ? true : null,
          card_equals_account: offset === 1, posting_card_present: offset === 0, name_present: true, document_present: true,
        })),
      },
    },
  }));
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE"
    memberPath="table.csv" allowCompare />);
  fireEvent.click(await screen.findByRole('checkbox', {name: '选择原始第 2 行核对双侧'}));
  fireEvent.click(screen.getByRole('checkbox', {name: '选择原始第 7 行核对双侧'}));
  fireEvent.click(screen.getByRole('button', {name: '核对所选两行是否互为双侧'}));
  const notice = screen.getByRole('status', {name: '双侧核对结果'});
  await waitFor(() => expect(notice.textContent).toContain('按本方账号互指。同笔对应待核'));
  const detail = screen.getByText(/尚缺能关联两条原行的对应凭据/);
  expect(detail.textContent).toContain('相邻第 6、8 行');
  expect(detail.textContent).toContain('第 20、24、25、27 列的编号为空');
  const registry = screen.getByText(/实际受益人仍待核/);
  expect(registry.textContent).toContain('开户资料第 5 行');
  expect(registry.textContent).toContain('本方卡号原格为空');
  expect(registry.textContent).toContain('开户登记的姓名和证件号均不同');
  expect(screen.queryByText(/对应同一笔账面往来/)).toBeNull();
  expect(own.commit).not.toHaveBeenCalled();
});

it.each([
  {
    outcome: {
      status: 'pending', reason: 'different_ledger',
      observation_correspondence: {
        own_card_column: 1, counterparty_identifier_column: 10, reference_column: 25,
        source_reference_kind: 'ledger_log', status_column: 19, time_column: 6, amount_column: 7, currency_column: 16,
      },
    },
    statusText: '两条原行对应同一笔账面往来',
    evidence: '日志号第 25 列相互核对',
  },
  {
    outcome: {
      status: 'excluded', reason: 'other_ledger_correspondence',
      alternative_correspondences: [[2, 5], [7, 8]],
    },
    statusText: '排除这组账面配对',
    evidence: '原始第 2、5 行和原始第 7、8 行',
  },
] satisfies {outcome: Partial<ImportRowComparison>; statusText: string; evidence: string}[])(
  'reports $statusText without asserting settlement', async ({outcome, statusText, evidence}) => {
    const own = client();
    own.compareImportRows = vi.fn(async (): Promise<ImportRowComparison> => ({
      case_id: '1', source_id: 'SYN-SOURCE', member_path: 'table.csv', scope: 'selected_source_rows',
      event_attribution: 'unknown',
      rows: [
        {record_no: good.record_no, source_row: good.source_row, own_account_column: 2, direction_column: 9, direction: 'out'},
        {record_no: first.record_no, source_row: first.source_row, own_account_column: 2, direction_column: 9, direction: 'in'},
      ],
      posting_comparison: {
        opposite_directions: true, reciprocal_card_text: true, same_recorded_clock: !!outcome.observation_correspondence,
        same_amount: true, same_currency: true, same_reference: false, reference_text_matches: true,
        reference_occurrences: [11, 11],
        columns: {direction: 9, own_card: 1, peer: 10, amount: 7, currency: 16, clock: 6, reference: 24},
      },
      ...outcome,
    }));
    render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE"
      memberPath="table.csv" allowCompare />);
    fireEvent.click(await screen.findByRole('checkbox', {name: '选择原始第 2 行核对双侧'}));
    fireEvent.click(screen.getByRole('checkbox', {name: '选择原始第 7 行核对双侧'}));
    fireEvent.click(screen.getByRole('button', {name: '核对所选两行是否互为双侧'}));
    const status = screen.getByRole('status', {name: '双侧核对结果'});
    await waitFor(() => expect(status.textContent).toContain(statusText));
    expect(status.textContent).toContain('交易结算待核');
    expect(screen.getByText((_text, element) => element?.tagName === 'P' &&
      Boolean(element.textContent?.includes(evidence)))).toBeTruthy();
    expect(screen.getByText(/原交易流水号字面相同/).textContent).toContain('此原号无法区分这些记录');
    expect(screen.queryByText(/还需能指回双方原行/)).toBeNull();
    expect(screen.queryByText(/交易流水号第 25 列/)).toBeNull();
    expect(own.commit).not.toHaveBeenCalled();
  },
);

it('uses full-file server counts, changes scope and navigates to original rows across pages without writes',async()=>{
  const own=client();render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv"/>);
  await screen.findByRole('button',{name:'全部 70'});
  expect(screen.getByRole('button',{name:'待核对 2'})).toBeTruthy();
  expect(screen.getByText('原始坏日期').textContent).toBe(' 原始坏日期 ');
  const width=screen.getByRole('separator',{name:'调整交易金额列宽'});fireEvent.keyDown(width,{key:'ArrowRight'});const resized=width.getAttribute('aria-valuenow');
  fireEvent.click(screen.getByRole('button',{name:'已接纳 68'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'已接纳 68'}).getAttribute('aria-pressed')).toBe('true'));
  await screen.findByText('9999999999999999.99');
  expect(screen.queryByText('原始坏日期')).toBeNull();
  expect(screen.getByRole('separator',{name:'调整交易金额列宽'}).getAttribute('aria-valuenow')).toBe(resized);
  fireEvent.click(screen.getByRole('button',{name:'待核对 2'}));await screen.findByRole('button',{name:/原始第 7 行：/});
  fireEvent.click(screen.getByRole('button',{name:'下一处待核对'}));await waitFor(()=>expect(screen.getByText('2 / 2')).toBeTruthy());
  expect((screen.getByRole('button',{name:'下一处待核对'}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole('button',{name:/原始第 62 行：/})).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'上一处待核对'}));await screen.findByText('1 / 2');
  expect(own.importReview).toHaveBeenCalledWith('SYN-SOURCE','table.csv',{view:'review',page:1,issueIndex:2},expect.any(AbortSignal));
  expect(own.commit).not.toHaveBeenCalled();expect(own.preview).not.toHaveBeenCalled();
});

it('opens the exact raw reason from the row icon and Escape only dismisses that surface',async()=>{
  render(<ImportReview client={client()} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv"/>);
  const trigger=await screen.findByRole('button',{name:/原始第 7 行：/});fireEvent.click(trigger);
  const popup=screen.getByRole('dialog',{name:'完整内容'});
  expect(popup.textContent).toContain('交易时间无效');expect(popup.textContent).toContain('交易时间： 原始坏日期 ');
  fireEvent.keyDown(popup,{key:'Escape'});expect(screen.queryByRole('dialog',{name:'完整内容'})).toBeNull();
  expect(document.activeElement).toBe(trigger);expect(screen.getByRole('group',{name:'来源记录筛选'})).toBeTruthy();
});

it('shows the cited source cells for a conflicting withdrawal direction', async () => {
  const own = client();
  const reason = '原第 2 行第 7 列的方向说明与本行第 4 列取款的默认方向相反。请核对该表区的方向释义。';
  const held: ImportReviewRecord = {
    ...row(4, 'conflicting_direction', '取款'), details: { reason },
    original: [{ column: 4, header: '交易方向', value: '取款', fields: ['direction'] }],
  };
  own.importReview = vi.fn(async (_s, _m, query) => ({ ...page(query.view), items: [held],
    issue: { index: 1, record_no: held.record_no, source_row: held.source_row, reason_code: held.reason_code } }));
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv" />);
  fireEvent.click(await screen.findByRole('button', { name: '原始第 4 行：收付方向依据相互矛盾' }));
  const popup = screen.getByRole('dialog', { name: '完整内容' });
  expect(popup.textContent).toContain(reason);
  expect(popup.textContent).toContain('交易方向：取款');
});

it('clears stale rows on a new member and discards late replies and revocation',async()=>{
  const own=client(),scope=new AbortController();let finish!:(value:ImportReviewPage)=>void;
  own.importReview=vi.fn(()=>new Promise<ImportReviewPage>(resolve=>{finish=resolve;}));
  const view=render(<ImportReview client={own} scopeSignal={scope.signal} sourceId="SYN-SOURCE" memberPath="table.csv" reserveIssueBar/>);
  const late=finish;
  view.rerender(<ImportReview client={own} scopeSignal={scope.signal} sourceId="SYN-SOURCE" memberPath="second.csv" reserveIssueBar/>);
  const grid=screen.getByRole('region',{name:'数据预览，可上下左右滚动'});
  const issueBar=view.container.querySelector('.ft-review-navigation');
  expect(issueBar?.getAttribute('aria-hidden')).toBe('true');
  expect(screen.getByRole('status').textContent).toContain('正在读取来源记录…');
  expect(screen.getByRole('button',{name:'全部 —'})).toBeTruthy();
  await act(async()=>late(page()));expect(screen.queryByText('原始坏日期')).toBeNull();
  await act(async()=>finish({...page(),member_path:'second.csv',items:page().items.map(row=>({...row,member_path:'second.csv'}))}));
  await screen.findByText('原始坏日期');
  expect(screen.queryByRole('status')).toBeNull();
  expect(screen.getByRole('region',{name:'数据预览，可上下左右滚动'})).toBe(grid);
  expect(screen.getByRole('button',{name:'下一处待核对'}).closest('.ft-review-navigation')).toBe(issueBar);
  act(()=>scope.abort());expect(screen.queryByRole('region',{name:'来源数据阅览'})).toBeNull();
});

it('does not turn a missing original into zero values or classify normal exclusions as problems',async()=>{
  const own=client();own.importReview=vi.fn(async():Promise<ImportReviewPage>=>({...page(),counts:{all:3,review:0,accepted:2},issue:null,original_error:'原件不可用',items:[{...good,disposition:'duplicate',original:null},{...good,record_no:2,source_row:3,disposition:'non_monetary',original:null}],total:3}));
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv"/>);
  await screen.findByText('原件不可用');expect(screen.getByText('重复')).toBeTruthy();expect(screen.getByText('未计入')).toBeTruthy();
  expect(screen.queryByRole('button',{name:'下一处待核对'})).toBeNull();expect(screen.getAllByText('原始单元格不可用')).toHaveLength(2);
});

it('targets balance and status cells and includes historical additional reasons and their original values',async()=>{
  const own=client(),historical={...first,reason_code:'invalid_balance',details:{review_reasons:'unknown_status,invalid_time'},original:[
    {column:1,header:'交易余额',value:'SYN-BAD-BALANCE',fields:['balance']},
    {column:2,header:'交易状态',value:'SYN-UNKNOWN-STATUS',fields:['status']},
    {column:3,header:'交易时间',value:'SYN-BAD-TIME',fields:['trade_time']},
  ]};
  own.importReview=vi.fn(async()=>({...page(),items:[historical],issue:{index:1,record_no:historical.record_no,source_row:7,reason_code:'invalid_balance'}}));
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv"/>);
  const balance=await screen.findByText(/SYN-BAD-BALANCE/);
  expect(balance.closest('td')?.getAttribute('data-problem')).toBe('true');
  expect(screen.getByText('SYN-UNKNOWN-STATUS').closest('td')?.getAttribute('data-problem')).toBe('true');
  expect(screen.getByText('SYN-BAD-TIME').closest('td')?.getAttribute('data-problem')).toBe('true');
  fireEvent.click(screen.getByRole('button',{name:/原始第 7 行：/}));
  const popup=screen.getByRole('dialog',{name:'完整内容'});
  expect(popup.textContent).toContain('SYN-BAD-BALANCE');expect(popup.textContent).toContain('SYN-UNKNOWN-STATUS');expect(popup.textContent).toContain('SYN-BAD-TIME');
});

it('identifies typed Excel errors without presenting the source boolean as accepted money',async()=>{
  const own=client();own.importReview=vi.fn(async():Promise<ImportReviewPage>=>({...page(),items:[{...first,reason_code:'invalid_cell_type',original:[{column:1,header:'交易金额',value:'1',fields:['amount'],kind:'boolean'}]}]}));
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv"/>);
  const cell=await screen.findByText('1（Excel布尔值）');expect(cell.closest('td')?.getAttribute('data-problem')).toBe('true');
});

it('reveals exact source values and the corresponding cell reason without requiring truncation',async()=>{
  const own=client();own.importReview=vi.fn(async(_source,_member,query):Promise<ImportReviewPage>=>query.view==='accepted'?page('accepted'):{...page(),items:[{
    ...first,details:{validation_issues:'invalid_time,invalid_amount'},original:[
      {column:1,header:'交易日期',value:' 2026/19/51 ',fields:['trade_time']},
      {column:2,header:'交易金额',value:'9999999999999999.99元?',fields:['amount']},
    ],
  }]});
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv"/>);
  const date=await screen.findByText('2026/19/51');
  expect(date.tabIndex).toBe(0);expect(date.scrollWidth).toBe(date.clientWidth);
  fireEvent.click(screen.getByRole('button',{name:/原始第 7 行：/}));
  expect(screen.getByRole('dialog',{name:'完整内容'}).textContent).toContain('交易金额格式无效');
  fireEvent.pointerOver(date.closest('td')!);
  const popup=await screen.findByRole('dialog',{name:'完整内容'});
  expect(popup.textContent).toContain('交易时间无效');expect(popup.textContent).toContain('原始值 · 交易日期： 2026/19/51 ');
  expect(popup.textContent).not.toContain('交易金额格式无效');
  fireEvent.keyDown(date,{key:'Escape'});expect(screen.queryByRole('dialog',{name:'完整内容'})).toBeNull();
  const amount=screen.getByText('9999999999999999.99元?');act(()=>amount.focus());
  const next=await screen.findByRole('dialog',{name:'完整内容'});
  expect(next.textContent).toContain('交易金额格式无效');expect(next.textContent).toContain('原始值 · 交易金额：9999999999999999.99元?');
  expect(next.textContent).not.toContain('交易时间无效');expect(own.importReview).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button',{name:'已接纳 68'}));await screen.findByText('9999999999999999.99');
  expect(screen.queryByRole('dialog',{name:'完整内容'})).toBeNull();
});

it('distinguishes an available empty source cell from an unavailable original',async()=>{
  const own=client();own.importReview=vi.fn(async():Promise<ImportReviewPage>=>({...page(),items:[{...first,original:[{column:1,header:'交易时间',value:'',fields:['trade_time']}]}]}));
  render(<ImportReview client={own} scopeSignal={new AbortController().signal} sourceId="SYN-SOURCE" memberPath="table.csv"/>);
  await screen.findByRole('button',{name:/原始第 7 行：/});
  const cell=document.querySelector<HTMLElement>('td[data-problem="true"] .ft-cell-value')!;
  act(()=>cell.focus());
  const popup=await screen.findByRole('dialog',{name:'完整内容'});
  expect(popup.textContent).toContain('原始值 · 交易时间：（空白）');expect(popup.textContent).not.toContain('原始值不可用');
});
