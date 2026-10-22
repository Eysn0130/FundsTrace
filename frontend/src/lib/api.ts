import type { Case, CaseList, CaseStats, FlowPage, ImportPreview, ImportJob, CommitRequest, RepreviewRequest, ImportSessionList, FlowSourceList, CleanupResult } from '../../../contracts/api-v1';

export class ApiError extends Error {
  constructor(public code: string, message: string, public status?: number) { super(message); }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api/v1${path}`, init); }
  catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiError('network_error', '无法连接本地服务，请检查服务是否运行。');
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(payload?.error?.code || 'request_failed', payload?.error?.message || '请求失败，请重试。', response.status);
  return payload as T;
}

const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const casePath = (id: string) => `/cases/${encodeURIComponent(id)}`;

export const api = {
  cases: (signal?: AbortSignal) => request<CaseList>('/cases', { signal }),
  createCase: (name: string, signal?: AbortSignal) => request<Case>('/cases', { ...json({ name }), signal }),
  stats: (id: string, signal?: AbortSignal) => request<CaseStats>(`${casePath(id)}/stats`, { signal }),
  flows: (id: string, filters: { account: string; kw: string; page: number }, signal?: AbortSignal) =>
    request<FlowPage>(`${casePath(id)}/flows?${new URLSearchParams({ ...filters, page: String(filters.page), page_size: '50' })}`, { signal }),
  preview: (id: string, file: File, signal?: AbortSignal) => {
    const body = new FormData(); body.append('file', file);
    return request<ImportPreview>(`${casePath(id)}/imports/preview`, { method: 'POST', body, signal });
  },
  repreview: (id: string, importId: string, body: RepreviewRequest, signal?: AbortSignal) =>
    request<ImportPreview>(`${casePath(id)}/imports/${encodeURIComponent(importId)}/preview`, { ...json(body), signal }),
  commit: (id: string, importId: string, body: CommitRequest, signal?: AbortSignal) =>
    request<{ case_id: string; import_id: string; started: boolean }>(`${casePath(id)}/imports/${encodeURIComponent(importId)}/commit`, { ...json(body), signal }),
  job: (id: string, importId: string, signal?: AbortSignal) =>
    request<ImportJob>(`${casePath(id)}/imports/${encodeURIComponent(importId)}`, { signal }),
  imports: (id: string, signal?: AbortSignal) => request<ImportSessionList>(`${casePath(id)}/imports?limit=100`, { signal }),
  sources: (id: string, flowId: string, page = 1, signal?: AbortSignal) =>
    request<FlowSourceList>(`${casePath(id)}/flows/${encodeURIComponent(flowId)}/sources?page=${page}&page_size=50`, { signal }),
  downloadSource: async (id: string, sourceId: string, filename: string, signal?: AbortSignal): Promise<void> => {
    const response = await fetch(`/api/v1${casePath(id)}/sources/${encodeURIComponent(sourceId)}/file`, { signal });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      throw new ApiError(payload?.error?.code || 'download_failed', payload?.error?.message || '无法下载原始文件。');
    }
    const blob = await response.blob();
    if (signal?.aborted) throw new DOMException('下载已中止。', 'AbortError');
    const url = URL.createObjectURL(blob);
    try {
      const link = document.createElement('a'); link.href = url; link.download = filename;
      document.body.appendChild(link); link.click(); link.remove();
    } finally { URL.revokeObjectURL(url); }
  },
  cleanup: (id: string, dryRun = true, signal?: AbortSignal) => request<CleanupResult>(`${casePath(id)}/imports/cleanup`, { ...json({ dry_run: dryRun }), signal }),
};

export type FundsApi = typeof api;
