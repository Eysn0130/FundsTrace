export type AccountSource = 'flows' | 'identity';
export type AccountContext = Readonly<{ caseId: string; name: string; generation: number }>;
export type AccountRow = Readonly<{
  account: string; name: string; id_no: string | null; identity_updated_at: string | null; photo_at: string | null;
  is_main: boolean; main_source: 'auto' | 'manual' | null; main_marked_at: string | null; has_flows: boolean;
}>;
export type CurrencyTotal = Readonly<{ currency: string; amount: string }>;
export type AccountPage = Readonly<{ case_id: string; source: AccountSource; total: number; page: number; page_size: number; items: AccountRow[] }>;
export type AccountProfile = AccountRow & Readonly<{
  case_id: string; photo: null | { updated_at: string; crop_cx: number; crop_cy: number; crop_r: number };
  cnt: number; in_cnt: number; out_cnt: number; in_totals: CurrencyTotal[]; out_totals: CurrencyTotal[];
  in_amt: string | null; out_amt: string | null; tmin: string | null; tmax: string | null;
  top_counterparties: ReadonlyArray<{ acct: string; name: string; in_cnt: number; out_cnt: number; in_totals: CurrencyTotal[]; out_totals: CurrencyTotal[] }>;
}>;
export type PhotoCrop = Readonly<{ cx: number; cy: number; r: number }>;
export type AccountMutation =
  | { kind: 'identity-set'; account: string; idNo: string }
  | { kind: 'photo-set'; account: string; file: File; crop: PhotoCrop }
  | { kind: 'photo-clear'; account: string }
  | { kind: 'photo-crop'; account: string; crop: PhotoCrop }
  | { kind: 'main-set'; account: string; on: boolean };
export type UnresolvedAccountOperation = Readonly<{ operationId: string; account: string; kind: AccountMutation['kind'] }>;
export type AccountWorkspaceActions = Readonly<{
  mutate(payload: AccountMutation, signal: AbortSignal): Promise<void>;
  reconcile(operationId: string, signal: AbortSignal): Promise<void>;
  openMainAccounts(signal: AbortSignal): void | Promise<void>;
  importIdentity(signal: AbortSignal): void | Promise<void>;
  downloadTemplate(signal: AbortSignal): void | Promise<void>;
  viewFlows(account: string, signal: AbortSignal): void | Promise<void>;
}>;
export type AccountRegistryApi = Readonly<{
  list(caseId: string, query: { source: AccountSource; q: string; page: number; pageSize: number }, signal: AbortSignal): Promise<AccountPage>;
  profile(caseId: string, account: string, signal: AbortSignal): Promise<AccountProfile>;
  photoUrl(caseId: string, account: string, updatedAt: string, original?: boolean): string;
}>;
