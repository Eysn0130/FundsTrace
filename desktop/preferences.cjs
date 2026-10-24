'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { pageAllowed } = require('./host-policy.cjs');
const CHANNEL = 'funds-trace:preferences';
const labels = new Set(['money', 'time', 'summary', 'currency']);
const keys = new Set(['fund_case', 'fund_edge_type', 'fund_show_ids', 'fund_show_photos', 'fund_edge_label', 'fund_edge_labels', 'fund_sidebar_width', 'fund_appearance', 'fund_minimap_expanded']);

function readAppearance(value) {
  if (typeof value !== 'string' || value.length > 256) return null;
  try {
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || Object.keys(parsed).sort().join() !== 'accent,theme,translucency,version'
        || parsed.version !== 1 || !['dark', 'light', 'system'].includes(parsed.theme)
        || !['blue', 'teal', 'violet', 'rose', 'amber'].includes(parsed.accent) || typeof parsed.translucency !== 'boolean') return null;
    return { version: 1, theme: parsed.theme, accent: parsed.accent, translucency: parsed.translucency };
  } catch { return null; }
}

function readWindowState(value) {
  try {
    const state = JSON.parse(value), bounds = state?.bounds;
    if (!bounds || typeof state.maximized !== 'boolean'
        || Object.keys(state).sort().join() !== 'bounds,maximized'
        || Object.keys(bounds).sort().join() !== 'height,width,x,y'
        || !['x', 'y', 'width', 'height'].every(key => Number.isSafeInteger(bounds[key]))
        || Math.abs(bounds.x) > 100000 || Math.abs(bounds.y) > 100000
        || bounds.width < 1 || bounds.width > 32768 || bounds.height < 1 || bounds.height > 32768) return null;
    return state;
  } catch { return null; }
}

function validPreference(key, value) {
  if (typeof value !== 'string' || value.length > 256) return false;
  switch (key) {
    case 'fund_appearance': return readAppearance(value) !== null;
    case 'fund_case': return /^[1-9]\d{0,18}$/.test(value);
    case 'fund_edge_type': return ['line', 'polyline', 'quadratic'].includes(value);
    case 'fund_show_ids': case 'fund_show_photos': case 'fund_minimap_expanded': return value === '0' || value === '1';
    case 'fund_sidebar_width': return /^(0|[1-9]\d{0,2})$/.test(value) && Number(value) <= 760;
    // Main owns normal window bounds. This key is never exposed by preference IPC.
    case 'fund_window_state': return readWindowState(value) !== null;
    case 'fund_edge_label': return labels.has(value) || value === 'all';
    case 'fund_edge_labels':
      try { const items = JSON.parse(value); return Array.isArray(items) && items.length >= 1 && items.length <= 4 && items.every(item => labels.has(item)) && new Set(items).size === items.length; }
      catch { return false; }
    default: return false;
  }
}

// Only presentation preferences enter this file. Sessions, tokens and case facts
// remain in their existing owners; renderer origins/ports may change each launch.
function preferenceStore(directory) {
  const file = path.join(directory, 'preferences.json');
  let values = {};
  try {
    if (fs.statSync(file).size <= 16384) {
      const saved = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (saved && typeof saved === 'object' && !Array.isArray(saved))
        values = Object.fromEntries(Object.entries(saved).filter(([key, value]) => validPreference(key, value)));
    }
  } catch { /* Missing/invalid preferences use the normal UI defaults. */ }
  function save(next) {
    const temporary = path.join(directory, `preferences-${randomUUID()}.tmp`);
    try {
      fs.mkdirSync(directory, { recursive: true });
      fs.writeFileSync(temporary, JSON.stringify(next) + '\n', { mode: 0o600, flag: 'wx' });
      fs.renameSync(temporary, file);
      values = next;
      return true;
    } catch { return false; }
    finally { try { fs.unlinkSync(temporary); } catch { /* Already renamed. */ } }
  }
  return {
    getItem(key) { return Object.hasOwn(values, key) ? values[key] : null; },
    setItem(key, value) { return validPreference(key, value) && save({ ...values, [key]: value }); },
    removeItem(key) {
      // An unknown key must not grant a general-purpose persistence channel.
      if (!keys.has(key)) return false;
      const next = { ...values }; delete next[key]; return save(next);
    },
  };
}

function preferenceRequest(store, event, origin, owned, operation, key, value) {
  const sender = event.sender, frame = event.senderFrame;
  if (!sender || sender.isDestroyed() || !owned.has(sender.id) || !frame || frame !== sender.mainFrame || !pageAllowed(frame.url, origin)) return null;
  if (typeof key !== 'string' || !keys.has(key)) return null;
  if (operation === 'get') return store.getItem(key);
  if (operation === 'set') return validPreference(key, value) && store.setItem(key, value);
  if (operation === 'remove') return store.removeItem(key);
  return null;
}

module.exports = { CHANNEL, validPreference, preferenceStore, preferenceRequest, readWindowState, readAppearance };
