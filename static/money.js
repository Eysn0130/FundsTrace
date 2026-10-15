/* Lossless legacy monetary display. Authoritative amounts arrive as decimal strings. */
(function (root) {
  'use strict';
  function cents(value) {
    // Safe integers support old count-free fixtures; floats must never be accepted as money.
    if (typeof value === 'number' && !Number.isSafeInteger(value)) throw new TypeError('Money must be a decimal string');
    const match = /^([+-]?)(\d+)(?:\.(\d{1,2}))?$/.exec(String(value));
    if (!match) throw new TypeError('Invalid money value');
    const result = BigInt(match[2]) * 100n + BigInt((match[3] || '').padEnd(2, '0'));
    return match[1] === '-' ? -result : result;
  }
  function format(value, missing = '—') {
    if (value == null || value === '') return missing;
    try {
      const amount = cents(value), absolute = amount < 0n ? -amount : amount;
      return `${amount < 0n ? '-' : ''}${String(absolute / 100n).replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${String(absolute % 100n).padStart(2, '0')}`;
    } catch (_) { return '金额格式错误'; }
  }
  function count(value) {
    const text = String(value ?? 0);
    return /^-?\d+$/.test(text) ? text.replace(/\B(?=(\d{3})+(?!\d))/g, ',') : '—';
  }
  function currency(value, code = 'CNY') {
    const normalized = code == null ? '' : String(code);
    const prefix = normalized === 'CNY' ? '¥' : normalized ? normalized + ' ' : '未标注币种 ';
    return `${prefix}${format(value)}`;
  }
  function totals(rows, fallback = null, code = 'CNY') {
    if (Array.isArray(rows) && rows.length) return rows.map(row => currency(row.amount, row.currency)).join('\n');
    return fallback == null ? '—' : currency(fallback, code);
  }
  root.FundsMoney = Object.freeze({ cents, format, count, currency, totals });
})(typeof globalThis === 'undefined' ? self : globalThis);
