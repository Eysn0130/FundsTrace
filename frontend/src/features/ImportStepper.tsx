import type { ImportItem } from './import-workflow.mjs';
import { ImportIcon, ImportProgress } from './ImportTree';

function needsAttention(item: ImportItem) {
  const result = item.job?.result;
  if (item.phase === 'failed' || item.phase === 'done' && item.error) return true;
  if (!result) return false;
  if (result.feedback) return (result.feedback.unresolved ?? 0) + (result.feedback.review ?? 0) > 0;
  if (result.table_summary) return result.table_summary.unresolved > 0;
  return result.skipped > 0 || (result.bad_examples?.length ?? 0) > 0;
}

/** The connectors carry stage/confirmed-file progress; no elapsed-time weighting. */
export function ImportStepper({ items, processing, total, confirmed, animateCompletion = true }: {
  items: ImportItem[]; processing: boolean; total?: number; confirmed: number; animateCompletion?: boolean;
}) {
  const unknown = items.some(item => item.phase === 'unknown' || item.queryingOnly);
  const terminal = items.length > 0 && items.every(item => ['done','failed','skipped'].includes(item.phase));
  const failed = terminal && !items.some(item => item.phase === 'done') && items.some(item => item.phase === 'failed');
  const stopped = terminal && items.every(item => item.phase === 'skipped');
  const attention = !failed && !stopped && items.some(needsAttention);
  const waiting = !processing && !terminal && items.some(item => item.phase === 'ready');
  const active = processing && !unknown;
  const fraction = total !== undefined && total > 0 ? confirmed / total : undefined;
  const value = unknown ? undefined : fraction;
  const outcome = unknown ? 'unknown' : failed ? 'error' : stopped ? 'stopped' : terminal ? attention ? 'attention' : 'complete' : waiting ? 'waiting' : 'active';
  const current = !items.length ? 0 : terminal ? 2 : 1;
  const nodes = [
    {label:'选择文件',icon:items.length ? 'import-circle-check' : 'import-circle-dot',state:items.length ? 'done' : 'active'},
    {label:unknown ? '结果待核对' : waiting ? '等待确认' : '识别与导入',icon:unknown || waiting || !active && !terminal ? 'import-clock' : failed ? 'import-circle-x' : terminal ? 'import-circle-check' : 'import-loader',state:unknown ? 'unknown' : failed ? 'error' : terminal ? 'done' : active ? 'active' : items.length ? 'waiting' : 'pending'},
    {label:failed ? '导入失败' : stopped ? '已停止' : terminal ? attention ? '完成，待核对' : '导入完成' : '导入结果',icon:failed ? 'import-circle-x' : stopped ? 'import-circle-minus' : terminal ? attention ? 'import-circle-alert' : 'import-circle-check' : 'import-circle-filled',state:terminal ? outcome : 'pending'},
  ];
  return <ol className="ft-import-steps" aria-label="导入步骤" data-outcome={outcome} data-animate-completion={terminal && animateCompletion || undefined}>{nodes.map((node,index)=><li key={index} data-state={node.state} aria-current={index === current ? 'step' : undefined}>
    {index > 0 && <div className="ft-step-connector" data-connector={index}>
      {index === 1 ? <span className="ft-stage-track" aria-hidden="true"><span style={{transform:`scaleX(${items.length ? 1 : 0})`}} /></span>
        : <ImportProgress label={unknown ? '导入处理进度，结果待核对' : '导入处理进度'} value={value} visualValue={unknown ? fraction : value} active={active} complete={terminal && !failed && !stopped && animateCompletion} />}
    </div>}
    <ImportIcon key={node.icon} name={node.icon} className={`ft-step-marker ${node.icon === 'import-loader' ? 'ft-step-spinner' : ''}`} /><span>{node.label}</span>
  </li>)}</ol>;
}
