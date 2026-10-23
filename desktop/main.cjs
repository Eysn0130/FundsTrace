'use strict';

const { app, Menu, dialog, session, screen, ipcMain, nativeTheme } = require('electron');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { launchConfig, HEADER, pageAllowed, popupKind, requestAllowed, downloadAllowed, windowBounds } = require('./host-policy.cjs');
const { startBackend } = require('./backend.cjs');
const { CHANNEL, preferenceStore, preferenceRequest, readWindowState } = require('./preferences.cjs');
const updates = require('./updates.cjs');
const { createStartup, waitForWorkbench } = require('./startup.cjs');
const { createWindowShell, HEIGHT: TITLEBAR_HEIGHT } = require('./window-shell.cjs');
const appearance = require('./appearance.cjs');
const appIcon = path.join(__dirname, 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png');

app.setName('FundsTrace');
app.setAppUserModelId('com.eysn.fundstrace');
const synthetic = process.argv.includes('--synthetic');
let backend, preparation, temporaryRoot, quitting = false, shutdownComplete = false, fatalShown = false;
let updateService, installUpdate = false, updateTimer;
const owned = new Set();
const windows = new Set();
const shells = new Map();

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
      // A single Main-owned store prevents geometry and renderer preferences
      // overwriting one another. Disposable source mode never changes user settings.
      const transientPreferences = new Map();
      const preferences = synthetic ? {
        getItem: key => transientPreferences.get(key) ?? null,
        setItem: (key, value) => { transientPreferences.set(key, value); return true; },
        removeItem: key => transientPreferences.delete(key),
      } : preferenceStore(app.getPath('userData'));
      const savedWindow = readWindowState(preferences.getItem('fund_window_state'));
      let origin, token;
      let startup;
      const savedAppearance = () => appearance.readAppearance(preferences.getItem('fund_appearance')) || appearance.DEFAULT;
      const resolvedAppearance = () => appearance.resolveAppearance(savedAppearance(), nativeTheme?.shouldUseDarkColors ?? true);
      function sendBusinessAppearance(contents) {
        if (!quitting && contents && !contents.isDestroyed() && pageAllowed(contents.getURL(), origin))
          contents.send(appearance.CHANNEL, { key: 'fund_appearance', value: JSON.stringify(savedAppearance()) });
      }
      function applyAppearance() {
        const snapshot = resolvedAppearance();
        for (const host of shells.values()) { host.setAppearance(snapshot); sendBusinessAppearance(host.contents); }
        startup?.setAppearance(snapshot);
      }
      nativeTheme?.on('updated', applyAppearance);
      app.once('will-quit', () => nativeTheme?.removeListener('updated', applyAppearance));
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
          const owner = [...windows].find(win => shells.get(win)?.contents === webContents) || null;
          const options = { type: state === 'cancelled' ? 'info' : 'warning', title: 'FundsTrace 下载', message: state === 'cancelled' ? '下载已取消，文件未保存。' : '下载未完成，请重新导出并选择保存位置。' };
          void (owner ? dialog.showMessageBox(owner, options) : dialog.showMessageBox(options));
        });
        // Do not setSavePath: Electron presents the normal Save dialog.
      });
      let menuReady = false;
      const windowContents = win => shells.get(win)?.contents;
      const menuWindow = fallback => process.platform === 'darwin'
        ? [...windows].find(win => !win.isDestroyed() && win.isFocused()) || fallback : fallback;
      const menuTemplate = win => [
        ...(process.platform === 'darwin' ? [{ label: 'FundsTrace', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }] }] : []),
        { label: '文件', submenu: [{ label: '资金图谱', click: () => void navigateWorkspace(menuWindow(win), 'home') }, { label: '案件与导入', click: () => void navigateWorkspace(menuWindow(win), 'cases') }, { type: 'separator' }, { role: 'quit' }] },
        { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
        { label: '显示', submenu: [{ label: '重新加载', accelerator: 'CmdOrCtrl+R', click: () => windowContents(menuWindow(win))?.reload() },
          { label: '实际大小', accelerator: 'CmdOrCtrl+0', click: () => windowContents(menuWindow(win))?.setZoomLevel(0) },
          { label: '放大', accelerator: 'CmdOrCtrl+Plus', click: () => zoom(menuWindow(win), .5) },
          { label: '缩小', accelerator: 'CmdOrCtrl+-', click: () => zoom(menuWindow(win), -.5) },
          { label: '全屏', accelerator: process.platform === 'darwin' ? 'Control+Command+F' : 'F11', click: () => { const current = menuWindow(win); if (!current.isDestroyed()) current.setFullScreen(!current.isFullScreen()); } }] },
        { label: '帮助', submenu: [{ label: '检查更新', enabled: app.isPackaged && process.platform === 'win32' && !synthetic, click: () => void updateService?.action('check') }] },
      ];
      function zoom(win, amount) {
        const contents = windowContents(win);
        if (contents && !contents.isDestroyed()) contents.setZoomLevel(Math.max(-7.5, Math.min(8.5, contents.getZoomLevel() + amount)));
      }
      async function navigate(win, url) {
        const host = shells.get(win);
        if (!host || win.isDestroyed()) return;
        try {
          await Promise.all([host.loaded, host.contents.loadURL(url)]);
          if (!quitting && !win.isDestroyed()) { win.show(); host.contents.focus(); }
        } catch { if (!quitting && !win.isDestroyed()) void fail('FundsTrace 页面无法打开，请重试。'); }
      }
      async function navigateWorkspace(win, page) {
        const contents = windowContents(win);
        if (!contents || contents.isDestroyed()) return;
        const command = page === 'cases'
          ? "(() => { const nav = window.FundsAppNavigation; if (typeof nav?.show !== 'function') return false; nav.show('cases'); return true; })()"
          : "(() => { const nav = window.FundsAppNavigation; if (typeof nav?.show !== 'function') return false; nav.show('home'); return true; })()";
        try { if (await contents.executeJavaScript(command)) return; }
        catch { /* Non-workspace pages use the approved main-workspace URL. */ }
        await navigate(win, page === 'cases' ? `${origin}/?page=cases` : `${origin}/`);
      }
      function openMenu(win) {
        const host = shells.get(win);
        if (!menuReady || quitting || !host || win.isDestroyed()) return;
        host.contents.focus();
        Menu.buildFromTemplate(menuTemplate(win)).popup({ window: win, x: 8, y: TITLEBAR_HEIGHT });
      }
      function createWindow(remember = false) {
        const owner = [...windows][0];
        const savedBounds = remember ? savedWindow?.bounds : null;
        const display = savedBounds ? screen.getDisplayMatching(savedBounds)
          : owner ? screen.getDisplayMatching(owner.getBounds()) : screen.getPrimaryDisplay();
        const initialBounds = windowBounds(display.workArea, savedBounds);
        const host = createWindowShell({
          windowOptions: { title: '循迹 FundsTrace', backgroundColor: '#10171f', icon: appIcon, ...initialBounds, show: false },
          webPreferences: { preload: path.join(__dirname, 'preload.cjs'), session: localSession, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, spellcheck: false },
          onFailure: () => void fail('FundsTrace 页面进程已停止，请重新打开应用。'), appearance: resolvedAppearance(),
        });
        const { win, contents } = host;
        const captionContents = win.webContents;
        shells.set(win, host);
        contents.on('did-finish-load', () => sendBusinessAppearance(contents));
        const contentsId = contents.id;
        windows.add(win); owned.add(contentsId);
        let displayId = display.id, fitting = false;
        let normalBounds = savedBounds || Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, initialBounds[key]]));
        let maximized = remember && savedWindow?.maximized === true;
        let saveTimer, savedWindowValue = preferences.getItem('fund_window_state');
        const saveWindow = () => {
          clearTimeout(saveTimer);
          const value = JSON.stringify({ bounds: normalBounds, maximized });
          if (remember && value !== savedWindowValue && preferences.setItem('fund_window_state', value)) savedWindowValue = value;
        };
        const scheduleSave = () => {
          if (!remember) return;
          clearTimeout(saveTimer); saveTimer = setTimeout(saveWindow, 250); saveTimer.unref();
        };
        let userResize = false, userMove = false;
        // These events identify a user gesture. Automatic work-area fitting,
        // DPI changes and maximized frames must not replace the preferred size.
        const userGestureAllowed = () => remember && !fitting && !win.isDestroyed() && !win.isMaximized() && !win.isFullScreen();
        const beginResize = () => { if (userGestureAllowed()) userResize = true; };
        const beginMove = () => { if (userGestureAllowed()) userMove = true; };
        const rememberBounds = () => {
          if ((userResize || userMove) && userGestureAllowed()) {
            // will-resize/move carry proposed native outer bounds on Windows.
            // Read the completed logical rectangle so hidden frame insets are
            // never accumulated on every restart. Moving retains preferred size.
            const current = win.getBounds();
            const next = { x: current.x, y: current.y,
              width: userResize ? current.width : normalBounds.width,
              height: userResize ? current.height : normalBounds.height };
            if (readWindowState(JSON.stringify({ bounds: next, maximized: false }))) normalBounds = next;
            scheduleSave();
          }
          userResize = userMove = false;
        };
        const rememberMaximized = () => { maximized = true; saveWindow(); };
        const rememberRestored = () => { maximized = false; saveWindow(); };
        const closeWindow = () => { rememberBounds(); saveWindow(); };
        win.on('will-move', beginMove); win.on('will-resize', beginResize);
        win.on('resized', rememberBounds); win.on('moved', rememberBounds);
        win.on('maximize', rememberMaximized); win.on('unmaximize', rememberRestored);
        win.on('close', closeWindow);
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
          clearTimeout(saveTimer);
          win.removeListener('will-move', beginMove); win.removeListener('will-resize', beginResize);
          win.removeListener('resized', rememberBounds); win.removeListener('moved', rememberBounds);
          win.removeListener('maximize', rememberMaximized); win.removeListener('unmaximize', rememberRestored);
          win.removeListener('close', closeWindow);
          win.removeListener('move', movedDisplay);
          for (const event of normalBoundsEvents) win.removeListener(event, fitWorkArea);
          screen.removeListener('display-metrics-changed', fitWorkArea); screen.removeListener('display-removed', fitWorkArea);
          windows.delete(win); owned.delete(contentsId); shells.delete(win);
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
        // Windows has no hidden native menu strip: keep accelerators in the
        // business renderer while the caption remains at an independent 100%.
        let altMenuPending = false;
        const shortcuts = (event, input) => {
          if (!menuReady || quitting) return;
          const key = input.key.toLowerCase();
          if (key === 'alt') {
            if (input.type === 'keyDown' && !input.control && !input.meta && !input.shift) altMenuPending = true;
            if (input.type === 'keyUp') {
              if (altMenuPending) { event.preventDefault(); openMenu(win); }
              altMenuPending = false;
            }
            return;
          }
          if (input.type !== 'keyDown') return;
          altMenuPending = false;
          if (key === 'f10' && !input.shift && !input.alt && !input.control && !input.meta) {
            event.preventDefault(); openMenu(win); return;
          }
          if (input.alt || input.meta) return;
          let action;
          if (key === 'f11') action = () => win.setFullScreen(!win.isFullScreen());
          else if (key === 'f5' || input.control && key === 'r') action = () => contents.reload();
          else if (input.control && key === '0') action = () => contents.setZoomLevel(0);
          else if (input.control && ['+', '=', 'add'].includes(key)) action = () => zoom(win, .5);
          else if (input.control && ['-', 'subtract'].includes(key)) action = () => zoom(win, -.5);
          if (action) { event.preventDefault(); action(); }
        };
        if (process.platform !== 'darwin') {
          const cancelAltMenu = () => { altMenuPending = false; };
          contents.on('before-input-event', shortcuts);
          captionContents.on('before-input-event', shortcuts);
          win.on('blur', cancelAltMenu);
          win.once('closed', () => {
            win.removeListener('blur', cancelAltMenu);
            contents.removeListener('before-input-event', shortcuts);
            captionContents.removeListener('before-input-event', shortcuts);
          });
        }
        contents.setWindowOpenHandler(({ url }) => {
          const kind = popupKind(url, origin);
          if (kind === 'download') { contents.downloadURL(url); return { action: 'deny' }; }
          if (kind !== 'page') return { action: 'deny' };
          // Own the new window instead of substituting a WebContents into
          // Electron's guest-window factory (which has a separate ownership
          // contract in current Electron versions).
          const child = createWindow();
          void navigate(child, url);
          return { action: 'deny' };
        });
        return win;
      }
      // The caption owns the Windows/Linux menu entry from the first frame.
      // macOS keeps its system application menu without adding an in-window strip.
      Menu.setApplicationMenu(process.platform === 'darwin' ? Menu.buildFromTemplate([
        ...(process.platform === 'darwin' ? [{ label: 'FundsTrace', submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'quit' }] }] : []),
        { label: '文件', submenu: [{ role: 'quit' }] },
        { label: '编辑', enabled: false, submenu: [] },
        { label: '显示', enabled: false, submenu: [] },
        { label: '帮助', enabled: false, submenu: [] },
      ]) : null);
      const primary = createWindow(true);
      const primaryContents = windowContents(primary);
      startup = createStartup(primary, { contentInset: TITLEBAR_HEIGHT, focusContents: primaryContents, appearance: resolvedAppearance(),
        onFailure: () => void fail('循迹启动页面无法显示，请关闭后重新打开应用。') });
      await Promise.all([shells.get(primary).loaded, startup.loaded]);
      if (quitting || primary.isDestroyed()) return;
      // maximize() also shows a hidden window; wait for both local surfaces first.
      if (savedWindow?.maximized) primary.maximize();
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
      ipcMain.on(CHANNEL, (event, operation, key, value) => {
        const result = preferenceRequest(preferences, event, origin, owned, operation, key, value);
        event.returnValue = result;
        if (result === true && key === 'fund_appearance' && ['set', 'remove'].includes(operation)) applyAppearance();
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
          for (const win of windows) {
            const contents = windowContents(win);
            if (!win.isDestroyed() && contents && pageAllowed(contents.getURL(), origin)) contents.send(updates.CHANNEL, value);
          }
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
      const workbench = waitForWorkbench(primary, origin, { ipcMain, contents: primaryContents });
      try {
        await Promise.all([
          primaryContents.loadURL(`${origin}/`).then(() => workbench.navigated()),
          workbench.ready.then(ready => { if (!ready) throw new Error('Workbench initialization failed'); }),
        ]);
      }
      catch (error) { workbench.dispose(); throw error; }
      if (quitting || primary.isDestroyed()) return;
      startup.finish();
      if (!await startup.finished || quitting || primary.isDestroyed()) return;
      menuReady = true;
      if (process.platform === 'darwin') Menu.setApplicationMenu(Menu.buildFromTemplate(menuTemplate(primary)));
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
