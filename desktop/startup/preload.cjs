'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const channel = 'funds-trace:startup';
const listeners = new Set();
let ready = false;
let completed = false;
// Buffer an early Main notification until the local document has subscribed.
ipcRenderer.on(channel, (_event, value) => {
  if (value !== 'ready' || ready) return;
  ready = true;
  for (const callback of listeners) callback();
});
contextBridge.exposeInMainWorld('FundsTraceStartup', Object.freeze({
  onReady: callback => {
    if (typeof callback !== 'function') return () => {};
    listeners.add(callback);
    if (ready) callback();
    return () => listeners.delete(callback);
  },
  complete: () => {
    if (!ready || completed) return;
    completed = true;
    ipcRenderer.send(channel, 'complete');
  },
}));
