import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ImportReport, ImportReportAccountOptions } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsTrendSelectionV1 } from '../panel/host';
import { linkAbortSignal } from '../panel/cancellation';
import { ImportIcon } from './ImportTree';

type Selection = { timelineScope: 'all' | 'selected' | 'except'; timelineAccounts: string[] };
export function ImportTrendFilter({ data, client, sourceId, scopeSignal, disabled, onChange }: {
  data: ImportReport; client: FundsCaseClientV1; sourceId: string; scopeSignal: AbortSignal; disabled?: boolean;
  onChange: (value: FundsTrendSelectionV1) => void;
}) {
  const [open,setOpen]=useState(false),[search,setSearch]=useState(''),[page,setPage]=useState(1);
  const [draft,setDraft]=useState<Selection>({timelineScope:'all',timelineAccounts:[]});
  const [collapsed,setCollapsed]=useState<Set<string>>(new Set());
  const [snapshot,setSnapshot]=useState<{key:string;value:ImportReportAccountOptions}>(),[error,setError]=useState('');
  const [placement,setPlacement]=useState({above:false,maxHeight:430,left:0,width:304});
  const root=useRef<HTMLSpanElement>(null),trigger=useRef<HTMLButtonElement>(null),input=useRef<HTMLInputElement>(null),id=useId();
  const key=JSON.stringify([client.binding.scopeKey,client.binding.generation,sourceId,data.member_path,data.analysis_revision,data.currency,data.account,search,page]);
  const options=snapshot?.key===key ? snapshot.value : undefined;
  useLayoutEffect(()=>{
    if(!open||!root.current)return;
    const container=root.current.closest('.ft-report-content');
    const position=()=>{
      if(!root.current)return;
      const anchor=root.current.getBoundingClientRect(),bounds=container?.getBoundingClientRect(),header=container?.querySelector('.ft-inspector-title')?.getBoundingClientRect();
      const below=(bounds?.bottom??window.innerHeight)-anchor.bottom-8,above=anchor.top-(header?.bottom??bounds?.top??0)-8;
      const up=below<250&&above>below,maxHeight=Math.max(96,Math.min(430,up?above:below));
      const pane=root.current.closest('.ft-trend-section')?.getBoundingClientRect()??bounds;
      const width=Math.min(304,Math.max(160,(pane?.width??window.innerWidth)-16)),left=Math.min(0,(pane?.right??window.innerWidth)-8-anchor.left-width);
      setPlacement(current=>current.above===up&&Math.abs(current.maxHeight-maxHeight)<.5&&current.left===left&&current.width===width?current:{above:up,maxHeight,left,width});
    };
    position();container?.addEventListener('scroll',position,{passive:true});window.addEventListener('resize',position);
    return()=>{container?.removeEventListener('scroll',position);window.removeEventListener('resize',position);};
  },[open,key,options?.items.length]);
  useEffect(()=>{
    if(!open || !client.importReportAccounts || data.currency===null)return;
    const controller=new AbortController(),unlink=linkAbortSignal(scopeSignal,controller);setError('');
    const timer=setTimeout(()=>{
      client.importReportAccounts!(sourceId,{memberPath:data.member_path,expectedRevision:data.analysis_revision,currency:data.currency!,account:data.account??undefined,search,page},controller.signal).then(value=>{
        if(controller.signal.aborted)return;
        if(value.case_id!==client.binding.caseId || value.source_id!==sourceId || value.member_path!==data.member_path || value.analysis_revision!==data.analysis_revision)throw new Error('账户范围已变化，请重新打开筛选');
        setSnapshot({key,value});
      }).catch(reason=>{if(!controller.signal.aborted)setError(reason.message);});
    },150);
    return()=>{clearTimeout(timer);unlink();controller.abort();};
  },[open,key,client,scopeSignal]);
  useEffect(()=>{
    if(!open)return;
    input.current?.focus({preventScroll:true});
    const outside=(event:PointerEvent)=>{if(event.target instanceof Node&&!root.current?.contains(event.target))setOpen(false);};
    document.addEventListener('pointerdown',outside);
    return()=>document.removeEventListener('pointerdown',outside);
  },[open]);
  if(!client.importReportAccounts)return null;
  const close=()=>{setOpen(false);trigger.current?.focus({preventScroll:true});};
  const checked=(account:string)=>draft.timelineScope==='all'||(draft.timelineScope==='selected'?draft.timelineAccounts.includes(account):!draft.timelineAccounts.includes(account));
  const toggle=(accounts:string[],value:boolean)=>{const current=draft;
    const mode=current.timelineScope==='all'?'except':current.timelineScope,ids=new Set(current.timelineAccounts);
    for(const account of accounts){if(value===(mode==='selected'))ids.add(account);else ids.delete(account);}
    if(ids.size>200){setError('一次最多调整200个账户，可按户名或账卡号缩小范围');return;}
    setError('');setDraft({timelineScope:mode,timelineAccounts:[...ids]});
  };
  const groups=new Map<string,NonNullable<typeof options>['items']>();
  for(const row of options?.items||[]){const name=row.names[0]||'户名未载明';groups.set(name,[...(groups.get(name)||[]),row]);}
  const scope=data.timeline_scope,active=scope&&(scope.mode!=='all'||scope.search);
  const total=options?.total,selectedCount=total===undefined?undefined:draft.timelineScope==='all'?total:draft.timelineScope==='selected'?draft.timelineAccounts.length:Math.max(0,total-draft.timelineAccounts.length);
  const allChecked=total===undefined?draft.timelineScope==='all':total>0&&selectedCount===total;
  return <span className="ft-trend-filter" ref={root} onBlur={event=>{if(open&&!event.currentTarget.contains(event.relatedTarget))setOpen(false);}} onKeyDown={event=>{if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();close();}}}>
    <button type="button" ref={trigger} disabled={disabled||data.currency===null} className="ft-report-filter-button" aria-haspopup="dialog" aria-expanded={open} aria-controls={id} aria-label={`时间变化账户筛选：${active?`已选${scope.account_count}个账户`:'全部账户'}`} onClick={()=>{
      if(open){close();return;}setDraft({timelineScope:scope?.mode||'all',timelineAccounts:[...(scope?.accounts||[])]});setSearch(scope?.search||'');setPage(1);setCollapsed(new Set());setOpen(true);
    }}><span>{active?`已选${scope.account_count}个账户`:'账户筛选'}</span><ImportIcon name="chevron-down"/></button>
    {open&&<span className="ft-trend-filter-popover" data-placement={placement.above?'above':'below'} style={{maxHeight:placement.maxHeight,left:placement.left,right:'auto',width:placement.width}} role="dialog" id={id} aria-label="时间变化账户筛选">
      <label className="ft-trend-search"><ImportIcon name="search"/><input ref={input} aria-label="搜索并筛选户名或账卡号" maxLength={200} placeholder="搜索户名或账卡号" value={search} onChange={event=>{setSearch(event.target.value);setPage(1);setCollapsed(new Set());setDraft({timelineScope:'all',timelineAccounts:[]});}}/></label>
      <label className="ft-trend-check"><input type="checkbox" ref={node=>{if(node)node.indeterminate=!allChecked&&!!selectedCount;}} checked={allChecked} aria-checked={!allChecked&&!!selectedCount?'mixed':allChecked} onChange={event=>setDraft({timelineScope:event.target.checked?'all':'selected',timelineAccounts:[]})}/><span>{search?'全部匹配账户':'全部账户'}</span><small>{options?`${options.total}个`:'…'}</small></label>
      <span className="ft-trend-options">{[...groups].map(([name,rows],index)=><span className="ft-trend-option-group" key={name}>
        <span className="ft-trend-group-heading"><button type="button" className="ft-trend-expand" aria-label={`${collapsed.has(name)?'展开':'收起'}${name}账户`} aria-expanded={!collapsed.has(name)} aria-controls={`${id}-group-${index}`} onClick={()=>setCollapsed(current=>{const next=new Set(current);if(next.has(name))next.delete(name);else next.add(name);return next;})}><ImportIcon name={collapsed.has(name)?'chevron-right':'chevron-down'}/></button><label className="ft-trend-check"><input type="checkbox" aria-label={`选择本页${name}账户`} ref={node=>{if(node)node.indeterminate=rows.some(row=>checked(row.account))&&!rows.every(row=>checked(row.account));}} aria-checked={rows.some(row=>checked(row.account))&&!rows.every(row=>checked(row.account))?'mixed':rows.every(row=>checked(row.account))} checked={rows.every(row=>checked(row.account))} onChange={event=>toggle(rows.map(row=>row.account),event.target.checked)}/><span title={rows.flatMap(row=>row.names).join('、')}>{name}</span><small>本页{rows.length}个</small></label></span>
        <span className="ft-trend-group-children" id={`${id}-group-${index}`} hidden={collapsed.has(name)}>{rows.map(row=><label className="ft-trend-check ft-trend-account" key={row.account}><input type="checkbox" aria-label={`选择账户${row.account_number}`} checked={checked(row.account)} onChange={event=>toggle([row.account],event.target.checked)}/><span title={[...row.account_numbers,...row.cards].join('、')}>{row.account_number||'账号未载明'}</span></label>)}</span>
      </span>)}{!options&&!error&&<span role="status">正在读取账户…</span>}{options&&!options.items.length&&<span>未找到匹配账户</span>}</span>
      {error&&<span className="ft-report-action-error" role="alert">{error}</span>}
      {options&&options.total>options.page_size&&<span className="ft-report-filter-pages"><button type="button" disabled={page===1} aria-label="上一页筛选账户" onClick={()=>setPage(p=>p-1)}><ImportIcon name="chevron-left"/></button><span>{page} / {Math.ceil(options.total/options.page_size)}</span><button type="button" disabled={page*options.page_size>=options.total} aria-label="下一页筛选账户" onClick={()=>setPage(p=>p+1)}><ImportIcon name="chevron-right"/></button></span>}
      <span className="ft-trend-filter-footer"><button type="button" className="button primary" onClick={()=>{onChange({...draft,timelineSearch:search.trim()});close();}}>应用</button><button type="button" className="ft-text-button" onClick={()=>{onChange({timelineScope:'all',timelineAccounts:[],timelineSearch:''});close();}}>重置</button></span>
    </span>}
  </span>;
}
