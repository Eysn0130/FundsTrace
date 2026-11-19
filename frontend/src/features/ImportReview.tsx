import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ImportReviewPage, ImportReviewRecord, ImportOriginalCell } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsImportReviewQueryV1 } from '../panel/host';
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
  const problems = problemFields(row);
  const values = (row.original || []).filter(cell=>cell.fields.some(field=>problems.has(field))).map(cell=>`${cell.header}：${cellText(cell)}`);
  return [`原始第 ${row.source_row} 行 · ${reasons(row).map(feedbackReason).join('；')}`,...values].join('\n');
}
function cellIssueText(row: ImportReviewRecord, field: string, original?: ImportOriginalCell) {
  const keys=original?.fields || (field==='trade_time'?['trade_time','time_part','_time2']:field==='amount'?['amount','income_amt','expense_amt']:field==='direction'?['direction','income_amt','expense_amt']:[field]);
  const codes=reasons(row).filter(reason=>keys.some(key=>problemFields(row,[reason]).has(key)));
  const cells=original?[original]:(row.original||[]).filter(cell=>cell.fields.some(key=>keys.includes(key)));
  return [`${(codes.length?codes:reasons(row)).map(feedbackReason).join('；')} · 原始第 ${row.source_row} 行`,
    ...(cells.length?cells.map(cell=>`原始值 · ${cell.header}：${cellText(cell)||'（空白）'}`):['原始值不可用'])].join('\n');
}

