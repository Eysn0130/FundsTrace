'use strict';
const { contextBridge, ipcRenderer } = require('electron');
// Readiness is a one-way signal from the initialized main workbench, with no data.
window.addEventListener('funds-trace:workbench', event => {
  if (event.detail === 'ready' || event.detail === 'failed') ipcRenderer.send('funds-trace:workbench', event.detail);
});
// Sandboxed preload exposes no IPC primitive, token, file path or case payload.
const channel = 'funds-trace:preferences';
contextBridge.exposeInMainWorld('FundsDesktopPreferences', Object.freeze({
  getItem: key => ipcRenderer.sendSync(channel, 'get', String(key)),
  setItem: (key, value) => {
    if (ipcRenderer.sendSync(channel, 'set', String(key), String(value)) !== true) throw new Error('偏好设置保存失败');
  },
  removeItem: key => {
    if (ipcRenderer.sendSync(channel, 'remove', String(key)) !== true) throw new Error('偏好设置保存失败');
  },
}));
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
