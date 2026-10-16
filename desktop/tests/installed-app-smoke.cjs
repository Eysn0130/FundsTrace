'use strict';
// CI-only installed EXE smoke. Never import Main, inspect request headers, or log CDP payloads.
// Usage: node installed-app-smoke.cjs <absolute-install-root> --report <absolute-json> [--screenshot <absolute-png>]
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const { performance } = require('node:perf_hooks');

const SENTINEL = 'FundsTrace synthetic installer lifecycle v1\n';
const SEED_CASE = 'SYN prior installer lifecycle case';
const CREATED_CASE = 'SYN current installer lifecycle case';
const report = { schemaVersion: 1, status: 'running', checks: {}, timings: {}, failedCheck: null,
  limits: { windowsServerAutomationOnly: true, windows10And11Interactive: false,
    dpi: false, importRecovery: false, screenshotIsRendererSurface: true } };
const timings = report.timings, owned = [];
let stage = 'ciSyntheticUser', reportPath, reportOwned = false, cdp, page;

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function bounded(promise, ms) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('TIMEOUT')), ms);
  })]); } finally { clearTimeout(timer); }
}
async function until(check, ms) {
  const deadline = performance.now() + ms;
  do { const result = await check(); if (result) return result; await delay(100); }
  while (performance.now() < deadline);
  throw new Error('TIMEOUT');
}
async function check(name, action) {
  stage = name;
  const start = performance.now();
  const result = await action();
  timings[name] = Math.round(performance.now() - start);
  report.checks[name] = true;
  return result;
}
function argumentsOf() {
  const args = process.argv.slice(2);
  assert.ok(args.length === 3 || args.length === 5);
  const installRoot = args.shift(), values = {};
  while (args.length) {
    const key = args.shift(), value = args.shift();
    assert.ok(['--report', '--screenshot'].includes(key) && !values[key] && value && path.isAbsolute(value));
    values[key] = path.resolve(value);
  }
  assert.ok(installRoot && path.isAbsolute(installRoot) && values['--report']);
  assert.equal(path.extname(values['--report']).toLowerCase(), '.json');
  if (values['--screenshot']) assert.equal(path.extname(values['--screenshot']).toLowerCase(), '.png');
  return { installRoot: path.resolve(installRoot), reportPath: values['--report'], screenshot: values['--screenshot'] };
}
function within(root, value) {
  const relative = path.relative(root, value);
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
}
function businessOrigin(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port
      && !url.username && !url.password && url.pathname === '/' ? url.origin : null;
  } catch { return null; }
}
function boot(executable, installRoot, env, debug) {
  const child = spawn(executable, debug ? ['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'] : [],
    { cwd: installRoot, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: false });
  const instance = { child, done: false, failed: false, debugUrl: null };
  owned.push(instance);
  instance.exited = new Promise(resolve => {
    child.once('exit', (code, signal) => { instance.done = true; resolve({ code, signal }); });
    child.once('error', () => { instance.failed = true; instance.done = true; resolve({ code: null, signal: null }); });
  });
  child.stdout.resume();
  let partial = '';
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', chunk => {
    if (!debug || instance.debugUrl) return;
    partial = (partial + chunk).slice(-8192);
    const lines = partial.split('\n'); partial = lines.pop();
    for (const line of lines) {
      const match = /^DevTools listening on (ws:\/\/127\.0\.0\.1:[1-9]\d*\/devtools\/browser\/[A-Za-z0-9-]+)\r?$/.exec(line);
      if (match) { instance.debugUrl = match[1]; partial = ''; break; }
    }
  });
  return instance;
}

// Only method names, result objects and session IDs stay in memory. Protocol errors
// deliberately discard their messages, which can contain URLs or page contents.
async function connect(url) {
  const socket = new WebSocket(url), pending = new Map();
  let sequence = 0, closed = false, listener = () => {};
  function rejectPending() {
    closed = true;
    for (const { reject, timer } of pending.values()) { clearTimeout(timer); reject(new Error('CDP_CLOSED')); }
    pending.clear();
  }
  socket.addEventListener('close', rejectPending);
  socket.addEventListener('error', rejectPending);
  socket.addEventListener('message', event => {
    let message;
    try { message = JSON.parse(String(event.data)); } catch { rejectPending(); return; }
    if (message.id) {
      const request = pending.get(message.id); if (!request) return;
      pending.delete(message.id); clearTimeout(request.timer);
      if (message.error) request.reject(new Error('CDP_COMMAND')); else request.resolve(message.result || {});
    } else {
      try { listener(message); } catch { rejectPending(); }
    }
  });
  try {
    await bounded(new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', () => reject(new Error('CDP_CONNECT')), { once: true });
    }), 10000);
  } catch (error) { socket.close(); throw error; }
  return {
    onEvent(handler) { listener = handler; },
    send(method, params = {}, sessionId, timeout = 10000) {
      if (closed || socket.readyState !== WebSocket.OPEN) return Promise.reject(new Error('CDP_CLOSED'));
      return new Promise((resolve, reject) => {
        const id = ++sequence;
        const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP_TIMEOUT')); }, timeout);
        pending.set(id, { resolve, reject, timer });
        try { socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })); }
        catch { pending.delete(id); clearTimeout(timer); reject(new Error('CDP_SEND')); }
      });
    },
    close() { listener = () => {}; rejectPending(); socket.close(); },
  };
}
async function evaluate(expression) {
  const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, page.sessionId);
  assert.ok(!result.exceptionDetails);
  return result.result?.value;
}
async function portClosed(port) {
  return new Promise(resolve => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const finish = value => { socket.destroy(); resolve(value); };
    socket.once('connect', () => finish(false));
    socket.once('error', error => finish(error.code === 'ECONNREFUSED'));
    socket.setTimeout(1000, () => finish(false));
  });
}

