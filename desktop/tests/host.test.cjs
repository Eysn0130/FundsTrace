'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const vm = require('node:vm');
const { launchConfig, sameOrigin, pageAllowed, popupKind, requestAllowed, downloadAllowed, HEADER, windowBounds } = require('../host-policy.cjs');
const { startBackend, verifyHealth } = require('../backend.cjs');

const origin = 'http://127.0.0.1:54321';

test('initial window and minimum sizes fit a small logical work area without double DPI scaling', () => {
  for (const area of [{x:0,y:0,width:1920,height:1040}, {x:0,y:0,width:960,height:500},
    {x:-1280,y:24,width:1280,height:696}]) {
    const bounds = windowBounds(area);
    assert.ok(bounds.width <= area.width && bounds.height <= area.height);
    assert.ok(bounds.minWidth <= bounds.width && bounds.minHeight <= bounds.height);
    assert.ok(bounds.x >= area.x && bounds.x + bounds.width <= area.x + area.width);
    assert.ok(bounds.y >= area.y && bounds.y + bounds.height <= area.y + area.height);
  }
  assert.deepEqual(windowBounds({x:0,y:0,width:960,height:500}),
    {x:0,y:0,width:960,height:500,minWidth:960,minHeight:500});
});

test('existing bounds retain a user size and position, fitting only outside the current work area', () => {
  const area = { x: -1280, y: 24, width: 1280, height: 696 };
  assert.deepEqual(windowBounds(area, { x: -1200, y: 60, width: 1100, height: 650 }),
    { x: -1200, y: 60, width: 1100, height: 650, minWidth: 960, minHeight: 640 });
  assert.deepEqual(windowBounds({ x: 1920, y: 20, width: 960, height: 500 },
    { x: 2100, y: 100, width: 1200, height: 760 }),
  { x: 1920, y: 20, width: 960, height: 500, minWidth: 960, minHeight: 500 });
});

test('host denies remote/credential origins, documents and arbitrary popups', () => {
  assert.equal(sameOrigin(`${origin}/api`, origin), true);
  for (const url of ['http://127.0.0.1:54322/', 'http://localhost:54321/', 'file:///etc/passwd', 'https://example.com', 'http://name:pass@127.0.0.1:54321/', 'javascript:alert(1)']) {
    assert.equal(sameOrigin(url, origin), false, url);
    assert.equal(pageAllowed(url, origin), false, url);
    assert.equal(popupKind(url, origin), 'deny', url);
  }
  assert.equal(pageAllowed(`${origin}/workspace/?case_id=2`, origin), true);
  assert.equal(pageAllowed(`${origin}/static/app.js`, origin), false);
  assert.equal(popupKind(`${origin}/analysis?case_id=2`, origin), 'page');
  assert.equal(popupKind(`${origin}/api/export/tracked.xlsx?case_id=2`, origin), 'download');
  assert.equal(popupKind(`${origin}/api/account-ids/template`, origin), 'download');
  assert.equal(popupKind(`${origin}/api/cases/2/export`, origin), 'download');
  assert.equal(popupKind(`${origin}/api/cases/2/export/extra`, origin), 'deny');
  assert.equal(popupKind(`${origin}/api/cases/0/export`, origin), 'deny');
  assert.equal(popupKind(`${origin}/api/cases`, origin), 'deny');
});

test('requests require an owned renderer and exact backend origin; blob downloads stay local', () => {
  const owned = new Set([7]);
  assert.equal(requestAllowed(`${origin}/static/graph-layout.js`, origin, 7, owned), true);
  for (const id of [-1, 0, 8, undefined]) assert.equal(requestAllowed(`${origin}/`, origin, id, owned), false);
  assert.equal(requestAllowed('https://example.com/', origin, 7, owned), false);
  assert.equal(downloadAllowed(`blob:${origin}/a-b`, origin), true);
  assert.equal(downloadAllowed('blob:https://example.com/a', origin), false);
});

