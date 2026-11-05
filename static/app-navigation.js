/* Shared navigation owns case changes. Embedded pages may only ask the host;
 * requests from old bindings or any other window never execute an action. */
(function () {
  'use strict';
  const channel = 'fundstrace:workspace', version = 1;
  const analysisFrame = document.querySelector('#analysisFrame');
  let analysisLoaded = false, revision = 0;
  const frame = document.querySelector('#casesFrame'), graphPage = document.querySelector('#graphPage');
  let page = 'home', surface = 'cases', loaded = false, busyOwner = null, pendingImportOpen = null;
  const feedbackInstances = { workspace: null, analysis: null };
  const feedbackModals = { workspace: false, analysis: false };
  const applyFeedbackModal = () => window.FundsFeedback?.setModalBlocked?.(page === 'analysis' ? feedbackModals.analysis : ['cases','accounts'].includes(page) ? feedbackModals.workspace : false);
  const handled = new Set();
  const feedbackOperations = new Map();
  let layoutFeedback = null;
  window.addEventListener('funds:graph-metric', event => {
    const value = event.detail, feedback = window.FundsFeedback;
    if (!feedback || !value || !isCurrentCase(value)) return;
    if (value.phase === 'layout-phase' && value.layoutPhase === 'computing') layoutFeedback = { ...caseContext() };
    if (!layoutFeedback || !isCurrentCase(layoutFeedback)) return;
    let status, title, detail = '';
    if (value.phase === 'layout-phase' && ['computing','committing'].includes(value.layoutPhase)) {
      status = 'running'; title = value.layoutPhase === 'committing' ? '正在应用图谱布局' : '正在计算图谱布局'; detail = '可继续平移和缩放，收起任务不会取消布局。';
    } else if (value.phase === 'layout') {
      const warning = document.querySelector('#graphLayoutStatus')?.textContent || '';
      status = value.outcome === 'retained' || warning ? 'warning' : 'success'; title = value.outcome === 'retained' ? '本次布局未完成，已保留原图' : '图谱布局已完成'; detail = warning;
    } else if (value.phase === 'layout-phase' && value.layoutPhase === 'idle') { status = 'cancelled'; title = '图谱布局已停止'; detail = '布局计算已停止，交易数据保留。'; }
    if (!status) return;
    feedback.task({ id: 'graph:layout', scope: layoutFeedback, status, title, detail, announce: false,
      actions: [{ id: 'open', label: '返回画布', run: () => show('home') }] });
    if (status !== 'running') layoutFeedback = null;
  });
  function syncOperationFeedback() {
    const feedback = window.FundsFeedback;
    if (!feedback) return;
    const context = { caseId: String(state.caseId), generation: state.caseGeneration };
    const values = [
      ['canvas', '画布操作', window.FundsCanvasActions?.status?.(), '#btnCanvasActions'],
      ['data', '资料批量处理', window.FundsDataToolsActions?.status?.(), '#btnDataActions'],
      ['main', '主账号标记', window.FundsMainAccountsActions?.status?.(), '#btnMainActions'],
      ['case', '案件操作', caseWorkspace?.status?.(), '#btnCases'],
      ['export', '导出', exportWorkspace?.status?.(), '#btnExport'],
    ];
    for (const [key, title, value, trigger] of values) {
      const owner = value?.operationContext || value?.context;
      const id = 'operation:' + key, previous = feedbackOperations.get(id);
      if (!owner) {
        if (previous?.startsWith(context.caseId + ':' + context.generation + ':unknown:') && !value?.unknown && !value?.pending) {
          feedbackOperations.set(id, context.caseId + ':' + context.generation + ':resolved:');
          feedback.task({ id, scope: context, status: 'success', title: title + '核对已结束', detail: '请以原操作界面的核对结果为准。' });
        }
        continue;
      }
      if (String(owner.caseId) !== context.caseId) continue;
      // Canvas's public status deliberately does not expose whether its naming
      // dialog was cancelled or a write committed. Mirror its unknown lease,
      // not a guessed completion inferred from pending=false.
      if (key === 'canvas' && !value.unknown) continue;
      let status = value.unknown || value.phase === 'unknown' ? 'unknown' : value.pending && (value.sent === true || key === 'export' || key === 'case' && value.view === 'export' || ['capturing','exporting','importing'].includes(value.phase)) ? 'running'
        : ['done','complete'].includes(value.phase) ? 'success' : value.phase === 'error' ? 'error' : null;
      if (!status || !previous && !['running','unknown'].includes(status)) continue;
      if (owner.generation !== state.caseGeneration && status !== 'unknown') continue;
      const signature = [context.caseId, context.generation, status, value.action || value.view || '', value.refreshError || ''].join(':');
      if (previous === signature) continue;
      feedbackOperations.set(id, signature);
      if (value.refreshError && status === 'success') status = 'warning';
      feedback.task({ id, scope: context, status, title: title + (status === 'unknown' ? '结果待核对' : status === 'success' ? '已完成' : status === 'error' ? '未完成' : status === 'warning' ? '已完成，视图待刷新' : '进行中'),
        detail: status === 'unknown' ? '原操作结果尚未确认。请打开原操作界面核对，避免重复提交。' : status === 'warning' ? '写入已完成，刷新失败不会重新提交。' : '',
        actions: [{ id: 'open', label: status === 'unknown' ? '打开核对' : '查看操作', run: () => {
          if (String(state.caseId) !== context.caseId || state.caseGeneration !== context.generation) return;
          show('home'); document.querySelector(trigger)?.click();
        } }] });
    }
    const unresolved = accountActions?.operations(caseContext()) || [];
    const remaining = new Set();
    for (const operation of unresolved) {
      const id = 'account:' + operation.operationId, signature = context.caseId + ':' + context.generation + ':unknown:';
      remaining.add(id);
      if (feedbackOperations.get(id) === signature) continue;
      feedbackOperations.set(id, signature);
      feedback.task({ id, scope: context, status: 'unknown', title: '账户资料结果待核对',
        detail: '尚不能确认是否写入。请在账户与身份中核对原操作。', actions: [{ id: 'open', label: '打开核对', run: () => show('accounts') }] });
    }
    for (const [id, value] of feedbackOperations) if (id.startsWith('account:') && !remaining.has(id) && value === context.caseId + ':' + context.generation + ':unknown:') {
      feedbackOperations.set(id, context.caseId + ':' + context.generation + ':resolved:');
      feedback.task({ id, scope: context, status: 'success', title: '账户操作核对已结束', detail: '请以账户与身份页面的核对结果为准。' });
    }
  }
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
  function flushImportOpen() {
    if (!pendingImportOpen) return;
    if (pendingImportOpen.caseId !== String(state.caseId) || pendingImportOpen.generation !== state.caseGeneration) { pendingImportOpen = null; return; }
    if (!loaded || !feedbackInstances.workspace) return;
    post(pendingImportOpen); pendingImportOpen = null;
  }
  function context() {
    return {type:'context',surface,unresolvedOperations:accountActions?.operations(caseContext()) || [],caseId:String(state.caseId),name:state.cases.find(item=>String(item.id)===String(state.caseId))?.name || '当前案件',generation:state.caseGeneration,
      pendingImports:window.FundsImportWorkflow?.externalStatus?.(caseContext()) || [], navigationPending:!!busyOwner, revision, active:page===surface, retainedCaseIds:window.FundsWorkspaceSession.retainedCaseIds()};
  }
  function ensureFrames() {
    if (!state.caseId || !state.cases.length) return;
    if (page==='analysis' && !analysisFrame.getAttribute('src')) analysisFrame.src=`/analysis?embedded=1&case_id=${encodeURIComponent(state.caseId)}`;
    if ((page==='cases' || page==='accounts') && !frame.getAttribute('src')) frame.src=`/workspace/?embedded=1&surface=${surface}&case_id=${encodeURIComponent(state.caseId)}`;
  }
  function sync(changed = false) {
    syncOperationFeedback();
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
    applyFeedbackModal();
    graphPage.classList.toggle('page-inactive',page !== 'home'); graphPage.inert = page !== 'home';
    frame.hidden = page !== 'cases' && page !== 'accounts';
    analysisFrame.hidden = page !== 'analysis';
    frame.title=surface === 'accounts' ? '账户与身份' : '案件与导入';
    for (const item of document.querySelectorAll('[data-nav-page]')) {
      if (item.dataset.navPage === page) item.setAttribute('aria-current','page'); else item.removeAttribute('aria-current');
    }
    sync();
    window.dispatchEvent(new CustomEvent('funds:feedback-layout', { detail: null }));
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
  const feedbackObserver = window.FundsFeedbackWorkspace?.create({
    context: caseContext, feedback: () => window.FundsFeedback,
    isCurrent: context => String(context.caseId) === String(state.caseId) && context.generation === state.caseGeneration,
    fetch: (...args) => fetch(...args),
    leases: context => window.FundsImportWorkflow?.externalStatus?.(context) || [],
    release: async (context, id) => {
      const release = window.FundsImportWorkflow?.releaseExternal;
      if (typeof release !== 'function') throw new Error('导入回执核对尚未就绪');
      await release(context, id, id);
      if (String(context.caseId) === String(state.caseId) && context.generation === state.caseGeneration) post({ type: 'feedback-import-settled', caseId: String(context.caseId), generation: context.generation, importId: id });
    },
    changed: context => String(context.caseId) === String(state.caseId) && context.generation === state.caseGeneration ? dataChanged() : undefined,
    viewingImport: () => !frame.hidden,
    openAccount: () => show('accounts'),
    accountOperations: context => accountActions?.operations(context) || [],
    openImport: (importId, importSurface, context) => {
      if (String(context.caseId) !== String(state.caseId) || context.generation !== state.caseGeneration) return;
      if (importSurface === 'legacy') { show('home'); void window.FundsImportWorkflow?.restore?.(importId); }
      else {
        pendingImportOpen = { type: 'feedback-open-import', caseId: String(state.caseId), generation: state.caseGeneration, importId };
        show('cases'); flushImportOpen();
      }
    },
  });
  const syncFeedback = () => feedbackObserver?.sync();
  window.addEventListener('funds:feedback-binding', () => { pendingImportOpen = null; layoutFeedback = null; feedbackModals.workspace = feedbackModals.analysis = false; applyFeedbackModal(); syncFeedback(); });
  window.addEventListener('funds:feedback-ready', () => { window.FundsFeedback?.bind(caseContext()); syncFeedback(); });
  window.addEventListener('funds:import-state', syncFeedback);
  window.addEventListener('funds:operation-state', syncOperationFeedback);
  window.addEventListener('funds:account-state', syncOperationFeedback);
  window.addEventListener('pagehide', () => feedbackObserver?.dispose());
  function detachFeedbackInstance(key) {
    if (feedbackInstances[key]) feedbackObserver?.detachSource?.(key + ':' + feedbackInstances[key]);
    feedbackInstances[key] = null;
  }
  for (const [key, target] of [['workspace', frame], ['analysis', analysisFrame]]) target.addEventListener('load', () => {
    detachFeedbackInstance(key); feedbackModals[key] = false; applyFeedbackModal();
    if (key === 'workspace') loaded = false; else analysisLoaded = false;
    target.contentWindow?.postMessage({ channel, version, type: 'feedback-hello' }, location.origin);
  });
  window.addEventListener('message',async event=>{
    if (event.origin!==location.origin || !valid(event.data)) return;
    const fromAnalysis = event.source===analysisFrame.contentWindow;
    if (!fromAnalysis && event.source!==frame.contentWindow) return;
    const message = event.data;
    if (message.type === 'feedback-modal') {
      const key = fromAnalysis ? 'analysis' : 'workspace';
      if (message.caseId !== String(state.caseId) || message.generation !== state.caseGeneration ||
        message.instanceId !== feedbackInstances[key] || typeof message.open !== 'boolean') return;
      feedbackModals[key] = message.open; applyFeedbackModal(); return;
    }
    if (message.type === 'feedback') {
      if (message.caseId !== String(state.caseId) || message.generation !== state.caseGeneration ||
        typeof message.instanceId !== 'string' || message.instanceId !== feedbackInstances[fromAnalysis ? 'analysis' : 'workspace'] ||
        fromAnalysis && !['analysis','download'].includes(message.event?.operation)) return;
      feedbackObserver?.receive(message.event, (fromAnalysis ? 'analysis:' : 'workspace:') + message.instanceId);
      return;
    }
    if (message.type==='ready') {
      const key = fromAnalysis ? 'analysis' : 'workspace';
      if (typeof message.instanceId === 'string' && /^[a-zA-Z0-9-]{12,80}$/.test(message.instanceId)) {
        if (feedbackInstances[key] && feedbackInstances[key] !== message.instanceId) detachFeedbackInstance(key);
        feedbackInstances[key] = message.instanceId;
      }
      if(fromAnalysis) analysisLoaded=true; else loaded=true; sync(); flushImportOpen(); return;
    }
    if (message.type!=='request' || typeof message.requestId!=='string' || message.requestId.length>160 || handled.has(message.requestId)) return;
    const owner = {};
    const release = () => {
      if (busyOwner !== owner) return;
      busyOwner = null; sync();
    };
    const acquire = () => {
      if (message.caseId!==String(state.caseId) || message.generation!==state.caseGeneration) throw new Error('案件已变化，请重新操作');
      if (busyOwner) throw new Error('正在切换案件，请稍后操作');
      busyOwner = owner; sync();
    };
    const reply = (ok,error,details={}) => {
      release();
      event.source?.postMessage({channel,version,type:'result',requestId:message.requestId,ok,...(error?{error}:{}),...details},location.origin);
    };
    if (message.caseId!==String(state.caseId) || message.generation!==state.caseGeneration) {reply(false,'案件已变化，请重新操作');return;}
    if (!['select-case','open-graph','open-canvas','track-cycle','case-action','account-action','import-acquire','import-release','data-changed'].includes(message.action) || !message.payload || typeof message.payload!=='object') {reply(false,'不支持此操作');return;}
    if (fromAnalysis && !['select-case','open-graph','open-canvas','track-cycle'].includes(message.action)) {reply(false,'不支持此分析操作');return;}
    handled.add(message.requestId);
    if (handled.size > 2048) handled.delete(handled.values().next().value);
    if (busyOwner) { reply(false,'正在切换案件，请稍后操作'); return; }
    try {
      if (message.action==='select-case') {
        if (typeof message.payload.caseId!=='string') throw new Error('案件标识无效');
        acquire();
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
        if(!isCurrentCase(binding)) throw new Error('案件或画布已变化');
        acquire();
        const result=await window.FundsCanvasActions.withWrite(binding,async()=>{
          if(!isCurrentCase(binding)) throw new Error('案件或画布已变化');
          try {return await caseRequest('/api/canvases/from-flows',{method:'POST',...jsonOpts({case_id:binding.caseId,name:payload.name,flow_ids:payload.flow_ids,snapshot:payload.snapshot})});}
          catch(error){error.writeSent=true;throw error;}
        });
        await refreshCanvasList(state.canvasId);reply(true,null,{result});
      } else if (message.action==='open-canvas') {
        const binding=caseContext(), canvasId=message.payload.canvasId;
        const reason=accountReason(binding); if(reason || window.FundsCanvasActions?.blocked()) throw new Error(reason || '画布操作尚未确认，请先核对');
        acquire();
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
    finally {release();}
  });
  async function dataChanged() {
    revision++; sync(); post({type:'data-changed',caseId:String(state.caseId),generation:state.caseGeneration});
    if(analysisLoaded) analysisFrame.contentWindow?.postMessage({channel,version,type:'data-changed',caseId:String(state.caseId),generation:state.caseGeneration},location.origin);
    await refreshAll();
  }
  window.FundsAppNavigation=Object.freeze({show,sync,dataChanged,importBlocker:context=>accountReason(context || caseContext()),accountsChanged:()=>{sync();post({type:'accounts-changed',caseId:String(state.caseId),generation:state.caseGeneration});}});
  window.addEventListener('DOMContentLoaded',()=>{
    syncFeedback();
    const positionFeedback = () => {
      const host = document.querySelector('[data-feedback-host="static"]');
      if (!host) return;
      const top = page === 'home' ? document.querySelector('#editorToolbar') : document.querySelector('#browserCaption');
      const bottom = top?.getBoundingClientRect().bottom || 32;
      host.style.setProperty('--feedback-top', Math.max(16, bottom + 16) + 'px');
    };
    window.addEventListener('resize', positionFeedback);
    window.addEventListener('funds:feedback-layout', positionFeedback);
    if (typeof ResizeObserver === 'function') {
      const observer = new ResizeObserver(positionFeedback);
      for (const node of [document.querySelector('#editorToolbar'), document.querySelector('#browserCaption')]) if (node) observer.observe(node);
    }
    positionFeedback();
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
