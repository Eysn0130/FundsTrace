import { useEffect, useRef, useState } from 'react';
import type { FlowSourceList } from '../../../contracts/api-v1';
import type { FundsCaseClientV1, FundsHostActionsV1 } from '../panel/host';
import { displayOperationalTime, formatMoney } from '../lib/format';
import { linkAbortSignal } from '../panel/cancellation';

export function FlowSources({ caseId, flowId, client, scopeSignal, actions }: { caseId: string; flowId: string; client: FundsCaseClientV1; scopeSignal: AbortSignal; actions?: FundsHostActionsV1 }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<FlowSourceList | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [downloadError, setDownloadError] = useState('');
  const [downloading, setDownloading] = useState('');
  const lifetime = useRef(new AbortController());
  useEffect(() => {
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
    const controller = lifetime.current;
    const unlink = linkAbortSignal(scopeSignal, controller);
    return () => { unlink(); controller.abort(); };
  }, [scopeSignal]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    const unlink = linkAbortSignal(scopeSignal, controller);
    if (controller.signal.aborted) return unlink;
    setLoading(true); setData(null); setError('');
    client.sources(flowId, page, controller.signal).then(value => {
      if (!active || controller.signal.aborted) return;
      if (value.case_id !== caseId || value.flow_id !== flowId) throw new Error('来源记录与当前案件流水不一致。');
      setData(value);
    }).catch(reason => { if (active && !controller.signal.aborted && reason.name !== 'AbortError') setError(reason.message); })
      .finally(() => { if (active && !controller.signal.aborted) setLoading(false); });
    return () => { active = false; unlink(); controller.abort(); };
  }, [client, caseId, flowId, page, reload, scopeSignal]);
  const download = async (sourceId: string, filename: string) => {
    if (!actions?.downloadSource) return;
    const signal = lifetime.current.signal;
    if (signal.aborted || scopeSignal.aborted) return;
    setDownloading(sourceId); setDownloadError('');
    try { await actions.downloadSource({ sourceId, filename }, signal); }
    catch (reason) { if (!signal.aborted) setDownloadError(`${filename}：${(reason as Error).message}`); }
    finally { if (!signal.aborted) setDownloading(''); }
  };
  return <section className="source-section" aria-label="流水来源">
    <div className="source-heading"><h3>来源追溯</h3><span className="muted">{data ? `${data.total} 条来源记录` : '按本案流水查询'}</span></div>
    {loading && <p role="status" className="muted">正在加载来源…</p>}
    {error && <div role="alert" className="error-banner">{error}<button onClick={() => setReload(value => value + 1)}>重试来源查询</button></div>}
    {downloadError && <div role="alert" className="error-banner">{downloadError}</div>}
    {data && !data.total && <p className="source-empty">未记录来源。历史流水无法据此还原原始文件或源行位置。</p>}
    {data?.items.map(source => <article className="source-record" key={`${source.source_id}:${source.source_row}:${source.observation_flow_id || flowId}`}>
      <div className="source-record-heading"><strong>{source.filename}</strong><span className="status-label">{source.disposition === 'duplicate' ? '重复记录关联' : '首次导入关联'}</span></div>
      {source.observation_flow_id && <p>来源记录 #{source.observation_flow_id} · 本方 {source.observation_account || '未提供'} · 金额 {source.observation_amount == null ? '未知' : formatMoney(source.observation_amount)} {source.observation_currency || '币种待定'} · 原余额 {source.observation_balance == null ? '未提供' : formatMoney(source.observation_balance)}</p>}
      <p>{source.row_kind === 'table_record'
        ? source.record_details?.sheet
          ? `工作表「${source.record_details.sheet}」 · 第 ${source.member_row} 行`
          : `来源成员「${source.member_path}」 · CSV 第 ${source.member_row} 条逻辑记录（含表头和空记录）`
        : source.row_kind === 'archive_record' ? `包成员「${source.member_path}」 · CSV 第 ${source.member_row} 条逻辑记录` : source.row_kind === 'worksheet_row' ? `工作表「${source.sheet}」 · 第 ${source.source_row} 行` : source.row_kind === 'csv_record' ? `CSV 第 ${source.source_row} 条逻辑记录（含表头和空记录）` : `来源位置 ${source.source_row}（行号口径未标定）`}</p>
      <p className="muted">{displayOperationalTime(source.imported_at)} · {source.size_bytes.toLocaleString('zh-CN')} 字节 · 解析器 {source.parser_version}</p>
      <div className="source-digest"><span>SHA-256</span><code>{source.sha256}</code></div>
      {source.origin === 'package' ? <p className="source-note">案件包携带的来源元数据；包内不含原始上传文件。</p>
        : source.source_available ? <div><button className="source-download" disabled={!actions?.downloadSource || downloading === source.source_id} onClick={() => void download(source.source_id, source.filename)}>下载原始文件 <span aria-hidden="true">↓</span></button>{!actions?.downloadSource && <p className="source-note">宿主未提供原件下载能力。</p>}</div>
          : <p className="source-note">原始上传文件当前不可用，保留来源元数据。</p>}
    </article>)}
    {data && data.total > data.page_size && <div className="source-pagination"><button className="button secondary" aria-label="来源上一页" disabled={loading || page <= 1} onClick={() => setPage(value => value - 1)}>上一页</button><span>来源第 {data.page} / {Math.ceil(data.total / data.page_size)} 页</span><button className="button secondary" aria-label="来源下一页" disabled={loading || page * data.page_size >= data.total} onClick={() => setPage(value => value + 1)}>下一页</button></div>}
  </section>;
}
