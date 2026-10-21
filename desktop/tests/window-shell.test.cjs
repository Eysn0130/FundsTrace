'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const vm = require('node:vm');
const { createWindowShell, HEIGHT } = require('../window-shell.cjs');
const { HEADER } = require('../host-policy.cjs');

const MENU_CHANNEL = 'funds-trace:titlebar';
const ENABLED_CHANNEL = 'funds-trace:titlebar-menu-enabled';
const desktop = path.resolve(__dirname, '..');
const localUrl = file => pathToFileURL(path.join(desktop, file)).href;

function harness({ platform = 'win32', loadError } = {}) {
  const sessions = [], views = [], windows = [], ipcMain = new EventEmitter();
  let nextId = 0;
  class Contents extends EventEmitter {
    constructor() {
      super();
      this.id = ++nextId; this.url = ''; this.mainFrame = { url: '' };
      this.sent = []; this.destroyed = false; this.closeCalls = [];
    }
    isDestroyed() { return this.destroyed; }
    getURL() { return this.url; }
    send(...args) { this.sent.push(args); }
    setWindowOpenHandler(handler) { this.popup = handler; }
    loadFile(file) {
      this.loadedFile = file;
      if (loadError) return Promise.reject(loadError);
      this.url = pathToFileURL(file).href; this.mainFrame.url = this.url;
      return Promise.resolve().then(() => this.emit('did-finish-load'));
    }
    close(options) {
      this.closeCalls.push(options); this.destroyed = true;
      // A normal owned-view teardown must never be reported as a crash.
      this.emit('render-process-gone', {}, { reason: 'clean-exit' });
    }
  }
  class Window extends EventEmitter {
    constructor(options) {
      super(); this.options = options; this.webContents = new Contents();
      this.bounds = { width: options.width || 1280, height: options.height || 720 };
      this.destroyed = false;
      this.contentView = new EventEmitter(); this.contentView.children = [];
      this.contentView.addChildView = view => this.contentView.children.push(view);
      this.contentView.removeChildView = view => { this.contentView.children = this.contentView.children.filter(child => child !== view); };
      windows.push(this);
    }
    setMenu(value) { this.menu = value; }
    getContentBounds() { return this.bounds; }
    isDestroyed() { return this.destroyed; }
    destroy() { this.destroyed = true; this.webContents.destroyed = true; this.emit('closed'); }
  }
  class View {
    constructor(options) { this.options = options; this.webContents = new Contents(); this.boundsHistory = []; views.push(this); }
    setBounds(bounds) { this.bounds = bounds; this.boundsHistory.push(bounds); }
    setBackgroundColor(color) { this.background = color; }
  }
  const deps = { BrowserWindow: Window, WebContentsView: View, ipcMain, platform,
    session: { fromPartition(partition, options) {
      const local = new EventEmitter(); local.partition = partition; local.options = options;
      local.setPermissionRequestHandler = handler => { local.permissionRequest = handler; };
      local.setPermissionCheckHandler = handler => { local.permissionCheck = handler; };
      local.setDisplayMediaRequestHandler = handler => { local.displayMedia = handler; };
      local.webRequest = { onBeforeRequest: handler => { local.beforeRequest = handler; },
        onBeforeSendHeaders: handler => { local.beforeSendHeaders = handler; } };
      sessions.push(local); return local;
    } },
  };
  return { deps, ipcMain, sessions, views, windows };
}

function create(fake, options = {}) {
  const failures = [], menus = [];
  const preferences = { session: { business: true }, preload: '/synthetic/business-preload.cjs', sandbox: true, contextIsolation: true, nodeIntegration: false };
  const shell = createWindowShell({
    windowOptions: { width: 1280, height: 720, show: false, backgroundColor: '#10171f' },
    webPreferences: preferences, onFailure: (...args) => failures.push(args), onMenu: win => menus.push(win), ...options,
  }, fake.deps);
  return { ...shell, failures, menus, preferences };
}
function request(handler, details) {
  let result; handler(details, value => { result = value; }); return result;
}
function menuEvent(root, frame = root.mainFrame) { return { sender: root, senderFrame: frame }; }

