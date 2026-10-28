/** Money never crosses JavaScript Number: keep every supplied decimal digit. */
export function formatMoney(value: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const [, sign, whole, fraction = '00'] = match;
  return `${sign}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`;
}

export function displayTime(value: string | null): string {
  return value?.replace('T', ' ').replace(/Z$/, '') || '—';
}

/** Operational timestamps carry UTC explicitly; do not reinterpret legacy times. */
export function displayOperationalTime(value: string | null): string {
  const time = value?.replace('T', ' ').slice(0, 19) || '—';
  return value?.endsWith('Z') ? `${time} UTC` : time;
}
