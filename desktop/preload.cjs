'use strict';
const { contextBridge, ipcRenderer } = require('electron');
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