test('source launch isolates runtime and Python settings, preserving only normal license configuration', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'funds-trace-launch-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const python = path.join(directory, 'python'); fs.writeFileSync(python, 'not executed');
  const env = { PATH: 'preserved', FUND_DATA_DIR: '/unrelated/data', PYTHONPATH: '/injected', FUNDS_TRACE_PYTHON: '/wrong/python', FUNDS_TRACE_LICENSE_SECRET: 'test-only-value', FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE: path.join(directory, 'public.pem') };
  const normal = launchConfig({ packaged: false, repoRoot: directory, python, dataDir: path.join(directory, 'data'), env });
  assert.equal(normal.command, python);
  assert.deepEqual(normal.args, [path.join(directory, 'desktop_backend.py')]);
  assert.equal(normal.env.FUND_DATA_DIR, undefined);
  assert.equal(normal.env.PYTHONPATH, undefined);
  assert.equal(normal.env.FUNDS_TRACE_PYTHON, undefined);
  assert.equal(normal.env.FUNDS_TRACE_LICENSE_SECRET, 'test-only-value');
  assert.equal(normal.env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE, env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE);
  assert.equal(normal.env.PYTHONUTF8, '1');
  assert.equal(launchConfig({ packaged: false, repoRoot: directory, python, dataDir: directory, synthetic: true, env }).env.FUNDS_TRACE_LICENSE_SECRET, undefined);
  assert.equal(launchConfig({ packaged: false, repoRoot: directory, python, dataDir: directory, synthetic: true, env }).env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE, undefined);
  assert.throws(() => launchConfig({ packaged: false, repoRoot: directory, python, dataDir: directory, env: { FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE: 'relative.pem' } }), /absolute/);
  assert.throws(() => launchConfig({ packaged: false, repoRoot: directory, python: 'python', dataDir: directory }), /absolute/);
});

test('installed launch refuses missing/mismatched native bundles and synthetic mode without fallback', t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'funds-trace-bundle-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const backend = path.join(directory, 'backend'); fs.mkdirSync(backend);
  const input = { packaged: true, resourcesPath: directory, dataDir: path.join(directory, 'data'), platform: 'win32', arch: 'x64' };
  assert.throws(() => launchConfig(input));
  fs.writeFileSync(path.join(backend, 'build-manifest.json'), JSON.stringify({ protocol: 1, platform: 'darwin', arch: 'x64' }));
  assert.throws(() => launchConfig(input), /mismatch/);
  fs.writeFileSync(path.join(backend, 'build-manifest.json'), JSON.stringify({ protocol: 1, platform: 'win32', arch: 'x64' }));
  assert.throws(() => launchConfig(input));
  fs.writeFileSync(path.join(backend, 'funds-trace-backend.exe'), 'not executed');
  assert.equal(launchConfig(input).command, path.join(backend, 'funds-trace-backend.exe'));
  assert.throws(() => launchConfig({ ...input, synthetic: true }), /Synthetic/);
});

function fakeChild() {
  const child = new EventEmitter(); child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.kills = [];
  child.kill = signal => { child.kills.push(signal || 'SIGTERM'); queueMicrotask(() => child.emit('exit', null, signal)); };
  return child;
}
const config = { command: '/explicit/python', args: ['/explicit/desktop_backend.py'], cwd: '/explicit', env: {}, dataDir: '/explicit/data', synthetic: true };

test('backend bootstrap sends token only over stdin, requires health, EOF stop is idempotent', async () => {
  const child = fakeChild(); let options, command, args, input = '', healthCalls = 0;
  child.stdin.on('data', value => { input += value; });
  child.stdin.on('finish', () => queueMicrotask(() => child.emit('exit', 0)));
  const instance = startBackend(config, { spawnProcess: (cmd, argv, opts) => { command = cmd; args = argv; options = opts; return child; }, health: async (port, token) => { healthCalls++; assert.equal(port, 54321); assert.match(token, /^[a-f0-9]{64}$/); }, readyTimeout: 1000 });
  const bootstrap = JSON.parse(input); assert.equal(bootstrap.protocol, 1); assert.equal(bootstrap.synthetic, true); assert.equal(bootstrap.dataDir, config.dataDir);
  assert.equal(command, config.command); assert.deepEqual(args, config.args);
  assert.equal(options.shell, false); assert.equal(options.windowsHide, true);
  assert.equal(JSON.stringify(options).includes(bootstrap.token), false);
  child.stdout.write('{"protocol":1,'); child.stdout.write('"port":54321}\n');
  const result = await instance.ready; assert.equal(result.origin, origin); assert.equal(healthCalls, 1);
  assert.equal(result.token, bootstrap.token);
  assert.deepEqual(await Promise.all([instance.stop(), instance.stop()]), [true, true]); assert.deepEqual(child.kills, []);
});