test('shell keeps the real business WebContents separate from the local caption session', async () => {
  const fake = harness(); const shell = create(fake);
  await shell.loaded;
  const { win, contents } = shell;
  assert.equal(HEIGHT, 32);
  assert.notEqual(contents, win.webContents);
  assert.equal(fake.views[0].webContents, contents);
  assert.deepEqual(fake.views[0].options.webPreferences, shell.preferences);
  assert.equal(fake.views[0].options.webPreferences.session, shell.preferences.session);
  assert.equal(win.options.webPreferences.session, fake.sessions[0]);
  assert.notEqual(win.options.webPreferences.session, shell.preferences.session);
  assert.match(fake.sessions[0].partition, /^funds-trace-titlebar-/);
  assert.deepEqual(fake.sessions[0].options, { cache: false });
  assert.equal(win.webContents.loadedFile, path.join(desktop, 'titlebar/index.html'));
  assert.equal(win.options.show, false);
  assert.deepEqual(win.contentView.children, [fake.views[0]]);
  assert.equal(fake.views[0].background, '#10171f');
  win.destroy();
});

test('native caption controls stay enabled while caller business preferences cannot enter the root', async () => {
  for (const platform of ['win32', 'linux', 'darwin']) {
    const fake = harness({ platform });
    const shell = create(fake, { windowOptions: { frame: false, titleBarStyle: 'default', titleBarOverlay: false,
      webPreferences: { nodeIntegration: true, sandbox: false, preload: '/synthetic/forbidden.cjs', session: { business: true } } } });
    await shell.loaded;
    assert.equal(shell.win.options.frame, true);
    assert.equal(shell.win.options.titleBarStyle, 'hidden');
    assert.deepEqual(shell.win.options.titleBarOverlay, platform === 'darwin' ? true : { color: '#151d27', symbolColor: '#cdd7e3', height: 32 });
    assert.equal(shell.win.options.autoHideMenuBar, true);
    if (platform !== 'darwin') assert.equal(shell.win.menu, null);
    const rootPreferences = shell.win.options.webPreferences;
    assert.equal(rootPreferences.preload, path.join(desktop, 'titlebar/preload.cjs'));
    for (const key of ['sandbox', 'contextIsolation', 'webSecurity']) assert.equal(rootPreferences[key], true, key);
    for (const key of ['nodeIntegration', 'nodeIntegrationInWorker', 'nodeIntegrationInSubFrames', 'allowRunningInsecureContent', 'webviewTag', 'navigateOnDragDrop']) assert.equal(rootPreferences[key], false, key);
    assert.equal(rootPreferences.zoomFactor, 1);
    shell.win.destroy();
  }
});

test('caption requests allow only exact bundled assets from this root renderer', async () => {
  const fake = harness(); const shell = create(fake); await shell.loaded;
  const root = shell.win.webContents, handler = fake.sessions[0].beforeRequest;
  for (const file of ['titlebar/index.html', 'titlebar/style.css', 'titlebar/renderer.js', 'assets/icon.png']) {
    assert.deepEqual(request(handler, { url: localUrl(file), webContentsId: root.id }), { cancel: false }, file);
    assert.deepEqual(request(handler, { url: localUrl(file), webContentsId: shell.contents.id }), { cancel: true });
  }
  for (const url of ['https://example.invalid/', 'http://127.0.0.1:54321/', 'ws://127.0.0.1:54321/', 'data:text/html,caption',
    'file:///synthetic/other.txt', localUrl('preload.cjs'), localUrl('titlebar/preload.cjs'), `${localUrl('titlebar/index.html')}?copy`, `${localUrl('assets/icon.png')}#copy`]) {
    assert.deepEqual(request(handler, { url, webContentsId: root.id }), { cancel: true }, url);
  }
  shell.win.destroy();
  assert.deepEqual(request(handler, { url: localUrl('titlebar/index.html'), webContentsId: root.id }), { cancel: true });
});

