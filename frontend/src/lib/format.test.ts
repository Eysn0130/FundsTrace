import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { displayOperationalTime, displayTime, formatMoney } from './format';

const legacyMoneySource = readFileSync(resolve(process.cwd(), '../static/money.js'), 'utf8');
const legacyMoney = runInNewContext(`${legacyMoneySource}\nFundsMoney;`) as {
  format(value: string | null | undefined): string;
};

describe('decimal presentation', () => {
  it.each<[string | null | undefined, string]>([
    ['9999999999999999.99', '9,999,999,999,999,999.99'],
    ['9999999999999999.98', '9,999,999,999,999,999.98'],
    ['-123.45', '-123.45'],
    ['-0.01', '-0.01'],
    ['0.00', '0.00'],
    ['0', '0.00'],
    ['1.2', '1.20'],
    ['1234567', '1,234,567.00'],
    ['+0000123.40', '123.40'],
    ['-0.00', '0.00'],
    [null, '—'],
    [undefined, '—'],
    ['', '—'],
    ['NaN', '金额格式错误'],
    ['Infinity', '金额格式错误'],
    ['0.001', '金额格式错误'],
    ['1.000', '金额格式错误'],
    ['1e3', '金额格式错误'],
    [' 1.00', '金额格式错误'],
    ['1,000.00', '金额格式错误'],
    ['12junk34', '金额格式错误'],
  ])('uses the same lossless legacy and React display for %s', (value, expected) => {
    expect(legacyMoney.format(value)).toBe(expected);
    expect(formatMoney(value)).toBe(expected);
  });
});

describe('timestamp presentation', () => {
  it('labels UTC operational timestamps without applying the browser timezone', () => {
    expect(displayOperationalTime('2026-10-15T04:30:00Z')).toBe('2026-10-15 04:30:00 UTC');
    expect(displayOperationalTime('2026-10-15T04:30:00.123456Z')).toBe('2026-10-15 04:30:00 UTC');
  });

  it('does not assign a timezone to legacy naive times or change transaction presentation', () => {
    expect(displayOperationalTime('2026-10-14 21:30:00')).toBe('2026-10-14 21:30:00');
    expect(displayOperationalTime(null)).toBe('—');
    expect(displayTime('2026-01-01T10:00:00')).toBe('2026-01-01 10:00:00');
    expect(displayTime('2026-01-01T10:00:00.123456')).toBe('2026-01-01 10:00:00.123456');
    expect(displayTime('2026-01-01T10:00:00Z')).toBe('2026-01-01 10:00:00Z');
    expect(displayTime('2026-01-01T10:00:00.123456Z')).toBe('2026-01-01 10:00:00.123456Z');
    expect(displayTime('2026-01-01T10:00:00.123456+08:00')).toBe('2026-01-01 10:00:00.123456+08:00');
  });
});