test('malformed, oversized and wrong-service readiness fail without exposing child output', async () => {
  for (const output of ['not readiness secret\n', '{"protocol":1,"port":0}\n', '{"protocol":1,"port":54321,"token":"secret"}\n', 'x'.repeat(5000)]) {
    const child = fakeChild(); child.stdin.on('finish', () => child.emit('exit', 0));
    const instance = startBackend(config, { spawnProcess: () => child, readyTimeout: 1000 });
    child.stdout.write(output);
    await assert.rejects(instance.ready, error => !error.message.includes('secret') && error.message.includes('readiness'));
    await instance.stop();
  }
  const child = fakeChild(); child.stdin.on('finish', () => child.emit('exit', 0));
  const instance = startBackend(config, { spawnProcess: () => child, health: async () => { throw new Error('sensitive diagnostic'); }, readyTimeout: 1000 });
  child.stdout.write('{"protocol":1,"port":54321}\n');
  await assert.rejects(instance.ready, /identity check failed/); await instance.stop();
});

test('backend timeout and kill fallback affect only the held child', async () => {
  const child = fakeChild();
  const instance = startBackend(config, { spawnProcess: () => child, readyTimeout: 10, stopTimeout: 10 });
  await assert.rejects(instance.ready, /timed out/); assert.equal(await instance.stop(), false); assert.deepEqual(child.kills, ['SIGTERM']);
});

