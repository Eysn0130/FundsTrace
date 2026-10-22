'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { startupTarget } = require('./installed-startup-journey.cjs');
const { titlebarTarget } = require('./installed-titlebar-journey.cjs');

// Exercise the real attachment handler with the lifecycle that exposed the race:
// both renderers start blank, then one becomes the local file and one the app.
function targetHarness() {
  const source = fs.readFileSync(path.join(__dirname, 'installed-app-smoke.cjs'), 'utf8');
  const start = source.indexOf('    cdp.onEvent(message => {');
  const end = source.indexOf("    await cdp.send('Target.setAutoAttach'", start);
  assert.ok(start >= 0 && end > start);
  const calls = [], urls = new Map();
  let callback, infoGate;
  const sessions = new Map(), heldBusinessSessions = new Set(), debuggerSessions = new Set();
  const context = vm.createContext({ startupTarget, titlebarTarget, sessions, heldBusinessSessions, debuggerSessions,
    startupInspectionHeld:true, instrumentationFailed:false, errorCount:0,
    businessOrigin(value) {
      try { const u = new URL(value); return u.protocol === 'http:' && u.hostname === '127.0.0.1'
        && u.port && u.pathname === '/' && !u.username && !u.password ? u.origin : null; } catch { return null; }
    },
    cdp:{onEvent(fn) { callback = fn; },send(method, params, sessionId) {
      calls.push({method,sessionId});
      if (method === 'Target.getTargetInfo') return infoGate || Promise.resolve({targetInfo:{url:urls.get(params.targetId)}});
      return Promise.resolve({});
    }},
  });
  vm.runInContext(source.slice(start, end), context);
  return { calls, sessions, heldBusinessSessions, urls,
    attach(id, url) { callback({method:'Target.attachedToTarget',params:{sessionId:id,targetInfo:{targetId:id,type:'page',url}}}); },
    pause(id) { callback({method:'Debugger.paused',sessionId:id,params:{}}); },
    blockInfo() { let resolve; infoGate = new Promise(yes => { resolve = yes; }); return resolve; },
    releaseFlag() { vm.runInContext('startupInspectionHeld = false', context); },
    flush:() => new Promise(resolve => setImmediate(resolve)),
    healthy:() => vm.runInContext('!instrumentationFailed', context),
  };
}

test('a blank startup target later classified as the local ASAR file is resumed, never held as business', async () => {
  const h = targetHarness(); h.attach('startup','about:blank'); await h.flush();
  h.urls.set('startup','file:///C:/SYN/FundsTrace/resources/app.asar/startup/index.html');
  h.pause('startup'); await h.flush();
  assert.equal(h.heldBusinessSessions.size,0);
  assert.ok(h.calls.some(call => call.method === 'Debugger.resume'));
  assert.ok(h.calls.some(call => call.method === 'Debugger.disable'));
  assert.equal(h.healthy(),true);
});

test('a persistent local caption is never held by business startup instrumentation',async()=>{
  const h=targetHarness();h.attach('caption','about:blank');await h.flush();
  h.urls.set('caption','file:///C:/SYN/FundsTrace/resources/app.asar/titlebar/index.html');
  h.pause('caption');await h.flush();
  assert.equal(h.heldBusinessSessions.size,0);
  assert.ok(h.calls.some(call=>call.method==='Debugger.resume'));
  assert.ok(h.calls.some(call=>call.method==='Debugger.disable'));
  assert.equal(h.healthy(),true);
});

test('a blank target is not kept paused until its URL is proven to be the actual business root', async () => {
  const h = targetHarness(); h.attach('app',''); await h.flush();
  h.urls.set('app','about:blank'); h.pause('app'); await h.flush();
  assert.equal(h.heldBusinessSessions.size,0);
  h.urls.set('app','http://127.0.0.1:54321/'); h.pause('app'); await h.flush();
  assert.equal(h.heldBusinessSessions.has('app'),true);
  assert.equal(h.sessions.get('app').paused,true);
  assert.equal(h.healthy(),true);
});

test('release during an asynchronous classification resumes the renderer instead of adding a late hold', async () => {
  const h = targetHarness(); h.attach('app','about:blank'); await h.flush();
  const resolve = h.blockInfo(); h.pause('app'); h.releaseFlag();
  resolve({targetInfo:{url:'http://127.0.0.1:54321/'}}); await h.flush();
  assert.equal(h.heldBusinessSessions.size,0);
  assert.ok(h.calls.some(call => call.method === 'Debugger.resume'));
  assert.ok(h.calls.some(call => call.method === 'Debugger.disable'));
  assert.equal(h.healthy(),true);
});
