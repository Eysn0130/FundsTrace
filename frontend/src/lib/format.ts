/** Format decimal money without Number conversion, rounding or inventing missing values. */
export function formatMoney(value: string | null | undefined): string {
  if (value == null || value === '') return '—';
  const match = typeof value === 'string' && /^([+-]?)(\d+)(?:\.(\d{1,2}))?$/.exec(value);
  if (!match) return '金额格式错误';
  const whole = match[2].replace(/^0+(?=\d)/, '');
  const fraction = (match[3] || '').padEnd(2, '0');
  const sign = match[1] === '-' && (whole !== '0' || fraction !== '00') ? '-' : '';
  return `${sign}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction}`;
}

export function reportMoneyUnit(currency: string | null): string {
  return currency === 'CNY' ? '元' : currency || '币种未明确';
}

export function formatReportMoney(value: string | null, currency: string | null): string {
  const amount = formatMoney(value);
  if (!value || !currency) return amount;
  return currency === 'CNY' ? `${amount}元` : `${amount} ${currency}`;
}

export function displayTime(value: string | null): string {
  return value?.replace('T', ' ') || '—';
}

/** Operational timestamps carry UTC explicitly; do not reinterpret legacy times. */
export function displayOperationalTime(value: string | null): string {
  const time = value?.replace('T', ' ').slice(0, 19) || '—';
  return value?.endsWith('Z') || value?.endsWith('+00:00') ? `${time} UTC` : time;
}

export function formatFileSize(bytes: number | undefined): string {
  if (bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes.toLocaleString('zh-CN')} B`;
  const unit = bytes < 1024 * 1024 ? 'KB' : 'MB';
  const scale = unit === 'KB' ? 1024 : 1024 * 1024;
  return `${(bytes / scale).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} ${unit}`;
}

export function formatImportDuration(milliseconds: number | undefined): string {
  if (milliseconds === undefined) return '—';
  const rounded = Math.round(milliseconds);
  if (!rounded) return '<1 ms';
  if (rounded < 1000) return `${rounded} ms`;
  return `${(milliseconds / 1000).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} s`;
}