/** One source-bound table: full-file filters and server-located issue positions. */
export function ImportReview({client,scopeSignal,sourceId,memberPath,reserveIssueBar,startInReview=false,startSourceRow}: {
  client: FundsCaseClientV1; scopeSignal: AbortSignal; sourceId: string; memberPath: string; reserveIssueBar?: boolean;
  startInReview?: boolean; startSourceRow?: number;
}) {
  const [query,setQuery] = useState<FundsImportReviewQueryV1>(() => startSourceRow === undefined
    ? {view:startInReview?'review':'all',page:1,issueIndex:1} : {view:'all',page:1,sourceRow:startSourceRow});
  const [snapshot,setSnapshot] = useState<{client:FundsCaseClientV1;sourceId:string;memberPath:string;key:string;data:ImportReviewPage}>();
  const [failure,setFailure] = useState<{key:string;message:string}>(),[reload,setReload] = useState(0);
  const [popover,setPopover] = useState<{anchor:HTMLElement;value:string}>(), pinned = useRef(false), timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const section = useRef<HTMLElement>(null), key=JSON.stringify(query);
  const discovered = useRef<{client:FundsCaseClientV1;sourceId:string;memberPath:string;cells:ImportOriginalCell[]}>({client,sourceId,memberPath,cells:[]});
  if(discovered.current.client!==client||discovered.current.sourceId!==sourceId||discovered.current.memberPath!==memberPath)discovered.current={client,sourceId,memberPath,cells:[]};
  const ownSnapshot = snapshot?.client===client && snapshot.sourceId===sourceId && snapshot.memberPath===memberPath && !scopeSignal.aborted ? snapshot : undefined;
  const data = ownSnapshot?.key===key ? ownSnapshot.data : undefined, counts=ownSnapshot?.data.counts;
  const exactRecord = query.sourceRow === undefined ? undefined : data?.items.find(row => row.source_row === query.sourceRow)?.record_no;
  const focusRecord = exactRecord ?? data?.issue?.record_no;
  const error = failure?.key===key ? failure.message : '';
  const issuePlaceholder = !counts && !error && reserveIssueBar;
  const hide = () => {clearTimeout(timer.current);pinned.current=false;setPopover(undefined);};
  useEffect(()=>()=>clearTimeout(timer.current),[]);
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
      if (query.sourceRow !== undefined && (value.issue || value.items.filter(row => row.source_row === query.sourceRow).length !== 1)) {
        throw new Error('来源记录与定位行不一致');
      }
      setSnapshot({client,sourceId,memberPath,key,data:value});
    }).catch(reason=>{if(active&&!controller.signal.aborted&&reason.name!=='AbortError')setFailure({key,message:reason.message});});
    return ()=>{active=false;scopeSignal.removeEventListener('abort',revoke);unlink();controller.abort();};
  },[client,scopeSignal,sourceId,memberPath,key,reload]);
  useLayoutEffect(()=>{
    if (focusRecord === undefined || scopeSignal.aborted) return;
    let settled=0;
    const frame=requestAnimationFrame(()=>{settled=requestAnimationFrame(()=>{
      const viewport=section.current?.querySelector<HTMLElement>('.ft-review-scroll');
      const row=section.current?.querySelector<HTMLElement>(`tr[data-record="${focusRecord}"]`);
      if (!viewport||!row||scopeSignal.aborted) return;
      const cell = (exactRecord === undefined && row.querySelector<HTMLElement>('[data-problem="true"]') || row.children[0]) as HTMLElement;
      if (exactRecord !== undefined) row.focus({ preventScroll: true });
      const bounds=viewport.getBoundingClientRect(),rect=cell.getBoundingClientRect();
      const header=viewport.querySelector('thead')?.getBoundingClientRect().height||32;
      const fixed=cell===row.children[0]?0:row.children[0].getBoundingClientRect().width;
      const context=Math.max(0,(viewport.clientHeight-header-rect.height)/2);
      viewport.scrollTo?.({top:Math.max(0,viewport.scrollTop+rect.top-bounds.top-header-context),
        left:rect.right>bounds.right ? viewport.scrollLeft+rect.right-bounds.right+12 : rect.left<bounds.left+fixed ? viewport.scrollLeft+rect.left-bounds.left-fixed-12 : viewport.scrollLeft,
        behavior:window.matchMedia?.('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    });});
    return ()=>{cancelAnimationFrame(frame);cancelAnimationFrame(settled);};
  },[data,exactRecord,focusRecord,scopeSignal]);
  if(scopeSignal.aborted)return null;
  const covered=new Set(baseColumns.map(column=>column.field));covered.add('counter_name');covered.add('counter_account');
  const coveredByBase = (cell: ImportOriginalCell) => cell.fields.some(field => covered.has(field));
  const knownColumns=discovered.current.cells;
  for (const row of data?.items || []) {
    for (const cell of row.original || []) {
      const index = knownColumns.findIndex(existing => existing.column === cell.column && existing.header === cell.header);
      if (index < 0) knownColumns.push({...cell, value: ''});
      else if (coveredByBase(knownColumns[index]) && !coveredByBase(cell)) {
        knownColumns[index] = {...cell, value: ''};
      }
    }
  }
  const sourceOnly=counts?.accepted===0&&knownColumns.length>0;
  const base=sourceOnly?[baseColumns[0]]:baseColumns;
  const extras=sourceOnly?knownColumns:knownColumns.filter(cell=>!coveredByBase(cell));
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
        const located = row.record_no === exactRecord;
        return <tr key={row.record_no} data-record={row.record_no} data-focused={row.record_no===focusRecord} data-review={bad||undefined}
          tabIndex={located ? -1 : undefined} aria-label={located ? `原始第 ${row.source_row} 行` : undefined}>
          {columns.map((column,index)=>{
            if(index===0)return <td key="row" className="ft-review-row-number" data-fit-cell="true">{bad&&<button type="button" className="ft-row-issue" aria-label={`原始第 ${row.source_row} 行：${reasons(row).map(feedbackReason).join('；')}`} onPointerEnter={event=>show(event.currentTarget,row)} onFocus={event=>show(event.currentTarget,row)} onPointerLeave={leave} onBlur={event=>{delete event.currentTarget.dataset.ftValueDismissed;if(!(event.relatedTarget as HTMLElement|null)?.closest('.ft-value-popover'))hide();}} onClick={event=>show(event.currentTarget,row,true)}><ImportIcon name="import-circle-alert"/></button>}<ImportCellValue>{row.source_row}</ImportCellValue>{row.disposition==='duplicate'&&<span className="ft-row-disposition">重复</span>}{!bad&&!['imported','duplicate'].includes(row.disposition)&&<span className="ft-row-disposition">{row.disposition==='removed'?'已移除':'未计入'}</span>}</td>;
            if(!values&&!row.original)return index===1?<td key="unavailable" colSpan={columns.length-1}>原始单元格不可用</td>:null;
            const original=column.index===-2?row.original?.find(cell=>cell.column===extras[index-base.length].column&&cell.header===extras[index-base.length].header&&(sourceOnly||!coveredByBase(cell))):undefined;
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
