/** Development-only integration fixture. It never queries HTTP or a real case. */
import { useEffect, useRef, useState } from 'react';
import type { Case, CaseStats, Flow } from '../../../contracts/api-v1';
import { FundsPanel, FUNDS_PANEL_VERSION } from '../panel';
import { createHttpFundsSession } from '../adapters/http';
import { ApiError, type FundsApi } from '../lib/api';
import './styles.css';

const cases: Case[] = [
  { id: '101', name: '合成宿主案甲 · 精确金额核对', created_at: null },
  { id: '202', name: '合成宿主案乙 · 独立流水', created_at: null },
];
const flow = (caseId: string, amount: string): Flow => ({
  case_id: caseId, id: `9007199254740${caseId}`, batch_id: '9007199254740994',
  trade_no: `SYN-HOST-${caseId}`, trade_time: '2026-01-01 10:00:00',
  payer_name: '合成付款方', payer_account: `000SYN-${caseId}-A`, payer_bank: '',
  payee_name: '合成收款方', payee_account: `000SYN-${caseId}-B`, payee_bank: '',
  amount, balance: null, currency: 'CNY', summary: '纯内存合成记录 · 非真实交易', channel: 'synthetic', self_account: '', self_name: '',
});
const records = [flow('101', '9999999999999999.99'), flow('202', '8.00')];
const stat = (id: string): CaseStats => ({ case_id: id, flows: 1, accounts: 2, batches: 1,
  currency: 'CNY', amount_total: records.find(row => row.case_id === id)!.amount,
  amount_totals: [{ currency: 'CNY', amount: records.find(row => row.case_id === id)!.amount }] });
const unavailable = async (): Promise<never> => { throw new ApiError('fixture_only', '此合成宿主仅验证核对和案件隔离；请在独立版验证真实导入流程。'); };

type Binding = ReturnType<typeof createHttpFundsSession>;

export default function HostPreview() {
  const [selected, setSelected] = useState('101');
  const [generation, setGeneration] = useState(1);
  const [active, setActive] = useState(true);
  const [narrow, setNarrow] = useState(true);
  const [delay, setDelay] = useState(false);
  const [binding, setBinding] = useState<Binding | null>(null);
  const current = useRef<Binding | null>(null);
  useEffect(() => {
    if (!active) { setBinding(null); return; }
    const pause = async () => { if (delay) await new Promise(resolve => setTimeout(resolve, 800)); };
    const transport: FundsApi = {
      cases: async () => ({ items: cases }), createCase: unavailable,
      stats: async id => { await pause(); return stat(id); },
      flows: async (id, filters) => {
        await pause();
        const items = records.filter(row => row.case_id === id
          && (!filters.account || row.payer_account.includes(filters.account) || row.payee_account.includes(filters.account))
          && (!filters.kw || row.summary.includes(filters.kw) || row.trade_no.includes(filters.kw)));
        return { case_id: id, items, total: items.length, page: 1, page_size: 50 };
      },
      imports: async id => ({ case_id: id, items: [] }),
      sources: async (id, flowId, page = 1) => ({ case_id: id, flow_id: flowId, items: [], total: 0, page, page_size: 50 }),
      preview: unavailable, repreview: unavailable, commit: unavailable, job: unavailable,
      downloadSource: unavailable,
      downloadImportReport: unavailable,
      cleanup: async (_id, dryRun = true) => ({ dry_run: dryRun, eligible: 0, protected: 0, queued: 0, deleted: 0, missing: 0, failed: 0 }),
    };
    const target = cases.find(item => item.id === selected)!;
    const next = createHttpFundsSession({ context: { caseId: target.id, name: target.name },
      scopeKey: `synthetic-host:${target.id}:${generation}`, generation, transport });
    current.current = next; setBinding(next);
    return () => { next.revoke(); if (current.current === next) current.current = null; };
  }, [selected, generation, active, delay]);

  const rebind = (id: string) => { current.current?.revoke(); setSelected(id); setActive(true); setGeneration(value => value + 1); };
  const session = active && binding?.session.context.caseId === selected && binding.session.generation === generation ? binding.session : null;
  return <div className="funds-host-preview">
    <aside className="fixture-sidebar"><strong>合成宿主</strong><p>仅内存数据，无 HTTP 或业务库</p>
      {cases.map(item => <button key={item.id} aria-pressed={selected === item.id && active} onClick={() => rebind(item.id)}>{item.name}</button>)}
      <button onClick={() => rebind(selected)}>同案重新绑定</button>
      <button onClick={() => { current.current?.revoke(); setActive(false); }}>撤销案件上下文</button>
      <label><input type="checkbox" checked={narrow} onChange={event => setNarrow(event.target.checked)} />限制为 600px 分栏</label>
      <label><input type="checkbox" checked={delay} onChange={event => { current.current?.revoke(); setDelay(event.target.checked); setGeneration(value => value + 1); }} />查询延迟 800ms</label>
      <p>绑定代次 {generation} · {active ? '有效' : '已撤销'}</p>
    </aside>
    <main className="fixture-content"><header><h1>共享资金面板 · 宿主预览</h1><p>图谱与下载未授予；由面板明确显示能力边界。</p></header>
      <section className="fixture-controls" aria-label="宿主样式隔离参照"><h2>宿主标题</h2><button>宿主按钮</button><p>该区域不应继承资金面板的按钮、字号或背景样式。</p></section>
      <div className="fixture-slot" style={{ maxWidth: narrow ? 600 : '100%' }}>
        <FundsPanel host={{ version: FUNDS_PANEL_VERSION, session }} />
      </div>
    </main>
  </div>;
}
