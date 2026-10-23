'use strict';
// Explicit diagnostic exception only. No endpoint or inspector URI leaves this module.
const path = require('node:path');
const { FAILURE_CODES } = require('../updates.cjs');
const CODES = [...new Set([...FAILURE_CODES, 'ERR_TIMED_OUT', 'ERR_CONNECTION_TIMED_OUT', 'ERR_PROXY_CONNECTION_FAILED',
  'ERR_TUNNEL_CONNECTION_FAILED', 'ERR_NO_SUPPORTED_PROXIES', 'ERR_PROXY_AUTH_UNSUPPORTED', 'ERR_NETWORK_CHANGED',
  'ERR_ADDRESS_UNREACHABLE', 'ERR_SSL_PROTOCOL_ERROR'])];
const KEY = '__fundsTraceOfficialNetworkDiagnostic';
const PHASES = ['unavailable', 'idle', 'checking', 'available', 'downloading', 'error', 'ready', 'installing', 'current'];

function diagnosticInspectorArguments(context) {
  if (!context?.enabled) return [];
  if (context.productionUpdate !== true || context.platform !== 'win32' || context.ci !== 'true' || context.githubActions !== 'true'
    || context.sentinelVerified !== true || context.temporaryInstallVerified !== true) throw new Error('OFFICIAL_DIAGNOSTIC_BINDING');
  return ['--inspect-brk=127.0.0.1:0'];
}
function publicUpdateSnapshot(value, relativeMs) {
  try {
    const phase = PHASES.includes(value?.phase) ? value.phase : null;
    return { phase, errorCode: phase === 'error' && [...FAILURE_CODES, 'UNKNOWN'].includes(value?.errorCode) ? value.errorCode : null,
      afterFailureMs: Number.isInteger(relativeMs) && relativeMs >= 0 && relativeMs <= 600000 ? relativeMs : null };
  } catch { return { phase: null, errorCode: null, afterFailureMs: null }; }
}
function projectEvents(value) {
  try {
    if (!value || typeof value.registered !== 'boolean' || typeof value.installed !== 'boolean'
      || typeof value.disposed !== 'boolean' || typeof value.appWasReady !== 'boolean'
      || !Number.isInteger(value.droppedCount) || value.droppedCount < 0 || value.droppedCount > 1000000
      || !Array.isArray(value.events) || value.events.length > 32) return null;
    const events = value.events.map(event => {
      if (!['responseStarted', 'completed', 'errorOccurred'].includes(event?.type)
        || !(event.httpStatus === null || Number.isInteger(event.httpStatus) && event.httpStatus >= 100 && event.httpStatus <= 599)
        || !['NONE', 'UNKNOWN', ...CODES].includes(event.netErrorCode)
        || !Number.isInteger(event.relativeMs) || event.relativeMs < 0 || event.relativeMs > 600000) throw new Error('INVALID_DIAGNOSTIC');
      return { type: event.type, httpStatus: event.httpStatus, netErrorCode: event.netErrorCode, relativeMs: event.relativeMs };
    });
    return { registered: value.registered, installed: value.installed, disposed: value.disposed,
      appWasReady: value.appWasReady, droppedCount: value.droppedCount, events };
  } catch { return null; }
}
function registrationExpression(packageJson, version) {
  if (version !== '0.1.12' || !path.isAbsolute(packageJson) || !/[\\/]resources[\\/]app\.asar[\\/]package\.json$/.test(packageJson)) throw new Error('OFFICIAL_DIAGNOSTIC_BINDING');
  return `(() => {
    const key=${JSON.stringify(KEY)};
    if(globalThis[key])return {registered:false,appWasReady:true};
    const module=process.getBuiltinModule('module');
    if(!module||typeof module.createRequire!=='function')return {registered:false,appWasReady:true};
    const electron=module.createRequire(${JSON.stringify(packageJson)})('electron');
    const state={registered:true,installed:false,disposed:false,appWasReady:electron.app.isReady(),events:[],droppedCount:0};
    globalThis[key]=state;
    if(state.appWasReady)return {registered:false,appWasReady:true};
    const started=Date.now(),codes=${JSON.stringify(CODES)};
    const push=(type,details)=>{
      if(state.disposed)return;
      if(state.events.length>=32){state.droppedCount=Math.min(1000000,state.droppedCount+1);return;}
      const status=details.statusCode;
      const error=type==='errorOccurred'?details.error:null;
      state.events.push({type,httpStatus:Number.isInteger(status)&&status>=100&&status<=599?status:null,
        netErrorCode:type==='errorOccurred'?(codes.find(code=>error==='net::'+code)||'UNKNOWN'):'NONE',
        relativeMs:Math.min(600000,Math.max(0,Date.now()-started))});
    };
    state.remove=()=>{
      state.disposed=true;let ok=true;
      if(state.session)for(const name of ['onResponseStarted','onCompleted','onErrorOccurred']){
        try{state.session.webRequest[name](null);}catch{ok=false;}
      }
      return ok;
    };
    electron.app.whenReady().then(()=>{
      if(state.disposed)return;
      const target=electron.session.fromPartition('electron-updater',{cache:false});
      state.session=target;
      const filter={urls:['https://analytix.top/fundstrace/latest.yml',
        'https://analytix.top/fundstrace/latest.yml?noCache=*',
        'https://download.analytix.top/releases/fundstrace/windows/${version}/FundsTrace-${version}-windows-x64-unsigned.exe']};
      target.webRequest.onResponseStarted(filter,details=>push('responseStarted',details));
      target.webRequest.onCompleted(filter,details=>push('completed',details));
      target.webRequest.onErrorOccurred(filter,details=>push('errorOccurred',details));
      state.installed=true;
    }).catch(()=>{state.remove();});
    return {registered:true,appWasReady:false};
  })()`;
}
const readExpression = `(() => {const s=globalThis.${KEY};return s?{registered:s.registered,installed:s.installed,
  disposed:s.disposed,appWasReady:s.appWasReady,droppedCount:s.droppedCount,events:s.events}:null;})()`;
