import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ImportJob, ImportPreview, ImportSession } from '../../../contracts/api-v1';
import { api } from '../lib/api';
import { mountImport } from './import-entry';

const importId = '0123456789abcdef';
const preview: ImportPreview = { case_id: '1', import_id: importId, filename: 'synthetic.csv', mode: 'A', automatic: true,
  columns: ['合成金额'], mapping: { amount: '合成金额' }, self_accounts_in_file: [], stats: { total: 1, ok: 1, skipped: 0 }, preview: [] };
const job: ImportJob = { case_id: '1', import_id: importId, stage: 'done', done: 1, total: 1, error: null, message: '完成',
  result: { total: 1, ok: 1, skipped: 0, rows_dup: 0, batch_id: '1', bad_examples: [], self_accounts: [], self_accounts_count: 0,
    main_matched: [], main_detected: [], accounts_in_batch: 0, source_id: importId } };
const cleanups: Array<() => void> = [];
afterEach(() => { for (const cleanup of cleanups.splice(0)) act(cleanup); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function mount(overrides: Partial<Parameters<typeof mountImport>[1]> = {}) {
  const element = document.createElement('div'); element.className = 'funds-trace'; document.body.append(element);
  const controller = new AbortController(), options = { caseId: '1', name: '合成案件', generation: 1, signal: controller.signal,
    acquire: vi.fn(async () => {}), release: vi.fn(async () => {}), changed: vi.fn(async () => {}), feedback: vi.fn(() => true), close: vi.fn(), ...overrides };
  let view!: ReturnType<typeof mountImport>; await act(async () => { view = mountImport(element, options); });
  cleanups.push(() => { view.dispose(); element.remove(); });
  return { options, controller, view };
}
function transport() {
  return { preview: vi.spyOn(api, 'preview').mockResolvedValue(preview), commit: vi.spyOn(api, 'commit').mockResolvedValue({ case_id: '1', import_id: importId, started: true }),
    job: vi.spyOn(api, 'job').mockResolvedValue(job) };
}
function upload() { fireEvent.change(screen.getByLabelText('选择流水文件'), { target: { files: [new File(['synthetic'], 'synthetic.csv')] } }); }

describe('the canvas import host', () => {
  it('keeps a durable completion when refreshing the surrounding canvas fails', async () => {
    const t = transport(), h = await mount({ changed: vi.fn(async () => { throw new Error('Synthetic list refresh failure'); }) });
    upload(); await screen.findByRole('status', { name: '导入完成' });
    expect(h.options.acquire).toHaveBeenCalledWith(importId, 'v1');
    expect(h.options.release).toHaveBeenCalledWith(importId, importId);
    expect(h.options.feedback).toHaveBeenCalledWith(expect.objectContaining({ kind: 'toast', severity: 'warning', title: '导入回执已读取，界面刷新尚未完成' }));
    expect(screen.queryByText('结果待核对')).toBeNull(); expect(t.commit).toHaveBeenCalledOnce();
  });
  it('does not send a write when the case lease cannot be acquired', async () => {
    const t = transport(); await mount({ acquire: vi.fn(async () => { throw new Error('Synthetic unresolved writer'); }) });
    upload(); await screen.findByRole('alert');
    expect(t.commit).not.toHaveBeenCalled(); expect(t.job).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '重新查询进度' })).toBeTruthy();
  });
  it('recovers an existing legacy operation through its original API and never auto-commits recovery', async () => {
    const t = transport(); const bodies: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/repreview')) return { ok: true, json: async () => preview };
      if (url.endsWith('/commit')) { bodies.push(JSON.parse(init?.body as string)); return { ok: true, json: async () => ({ import_id: importId }) }; }
      if (url.startsWith('/api/import/progress?')) return { ok: true, json: async () => job };
      throw new Error('Unexpected synthetic legacy path');
    });
    vi.stubGlobal('fetch', fetcher);
    const resume: ImportSession = { case_id: '1', import_id: importId, filename: preview.filename, surface: 'legacy', mode: 'A', mapping: preview.mapping,
      stage: 'ready', source_state: 'retained', source_available: true, recoverable: true, created_at: '2026-01-01T00:00:00Z', expires_at: null,
      error_code: null, attempt: 0, request: { mode: 'A', mapping: preview.mapping, mark_accounts: [], self_accounts: ['0001','0002'] }, result: null };
    const h = await mount({ resume });
    await screen.findByRole('button', { name: '确认导入' }); expect(bodies).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '确认导入' }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual(expect.objectContaining({ case_id: '1', import_id: importId, mark_accounts: '', self_accounts: '0001,0002' }));
    await screen.findByRole('status', { name: '导入完成' });
    expect(h.options.acquire).toHaveBeenCalledWith(importId, 'legacy'); expect(t.commit).not.toHaveBeenCalled(); expect(t.preview).not.toHaveBeenCalled();
  });
});
