import { describe, expect, it } from 'vitest';
import { clockPosition, moneyUnits, plotRatio, unitsMoney } from './report-geometry';

describe('exact money and chart geometry boundary', () => {
  it('preserves adjacent cents above safe integers and signed values', () => {
    const low = moneyUnits('9999999999999999.98'), high = moneyUnits('9999999999999999.99');
    expect(high - low).toBe(1n);
    expect(unitsMoney(high)).toBe('9999999999999999.99');
    expect(unitsMoney(moneyUnits('-0.01'))).toBe('-0.01');
    expect(plotRatio(low, low, high)).toBe(0);
    expect(plotRatio(high, low, high)).toBe(1);
  });
  it('uses one common signed domain and handles an empty range', () => {
    expect(plotRatio(0n, -100n, 100n)).toBe(.5);
    expect(plotRatio(0n, 0n, 0n)).toBe(0);
    expect(() => moneyUnits('unknown')).toThrow();
  });
  it('places sparse time buckets by calendar duration without inferring a timezone', () => {
    const first = clockPosition('2026-11-13 09:00:00'), near = clockPosition('2026-11-13 09:05:00'), last = clockPosition('2026-11-13 10:00:00');
    expect(plotRatio(near, first, last)).toBeCloseTo(1 / 12, 5);
    expect(clockPosition('2026-11-13 09:00:00.123456') - first).toBe(123456n);
    expect(clockPosition('2026-11-13 09:00:00Z')).toBe(first);
  });
});
