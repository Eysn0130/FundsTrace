import { useEffect, useRef, useState } from 'react';
import type { ImportPreview, ImportJob, ImportSession } from '../../../contracts/api-v1';
import { Modal } from '../components/Modal';
import type { FundsCaseClientV1, FundsCaseContextV1 } from '../panel/host';
import { formatMoney, displayTime } from '../lib/format';
import { linkAbortSignal, waitForNextPoll } from '../panel/cancellation';

const fieldNames: Record<string, string> = {
  trade_no: '流水号', trade_time: '交易时间', _time2: '时间补充', amount: '交易金额', balance: '交易后余额', currency: '币种',
  payer_name: '付款户名', payer_account: '付款账号', payer_bank: '付款开户行', payee_name: '收款户名', payee_account: '收款账号', payee_bank: '收款开户行',
  self_account: '本方账号', self_name: '本方户名', counter_account: '对手账号', counter_name: '对手户名', counter_bank: '对手开户行',
  direction: '收支方向', income_amt: '收入金额', expense_amt: '支出金额', summary: '摘要', channel: '渠道',
};
const fieldsA = ['trade_time', '_time2', 'payer_account', 'payer_name', 'payee_account', 'payee_name', 'amount', 'trade_no', 'balance', 'currency', 'self_account', 'self_name', 'summary'];
const fieldsB = ['trade_time', '_time2', 'self_account', 'self_name', 'counter_account', 'counter_name', 'direction', 'amount', 'income_amt', 'expense_amt', 'trade_no', 'balance', 'currency', 'summary'];
const modeFields = {
  A: new Set([...fieldsA, 'payer_bank', 'payee_bank', 'self_name', 'channel', '_time2']),
  B: new Set([...fieldsB, 'counter_bank', 'channel', '_time2']),
};

