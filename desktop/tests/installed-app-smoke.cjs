'use strict';
// CI-only installed EXE smoke. Never import Main, inspect request headers, or log CDP payloads.
// Usage: node installed-app-smoke.cjs <absolute-install-root> --report <absolute-json> [--screenshot <absolute-png>]
const assert = require('node:assert/strict');
const { spawn, execFile } = require('node:child_process');
const { promisify } = require('node:util');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const os = require('node:os');
const { performance } = require('node:perf_hooks');
const { runInstalledGraphJourney, safeJourneyFailure } = require('./installed-graph-journey.cjs');
const { createUpdateFixture, runInstalledUpdateJourney } = require('./installed-update-journey.cjs');
const { FAILURE_CODES } = require('../updates.cjs');

const SENTINEL = 'FundsTrace synthetic installer lifecycle v1\n';
const SEED_CASE = 'SYN prior installer lifecycle case';
const CREATED_CASE = 'SYN current installer lifecycle case';
const COMPATIBILITY_CASES = ['SYN prior compatibility populated case', 'SYN prior compatibility isolated case',
  'SYN prior compatibility populated case(导入1)', 'SYN prior compatibility populated case(导入2)'];
const VIEWPORTS = [[960, 500], [960, 640], [1280, 640]], DEVICE_SCALES = [1, 1.5, 2];
const report = { schemaVersion: 1, status: 'running', checks: {}, timings: {}, failedCheck: null,
  limits: { windowsServerAutomationOnly: true, windows10And11Interactive: false,
    dpi: false, importRecovery: false, screenshotIsRendererSurface: true,
    nativeGraphExportSaveDialog: false, taskbarAppearance: false,
    simulatedCssViewportOnly: true, actualSystemDpiValidated: false,
    offlineEnforcement: 'not-measured-by-this-helper; see standard-user compatibility report',
    simulatedCssViewports: VIEWPORTS, simulatedDeviceScaleFactors: DEVICE_SCALES } };
