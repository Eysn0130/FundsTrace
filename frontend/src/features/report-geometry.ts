/** Money stays exact; only bounded, dimensionless chart geometry becomes Number. */
export function moneyUnits(value: string) {
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(value);
  if (!match) throw new Error('统计金额格式无效');
  return BigInt(`${match[1]}${match[2]}${(match[3] || '').padEnd(2, '0')}`);
}

export function plotRatio(value: bigint, low: bigint, high: bigint) {
  if (high <= low) return 0;
  return Number((value - low) * 1000000n / (high - low)) / 1000000;
}

export function unitsMoney(value: bigint) {
  const sign = value < 0n ? '-' : '', text = (value < 0n ? -value : value).toString().padStart(3, '0');
  return `${sign}${text.slice(0, -2)}.${text.slice(-2)}`;
}

/** An unqualified clock uses an abstract calendar axis, never the device timezone. */
export function clockPosition(value: string) {
  const match = /^(\d{4,6})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?Z?$/.exec(value);
  if (!match) throw new Error('统计时间格式无效');
  const calendar = new Date(0);
  calendar.setUTCFullYear(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  calendar.setUTCHours(Number(match[4]), Number(match[5]), Number(match[6]), 0);
  return BigInt(calendar.getTime()) * 1000n + BigInt((match[7] || '').padEnd(6, '0').slice(0, 6));
}