test('caption denies navigation, subframes, popups, permissions and downloads', async () => {
  const fake = harness(); const shell = create(fake); await shell.loaded;
  const root = shell.win.webContents, local = fake.sessions[0];
  let denied;
  local.permissionRequest(root, 'camera', value => { denied = value; }); assert.equal(denied, false);
  assert.equal(local.permissionCheck(root, 'media'), false);
  local.displayMedia({}, value => { denied = value; }); assert.deepEqual(denied, {});
  for (const eventName of ['will-navigate', 'will-frame-navigate', 'will-redirect', 'will-attach-webview']) {
    let prevented = false; root.emit(eventName, { preventDefault() { prevented = true; } }, 'https://example.invalid/');
    assert.equal(prevented, true, eventName);
  }
  let prevented = false; local.emit('will-download', { preventDefault() { prevented = true; } }); assert.equal(prevented, true);
  assert.deepEqual(root.popup({ url: localUrl('titlebar/index.html') }), { action: 'deny' });
  shell.win.destroy();
});

test('caption session strips desktop authentication headers without mutating renderer headers', async () => {
  const fake = harness(); const shell = create(fake); await shell.loaded;
  const headers = { [HEADER]: 'synthetic-untrusted', [HEADER.toLowerCase()]: 'synthetic-lowercase',
    [HEADER.toUpperCase()]: 'synthetic-uppercase', Accept: 'text/html' };
  assert.deepEqual(request(fake.sessions[0].beforeSendHeaders, { requestHeaders: headers }), { requestHeaders: { Accept: 'text/html' } });
  assert.equal(headers[HEADER], 'synthetic-untrusted');
  assert.deepEqual(request(fake.sessions[0].beforeSendHeaders, { requestHeaders: { Accept: 'image/png' } }), { requestHeaders: { Accept: 'image/png' } });
  shell.win.destroy();
});

test('business bounds reserve exactly 32 DIP through resize and content bounds changes', async () => {
  const fake = harness(); const shell = create(fake); await shell.loaded;
  const view = fake.views[0];
  assert.deepEqual(view.bounds, { x: 0, y: 32, width: 1280, height: 688 });
  shell.win.bounds = { width: 960, height: 640 }; shell.win.emit('resize');
  assert.deepEqual(view.bounds, { x: 0, y: 32, width: 960, height: 608 });
  shell.win.bounds = { width: 683, height: 324 }; shell.win.contentView.emit('bounds-changed');
  assert.deepEqual(view.bounds, { x: 0, y: 32, width: 683, height: 292 });
  shell.win.bounds = { width: 400, height: 16 }; shell.win.emit('resize');
  assert.deepEqual(view.bounds, { x: 0, y: 32, width: 400, height: 0 });
  shell.win.destroy();
  const count = view.boundsHistory.length;
  shell.win.emit('resize'); shell.win.contentView.emit('bounds-changed');
  assert.equal(view.boundsHistory.length, count);
});

test('menu is disabled until enabled and rejects non-main frames, other senders and altered file URLs', async () => {
  const fake = harness(); const shell = create(fake); await shell.loaded;
  const root = shell.win.webContents;
  assert.deepEqual(root.sent, [[ENABLED_CHANNEL, false]]);
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'open-menu'); assert.equal(shell.menus.length, 0);
  shell.setMenuEnabled(true); assert.deepEqual(root.sent.at(-1), [ENABLED_CHANNEL, true]);
  for (const event of [undefined, menuEvent(root, { url: root.getURL() }), { sender: shell.contents, senderFrame: root.mainFrame },
    { sender: root, senderFrame: null }]) fake.ipcMain.emit(MENU_CHANNEL, event, 'open-menu');
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'close');
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'open-menu', { unexpected: true });
  root.mainFrame.url += '#different'; fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'open-menu');
  root.mainFrame.url = localUrl('titlebar/index.html'); root.url += '?different';
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'open-menu');
  assert.equal(shell.menus.length, 0);
  root.url = localUrl('titlebar/index.html');
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'open-menu'); assert.deepEqual(shell.menus, [shell.win]);
  shell.setMenuEnabled(false); fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'open-menu');
  shell.setMenuEnabled('true'); fake.ipcMain.emit(MENU_CHANNEL, menuEvent(root), 'open-menu');
  assert.deepEqual(root.sent.at(-1), [ENABLED_CHANNEL, false]); assert.equal(shell.menus.length, 1);
  shell.win.destroy();
});

