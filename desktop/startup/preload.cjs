'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const channel = 'funds-trace:startup';
const listeners = new Set();
let ready = false;
let completed = false;
const appearanceListeners = new Set();
let appearance = { version: 1, theme: 'dark', accent: 'blue', translucency: false };
function acceptAppearance(value) {
  if (!value || Object.keys(value).sort().join() !== 'accent,theme,translucency,version' || value.version !== 1
      || !['dark', 'light'].includes(value.theme) || !['blue', 'teal', 'violet', 'rose', 'amber'].includes(value.accent)
      || typeof value.translucency !== 'boolean') return;
  appearance = Object.freeze({ version: 1, theme: value.theme, accent: value.accent, translucency: value.translucency });
  for (const callback of appearanceListeners) { try { callback(appearance); } catch { /* Keep delivery independent. */ } }
}
try { const arg = process.argv?.find(value => value.startsWith('--funds-appearance=')); if (arg) acceptAppearance(JSON.parse(arg.slice('--funds-appearance='.length))); } catch { /* Normal dark defaults. */ }
ipcRenderer.on('funds-trace:appearance', (_event, value) => acceptAppearance(value));
// Buffer an early Main notification until the local document has subscribed.
ipcRenderer.on(channel, (_event, value) => {
  if (value !== 'ready' || ready) return;
  ready = true;
  for (const callback of listeners) callback();
});
contextBridge.exposeInMainWorld('FundsTraceStartup', Object.freeze({
  onAppearance: callback => {
    if (typeof callback !== 'function') return () => {};
    appearanceListeners.add(callback); callback(appearance);
    return () => appearanceListeners.delete(callback);
  },
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