test('health checks status, protocol and exact service using main-only authorization', async t => {
  let response = { protocol: 1, service: 'FundsTrace', ready: true }, observed;
  const server = http.createServer((req, res) => { observed = req.headers[HEADER.toLowerCase()]; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(response)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  await verifyHealth(server.address().port, 'test-main-only-token'); assert.equal(observed, 'test-main-only-token');
  response = { protocol: 1, service: 'Other', ready: true };
  await assert.rejects(verifyHealth(server.address().port, 'test-main-only-token'), /identity check failed/);
});

test('distribution manifest prevents cross-platform packaging and includes only host source', async t => {
  const builder = require('../electron-builder.cjs');
  assert.equal(builder.appId, 'com.eysn.fundstrace');
  assert.deepEqual(builder.files, ['package.json', 'main.cjs', 'backend.cjs', 'host-policy.cjs', 'preferences.cjs', 'preload.cjs', 'updates.cjs']);
  assert.deepEqual(builder.publish, { provider: 'generic', url: 'https://analytix.top/fundstrace/' });
  assert.equal(builder.nsis.deleteAppDataOnUninstall, false);
  assert.equal(builder.win.signExecutable, false);
  assert.notEqual(builder.win.signAndEditExecutable, false, 'unsigned packaging must retain executable version resources');
  assert.deepEqual(builder.extraResources, [
    { from: path.resolve(__dirname, '../../build/desktop/backend/funds-trace-backend'), to: 'backend', filter: ['**/*'] },
    { from: path.resolve(__dirname, '../licenses/Apache-2.0.txt'), to: 'licenses/Analytix-Apache-2.0.txt' }
  ]);
  // Missing native backend must fail rather than create an Electron-only package.
  if (!fs.existsSync(path.join(builder.extraResources[0].from, 'build-manifest.json'))) {
    await assert.rejects(builder.beforePack({ electronPlatformName: 'win32', arch: 1 }));
  }
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Exercise the real Main entry and Electron lifecycle events without a business server.
function mainHarness({ mkdir = async () => {}, synthetic = false, updater, stopBackend = async () => true, restartResponse = 0 } = {}) {
  const app = new EventEmitter();
  const state = { windows: [], errors: [], backendStarts: 0, backendStops: 0, quitCount: 0, removed: [] };
  const ready = deferred();
  const screen = new EventEmitter();
  state.display = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1040 } };
  screen.getPrimaryDisplay = screen.getDisplayMatching = () => state.display;
  app.isPackaged = !!updater;
  app.getVersion = () => '0.1.7';
  app.setName = app.setAppUserModelId = () => {};
  app.requestSingleInstanceLock = () => true;
  app.whenReady = () => Promise.resolve();
  app.getPath = () => path.join(os.tmpdir(), 'FundsTrace synthetic user');
  app.quit = () => {
    let prevented = false;
    app.emit('before-quit', { preventDefault: () => { prevented = true; } });
    if (!prevented) state.quitCount++;
  };
  class Window extends EventEmitter {
    constructor(options) {
      super(); this.options = options; this.loads = []; this.shown = 0; this.focused = 0;
      this.bounds = { x: options.x, y: options.y, width: options.width, height: options.height };
      this.minimum = [options.minWidth, options.minHeight]; this.boundsChanges = [];
      this.webContents = new EventEmitter(); this.webContents.id = state.windows.length + 1;
      this.webContents.setWindowOpenHandler = handler => { this.popup = handler; };
      this.webContents.isDestroyed = () => this.destroyed;
      this.webContents.getURL = () => this.loads.at(-1);
      this.webContents.send = () => {};
      state.windows.push(this);
    }
    async loadURL(url) { this.loads.push(url); this.webContents.mainFrame = { url }; this.emit('ready-to-show'); }
    show() { this.shown++; }
    focus() { this.focused++; }
    isMinimized() { return true; }
    restore() { this.restored = true; }
    isDestroyed() { return Boolean(this.destroyed); }
    getBounds() { return { ...this.bounds }; }
    getMinimumSize() { return [...this.minimum]; }
    setMinimumSize(width, height) { this.minimum = [width, height]; }
    setBounds(bounds) {
      assert.ok(bounds.width >= this.minimum[0] && bounds.height >= this.minimum[1], 'minimum must be lowered before fitting bounds');
      this.bounds = { ...bounds }; this.boundsChanges.push(bounds); this.emit('move');
    }
    isMaximized() { return Boolean(this.maximized); }
    isFullScreen() { return Boolean(this.fullscreen); }
    close() {
      this.destroyed = true; this.emit('closed');
      if (state.windows.every(win => win.destroyed)) app.emit('window-all-closed');
    }
  }
  const localSession = new EventEmitter();
  localSession.setPermissionRequestHandler = handler => { state.permission = handler; };
  localSession.setPermissionCheckHandler = handler => { state.permissionCheck = handler; };
  localSession.webRequest = {
    onBeforeRequest: handler => { state.request = handler; },
    onBeforeSendHeaders: handler => { state.headers = handler; },
  };
  const electron = { app, ipcMain: new EventEmitter(), BrowserWindow: Window, session: { fromPartition: () => localSession }, screen,
    Menu: { buildFromTemplate: value => value, setApplicationMenu: () => {} },
    dialog: { showErrorBox: (_title, message) => state.errors.push(message), showMessageBox: async () => ({ response: restartResponse }) } };
  electron.ipcMain.handle = (name, handler) => { state.updateRequest = handler; };
  const policy = { ...require('../host-policy.cjs'), launchConfig: input => input };
  const filesystem = { mkdir, mkdtemp: async () => path.join(os.tmpdir(), 'FundsTrace owned temporary'),
    rm: async root => { state.removed.push(root); } };
  const source = fs.readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
  vm.runInNewContext(source, { require: name => {
    if (name === 'electron') return electron;
    if (name === './host-policy.cjs') return policy;
    if (name === './updates.cjs') return require('../updates.cjs');
    if (name === 'electron-updater') return { autoUpdater: updater };
    if (name === './preferences.cjs') return { ...require('../preferences.cjs'), preferenceStore: () => ({
      getItem: () => null, setItem: () => true, removeItem: () => true,
    }) };
    if (name === 'node:fs/promises') return filesystem;
    if (name === './backend.cjs') return { startBackend: (_config, options) => {
      state.backendStarts++; state.backendOptions = options;
      return { ready: ready.promise, stop: async () => { state.backendStops++; return await stopBackend(); } };
    } };
    return require(name);
  }, clearInterval, setInterval, setImmediate, __dirname: path.resolve(__dirname, '..'), process: { argv: ['FundsTrace', ...(synthetic ? ['--synthetic'] : [])], env: {}, platform: 'win32' } });
  return { app, state, ready, screen, flush: async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); } };
}