test('menu enablement survives local load completion without changing caption options or business bounds', async () => {
  const fake = harness(); const shell = create(fake);
  const options = structuredClone({ titleBarStyle: shell.win.options.titleBarStyle, titleBarOverlay: shell.win.options.titleBarOverlay });
  const bounds = { ...fake.views[0].bounds };
  shell.setMenuEnabled(true); await shell.loaded;
  assert.deepEqual(shell.win.webContents.sent.at(-1), [ENABLED_CHANNEL, true]);
  assert.deepEqual({ titleBarStyle: shell.win.options.titleBarStyle, titleBarOverlay: shell.win.options.titleBarOverlay }, options);
  assert.deepEqual(fake.views[0].bounds, bounds);
  shell.win.destroy();
  const sent = shell.win.webContents.sent.length;
  shell.setMenuEnabled(false); shell.win.webContents.emit('did-finish-load');
  assert.equal(shell.win.webContents.sent.length, sent);
});

test('destroy releases the business renderer and every owned listener before closing it', async () => {
  const fake = harness(); const shell = create(fake); await shell.loaded;
  const root = shell.win.webContents, local = fake.sessions[0];
  assert.equal(fake.ipcMain.listenerCount(MENU_CHANNEL), 1);
  assert.equal(shell.win.listenerCount('resize'), 1);
  assert.equal(shell.win.contentView.listenerCount('bounds-changed'), 1);
  shell.win.destroy();
  assert.deepEqual(shell.contents.closeCalls, [{ waitForBeforeUnload: false }]);
  assert.equal(fake.ipcMain.listenerCount(MENU_CHANNEL), 0);
  assert.equal(shell.win.listenerCount('resize'), 0);
  assert.equal(shell.win.contentView.listenerCount('bounds-changed'), 0);
  assert.equal(root.listenerCount('did-finish-load'), 0);
  assert.equal(root.listenerCount('render-process-gone'), 0);
  assert.equal(shell.contents.listenerCount('render-process-gone'), 0);
  assert.equal(local.listenerCount('will-download'), 0);
  assert.equal(local.beforeRequest, null);
  assert.equal(local.beforeSendHeaders, null);
  assert.deepEqual(shell.failures, []);
  shell.win.emit('closed');
  assert.equal(shell.contents.closeCalls.length, 1);
});

test('multiple windows isolate caption requests, menu enablement and cleanup', async () => {
  const fake = harness(); const first = create(fake), second = create(fake);
  await Promise.all([first.loaded, second.loaded]);
  assert.notEqual(fake.sessions[0].partition, fake.sessions[1].partition);
  assert.equal(fake.ipcMain.listenerCount(MENU_CHANNEL), 2);
  first.setMenuEnabled(true);
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(second.win.webContents), 'open-menu');
  assert.deepEqual(first.menus, []); assert.deepEqual(second.menus, []);
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(first.win.webContents), 'open-menu');
  assert.deepEqual(first.menus, [first.win]); assert.deepEqual(second.menus, []);
  assert.deepEqual(request(fake.sessions[0].beforeRequest, { url: localUrl('assets/icon.png'), webContentsId: second.win.webContents.id }), { cancel: true });
  first.win.destroy(); assert.equal(fake.ipcMain.listenerCount(MENU_CHANNEL), 1);
  second.setMenuEnabled(true);
  fake.ipcMain.emit(MENU_CHANNEL, menuEvent(second.win.webContents), 'open-menu'); assert.deepEqual(second.menus, [second.win]);
  assert.deepEqual(request(fake.sessions[1].beforeRequest, { url: localUrl('assets/icon.png'), webContentsId: second.win.webContents.id }), { cancel: false });
  second.win.destroy();
});

test('root load failure and renderer loss report once while normal destruction stays quiet', async () => {
  const error = new Error('synthetic caption load failure');
  const failed = create(harness({ loadError: error }));
  await assert.rejects(failed.loaded, error); assert.equal(failed.failures.length, 1);
  failed.win.destroy(); assert.equal(failed.failures.length, 1);
  for (const layer of ['root', 'business']) {
    const fake = harness(); const shell = create(fake); await shell.loaded;
    const contents = layer === 'root' ? shell.win.webContents : shell.contents;
    contents.emit('render-process-gone', {}, { reason: 'crashed' });
    contents.emit('render-process-gone', {}, { reason: 'crashed' });
    assert.equal(shell.failures.length, 1, layer);
    shell.win.destroy(); assert.equal(shell.failures.length, 1, layer);
  }
});

