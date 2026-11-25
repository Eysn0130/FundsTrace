import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ImportReviewPage, ImportReviewRecord, ImportOriginalCell, ImportRowComparison, ImportSignedAmountReview } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { linkAbortSignal } from '../panel/cancellation';
import { feedbackReason } from './ImportDetails';
import { ImportCellValue, ImportTable } from './ImportTable';
import { ImportIcon } from './ImportTree';
import { ImportFullValue } from './ImportFullValue';
import { previewCells, previewHeadings, previewWidths } from './ImportDataPreview';

const fields = ['source_row','trade_time','kind','counterparty','payer_name','payer_account','payee_name','payee_account','amount','currency','balance','summary','trade_no','payer_bank','payee_bank','channel','self_account','self_name'];
const order = [0,1,2,8,9,-1,3,4,5,6,7,10,11,12,13,14,15,16,17];
const baseColumns = order.map(index => index < 0 ? {field:'direction',heading:'交易方向',width:130,index} : {field:fields[index],heading:previewHeadings[index],width:previewWidths[index],index});
baseColumns[3] = {...baseColumns[3],heading:'交易金额'};
const moneyColumns = [3,11];
const needsReview = (row: ImportReviewRecord) => row.disposition === 'review' || row.disposition === 'unresolved';
function reasons(row: ImportReviewRecord) {
  return [...new Set([row.reason_code,...(row.details.validation_issues || '').split(','),...(row.details.review_reasons || '').split(',')].filter(Boolean))];
}
function problemFields(row: ImportReviewRecord, codes = reasons(row)) {
  const result = new Set<string>();
  for (const reason of codes) {
    if (/time|date/.test(reason)) ['trade_time','time_part','_time2'].forEach(field=>result.add(field));
    if (/amount|unit|income|expense/.test(reason)) ['amount','income_amt','expense_amt'].forEach(field=>result.add(field));
    if (/currency/.test(reason)) result.add('currency');
    if (/balance/.test(reason)) result.add('balance');
    if (/status/.test(reason)) result.add('status');
    if (/direction/.test(reason)) ['direction','income_amt','expense_amt'].forEach(field=>result.add(field));
    if (/identity|account/.test(reason)) ['self_account','self_card','payer_account','payee_account','counter_account','counter_card'].forEach(field=>result.add(field));
    if (reason === 'formula_value') row.original?.filter(cell=>cell.value.startsWith('=')).flatMap(cell=>cell.fields).forEach(field=>result.add(field));
    if (reason === 'invalid_cell_type') row.original?.filter(cell=>cell.kind==='boolean'||cell.kind==='error').flatMap(cell=>cell.fields).forEach(field=>result.add(field));
  }
  return result;
}
function cellText(cell: ImportOriginalCell) {
  return cell.kind==='boolean'?`${cell.value}（Excel布尔值）`:cell.kind==='error'?`${cell.value}（Excel类型异常）`:cell.value;
}
function rawValue(row: ImportReviewRecord, field: string, heading: string) {
  const keys = field === 'counterparty' ? ['counter_name','counter_account'] : [field];
  const cells = (row.original || []).filter(cell=>cell.fields.some(value=>keys.includes(value)));
  return cells.map(cell=>['amount','balance'].includes(field) && cell.header !== heading ? `${cellText(cell)}（${cell.header}）` : cellText(cell)).join(' / ') || '—';
}
function issueText(row: ImportReviewRecord) {
  const codes = reasons(row);
  const problems = problemFields(row, codes);
  const values = (row.original || []).filter(cell=>cell.fields.some(field=>problems.has(field))).map(cell=>`${cell.header}：${cellText(cell)}`);
  const messages = codes.map(code => code === 'conflicting_direction' && row.reason_code === code
    ? row.details.reason || feedbackReason(code) : feedbackReason(code));
  return [`原始第 ${row.source_row} 行 · ${messages.join('；')}`,...values].join('\n');
}
function cellIssueText(row: ImportReviewRecord, field: string, original?: ImportOriginalCell) {
  const keys=original?.fields || (field==='trade_time'?['trade_time','time_part','_time2']:field==='amount'?['amount','income_amt','expense_amt']:field==='direction'?['direction','income_amt','expense_amt']:[field]);
  const codes=reasons(row).filter(reason=>keys.some(key=>problemFields(row,[reason]).has(key)));
  const cells=original?[original]:(row.original||[]).filter(cell=>cell.fields.some(key=>keys.includes(key)));
  return [`${(codes.length?codes:reasons(row)).map(feedbackReason).join('；')} · 原始第 ${row.source_row} 行`,
    ...(cells.length?cells.map(cell=>{
      const typed = codes.includes('negative_amount') && row.member_path.startsWith('worksheets/') && cell.kind === 'text';
      return `${typed ? `原始第 ${cell.column} 列` : '原始值'} · ${cell.header}：${cellText(cell)||'（空白）'}${typed ? '（Excel 文本）' : ''}`;
    }):['原始值不可用'])].join('\n');
}