test('Main adapts existing windows after DPI/work-area changes while retaining bounds that still fit', async () => {
  const harness = mainHarness(); await harness.flush();
  const win = harness.state.windows[0];
  win.bounds = { x: 200, y: 100, width: 1100, height: 750 };
  harness.state.display = { id: 1, workArea: { x: 0, y: 0, width: 960, height: 500 } };
  harness.screen.emit('display-metrics-changed', {}, harness.state.display, ['scaleFactor', 'workArea']);
  assert.deepEqual(win.minimum, [960, 500]);
  assert.deepEqual(win.bounds, { x: 0, y: 0, width: 960, height: 500 });
  assert.equal(win.boundsChanges.length, 1);
  harness.state.display = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1040 } };
  win.bounds = { x: 50, y: 70, width: 1200, height: 760 };
  harness.screen.emit('display-metrics-changed', {}, harness.state.display, ['workArea']);
  assert.deepEqual(win.minimum, [960, 640]);
  assert.deepEqual(win.bounds, { x: 50, y: 70, width: 1200, height: 760 });
  assert.equal(win.boundsChanges.length, 1, 'a valid user size and position must not be reset');
  win.close(); await harness.flush();
  assert.equal(harness.screen.listenerCount('display-metrics-changed'), 0);
  assert.equal(harness.screen.listenerCount('display-removed'), 0);
});

test('Main fits a new display or removed-display recovery without trapping movement inside the old display', async () => {
  const harness = mainHarness(); await harness.flush();
  const win = harness.state.windows[0];
  win.bounds = { x: 1000, y: 60, width: 1200, height: 760 };
  win.emit('move');
  assert.equal(win.boundsChanges.length, 0, 'normal dragging must reach the next display');
  harness.state.display = { id: 2, workArea: { x: 1920, y: 20, width: 960, height: 500 } };
  win.bounds = { x: 2100, y: 100, width: 1200, height: 760 };
  win.emit('move');
  assert.deepEqual(win.minimum, [960, 500]);
  assert.deepEqual(win.bounds, { x: 1920, y: 20, width: 960, height: 500 });
  harness.state.display = { id: 1, workArea: { x: 0, y: 0, width: 1920, height: 1040 } };
  harness.screen.emit('display-removed', {}, { id: 2 });
  assert.deepEqual(win.bounds, { x: 960, y: 20, width: 960, height: 500 });
  const changes = win.boundsChanges.length;
  win.maximized = true;
  win.bounds = { x: -8, y: -8, width: 1936, height: 1056 };
  harness.screen.emit('display-metrics-changed', {}, harness.state.display, ['workArea']);
  assert.equal(win.boundsChanges.length, changes, 'the window manager owns maximized bounds');
  win.maximized = false;
  win.emit('unmaximize');
  assert.deepEqual(win.bounds, { x: 0, y: 0, width: 1920, height: 1040 }, 'restoring normal bounds rechecks the current work area');
  win.fullscreen = true;
  win.bounds = { x: -8, y: -8, width: 1936, height: 1056 };
  harness.screen.emit('display-metrics-changed', {}, harness.state.display, ['workArea']);
  assert.equal(win.boundsChanges.length, changes + 1, 'the window manager owns fullscreen bounds');
  win.fullscreen = false;
  win.emit('leave-full-screen');
  assert.deepEqual(win.bounds, { x: 0, y: 0, width: 1920, height: 1040 });
  win.close(); await harness.flush();
  assert.equal(win.listenerCount('move'), 0);
  assert.equal(win.listenerCount('unmaximize'), 0);
});

