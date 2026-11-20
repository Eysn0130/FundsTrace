import type { Case, CaseList, CaseStats, FlowPage, ImportPreview, ImportJob, ImportSourceMetadata, ImportRecordPage, ImportReviewPage, ImportReport, ImportReportIntegrity, ImportReportRecords, ImportReportAccountOptions, CommitRequest, RepreviewRequest, ImportSessionList, FlowSourceList, CleanupResult, WithdrawnImport } from '../../../contracts/api-v1';
import type { FundsImportReviewQueryV1, FundsReportDownloadV1, FundsReportQueryV1, FundsReportRecordsQueryV1, FundsReportAccountQueryV1 } from '../panel/host';

export class ApiError extends Error {
  constructor(public code: string, message: string, public status?: number, public failureKind?: string) { super(message); }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api/v1${path}`, init); }
  catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiError('network_error', '无法连接本地服务，请检查服务是否运行。');
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(payload?.error?.code || 'request_failed', payload?.error?.message || '请求失败，请重试。', response.status,
    response.headers.get('X-FundsTrace-Import-Failure-Kind') || 'unknown');
  return payload as T;
}

const json = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const casePath = (id: string) => `/cases/${encodeURIComponent(id)}`;
const reportQuery = (query: FundsReportQueryV1, pageSize: number) => { const params = new URLSearchParams({
  member_path: query.memberPath, page: String(query.page ?? 1), page_size: String(pageSize),
  ...(query.accountsPage !== undefined ? { accounts_page: String(query.accountsPage) } : {}),
  ...(query.relationsPage !== undefined ? { relations_page: String(query.relationsPage) } : {}),
  ...(query.currency !== undefined ? { currency: query.currency } : {}),
  ...(query.account !== undefined ? { account: query.account } : {}),
  ...(query.peerAccount !== undefined ? { peer_account: query.peerAccount } : {}),
  ...(query.timelineGrain !== undefined ? { timeline_grain: query.timelineGrain } : {}),
  ...(query.timelinePage !== undefined ? { timeline_page: String(query.timelinePage) } : {}),
  ...(query.expectedRevision !== undefined ? { expected_revision: query.expectedRevision } : {}),
  ...(query.timelineScope !== undefined ? { timeline_scope: query.timelineScope } : {}),
  ...(query.timelineSearch !== undefined ? { timeline_search: query.timelineSearch } : {}),
});
  for (const account of query.timelineAccounts || []) params.append('timeline_accounts', account);
  return params;
};
const reportPath = (id: string, sourceId: string) => `${casePath(id)}/imports/${encodeURIComponent(sourceId)}/report`;

async function download(path: string, filename: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(`/api/v1${path}`, { signal });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(payload?.error?.code || 'download_failed', payload?.error?.message || '无法下载文件。', response.status);
  }
  const blob = await response.blob();
  if (signal?.aborted) throw new DOMException('下载已中止。', 'AbortError');
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a'); link.href = url; link.download = filename;
    document.body.appendChild(link); link.click(); link.remove();
  } finally { URL.revokeObjectURL(url); }
}

export const api = {
  cases: (signal?: AbortSignal) => request<CaseList>('/cases', { signal }),
  createCase: (name: string, signal?: AbortSignal) => request<Case>('/cases', { ...json({ name }), signal }),
  stats: (id: string, signal?: AbortSignal) => request<CaseStats>(`${casePath(id)}/stats`, { signal }),
  flows: (id: string, filters: { account: string; kw: string; page: number }, signal?: AbortSignal) =>
    request<FlowPage>(`${casePath(id)}/flows?${new URLSearchParams({ ...filters, page: String(filters.page), page_size: '50', granularity: 'events' })}`, { signal }),
  preview: (id: string, file: File, signal?: AbortSignal) => {
    const body = new FormData(); body.append('file', file);
    return request<ImportPreview>(`${casePath(id)}/imports/preview`, { method: 'POST', body, signal });
  },
  previewDirectory: (id: string, files: File[], signal?: AbortSignal) => {
    const body = new FormData();
    for (const file of files) body.append('files', file, file.webkitRelativePath || file.name);
    return request<ImportPreview>(`${casePath(id)}/imports/directory/preview`, { method: 'POST', body, signal });
  },
  repreview: (id: string, importId: string, body: RepreviewRequest, signal?: AbortSignal) =>
    request<ImportPreview>(`${casePath(id)}/imports/${encodeURIComponent(importId)}/preview`, { ...json(body), signal }),
  commit: (id: string, importId: string, body: CommitRequest, signal?: AbortSignal) =>
    request<{ case_id: string; import_id: string; started: boolean }>(`${casePath(id)}/imports/${encodeURIComponent(importId)}/commit`, { ...json(body), signal }),
  job: (id: string, importId: string, signal?: AbortSignal) =>
    request<ImportJob>(`${casePath(id)}/imports/${encodeURIComponent(importId)}`, { signal }),
  importSourceMetadata: (id: string, sourceId: string, signal?: AbortSignal) =>
    request<ImportSourceMetadata>(`${casePath(id)}/sources/${encodeURIComponent(sourceId)}/metadata`, { signal }),
  withdrawImportSource: (id: string, sourceId: string, batchId: string, signal?: AbortSignal) =>
    request<WithdrawnImport>(`${casePath(id)}/sources/${encodeURIComponent(sourceId)}/operation?${new URLSearchParams({ expected_batch_id: batchId })}`, { method: 'DELETE', signal }),
  imports: (id: string, signal?: AbortSignal, importId?: string) =>
    request<ImportSessionList>(`${casePath(id)}/imports?${importId === undefined ? 'limit=100' : 'import_id=' + encodeURIComponent(importId)}`, { signal }),
  importRecords: (id: string, sourceId: string, page = 1, signal?: AbortSignal, memberPath?: string, sourceRow?: number) =>
    request<ImportRecordPage>(`${casePath(id)}/imports/${encodeURIComponent(sourceId)}/records?${new URLSearchParams({ page: String(page), page_size: '50', ...(memberPath !== undefined ? { member_path: memberPath, include_preview: 'true' } : {}), ...(sourceRow !== undefined ? { source_row: String(sourceRow) } : {}) })}`, { signal }),
  importReview: (id: string, sourceId: string, memberPath: string, query: FundsImportReviewQueryV1, signal?: AbortSignal) => {
    const parameters = new URLSearchParams({ member_path: memberPath, view: query.view, page: String(query.page), page_size: '50' });
    if (query.sourceRow !== undefined) parameters.set('source_row', String(query.sourceRow));
    else if (query.issueIndex !== undefined) parameters.set('issue_index', String(query.issueIndex));
    return request<ImportReviewPage>(`${casePath(id)}/imports/${encodeURIComponent(sourceId)}/review?${parameters}`, { signal });
  },
  importFundsReport: (id: string, sourceId: string, query: FundsReportQueryV1, signal?: AbortSignal) =>
    request<ImportReport>(`${reportPath(id, sourceId)}?${reportQuery(query, 20)}`, { signal }),
  importReportAccounts: (id: string, sourceId: string, query: FundsReportAccountQueryV1, signal?: AbortSignal) =>
    request<ImportReportAccountOptions>(`${reportPath(id,sourceId)}/accounts?${new URLSearchParams({member_path:query.memberPath,expected_revision:query.expectedRevision,currency:query.currency,search:query.search || '',page:String(query.page || 1),...(query.account !== undefined ? {account:query.account} : {})})}`,{signal}),
  verifyImportSource: (id: string, sourceId: string, query: Readonly<{ memberPath: string; expectedRevision: string }>, signal?: AbortSignal) =>
    request<ImportReportIntegrity>(`${reportPath(id, sourceId)}/verify?${new URLSearchParams({ member_path: query.memberPath, expected_revision: query.expectedRevision })}`, { method: 'POST', signal }),
  importReportRecords: (id: string, sourceId: string, query: FundsReportRecordsQueryV1, signal?: AbortSignal) => {
    const parameters = reportQuery(query, 50);
    if (query.counterparty !== undefined) parameters.set('counterparty', query.counterparty);
    if (query.direction !== undefined) parameters.set('direction', query.direction);
    if (query.timeStart !== undefined) parameters.set('time_start', query.timeStart);
    if (query.timeEnd !== undefined) parameters.set('time_end', query.timeEnd);
    if (query.timeBasis !== undefined) parameters.set('time_basis', query.timeBasis);
    return request<ImportReportRecords>(`${reportPath(id, sourceId)}/records?${parameters}`, { signal });
  },
  sources: (id: string, flowId: string, page = 1, signal?: AbortSignal) =>
    request<FlowSourceList>(`${casePath(id)}/flows/${encodeURIComponent(flowId)}/sources?page=${page}&page_size=50`, { signal }),
  downloadSource: (id: string, sourceId: string, filename: string, signal?: AbortSignal): Promise<void> =>
    download(`${casePath(id)}/sources/${encodeURIComponent(sourceId)}/file`, filename, signal),
  downloadImportReport: (id: string, sourceId: string, filename: string, signal?: AbortSignal): Promise<void> =>
    download(`${casePath(id)}/sources/${encodeURIComponent(sourceId)}/report`, filename, signal),
  downloadFundsReport: (id: string, source: FundsReportDownloadV1, signal?: AbortSignal): Promise<void> =>
    download(`${reportPath(id, source.sourceId)}/document?${reportQuery(source, 20)}`, source.filename, signal),
  cleanup: (id: string, dryRun = true, signal?: AbortSignal) => request<CleanupResult>(`${casePath(id)}/imports/cleanup`, { ...json({ dry_run: dryRun }), signal }),
};

type OptionalUseCase = 'previewDirectory' | 'importSourceMetadata' | 'withdrawImportSource' | 'importRecords' | 'importReview' | 'importFundsReport' | 'verifyImportSource' | 'importReportRecords' | 'importReportAccounts' | 'downloadFundsReport';
export type FundsApi = Omit<typeof api, OptionalUseCase> & Partial<Pick<typeof api, OptionalUseCase>>;