const timings = report.timings, owned = [];
let stage = 'ciSyntheticUser', reportPath, reportOwned = false, cdp, page, analysisPage, updateFixture;

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
async function windowIcons(installRoot, processId, minimumWindows, taskbarScreenshot) {
  let stdout;
  try {
    ({ stdout } = await promisify(execFile)('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File',
      path.resolve(__dirname, '../../scripts/test-windows-brand-icon.ps1'),
      '-InstallRoot', installRoot, '-ExpectedIco', path.resolve(__dirname, '../assets/icon.ico'),
      '-ProcessId', String(processId), ...(taskbarScreenshot ? ['-TaskbarScreenshot', taskbarScreenshot] : [])],
    { timeout: 60000, maxBuffer: 256 * 1024 }));
  } catch (error) {
    // Retain only fixed native stages and icon fingerprints. Compiler/COM
    // messages and process arguments may contain local paths or headers.
    try {
      const raw = String(error.stdout || '');
      if (raw.length > 32768) throw new Error('DIAGNOSTIC_TOO_LARGE');
      const failed = JSON.parse(raw.replace(/^\uFEFF/, ''));
      if (failed.schemaVersion === 1 && failed.status === 'failed') {
        const sources = ['WM_GETICON-small2', 'WM_GETICON-big', 'WM_GETICON-small', 'class-small', 'class-big'];
        report.nativeWindowIconFailure = {
          failedCheck: ['ciPlatform', 'inputBinding', 'nativeIconReader', 'installedProcessBinding', 'visibleWindowIcons'].includes(failed.failedCheck) ? failed.failedCheck : null,
          visibleWindowCount: Number.isInteger(failed.visibleWindowCount) && failed.visibleWindowCount >= 0 && failed.visibleWindowCount <= 64 ? failed.visibleWindowCount : null,
          iconComparisons: Array.isArray(failed.iconComparisons) ? failed.iconComparisons.slice(0, 8).filter(icon =>
            sources.includes(icon?.Source) && typeof icon.Matched === 'boolean'
            && Number.isInteger(icon.Width) && icon.Width > 0 && icon.Width <= 1024
            && Number.isInteger(icon.Height) && icon.Height > 0 && icon.Height <= 1024
            && typeof icon.ActualSha256 === 'string' && typeof icon.ExpectedSha256 === 'string'
            && /^[a-f0-9]{64}$/.test(icon.ActualSha256) && /^[a-f0-9]{64}$/.test(icon.ExpectedSha256)
          ).map(({ Source, Width, Height, Matched, ActualSha256, ExpectedSha256 }) =>
            ({ Source, Width, Height, Matched, ActualSha256, ExpectedSha256 })) : [],
        };
      }
    } catch {}
    throw new Error('NATIVE_WINDOW_ICON_CHECK_FAILED');
  }
  const result = JSON.parse(stdout.replace(/^\uFEFF/, ''));
  assert.equal(result.status, 'passed');
  assert.ok(result.visibleWindowCount >= minimumWindows);
  return result;
}
function argumentsOf() {
  const args = process.argv.slice(2);
  assert.ok(args.length === 3 || args.length === 5 || args.length === 7);
  const installRoot = args.shift(), values = {};
  while (args.length) {
    const key = args.shift(), value = args.shift();
    assert.ok(['--report', '--screenshot', '--update-fixture'].includes(key) && !values[key] && value && path.isAbsolute(value));
    values[key] = path.resolve(value);
  }
  assert.ok(installRoot && path.isAbsolute(installRoot) && values['--report']);
  assert.equal(path.extname(values['--report']).toLowerCase(), '.json');
  if (values['--screenshot']) assert.equal(path.extname(values['--screenshot']).toLowerCase(), '.png');
  return { installRoot: path.resolve(installRoot), reportPath: values['--report'], screenshot: values['--screenshot'], updateFixture: values['--update-fixture'] };
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
  const args = debug ? ['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'] : [];
  if (updateFixture) args.push('--no-proxy-server', '--host-resolver-rules=MAP analytix.top 127.0.0.1, MAP download.analytix.top 127.0.0.1');
  const child = spawn(executable, args,
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
async function evaluate(expression, target = page) {
  const result = await cdp.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, target.sessionId);
  assert.ok(!result.exceptionDetails);
  return result.result?.value;
}
// Runtime inspects/layouts the DOM and reads the published update status. Product actions use actual CDP
// pointer input; navigation uses existing links or Page.navigate where no link exists.
async function clickElement(expression, target = page) {
  const point = await evaluate(`(() => {
    const element = ${expression}; if (!element || element.disabled) return null;
    element.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    const rect = element.getBoundingClientRect(), x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    if (rect.width <= 0 || rect.height <= 0 || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return null;
    const hit = document.elementFromPoint(x, y); return hit && (hit === element || element.contains(hit)) ? { x, y } : null;
  })()`, target);
  assert.ok(point);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 }, target.sessionId);
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 }, target.sessionId);
}
function visibleControl(expression) {
  return `(() => {
    const element = ${expression}; if (!element || element.disabled) return false;
    const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
    if (style.visibility !== 'visible' || style.display === 'none' || Number(style.opacity) === 0
      || rect.width < 16 || rect.height < 16 || rect.left < -1 || rect.top < -1
      || rect.right > innerWidth + 1 || rect.bottom > innerHeight + 1) return false;
    return [[.5,.5],[.2,.2],[.8,.2],[.2,.8],[.8,.8]].every(([rx, ry]) => {
      const hit = document.elementFromPoint(rect.x + rect.width * rx, rect.y + rect.height * ry);
      return hit && (hit === element || element.contains(hit));
    });
  })()`;
}
async function readyDocument(expression, target = page) {
  await until(async () => {
    try { return await evaluate(`document.readyState === 'complete' && (${expression})`, target); }
    catch { return false; } // An actual navigation may replace the execution context.
  }, 20000);
}
async function viewportMatrix(name, target, inspect, controls, reachableControls = []) {
  // Chromium emulation checks the shipped renderer at CSS sizes and emulated
  // DPRs. It does not change Windows display scaling or establish real DPI evidence.
  for (const [width, height] of VIEWPORTS) for (const deviceScaleFactor of DEVICE_SCALES) {
    await check(`${name}Css${width}x${height}Dpr${String(deviceScaleFactor).replace('.', '_')}`, async () => {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor,
        mobile: false, screenWidth: width, screenHeight: height }, target.sessionId);
      await evaluate(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`, target);
      assert.equal(await evaluate(`innerWidth === ${width} && innerHeight === ${height}
        && devicePixelRatio === ${deviceScaleFactor}
        && document.documentElement.scrollWidth <= innerWidth + 1 && (${inspect})`, target), true);
      for (const control of controls) assert.equal(await evaluate(visibleControl(control), target), true);
      for (const control of reachableControls) {
        // React is an ordinary vertically scrolling document. Check the empty
        // case entry after bringing it into view, including overlay hit tests.
        await evaluate(`(${control}).scrollIntoView({ block: 'center', inline: 'nearest' })`, target);
        assert.equal(await evaluate(visibleControl(control), target), true);
      }
      await evaluate(`window.scrollTo(0, 0)`, target);
    });
  }
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
  if (config.updateFixture) updateFixture = await check('isolatedUpdateHttpsFixture', () => createUpdateFixture(config.updateFixture));
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
        // A newly created renderer is paused before its execution context
        // exists. Resume it before awaiting Runtime.enable, which otherwise
        // waits for the same paused renderer to initialize.
        await cdp.send('Runtime.runIfWaitingForDebugger', {}, sessionId);
        if (targetInfo.type === 'page') {
          await cdp.send('Runtime.enable', {}, sessionId);
          await cdp.send('Page.enable', {}, sessionId);
        }
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
  report.workspaceWindowIcons = await check('installedWorkspaceWindowIcon', () => windowIcons(config.installRoot, primary.child.pid, 1,
    config.screenshot ? `${config.screenshot.slice(0, -4)}.taskbar.png` : undefined));
  await check('nodeSelectionModuleLoaded', async () => {
    assert.equal(await evaluate(`typeof window.FundsGraphSelection?.create === 'function'
      && !!state.graphSelection && !state.graphSelection.status().destroyed`), true);
  });
  await check('authenticatedWorkspaceLoaded', async () => {
    // The renderer's own normal fetch exercises Main's exact-origin request policy.
    // Return booleans only; never ask CDP for network headers or Main state.
    assert.equal(await evaluate(`(async () => {
      const response = await fetch('/api/v1/cases'); if (!response.ok) return false;
      const value = await response.json(), allowed = new Set(${JSON.stringify([SEED_CASE, CREATED_CASE, ...COMPATIBILITY_CASES])});
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
  });
  await viewportMatrix('graphLayout', page, `(() => {
    const graph = document.querySelector('#graphContainer')?.getBoundingClientRect();
    return graph && graph.width > 100 && graph.height > 80 && graph.left >= 0 && graph.top >= 0
      && graph.right <= innerWidth + 1 && graph.bottom <= innerHeight + 1
      && !!document.querySelector('#graphContainer canvas, #graphContainer svg');
  })()`, [`document.querySelector('#btnImport')`, `document.querySelector('#btnImport2')`]);
  await check('minimalCssViewportScreenshot', async () => {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 960, height: 500, deviceScaleFactor: 1,
      mobile: false, screenWidth: 960, screenHeight: 500 }, page.sessionId);
    await evaluate(`new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))`);
    if (config.screenshot) {
      const result = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, page.sessionId);
      assert.ok(typeof result.data === 'string' && result.data.length < 24 * 1024 * 1024);
      const bytes = Buffer.from(result.data, 'base64');
      assert.ok(bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
      await fs.writeFile(config.screenshot, bytes, { flag: 'wx' });
    }
  });
  await runInstalledGraphJourney({ cdp, page, evaluate, clickElement, until, check,
    createdCase: CREATED_CASE, seedCase: SEED_CASE,
    captureGraph: config.screenshot ? () => check('installedGraphScreenshot', async () => {
      const result = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true,
        captureBeyondViewport: false }, page.sessionId);
      assert.ok(typeof result.data === 'string' && result.data.length < 24 * 1024 * 1024);
      const bytes = Buffer.from(result.data, 'base64');
      assert.ok(bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
      await fs.writeFile(`${config.screenshot.slice(0, -4)}.graph.png`, bytes, { flag: 'wx' });
    }) : null,
  });
  await check('installedReactWorkspaceOpened', async () => {
    // The shipped root has no workspace anchor. Navigate the actual installed
    // BrowserWindow to its public route; do not fetch HTML as rendering evidence.
    await cdp.send('Page.navigate', { url: `${page.origin}/workspace/` }, page.sessionId);
    await readyDocument(`location.pathname === '/workspace/' && !!document.querySelector('.funds-trace .case-list')
      && [...document.querySelectorAll('.case-item')].some(item => item.textContent.includes(${JSON.stringify(SEED_CASE)}))`);
    await clickElement(`[...document.querySelectorAll('.case-item')].find(item => item.textContent.includes(${JSON.stringify(SEED_CASE)}))`);
    await readyDocument(`document.querySelector('.workspace-heading h1')?.textContent === ${JSON.stringify(SEED_CASE)}
      && !!document.querySelector('.empty-state h3') && !document.querySelector('[role="alert"]')`);
  });
  await viewportMatrix('reactWorkspaceLayout', page, `!!document.querySelector('.main-workspace[aria-label="资金流水面板"]')
    && document.querySelector('.workspace-heading h1')?.textContent === ${JSON.stringify(SEED_CASE)}
    && !document.querySelector('[role="alert"]')`, [
    `document.querySelector('.heading-actions .button.primary')`,
  ], [
    `document.querySelector('.empty-state .button.primary')`,
  ]);
  await check('reactEmptyCaseImportEntry', async () => {
    await clickElement(`document.querySelector('.empty-state .button.primary')`);
    await readyDocument(`!!document.querySelector('dialog[open] input[type="file"]')`);
    assert.equal(await evaluate(visibleControl(`document.querySelector('dialog[open] input[type="file"]')`)), true);
    await clickElement(`[...document.querySelectorAll('dialog[open] button')].find(item => item.textContent.trim() === '取消')`);
    await readyDocument(`!document.querySelector('dialog[open]')`);
  });
  await check('workspaceGraphLinkOpened', async () => {
    await clickElement(`document.querySelector('.workspace-nav a[href^="/?case_id="]')`);
    await readyDocument(`location.pathname === '/' && typeof state === 'object' && !state.graphLoading
      && state.graphInited && state.cases.find(item => item.id === state.caseId)?.name === ${JSON.stringify(SEED_CASE)}
      && state.stats.flows === 0 && state.lastGraph.nodes.length === 0`);
  });
  await check('analysisLinkClicked', () => clickElement(`document.querySelector('#analysisLink')`));
  await check('installedAnalysisWindowAttached', async () => {
    analysisPage = await until(async () => {
      assert.ok(!primary.done && !instrumentationFailed);
      const { targetInfos } = await cdp.send('Target.getTargets');
      const pageCount = targetInfos.filter(target => target.type === 'page').length;
      assert.ok(pageCount <= 2);
      if (pageCount !== 2) return false;
      const target = targetInfos.find(target => {
        if (target.type !== 'page' || target.targetId === page.targetId) return false;
        try { const url = new URL(target.url); return url.origin === page.origin && url.pathname === '/analysis'; }
        catch { return false; }
      });
      const session = target && sessions.get(target.targetId);
      return session?.ready ? { ...session, targetId: target.targetId, origin: page.origin } : false;
    }, 20000);
  });
  await check('installedAnalysisLinkOpened', async () => {
    await readyDocument(`location.pathname === '/analysis' && !!document.querySelector('#panel')
      && !!document.querySelector('#results .analysis-empty') && !!document.querySelector('#caseSel')?.options.length
      && document.querySelector('#caseStats')?.textContent.includes('流水')`, analysisPage);
  });
  report.analysisWindowIcons = await check('installedAnalysisWindowIcon', () => windowIcons(config.installRoot, primary.child.pid, 2));
  await viewportMatrix('analysisLayout', analysisPage, `!!document.querySelector('#panel')
    && !!document.querySelector('#results .analysis-empty') && document.querySelector('#caseStats')?.textContent.includes('流水')`, [
    `document.querySelector('#caseSel')`, `document.querySelector('#backToGraph')`,
  ]);
  await check('analysisControlsReachable', async () => {
    await evaluate(`document.querySelector('#btnRun').scrollIntoView({ block: 'center' })`, analysisPage);
    assert.equal(await evaluate(visibleControl(`document.querySelector('#btnRun')`), analysisPage), true);
    await cdp.send('Page.close', {}, analysisPage.sessionId);
    await until(async () => {
      const { targetInfos } = await cdp.send('Target.getTargets');
      return !targetInfos.some(target => target.targetId === analysisPage.targetId);
    }, 10000);
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
  if (updateFixture) report.updateCycle = await runInstalledUpdateJourney({
    fixture: updateFixture, primary, evaluate, clickElement, cdp, page, check, screenshot: config.screenshot,
  });
  await check('normalClose', async () => {
    // CDP Page.close runs beforeunload. Main's window-all-closed/before-quit
    // handlers must finish their owned backend shutdown; Browser.close is not used.
    if (!primary.done) await cdp.send('Page.close', {}, page.sessionId).catch(() => {});
    assert.deepEqual(await bounded(primary.exited, 30000), { code: 0, signal: null });
    assert.equal(errorCount, 0);
  });
  await check('backendReleasedAfterClose', () => until(() => portClosed(Number(new URL(page.origin).port)), 15000));
  report.status = 'passed';
})().catch(async error => {
  report.status = 'failed'; report.failedCheck = stage; process.exitCode = 1;
  if (updateFixture) {
    let errorCode = 'UNAVAILABLE';
    // Read the published status only; do not inspect Main or request headers.
    try {
      const status = await evaluate('window.FundsDesktopUpdates.state()');
      if (FAILURE_CODES.includes(status?.errorCode) || status?.errorCode === 'UNKNOWN') errorCode = status.errorCode;
    } catch {}
    const local = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    const cache = path.join(local, 'fundstrace-updater');
    report.updateFixtureDiagnostics = { ...updateFixture.requests, lastPhase: updateFixture.lastPhase || 'not-started', errorCode,
      cacheUnderOwnProfile: within(os.homedir(), cache),
      cacheDirectoryPresent: await fs.stat(cache).then(value => value.isDirectory(), () => false),
      cachePendingPresent: await fs.stat(path.join(cache, 'pending')).then(value => value.isDirectory(), () => false),
    };
  }
  const detail = safeJourneyFailure(error) || (['UPDATE_DIALOG_TIMEOUT','UPDATE_DIALOG_FAILED','UPDATE_JOURNEY_TIMEOUT'].includes(error?.message)
    ? { step: 'update', kind: error.message } : null);
  if (detail) report.failureDetail = detail;
}).finally(async () => {
  // Best effort graceful close, then only handles spawned above. No process-name
  // discovery, user-data removal, or termination of unrelated installations.
  if (cdp && owned.some(instance => !instance.done)) {
    if (analysisPage) await cdp.send('Page.close', {}, analysisPage.sessionId, 3000).catch(() => {});
    if (page) await cdp.send('Page.close', {}, page.sessionId, 3000).catch(() => {});
    await Promise.all(owned.filter(instance => !instance.done).map(instance => bounded(instance.exited, 5000).catch(() => {})));
  }
  cdp?.close();
  if (updateFixture) await updateFixture.close();
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
