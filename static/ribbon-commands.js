/* Adapts the ribbon to the existing case/canvas commands; no parallel graph store. */
(function (root) {
  'use strict';
  let lastGraph = null, lastBinding = '', filterKey = '', widthKey = '', styleKey = '';
  let lastStatus = '', focusChrome = null, edgeGesture = null, pointerGesture = null;
  let relationFilter = null, relationChoices = [];
  const availability = new Map();
  const selected = () => [...(state.graphSelection?.ids || [])];
  const selectedEdge = () => state.graphView.selectedEdge && state.graphView.edgeItems.get(state.graphView.selectedEdge);
  const ready = () => !!state.caseId && !!state.graph && !state.graphLoading && !layoutJob
    && !root.FundsCanvasEditor?.history.status().busy;
  const currentGesture = gesture => gesture && gesture.graph === state.graph && gesture.view === state.graphView
    && isCurrentCase(gesture.context);
  const inspectingAllowed = () => ready() && !state.graphSelection?.status?.().dragging
    && !currentGesture(edgeGesture) && !currentGesture(pointerGesture);
  function inspectionTarget() {
    const ids = selected(), edge = selectedEdge();
    if (edge && !ids.length) return { kind: 'edge', item: edge };
    if (!edge && ids.length === 1) {
      const item = state.graph?.findById(ids[0]);
      if (item) return { kind: 'node', item, id: ids[0] };
    }
    return null;
  }
  const currencyValue = value => value === 'all' ? null : value?.startsWith('currency:') ? value.slice(9) : null;
  const currencyOption = value => value === null ? 'all' : 'currency:' + value;
  const currencyLabel = value => value === '' ? '未标注币种' : value;
  const labelColorValue = value => {
    const hex = (value || '#dfe3ea').toLowerCase();
    return /^#[\da-f]{3}$/.test(hex) ? '#' + [...hex.slice(1)].map(digit => digit + digit).join('') : hex;
  };
  // Aggregate each property independently. A shared font must remain visible even
  // when another property (for example colour) differs across the selection.
  function selectedLabelValues(ids, edge) {
    const kind = edge ? 'edge' : 'node';
    const styles = (edge ? [edgeKey(edge.getModel())] : ids.length ? ids : [undefined]).map(id => {
      const overrides = graphLabelStyle(kind, id);
      const style = typeof FundsGraphPresentation.effectiveLabel === 'function' ?
        FundsGraphPresentation.effectiveLabel(kind, overrides) : overrides;
      return {
        labelFontFamily: style.fontFamily || 'Arial',
        labelFontSize: style.fontSize || 13,
        labelBold: ['bold', 700, '700'].includes(style.fontWeight),
        labelItalic: style.fontStyle === 'italic',
        labelColor: labelColorValue(style.fill),
      };
    });
    if (!styles.length) return {};
    return Object.fromEntries(Object.keys(styles[0]).map(property => [property,
      styles.every(style => style[property] === styles[0][property]) ? styles[0][property] : { mixed: true }]));
  }

  function enable(command, value) {
    if (!root.FundsRibbon?.setCommandEnabled || availability.get(command) === value) return;
    availability.set(command, value); root.FundsRibbon.setCommandEnabled(command, value);
  }
  function populateCurrencies() {
    const choices = FundsGraphRelations.currencyChoices(state.lastGraph.edges);
    relationChoices = choices;
    for (const [id, all] of [['relationCurrency', true], ['widthCurrency', false]]) {
      if (all && relationFilter) continue;
      const select = document.getElementById(id); if (!select) continue;
      const previous = select.value;
      select.replaceChildren();
      for (const choice of [...(all ? [{value:null,label:'全部币种（不筛选）'}] : []), ...choices]) {
        const option = document.createElement('option');
        option.value = currencyOption(choice.value); option.textContent = choice.label; select.appendChild(option);
      }
      // Keep a saved, currently absent currency explicit: it produces an empty view,
      // never an implicit replacement with CNY or a mixed-currency sum.
      const saved = all ? state.presentation.relationFilter.currency : state.presentation.amountWidth.currency;
      if (saved !== null && !choices.some(choice => choice.value === saved)) {
        const option = document.createElement('option'); option.value = currencyOption(saved);
        option.textContent = `${currencyLabel(saved)}（当前图无此币种）`; select.appendChild(option);
      }
      if ([...select.options].some(option => option.value === previous)) select.value = previous;
      else if (!all && choices.some(choice => choice.value === 'CNY')) select.value = currencyOption('CNY');
    }
  }
  function sync() {
    if (!root.FundsRibbon?.setStatus || typeof FundsGraphRelations === 'undefined') return;
    const binding = JSON.stringify(caseContext()), p = state.presentation, ids = selected(), edge = selectedEdge();
    if (lastGraph !== state.lastGraph || lastBinding !== binding) {
      lastGraph = state.lastGraph; populateCurrencies();
      if (lastBinding !== binding) { filterKey = ''; widthKey = ''; styleKey = ''; focusChrome = null; }
      lastBinding = binding;
      for (const link of document.querySelectorAll('[data-analysis-algorithm]')) {
        link.href=`/analysis?case_id=${encodeURIComponent(state.caseId)}&algorithm=${encodeURIComponent(link.dataset.analysisAlgorithm)}`;
      }
    }
    const cameraReady=graphCameraInteractionAllowed();
    const nextStatus = JSON.stringify([ids.length,!!edge,!ready(),cameraReady]);
    if (lastStatus !== nextStatus) {
      root.FundsRibbon.setStatus({selectionCount:ids.length,edgeSelectionCount:edge ? 1 : 0,busy:!ready(),cameraReady}); lastStatus=nextStatus;
    }
    const hasNodes = !!state.graphView.index?.nodeIds.size;
    for (const command of ['selectAll','invertSelection','findNode']) enable(command, hasNodes);
    enable('locateNew',!!state.graphView.recentNodeIds?.size);
    for (const command of ['clearSelection','fitSelection','focusSelection']) enable(command, ids.length > 0 || !!edge);
    for (const command of ['accountDetails','emphasize']) enable(command, ids.length === 1);
    enable('inspectSelection', inspectingAllowed() && !!inspectionTarget());
    enable('viewFlows', !!edge || ids.length === 1);
    for (const command of ['hideSelection','showOnlySelection']) enable(command, ids.length > 0);
    enable('annotateSelection', ids.length === 1);
    for (const command of ['alignLeft','alignTop']) enable(command, ids.length > 1);
    for (const command of ['distributeHorizontal','distributeVertical']) enable(command, ids.length > 2);
    enable('restoreHidden', p.hiddenAccounts.length > 0 || p.onlyAccounts !== null);
    for (const command of ['labelFontFamily','labelFontSize','labelBold','labelItalic','labelColor']) enable(command, !!edge || ids.length > 0);
    for (const command of ['nodeBorderColor','nodeBorderWidth','nodeSize']) enable(command, ids.length>0 && !edge);
    for (const id of ['btnNodeBorder','btnNodeSize']) { const button=$('#'+id);if(button)button.disabled=!ready()||!ids.length||!!edge; }
    for (const command of ['edgeColor','edgeDash']) enable(command, !!edge);
    enable('edgeWidth', !!edge && !p.amountWidth.enabled);
    const nextFilter = JSON.stringify(p.relationFilter);
    if (!relationFilter && filterKey !== nextFilter) {
      filterKey = nextFilter;
      root.FundsRibbon.setValues({relationCurrency:currencyOption(p.relationFilter.currency),relationMin:p.relationFilter.min,relationMax:p.relationFilter.max});
      const status = $('#relationFilterStatus');
      if (status) status.textContent = p.relationFilter.active ? '已应用到当前图谱；流水查询条件保持不变' : '按单币种聚合关系金额筛选，仅影响当前图谱';
    }
    const nextWidth = JSON.stringify(p.amountWidth);
    if (widthKey !== nextWidth) {
      widthKey = nextWidth;
      root.FundsRibbon.setValues({amountWidthCk:p.amountWidth.enabled});
      if (p.amountWidth.currency !== null) root.FundsRibbon.setValues({widthCurrency:currencyOption(p.amountWidth.currency)});
    }
    const widthCk = $('#amountWidthCk'); if (widthCk) widthCk.disabled = !ready() || !$('#widthCurrency')?.options.length;
    const labelValues = selectedLabelValues(ids, edge);
    const edgeStyle = edge ? p.edgeStyles[edgeKey(edge.getModel())] || {} : {};
    const nodeValues={};
    for(const [command,property,fallback] of [['nodeBorderColor','stroke',null],['nodeBorderWidth','lineWidth',null],['nodeSize','size','auto']]) {
      const values=ids.map(id=>{const model=state.graphView.nodeItems?.get(id)?.getModel?.();return p.nodeStyles?.[id]?.[property]??(property==='size'?fallback:model?.style?.[property]??fallback);});
      nodeValues[command]=values.length&&values.every(value=>value===values[0])?values[0]:{mixed:true};
    }
    const nextStyle = JSON.stringify([ids,state.graphView.selectedEdge,labelValues,edgeStyle,nodeValues]);
    if (styleKey !== nextStyle) {
      styleKey = nextStyle;
      root.FundsRibbon.setValues({...labelValues,...nodeValues,
        edgeColor:edgeStyle.stroke || '#7f8b99',edgeWidth:edgeStyle.lineWidth || 1.4,
        edgeDash:edgeStyle.lineDash?.[0] === 6 ? 'dashed' : edgeStyle.lineDash?.[0] === 2 ? 'dotted' : 'solid'});
    }
    const encoding = state.graphView.amountWidths;
    let legend = $('#amountWidthLegend');
    if (!legend && $('#legend')) { legend = document.createElement('div'); legend.id='amountWidthLegend'; legend.className='amount-width-legend'; $('#legend').appendChild(legend); }
    if (legend) {
      legend.hidden = !p.amountWidth.enabled;
      legend.textContent = encoding ? `${currencyLabel(encoding.legend.currency)} 金额→线宽 · ${encoding.legend.minAmount ?? '—'} — ${encoding.legend.maxAmount ?? '—'} · 全图统一比例`
        + (encoding.missing.size ? ` · ${encoding.missing.size} 条无该币种，使用普通线宽` : '')
        + (encoding.invalid.size ? ` · ${encoding.invalid.size} 条金额无效，未编码` : '') : '';
    }
    const edgeWidth = $('#edgeWidth');
    if (edgeWidth) edgeWidth.title = p.amountWidth.enabled ? '金额线宽已启用；关闭后可设置固定线宽' : '所选关系的固定线宽';
    relationFilter?.update();
  }
  function filterBlocker(ignoreHistory = false) {
    const context = caseContext();
    if (!context.caseId || !state.graph) return '请先打开案件画布';
    if (root.FundsDataToolsActions?.blocked(context) || root.FundsMainAccountsActions?.blocked(context)) return '案件资料操作尚未结束，请先完成或核对';
    const cases = caseWorkspace?.status(), canvas = root.FundsCanvasActions?.status(), exported = exportWorkspace?.status();
    if (cases?.pending || cases?.phase === 'unknown' || canvas?.pending || canvas?.unknown || exported?.pending || batchView.deleting) return '案件或画布正在处理，请稍后应用';
    if ([...accountDataWrites].some(write => String(write.caseId) === String(context.caseId)) || nodeWrites.size || pcState.binding || nodeConfirmation || nodeWorkspace?.confirming || ['loading','removing'].includes(nodeWorkspace?.phase)) return '账户资料正在处理，请稍后应用';
    if (hasNodeDrafts()) return '账户资料有未保存修改，请先保存或放弃后再应用筛选';
    if (state.graphLoading || layoutJob || state.graphView.exportDepth || state.graphSelection?.status?.().dragging || currentGesture(edgeGesture) || currentGesture(pointerGesture)
      || !ignoreHistory && root.FundsCanvasEditor?.history.status().busy) return '画布正在处理，请稍后应用';
    return '';
  }
  function installRelationFilter() {
    if (!root.FundsRelationFilter) return;
    relationFilter = root.FundsRelationFilter.create({
      read: () => ({ binding: JSON.stringify(caseContext()), filter: state.presentation.relationFilter,
        graph: state.lastGraph, presentation: state.presentation, visibleIds: state.graphView.visibleIds,
        view: state.graphView, currencies: relationChoices, ready: !!state.caseId && !!state.graph && !state.graphLoading }),
      preview: (filter, snapshot) => {
        const result = FundsGraphPresentation.displayData(snapshot.graph, { ...snapshot.presentation, relationFilter: filter }, snapshot.visibleIds);
        return { nodes: result.stats.visibleNodes, edges: result.stats.visibleEdges };
      },
      blocker: () => filterBlocker(), formatCount: value => FundsMoney.count(value),
      commit: async (filter, binding) => {
        if (binding !== JSON.stringify(caseContext())) return false;
        const reason = filterBlocker(); if (reason) throw new Error(reason);
        return edit(filter.active ? '筛选图内关系金额' : '清除图内金额筛选', p => {
          if (binding !== JSON.stringify(caseContext())) throw new Error('画布已切换，请重新打开筛选');
          const blocked = filterBlocker(true); if (blocked) throw new Error(blocked);
          // Recheck the composed view before publishing a history entry.
          FundsGraphPresentation.displayData(state.lastGraph, { ...p, relationFilter: filter }, state.graphView.visibleIds);
          p.relationFilter = filter;
        });
      }
    });
  }
  async function edit(label, change) {
    if (!ready()) return false;
    return root.FundsCanvasEditor.command(label, () => {
      savePositions(); captureEdgeLabelRatios();
      const next = newGraphPresentation(state.presentation);
      change(next);
      const previous = state.presentation;
      state.presentation = newGraphPresentation(next);
      try { state.forceFit = false; renderGraph(); }
      catch (error) {
        state.presentation=previous; state.forceRebuild=true;
        renderGraph(); throw error;
      }
      sync();
    });
  }
  function fitItems(items) {
    if (typeof focusGraphItems === 'function') return focusGraphItems(items, { preferredZoom: 2 }).then(result => result.ok);
    const graph=state.graph, container=$('#graphContainer');
    const models=items.map(item=>item.getModel()).filter(m=>Number.isFinite(m.x)&&Number.isFinite(m.y));
    if (!models.length) return false;
    state.graphCamera?.resize?.();
    state.graphCamera?.cancelComposition?.();
    const bounds=models.map(m=>({x:m.x,y:m.y,r:(Array.isArray(m.size)?Math.max(...m.size):m.size||32)/2}));
    const minX=Math.min(...bounds.map(m=>m.x-m.r)),maxX=Math.max(...bounds.map(m=>m.x+m.r));
    const minY=Math.min(...bounds.map(m=>m.y-m.r)),maxY=Math.max(...bounds.map(m=>m.y+m.r));
    const zoom=Math.max(.02,Math.min(2,(container.clientWidth-100)/Math.max(1,maxX-minX),(container.clientHeight-120)/Math.max(1,maxY-minY)));
    graph.zoomTo(zoom); const center=graph.getCanvasByPoint((minX+maxX)/2,(minY+maxY)/2);
    graph.translate(container.clientWidth/2-center.x,container.clientHeight/2-center.y);
    scheduleGraphViewport(); scheduleGraphLabels(); state.graphSelection?.refresh(); return true;
  }
  function fitSelection() {
    const edge=selectedEdge();
    const ids=edge?[edge.getModel().source,edge.getModel().target]:selected();
    return fitItems(ids.map(id=>state.graph.findById(id)).filter(Boolean));
  }
  function arrange(command) {
    const nodes=selected().map(id=>state.graph.findById(id)?.getModel()).filter(Boolean);
    const horizontal=command==='distributeHorizontal', distribute=command.startsWith('distribute');
    if(nodes.length<(distribute?3:2))return false;
    return root.FundsCanvasEditor.command(distribute?'均匀分布节点':'对齐节点',()=>{
      savePositions(); captureEdgeLabelRatios();
      const axis=command==='alignLeft'||horizontal?'x':'y';
      const ordered=[...nodes].sort((a,b)=>a[axis]-b[axis]||String(a.id).localeCompare(String(b.id)));
      const min=ordered[0][axis],max=ordered.at(-1)[axis],deltas=new Map();
      ordered.forEach((model,index)=>{
        const value=distribute?min+(max-min)*index/(nodes.length-1):min;
        const next={x:model.x,y:model.y,[axis]:value};
        deltas.set(model.id,{x:next.x-model.x,y:next.y-model.y}); state.positions[model.id]=next;
      });
      for(const edge of state.lastGraph.edges){
        const from=deltas.get(edge.source),to=deltas.get(edge.target);if(!from&&!to)continue;
        const key=edgeKey(edge),points=state.edgeBendPos[key];
        if(points)state.edgeBendPos[key]=points.map((point,index)=>{const t=(index+1)/(points.length+1);return{
          x:point.x+(from?.x||0)*(1-t)+(to?.x||0)*t,y:point.y+(from?.y||0)*(1-t)+(to?.y||0)*t};});
        delete state.edgeSnapshotLabels[key];
      }
      state.forceFit=false; renderGraph();
    });
  }
  async function execute(command,value) {
    if (command === 'findNode') return root.FundsNodeFinderActions?.search(value) ?? false;
    const cameraCommand=['zoomIn','zoomOut','zoomReset','fitSelection','focusSelection','locateNew'].includes(command);
    if (!(cameraCommand?graphCameraInteractionAllowed():ready())) { sync(); return false; }
    const ids=selected(),edge=selectedEdge();
    if(command==='inspectSelection'){
      const target=inspectionTarget();
      if(!inspectingAllowed()||!target)return false;
      const context=caseContext(),graph=state.graph,view=state.graphView;
      root.FundsRibbon?.closePopovers();
      if(target.kind==='edge')await showEdgeDrawer(target.item.getModel(),target.item);
      else await showNodeDrawer(target.id,target.item);
      const current=inspectionTarget();
      return isCurrentCase(context)&&state.graph===graph&&state.graphView===view
        &&current?.kind===target.kind&&current.item===target.item;
    }
    if(command==='selectAll'){state.graphSelection?.rebind(state.graph.getNodes().map(item=>item.getModel().id));return true;}
    if(command==='clearSelection'){closeDrawer();return true;}
    if(command==='invertSelection'){const previous=new Set(ids);state.graphSelection?.rebind(state.graph.getNodes().map(item=>item.getModel().id).filter(id=>!previous.has(id)));return true;}
    if(command==='locateNew'){root.FundsRibbon.closePopovers();root.FundsToolbarMenu?.close('activate');return locateGraphAdditions();}
    if(command==='fitSelection'||command==='focusSelection')return fitSelection();
    if(['zoomIn','zoomOut','zoomReset'].includes(command)){
      state.graphCamera?.resize?.();
      state.graphCamera?.cancelComposition?.();
      const zoom=command==='zoomReset'?1:state.graph.getZoom()*(command==='zoomIn'?1.2:1/1.2);
      const c=$('#graphContainer');state.graph.zoomTo(Math.max(.02,Math.min(4,zoom)),{x:c.clientWidth/2,y:c.clientHeight/2});
      scheduleGraphViewport();scheduleGraphLabels();state.graphSelection?.refresh();return true;
    }
    if(command==='accountDetails'||command==='emphasize'||command==='viewFlows'){
      if(command==='viewFlows'&&edge)return showEdgeDrawer(edge.getModel(),edge);
      if(ids.length!==1)return false;
      const context=caseContext(),id=ids[0];await showNodeDrawer(id,state.graph.findById(id));
      if(!isCurrentCase(context)||state.graphView.selectedAccount!==id)return false;
      if(command==='viewFlows')$('#ndBrowse')?.click();
      if(command==='emphasize'){$('#ndFocusScale')?.scrollIntoView({block:'nearest'});$('#ndFocusScale')?.focus();}
      return true;
    }
    if(command==='hideSelection'&&ids.length)return edit('隐藏选中账户',p=>{p.hiddenAccounts=[...new Set([...p.hiddenAccounts,...ids])];});
    if(command==='showOnlySelection'&&ids.length)return edit('仅显示选中账户',p=>{p.onlyAccounts=ids;p.hiddenAccounts=[];});
    if(command==='restoreHidden')return edit('恢复隐藏账户',p=>{p.hiddenAccounts=[];p.onlyAccounts=null;});
    if(['alignLeft','alignTop','distributeHorizontal','distributeVertical'].includes(command))return arrange(command);
    if(command.startsWith('label')&&['labelFontFamily','labelFontSize','labelBold','labelItalic','labelColor'].includes(command)){
      if(!edge&&!ids.length)return false;
      const patch=command==='labelFontFamily'?{fontFamily:value}:command==='labelFontSize'?{fontSize:Number(value),lineHeight:Number(value)+5}:
        command==='labelBold'?{fontWeight:value?'bold':'normal'}:command==='labelItalic'?{fontStyle:value?'italic':'normal'}:{fill:value};
      const safe=FundsGraphPresentation.sanitizeLabel(patch);if(!Object.keys(safe).length)return false;
      return edit('更改文字样式',p=>{const map=edge?p.edgeLabels:p.nodeLabels;for(const id of edge?[edgeKey(edge.getModel())]:ids)Object.defineProperty(map,id,{value:{...(map[id]||{}),...safe},enumerable:true,configurable:true,writable:true});});
    }
    if(['nodeBorderColor','nodeBorderWidth','nodeSize'].includes(command)) {
      if(edge||!ids.length)return false;
      const property=command==='nodeBorderColor'?'stroke':command==='nodeBorderWidth'?'lineWidth':'size';
      const patch=FundsGraphPresentation.sanitizeNode({[property]:property==='stroke'?value:Number(value)});
      if(!Object.keys(patch).length&&!(property==='size'&&value==='auto'))return false;
      return edit('更改节点外观',p=>{for(const id of ids){const style={...(p.nodeStyles[id]||{}),...patch};
        if(property==='size'&&value==='auto')delete style.size;
        Object.defineProperty(p.nodeStyles,id,{value:style,enumerable:true,configurable:true,writable:true});}});
    }
    if(['edgeColor','edgeWidth','edgeDash'].includes(command)&&edge){
      if(command==='edgeWidth'&&state.presentation.amountWidth.enabled)return false;
      const patch=command==='edgeColor'?{stroke:value}:command==='edgeWidth'?{lineWidth:Number(value)}:{lineDash:value==='dashed'?[6,4]:value==='dotted'?[2,3]:[]};
      const safe=FundsGraphPresentation.sanitizeEdge(patch),key=edgeKey(edge.getModel());
      return edit('更改关系线样式',p=>{Object.defineProperty(p.edgeStyles,key,{value:{...(p.edgeStyles[key]||{}),...safe},enumerable:true,configurable:true,writable:true});});
    }
    if (command === 'annotateSelection') return openAnnotationEditor();
    if(command==='styleReset')return edit('恢复默认图谱样式',p=>{p.nodeLabels={};p.nodeStyles={};p.edgeLabels={};p.edgeStyles={};p.annotations=[];syncGraphAnnotationsAfterReset();return true;});
    if(command==='toggleLegend'){$('#legend').hidden=!$('#legend').hidden;return true;}
    const shell=$('#workspace')?.fundsWorkspaceShell;
    if(command==='toggleSidebar'){shell?.setSidebarCollapsed(!shell.getState().sidebarCollapsed);return true;}
    if(command==='focusCanvas'&&shell){
      if(focusChrome){shell.setSidebarCollapsed(focusChrome.sidebarCollapsed);shell.setToolbarCollapsed(focusChrome.toolbarCollapsed);focusChrome=null;}
      else{focusChrome=shell.getState();shell.setSidebarCollapsed(true);shell.setToolbarCollapsed(true);}return true;
    }
    return false;
  }
  function applyFilter() {
    if (relationFilter) return relationFilter.apply();
    const result=FundsGraphRelations.validateFilter({currency:currencyValue($('#relationCurrency').value),min:$('#relationMin').value,max:$('#relationMax').value});
    const status=$('#relationFilterStatus');
    if(!result.ok){if(status)status.textContent=result.error;toast(result.error,'err');return false;}
    try{FundsGraphRelations.data(state.lastGraph,result.filter);}catch(error){if(status)status.textContent=error.message;toast(error.message,'err');return false;}
    return edit('筛选图内关系金额',p=>{p.relationFilter=result.filter;});
  }
  function applyWidth() {
    const currency=currencyValue($('#widthCurrency').value),enabled=$('#amountWidthCk').checked;
    if(enabled&&currency===null){sync();return false;}
    return edit('金额映射关系线宽',p=>{p.amountWidth={enabled,currency};});
  }
  function mount() {
    installRelationFilter();
    document.addEventListener('funds:ribbon-command',event=>{
      void Promise.resolve(execute(event.detail?.command,event.detail?.value)).catch(error=>toast(error.message||'操作失败','err')).finally(sync);
    });
    if (!relationFilter) {
      $('#btnRelationApply')?.addEventListener('click',()=>{void applyFilter();});
      $('#btnRelationClear')?.addEventListener('click',()=>{void edit('清除图内金额筛选',p=>{p.relationFilter={currency:null,min:'',max:'',active:false};});});
      for(const id of ['relationMin','relationMax'])document.getElementById(id)?.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();void applyFilter();}});
    }
    $('#amountWidthCk')?.addEventListener('change',()=>{void applyWidth();});
    $('#widthCurrency')?.addEventListener('change',()=>{if($('#amountWidthCk').checked)void applyWidth();});
    const container=$('#graphContainer');
    container?.addEventListener('pointerdown',event=>{
      if(event.button!==undefined&&event.button!==0)return;
      pointerGesture={context:caseContext(),graph:state.graph,view:state.graphView};sync();
    },true);
    container?.addEventListener('keydown',event=>{
      if(event.key!=='Enter'||event.defaultPrevented||event.repeat||event.isComposing
        ||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey
        ||event.target!==container||document.activeElement!==container
        ||event.target.closest?.('input,select,textarea,[contenteditable]:not([contenteditable="false"])'))return;
      const modal=[...document.querySelectorAll('.modal,dialog,[role="dialog"]')].some(node=>node.open
        ||(!node.hidden&&node.style.display!=='none'&&node.getClientRects().length));
      if(modal||root.FundsToolbarMenu?.isOpen()||!inspectingAllowed()||!inspectionTarget())return;
      event.preventDefault();
      document.dispatchEvent(new CustomEvent('funds:ribbon-command',{detail:{command:'inspectSelection'}}));
    });
    root.addEventListener('funds:edge-drag-start',event=>{
      if(event.defaultPrevented)return;
      edgeGesture={context:caseContext(),graph:state.graph,view:state.graphView};sync();
    });
    const clearGestures=()=>{if(edgeGesture||pointerGesture){edgeGesture=null;pointerGesture=null;sync();}};
    root.addEventListener('funds:edge-drag-end',()=>{edgeGesture=null;sync();});
    for(const event of ['pointerup','pointercancel','mouseup','blur'])root.addEventListener(event,clearGestures);
    root.addEventListener('mouseout',event=>{if(event.relatedTarget===null)clearGestures();});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)clearGestures();});
    for(const event of ['funds:selection-drag-start','funds:selection-drag-end'])root.addEventListener(event,sync);
    root.addEventListener('funds:graph-metric',sync);
    root.addEventListener('funds:main-accounts-state',sync);
    root.addEventListener('fundstrace:workspace-resize',()=>{root.FundsRibbon?.closePopovers();});
    sync();
  }
  root.FundsRibbonCommands=Object.freeze({sync,execute,applyFilter,applyWidth});
  if(document.readyState==='loading')root.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})(window);
