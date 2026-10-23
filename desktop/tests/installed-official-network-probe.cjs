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
    if(globalThis[key])return {registered:false,appWasReady:false,reason:'duplicate'};
    if(typeof process.getBuiltinModule!=='function')return {registered:false,appWasReady:false,reason:'moduleUnavailable'};
    const module=process.getBuiltinModule('module');
    if(!module||typeof module.createRequire!=='function')return {registered:false,appWasReady:false,reason:'moduleUnavailable'};
    const electron=module.createRequire(${JSON.stringify(packageJson)})('electron');
    const state={registered:true,installed:false,disposed:false,appWasReady:electron.app.isReady(),events:[],droppedCount:0};
    globalThis[key]=state;
    if(state.appWasReady)return {registered:false,appWasReady:true,reason:'alreadyReady'};
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
  let uri, partial = '', socket, closed = false, sequence = 0, verified = false, debuggerEnabled = false, paused = false, pauseCount = 0;
  let resolveUri, resolveEntryPause;
  const uriReady = new Promise(resolve => { resolveUri = resolve; });
  // Install the waiter before any command can deliver the initial pause event.
  const entryPause = new Promise(resolve => { resolveEntryPause = resolve; });
  const pending = new Map();
  const report = { diagnosticOnly: true, acceptanceEligible: false, attached: false, registered: false,
    stage: 'endpoint', failureClass: null, exceptionClassName: null, entryPauseObserved: false, extraPauseObserved: false,
    identityConfirmed: false, bootstrapReleased: false, debuggerDisabled: false, coverageAvailable: false,
    resumeSucceeded: false, teardownSucceeded: false, detached: false, observation: null };
  const allowance = () => Math.max(0, Math.min(2000, Math.floor(budgetMs())));
  const failure = (kind, message = 'OFFICIAL_DIAGNOSTIC_UNAVAILABLE') => {
    const error = new Error(message); error.diagnosticFailureClass = kind; return error;
  };
  const owned = () => { if (binding() !== true) throw failure('childExited', 'OFFICIAL_DIAGNOSTIC_BINDING'); };
  const rejectPending = () => { for (const item of pending.values()) { clearTimeout(item.timer); item.reject(failure('unavailable')); } pending.clear(); };
  async function step(name, action) {
    if (!report.failureClass) report.stage = name;
    try { return await action(); }
    catch (error) {
      if (!report.failureClass) {
        const kinds = ['timeout', 'protocolError', 'evaluationException', 'bindingMismatch', 'ackRejected', 'processUnavailable',
          'moduleUnavailable', 'alreadyReady', 'unexpectedPause', 'childExited', 'budgetExhausted', 'unavailable'];
        report.failureClass = kinds.includes(error?.diagnosticFailureClass) ? error.diagnosticFailureClass : 'unavailable';
      }
      report.coverageAvailable = false;
      throw error;
    }
  }
  const requireSinglePause = () => { if (report.extraPauseObserved) throw failure('unexpectedPause'); };
  async function deadline(promise) {
    const ms = allowance(); if (!ms) throw failure('budgetExhausted');
    let timer; try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(failure('timeout')), ms); })]); }
    finally { clearTimeout(timer); }
  }
  async function send(method, params = {}) {
    owned(); const ms = allowance();
    if (!ms) throw failure('budgetExhausted');
    if (closed || !socket || socket.readyState !== WebSocketImpl.OPEN) throw failure('unavailable');
    return new Promise((resolve, reject) => {
      const id = ++sequence, timer = setTimeout(() => { pending.delete(id); reject(failure('timeout')); }, ms);
      pending.set(id, { resolve, reject, timer });
      try { socket.send(JSON.stringify({ id, method, params })); } catch { pending.delete(id); clearTimeout(timer); reject(failure('unavailable')); }
    });
  }
  async function evaluate(expression) {
    const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: false });
    if (value.exceptionDetails) {
      const name = value.exceptionDetails.exception?.className;
      if (report.exceptionClassName === null) report.exceptionClassName = ['ReferenceError', 'TypeError', 'Error'].includes(name) ? name : 'UNKNOWN';
      throw failure('evaluationException');
    }
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
      await step('endpoint', async () => { owned(); await deadline(uriReady); owned(); });
      await step('connect', async () => {
        socket = new WebSocketImpl(uri); uri = undefined;
        socket.addEventListener('message', event => {
          let message; try { message = JSON.parse(String(event.data)); } catch { rejectPending(); return; }
          if (message.method === 'Debugger.paused') {
            paused = true; pauseCount++;
            if (pauseCount === 1) { report.entryPauseObserved = true; resolveEntryPause(); }
            else { report.extraPauseObserved = true; report.coverageAvailable = false; }
            return; // Never read callFrames, scopes, locals, URLs or pause data.
          }
          if (message.method === 'Debugger.resumed') { paused = false; return; }
          const item = pending.get(message.id); if (!item) return;
          pending.delete(message.id); clearTimeout(item.timer);
          if (message.error) item.reject(failure('protocolError')); else item.resolve(message.result || {});
        });
        socket.addEventListener('close', () => { closed = true; report.detached = true; rejectPending(); });
        socket.addEventListener('error', rejectPending);
        await deadline(new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', () => reject(failure('unavailable')), { once: true }); }));
        report.attached = true;
      });
      await step('runtimeEnable', () => send('Runtime.enable'));
      await step('debuggerEnable', async () => { await send('Debugger.enable'); debuggerEnabled = true; });
      await step('bootstrapRelease', async () => { await send('Runtime.runIfWaitingForDebugger'); report.bootstrapReleased = true; });
      await step('entryPause', async () => { await deadline(entryPause); requireSinglePause(); });
      await step('processAvailability', async () => {
        if (await evaluate(`typeof process==='object' && process!==null`) !== true) throw failure('processUnavailable');
      });
      await step('identity', async () => {
        if (await evaluate(`typeof process==='object' && process!==null && process.pid===${expectedPid} && typeof process.execPath==='string' && process.execPath.toLowerCase()===${JSON.stringify(executable.toLowerCase())}`) !== true) throw failure('bindingMismatch', 'OFFICIAL_DIAGNOSTIC_BINDING');
        verified = true; report.identityConfirmed = true; requireSinglePause();
      });
      await step('registrationAck', async () => {
        requireSinglePause();
        const ack = await evaluate(registrationExpression(packageJson, version));
        if (ack?.reason === 'moduleUnavailable') throw failure('moduleUnavailable');
        if (ack?.appWasReady === true) throw failure('alreadyReady');
        if (ack?.registered !== true || ack.appWasReady !== false) throw failure('ackRejected');
        report.registered = true;
      });
      await step('resume', async () => { await send('Debugger.resume'); paused = false; report.resumeSucceeded = true; });
      await step('debuggerDisable', async () => { await send('Debugger.disable'); debuggerEnabled = false; report.debuggerDisabled = true; requireSinglePause(); });
      report.stage = 'observing';
    },
    async read() {
      try {
        report.observation = verified ? await step('observation', async () => {
          requireSinglePause();
          const value = projectEvents(await evaluate(readExpression));
          if (!value) throw failure('ackRejected');
          return value;
        }) : null;
        report.coverageAvailable = !report.failureClass && report.debuggerDisabled && report.observation?.installed === true
          && report.observation.registered === true && !report.observation.disposed && !report.observation.appWasReady;
      }
      catch { report.observation = null; }
      return { ...report };
    },
    async dispose() {
      // An identity mismatch gets only detach and the existing owned-child
      // cleanup. No framework call or resume is permitted against that endpoint.
      if (verified && socket && !closed) {
        try {
          await step('teardown', async () => { if (await evaluate(removeExpression) !== true) throw failure('ackRejected'); });
          report.teardownSucceeded = true;
        } catch { report.teardownSucceeded = false; }
        if (paused && debuggerEnabled) {
          try { await step('resume', () => send('Debugger.resume')); paused = false; report.resumeSucceeded = true; } catch { }
        }
        if (debuggerEnabled) {
          try { await step('debuggerDisable', () => send('Debugger.disable')); debuggerEnabled = false; report.debuggerDisabled = true; } catch { }
        }
      }
      try { socket?.close(); } catch { }
      if (socket && !closed) { try { await step('detach', () => deadline(new Promise(resolve => socket.addEventListener('close', resolve, { once: true })))); } catch { } }
      closed = true; uri = undefined; partial = ''; rejectPending();
      if (!report.failureClass) report.stage = 'detached';
      return { ...report };
    },
  };
}

module.exports = { diagnosticInspectorArguments, publicUpdateSnapshot, projectEvents, registrationExpression, createOfficialNetworkProbe };
