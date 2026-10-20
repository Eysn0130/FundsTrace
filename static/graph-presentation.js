/* Serializable, display-only graph settings. Transaction facts stay in the source graph. */
(function (root, factory) {
  'use strict';
  const relations = root.FundsGraphRelations || (typeof module === 'object' && module.exports
    ? require('./graph-relations.js') : null);
  const api = factory(relations);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.FundsGraphPresentation = api;
})(typeof window === 'undefined' ? globalThis : window, function (relations) {
  'use strict';

  const families = new Set(['Microsoft YaHei', 'PingFang SC', 'SimSun', 'SimHei', 'Arial', 'system-ui', 'sans-serif']);
  const weights = new Set(['normal', 'bold', '400', '700', 400, 700]);
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const bounded = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
  const color = value => typeof value === 'string' && /^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(value);
  const accountId = node => typeof node.account === 'string' ? node.account
    : typeof node.id === 'string' ? node.id : null;

  function sanitizeLabel(input) {
    const result = {};
    if (!object(input)) return result;
    if (families.has(input.fontFamily)) result.fontFamily = input.fontFamily;
    if (bounded(input.fontSize, 8, 32)) result.fontSize = input.fontSize;
    if (weights.has(input.fontWeight)) result.fontWeight = input.fontWeight;
    if (input.fontStyle === 'normal' || input.fontStyle === 'italic') result.fontStyle = input.fontStyle;
    if (color(input.fill)) result.fill = input.fill;
    if (bounded(input.lineHeight, 8, 64)) result.lineHeight = input.lineHeight;
    return result;
  }

  // One effective style for drawing, measurement, controls and offline output.
  // Old snapshots may contain only a fontSize or an undersized lineHeight.
  function effectiveLabel(kind, input) {
    const node = kind === 'node', custom = sanitizeLabel(input);
    const result = { fill: '#dfe3ea', fontSize: 13, lineHeight: 18,
      fontFamily: 'Arial', fontWeight: 'normal', fontStyle: 'normal',
      stroke:null,lineWidth:0,
      ...custom };
    result.lineHeight = Math.max(result.lineHeight, result.fontSize + 4);
    return result;
  }

  function sanitizeEdge(input) {
    const result = {};
    if (!object(input)) return result;
    if (color(input.stroke)) result.stroke = input.stroke;
    if (bounded(input.lineWidth, 0.5, 12)) result.lineWidth = input.lineWidth;
    if (Array.isArray(input.lineDash) && (input.lineDash.length === 0
      || input.lineDash.length === 2 && (input.lineDash[0] === 6 && input.lineDash[1] === 4
        || input.lineDash[0] === 2 && input.lineDash[1] === 3))) result.lineDash = input.lineDash.slice();
    return result;
  }
  function sanitizeNode(input) {
    const result={};if(!object(input))return result;
    if(color(input.stroke))result.stroke=input.stroke;
    if(bounded(input.lineWidth,0.5,5))result.lineWidth=input.lineWidth;
    if(bounded(input.size,24,96))result.size=input.size;
    return result;
  }

  function styleMap(input, sanitize) {
    if (!object(input)) return {};
    const entries = [];
    for (const [key, value] of Object.entries(input)) {
      if (!key) continue;
      const safe = sanitize(value);
      if (Object.keys(safe).length) entries.push([key, safe]);
    }
    // fromEntries creates own keys safely even for an account named '__proto__'.
    return Object.fromEntries(entries);
  }

  function accounts(input) {
    return Array.isArray(input) ? [...new Set(input.filter(value => typeof value === 'string' && value !== ''))] : [];
  }

  // 标注可选字体限定在系统常见中文字体，避免随意字符串进入渲染与导出。
  const ANNOTATION_FONTS = new Set(['Arial', 'Microsoft YaHei', 'SimSun', 'SimHei', 'KaiTi', 'FangSong']);
  const ANNOTATION_DEFAULT_COLOR = '#e3a12b';
  const ANNOTATION_COLOR = /^#[0-9a-f]{6}$/i;

  // 主体标注锚定在节点上：anchor 只在“图形正上方”和“文字下方”之间取，dx/dy 是相对锚点的微调；
  // size/fontFamily/color 决定标注自身的外观，非法值回落到默认。
  function annotations(input) {
    if (!Array.isArray(input)) return [];
    const out = [];
    for (const item of input.slice(0, 200)) {
      if (!object(item)) continue;
      // 多行标注：统一换行符、逐行去首尾空白、最多 6 行、200 字。
      const text = typeof item.text === 'string'
        ? item.text.replace(/\r\n?/g, '\n').split('\n').map(line => line.trim()).join('\n')
          .replace(/\n{3,}/g, '\n\n').replace(/^\n+|\n+$/g, '').split('\n').slice(0, 6).join('\n').slice(0, 200)
        : '';
      const id = typeof item.id === 'string' && item.id ? item.id.slice(0, 80) : '';
      if (!text || !id) continue;
      const size = Number(item.size);
      const dx = Number(item.dx), dy = Number(item.dy);
      out.push({
        id, text,
        anchor: item.anchor === 'above' ? 'above' : 'below',
        dx: Number.isFinite(dx) ? Math.max(-400, Math.min(400, dx)) : 0,
        dy: Number.isFinite(dy) ? Math.max(-400, Math.min(400, dy)) : 0,
        size: Number.isFinite(size) ? Math.min(40, Math.max(9, size)) : 13,
        fontFamily: ANNOTATION_FONTS.has(item.fontFamily) ? item.fontFamily : 'Arial',
        color: typeof item.color === 'string' && ANNOTATION_COLOR.test(item.color) ? item.color.toLowerCase() : ANNOTATION_DEFAULT_COLOR,
      });
    }
    return out;
  }
  function create(input) {
    const source = object(input) ? input : {};
    const checked = relations && typeof relations.validateFilter === 'function'
      ? relations.validateFilter(source.relationFilter || {}) : { ok: false };
    const amountWidth = object(source.amountWidth) ? source.amountWidth : {};
    const currency = typeof amountWidth.currency === 'string' ? amountWidth.currency : null;
    return {
      version: 1,
      relationFilter: checked.ok ? checked.filter : { currency: null, min: '', max: '', active: false },
      amountWidth: { enabled: amountWidth.enabled === true && currency !== null, currency },
      nodeLabels: styleMap(source.nodeLabels, sanitizeLabel),
      nodeStyles: styleMap(source.nodeStyles, sanitizeNode),
      edgeLabels: styleMap(source.edgeLabels, sanitizeLabel),
      edgeStyles: styleMap(source.edgeStyles, sanitizeEdge),
      hiddenAccounts: accounts(source.hiddenAccounts),
      onlyAccounts: Array.isArray(source.onlyAccounts) ? accounts(source.onlyAccounts) : null,
      annotations: annotations(source.annotations),
    };
  }

  function displayData(graph, presentation, neighborhoodIds) {
    if (!relations || typeof relations.data !== 'function') throw new Error('关系展示策略模块未加载');
    const source = object(graph) ? graph : {};
    const state = create(presentation);
    const sourceNodes = Array.isArray(source.nodes) ? source.nodes : [];
    const sourceEdges = Array.isArray(source.edges) ? source.edges : [];
    let neighborhood = null;
    if (neighborhoodIds != null) {
      if (typeof neighborhoodIds === 'string' || typeof neighborhoodIds[Symbol.iterator] !== 'function') {
        throw new TypeError('邻域节点须为账户集合');
      }
      neighborhood = new Set([...neighborhoodIds].filter(value => typeof value === 'string'));
    }
    const scopedNodes = sourceNodes.filter(node => neighborhood === null || neighborhood.has(accountId(node)));
    const scopedIds = new Set(scopedNodes.map(accountId).filter(id => id !== null));
    const scopedEdges = sourceEdges.filter(edge => scopedIds.has(edge.source) && scopedIds.has(edge.target));
    const filtered = relations.data({ nodes: scopedNodes, edges: scopedEdges }, state.relationFilter);
    const hidden = new Set(state.hiddenAccounts), only = state.onlyAccounts === null ? null : new Set(state.onlyAccounts);
    const nodes = filtered.nodes.filter(node => {
      const id = accountId(node);
      return id !== null && !hidden.has(id) && (only === null || only.has(id));
    });
    const nodeIds = new Set(nodes.map(accountId));
    const edges = filtered.edges.filter(edge => nodeIds.has(edge.source) && nodeIds.has(edge.target));
    return { nodes, edges, nodeIds, edgeKeys: new Set(edges.map(relations.edgeKey)), filter: filtered.filter,
      stats: { ...filtered.stats, fullNodes: sourceNodes.length, fullEdges: sourceEdges.length,
        relationVisibleNodes: filtered.nodes.length, relationVisibleEdges: filtered.edges.length,
        visibleNodes: nodes.length, visibleEdges: edges.length,
        hiddenNodes: filtered.nodes.length - nodes.length, hiddenEdges: filtered.edges.length - edges.length } };
  }

  return Object.freeze({ sanitizeLabel, effectiveLabel, sanitizeNode, sanitizeEdge, create, displayData });
});
