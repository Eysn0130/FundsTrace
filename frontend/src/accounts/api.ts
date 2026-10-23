import type { AccountPage, AccountProfile, AccountRegistryApi, AccountRow, AccountSuggestions, CurrencyTotal } from './types';

async function get(path: string, query: Record<string, string>, signal: AbortSignal): Promise<unknown> {
  let response: Response;
  try { response = await fetch(`${path}?${new URLSearchParams(query)}`, { signal }); }
  catch (error) { if (signal.aborted) throw error; throw new Error('无法连接本地服务，请检查连接后重新加载。'); }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.error?.message || body?.detail || (response.status === 404 ? '当前案件中未找到此账户，请重新选择。' : '账户资料读取失败，请重试。'));
  if (!body || typeof body !== 'object') throw new Error('账户资料响应不完整，请重新加载。');
  return body;
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const identifier = (value: unknown): value is string => typeof value === 'string' && value.length > 0;
const nullableString = (value: unknown) => value === null || typeof value === 'string';
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;
const money = (value: unknown): value is string => typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value);
const totals = (value: unknown): value is CurrencyTotal[] => Array.isArray(value) && value.every(item => record(item) && typeof item.currency === 'string' && money(item.amount));
function accountRow(value: unknown): value is AccountRow & Record<string, unknown> {
  return record(value) && identifier(value.account) && typeof value.name === 'string'
    && nullableString(value.id_no) && nullableString(value.identity_updated_at) && nullableString(value.photo_at)
    && typeof value.is_main === 'boolean' && (value.main_source === null || value.main_source === 'auto' || value.main_source === 'manual')
    && nullableString(value.main_marked_at) && typeof value.has_flows === 'boolean';
}
export function isAccountPage(value: unknown): value is AccountPage {
  return record(value) && identifier(value.case_id) && (value.source === 'flows' || value.source === 'identity')
    && count(value.total) && count(value.page) && value.page > 0 && count(value.page_size) && value.page_size > 0 && value.page_size <= 100
    && Array.isArray(value.items) && value.items.length <= value.page_size && value.items.every(accountRow);
}
export function isAccountProfile(value: unknown): value is AccountProfile {
  if (!record(value) || !accountRow(value) || !identifier(value.case_id)) return false;
  const photo = value.photo;
  return (photo === null || (record(photo) && typeof photo.updated_at === 'string' && ['crop_cx', 'crop_cy', 'crop_r'].every(key => typeof photo[key] === 'number' && Number.isFinite(photo[key]))))
    && count(value.cnt) && count(value.in_cnt) && count(value.out_cnt) && totals(value.in_totals) && totals(value.out_totals)
    && (value.in_amt === null || money(value.in_amt)) && (value.out_amt === null || money(value.out_amt)) && nullableString(value.tmin) && nullableString(value.tmax)
    && Array.isArray(value.top_counterparties) && value.top_counterparties.every(item => record(item) && identifier(item.acct) && typeof item.name === 'string'
      && count(item.in_cnt) && count(item.out_cnt) && totals(item.in_totals) && totals(item.out_totals));
}

export function isAccountSuggestions(value: unknown): value is AccountSuggestions {
  return record(value) && identifier(value.case_id) && (value.source === 'flows' || value.source === 'identity')
    && typeof value.query === 'string' && typeof value.has_more === 'boolean' && (value.next_offset === null || count(value.next_offset))
    && Array.isArray(value.items) && value.items.length <= 50 && value.items.every(item => record(item) && identifier(item.account)
      && typeof item.name === 'string' && nullableString(item.id_no) && ['account', 'name', 'id'].includes(String(item.matched_field)) && typeof item.matched_text === 'string')
    && new Set(value.items.map(item => item.account)).size === value.items.length;
}

/** Account registry reads only. The parent owns every write and bulk operation. */
export const accountRegistryApi: AccountRegistryApi = {
  async list(caseId, query, signal) {
    const result = await get('/api/account-registry', { case_id: caseId, source: query.source, q: query.q, page: String(query.page), page_size: String(query.pageSize), ...(query.accountExact ? { account_exact: 'true' } : {}) }, signal);
    if (!isAccountPage(result) || result.case_id !== caseId || result.source !== query.source || result.page !== query.page
      || (query.accountExact && (result.total > 1 || result.items.some(item => item.account !== query.q)))) throw new Error('账户列表响应不完整或案件不一致，请重新加载。');
    return result;
  },
  async suggestions(caseId, query, signal) {
    const result = await get('/api/account-suggestions', { case_id: caseId, source: query.source, q: query.q, offset: String(query.offset), page_size: String(query.limit) }, signal);
    if (!isAccountSuggestions(result) || result.case_id !== caseId || result.source !== query.source || result.query !== query.q.normalize('NFKC').trim()
      || result.items.length > query.limit || (result.has_more ? result.items.length !== query.limit || result.next_offset !== query.offset + result.items.length : result.next_offset !== null)) {
      throw new Error('账户候选响应不完整或查询范围不一致，请重新输入。');
    }
    return result;
  },
  async profile(caseId, account, signal) {
    const result = await get('/api/account-registry/profile', { case_id: caseId, account }, signal);
    if (!isAccountProfile(result) || result.case_id !== caseId || result.account !== account) throw new Error('账户资料响应不完整或案件不一致，请重新加载。');
    return result;
  },
  photoUrl(caseId, account, updatedAt, original = false) {
    return `/api/account-photos/${original ? 'orig' : 'img'}?${new URLSearchParams({ case_id: caseId, account, v: updatedAt })}`;
  },
};
