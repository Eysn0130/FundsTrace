'use strict';

const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { randomUUID } = require('node:crypto');
const { HEADER } = require('./host-policy.cjs');

const HEIGHT = 32;
const MENU_CHANNEL = 'funds-trace:titlebar';
const MENU_ENABLED_CHANNEL = 'funds-trace:titlebar-menu-enabled';
const shellFile = path.join(__dirname, 'titlebar/index.html');
const shellUrl = pathToFileURL(shellFile).href;
const assetUrls = new Set(['titlebar/index.html', 'titlebar/style.css', 'titlebar/renderer.js', 'assets/icon.png']
  .map(name => pathToFileURL(path.join(__dirname, name)).href));

// The root document owns only local caption chrome. The caller owns navigation,
// authentication and application behavior in the separate business WebContents.
function createWindowShell({ windowOptions, webPreferences, onFailure, onMenu },
  { BrowserWindow, WebContentsView, session, ipcMain, platform = process.platform } = require('electron')) {
  const local = session.fromPartition(`funds-trace-titlebar-${randomUUID()}`, { cache: false });
  const win = new BrowserWindow({
    ...windowOptions,
    frame: true,
    titleBarStyle: 'hidden',
    titleBarOverlay: platform === 'darwin' ? true : { color: '#151d27', symbolColor: '#cdd7e3', height: HEIGHT },
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'titlebar/preload.cjs'), session: local,
      sandbox: true, contextIsolation: true, nodeIntegration: false,
      nodeIntegrationInWorker: false, nodeIntegrationInSubFrames: false,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false,
      spellcheck: false, navigateOnDragDrop: false, disableDialogs: true,
      zoomFactor: 1,
    },
  });
  if (platform !== 'darwin') win.setMenu(null);
  const root = win.webContents;
  // Native window getters are invalid by the time Electron emits `closed`.
  const contentView = win.contentView;
  const businessView = new WebContentsView({ webPreferences: { ...webPreferences } });
  const contents = businessView.webContents;
  let disposed = false, failed = false, menuEnabled = false;

  const prevent = event => event.preventDefault();
  const reportFailure = () => {
    if (disposed || failed || win.isDestroyed()) return;
    failed = true;
    onFailure();
  };
  local.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  local.setPermissionCheckHandler(() => false);
  local.setDisplayMediaRequestHandler((_request, callback) => callback({}));
  local.webRequest.onBeforeRequest((details, callback) => callback({
    cancel: disposed || details.webContentsId !== root.id || !assetUrls.has(details.url),
  }));
  local.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    for (const key of Object.keys(headers)) if (key.toLowerCase() === HEADER.toLowerCase()) delete headers[key];
    callback({ requestHeaders: headers });
  });
  local.on('will-download', prevent);
  for (const event of ['will-navigate', 'will-frame-navigate', 'will-redirect', 'will-attach-webview']) root.on(event, prevent);
  root.setWindowOpenHandler(() => ({ action: 'deny' }));
  root.on('render-process-gone', reportFailure);
  contents.on('render-process-gone', reportFailure);

  function sendMenuEnabled() {
    if (disposed || root.isDestroyed() || root.getURL() !== shellUrl) return;
    root.send(MENU_ENABLED_CHANNEL, menuEnabled);
  }
  function onMenuRequest(event, action, ...extra) {
    if (disposed || !menuEnabled || extra.length || action !== 'open-menu' || win.isDestroyed()
        || root.isDestroyed() || event?.sender !== root || event.senderFrame !== root.mainFrame
        || event.senderFrame?.url !== shellUrl || root.getURL() !== shellUrl) return;
    onMenu(win);
  }
  function fit() {
    if (disposed || win.isDestroyed()) return;
    const { width, height } = win.getContentBounds();
    businessView.setBounds({ x: 0, y: HEIGHT, width: Math.max(0, width), height: Math.max(0, height - HEIGHT) });
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    ipcMain.removeListener(MENU_CHANNEL, onMenuRequest);
    win.removeListener('resize', fit);
    win.removeListener('closed', dispose);
    contentView.removeListener('bounds-changed', fit);
    root.removeListener('did-finish-load', sendMenuEnabled);
    root.removeListener('render-process-gone', reportFailure);
    for (const event of ['will-navigate', 'will-frame-navigate', 'will-redirect', 'will-attach-webview']) root.removeListener(event, prevent);
    contents.removeListener('render-process-gone', reportFailure);
    local.removeListener('will-download', prevent);
    local.webRequest.onBeforeRequest(null);
    local.webRequest.onBeforeSendHeaders(null);
    if (!win.isDestroyed()) contentView.removeChildView(businessView);
    // Removing a View does not release its renderer, including after win.destroy().
    if (!contents.isDestroyed()) contents.close({ waitForBeforeUnload: false });
  }

  ipcMain.on(MENU_CHANNEL, onMenuRequest);
  root.on('did-finish-load', sendMenuEnabled);
  win.on('resize', fit);
  win.once('closed', dispose);
  contentView.on('bounds-changed', fit);
  businessView.setBackgroundColor(windowOptions?.backgroundColor || '#10171f');
  contentView.addChildView(businessView);
  fit();
  const loaded = root.loadFile(shellFile).catch(error => { reportFailure(); throw error; });
  return { win, contents, loaded, setMenuEnabled(value) {
    if (disposed || typeof value !== 'boolean') return;
    menuEnabled = value;
    sendMenuEnabled();
  } };
}

module.exports = { createWindowShell, HEIGHT };
