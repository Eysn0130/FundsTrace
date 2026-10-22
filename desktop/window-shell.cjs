'use strict';

const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { randomUUID } = require('node:crypto');
const { HEADER } = require('./host-policy.cjs');
const appearance = require('./appearance.cjs');

const HEIGHT = 32;
const shellFile = path.join(__dirname, 'titlebar/index.html');
const shellUrl = pathToFileURL(shellFile).href;
const assetUrls = new Set(['titlebar/index.html', 'titlebar/style.css', 'titlebar/renderer.js', 'assets/icon.png']
  .map(name => pathToFileURL(path.join(__dirname, name)).href));

// The root document owns only local caption chrome. The caller owns navigation,
// authentication and application behavior in the separate business WebContents.
function createWindowShell({ windowOptions, webPreferences, onFailure, appearance: initialAppearance = appearance.DEFAULT },
  { BrowserWindow, WebContentsView, session, platform = process.platform } = require('electron')) {
  let snapshot = appearance.resolveAppearance(initialAppearance);
  const palette = appearance.colors(snapshot);
  const local = session.fromPartition(`funds-trace-titlebar-${randomUUID()}`, { cache: false });
  const win = new BrowserWindow({
    ...windowOptions,
    backgroundColor: palette.background,
    frame: true,
    titleBarStyle: 'hidden',
    titleBarOverlay: platform === 'darwin' ? true : { color: palette.caption, symbolColor: palette.foreground, height: HEIGHT },
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'titlebar/preload.cjs'), session: local,
      sandbox: true, contextIsolation: true, nodeIntegration: false,
      nodeIntegrationInWorker: false, nodeIntegrationInSubFrames: false,
      webSecurity: true, allowRunningInsecureContent: false, webviewTag: false,
      spellcheck: false, navigateOnDragDrop: false, disableDialogs: true,
      zoomFactor: 1,
      additionalArguments: [appearance.ARGUMENT + JSON.stringify(snapshot)],
    },
  });
  if (platform !== 'darwin') win.setMenu(null);
  const root = win.webContents;
  // Native window getters are invalid by the time Electron emits `closed`.
  const contentView = win.contentView;
  const businessView = new WebContentsView({ webPreferences: { ...webPreferences } });
  const contents = businessView.webContents;
  let disposed = false, failed = false;

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

  function sendAppearance() {
    if (disposed || root.isDestroyed() || root.getURL() !== shellUrl) return;
    root.send(appearance.CHANNEL, snapshot);
  }
  function setAppearance(value) {
    const next = appearance.readAppearance(JSON.stringify(value));
    if (disposed || win.isDestroyed() || !next || next.theme === 'system') return false;
    snapshot = next;
    const colors = appearance.colors(snapshot);
    win.setBackgroundColor?.(colors.background);
    businessView.setBackgroundColor(colors.background);
    if (platform !== 'darwin') win.setTitleBarOverlay?.({ color: colors.caption, symbolColor: colors.foreground, height: HEIGHT });
    sendAppearance();
    return true;
  }
  function fit() {
    if (disposed || win.isDestroyed()) return;
    const { width, height } = win.getContentBounds();
    businessView.setBounds({ x: 0, y: HEIGHT, width: Math.max(0, width), height: Math.max(0, height - HEIGHT) });
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    win.removeListener('resize', fit);
    win.removeListener('closed', dispose);
    contentView.removeListener('bounds-changed', fit);
    root.removeListener('did-finish-load', sendAppearance);
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

  root.on('did-finish-load', sendAppearance);
  win.on('resize', fit);
  win.once('closed', dispose);
  contentView.on('bounds-changed', fit);
  businessView.setBackgroundColor(palette.background);
  contentView.addChildView(businessView);
  fit();
  const loaded = root.loadFile(shellFile).catch(error => { reportFailure(); throw error; });
  return { win, contents, loaded, setAppearance };
}

module.exports = { createWindowShell, HEIGHT };
