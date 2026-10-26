import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ImportPreview, ImportJob } from '../../../contracts/api-v1';
import type { FundsCaseClientV1 } from '../panel/host';
import { ImportDialog } from './ImportDialog';

const preview: ImportPreview = { case_id: '1', import_id: 'SYN-DIRECTORY', filename: '资料.zip', mode: 'B', automatic: true,
  columns: [], mapping: {}, self_accounts_in_file: [], stats: { total: 2, ok: 1, skipped: 1 }, preview: [] };
function clientFor(): FundsCaseClientV1 {
  return { binding: { caseId: '1', scopeKey: 'directory', generation: 1 }, stats: vi.fn(), flows: vi.fn(),
    preview: vi.fn(), previewDirectory: vi.fn(async () => preview), repreview: vi.fn(),
    commit: vi.fn(async () => ({ case_id: '1', import_id: preview.import_id, started: true })),
    job: vi.fn(async (): Promise<ImportJob> => ({ case_id: '1', import_id: preview.import_id, stage: 'done', done: 1, total: 2,
      message: '完成', error: null, result: null })), imports: vi.fn(), sources: vi.fn(), cleanup: vi.fn() };
}
function show(client: FundsCaseClientV1, signal = new AbortController().signal) {
  return render(<ImportDialog target={{ caseId: '1', name: '合成目录案件' }} client={client} scopeSignal={signal}
    onClose={vi.fn()} onImported={vi.fn()} />);
}
function files() {
  return ['流水.csv', '账户.csv'].map(name => {
    const file = new File(['synthetic'], name);
    Object.defineProperty(file, 'webkitRelativePath', { value: `资料/银行/${name}` });
    return file;
  });
}

describe('folder import', () => {
  it('passes every original File with relative paths and commits one case-bound import', async () => {
    const client = clientFor(), selected = files(); show(client);
    const input = screen.getByLabelText('选择整个文件夹') as HTMLInputElement;
    expect(input.hasAttribute('webkitdirectory')).toBe(true);
    fireEvent.change(input, { target: { files: selected } });
    await screen.findByRole('heading', { name: '流水已写入当前案件' });
    expect(client.previewDirectory).toHaveBeenCalledWith(selected, expect.any(AbortSignal));
    expect(vi.mocked(client.previewDirectory!).mock.calls[0][0][1]).toBe(selected[1]);
    expect(client.preview).not.toHaveBeenCalled();
    expect(client.commit).toHaveBeenCalledExactlyOnceWith(preview.import_id,
      { mode: 'B', mapping: {}, mark_accounts: [], self_accounts: [] }, expect.any(AbortSignal));
  });

  it('does not submit a late directory preview after the case session is revoked', async () => {
    const client = clientFor(), controller = new AbortController();
    let resolve!: (value: ImportPreview) => void;
    client.previewDirectory = vi.fn(() => new Promise<ImportPreview>(done => { resolve = done; })); show(client, controller.signal);
    fireEvent.change(screen.getByLabelText('选择整个文件夹'), { target: { files: files() } });
    await waitFor(() => expect(client.previewDirectory).toHaveBeenCalledOnce());
    act(() => controller.abort()); await act(async () => resolve(preview));
    expect(client.commit).not.toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: '流水已写入当前案件' })).toBeNull();
  });

  it('keeps the folder action unavailable when an embedded host does not implement it', () => {
    const client = clientFor(); delete client.previewDirectory; show(client);
    expect(screen.queryByLabelText('选择整个文件夹')).toBeNull();
    expect(screen.getByLabelText('选择流水文件')).toBeTruthy();
  });
});
