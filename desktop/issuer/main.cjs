'use strict';
const { app, BrowserWindow, Menu, dialog, session, ipcMain, clipboard, screen } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { createIssuer, IssuerError } = require('./core.cjs');
const { trustedSender, localResource } = require('./policy.cjs');
const appIcon = path.join(__dirname, '..', 'assets', process.platform === 'win32' ? 'icon.ico' : 'icon.png');

app.setName('FundsTrace Issuer');
app.setAppUserModelId('com.eysn.fundstrace.issuer');
app.setPath('userData', path.join(app.getPath('appData'), 'FundsTrace Issuer'));
const issuer = createIssuer();
const page = pathToFileURL(path.join(__dirname, 'index.html')).href;
const resources = new Set(['index.html', 'style.css', 'renderer.js', '../assets/icon.png'].map(name => pathToFileURL(path.join(__dirname, name)).href));
let win, busy = false;
const cancelled = () => ({ ok: false, cancelled: true });
function handle(name, operation) {
  ipcMain.handle(`issuer:${name}`, async (event, input) => {
    if (!trustedSender(event, win?.webContents, page)) return { ok: false, error: '此操作不可用。' };
    if (busy) return { ok: false, error: '请等待当前操作完成。' };
    busy = true;
    try { return await operation(input); }
    catch (error) { return { ok: false, error: error instanceof IssuerError ? error.message : '操作未完成，请重试。' }; }
    finally { busy = false; }
  });
}
async function savePath(title, defaultPath) {
  const result = await dialog.showSaveDialog(win, { title, defaultPath, filters: [{ name: 'PEM 密钥', extensions: ['pem'] }], properties: ['showOverwriteConfirmation'] });
  return result.canceled ? null : result.filePath;
}
handle('getState', () => ({ ok: true, ...issuer.state() }));
handle('createKeys', async input => {
  if (typeof input !== 'string' || [...input].length < 12 || input.length > 4096) throw new IssuerError('password');
  const privatePath = await savePath('保存新的加密签发私钥', 'fundstrace-issuer-private.pem');
  if (!privatePath) return cancelled();
  const publicPath = await savePath('保存验证公钥', 'fundstrace-license-public.pem');
  if (!publicPath) return cancelled();
  return { ok: true, ...await issuer.create(input, privatePath, publicPath) };
});
handle('loadKey', async input => {
  if (typeof input !== 'string') throw new IssuerError('key');
  const result = await dialog.showOpenDialog(win, { title: '加载签发私钥', properties: ['openFile'], filters: [{ name: 'PKCS8 私钥', extensions: ['pem'] }] });
  if (result.canceled || result.filePaths.length !== 1) return cancelled();
  return { ok: true, ...await issuer.load(result.filePaths[0], input) };
});
handle('exportPublic', async () => {
  if (!issuer.state().loaded) throw new IssuerError('missing');
  const file = await savePath('导出验证公钥', 'fundstrace-license-public.pem');
  if (!file) return cancelled();
  return { ok: true, ...await issuer.exportPublic(file) };
});
handle('issue', input => ({ ok: true, ...issuer.issue(input?.machine, input?.expiry) }));
handle('clear', () => ({ ok: true, ...issuer.clear() }));
handle('copyCode', input => { clipboard.writeText(issuer.copyValue(input)); return { ok: true }; });
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.show(); win.focus(); } });
  app.on('before-quit', () => issuer.clear());
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(async () => {
    app.dock?.setIcon(appIcon);
    const local = session.fromPartition('fundstrace-issuer', { cache: false });
    local.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    local.setPermissionCheckHandler(() => false);
    local.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !localResource(details.url, resources) }));
    local.on('will-download', event => event.preventDefault());
    const area = screen.getPrimaryDisplay().workArea;
    win = new BrowserWindow({ title: 'FundsTrace Issuer', icon: appIcon, width: Math.min(980, area.width), height: Math.min(760, area.height), minWidth: Math.min(680, area.width), minHeight: Math.min(540, area.height), show: false,
      webPreferences: { preload: path.join(__dirname, 'preload.cjs'), session: local, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, spellcheck: false } });
    win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    for (const name of ['will-navigate', 'will-redirect', 'will-attach-webview']) win.webContents.on(name, event => event.preventDefault());
    win.webContents.on('render-process-gone', () => { issuer.clear(); app.quit(); });
    win.on('closed', () => { issuer.clear(); win = null; });
    Menu.setApplicationMenu(Menu.buildFromTemplate([{ label: '编辑', submenu: [{ role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] }, { label: '文件', submenu: [{ role: 'quit' }] }]));
    win.once('ready-to-show', () => win.show());
    await win.loadURL(page);
  }).catch(() => { issuer.clear(); dialog.showErrorBox('FundsTrace Issuer', '签发工具无法启动，请检查应用资源。'); app.quit(); });
}
