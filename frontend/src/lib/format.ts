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

export function displayTime(value: string | null): string {
  return value?.replace('T', ' ') || '—';
}

/** Operational timestamps carry UTC explicitly; do not reinterpret legacy times. */
export function displayOperationalTime(value: string | null): string {
  const time = value?.replace('T', ' ').slice(0, 19) || '—';
  return value?.endsWith('Z') ? `${time} UTC` : time;
}
