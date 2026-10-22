'use strict';

const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { randomUUID } = require('node:crypto');
const { HEADER, pageAllowed } = require('./host-policy.cjs');

const CHANNEL = 'funds-trace:startup';
const WORKBENCH_CHANNEL = 'funds-trace:workbench';
const startupFile = path.join(__dirname, 'startup/index.html');
const startupUrl = pathToFileURL(startupFile).href;
const assetUrls = new Set(['startup/index.html', 'startup/style.css', 'startup/renderer.js', 'assets/icon.png']
  .map(name => pathToFileURL(path.join(__dirname, name)).href));

// A separate local-only renderer never receives the backend origin, token or case data.
function createStartup(win, { onFailure, contentInset = 0, focusContents = win.webContents }, { WebContentsView, session, ipcMain, setTimer = setTimeout, clearTimer = clearTimeout } = require('electron')) {
  // Keep the EventEmitter reference before the native window can be destroyed.
  const contentView = win.contentView;
  const local = session.fromPartition(`funds-trace-startup-${randomUUID()}`, { cache: false });
  const view = new WebContentsView({ webPreferences: {
    preload: path.join(__dirname, 'startup/preload.cjs'), session: local,
    sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true,
    allowRunningInsecureContent: false, webviewTag: false, spellcheck: false,
    // Only this short-lived layer runs while occluded, so the fade can finish
    // and release its renderer even if the user switches to another app.
    backgroundThrottling: false,
  } });
  const contents = view.webContents;
  let disposed = false, finishing = false, finishTimer;
  let resolveFinished;
  const finished = new Promise(resolve => { resolveFinished = resolve; });
  const denyPermission = (_contents, _permission, callback) => callback(false);
  local.setPermissionRequestHandler(denyPermission);
  local.setPermissionCheckHandler(() => false);
  local.webRequest.onBeforeRequest((details, callback) => callback({
    cancel: disposed || details.webContentsId !== contents.id || !assetUrls.has(details.url),
  }));
  local.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    for (const key of Object.keys(headers)) if (key.toLowerCase() === HEADER.toLowerCase()) delete headers[key];
    callback({ requestHeaders: headers });
  });
  local.on('will-download', event => event.preventDefault());
  contents.on('will-navigate', event => event.preventDefault());
  contents.on('will-redirect', event => event.preventDefault());
  contents.on('will-attach-webview', event => event.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
  contents.on('render-process-gone', () => { if (!disposed) onFailure(); });

  function fit() {
    if (disposed || win.isDestroyed()) return;
    const { width, height } = win.getContentBounds();
    view.setBounds({ x: 0, y: contentInset, width, height: Math.max(0, height - contentInset) });
  }
  function dispose(completed = false) {
    if (disposed) return;
    disposed = true;
    clearTimer(finishTimer);
    ipcMain.removeListener(CHANNEL, onComplete);
    win.removeListener('resize', fit);
    win.removeListener('closed', onClosed);
    contentView.removeListener('bounds-changed', fit);
    if (!win.isDestroyed()) contentView.removeChildView(view);
    // Removing a child View does not destroy its renderer.
    if (!contents.isDestroyed()) contents.close();
    resolveFinished(completed);
  }
  const onClosed = () => dispose();
  function onComplete(event, action) {
    if (disposed || !finishing || action !== 'complete' || event.sender !== contents
        || event.senderFrame !== contents.mainFrame || event.senderFrame?.url !== startupUrl
        || contents.getURL() !== startupUrl) return;
    dispose(true);
    if (!win.isDestroyed() && !focusContents.isDestroyed()) focusContents.focus();
  }
  ipcMain.on(CHANNEL, onComplete);
  win.on('resize', fit);
  win.once('closed', onClosed);
  contentView.on('bounds-changed', fit);
  view.setBackgroundColor('rgba(0, 0, 0, 0)');
  contentView.addChildView(view);
  fit();
  const loaded = contents.loadFile(startupFile);
  return { loaded, finished, dispose,
    finish() {
      if (disposed || finishing) return;
      finishing = true;
      finishTimer = setTimer(() => { dispose(); onFailure(); }, 10_000);
      finishTimer.unref?.();
      contents.send(CHANNEL, 'ready');
    },
  };
}

// The application's first case, graph and table must finish initializing. A health
// response or did-finish-load alone does not mean the workbench is usable.
function waitForWorkbench(win, origin, { ipcMain, contents = win.webContents, setTimer = setTimeout, clearTimer = clearTimeout } = require('electron')) {
  let settled = false, initialized = false, navigated = false, resolve;
  const ready = new Promise(yes => { resolve = yes; });
  function finish(value) {
    if (settled) return;
    settled = true;
    clearTimer(timer);
    ipcMain.removeListener(WORKBENCH_CHANNEL, onReady);
    win.removeListener('closed', onClosed);
    resolve(value);
  }
  const onClosed = () => finish(false);
  function onReady(event, phase) {
    if (event.sender !== contents || event.senderFrame !== contents.mainFrame
        || event.senderFrame?.url !== contents.getURL()
        || !pageAllowed(contents.getURL(), origin) || new URL(contents.getURL()).pathname !== '/') return;
    if (phase === 'ready') { initialized = true; if (navigated) finish(true); }
    else if (phase === 'failed') finish(false);
  }
  ipcMain.on(WORKBENCH_CHANNEL, onReady);
  win.once('closed', onClosed);
  const timer = setTimer(() => finish(false), 120_000);
  timer.unref?.();
  return { ready, dispose: () => finish(false), navigated() {
    navigated = true;
    if (initialized) finish(true);
  } };
}

module.exports = { createStartup, waitForWorkbench, CHANNEL, WORKBENCH_CHANNEL };
