import { describe, it, expect } from 'vitest';
import { displayOperationalTime, displayTime, formatMoney } from './format';

describe('decimal presentation', () => {
  it('preserves every cent beyond JavaScript safe-integer precision', () => {
    expect(formatMoney('9999999999999999.99')).toBe('9,999,999,999,999,999.99');
    expect(formatMoney('9999999999999999.98')).toBe('9,999,999,999,999,999.98');
    expect(formatMoney('-123.45')).toBe('-123.45');
    expect(formatMoney('0.00')).toBe('0.00');
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
    expect(displayTime('2026-01-01T10:00:00Z')).toBe('2026-01-01 10:00:00');
  });
});
