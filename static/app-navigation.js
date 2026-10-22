/* Shared navigation owns case changes. Embedded pages may only ask the host;
 * requests from old bindings or any other window never execute an action. */
(function () {
  'use strict';
  const channel = 'fundstrace:workspace', version = 1;
  const analysisFrame = document.querySelector('#analysisFrame');
  let analysisLoaded = false, revision = 0;
  const frame = document.querySelector('#casesFrame'), graphPage = document.querySelector('#graphPage');
  let page = 'home', surface = 'cases', loaded = false, busy = false;
  const handled = new Set();
  function accountReason(context) {
    if (window.FundsDataToolsActions?.blocked(context)) return '资料批量操作尚未结束，请先核对';
    if (window.FundsMainAccountsActions?.blocked(context)) return '主账号操作尚未结束，请先核对';
    const cases=caseWorkspace?.status(), canvases=window.FundsCanvasActions?.status(), exported=exportWorkspace?.status();
    if (cases?.pending || cases?.phase==='unknown' || canvases?.pending || canvases?.unknown || exported?.pending || batchView.deleting) return '案件或画布正在处理，请稍后操作';
    if ([...accountDataWrites].some(write=>String(write.caseId)===String(context.caseId)) || nodeWrites.size || pcState.binding || nodeConfirmation || nodeWorkspace?.confirming) return '账户资料正在处理，请稍后操作';
    if (state.graphLoading || cancelAnnotationDrag || layoutJob || state.graphView.exportDepth || state.graphSelection?.status?.().dragging || window.FundsCanvasEditor?.history?.status?.().busy) return '画布正在处理，请稍后操作';
    return '';
  }
  const accountActions=window.FundsAccountWorkspace?.create({
    context:caseContext,isCurrent:context=>isCurrentCase(context,false),request:caseRequest,
    blob:(path,options)=>caseRequest(path,options || {},true),leases:accountDataWrites,
    reason:accountReason,hasDrafts:hasNodeDrafts,main:()=>window.FundsMainAccountsActions,
    trigger:()=>document.querySelector('#navAccounts'),
    canWrite:async context=>(!window.FundsImportWorkflow || await window.FundsImportWorkflow.canWrite(context)) && isCurrentCase(context,false),
    onState:()=>{window.FundsAppNavigation?.sync();syncNodeWorkspaceControls();window.FundsCanvasActions?.sync();window.dispatchEvent(new CustomEvent('funds:account-state'));},
    changed:()=>window.FundsAppNavigation?.accountsChanged(),
    publish:async context=>{
      if (!isCurrentCase(context,false)) return;
      const binding=caseContext(); state.forceRebuild=true;
      await loadGraph(true,binding);
      if (isCurrentCase(binding)) window.FundsAppNavigation?.accountsChanged();
    },
  });
  window.FundsAccountActions=accountActions;
  const post = message => { if (loaded) frame.contentWindow?.postMessage({channel,version,...message},location.origin); };
  function context() {
    return {type:'context',surface,unresolvedOperations:accountActions?.operations(caseContext()) || [],caseId:String(state.caseId),name:state.cases.find(item=>String(item.id)===String(state.caseId))?.name || '当前案件',generation:state.caseGeneration,
      pendingImports:window.FundsImportWorkflow?.externalStatus?.(caseContext()) || [], revision, active:page===surface, retainedCaseIds:window.FundsWorkspaceSession.retainedCaseIds()};
  }
  function ensureFrames() {
    if (!state.caseId || !state.cases.length) return;
    if (page==='analysis' && !analysisFrame.getAttribute('src')) analysisFrame.src=`/analysis?embedded=1&case_id=${encodeURIComponent(state.caseId)}`;
    if ((page==='cases' || page==='accounts') && !frame.getAttribute('src')) frame.src=`/workspace/?embedded=1&surface=${surface}&case_id=${encodeURIComponent(state.caseId)}`;
  }
  function sync(changed = false) {
    ensureFrames();
    if (state.caseId && state.cases.length) post(context());
    if (analysisLoaded) analysisFrame.contentWindow?.postMessage({channel,version,...context(),surface:'analysis',active:page==='analysis'},location.origin);
    if (changed) { post({type:'cases-changed'}); if(analysisLoaded) analysisFrame.contentWindow?.postMessage({channel,version,type:'cases-changed'},location.origin); }
  }
  function show(next) {
    if (!['home','cases','accounts','analysis'].includes(next)) return false;
    if (next !== page && page === 'home') window.FundsWorkspaceSession.capture();
    const returning = page !== 'home' && next === 'home';
    if (next === 'cases' || next === 'accounts') surface=next;
    page = next;
    graphPage.classList.toggle('page-inactive',page !== 'home'); graphPage.inert = page !== 'home';
    frame.hidden = page !== 'cases' && page !== 'accounts';
    analysisFrame.hidden = page !== 'analysis';
    frame.title=surface === 'accounts' ? '账户与身份' : '案件与导入';
    for (const item of document.querySelectorAll('[data-nav-page]')) {
      if (item.dataset.navPage === page) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current');
    }
    sync();
    const url = new URL(location.href);
    if (page === 'home') url.searchParams.delete('page'); else url.searchParams.set('page',page);
    history.replaceState(null,'',url);
    if (page === 'home') requestAnimationFrame(()=>{state.graphCamera?.resize?.();scheduleGraphViewport();scheduleGraphLabels();});
    if (returning) void refreshAll().catch(()=>toast('部分数据刷新失败，请稍后重试','err'));
    return true;
  }
  function valid(message) {
    return message && message.channel===channel && message.version===version && typeof message.type==='string';
  }
  window.addEventListener('message',async event=>{
    if (event.origin!==location.origin || !valid(event.data)) return;
    const fromAnalysis = event.source===analysisFrame.contentWindow;
    if (!fromAnalysis && event.source!==frame.contentWindow) return;
    const message = event.data;
    if (message.type==='ready') { if(fromAnalysis) analysisLoaded=true; else loaded=true; sync(); return; }
    if (message.type!=='request' || typeof message.requestId!=='string' || message.requestId.length>160 || handled.has(message.requestId)) return;
    const reply = (ok,error,details={}) => event.source?.postMessage({channel,version,type:'result',requestId:message.requestId,ok,...(error?{error}:{}),...details},location.origin);
    if (message.caseId!==String(state.caseId) || message.generation!==state.caseGeneration) {reply(false,'案件已变化，请重新操作');return;}
    if (!['select-case','open-graph','open-canvas','track-cycle','case-action','account-action','import-acquire','import-release','data-changed'].includes(message.action) || !message.payload || typeof message.payload!=='object') {reply(false,'不支持此操作');return;}
    if (fromAnalysis && !['select-case','open-graph','open-canvas','track-cycle'].includes(message.action)) {reply(false,'不支持此分析操作');return;}
    handled.add(message.requestId);
    if (handled.size > 2048) handled.delete(handled.values().next().value);
    if (busy) { reply(false,'正在切换案件，请稍后操作'); return; }
    try {
      if (message.action==='select-case') {
        if (typeof message.payload.caseId!=='string') throw new Error('案件标识无效');
        busy = true;
        const rows = await refreshManagedCases();
        if (message.caseId!==String(state.caseId) || message.generation!==state.caseGeneration) throw new Error('案件已变化，请重新操作');
        const target = rows.find(item=>String(item.id)===message.payload.caseId);
        if (!target) throw new Error('案件已不存在，请刷新案件列表');
        if (!await openManagedCase(target.id,caseContext())) throw new Error('当前操作未完成，请处理后再切换');
        sync(); reply(true);
      } else if (message.action==='import-acquire') {
        const reason=accountReason(caseContext()); if(reason) throw new Error(reason);
        await window.FundsImportWorkflow.acquireExternal(caseContext(),message.payload.importId); sync(); reply(true);
      } else if (message.action==='import-release') {
        await window.FundsImportWorkflow.releaseExternal(caseContext(),message.payload.importId,message.payload.jobId,message.payload.rejected===true); sync(); reply(true);
      } else if (message.action==='data-changed') { await dataChanged(); reply(true);
      } else if (message.action==='track-cycle') {
        const binding=caseContext(), payload=message.payload;
        if(accountReason(binding) || accountActions?.blocked(binding)) throw new Error('当前写入或画布操作尚未结束，请先核对');
        if(typeof payload.name!=='string' || !payload.name.trim() || payload.name.length>60 || !Array.isArray(payload.flow_ids) || !payload.flow_ids.length || payload.flow_ids.some(id=>!/^\d+$/.test(String(id))) || !payload.snapshot || typeof payload.snapshot!=='object') throw new Error('分析画布参数无效');
        if(!await window.FundsImportWorkflow.canWrite(binding)) throw new Error('流水导入尚未结束，请先核对');
        busy=true;
        const result=await window.FundsCanvasActions.withWrite(binding,async()=>{
          if(!isCurrentCase(binding)) throw new Error('案件或画布已变化');
          try {return await caseRequest('/api/canvases/from-flows',{method:'POST',...jsonOpts({case_id:binding.caseId,name:payload.name,flow_ids:payload.flow_ids,snapshot:payload.snapshot})});}
          catch(error){error.writeSent=true;throw error;}
        });
        await refreshCanvasList(state.canvasId);reply(true,null,{result});
      } else if (message.action==='open-canvas') {
        const binding=caseContext(), canvasId=message.payload.canvasId;
        const reason=accountReason(binding); if(reason || window.FundsCanvasActions?.blocked()) throw new Error(reason || '画布操作尚未确认，请先核对');
        busy=true;
        if(typeof canvasId!=='string' || !/^[1-9]\d*$/.test(canvasId)) throw new Error('画布标识无效');
        if(window.FundsAccountActions?.blocked(caseContext()) || !await window.FundsImportWorkflow.canWrite(caseContext())) throw new Error('当前写入尚未完成，请先核对');
        if(!isCurrentCase(binding) || accountReason(binding) || window.FundsCanvasActions?.blocked()) throw new Error('案件或画布已变化，请重新打开');
        show('home'); await loadCanvas(canvasId);
        const target=caseContext();
        if(!isCurrentCase(binding,false) || String(target.caseId)!==String(binding.caseId) || String(target.canvasId)!==canvasId) throw new Error('画布已变化，请重新打开');
        await refreshCanvasList(target.canvasId);
        if(!isCurrentCase(target)) throw new Error('画布已变化，请重新打开');
        const flowId=message.payload.flowId;
        if(typeof flowId==='string' && /^[1-9]\d*$/.test(flowId)) { pendingFocusEdge=flowId; requestedCanvasId=state.canvasId; pendingGraphContext={...caseContext(),ready:false}; await applyPendingFocusEdge(); }
        reply(true);
      } else if (message.action==='open-graph') { show('home'); reply(true); }
      else if (message.action==='account-action') {
        const payload=message.payload, binding=caseContext();
        if (!accountActions) throw new Error('账户工作区未能初始化，请重新打开页面');
        if (payload.action==='mutate') await accountActions.mutate(payload.mutation,binding);
        else if (payload.action==='reconcile' && typeof payload.operationId==='string') await accountActions.reconcile(payload.operationId,binding);
        else if (payload.action==='main-accounts') {
          if (!window.FundsMainAccountsActions.open(document.querySelector('#navAccounts'))) throw new Error(accountReason(binding) || '主账号管理暂不可用');
        } else if (payload.action==='import-identity') {
          if (accountActions.blocked(binding)) throw new Error('账户操作尚未确认，请先核对');
          if (!window.FundsDataToolsActions.open('import',document.querySelector('#navAccounts'))) throw new Error(accountReason(binding) || '身份证对照表导入暂不可用');
        } else if (payload.action==='download-template') await window.FundsDataToolsActions.downloadTemplate();
        else if (payload.action==='view-flows' && typeof payload.account==='string' && payload.account.trim()) {
          const value=await caseRequest('/api/account-registry/profile?'+new URLSearchParams({case_id:String(binding.caseId),account:payload.account}));
          if (!isCurrentCase(binding,false) || value.case_id!==String(binding.caseId) || value.account!==payload.account) throw new Error('案件或账户已变化，请重新选择');
          show('home');
          for (const id of ['fAccount','fName','fCounter','fKw','fMin','fMax','fFrom','fTo']) document.querySelector('#'+id).value='';
          document.querySelector('#fTracked').checked=false;
          document.querySelector('#fAccount').value=payload.account;
          document.querySelector('#fAccount').dataset.exactAccount=payload.account;
          await doSearch(1);
        } else throw new Error('不支持此账户操作');
        sync();reply(true);
      } else {
        const action=message.payload.action;
        if (!['import','export','delete'].includes(action)) throw new Error('不支持此案件操作');
        caseWorkspace.open(document.querySelector('[data-nav-page="cases"]'));
        document.querySelector({import:'#btnImportCase',export:'#btnExportCase',delete:'#btnDelCase'}[action]).click();
        reply(true);
      }
    } catch(error) {reply(false,error?.message || '操作失败，请重试',{...(typeof error?.code==='string'?{code:error.code}:{}),...(typeof error?.operationId==='string'?{operationId:error.operationId}:{})});}
    finally {busy=false;}
  });
  async function dataChanged() {
    revision++; sync(); post({type:'data-changed',caseId:String(state.caseId),generation:state.caseGeneration});
    if(analysisLoaded) analysisFrame.contentWindow?.postMessage({channel,version,type:'data-changed',caseId:String(state.caseId),generation:state.caseGeneration},location.origin);
    await refreshAll();
  }
  window.FundsAppNavigation=Object.freeze({show,sync,dataChanged,importBlocker:context=>accountReason(context || caseContext()),accountsChanged:()=>{sync();post({type:'accounts-changed',caseId:String(state.caseId),generation:state.caseGeneration});}});
  window.addEventListener('DOMContentLoaded',()=>{
    for (const item of document.querySelectorAll('[data-nav-page]')) item.onclick=()=>show(item.dataset.navPage);
    document.querySelector('#fAccount').addEventListener('input',event=>{delete event.currentTarget.dataset.exactAccount;});
    document.querySelector('#navAnalysis').onclick=()=>show('analysis');
    for(const link of document.querySelectorAll('#analysisLink,[data-analysis-algorithm]')) link.addEventListener('click',event=>{
      if(event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); show('analysis');
      const algorithm=link.dataset.analysisAlgorithm;
      if(algorithm) analysisFrame.addEventListener('load',()=>analysisFrame.contentWindow?.postMessage({channel,version,type:'algorithm',algorithm},location.origin),{once:true});
      if(algorithm && analysisLoaded) analysisFrame.contentWindow?.postMessage({channel,version,type:'algorithm',algorithm},location.origin);
    });
    document.querySelector('#navSettings').onclick=()=>window.FundsAppearance?.openSettings();
    document.querySelector('#navUpdate').onclick=()=>window.FundsAppearance?.openSettings('about');
    if (window.FundsDesktopPreferences) document.querySelector('#browserCaption').hidden=true;
    const requested=new URL(location.href).searchParams.get('page');
    show(['cases','accounts','analysis'].includes(requested)?requested:'home');
  });
})();
