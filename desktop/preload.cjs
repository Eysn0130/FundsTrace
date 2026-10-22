'use strict';
const { contextBridge, ipcRenderer } = require('electron');
// The browser version retains its own brand; the desktop host already owns it.
window.addEventListener('DOMContentLoaded', () => {
  document.documentElement.dataset.fundsDesktopShell = 'true';
}, { once: true });
// Readiness is a one-way signal from the initialized main workbench, with no data.
window.addEventListener('funds-trace:workbench', event => {
  if (event.detail === 'ready' || event.detail === 'failed') ipcRenderer.send('funds-trace:workbench', event.detail);
});
// Sandboxed preload exposes no IPC primitive, token, file path or case payload.
const channel = 'funds-trace:preferences';
const appearanceChannel = 'funds-trace:appearance';
const appearanceListeners = new Set();
let lastAppearance;
ipcRenderer.on(appearanceChannel, (_event, change) => {
  if (!change || change.key !== 'fund_appearance' || typeof change.value !== 'string' || change.value.length > 256) return;
  lastAppearance = Object.freeze({ key: 'fund_appearance', value: change.value });
  for (const callback of appearanceListeners) { try { callback(lastAppearance); } catch { /* One subscriber cannot interrupt delivery. */ } }
});
const preferences = Object.freeze({
  getItem: key => ipcRenderer.sendSync(channel, 'get', String(key)),
  setItem: (key, value) => {
    if (ipcRenderer.sendSync(channel, 'set', String(key), String(value)) !== true) throw new Error('偏好设置保存失败');
  },
  removeItem: key => {
    if (ipcRenderer.sendSync(channel, 'remove', String(key)) !== true) throw new Error('偏好设置保存失败');
  },
  subscribe: callback => {
    if (typeof callback !== 'function') return () => {};
    appearanceListeners.add(callback);
    if (lastAppearance) callback(lastAppearance);
    return () => appearanceListeners.delete(callback);
  },
});
contextBridge.exposeInMainWorld('FundsDesktopPreferences', preferences);
contextBridge.exposeInMainWorld('FundsPreferences', preferences);
const updateChannel = 'funds-trace:updates';
contextBridge.exposeInMainWorld('FundsDesktopUpdates', Object.freeze({
  state: () => ipcRenderer.invoke(updateChannel, 'state'),
  check: () => ipcRenderer.invoke(updateChannel, 'check'),
  download: () => ipcRenderer.invoke(updateChannel, 'download'),
  install: () => ipcRenderer.invoke(updateChannel, 'install'),
  subscribe: callback => {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, state) => callback(state);
    ipcRenderer.on(updateChannel, listener);
    return () => ipcRenderer.removeListener(updateChannel, listener);
  },
}));