const removeExpression = `(() => {const s=globalThis.${KEY};return s&&typeof s.remove==='function'?s.remove():false;})()`;

function createOfficialNetworkProbe({ binding, packageJson, version, expectedPid, executable, budgetMs, WebSocketImpl = WebSocket }) {
  if (!Number.isInteger(expectedPid) || expectedPid <= 0 || typeof packageJson !== 'string' || typeof executable !== 'string' || !path.isAbsolute(executable)
    || executable.toLowerCase() !== path.join(path.dirname(path.dirname(path.dirname(packageJson))), 'FundsTrace.exe').toLowerCase()) throw new Error('OFFICIAL_DIAGNOSTIC_BINDING');
  // This closure alone owns the child-emitted inspector endpoint and socket.
  let uri, partial = '', socket, closed = false, sequence = 0, verified = false;
  let resolveUri;
  const uriReady = new Promise(resolve => { resolveUri = resolve; });
  const pending = new Map();
  const report = { diagnosticOnly: true, acceptanceEligible: false, attached: false, registered: false,
    resumeSucceeded: false, teardownSucceeded: false, detached: false, observation: null };
  const allowance = () => Math.max(0, Math.min(2000, Math.floor(budgetMs())));
  const owned = () => { if (binding() !== true) throw new Error('OFFICIAL_DIAGNOSTIC_BINDING'); };
  const rejectPending = () => { for (const item of pending.values()) { clearTimeout(item.timer); item.reject(new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE')); } pending.clear(); };
  async function deadline(promise) {
    const ms = allowance(); if (!ms) throw new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE');
    let timer; try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE')), ms); })]); }
    finally { clearTimeout(timer); }
  }
  async function send(method, params = {}) {
    owned(); const ms = allowance();
    if (!ms || closed || !socket || socket.readyState !== WebSocketImpl.OPEN) throw new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE');
    return new Promise((resolve, reject) => {
      const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE')); }, ms);
      pending.set(id, { resolve, reject, timer });
      try { socket.send(JSON.stringify({ id, method, params })); } catch { pending.delete(id); clearTimeout(timer); reject(new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE')); }
    });
  }
  async function evaluate(expression) {
    const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: false });
    if (value.exceptionDetails) throw new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE');
    return value.result?.value;
  }
  return {
    consume(chunk) {
      if (uri || closed) return;
      partial = (partial + String(chunk)).slice(-8192);
      const lines = partial.split('\n'); partial = lines.pop();
      for (const line of lines) {
        const match = /^Debugger listening on (ws:\/\/127\.0\.0\.1:[1-9]\d{0,4}\/[A-Za-z0-9-]+)\r?$/.exec(line);
        if (match && Number(new URL(match[1]).port) <= 65535) { uri = match[1]; partial = ''; resolveUri(); break; }
      }
    },
    async attach() {
      owned();
      await deadline(uriReady); owned();
      socket = new WebSocketImpl(uri); uri = undefined;
      socket.addEventListener('message', event => {
        let message; try { message = JSON.parse(String(event.data)); } catch { rejectPending(); return; }
        const item = pending.get(message.id); if (!item) return;
        pending.delete(message.id); clearTimeout(item.timer);
        if (message.error) item.reject(new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE')); else item.resolve(message.result || {});
      });
      socket.addEventListener('close', () => { closed = true; report.detached = true; rejectPending(); });
      socket.addEventListener('error', rejectPending);
      await deadline(new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', () => reject(new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE')), { once: true }); }));
      report.attached = true;
      if (await evaluate(`process.pid===${expectedPid} && typeof process.execPath==='string' && process.execPath.toLowerCase()===${JSON.stringify(executable.toLowerCase())}`) !== true) throw new Error('OFFICIAL_DIAGNOSTIC_BINDING');
      verified = true;
      const ack = await evaluate(registrationExpression(packageJson, version));
      if (ack?.registered !== true || ack.appWasReady !== false) throw new Error('OFFICIAL_DIAGNOSTIC_UNAVAILABLE');
      report.registered = true;
      await send('Runtime.runIfWaitingForDebugger');
      // No Debugger.enable, breakpoints, scopes or object inspection are used.
      report.resumeSucceeded = true;
    },
    async read() {
      try { report.observation = verified ? projectEvents(await evaluate(readExpression)) : null; }
      catch { report.observation = null; }
      return { ...report };
    },
    async dispose() {
      if (verified && socket && !closed && !report.resumeSucceeded) { try { await send('Runtime.runIfWaitingForDebugger'); report.resumeSucceeded = true; } catch { } }
      try { if (verified && socket && !closed) report.teardownSucceeded = await evaluate(removeExpression) === true; } catch { report.teardownSucceeded = false; }
      try { socket?.close(); } catch { }
      if (socket && !closed) { try { await deadline(new Promise(resolve => socket.addEventListener('close', resolve, { once: true }))); } catch { } }
      closed = true; uri = undefined; partial = ''; rejectPending();
      return { ...report };
    },
  };
}

module.exports = { diagnosticInspectorArguments, publicUpdateSnapshot, projectEvents, registrationExpression, createOfficialNetworkProbe };