type RowPosition = Pick<ImportReviewRecord, 'record_no' | 'source_row'>;
type Comparison = { client: FundsCaseClientV1; sourceId: string; memberPath: string; rows: RowPosition[];
  result?: ImportRowComparison; error?: string; checking?: boolean };
const comparisonMessages = {
  same_ledger_same_direction: '这两条是同一本账的同向记录，排除互为一出一入的双侧分录。实际同笔、重复和结算仍待核。',
  different_ledger: '原行记录的本方账号不同，双侧关系待核。',
  different_direction: '两条原行来自同一本方账簿，收付方向相反。是否同笔及是否冲正，仍待核。',
  source_role_unproved: '选中原行的本方账号或方向无法核定，双侧关系待核。',
  different_amount_currency: '所选两行可排除同币同额的镜像配对。其他经济关系待核。',
  other_ledger_correspondence: '所选两行各有已核对侧，排除这组账面配对。交易结算待核。',
  source_account_text_pending: '两条原行按本方账号互指。同笔对应待核，原金额与各自余额分别保留。',
};

function SignedAmountDetails({review}: {review:ImportSignedAmountReview}) {
  const shownMarker = (marker:string) => marker ? `“${marker}”` : '空白或未识别';
  const excluded = [
    review.unreadable_count && `无法解析 ${review.unreadable_count} 条`,
    review.finer_than_cent_count && `精度超过分 ${review.finer_than_cent_count} 条`,
  ].filter(Boolean).join('、');
  const located = (items:ImportSignedAmountReview['candidate_groups'][number]['positive_rows']) => items.map(row =>
    `第 ${row.source_row} 行（${row.raw_amount}，原标记 ${shownMarker(row.direction_marker)}）`).join('、');
  return <>
    <p>同列正数 {review.positive_count} 条，零 {review.zero_count} 条，空白 {review.blank_count} 条{excluded ? `，另有${excluded}` : ''}。</p>
    {!!review.negative_bank_field_only_count &&
      <p>其中 {review.negative_bank_field_only_count} 条有对手开户银行，对手户名、证件和账卡号原格为空。</p>}
    {!!review.candidate_group_count && <details>
      <summary>同号正负候选 {review.candidate_group_count} 组</summary>
      {review.candidate_groups.map(group => <p key={group.negative_rows[0].source_row}>
        {located(group.negative_rows)}与{located(group.positive_rows)}在第 21 列同号，币种和金额绝对值相同。
        {(group.negative_count > group.negative_rows.length || group.positive_count > group.positive_rows.length)
          && `同额原行共 ${group.negative_count + group.positive_count} 条，此处列部分位置。`}
        另有 {group.other_same_reference_count} 条同号原行。{group.own_account_missing && '候选中有本方账号空白。'}冲正关系待核。
      </p>)}
      {review.candidate_group_count > review.candidate_groups.length && <p>当前列出前 {review.candidate_groups.length} 组。</p>}
    </details>}
  </>;
}