test('caption preload exposes only fixed menu IPC and buffers strictly boolean enabled state', () => {
  const ipc = new EventEmitter(), sent = [], documentListeners = new Map(); let bridge;
  ipc.send = (...args) => sent.push(args);
  const document = { documentElement: { dataset: {} }, addEventListener: (event, listener) => documentListeners.set(event, listener) };
  vm.runInNewContext(fs.readFileSync(path.join(desktop, 'titlebar/preload.cjs'), 'utf8'), {
    document, process: { platform: 'darwin' }, require: name => {
      assert.equal(name, 'electron');
      return { ipcRenderer: ipc, contextBridge: { exposeInMainWorld: (name, value) => { assert.equal(name, 'FundsTraceTitlebar'); bridge = value; } } };
    },
  });
  assert.deepEqual(Object.keys(bridge).sort(), ['onMenuEnabled', 'openMenu']);
  const seen = []; const unsubscribe = bridge.onMenuEnabled(value => seen.push(value));
  assert.deepEqual(seen, [false]);
  for (const value of ['true', 1, {}, null]) ipc.emit(ENABLED_CHANNEL, { private: 'synthetic' }, value);
  assert.deepEqual(seen, [false]);
  ipc.emit(ENABLED_CHANNEL, {}, true); assert.deepEqual(seen, [false, true]);
  const late = []; bridge.onMenuEnabled(value => late.push(value)); assert.deepEqual(late, [true]);
  unsubscribe(); ipc.emit(ENABLED_CHANNEL, {}, false); assert.deepEqual(seen, [false, true]);
  bridge.openMenu({ ignored: true }); assert.deepEqual(sent, [[MENU_CHANNEL, 'open-menu']]);
  documentListeners.get('DOMContentLoaded')(); assert.equal(document.documentElement.dataset.platform, 'darwin');
});

test('caption renderer only changes menu availability and never sends a disabled click', () => {
  let enabledListener, openCount = 0, removed = false;
  const events = new Map(), windowEvents = new Map();
  const menu = { disabled: true, addEventListener: (event, listener) => events.set(event, listener) };
  const bridge = { onMenuEnabled(callback) { enabledListener = callback; callback(false); return () => { removed = true; }; },
    openMenu() { openCount++; } };
  vm.runInNewContext(fs.readFileSync(path.join(desktop, 'titlebar/renderer.js'), 'utf8'), {
    document: { querySelector: selector => { assert.equal(selector, '#titlebar-menu'); return menu; } },
    window: { FundsTraceTitlebar: bridge, addEventListener: (event, listener) => windowEvents.set(event, listener) },
  });
  events.get('click')(); assert.equal(openCount, 0);
  enabledListener(true); assert.equal(menu.disabled, false); events.get('click')(); assert.equal(openCount, 1);
  enabledListener('false'); assert.equal(menu.disabled, false);
  enabledListener(false); assert.equal(menu.disabled, true); events.get('click')(); assert.equal(openCount, 1);
  windowEvents.get('pagehide')(); assert.equal(removed, true);
});

test('caption document stays local and exposes only a menu button beside the approved icon', () => {
  const html = fs.readFileSync(path.join(desktop, 'titlebar/index.html'), 'utf8');
  assert.match(html, /connect-src 'none'/);
  assert.match(html, /frame-src 'none'/);
  assert.match(html, /object-src 'none'/);
  assert.doesNotMatch(html, /https?:|\son\w+\s*=|<iframe|<webview|<style[\s>]/i);
  const buttons = [...html.matchAll(/<button\b([^>]*)>/g)];
  assert.equal(buttons.length, 1);
  assert.match(buttons[0][1], /id="titlebar-menu"/);
  assert.match(buttons[0][1], /\bdisabled\b/);
  assert.match(html, /<button\b[^>]*id="titlebar-menu"[^>]*>\s*菜单\s*<\/button>/);
  const references = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(references.sort(), ['../assets/icon.png', 'renderer.js', 'style.css']);
  for (const reference of references) assert.ok(fs.existsSync(path.resolve(desktop, 'titlebar', reference)));
});
