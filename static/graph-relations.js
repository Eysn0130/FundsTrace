/* Display-only directed-relation money policy. No membership or raw-flow changes. */
(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports && !root.FundsMoney) require('./money.js');
  const api = factory(root.FundsMoney || (typeof globalThis !== 'undefined' && globalThis.FundsMoney));
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.FundsGraphRelations = api;
})(typeof window === 'undefined' ? globalThis : window, function (money) {
  'use strict';

  const SCALE = 1000000n;
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  // Match app.js/graph-view.js: an API relation is one directed account pair.
  // Ignore G6-generated IDs, which can change when rebuilding the visible graph.
  const edgeKey = edge => edge.source + '=>' + edge.target;
  const nodeId = node => typeof node.account === 'string' ? node.account
    : typeof node.id === 'string' ? node.id : null;

  function failure(code, message, field) {
    const error = new TypeError(message);
    error.code = code;
    if (field) error.field = field;
    return error;
  }

  function exactAmount(value, field) {
    if (!money || typeof money.cents !== 'function') {
      throw failure('MONEY_UNAVAILABLE', '精确金额模块未加载', field);
    }
    if (typeof value !== 'string' || !/^\d+(?:\.\d{1,2})?$/.test(value)) {
      throw failure('INVALID_AMOUNT', '金额须为非负数，且最多保留两位小数', field);
    }
    return money.cents(value);
  }

  function decimal(cents) {
    return String(cents / 100n) + '.' + String(cents % 100n).padStart(2, '0');
  }

  function normalizeFilter(input = {}) {
    if (input == null || typeof input !== 'object' || Array.isArray(input)) {
      throw failure('INVALID_FILTER', '关系筛选参数无效');
    }
    const currency = input.currency == null ? null : input.currency;
    if (currency !== null && typeof currency !== 'string') {
      throw failure('INVALID_CURRENCY', '请选择明确的币种', 'currency');
    }
    if (currency !== null && currency.length > 32) {
      throw failure('INVALID_CURRENCY', '币种名称不能超过 32 个字符', 'currency');
    }
    const amounts = {};
    for (const field of ['min', 'max']) {
      const value = input[field];
      if (value == null || value === '') amounts[field] = '';
      else {
        if (typeof value !== 'string') throw failure('INVALID_AMOUNT', '金额须以十进制文本输入', field);
        const text = value.trim();
        if (!text) amounts[field] = '';
        else {
          amounts[field] = decimal(exactAmount(text, field));
          // Keep applied display settings compatible with the HTML export contract.
          if (amounts[field].length > 40) throw failure('AMOUNT_TOO_LONG', '金额最多支持 37 位整数和两位小数', field);
        }
      }
    }
    if (currency === null && (amounts.min !== '' || amounts.max !== '')) {
      throw failure('CURRENCY_REQUIRED', '金额筛选须先选择币种', 'currency');
    }
    if (amounts.min !== '' && amounts.max !== '' && money.cents(amounts.min) > money.cents(amounts.max)) {
      throw failure('REVERSED_RANGE', '最低金额不能大于最高金额', 'max');
    }
    // An explicitly selected currency, including '' (unlabelled), is a filter.
    return { currency, min: amounts.min, max: amounts.max, active: currency !== null };
  }

  function validateFilter(input) {
    try { return { ok: true, filter: normalizeFilter(input) }; }
    catch (error) {
      return { ok: false, error: error.message, code: error.code || 'INVALID_FILTER', field: error.field || null };
    }
  }

  function amountFor(edge, currency) {
    const totals = Array.isArray(edge.amount_totals) ? edge.amount_totals : [];
    const matches = totals.filter(row => row && row.currency === currency);
    if (!matches.length) return null;
    if (matches.length !== 1) {
      throw failure('AMBIGUOUS_CURRENCY_TOTAL', '同一关系包含重复的币种合计，不能确定金额');
    }
    return exactAmount(matches[0].amount, 'amount_totals');
  }

  function data(graph = {}, input = {}, options = {}) {
    const filter = normalizeFilter(input);
    graph = graph || {};
    const sourceNodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const sourceEdges = Array.isArray(graph.edges) ? graph.edges : [];
    const validNodes = new Set(sourceNodes.map(nodeId).filter(id => id !== null));
    const nodeIds = new Set(), edgeKeys = new Set(), edges = [];
    let missingCurrencyEdges = 0;
    const minimum = filter.min === '' ? null : money.cents(filter.min);
    const maximum = filter.max === '' ? null : money.cents(filter.max);
    for (const edge of sourceEdges) {
      if (filter.active) {
        const amount = amountFor(edge, filter.currency);
        if (amount === null) { missingCurrencyEdges += 1; continue; }
        if (minimum !== null && amount < minimum || maximum !== null && amount > maximum) continue;
        if (!validNodes.has(edge.source) || !validNodes.has(edge.target)) continue;
      }
      edges.push(edge); edgeKeys.add(edgeKey(edge));
      if (validNodes.has(edge.source)) nodeIds.add(edge.source);
      if (validNodes.has(edge.target)) nodeIds.add(edge.target);
    }
    const nodes = !filter.active || options.keepIsolated === true
      ? sourceNodes.slice() : sourceNodes.filter(node => nodeIds.has(nodeId(node)));
    if (!filter.active || options.keepIsolated === true) {
      for (const id of validNodes) nodeIds.add(id);
    }
    return { nodes, edges, nodeIds, edgeKeys, filter, stats: {
      inputNodes: sourceNodes.length, inputEdges: sourceEdges.length,
      visibleNodes: nodes.length, visibleEdges: edges.length,
      excludedEdges: sourceEdges.length - edges.length, missingCurrencyEdges,
    } };
  }

  function currencyChoices(edges = []) {
    const currencies = new Set();
    for (const edge of edges || []) {
      for (const total of Array.isArray(edge.amount_totals) ? edge.amount_totals : []) {
        if (total && typeof total.currency === 'string') currencies.add(total.currency);
      }
    }
    return [...currencies].sort((a, b) => a < b ? -1 : a > b ? 1 : 0)
      .map(value => ({ value, label: value === '' ? '未标注币种' : value, unknown: value === '' }));
  }

  function encodeWidths(edges = [], currency, options = {}) {
    if (typeof currency !== 'string') throw failure('CURRENCY_REQUIRED', '金额线宽须先选择币种', 'currency');
    const minWidth = own(options, 'minWidth') ? options.minWidth : 1.5;
    const maxWidth = own(options, 'maxWidth') ? options.maxWidth : 7;
    if (typeof minWidth !== 'number' || typeof maxWidth !== 'number' || !Number.isFinite(minWidth)
      || !Number.isFinite(maxWidth) || minWidth <= 0 || maxWidth < minWidth) {
      throw failure('INVALID_WIDTH_RANGE', '线宽范围须为正数，且最小线宽不能大于最大线宽');
    }
    const amounts = new Map(), widths = new Map(), missing = new Set(), invalid = new Map();
    let minimum = null, maximum = null;
    for (const edge of edges || []) {
      const key = edgeKey(edge);
      let amount;
      try { amount = amountFor(edge, currency); }
      catch (error) { invalid.set(key, error.message); continue; }
      if (amount === null) { missing.add(key); continue; }
      amounts.set(key, amount);
      if (minimum === null || amount < minimum) minimum = amount;
      if (maximum === null || amount > maximum) maximum = amount;
    }
    const equalWidth = maximum === 0n ? minWidth : (minWidth + maxWidth) / 2;
    for (const [key, amount] of amounts) {
      // Only a bounded ratio becomes Number; authoritative cents never do.
      // A square-root scale makes smaller relations legible in a skewed graph.
      const normalized = minimum === maximum ? null
        : Number((amount - minimum) * SCALE / (maximum - minimum)) / Number(SCALE);
      widths.set(key, normalized === null ? equalWidth
        : minWidth + Math.sqrt(normalized) * (maxWidth - minWidth));
    }
    return { widths, missing, invalid, legend: {
      currency, scale: 'sqrt', minAmount: minimum === null ? null : decimal(minimum),
      maxAmount: maximum === null ? null : decimal(maximum), minWidth, maxWidth,
      equalWidth: minimum !== null && minimum === maximum ? equalWidth : null,
      encodedCount: widths.size, missingCount: missing.size, invalidCount: invalid.size,
    } };
  }

  return Object.freeze({ edgeKey, normalizeFilter, validateFilter, data, currencyChoices, encodeWidths });
});
