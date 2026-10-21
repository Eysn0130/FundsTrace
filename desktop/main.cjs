'use strict';

const { app, BrowserWindow, Menu, dialog, session, screen, ipcMain } = require('electron');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { launchConfig, HEADER, pageAllowed, popupKind, requestAllowed, downloadAllowed, windowBounds } = require('./host-policy.cjs');
const { startBackend } = require('./backend.cjs');
const { CHANNEL, preferenceStore, preferenceRequest } = require('./preferences.cjs');
const updates = require('./updates.cjs');
const { createStartup, waitForWorkbench } = require('./startup.cjs');
const appIcon = path.join(__dirname, 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png');

app.setName('FundsTrace');
app.setAppUserModelId('com.eysn.fundstrace');
const synthetic = process.argv.includes('--synthetic');
let backend, preparation, temporaryRoot, quitting = false, shutdownComplete = false, fatalShown = false;
let updateService, installUpdate = false, updateTimer;
const owned = new Set();
const windows = new Set();

async function fail(message) {
  if (quitting || fatalShown) return;
  fatalShown = true;
  dialog.showErrorBox('FundsTrace', message);
  app.quit();
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    const win = [...windows][0]; if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); }
  });
  app.on('before-quit', event => {
    if (shutdownComplete) return;
    event.preventDefault(); if (quitting) return; quitting = true;
    clearInterval(updateTimer);
    void (async () => {
      try {
        await preparation?.catch(() => {});
        const graceful = await backend?.stop();
        if (installUpdate && graceful !== true) {
          installUpdate = false;
          dialog.showErrorBox('FundsTrace 更新', '数据服务未能正常退出，本次未启动安装。请重新打开应用，确认导入和导出已完成后重试更新。');
        }
        if (temporaryRoot) await fs.rm(temporaryRoot, { recursive: true, force: true });
      } catch {
        if (installUpdate) {
          installUpdate = false;
          dialog.showErrorBox('FundsTrace 更新', '退出数据服务时发生错误，本次未启动安装。请重新打开应用后重试更新。');
        }
      }
      finally {
        shutdownComplete = true;
        if (installUpdate) updateService.install();
        else app.quit();
      }
    })();
  });
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(async () => {
    try {
      if (synthetic && app.isPackaged) throw new Error('Synthetic mode is unavailable in installed builds.');
      app.dock?.setIcon(appIcon);
      let origin, token;
      const localSession = session.fromPartition(`funds-trace-${randomUUID()}`, { cache: false });
      localSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
      localSession.setPermissionCheckHandler(() => false);
      localSession.webRequest.onBeforeRequest((details, callback) => {
        callback({ cancel: !requestAllowed(details.url, origin, details.webContentsId, owned) });
      });
      localSession.webRequest.onBeforeSendHeaders((details, callback) => {
        const headers = { ...details.requestHeaders };
        for (const key of Object.keys(headers)) if (key.toLowerCase() === HEADER.toLowerCase()) delete headers[key];
        if (requestAllowed(details.url, origin, details.webContentsId, owned)) headers[HEADER] = token;
        callback({ requestHeaders: headers });
      });
      localSession.on('will-download', (event, item, webContents) => {
        if (!webContents || !owned.has(webContents.id) || !downloadAllowed(item.getURL(), origin)) { event.preventDefault(); return; }
        item.once('done', (_event, state) => {
          if (state === 'completed' || quitting) return;
          const owner = webContents.isDestroyed() ? null : BrowserWindow.fromWebContents(webContents);
          const options = { type: state === 'cancelled' ? 'info' : 'warning', title: 'FundsTrace 下载', message: state === 'cancelled' ? '下载已取消，文件未保存。' : '下载未完成，请重新导出并选择保存位置。' };
          void (owner ? dialog.showMessageBox(owner, options) : dialog.showMessageBox(options));
        });
        // Do not setSavePath: Electron presents the normal Save dialog.
      });
      function createWindow() {
        const owner = [...windows][0];
        const display = owner ? screen.getDisplayMatching(owner.getBounds()) : screen.getPrimaryDisplay();
        const win = new BrowserWindow({ title: '循迹 FundsTrace', backgroundColor: '#10171f', icon: appIcon, ...windowBounds(display.workArea), show: false,
          webPreferences: { preload: path.join(__dirname, 'preload.cjs'), session: localSession, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, spellcheck: false } });
        const contents = win.webContents;
        const contentsId = contents.id;
        windows.add(win); owned.add(contentsId);
        let displayId = display.id, fitting = false;
        const fitWorkArea = () => {
          if (fitting || win.isDestroyed()) return;
          fitting = true;
          try {
            const current = win.getBounds(), nextDisplay = screen.getDisplayMatching(current);
            displayId = nextDisplay.id;
            const { minWidth, minHeight, ...bounds } = windowBounds(nextDisplay.workArea, current);
            const [minimumWidth, minimumHeight] = win.getMinimumSize();
            if (minimumWidth !== minWidth || minimumHeight !== minHeight) win.setMinimumSize(minWidth, minHeight);
            // The window manager owns maximized/fullscreen bounds (including frame margins).
            if (win.isMaximized() || win.isFullScreen()) return;
            if (['x', 'y', 'width', 'height'].some(key => bounds[key] !== current[key])) win.setBounds(bounds);
          } finally { fitting = false; }
        };
        const movedDisplay = () => {
          // Do not trap a drag at the old display edge before it can cross to the next one.
          if (!win.isDestroyed() && screen.getDisplayMatching(win.getBounds()).id !== displayId) fitWorkArea();
        };
        win.on('move', movedDisplay);
        const normalBoundsEvents = ['restore', 'unmaximize', 'leave-full-screen'];
        for (const event of normalBoundsEvents) win.on(event, fitWorkArea);
        screen.on('display-metrics-changed', fitWorkArea);
        screen.on('display-removed', fitWorkArea);
        win.on('closed', () => {
          win.removeListener('move', movedDisplay);
          for (const event of normalBoundsEvents) win.removeListener(event, fitWorkArea);
          screen.removeListener('display-metrics-changed', fitWorkArea); screen.removeListener('display-removed', fitWorkArea);
          windows.delete(win); owned.delete(contentsId);
        });
        contents.on('will-attach-webview', event => event.preventDefault());
        contents.on('will-navigate', (event, url) => {
          if (pageAllowed(url, origin)) return;
          event.preventDefault();
          // Older retained pages also use attachment links. Preserve only
          // their explicit export routes without navigating to an API document.
          if (popupKind(url, origin) === 'download') contents.downloadURL(url);
        });
        contents.on('will-redirect', (event, url) => { if (!pageAllowed(url, origin)) event.preventDefault(); });
        contents.on('render-process-gone', () => void fail('FundsTrace 页面进程已停止，请重新打开应用。'));
        contents.setWindowOpenHandler(({ url }) => {
          const kind = popupKind(url, origin);
          if (kind === 'download') { contents.downloadURL(url); return { action: 'deny' }; }
          if (kind !== 'page') return { action: 'deny' };
          // Own the new window instead of substituting a WebContents into
          // Electron's guest-window factory (which has a separate ownership
          // contract in current Electron versions).
          const child = createWindow();
          void child.loadURL(url).catch(() => void fail('FundsTrace 分析页面无法打开，请重试。'));
          return { action: 'deny' };
        });
        win.once('ready-to-show', () => { if (!quitting && !win.isDestroyed()) win.show(); });
        return win;
      }
      // Reserve native menu height before the fixed startup composition is shown.
      Menu.setApplicationMenu(Menu.buildFromTemplate([
        ...(process.platform === 'darwin' ? [{ label: 'FundsTrace', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }] }] : []),
        { label: '文件', submenu: [{ role: 'quit' }] },
        { label: '编辑', enabled: false, submenu: [] },
        { label: '显示', enabled: false, submenu: [] },
        { label: '帮助', enabled: false, submenu: [] },
      ]));
      const primary = createWindow();
      const startup = createStartup(primary, { onFailure: () => void fail('循迹启动页面无法显示，请关闭后重新打开应用。') });
      await startup.loaded;
      if (quitting || primary.isDestroyed()) return;
      primary.show();
      preparation = (async () => {
        const dataDir = synthetic
          ? path.join(temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'funds-trace-desktop-')), 'data')
          : path.join(app.getPath('userData'), 'data');
        await fs.mkdir(dataDir, { recursive: true });
        if (quitting || primary.isDestroyed()) return;
        const config = launchConfig({ packaged: app.isPackaged, resourcesPath: process.resourcesPath, repoRoot: path.resolve(__dirname, '..'), python: process.env.FUNDS_TRACE_PYTHON, dataDir, synthetic });
        backend = startBackend(config, { onExit: () => void fail('FundsTrace 数据服务已停止。请关闭后重新打开应用；未完成的任务会在下次启动时识别。') });
      })();
      await preparation;
      if (!backend || quitting || primary.isDestroyed()) return;
      ({ origin, token } = await backend.ready);
      if (quitting || primary.isDestroyed()) return;
      const preferences = preferenceStore(app.getPath('userData'));
      ipcMain.on(CHANNEL, (event, operation, key, value) => {
        event.returnValue = preferenceRequest(preferences, event, origin, owned, operation, key, value);
      });
      const updatesEnabled = app.isPackaged && process.platform === 'win32' && !synthetic;
      updateService = updates.updateController({
        updater: updatesEnabled ? require('electron-updater').autoUpdater : undefined,
        enabled: updatesEnabled, version: app.getVersion(),
        notify: value => {
          if (value.phase === 'error' && installUpdate && shutdownComplete) {
            dialog.showErrorBox('FundsTrace 更新', '安装程序未能启动。请从 analytix.top 下载最新版覆盖安装，已有案件和设置会保留。');
            installUpdate = false; app.quit(); return;
          }
          for (const win of windows) if (!win.isDestroyed() && pageAllowed(win.webContents.getURL(), origin))
            win.webContents.send(updates.CHANNEL, value);
        },
        restart: async () => {
          const owner = [...windows].find(win => !win.isDestroyed());
          const options = { type: 'question', title: '重启更新 FundsTrace',
            message: '更新已下载并通过完整性校验。现在重启更新？',
            detail: '请先保存所有窗口中的画布，并等待导入、导出等操作完成。已有案件和设置会保留。',
            buttons: ['稍后', '重启更新'], defaultId: 0, cancelId: 0, noLink: true };
          const result = await (owner ? dialog.showMessageBox(owner, options) : dialog.showMessageBox(options));
          if (result.response !== 1 || quitting) return false;
          installUpdate = true;
          setImmediate(() => app.quit());
          return true;
        },
      });
      ipcMain.handle(updates.CHANNEL, (event, action) => {
        if (!updates.allowed(event, origin, owned) || quitting) return null;
        if (action === 'state') return updateService.snapshot();
        if (['check', 'download', 'install'].includes(action)) return updateService.action(action);
        return null;
      });
      const installMenu = () => Menu.setApplicationMenu(Menu.buildFromTemplate([
        ...(process.platform === 'darwin' ? [{ label: 'FundsTrace', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }] }] : []),
        { label: '文件', submenu: [{ label: '资金图谱', click: () => [...windows][0]?.loadURL(`${origin}/`) }, { label: '案件与导入', click: () => [...windows][0]?.loadURL(`${origin}/workspace/`) }, { type: 'separator' }, { role: 'quit' }] },
        { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
        { label: '显示', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }] },
        { label: '帮助', submenu: [{ label: '检查更新', enabled: updatesEnabled, click: () => void updateService.action('check') }] }
      ]));
      const workbench = waitForWorkbench(primary, origin);
      try {
        await Promise.all([
          primary.loadURL(`${origin}/`).then(() => workbench.navigated()),
          workbench.ready.then(ready => { if (!ready) throw new Error('Workbench initialization failed'); }),
        ]);
      }
      catch (error) { workbench.dispose(); throw error; }
      if (quitting || primary.isDestroyed()) return;
      startup.finish();
      if (!await startup.finished || quitting || primary.isDestroyed()) return;
      installMenu();
      if (updatesEnabled) {
        void updateService.action('check');
        updateTimer = setInterval(() => { if (!quitting) void updateService.action('check'); }, 6 * 60 * 60 * 1000);
        updateTimer.unref();
      }
    } catch (error) {
      await fail(error?.message === 'FundsTrace backend startup timed out'
        ? 'FundsTrace 启动超时。请关闭后重新打开应用；若仍无法启动，请检查可用磁盘空间，或重新安装当前版本。已有案件保留在用户数据目录。'
        : 'FundsTrace 无法完成启动。请关闭后重新打开应用；若仍失败，请重新安装当前版本并确认用户数据目录可写。重新安装不会主动删除已有案件。');
    }
  });
}
