'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const listeners = new Set();
let menuEnabled = false;

document.addEventListener('DOMContentLoaded', () => {
  document.documentElement.dataset.platform = process.platform;
}, { once: true });

// Keep the last boolean so an early Main notification is not lost.
ipcRenderer.on('funds-trace:titlebar-menu-enabled', (_event, enabled) => {
  if (typeof enabled !== 'boolean') return;
  menuEnabled = enabled;
  for (const callback of listeners) callback(menuEnabled);
});

contextBridge.exposeInMainWorld('FundsTraceTitlebar', Object.freeze({
  openMenu: () => ipcRenderer.send('funds-trace:titlebar', 'open-menu'),
  onMenuEnabled: callback => {
    if (typeof callback !== 'function') return () => {};
    listeners.add(callback);
    callback(menuEnabled);
    return () => listeners.delete(callback);
  },
}));