export function ImportDialog({ target, client, scopeSignal, resume, onClose, onImported }: { target: FundsCaseContextV1; client: FundsCaseClientV1; scopeSignal: AbortSignal; resume?: ImportSession; onClose: () => void; onImported: () => void }) {
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<'A' | 'B'>('A');
  const [marked, setMarked] = useState<string[]>(resume?.request?.mark_accounts ?? []);
  const [selfAccounts] = useState(resume?.request?.self_accounts ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [job, setJob] = useState<ImportJob | null>(null);
  const [dirty, setDirty] = useState(false);
  const [polling, setPolling] = useState(false);
  const alive = useRef(true);
  const lifetime = useRef(new AbortController());
  const operation = useRef(0);
  const selectionInitialized = useRef(resume?.request?.mark_accounts != null);
  const valid = (token: number) => alive.current && token === operation.current && !lifetime.current.signal.aborted && !scopeSignal.aborted;
  const acceptPreview = (value: ImportPreview) => {
    if (value.case_id !== target.caseId || (resume && value.import_id !== resume.import_id)) throw new Error('预览与当前案件导入记录不一致。');
    setPreview(value); setMapping(value.mapping); setMode(value.mode); setDirty(false);
    if (!selectionInitialized.current) { setMarked(value.self_accounts_in_file.map(account => account.account)); selectionInitialized.current = true; }
  };
  const poll = async (importId: string, token: number, signal = lifetime.current.signal) => {
    if (!valid(token) || signal.aborted) return;
    setPolling(true);
    try {
      while (valid(token)) {
        const current = await client.job(importId, signal);
        if (!valid(token)) return;
        if (current.case_id !== target.caseId || current.import_id !== importId) throw new Error('导入进度与当前案件不一致。');
        setJob(current);
        if (current.stage === 'error') throw new Error(current.error || '导入失败，未完成写入。');
        if (current.stage === 'done') { onImported(); return; }
        await waitForNextPoll(300, signal);
      }
    } catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setPolling(false); }
  };
  const restorePreview = async () => {
    if (!resume || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = ++operation.current;
    setBusy(true); setError(''); setJob(null);
    try {
      const value = await client.repreview(resume.import_id, { mode: resume.request?.mode ?? resume.mode, mapping: resume.request?.mapping ?? resume.mapping }, lifetime.current.signal);
      if (valid(token)) acceptPreview(value);
    } catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  useEffect(() => {
    alive.current = true;
    if (lifetime.current.signal.aborted) lifetime.current = new AbortController();
    const controller = lifetime.current;
    const unlink = linkAbortSignal(scopeSignal, controller);
    if (resume && !controller.signal.aborted) {
      if (resume.case_id !== target.caseId || resume.surface !== 'v1') setError('导入记录与当前案件工作区不一致。');
      else if (resume.stage === 'done' || resume.stage === 'parsing' || resume.stage === 'importing') {
        const current: ImportJob = { case_id: target.caseId, import_id: resume.import_id, stage: resume.stage, done: resume.stage === 'done' ? resume.result?.ok ?? 0 : 0, total: resume.result?.total ?? 0, message: resume.stage === 'done' ? '完成' : '正在查询进度…', error: null, result: resume.result };
        setJob(current);
        if (resume.stage !== 'done') void poll(resume.import_id, ++operation.current);
      } else if (resume.source_available && (resume.stage === 'ready' || resume.recoverable)) void restorePreview();
      else setError('原始上传文件不可用于恢复，请重新上传。');
    }
    return () => { alive.current = false; operation.current += 1; unlink(); controller.abort(); };
  }, [scopeSignal]);
  const upload = async (file: File | undefined) => {
    if (!file || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = ++operation.current;
    setBusy(true); setError(''); setJob(null);
    selectionInitialized.current = false;
    try { const value = await client.preview(file, lifetime.current.signal); if (valid(token)) acceptPreview(value); }
    catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  const repreview = async () => {
    if (!preview || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = ++operation.current;
    setBusy(true); setError(''); setJob(null);
    try { const value = await client.repreview(preview.import_id, { mode, mapping }, lifetime.current.signal); if (valid(token)) acceptPreview(value); }
    catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  const commit = async () => {
    if (!preview || dirty || lifetime.current.signal.aborted || scopeSignal.aborted) return;
    const token = ++operation.current;
    setBusy(true); setError('');
    try {
      const started = await client.commit(preview.import_id, { mode, mapping, mark_accounts: marked, self_accounts: selfAccounts }, lifetime.current.signal);
      if (!valid(token)) return;
      if (started.case_id !== target.caseId || started.import_id !== preview.import_id) throw new Error('导入确认与当前案件不一致。');
      setBusy(false);
      await poll(preview.import_id, token);
    } catch (reason) { if (valid(token)) setError((reason as Error).message); }
    finally { if (valid(token)) setBusy(false); }
  };
  const complete = job?.stage === 'done';
  const activeJob = job?.stage === 'parsing' || job?.stage === 'importing';
  const working = busy || polling;
  return <Modal title="导入流水" onClose={onClose} busy={busy}>
    <div className="modal-context">导入到 <strong>{target.name}</strong><span>当前案件内去重</span></div>
    <ol className="import-steps" aria-label="导入步骤"><li className={!preview ? 'active' : 'done'}>1 <span>选择文件</span></li><li className={preview && !job ? 'active' : ''}>2 <span>映射与预览</span></li><li className={job ? 'active' : ''}>3 <span>核对结果</span></li></ol>
    {error && <div role="alert" className="error-banner">{error}{!working && activeJob ? <button onClick={() => { setError(''); void poll(resume?.import_id ?? preview!.import_id, ++operation.current); }}>重新查询进度</button> : !working && job?.stage === 'error' && job.recoverable !== false ? <button onClick={() => void (preview ? repreview() : restorePreview())}>重新预览后重试</button> : !working && resume && !preview && !complete && <button onClick={() => void restorePreview()}>重试恢复预览</button>}</div>}
    {complete ? <div className="import-complete" role="status"><span className="success-symbol">✓</span><h3>流水已写入当前案件</h3><p>新增 <strong>{job.result?.ok ?? 0}</strong> 笔 · 重复 {job.result?.rows_dup ?? 0} 笔 · 跳过 {job.result?.skipped ?? 0} 行</p><p className="muted">批次 {job.result?.batch_id} · {preview?.filename ?? resume?.filename}</p></div>
      : !preview ? resume ? <div className="resume-summary"><strong>{resume.filename}</strong><p className="muted">{working ? activeJob ? '正在查询原任务，关闭窗口后任务仍继续。' : '正在用原始上传与已保存映射重新预览…' : '请核对任务状态后继续。'}</p></div>
        : <label className="file-drop"><span className="upload-symbol">↑</span><strong>{busy ? '正在解析文件…' : '选择 CSV 或 Excel 文件'}</strong><span>支持 UTF-8／GBK CSV 和 XLSX</span><input aria-label="选择流水文件" type="file" accept=".csv,.tsv,.txt,.xlsx" disabled={working} onChange={event => void upload(event.target.files?.[0])} /></label>
      : <>
        <div className="file-summary"><strong>{preview.filename}</strong><span>{preview.stats.total} 行 · 可导入 {preview.stats.ok} 行 · 跳过 {preview.stats.skipped} 行</span></div>
        <label className="mode-field">文件类型<select aria-label="流水文件类型" value={mode} disabled={working || activeJob} onChange={event => { const next = event.target.value as 'A' | 'B'; setMode(next); setMapping(value => Object.fromEntries(Object.entries(value).filter(([field]) => modeFields[next].has(field)))); setDirty(true); }}><option value="A">双边流水（付款方 → 收款方）</option><option value="B">单账户流水（本方与交易对手）</option></select></label>
        <details className="mapping"><summary>检查字段映射 <span>{dirty ? '已修改，请重新预览' : '已自动识别，可调整'}</span></summary><div className="mapping-grid">{(mode === 'A' ? fieldsA : fieldsB).map(field => <label key={field}>{fieldNames[field]}<select aria-label={`映射${fieldNames[field]}`} value={mapping[field] || ''} disabled={working || activeJob} onChange={event => { setMapping(value => ({ ...value, [field]: event.target.value })); setDirty(true); }}><option value="">不使用</option>{preview.columns.map(column => <option key={column}>{column}</option>)}</select></label>)}</div></details>
        {dirty && <button className="button secondary" onClick={() => void repreview()} disabled={working || activeJob}>按当前映射重新预览</button>}
        <div className="preview-wrap"><table className="preview-table"><caption>规范化预览 · 前 {preview.preview.length} 行</caption><thead><tr><th>交易时间</th><th>付款方</th><th>收款方</th><th className="align-right">金额</th></tr></thead><tbody>{preview.preview.map((row, index) => <tr key={index}><td className="mono">{displayTime(row.trade_time)}</td><td>{row.payer_name || '—'}<small className="mono">{row.payer_account}</small></td><td>{row.payee_name || '—'}<small className="mono">{row.payee_account}</small></td><td className="align-right mono">{formatMoney(row.amount)} <small>{row.currency}</small></td></tr>)}</tbody></table></div>
        {preview.self_accounts_in_file.length > 0 && <fieldset className="main-accounts"><legend>标记本方主账号</legend>{preview.self_accounts_in_file.map(account => <label key={account.account}><input type="checkbox" checked={marked.includes(account.account)} disabled={working || activeJob} onChange={event => setMarked(value => event.target.checked ? [...value, account.account] : value.filter(item => item !== account.account))} />{account.name || account.account} <span className="mono">{account.account}</span></label>)}</fieldset>}
      </>}
    {working && !complete && <div role="status" className="import-progress"><progress max={job?.total || 1} value={job?.done || 0} /><span>{job?.message || '正在处理…'} {job ? `${job.done} / ${job.total}` : ''}</span></div>}
    <footer className="modal-footer"><button className="button secondary" disabled={busy} onClick={onClose}>{complete ? '返回流水核对' : activeJob ? '关闭进度' : '取消'}</button>{preview && !complete && !activeJob && <button className="button primary" disabled={working || dirty || preview.stats.ok === 0 || job?.stage === 'error'} onClick={() => void commit()}>{working ? '正在导入…' : '确认导入'}</button>}</footer>
  </Modal>;
}
