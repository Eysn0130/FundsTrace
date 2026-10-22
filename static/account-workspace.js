/* Case-bound account commands. Transport uncertainty is retained in the parent;
 * reopening a page cannot replay a sent write or clear its reconciliation lock. */
(function(root) {
  'use strict';
  const kindSet = new Set(['identity-set','photo-set','photo-clear','photo-crop','main-set']);
  const same = (a,b) => a && b && String(a.caseId)===String(b.caseId);
  const cropOf = value => {
    if (!value || !['cx','cy','r'].every(key=>typeof value[key]==='number' && Number.isFinite(value[key]))) throw new Error('照片裁剪参数无效');
    if (value.cx<0 || value.cx>1 || value.cy<0 || value.cy>1 || value.r<.05 || value.r>1) throw new Error('照片裁剪参数超出范围');
    return Object.fromEntries(['cx','cy','r'].map(key=>[key,Number(value[key].toFixed(4))]));
  };
  const failure = (message,code,operationId) => Object.assign(new Error(message),{code,...(operationId?{operationId}:{})});
  function create(options) {
    const jobs = new Map(); let sequence = 0;
    const current = context => options.isCurrent(context);
    const activeFor = context => [...jobs.values()].find(job=>same(job.context,context));
    const blocked = context => context ? !!activeFor(context) : jobs.size>0;
    const operations = context => [...jobs.values()].filter(job=>job.unknown && same(job.context,context)).map(job=>({operationId:job.id,account:job.account,kind:job.kind}));
    const notify = () => options.onState?.();
    const profilePath = (context,account) => '/api/account-registry/profile?'+new URLSearchParams({case_id:String(context.caseId),account});
    async function profile(context,account,allowMissing=false) {
      let value;
      try { value=await options.request(profilePath(context,account)); }
      catch(error) {
        if (!allowMissing || error?.status!==404) throw error;
        const cases=await options.request('/api/cases');
        if (!Array.isArray(cases) || !cases.some(item=>String(item.id)===String(context.caseId))) throw error;
        return null; // The last identity/photo record may have been removed.
      }
      if (!value || value.case_id!==String(context.caseId) || value.account!==account) throw new Error('账户资料回执与当前对象不一致');
      return value;
    }
    const originalPath = job => '/api/account-photos/orig?'+new URLSearchParams({case_id:String(job.context.caseId),account:job.account,v:job.id});
    async function hash(blob) {
      const bytes=await blob.arrayBuffer(), digest=await root.crypto.subtle.digest('SHA-256',bytes);
      return [...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
    }
    function release(job) { jobs.delete(job.id); options.leases.delete(job); notify(); }
    async function publish(job) {
      if (current(job.context)) await options.publish?.(job.context,job.kind);
      notify();
    }
    function assertReady(context) {
      if (!current(context)) throw new Error('案件已变化，请重新操作');
      if (blocked(context)) throw new Error('账户操作尚未确认，请先核对当前结果');
      const reason=options.reason?.(context); if (reason) throw new Error(reason);
      if (options.hasDrafts?.()) throw new Error('主页账户资料还有未保存修改，请先返回主页保存或处理');
    }
    async function runMain(job,on) {
      const actions=options.main();
      if (!actions?.open(options.trigger())) throw new Error('主账号管理暂不可用，请稍后重试');
      if (!await actions.refresh() || !current(job.context)) throw new Error(actions.status().error || '主账号资料未能读取');
      let row=actions.status().rows.find(item=>item.account===job.account);
      if (on) {
        if (row?.source==='manual') { actions.close(); return; }
        if (!row) {
          if (!actions.showSearch() || !await actions.search(job.account,1,true)) throw new Error('账号未能查询，请在主账号管理中重试');
          row=actions.status().candidates.find(item=>item.account===job.account);
          if (!row) throw new Error('主账号须从本案流水账户中设置');
        }
        await actions.prepare('manual',row); // manual prepares AND submits once.
      } else if (row) {
        // The established controller owns the confirmation and its write.
        await new Promise((resolve,reject)=>{
          let started=false;
          const cleanup=()=>root.removeEventListener('funds:main-accounts-state',check);
          function check() {
            const status=actions.status();
            if (status.phase==='confirm' || status.pending) started=true;
            if (!started) return;
            if (status.pending || status.phase==='confirm') return;
            if (status.unknown || status.phase==='done' || status.phase==='error') {cleanup();resolve();}
            else if (!status.visible || status.phase==='idle') {cleanup();reject(failure('已取消更改主账号标记','cancelled'));}
          }
          root.addEventListener('funds:main-accounts-state',check);
          if (!actions.prepare('remove',row)) {cleanup();reject(new Error('主账号操作暂不可用'));}
        });
      }
      const status=actions.status();
      if (status.unknown) throw failure('主账号结果尚未确认，请读取当前状态核对','write-unknown',job.id);
      if (status.phase==='error') throw new Error(status.error || '主账号操作未完成');
      const value=await profile(job.context,job.account);
      if (value.is_main!==on || on && value.main_source!=='manual') throw new Error('主账号标记尚未达到请求状态，请重新核对');
      actions.close();
    }
    async function mutate(payload,context) {
      assertReady(context);
      if (!payload || !kindSet.has(payload.kind) || typeof payload.account!=='string' || !payload.account.trim() || payload.account!==payload.account.trim()) throw new Error('账户操作参数无效');
      const job={id:'account-operation-'+(++sequence),context:{...context},caseId:context.caseId,account:payload.account,kind:payload.kind,unknown:false,sent:false};
      if (payload.kind==='identity-set') {
        if (typeof payload.idNo!=='string') throw new Error('身份证号无效');
        job.identity=payload.idNo.trim().replace(/ /g,'').toUpperCase();
        if (job.identity && !/^(?:\d{15}|\d{17}[0-9X])$/.test(job.identity)) throw new Error('身份证号格式不正确（应为15位或18位）');
      }
      if (payload.kind==='main-set' && typeof payload.on!=='boolean') throw new Error('主账号操作无效');
      if (payload.kind==='photo-set' || payload.kind==='photo-crop') job.crop=cropOf(payload.crop);
      if (payload.kind==='photo-set' && (!(payload.file instanceof root.Blob) || payload.file.size>8*1024*1024 || !/\.(?:jpe?g|png|webp|bmp)$/i.test(payload.file.name || ''))) throw new Error('请选择不超过8MB的JPG、PNG、WebP或BMP图片');
      // Reserve before awaiting any read. Known-account check also prevents a
      // stale or fabricated child request from registering an unrelated account.
      jobs.set(job.id,job);
      if (job.kind!=='main-set') options.leases.add(job);
      notify();
      try {
        const before=await profile(context,job.account);
        if (!current(context)) throw new Error('案件已变化，请重新操作');
        if (job.kind!=='main-set' && options.canWrite && !await options.canWrite(context)) throw new Error('导入或案件操作尚未结束，请先核对');
        if (!current(context)) throw new Error('案件已变化，请重新操作');
        if (job.kind==='main-set') {
          job.on=payload.on;
          await runMain(job,payload.on);
        } else {
          let path,body;
          if (job.kind==='identity-set') {path='/api/account-ids/set';body={case_id:context.caseId,account:job.account,id_no:job.identity};}
          else if (job.kind==='photo-clear') {path='/api/account-photos/clear';body={case_id:context.caseId,account:job.account};}
          else {
            let file=payload.file;
            if (job.kind==='photo-crop') {
              if (!before.photo) throw new Error('此账户尚未设置照片');
              const blob=await options.blob(originalPath(job));
              const extension=({'image/png':'png','image/webp':'webp','image/bmp':'bmp','image/jpeg':'jpg'})[blob.type] || 'png';
              file=new root.File([blob],'photo.'+extension,{type:blob.type});
            }
            job.photoHash=await hash(file);
            if (!current(context)) throw new Error('案件已变化，请重新操作');
            body=new root.FormData(); path='/api/account-photos/set';
            body.append('case_id',String(context.caseId));body.append('account',job.account);body.append('file',file,file.name);
            for (const key of ['cx','cy','r']) body.append('crop_'+key,String(job.crop[key]));
          }
          job.sent=true;
          const result=await options.request(path,{method:'POST',...(body instanceof root.FormData?{body}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
          if (!result || result.ok!==true || job.kind!=='photo-clear' && result.account!==job.account || job.kind==='photo-clear' && result.cleared!==true
            || job.kind==='identity-set' && (job.identity ? result.id_no!==job.identity : result.cleared!==true)) throw new Error('账户写入回执未能确认');
        }
        release(job);
        try {await publish(job);} catch (_) {options.changed?.();} // A committed write must never be retried because view refresh failed.
      } catch(error) {
        const rejected=error?.status>=400 && error.status<500 && error.status!==408;
        if (error?.code==='write-unknown' || job.sent && !rejected) {
          job.unknown=true;notify();
          throw failure('操作结果尚未确认，请先核对当前资料，系统不会重复提交','write-unknown',job.id);
        }
        release(job);throw error;
      }
    }
    async function reconcile(operationId,context) {
      const job=jobs.get(operationId);
      if (!job?.unknown || !same(job.context,context) || !current(context)) throw new Error('该账户操作已变化，请重新读取资料');
      const value=await profile(context,job.account,true);
      if (!current(context)) throw new Error('案件已变化，请重新操作');
      let matched=false;
      if (job.kind==='identity-set') matched=(value?.id_no || '')===job.identity;
      else if (job.kind==='photo-clear') matched=!value || value.photo===null;
      else if (job.kind==='main-set') {
        const actions=options.main();
        if (actions.status().unknown) {
          if (!actions.status().visible) actions.open(options.trigger());
          if (!await actions.reconcile()) throw new Error('主账号资料仍无法确认，请稍后核对');
          if (!actions.acknowledge()) throw new Error('主账号资料尚未完成核对');
        }
        matched=(value?.is_main || false)===job.on && (!job.on || value?.main_source==='manual');
      } else if (value?.photo) {
        matched=['cx','cy','r'].every(key=>Math.abs(value.photo['crop_'+key]-job.crop[key])<.000001);
        if (matched) matched=await hash(await options.blob(originalPath(job)))===job.photoHash;
      }
      if (!current(context)) throw new Error('案件已变化，请重新操作');
      release(job);try {await publish(job);} catch (_) {options.changed?.();}
      if (!matched) throw failure('已读取当前资料，尚未达到所请求的状态。请核对后重新选择操作；未自动重复提交','write-mismatch',job.id);
    }
    return Object.freeze({mutate,reconcile,blocked,operations});
  }
  root.FundsAccountWorkspace=Object.freeze({create});
})(window);