/** One source-bound table: full-file filters and server-located issue positions. */
export function ImportReview({client,scopeSignal,sourceId,memberPath,reserveIssueBar,startInReview=false,allowCompare=false}: {
  client: FundsCaseClientV1; scopeSignal: AbortSignal; sourceId: string; memberPath: string; reserveIssueBar?: boolean;
  startInReview?: boolean; allowCompare?: boolean;
}) {
  const [query,setQuery] = useState<{view:ImportReviewPage['view'];page:number;issueIndex?:number}>(
    () => ({view:startInReview?'review':'all',page:1,issueIndex:1}));
  const [snapshot,setSnapshot] = useState<{client:FundsCaseClientV1;sourceId:string;memberPath:string;key:string;data:ImportReviewPage}>();
  const [failure,setFailure] = useState<{key:string;message:string}>(),[reload,setReload] = useState(0);
  const [comparison,setComparison] = useState<Comparison>();
  const [signedAmounts,setSignedAmounts] = useState<{client:FundsCaseClientV1; scopeSignal:AbortSignal; sourceId:string;
    memberPath:string; review?:ImportSignedAmountReview; error?:string}>();
  const comparisonRequest = useRef<AbortController | undefined>(undefined);
  const compareEnabled = !!(allowCompare && client.compareImportRows && !scopeSignal.aborted);
  const pair = compareEnabled && comparison?.client === client && comparison.sourceId === sourceId
    && comparison.memberPath === memberPath ? comparison : undefined;
  const correspondence = pair?.result?.observation_correspondence;
  const ledgerLog = correspondence?.source_reference_kind === 'ledger_log';
  const posting = pair?.result?.posting_comparison;
  const accountLead = pair?.result?.account_pair_lead;
  const registration = accountLead?.registration_context;
  const alternativePairs = pair?.result?.alternative_correspondences;
  const signedAudit = compareEnabled && signedAmounts?.client === client && signedAmounts.scopeSignal === scopeSignal
    && signedAmounts.sourceId === sourceId && signedAmounts.memberPath === memberPath ? signedAmounts : undefined;
  const pickedRows = pair?.rows ?? [];
  const [popover,setPopover] = useState<{anchor:HTMLElement;value:string}>(), pinned = useRef(false), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const section = useRef<HTMLElement>(null), key=JSON.stringify(query);
  const discovered = useRef<{client:FundsCaseClientV1;sourceId:string;memberPath:string;cells:ImportOriginalCell[]}>({client,sourceId,memberPath,cells:[]});
  if(discovered.current.client!==client||discovered.current.sourceId!==sourceId||discovered.current.memberPath!==memberPath)discovered.current={client,sourceId,memberPath,cells:[]};
  const ownSnapshot = snapshot?.client===client && snapshot.sourceId===sourceId && snapshot.memberPath===memberPath && !scopeSignal.aborted ? snapshot : undefined;
  const data = ownSnapshot?.key===key ? ownSnapshot.data : undefined, counts=ownSnapshot?.data.counts;
  const error = failure?.key===key ? failure.message : '';
  const issuePlaceholder = !counts && !error && reserveIssueBar;
  const hide = () => {clearTimeout(timer.current);pinned.current=false;setPopover(undefined);};
  useEffect(()=>()=>clearTimeout(timer.current),[]);
  useEffect(() => () => { comparisonRequest.current?.abort(); comparisonRequest.current = undefined; },
    [client,scopeSignal,sourceId,memberPath,compareEnabled]);
  useEffect(() => {
    if (!compareEnabled || !client.importSignedAmounts) return;
    const controller = new AbortController(), unlink = linkAbortSignal(scopeSignal, controller);
    const binding = {client,scopeSignal,sourceId,memberPath};
    client.importSignedAmounts(sourceId, memberPath, controller.signal).then(review => {
      if (controller.signal.aborted) return;
      if (review && (review.case_id !== client.binding.caseId || review.source_id !== sourceId || review.member_path !== memberPath)) {
        throw new Error('金额核对结果与当前来源不一致。');
      }
      setSignedAmounts({...binding,review:review || undefined});
    }).catch(error => {
      if (!controller.signal.aborted && error.name !== 'AbortError') {
        setSignedAmounts({...binding,error:'暂无法核对原表金额符号。'});
      }
    });
    return () => { unlink(); controller.abort(); };
  }, [client,scopeSignal,sourceId,memberPath,compareEnabled]);
  function pick(row: ImportReviewRecord) {
    const existing = pickedRows.some(item => item.record_no === row.record_no);
    if (!compareEnabled || (!existing && pickedRows.length === 2)) return;
    comparisonRequest.current?.abort(); comparisonRequest.current = undefined;
    setComparison({client,sourceId,memberPath,rows:existing ? pickedRows.filter(item => item.record_no !== row.record_no)
      : [...pickedRows,{record_no:row.record_no,source_row:row.source_row}]});
  }
  function resetComparison() {
    comparisonRequest.current?.abort(); comparisonRequest.current = undefined; setComparison(undefined);
  }
  async function compareSelected() {
    if (pickedRows.length !== 2 || !compareEnabled || !client.compareImportRows || comparisonRequest.current) return;
    const controller = new AbortController(), unlink = linkAbortSignal(scopeSignal, controller);
    comparisonRequest.current = controller;
    const selected = {client,sourceId,memberPath,rows:pickedRows};
    const active = () => comparisonRequest.current === controller && !controller.signal.aborted && !scopeSignal.aborted;
    setComparison({...selected,checking:true});
    try {
      const response = await client.compareImportRows(sourceId, {member_path:memberPath,
        record_nos:pickedRows.map(row => row.record_no)}, controller.signal);
      if (!active()) return;
      if (response.case_id !== client.binding.caseId || response.source_id !== sourceId || response.member_path !== memberPath
        || response.scope !== 'selected_source_rows' || response.rows.length !== 2 || !pickedRows.every(row =>
          response.rows.some(original => original.record_no === row.record_no && original.source_row === row.source_row))) {
        throw new Error('核对结果不属于所选的两条原行。');
      }
      setComparison({...selected,result:response});
    } catch (cause) {
      if (active()) setComparison({...selected,error:cause instanceof Error ? cause.message : '所选原行暂无法核对。'});
    } finally {
      unlink();
      if (comparisonRequest.current === controller) comparisonRequest.current = undefined;
    }
  }
  useEffect(()=>{
    const controller=new AbortController(),unlink=linkAbortSignal(scopeSignal,controller);let active=true;
    setFailure(undefined);hide();
    const revoke=()=>{setSnapshot(undefined);setFailure(undefined);hide();};
    scopeSignal.addEventListener('abort',revoke,{once:true});
    client.importReview!(sourceId,memberPath,query,controller.signal).then(value=>{
      if (!active || controller.signal.aborted) return;
      if (value.case_id!==client.binding.caseId || value.source_id!==sourceId || value.member_path!==memberPath || value.view!==query.view
        || value.items.some(row=>row.member_path!==memberPath||!Number.isSafeInteger(row.record_no)||row.record_no<1||!Number.isSafeInteger(row.source_row)||row.source_row<1)
        || ['all','review','accepted'].some(name=>!Number.isSafeInteger(value.counts[name])||value.counts[name]<0)
        || value.counts.review+value.counts.accepted>value.counts.all || value.total!==value.counts[query.view]) throw new Error('来源记录与当前案件或文件不一致');
      setSnapshot({client,sourceId,memberPath,key,data:value});
    }).catch(reason=>{if(active&&!controller.signal.aborted&&reason.name!=='AbortError')setFailure({key,message:reason.message});});
    return ()=>{active=false;scopeSignal.removeEventListener('abort',revoke);unlink();controller.abort();};
  },[client,scopeSignal,sourceId,memberPath,key,reload]);
  useLayoutEffect(()=>{
    if (!data?.issue || scopeSignal.aborted) return;
    let settled=0;
    const frame=requestAnimationFrame(()=>{settled=requestAnimationFrame(()=>{
      const viewport=section.current?.querySelector<HTMLElement>('.ft-review-scroll');
      const row=section.current?.querySelector<HTMLElement>(`tr[data-record="${data.issue!.record_no}"]`);
      if (!viewport||!row||scopeSignal.aborted) return;
      const cell=row.querySelector<HTMLElement>('[data-problem="true"]')||row.children[0] as HTMLElement;
      const bounds=viewport.getBoundingClientRect(),rect=cell.getBoundingClientRect();
      const header=viewport.querySelector('thead')?.getBoundingClientRect().height||32;
      const fixed=cell===row.children[0]?0:row.children[0].getBoundingClientRect().width;
      const context=Math.max(0,(viewport.clientHeight-header-rect.height)/2);
      viewport.scrollTo?.({top:Math.max(0,viewport.scrollTop+rect.top-bounds.top-header-context),
        left:rect.right>bounds.right ? viewport.scrollLeft+rect.right-bounds.right+12 : rect.left<bounds.left+fixed ? viewport.scrollLeft+rect.left-bounds.left-fixed-12 : viewport.scrollLeft,
        behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    });});
    return ()=>{cancelAnimationFrame(frame);cancelAnimationFrame(settled);};
  },[data,scopeSignal]);
  if(scopeSignal.aborted)return null;
  const covered=new Set(baseColumns.map(column=>column.field));covered.add('counter_name');covered.add('counter_account');
  const knownColumns=discovered.current.cells;
  for(const row of data?.items||[])for(const cell of row.original||[])if(!knownColumns.some(existing=>existing.column===cell.column&&existing.header===cell.header))knownColumns.push({...cell,value:''});
  const sourceOnly=counts?.accepted===0&&knownColumns.length>0;
  const base=sourceOnly?[baseColumns[0]]:baseColumns;
  const extras=sourceOnly?knownColumns:knownColumns.filter(cell=>!cell.fields.some(field=>covered.has(field)));
  const columns=[...base,...extras.map(cell=>({field:`original:${cell.column}:${cell.header}`,heading:sourceOnly?cell.header:`原始·${cell.header}`,width:180,index:-2}))];
  const setView=(view:ImportReviewPage['view'])=>{hide();setQuery({view,page:1,...(view!=='accepted'?{issueIndex:1}:{})});};
  const navigate=(delta:number)=>{
    if(!data||!counts?.review)return;
    const index=Math.max(1,Math.min(counts.review,(data.issue?.index||0)+delta));
    hide();setQuery({view:query.view==='accepted'?'all':query.view,page:1,issueIndex:index});
  };
  const selected=data?.items.find(row=>row.record_no===data.issue?.record_no);
  const show=(anchor:HTMLElement,row:ImportReviewRecord,click=false)=>{
    clearTimeout(timer.current);
    if(anchor.dataset.ftValueDismissed&&!click)return;
    if(click){pinned.current=true;setPopover({anchor,value:issueText(row)});}
    else timer.current=setTimeout(()=>setPopover({anchor,value:issueText(row)}),240);
  };
  const leave=()=>{clearTimeout(timer.current);if(!pinned.current)timer.current=setTimeout(hide,160);};
  const dismissRowHint=(target:EventTarget)=>{if((target as HTMLElement).closest('td:not(.ft-review-row-number)'))hide();};
  return <section ref={section} className="ft-preview-section ft-review-section" aria-label="来源数据阅览" onPointerOver={event=>dismissRowHint(event.target)} onFocus={event=>dismissRowHint(event.target)}>
    <div className="ft-section-heading ft-review-heading"><h4>数据预览</h4><div className="ft-review-filters" role="group" aria-label="来源记录筛选">{(['all','review','accepted'] as const).map(view=><button type="button" key={view} aria-pressed={query.view===view} onClick={()=>setView(view)}>{({all:'全部',review:'待核对',accepted:'已接纳'})[view]} <span>{counts?.[view]??'—'}</span></button>)}</div></div>
    {compareEnabled && <div>
      <div role="status" aria-label="原表金额符号核对">{signedAudit?.error || (signedAudit?.review &&
        <>原表第 4 列有 {signedAudit.review.negative_count} 条负金额。第 6 列收付标记为 {signedAudit.review.negative_directions
          .map(entry => `${entry.marker ? `“${entry.marker}”` : '空白或未识别'} ${entry.count} 条`).join('、')}。</>)}</div>
      {signedAudit?.review && <SignedAmountDetails review={signedAudit.review} />}
    </div>}
    {compareEnabled && <div>
      <button type="button" className="ft-text-button" disabled={pickedRows.length !== 2 || pair?.checking} onClick={() => void compareSelected()}>
        {pair?.checking ? '正在核对' : '核对所选两行是否互为双侧'}
      </button>
      {!!pickedRows.length && <><span>已选原始第 {pickedRows.map(row => row.source_row).join('、')} 行</span>
        <button type="button" className="ft-text-button" onClick={resetComparison}>清空选择</button></>}
      <p role="status" aria-label="双侧核对结果">{pair?.error || (pair?.result && <>
        {correspondence ? (ledgerLog ? '两条原行对应同一笔账面往来。可回查两条原行及各自余额，交易结算待核。'
          : '两条原行的两端与交易流水号在本表相互印证。可回查两条原行及各自余额，交易是否成功仍待核。')
          : comparisonMessages[pair.result.reason] ?? '双侧关系待核。'}
        {' '}{pair.result.rows.map(row => `原始第 ${row.source_row} 行${row.own_account_column != null && row.direction_column != null
          ? `（本方第 ${row.own_account_column} 列，${row.direction === 'in' ? '入账' : row.direction === 'out' ? '出账' : '方向'}第 ${row.direction_column} 列）` : ''}`).join('、')}。
      </>)}</p>
      {correspondence && <p>
        本方卡号第 {correspondence.own_card_column} 列、对手账卡号第 {correspondence.counterparty_identifier_column} 列、{ledgerLog ? '日志号' : '交易流水号'}第 {correspondence.reference_column} 列相互核对。
        交易时间第 {correspondence.time_column} 列、金额第 {correspondence.amount_column} 列、币种第 {correspondence.currency_column} 列可回查，交易状态见第 {correspondence.status_column} 列。
        {ledgerLog && '两侧收付相反，两本账的相邻余额均能衔接。'}
      </p>}
      {!!alternativePairs?.length && <p>
        本表已核对的账面组为{alternativePairs.map(rows => `原始第 ${rows.join('、')} 行`).join('和')}。
      </p>}
      {accountLead && <p>
        本方账号第 {accountLead.own_account_column} 列与对方账卡第 {accountLead.counterparty_identifier_column} 列按账号互指，两侧同币同额、原报时刻相同。
        {accountLead.observations.map(item => `第 ${item.source_row} 行的余额见第 ${accountLead.balance_column} 列，可据相邻第 ${item.balance_witness_rows.join('、')} 行核算。同表按该侧账号、对端、方向、币额找到 ${item.whole_member_candidate_count} 条。`).join('')
        }第 {accountLead.empty_reference_columns.join('、')} 列的编号为空。尚缺能关联两条原行的对应凭据。
      </p>}
      {registration && <p>
        {registration.matches.map(item => `原流水第 ${item.posting_source_row} 行可按账号与币种回查开户资料第 ${item.source_row} 行。
          资料第 ${item.card_column} 列为卡号，第 ${item.account_column} 列为账号。${item.card_equals_account ? '这两列原值相同。' : ''}
          ${item.posting_card_present ? '' : '该流水的本方卡号原格为空。'}`).join('')}
        {registration.different_name && registration.different_document && '这两条开户登记的姓名和证件号均不同。'}
        交易时的适用关系和实际受益人仍待核。
      </p>}
      {posting && !accountLead && <p>第 {posting.columns.direction} 列收付方向{posting.opposite_directions ? '相反' : '相同'}，第 {posting.columns.own_card}、{posting.columns.peer} 列原标识{posting.reciprocal_card_text ? '文本互指' : '未见文本互指'}。
        第 {posting.columns.amount} 列金额{posting.same_amount ? '相同' : '不同'}，第 {posting.columns.currency} 列币种{posting.same_currency ? '相同' : '不同'}。
        第 {posting.columns.clock} 列记录时刻{posting.same_recorded_clock ? '相同' : '不同'}，第 {posting.columns.reference} 列{posting.reference_text_matches == null
          ? (posting.same_reference ? '流水号相同' : '未核得同一流水号')
          : (posting.reference_text_matches ? '原交易流水号字面相同' : '未见相同的原交易流水号')}。
        所选原号在本表分别出现 {posting.reference_occurrences.map(count => count == null ? '次数待核' : `${count} 次`).join('、')}。
        {posting.reference_text_matches && !posting.same_reference && '此原号无法区分这些记录。'}
        {posting.different_vouchers && `第 ${posting.columns.voucher} 列凭证号不同，仍需核明两号的适用范围及对应。`}
        {posting.same_amount && posting.same_currency && posting.opposite_directions && posting.reciprocal_card_text
          && !posting.same_reference && !correspondence && !alternativePairs?.length
          && '核定这两行是否同笔，还需能指回双方原行的编号对应或等效凭据，并核全短时同额竞争记录。'}
      </p>}
      {pair?.result?.selected_reference_column && <p>所选两行在原表第 {pair.result.selected_reference_column} 列的交易流水号相同。</p>}
      {pair?.result?.reference_competition && <p>
        同表区第 {pair.result.reference_competition.first_row} 至 {pair.result.reference_competition.last_row} 行内，
        另有 {pair.result.reference_competition.other_count} 条同号原行
        {pair.result.reference_competition.other_source_rows.length > 0 && <>（原始第 {pair.result.reference_competition.other_source_rows.join('、')} 行）</>}。
      </p>}
      {!!pair?.result?.reference_competition?.selected_differences.length && <p>
        所选两行的原文差异在{pair.result.reference_competition.selected_differences.map(field => `第 ${field.column} 列${field.heading}`).join('、')}。
      </p>}
    </div>}
    {(!!counts?.review || issuePlaceholder) && <div className="ft-review-navigation" aria-hidden={issuePlaceholder || undefined}>
      {!!counts?.review && <>
        <ImportIcon name="import-circle-alert"/>
        <span className="ft-review-current">{selected ? `原始第 ${selected.source_row} 行 · ${feedbackReason(selected.reason_code)}` : `待核对 ${counts.review} 行`}</span>
        <div className="ft-review-navigation-actions">
          <button type="button" className="icon-button" aria-label="上一处待核对" disabled={!data?.issue || data.issue.index <= 1} onClick={()=>navigate(-1)}><ImportIcon name="chevron-left"/></button>
          {data?.issue && <span aria-live="polite">{data.issue.index} / {counts.review}</span>}
          <button type="button" className="icon-button" aria-label="下一处待核对" disabled={!data || data.issue?.index === counts.review} onClick={()=>navigate(1)}><ImportIcon name="chevron-right"/></button>
        </div>
      </>}
    </div>}
    <ImportTable autoSize={!!data} revealAfterFit className="ft-preview-scroll ft-review-scroll" label="数据预览，可上下左右滚动" headings={columns.map(column=>column.heading)} widths={columns.map(column=>column.width)} moneyColumns={sourceOnly?[]:moneyColumns}><tbody>
      {!data && (error
        ? <tr><td colSpan={columns.length} className="ft-review-message" role="alert">{error}<button className="ft-text-button" onClick={() => setReload(value => value + 1)}>重试</button></td></tr>
        : <tr><td colSpan={columns.length} className="ft-review-message">
          <div className="ft-review-pending ft-import-waiting" role="status">
            正在读取来源记录…
            <span aria-hidden="true" />
            <span aria-hidden="true" />
          </div>
        </td></tr>)}
      {data?.items.map(row=>{
        const values=row.preview?previewCells(row.preview):null,problems=problemFields(row),bad=needsReview(row);
        return <tr key={row.record_no} data-record={row.record_no} data-focused={row.record_no===data.issue?.record_no} data-review={bad||undefined}>
          {columns.map((column,index)=>{
            if(index===0){
              const picked = pickedRows.some(item => item.record_no === row.record_no);
              return <td key="row" className="ft-review-row-number" data-fit-cell="true">{compareEnabled && <input type="checkbox"
                aria-label={`选择原始第 ${row.source_row} 行核对双侧`} checked={picked}
                disabled={!row.original?.length || row.disposition === 'removed' || (!picked && pickedRows.length === 2)}
                onChange={() => pick(row)} />}{bad&&<button type="button" className="ft-row-issue" aria-label={`原始第 ${row.source_row} 行：${reasons(row).map(feedbackReason).join('；')}`} onPointerEnter={event=>show(event.currentTarget,row)} onFocus={event=>show(event.currentTarget,row)} onPointerLeave={leave} onBlur={event=>{delete event.currentTarget.dataset.ftValueDismissed;if(!(event.relatedTarget as HTMLElement|null)?.closest('.ft-value-popover'))hide();}} onClick={event=>show(event.currentTarget,row,true)}><ImportIcon name="import-circle-alert"/></button>}<ImportCellValue>{row.source_row}</ImportCellValue>{row.disposition==='duplicate'&&<span className="ft-row-disposition">重复</span>}{!bad&&!['imported','duplicate'].includes(row.disposition)&&<span className="ft-row-disposition">{row.disposition==='removed'?'已移除':'未计入'}</span>}</td>;
            }
            if(!values&&!row.original)return index===1?<td key="unavailable" colSpan={columns.length-1}>原始单元格不可用</td>:null;
            const original=column.index===-2?row.original?.find(cell=>cell.column===extras[index-base.length].column&&cell.header===extras[index-base.length].header):undefined;
            const value=column.index===-2?(original?cellText(original):'—'):column.index>=0&&values?values[column.index]:rawValue(row,column.field,column.heading);
            const problem=bad&&(original?original.fields.some(field=>problems.has(field)):problems.has(column.field));
            return <td key={column.field} data-problem={problem||undefined} data-fit-text={!values||column.index<0||undefined} className={!sourceOnly&&moneyColumns.includes(index)?'ft-money':undefined}><ImportCellValue fullValue={problem?cellIssueText(row,column.field,original):undefined}>{value}</ImportCellValue></td>;
          })}
        </tr>;
      })}
      {data&&!data.items.length&&<tr><td colSpan={columns.length} className="ft-review-message">{query.view==='review'?'没有待核对记录':query.view==='accepted'?'没有已接纳记录':'没有可查询的来源记录'}</td></tr>}
    </tbody></ImportTable>
    {data?.original_error&&<p className="ft-review-source-error" role="status">{data.original_error}</p>}
    {data&&data.total>data.page_size&&<div className="source-pagination ft-review-pagination"><button className="ft-text-button" disabled={data.page<=1} onClick={()=>setQuery({view:query.view,page:data.page-1})}>上一页</button><span>{data.page} / {Math.ceil(data.total/data.page_size)}</span><button className="ft-text-button" disabled={data.page*data.page_size>=data.total} onClick={()=>setQuery({view:query.view,page:data.page+1})}>下一页</button></div>}
    {popover&&<ImportFullValue anchor={popover.anchor} value={popover.value} onClose={hide} onPointerEnter={()=>clearTimeout(timer.current)} onPointerLeave={leave}/>}
  </section>;
}