test('empty-case minimap suppression preserves the user preference and rejects stale case statistics', async () => {
  const nodes = new Map();
  const node = selector => {
    if (!nodes.has(selector)) {
      const classes = new Set();
      nodes.set(selector, { style: {}, hidden: false, disabled: false, attributes: {},
        classList: { toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
        querySelectorAll: () => [],
        setAttribute(name, value) { this.attributes[name] = value; } });
    }
    return nodes.get(selector);
  };
  const stats = deferred();
  const context = vm.createContext({ document: { querySelector: node, querySelectorAll: () => [] },
    window: { location: { href: `${origin}/?case_id=1`, search: '?case_id=1' }, history: { replaceState() {} }, addEventListener() {} },
    localStorage: { getItem: () => null, setItem() {} }, FundsMoney: { count: String }, URL, URLSearchParams, clearTimeout, setTimeout,
    fetch: async () => ({ ok: true, headers: { get: () => 'application/json' }, json: () => stats.promise }) });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/app.js'), 'utf8'), context);
  const run = source => vm.runInContext(source, context);
  run('selectCase(1); state.stats = { flows: 3 }; updateHeader();');
  const panel = node('#graphMinimapPanel'), button = node('#btnMinimap'), wrap = node('#canvasWrap');
  assert.equal(wrap.classList.contains('case-empty'), false);
  assert.equal(button.disabled, false);
  const pending = run('refreshStats()');
  run('selectCase(2)');
  assert.equal(wrap.classList.contains('case-empty'), true, 'case selection suppresses an old minimap before statistics arrive');
  assert.equal(button.disabled, true);
  assert.equal(panel.hidden, false, 'temporary suppression must not change the visibility preference');
  stats.resolve({ flows: 3 }); await pending;
  assert.equal(wrap.classList.contains('case-empty'), true, 'late results from the old case cannot restore its minimap');
  run('state.stats = { flows: 2 }; updateHeader();');
  assert.equal(wrap.classList.contains('case-empty'), false);
  assert.equal(button.disabled, false);
  assert.equal(button.attributes['aria-pressed'], 'true');
  panel.hidden = true;
  run('selectCase(1); state.stats = { flows: 3 }; updateHeader();');
  assert.equal(panel.hidden, true, 'a minimap hidden by the user stays hidden across empty and populated cases');
  assert.equal(button.attributes['aria-pressed'], 'false');
});

test('Main shows a restricted startup window before backend readiness and focuses it on a second launch', async () => {
  const harness = mainHarness(); await harness.flush();
  const { state } = harness;
  assert.equal(state.backendStarts, 1);
  assert.equal(state.windows.length, 1, 'slow backend must still have a visible application window');
  const win = state.windows[0];
  assert.ok(win.shown > 0);
  assert.match(decodeURIComponent(win.loads[0]), /正在启动/);
  assert.match(decodeURIComponent(win.loads[0]), /default-src 'none'/);
  assert.equal(win.options.webPreferences.sandbox, true);
  assert.equal(win.options.webPreferences.contextIsolation, true);
  assert.equal(win.options.webPreferences.nodeIntegration, false);
  let response;
  const request = { url: win.loads[0], resourceType: 'mainFrame', webContentsId: win.webContents.id };
  state.request(request, value => { response = value; }); assert.equal(response.cancel, false);
  state.request({ ...request, url: 'https://example.com/' }, value => { response = value; }); assert.equal(response.cancel, true);
  state.headers({ ...request, requestHeaders: { [HEADER]: 'renderer-supplied' } }, value => { response = value; });
  assert.equal(response.requestHeaders[HEADER], undefined);
  harness.app.emit('second-instance'); assert.equal(win.focused, 1); assert.equal(win.restored, true);
  harness.ready.resolve({ origin, token: 'main-only-test-token' }); await harness.flush();
  assert.deepEqual(win.loads.slice(1), [`${origin}/`]);
  state.request(request, value => { response = value; }); assert.equal(response.cancel, true, 'startup document is unavailable after readiness');
  state.headers({ ...request, url: `${origin}/api/cases`, requestHeaders: {} }, value => { response = value; });
  assert.equal(response.requestHeaders[HEADER], 'main-only-test-token');
  assert.ok(!win.loads[0].includes('main-only-test-token'));
  win.close(); await harness.flush(); assert.equal(state.backendStops, 1); assert.equal(state.quitCount, 1);
});

test('closing during preparation removes owned temporary state and never launches a late backend', async () => {
  const directory = deferred();
  const harness = mainHarness({ mkdir: () => directory.promise, synthetic: true }); await harness.flush();
  assert.equal(harness.state.windows.length, 1);
  harness.state.windows[0].close(); await harness.flush();
  assert.equal(harness.state.quitCount, 0, 'quit waits for preparation cleanup');
  directory.resolve(); await harness.flush();
  assert.equal(harness.state.backendStarts, 0);
  assert.equal(harness.state.removed.length, 1);
  assert.equal(harness.state.quitCount, 1);
});

test('closing a startup window stops its backend and ignores readiness arriving after close', async () => {
  const harness = mainHarness(); await harness.flush();
  const win = harness.state.windows[0]; assert.ok(win, 'startup window exists before readiness');
  win.close(); await harness.flush(); assert.equal(harness.state.backendStops, 1);
  harness.ready.resolve({ origin, token: 'never-rendered' }); await harness.flush();
  assert.equal(win.loads.length, 1); assert.equal(harness.state.windows.length, 1);
  assert.deepEqual(harness.state.errors, []);
});

test('a startup failure reports a safe recovery message and stops the owned backend', async () => {
  const harness = mainHarness(); await harness.flush();
  harness.ready.reject(new Error('FundsTrace backend startup timed out')); await harness.flush();
  assert.equal(harness.state.errors.length, 1);
  assert.match(harness.state.errors[0], /启动超时/);
  assert.match(harness.state.errors[0], /重新打开/);
  assert.equal(harness.state.backendStops, 1);
  assert.equal(harness.state.windows[0].loads.length, 1);
  assert.equal(harness.state.quitCount, 1);
});

test('installed update stops the owned backend before running NSIS and ordinary quit never installs', async () => {
  for (const { update, graceful } of [{ update: false, graceful: true }, { update: true, graceful: true }, { update: true, graceful: false }]) {
    const updater = new EventEmitter(), installed = [], stopped = deferred();
    const info = { product: 'com.eysn.fundstrace', version: '0.1.8', files: [{
      url: 'https://download.analytix.top/releases/fundstrace/windows/0.1.8/FundsTrace-0.1.8-windows-x64-unsigned.exe',
      sha512: Buffer.alloc(64, 1).toString('base64'), size: 160000000,
    }] };
    updater.checkForUpdates = async () => ({ updateInfo: info });
    updater.downloadUpdate = async () => ['main-owned-cache-file'];
    updater.quitAndInstall = (...args) => { installed.push(args); harness.app.quit(); };
    const harness = mainHarness({ updater, stopBackend: async () => { await stopped.promise; return graceful; }, restartResponse: 1 });
    await harness.flush(); harness.ready.resolve({ origin: 'http://127.0.0.1:55123', token: 'synthetic-only-token' }); await harness.flush();
    const sender = harness.state.windows[0].webContents;
    const event = { sender, senderFrame: sender.mainFrame };
    assert.equal((await harness.state.updateRequest(event, 'state')).phase, 'available');
    assert.equal((await harness.state.updateRequest(event, 'download')).phase, 'ready');
    if (update) await harness.state.updateRequest(event, 'install'); else harness.app.quit();
    await harness.flush(); assert.equal(harness.state.backendStops, 1); assert.deepEqual(installed, []);
    stopped.resolve(); await harness.flush();
    assert.equal(installed.length, update && graceful ? 1 : 0);
    if (update && graceful) assert.deepEqual(installed[0], [true, true]);
    if (update && !graceful) assert.ok(harness.state.errors.some(message => message.includes('本次未启动安装')));
    assert.equal(harness.state.quitCount, 1);
  }
});
