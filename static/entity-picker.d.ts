export interface EntityPickerItem {
  key: string;
  label: string;
  value: string;
  account?: string;
  idNo?: string;
  number?: string;
  detail?: string;
  matchedField?: 'account' | 'id' | 'idNo' | 'id_number' | 'label' | 'name' | 'number' | 'detail';
  matchedText?: string;
  onGraph?: boolean;
}
export interface EntityPickerRequest {
  query: string;
  offset: number;
  limit: number;
  signal: AbortSignal;
  scope: string;
}
export interface EntityPickerOptions<T extends EntityPickerItem = EntityPickerItem> {
  input: HTMLInputElement | HTMLTextAreaElement;
  scope: () => string | null;
  search: (request: EntityPickerRequest) => Promise<{ items: T[]; hasMore: boolean; nextOffset?: number }>;
  onSelect?: (item: T) => void;
  onSubmit?: (query: string, selected: T | null) => void;
  kind: 'account' | 'case' | 'text';
  token?: (input: HTMLInputElement | HTMLTextAreaElement) => { query: string; start: number; end: number };
  ghost?: boolean;
  debounce?: number;
}
export interface EntityPickerController<T extends EntityPickerItem = EntityPickerItem> {
  /** Revokes requests, options and selected identity. Does not change the input or start a query. */
  invalidate(): void;
  /** Dismisses candidates, preserving an explicit selection while its value/scope remain unchanged. */
  close(): void;
  open(): void;
  destroy(): void;
  getSelected(): T | null;
}
export interface EntityPickerAPI {
  create<T extends EntityPickerItem = EntityPickerItem>(options: EntityPickerOptions<T>): EntityPickerController<T>;
}
declare global { interface Window { FundsEntityPicker: EntityPickerAPI } }
declare const FundsEntityPicker: EntityPickerAPI;
export default FundsEntityPicker;