(async () => {
  const config = await check('ciSyntheticUser', async () => {
    assert.equal(process.platform, 'win32'); assert.equal(process.arch, 'x64');
    assert.equal(process.env.CI, 'true'); assert.equal(process.env.GITHUB_ACTIONS, 'true');
    assert.ok(Number(process.versions.node.split('.')[0]) >= 22 && typeof WebSocket === 'function');
    const value = argumentsOf();
    assert.ok(process.env.APPDATA && path.isAbsolute(process.env.APPDATA));
    const userRoot = path.join(process.env.APPDATA, 'FundsTrace');
    assert.equal(await fs.readFile(path.join(userRoot, 'installer-lifecycle-sentinel.txt'), 'utf8'), SENTINEL);
    assert.ok((await fs.stat(path.join(userRoot, 'data', 'fund.duckdb'))).isFile());
    for (const output of [value.reportPath, value.screenshot].filter(Boolean)) {
      assert.ok(!within(userRoot, output) && !within(value.installRoot, output));
    }
    assert.ok((await fs.stat(path.join(value.installRoot, 'FundsTrace.exe'))).isFile());
    return value;
  });
  reportPath = config.reportPath;
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' }); reportOwned = true;
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    !/^(FUND_|FUNDS_TRACE_|ELECTRON_|NODE_OPTIONS$)/i.test(name)));
  const executable = path.join(config.installRoot, 'FundsTrace.exe');
  const primary = await check('installedExecutableStarted', async () => {
    const instance = boot(executable, config.installRoot, env, true);
    await bounded(new Promise((resolve, reject) => {
      instance.child.once('spawn', resolve);
      instance.child.once('error', () => reject(new Error('SPAWN_FAILED')));
    }), 10000);
    return instance;
  });
  let errorCount = 0, instrumentationFailed = false;
  const sessions = new Map();
  await check('installedWindowReady', async () => {
    const debugUrl = await until(() => {
      assert.ok(!primary.done); return primary.debugUrl;
    }, 30000);
    cdp = await connect(debugUrl);
    cdp.onEvent(message => {
      if (message.method === 'Runtime.exceptionThrown' || message.method === 'Inspector.targetCrashed'
        || message.method === 'Target.targetCrashed'
        || message.method === 'Runtime.consoleAPICalled' && message.params?.type === 'error') errorCount += 1;
      if (message.method !== 'Target.attachedToTarget') return;
      const { sessionId, targetInfo } = message.params;
      const item = { sessionId, ready: false };
      if (targetInfo.type === 'page') sessions.set(targetInfo.targetId, item);
      void (async () => {
        if (targetInfo.type === 'page') {
          await cdp.send('Runtime.enable', {}, sessionId);
          await cdp.send('Page.enable', {}, sessionId);
        }
        await cdp.send('Runtime.runIfWaitingForDebugger', {}, sessionId);
        item.ready = true;
      })().catch(() => { instrumentationFailed = true; });
    });
    await cdp.send('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
    page = await until(async () => {
      assert.ok(!primary.done && !instrumentationFailed);
      const { targetInfos } = await cdp.send('Target.getTargets');
      const pages = targetInfos.filter(target => target.type === 'page');
      assert.ok(pages.length <= 1);
      const target = pages[0];
      const origin = target && businessOrigin(target.url);
      if (!origin) return false;
      const session = sessions.get(target.targetId);
      return session?.ready ? { ...session, targetId: target.targetId, origin } : false;
    }, 90000);
    await until(async () => {
      assert.ok(!primary.done && !instrumentationFailed);
      const session = sessions.get(page.targetId);
      if (!session?.ready) return false;
      page.sessionId = session.sessionId;
      // The data: startup document can replace its execution context while a
      // first DOM probe is in flight. Retry only this bounded navigation probe.
      try {
        return await evaluate(`document.readyState === 'complete' && !!document.querySelector('#graphContainer')
          && !!document.querySelector('#caseSelect') && typeof state === 'object' && !state.graphLoading && state.graphInited`);
      } catch { return false; }
    }, 15000);
  });
  await check('nodeSelectionModuleLoaded', async () => {
    assert.equal(await evaluate(`typeof window.FundsGraphSelection?.create === 'function'
      && !!state.graphSelection && !state.graphSelection.status().destroyed`), true);
  });
  await check('authenticatedWorkspaceLoaded', async () => {
    // The renderer's own normal fetch exercises Main's exact-origin request policy.
    // Return booleans only; never ask CDP for network headers or Main state.
    assert.equal(await evaluate(`(async () => {
      const response = await fetch('/api/v1/cases'); if (!response.ok) return false;
      const value = await response.json(), allowed = new Set(${JSON.stringify([SEED_CASE, CREATED_CASE])});
      return Array.isArray(value.items) && value.items.some(item => item.name === ${JSON.stringify(SEED_CASE)})
        && value.items.some(item => item.name === ${JSON.stringify(CREATED_CASE)})
        && value.items.every(item => item.id === '1' || allowed.has(item.name));
    })()`), true);
    const denied = await fetch(`${page.origin}/api/v1/cases`, { redirect: 'manual', signal: AbortSignal.timeout(10000) });
    assert.equal(denied.status, 403); await denied.arrayBuffer();
  });
  await check('activationFree', async () => {
    assert.equal(await evaluate(`(async () => {
      const response = await fetch('/api/license/info'); if (!response.ok) return false;
      const value = await response.json(); return value.activation_required === false && value.activated === false;
    })()`), true);
  });
  await check('syntheticWindowContent', async () => {
    assert.equal(await evaluate(`(() => {
      const select = document.querySelector('#caseSelect');
      const option = [...select.options].find(item => item.textContent === ${JSON.stringify(CREATED_CASE)});
      if (!option) return false; select.value = option.value; select.dispatchEvent(new Event('change', { bubbles: true })); return true;
    })()`), true);
    await until(() => evaluate(`!state.graphLoading && state.graphInited && state.graphSelection
      && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(CREATED_CASE)}
      && state.lastGraph.nodes.length === 0 && state.lastGraph.edges.length === 0`), 15000);
    if (config.screenshot) {
      const result = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, page.sessionId);
      assert.ok(typeof result.data === 'string' && result.data.length < 24 * 1024 * 1024);
      const bytes = Buffer.from(result.data, 'base64');
      assert.ok(bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
      await fs.writeFile(config.screenshot, bytes, { flag: 'wx' });
    }
  });
  await check('singleInstance', async () => {
    const second = boot(executable, config.installRoot, env, false);
    assert.deepEqual(await bounded(second.exited, 15000), { code: 0, signal: null });
    assert.ok(!primary.done);
    const { targetInfos } = await cdp.send('Target.getTargets');
    const pages = targetInfos.filter(target => target.type === 'page');
    assert.equal(pages.length, 1); assert.equal(pages[0].targetId, page.targetId);
    assert.equal(businessOrigin(pages[0].url), page.origin);
    assert.equal(await evaluate(`document.readyState === 'complete' && !!document.querySelector('#graphContainer')`), true);
  });
  await check('noPageExceptions', async () => {
    assert.ok(!instrumentationFailed); assert.equal(errorCount, 0);
  });
  await check('normalClose', async () => {
    // CDP Page.close runs beforeunload. Main's window-all-closed/before-quit
    // handlers must finish their owned backend shutdown; Browser.close is not used.
    await cdp.send('Page.close', {}, page.sessionId).catch(() => {});
    assert.deepEqual(await bounded(primary.exited, 30000), { code: 0, signal: null });
    assert.equal(errorCount, 0);
  });
  await check('backendReleasedAfterClose', () => until(() => portClosed(Number(new URL(page.origin).port)), 15000));
  report.status = 'passed';
})().catch(() => {
  report.status = 'failed'; report.failedCheck = stage; process.exitCode = 1;
}).finally(async () => {
  // Best effort graceful close, then only handles spawned above. No process-name
  // discovery, user-data removal, or termination of unrelated installations.
  if (cdp && owned.some(instance => !instance.done)) {
    if (page) await cdp.send('Page.close', {}, page.sessionId, 3000).catch(() => {});
    await Promise.all(owned.filter(instance => !instance.done).map(instance => bounded(instance.exited, 5000).catch(() => {})));
  }
  cdp?.close();
  for (const instance of owned) {
    if (!instance.done) {
      instance.child.kill();
      await bounded(instance.exited, 10000).catch(() => { report.status = 'failed'; report.failedCheck = 'ownedProcessCleanup'; process.exitCode = 1; });
    }
  }
  if (reportOwned) {
    try { await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8'); }
    catch { report.status = 'failed'; report.failedCheck = 'safeReport'; process.exitCode = 1; }
  }
  if (report.status === 'passed') process.stdout.write(`${JSON.stringify(report)}\n`);
  else process.stderr.write(`${report.failedCheck} failed.\n`);
}).catch(() => {
  process.stderr.write('ownedProcessCleanup failed.\n'); process.exitCode = 1;
});
