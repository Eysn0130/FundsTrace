/* Embedded analysis shares only its host's versioned case context. */
(function(root){
  'use strict';
  if(root.parent===root || new URLSearchParams(location.search).get('embedded')!=='1') return;
  const channel='fundstrace:workspace', version=1, pending=new Map();
  let binding=null, serial=0, applying=false, dataRevision=-1;
  const drafts=new Map();
  const fields=['subjects','cycDays','ffWin','ffTol','pSrc','pDst','pHops','pOrder'];
  const post=message=>parent.postMessage({channel,version,...message},location.origin);
  const request=(action,payload={})=>new Promise((resolve,reject)=>{
    if(!binding) return reject(new Error('工作区尚未连接'));
    const requestId='analysis:'+Date.now()+':'+(++serial);
    pending.set(requestId,{resolve,reject,binding:{...binding}});
    post({type:'request',requestId,caseId:binding.caseId,generation:binding.generation,action,payload});
  });
  function capture(){
    if(!caseId) return;
    drafts.set(String(caseId),{fields:Object.fromEntries(fields.map(id=>[id,$('#'+id).value])),algorithms:$$('.alg').filter(input=>input.checked).map(input=>input.value),baseline:draftBaseline});
  }
  function restoreDraft(id){
    const saved=drafts.get(String(id));if(!saved)return;
    for(const field of fields)$('#'+field).value=saved.fields[field];
    for(const input of $$('.alg'))input.checked=saved.algorithms.includes(input.value);
    draftBaseline=saved.baseline;syncAlgorithmFields();
  }
  function applyContext(message,captureCurrent=true){
    if(!analysisCases.length) {binding=message;return;}
    if(String(caseId)!==message.caseId || binding?.generation!==message.generation){
      if(captureCurrent)capture(); applying=true;
      try {
        selectAnalysisCase(message.caseId);
        restoreDraft(message.caseId); void showStats();
      } finally {applying=false;}
    }
    binding=message;
    if(dataRevision!==-1 && message.revision!==dataRevision) invalidate();
    dataRevision=message.revision;
  }
  function invalidate(){
    analysisRequest++;statsRequest++;resultRevision++;
    lastRun=lastResults=null;reportPending=false;
    if(analysisPhase==='running') $('#pbar').hidden=true;
    setAnalysisPhase('ready');
    $('#resultScope').textContent='案件数据已更新 · 原结果为历史快照，请重新运行';
    for(const button of $$('#results button'))button.disabled=true;
    for(const link of $$('#results a.btn-graph')) {link.setAttribute('aria-disabled','true');link.removeAttribute('href');}
    void showStats();
  }
  root.addEventListener('message',event=>{
    if(event.origin!==location.origin || event.source!==parent || event.data?.channel!==channel || event.data.version!==version)return;
    const message=event.data;
    if(message.type==='context' && typeof message.caseId==='string' && Number.isSafeInteger(message.generation))applyContext(message);
    else if(message.type==='result'){
      const item=pending.get(message.requestId);if(!item)return;pending.delete(message.requestId);
      if(item.binding.caseId!==binding?.caseId || item.binding.generation!==binding?.generation){item.resolve(undefined);return;}
      message.ok?item.resolve(message.result):item.reject(new Error(message.error||'操作未完成'));
    } else if(message.type==='cases-changed') void loadCases();
    else if(message.type==='algorithm' && analysisMethods[message.algorithm]){
      for(const input of $$('.alg'))input.checked=input.value===message.algorithm;syncAlgorithmFields();
    }
  });
  const originalLoad=loadCases;
  loadCases=async function(){
    capture();const before=analysisContext();
    await originalLoad();
    if(binding){
      // The loader may clear its local selection after a failed read. Never
      // capture those cleared fields over the case's retained editing intent.
      applyContext(binding,false);
      if(!analysisCases.length || before.caseId!==caseId || before.generation!==caseGeneration)restoreDraft(binding.caseId);
    }
  };
  const originalApi=api;
  api=async function(path,body){return path==='/api/canvases/from-flows'?request('track-cycle',{name:body.name,flow_ids:body.flow_ids,snapshot:body.snapshot}):originalApi(path,body);};
  $('#caseSel').onchange=async event=>{
    const next=event.target.value;$('#caseSel').value=String(caseId||'');
    if(!binding || next===String(caseId))return;
    try{capture();await request('select-case',{caseId:next});}catch(error){analysisError(error.message);}
  };
  $('#backToGraph').onclick=async event=>{
    if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    event.preventDefault();capture();
    try{await request('open-graph');}catch(error){analysisError(error.message);}
  };
  document.addEventListener('click',async event=>{
    const link=event.target.closest('a.btn-graph');if(!link || event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    event.preventDefault();if(link.getAttribute('aria-disabled')==='true')return;const url=new URL(link.href,location.href);
    try{await request('open-canvas',{canvasId:url.searchParams.get('canvas_id'),flowId:url.searchParams.get('focus_edge')});}catch(error){analysisError(error.message);}
  });
  root.addEventListener('pagehide',()=>{for(const item of pending.values())item.reject(new Error('分析页面已关闭'));pending.clear();});
  post({type:'ready'});
  // Initial async loading may already have started before this adapter loads.
  void loadCases();
})(window);
