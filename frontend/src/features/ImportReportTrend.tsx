import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ImportReportTimeBucket } from '../../../contracts/api-v1';
import { displayTime, formatMoney, reportMoneyUnit } from '../lib/format';
import { ImportCellValue, ImportTable } from './ImportTable';
import { moneyUnits, plotRatio, unitsMoney } from './report-geometry';

export function reportTimeBasis(basis: string) {
  return ({ source_wall_clock: '来源壁钟时间（未标时区）', wall_clock: '来源壁钟时间（未标时区）', unqualified: '来源壁钟时间（未标时区）', mixed: '混合时间域（不可直接排序）', utc: 'UTC', instant: 'UTC' } as Record<string, string>)[basis] || basis;
}
export type TrendMode = 'bar' | 'line' | 'combined';
export function trendPeriod(row: ImportReportTimeBucket, grain: string) {
  const date=row.start.slice(0,10),year=date.slice(0,4),month=String(parseInt(date.slice(5,7),10)),day=String(parseInt(date.slice(8,10),10));
  return grain==='年'?`${year}年`:grain==='月'?`${year}年${month}月`:grain==='日'?`${year}年${month}月${day}日`:displayTime(row.start);
}

/** Calendar cells include explicit gaps; financial values are never imputed. */
export function ImportReportTrend({ buckets, grain, currency, mode='combined', disabled=false, onOpenRecords }: {
  buckets: ImportReportTimeBucket[]; grain: string; currency: string | null; mode?: TrendMode; disabled?: boolean;
  onOpenRecords?: (bucket: ImportReportTimeBucket, direction?: 'in'|'out')=>void;
}) {
  const moneyLabel=reportMoneyUnit(currency);
  const [basis,setBasis]=useState(''),[hover,setHover]=useState<number>(),[pinned,setPinned]=useState<number>();
  const [valuesOpen,setValuesOpen]=useState(true),canvas=useRef<HTMLCanvasElement>(null),plot=useRef<HTMLDivElement>(null),table=useRef<HTMLDivElement>(null);
  const geometry=useRef({left:0,right:0,slot:1,width:0});
  const domains=[...new Set(buckets.map(row=>row.time_basis))],domain=domains.find(value=>value===basis)||domains[0];
  const rows=buckets.filter(row=>row.time_basis===domain),signature=JSON.stringify(rows),point=hover??pinned;
  const selected=point===undefined?undefined:rows[point];
  const distinctCounts=rows.filter(row=>row.has_records!==false).every(row=>row.transaction_count!=null);
  const countLabel=distinctCounts?'交易笔数':'收支笔次';
  const countValue=(row:ImportReportTimeBucket)=>distinctCounts?row.transaction_count??0:row.in_count+row.out_count;
  useEffect(()=>{setHover(undefined);setPinned(undefined);},[signature,domain]);
  useEffect(()=>{if(disabled){setHover(undefined);setPinned(undefined);}},[disabled]);
  useLayoutEffect(()=>{
    const element=canvas.current;if(!element||!rows.length)return;
    const draw=()=>{
      const ctx=element.getContext('2d');if(!ctx)return;
      const width=element.clientWidth,height=element.clientHeight,density=window.devicePixelRatio||1;
      element.width=Math.round(width*density);element.height=Math.round(height*density);ctx.scale(density,density);
      const style=getComputedStyle(element),muted=style.getPropertyValue('--ft-muted'),line=style.getPropertyValue('--ft-line');
      const values=rows.flatMap(r=>r.has_records===false?[]:[r.in_amount,r.out_amount].flatMap(v=>v===null?[]:[moneyUnits(v)]));
      const high=values.reduce((a,b)=>a>b?a:b,0n),low=values.reduce((a,b)=>a<b?a:b,0n),maxAbs=high>-low?high:-low;
      const scale=maxAbs>=10000000000n?100000000n:maxAbs>=1000000n?10000n:1n,unit=scale===100000000n?`亿${moneyLabel}`:scale===10000n?`万${moneyLabel}`:moneyLabel;
      const nice=(value:bigint)=>{if(value===0n)return 0n;if(value<=4n)return 4n;const magnitude=10n**BigInt(value.toString().length-1);return [10n,12n,15n,20n,25n,50n,75n,100n].map(n=>(magnitude*n+9n)/10n).find(n=>n>=value)!;};
      const top=high===low?100n:nice(high),bottom=low<0n?-nice(-low):0n;
      const label=(v:bigint)=>formatMoney(unitsMoney(v/scale));
      const left=Math.min(138,Math.max(64,Math.max(label(top).length,label(bottom).length)*6.2+12)),right=24,slot=Math.max(1,(width-left-right)/rows.length);
      geometry.current={left,right:width-right,slot,width};
      const x=(i:number)=>left+(i+.5)*slot,upperTop=22,upperBottom=mode==='combined'?177:height-29;
      const y=(v:bigint)=>upperTop+(1-plotRatio(v,bottom,top))*(upperBottom-upperTop),countTop=211,countBottom=height-29;
      const countMax=Math.max(2,...rows.map(countValue)),cy=(n:number)=>countBottom-n/countMax*(countBottom-countTop);
      ctx.font=`11px ${style.fontFamily}`;ctx.fillStyle=muted;ctx.textAlign='left';ctx.fillText(`金额（${unit}）`,4,12);
      ctx.strokeStyle=line;ctx.lineWidth=.7;
      for(let i=0;i<=4;i++){const v=bottom+(top-bottom)*BigInt(i)/4n,py=y(v);ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(width-right,py);ctx.stroke();ctx.textAlign='right';ctx.fillText(label(v),left-8,py+4);}
      let gapStart=-1;
      const gap=(end:number)=>{const start=left+gapStart*slot,w=(end-gapStart)*slot;ctx.fillStyle='rgba(140,140,140,.08)';ctx.fillRect(start,upperTop,w,upperBottom-upperTop);if(mode==='combined')ctx.fillRect(start,countTop,w,countBottom-countTop);if(w>145){ctx.fillStyle=muted;ctx.textAlign='center';ctx.fillText('本次资料未见纳入记录',start+w/2,(upperTop+upperBottom)/2);}gapStart=-1;};
      rows.forEach((row,i)=>{if(row.has_records===false){if(gapStart<0)gapStart=i;}else if(gapStart>=0)gap(i);});if(gapStart>=0)gap(rows.length);
      for(const [field,color]of [['in_amount','#37c9a2'],['out_amount','#6484ff']]as const){
        ctx.strokeStyle=color;ctx.fillStyle=color;ctx.lineWidth=2;
        if(mode!=='line')rows.forEach((row,i)=>{const v=row[field];if(row.has_records===false||v===null)return;const amount=moneyUnits(v);if(amount===0n)return;const bar=Math.min(24,slot*.28),px=x(i)+(field==='in_amount'?-bar-1:1),zero=y(0n),py=y(amount);ctx.fillRect(px,Math.min(py,zero),bar,Math.max(1,Math.abs(py-zero)));});
        else {let connected=false;ctx.beginPath();rows.forEach((row,i)=>{const v=row[field];if(row.has_records===false||v===null){connected=false;return;}const px=x(i),py=y(moneyUnits(v));if(connected)ctx.lineTo(px,py);else ctx.moveTo(px,py);connected=true;});ctx.stroke();rows.forEach((row,i)=>{const v=row[field];if(row.has_records!==false&&v!==null&&(rows.length<=24||point===i)){ctx.beginPath();ctx.arc(x(i),y(moneyUnits(v)),point===i?4:2.5,0,Math.PI*2);ctx.fill();}});}
      }
      if(mode==='combined'){
        ctx.fillStyle=muted;ctx.textAlign='left';ctx.fillText(countLabel,4,countTop-9);ctx.strokeStyle=line;ctx.lineWidth=.7;
        for(const v of [0,Math.ceil(countMax/2),countMax]){const py=cy(v);ctx.beginPath();ctx.moveTo(left,py);ctx.lineTo(width-right,py);ctx.stroke();ctx.textAlign='right';ctx.fillText(String(v),left-8,py+4);}
        ctx.strokeStyle=muted;ctx.fillStyle=muted;ctx.lineWidth=1.5;let connected=false;ctx.beginPath();rows.forEach((row,i)=>{if(row.has_records===false){connected=false;return;}const py=cy(countValue(row));if(connected)ctx.lineTo(x(i),py);else ctx.moveTo(x(i),py);connected=true;});ctx.stroke();rows.forEach((row,i)=>{if(row.has_records!==false&&(rows.length<=24||point===i)){ctx.beginPath();ctx.arc(x(i),cy(countValue(row)),point===i?4:2.5,0,Math.PI*2);ctx.fill();}});
      }
      ctx.fillStyle=muted;ctx.textAlign='center';const stride=Math.max(1,Math.ceil(rows.length*78/Math.max(1,width-left-right)));
      rows.forEach((row,i)=>{if(i!==0&&i!==rows.length-1&&(i%stride||rows.length-1-i<stride))return;const text=grain==='年'?row.start.slice(0,4):grain==='月'?row.start.slice(0,7):grain==='日'?row.start.slice(0,10):row.start.slice(11,16);ctx.fillText(text,x(i),height-7);});
      if(point!==undefined&&rows[point]){ctx.save();ctx.strokeStyle=muted;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x(point),upperTop);ctx.lineTo(x(point),height-24);ctx.stroke();ctx.restore();}
    };
    draw();const observer=typeof ResizeObserver==='function'?new ResizeObserver(draw):undefined;observer?.observe(element);return()=>observer?.disconnect();
  },[signature,point,mode,grain,moneyLabel,countLabel]);
  useEffect(()=>{
    if(point===undefined)return;const viewport=table.current?.querySelector<HTMLElement>('.ft-grid-scroll'),row=table.current?.querySelector<HTMLElement>(`tr[data-point="${point}"]`);
    if(!viewport||!row)return;const r=row.getBoundingClientRect(),v=viewport.getBoundingClientRect();if(r.bottom>v.bottom)viewport.scrollTop+=r.bottom-v.bottom;else if(r.top<v.top+28)viewport.scrollTop-=v.top+28-r.top;
  },[point,valuesOpen]);
  if(!rows.length)return <p className="ft-report-empty">当前范围没有可按时间归集的已纳入记录。</p>;
  const hit=(clientX:number)=>{const rect=canvas.current?.getBoundingClientRect(),g=geometry.current;if(!rect?.width)return undefined;const x=(clientX-rect.left)*g.width/rect.width;return x<g.left||x>g.right?undefined:Math.max(0,Math.min(rows.length-1,Math.floor((x-g.left)/g.slot)));};
  const tipX=point===undefined?8:Math.max(8,Math.min(geometry.current.width-248,geometry.current.left+(point+.5)*geometry.current.slot+10));
  return <div className="ft-report-trend" data-mode={mode} aria-busy={disabled} onKeyDown={event=>{if(event.key==='Escape'&&point!==undefined){event.preventDefault();event.stopPropagation();if((event.target as Element).closest('.ft-report-chart-tip'))plot.current?.focus({preventScroll:true});setHover(undefined);setPinned(undefined);}}}>
    <div className="ft-report-chart-heading"><span title={reportTimeBasis(domain)}>{grain} · {currency||'币种未明确'} · {domain==='unqualified'?'按原始记载日期':reportTimeBasis(domain)}</span>{domains.length>1&&<select disabled={disabled} aria-label="趋势时间域" value={domain} onChange={event=>setBasis(event.target.value)}>{domains.map(v=><option key={v} value={v}>{reportTimeBasis(v)}</option>)}</select>}<div className="ft-report-legend"><span data-series="in">进账</span><span data-series="out">出账</span>{mode==='combined'&&<span>{countLabel}</span>}</div></div>
    <div className="ft-report-plot" ref={plot} tabIndex={disabled?-1:0} role="group" aria-label="资金变化图，左右方向键查看各时段" onKeyDown={event=>{
      if(disabled||event.target!==event.currentTarget)return;
      if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();setHover(event.key==='Home'?0:event.key==='End'?rows.length-1:Math.max(0,Math.min(rows.length-1,(point??-1)+(event.key==='ArrowLeft'?-1:1))));}
      else if((event.key==='Enter'||event.key===' ')&&point!==undefined){event.preventDefault();setPinned(point);}
    }} onPointerMove={event=>{if(!disabled&&!(event.target as Element).closest('.ft-report-chart-tip'))setHover(hit(event.clientX));}} onPointerLeave={event=>{if(!event.currentTarget.contains(document.activeElement))setHover(undefined);}} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget))setHover(undefined);}} onClick={event=>{if(!disabled&&!(event.target as Element).closest('.ft-report-chart-tip'))setPinned(hit(event.clientX));}}>
      <canvas ref={canvas} aria-label={`${currency||'未知币种'}进出账趋势，共${rows.length}个日历时段`}/>
      {!disabled&&selected&&<div className="ft-report-chart-tip" role="status" style={{left:`clamp(8px, ${tipX}px, calc(100% - 248px))`,right:'auto'}}><strong>{trendPeriod(selected,grain)}</strong>{selected.has_records===false?<span>本次资料未见纳入记录</span>:<><span data-series="in">进账 {formatMoney(selected.in_amount)} {moneyLabel} · {selected.in_count} 笔</span><span data-series="out">出账 {formatMoney(selected.out_amount)} {moneyLabel} · {selected.out_count} 笔</span><span>收支差额 {formatMoney(selected.net_amount)} {moneyLabel}</span>{selected.transaction_count!=null&&selected.transaction_count!==selected.in_count+selected.out_count&&<span>本期交易 {selected.transaction_count} 笔</span>}{onOpenRecords&&<button type="button" className="ft-text-button" onClick={()=>onOpenRecords(selected)}>查看本期明细</button>}</>}</div>}
    </div>
    {pinned!==undefined&&rows[pinned]&&<div className="ft-trend-pinned"><span>已选 {trendPeriod(rows[pinned],grain)}</span><button className="ft-text-button" type="button" onClick={()=>{setPinned(undefined);setHover(undefined);}}>取消选中</button></div>}
    <details className="ft-report-trend-values" open={valuesOpen} onToggle={event=>setValuesOpen(event.currentTarget.open)}><summary>{grain==='月'?'月度':'期间'}明细 · {rows.filter(r=>r.has_records!==false).length} 个时段</summary><div ref={table}><ImportTable fillToSlot autoSize={valuesOpen} className="ft-report-grid" label="时间变化明细" headings={['期间',`进账金额（${moneyLabel}）`,'笔数',`出账金额（${moneyLabel}）`,'笔数',`收支差额（${moneyLabel}）`,...(onOpenRecords?['来源']:[])]} widths={[160,170,64,170,64,170,...(onOpenRecords?[120]:[])]} moneyColumns={[1,3,5]}><tbody>{rows.map((row,index)=>row.has_records===false?null:<tr key={row.start} data-point={index} aria-selected={point===index}>
      <td><button type="button" disabled={disabled} className="ft-report-cell-button" aria-label={`选择${trendPeriod(row,grain)}`} aria-pressed={pinned===index} onClick={()=>{setPinned(index);setHover(undefined);}}><ImportCellValue>{trendPeriod(row,grain)}</ImportCellValue></button></td>
      <td className="ft-money"><button type="button" disabled={disabled||!onOpenRecords||!row.in_count} className="ft-report-cell-button" onClick={()=>onOpenRecords?.(row,'in')}><ImportCellValue>{formatMoney(row.in_amount)}</ImportCellValue></button></td><td>{row.in_count}</td>
      <td className="ft-money"><button type="button" disabled={disabled||!onOpenRecords||!row.out_count} className="ft-report-cell-button" onClick={()=>onOpenRecords?.(row,'out')}><ImportCellValue>{formatMoney(row.out_amount)}</ImportCellValue></button></td><td>{row.out_count}</td><td className="ft-money"><ImportCellValue>{formatMoney(row.net_amount)}</ImportCellValue></td>
      {onOpenRecords&&<td><button type="button" disabled={disabled} className="ft-text-button" onClick={()=>onOpenRecords(row)}>查看本期明细</button></td>}
    </tr>)}</tbody></ImportTable></div></details>
  </div>;
}
