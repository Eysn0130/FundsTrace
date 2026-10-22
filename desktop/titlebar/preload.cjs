'use strict';
const { contextBridge, ipcRenderer } = require('electron');
const listeners = new Set();
let snapshot = { version: 1, theme: 'dark', accent: 'blue', translucency: false };
function accept(value) {
  if (!value || Object.keys(value).sort().join() !== 'accent,theme,translucency,version' || value.version !== 1
      || !['dark', 'light'].includes(value.theme) || !['blue', 'teal', 'violet', 'rose', 'amber'].includes(value.accent)
      || typeof value.translucency !== 'boolean') return;
  snapshot = Object.freeze({ version: 1, theme: value.theme, accent: value.accent, translucency: value.translucency });
  for (const callback of listeners) { try { callback(snapshot); } catch { /* Keep delivery independent. */ } }
}
try { const arg = process.argv?.find(value => value.startsWith('--funds-appearance=')); if (arg) accept(JSON.parse(arg.slice('--funds-appearance='.length))); } catch { /* Normal dark defaults. */ }

document.addEventListener('DOMContentLoaded', () => {
  document.documentElement.dataset.platform = process.platform;
}, { once: true });

// Main-to-caption only. No menu command, preferences, backend or case bridge.
ipcRenderer.on('funds-trace:appearance', (_event, value) => accept(value));

contextBridge.exposeInMainWorld('FundsTraceTitlebar', Object.freeze({
  onAppearance: callback => {
    if (typeof callback !== 'function') return () => {};
    listeners.add(callback);
    callback(snapshot);
    return () => listeners.delete(callback);
  },
}));
