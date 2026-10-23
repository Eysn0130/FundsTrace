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
  const shapes = new Set(['circle', 'rect', 'diamond', 'ellipse']);
  const icons = new Set(['none', 'person', 'organization', 'account', 'flag']);
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const bounded = (value, min, max) => typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
  const color = value => typeof value === 'string' && /^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(value);
  const accountId = node => typeof node.account === 'string' ? node.account
    : typeof node.id === 'string' ? node.id : null;
  const palettes = Object.freeze({
    dark: Object.freeze({ ink: '#dfe3ea', mutedEdge: '#3f4854', edge: '#7f8b99', selection: '#66d9ef' }),
    light: Object.freeze({ ink: '#111111', mutedEdge: '#c9c9c9', edge: '#7f8b99', selection: '#2367ad' }),
  });
  const palette = theme => palettes[theme === 'light' ? 'light' : 'dark'];

  function sanitizeLabel(input) {
    const result = {};
    if (!object(input)) return result;
    if (families.has(input.fontFamily)) result.fontFamily = input.fontFamily;
    if (bounded(input.fontSize, 8, 32)) result.fontSize = input.fontSize;
    if (weights.has(input.fontWeight)) result.fontWeight = input.fontWeight;
    if (input.fontStyle === 'normal' || input.fontStyle === 'italic') result.fontStyle = input.fontStyle;
    if (typeof input.underline === 'boolean') result.underline = input.underline;
    if (color(input.fill)) result.fill = input.fill;
    if (bounded(input.lineHeight, 8, 64)) result.lineHeight = input.lineHeight;
    return result;
  }

  // One effective style for drawing, measurement, controls and offline output.
  // Old snapshots may contain only a fontSize or an undersized lineHeight.
  function effectiveLabel(kind, input, theme = 'dark') {
    const node = kind === 'node', custom = sanitizeLabel(input);
    const result = { fill: palette(theme).ink, fontSize: 13, lineHeight: 18,
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
    if(shapes.has(input.shape))result.shape=input.shape;
    if(icons.has(input.icon))result.icon=input.icon;
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

  function groups(input) {
    const ids = new Set();
    return (Array.isArray(input) ? input : []).slice(0, 200).flatMap(item => {
      if (!object(item) || typeof item.id !== 'string' || !item.id || item.id.length > 160 || ids.has(item.id)) return [];
      const name = typeof item.name === 'string' ? item.name.trim().slice(0, 60) : '';
      if (!name) return [];
      ids.add(item.id);
      return [{id:item.id,name,color:color(item.color)?item.color:'#7f8b99',
        members:accounts(item.members),notes:typeof item.notes==='string'?item.notes.trim().slice(0, 1000):''}];
    });
  }

  function timeWindow(input) {
    const source=object(input)?input:{};
    // These are original transaction-local civil times, never converted to UTC.
    const civil=value=>typeof value==='string'&&/^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?)?$/.test(value)?value.replace('T',' '):'';
    return {start:civil(source.start),end:civil(source.end),includeUndated:source.includeUndated!==false};
  }

  // 标注可选字体限定在系统常见中文字体，避免随意字符串进入渲染与导出。
  const ANNOTATION_FONTS = new Set(['Arial', 'Microsoft YaHei', 'SimSun', 'SimHei', 'KaiTi', 'FangSong']);
  const ANNOTATION_DEFAULT_COLOR = '#e3a12b';
  const ANNOTATION_COLOR = /^#[0-9a-f]{6}$/i;

  // 主体标注锚定在节点上：anchor 只在“图形正上方”和“文字下方”之间取，dx/dy 是相对锚点的微调；
  // size/fontFamily/color 决定标注自身的外观，非法值回落到默认。
  function annotations(input) {
    if (!Array.isArray(input)) return [];
    const out = [], ids = new Set();
    for (const item of input.slice(0, 200)) {
      if (!object(item)) continue;
      // 多行标注：统一换行符、逐行去首尾空白、最多 6 行、200 字。
      const text = typeof item.text === 'string'
        ? item.text.replace(/\r\n?/g, '\n').split('\n').map(line => line.trim()).join('\n')
          .replace(/\n{3,}/g, '\n\n').replace(/^\n+|\n+$/g, '').split('\n').slice(0, 6).join('\n').slice(0, 200)
        : '';
      const id = typeof item.id === 'string' ? item.id : '';
      if (!text || !id || ids.has(id)) continue;
      ids.add(id);
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
      pinnedAccounts: accounts(source.pinnedAccounts),
      groups: groups(source.groups),
      timeWindow: timeWindow(source.timeWindow),
    };
  }

  function decoration(item, name, kind, attrs) {
    const group=item?.getContainer?.(), existing=(group?.get?.('children')||[]).find(shape=>shape.get('name')===name);
    if(!attrs){existing?.remove?.();return null;}
    if(existing){existing.attr(attrs);existing.show?.();return existing;}
    return group?.addShape?.(kind,{name,capture:false,attrs})||null;
  }

  // Built-in vector marks are explicit analyst display choices, never detected roles.
  function decorateNode(item, input, options={}) {
    const style=sanitizeNode(input), model=item.getModel(), size=Array.isArray(model.size)?Math.max(...model.size):model.size||32;
    const ink=options.ink||'#24394c', k=Math.min(1.4,size/36), path=[];
    const command=(op,...values)=>path.push([op,...values.map(value=>value*k)]);
    if(style.icon==='person') {
      command('M',-4,-7);command('Q',-4,-12,0,-12);command('Q',4,-12,4,-7);command('Q',4,-2,0,-2);command('Q',-4,-2,-4,-7);
      command('M',-8,9);command('Q',-8,0,0,0);command('Q',8,0,8,9);
    }else if(style.icon==='organization') {
      command('M',-9,9);command('L',-9,-9);command('L',9,-9);command('L',9,9);command('Z');
      for(const x of [-4,4])for(const y of [-4,2]){command('M',x-1,y);command('L',x+1,y);}
      command('M',-2,9);command('L',-2,5);command('L',2,5);command('L',2,9);
    }else if(style.icon==='account') {
      command('M',-10,-7);command('L',10,-7);command('L',10,7);command('L',-10,7);command('Z');
      command('M',-10,-2);command('L',10,-2);command('M',-6,3);command('L',-1,3);
    }else if(style.icon==='flag') {
      command('M',-5,10);command('L',-5,-10);command('L',8,-10);command('L',4,-4);command('L',-5,-4);
    }
    decoration(item,'funds-node-icon','path',path.length?{path,stroke:ink,lineWidth:1.6,fill:null,lineCap:'round',lineJoin:'round'}:null);
    decoration(item,'funds-node-pin','circle',options.pinned?{x:size/2-2,y:-size/2+2,r:3,fill:'#e3a12b',stroke:ink,lineWidth:1}:null);
    const assigned=groups(options.groups?.map(group=>({...group,members:group.members||[]}))), label=(item.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='text-shape');
    const visible=label&&label.get?.('visible')!==false&&model.label!==undefined;
    const bounds=label?.getBBox?.(), annotation=(item.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='funds-node-annotation')?.getBBox?.();
    const y=Math.max(size/2,Number.isFinite(bounds?.maxY)?bounds.maxY:size/2,Number.isFinite(annotation?.maxY)?annotation.maxY:size/2)+5;
    decoration(item,'funds-node-group','text',assigned.length&&visible?{x:0,y,text:`分组：${assigned[0].name}${assigned.length>1?' +'+(assigned.length-1):''}`,
      fontSize:11,lineHeight:15,fontFamily:'Arial',fill:assigned[0].color,textAlign:'center',textBaseline:'top'}:null);
  }

  function decorateLabel(item, input) {
    const style=sanitizeLabel(input), text=(item.getContainer?.()?.get?.('children')||[]).find(shape=>shape.get('name')==='text-shape');
    const bounds=text?.getBBox?.();
    if(!style.underline||!text||text.get?.('visible')===false||item.getModel().label===undefined||!bounds||![bounds.minX,bounds.maxX,bounds.minY,bounds.maxY].every(Number.isFinite)) {
      decoration(item,'funds-label-underline','path',null);return;
    }
    const lines=String(text.attr('text')??item.getModel().label).split('\n'), height=Math.max(1,(bounds.maxY-bounds.minY)/lines.length),path=[];
    for(let i=0;i<lines.length;i++)if(lines[i])path.push(['M',bounds.minX,bounds.minY+height*(i+1)-1],['L',bounds.maxX,bounds.minY+height*(i+1)-1]);
    decoration(item,'funds-label-underline','path',{path,stroke:text.attr('fill')||style.fill||'#111111',lineWidth:1,fill:null});
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

  return Object.freeze({ palette, sanitizeLabel, effectiveLabel, sanitizeNode, sanitizeEdge, create, displayData, decorateNode, decorateLabel });
});
