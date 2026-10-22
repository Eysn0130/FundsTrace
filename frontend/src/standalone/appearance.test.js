import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';

const source = readFileSync(resolve(process.cwd(), '../static/appearance.js'), 'utf8');
const defaults = { version: 1, theme: 'dark', accent: 'blue', translucency: false };
const setting = overrides => ({ ...defaults, ...overrides });
let channels, media, mediaListeners;
function load(target = window) { new Function('window', source)(target); return target.FundsAppearance; }
beforeEach(() => {
  document.body.innerHTML = ''; window.localStorage.clear(); delete window.FundsAppearance; delete window.FundsPreferences; delete window.FundsDesktopPreferences; delete window.FundsDesktopUpdates;
  channels = []; mediaListeners = [];
  media = { matches: false, addEventListener: vi.fn((_name, listener) => mediaListeners.push(listener)), removeEventListener: vi.fn() };
  vi.stubGlobal('matchMedia', vi.fn(() => media));
  vi.stubGlobal('BroadcastChannel', class { constructor() { channels.push(this); this.postMessage = vi.fn(); this.close = vi.fn(); } });
});
afterEach(() => {
  window.dispatchEvent(new Event('pagehide')); document.body.innerHTML = ''; delete window.FundsAppearance; delete window.FundsPreferences; delete window.FundsDesktopPreferences; delete window.FundsDesktopUpdates;
  vi.unstubAllGlobals(); vi.restoreAllMocks();
});

describe('application appearance preferences', () => {
  it('uses dark defaults and rejects invalid versions, enums, flags and extra keys', () => {
    window.localStorage.setItem('fund_appearance', JSON.stringify({ ...defaults, caseId: 'not-an-appearance-field' }));
    const api = load(); expect(api.get()).toEqual(defaults); expect(document.documentElement.dataset.ftTheme).toBe('dark');
    for (const invalid of [setting({ version: 2 }), setting({ theme: 'unknown' }), setting({ accent: '#fff' }), setting({ translucency: 'true' }), { ...defaults, caseId: '1' }]) expect(() => api.apply(invalid)).toThrow(/外观设置格式无效/);
    const returned = api.get(); returned.theme = 'light'; expect(api.get().theme).toBe('dark');
  });

  it('previews only this document and its iframe, then restores on Escape without persisting or broadcasting', () => {
    const stored = setting({ theme: 'light', accent: 'teal', translucency: true }); window.localStorage.setItem('fund_appearance', JSON.stringify(stored));
    const frame = document.createElement('iframe'); document.body.append(frame); const post = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {});
    const api = load(); api.openSettings();
    fireEvent.click(screen.getByLabelText('深色')); fireEvent.click(screen.getByLabelText('玫红'));
    expect(document.documentElement.dataset.ftTheme).toBe('dark'); expect(document.documentElement.dataset.ftAccent).toBe('rose'); expect(api.get()).toEqual(stored);
    expect(channels[0].postMessage).not.toHaveBeenCalled();
    expect(post).toHaveBeenLastCalledWith({ channel: 'fundstrace:appearance', version: 1, type: 'state', preference: { ...stored, theme: 'dark', accent: 'rose' }, preview: true }, window.location.origin);
    fireEvent(screen.getByRole('dialog', { name: '设置' }), new Event('cancel', { cancelable: true }));
    expect(document.documentElement.dataset.ftTheme).toBe('light'); expect(document.documentElement.dataset.ftAccent).toBe('teal');
    expect(JSON.parse(window.localStorage.getItem('fund_appearance'))).toEqual(stored); expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('saves one exact appearance key and only then notifies other documents', () => {
    const api = load(); api.openSettings(); fireEvent.click(screen.getByLabelText('浅色')); fireEvent.click(screen.getByLabelText('琥珀')); fireEvent.click(screen.getByRole('switch', { name: '半透明材质' }));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    const wanted = setting({ theme: 'light', accent: 'amber', translucency: true });
    expect(JSON.parse(window.localStorage.getItem('fund_appearance'))).toEqual(wanted); expect(api.get()).toEqual(wanted);
    expect(channels[0].postMessage).toHaveBeenCalledExactlyOnceWith({ channel: 'fundstrace:appearance', version: 1, type: 'saved', preference: wanted });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('restores defaults as a preview until explicitly saved', () => {
    const stored = setting({ theme: 'light', accent: 'violet', translucency: true }); window.localStorage.setItem('fund_appearance', JSON.stringify(stored));
    const api = load(); api.openSettings(); fireEvent.click(screen.getByRole('button', { name: '恢复默认' }));
    expect(document.documentElement.dataset.ftTheme).toBe('dark'); expect(api.get()).toEqual(stored);
    fireEvent.click(screen.getByRole('button', { name: '取消' })); expect(document.documentElement.dataset.ftTheme).toBe('light'); expect(api.get()).toEqual(stored);
  });

  it('keeps a failed save as an editable preview and permits cancellation', () => {
    const setItem = vi.fn(() => { throw new Error('synthetic persistence failure'); });
    window.FundsPreferences = { getItem: () => null, setItem, subscribe: () => () => {} };
    const api = load(); api.openSettings(); fireEvent.click(screen.getByLabelText('浅色')); fireEvent.click(screen.getByRole('button', { name: '保存' }));
    expect(screen.getByText(/外观设置未能保存/)).toBeTruthy(); expect(screen.getByRole('dialog')).toBeTruthy(); expect(api.get()).toEqual(defaults);
    expect(document.documentElement.dataset.ftTheme).toBe('light'); expect(channels[0].postMessage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '取消' })); expect(document.documentElement.dataset.ftTheme).toBe('dark');
  });

  it('does not report a failed save when persistence succeeds but an optional broadcast peer is unavailable', () => {
    const api = load(); channels[0].postMessage.mockImplementation(() => { throw new Error('synthetic closed channel'); });
    expect(() => api.save(setting({ theme: 'light' }))).not.toThrow();
    expect(api.get().theme).toBe('light'); expect(JSON.parse(window.localStorage.getItem('fund_appearance')).theme).toBe('light');
  });

  it('uses Main preference aliases and receives only validated appearance notifications', () => {
    let listener; const setItem = vi.fn(); window.FundsDesktopPreferences = { getItem: () => JSON.stringify(defaults), setItem, subscribe: callback => { listener = callback; return () => {}; } };
    const api = load(); api.save(setting({ accent: 'teal' })); expect(setItem).toHaveBeenCalledExactlyOnceWith('fund_appearance', JSON.stringify(setting({ accent: 'teal' })));
    listener({ key: 'fund_case', value: '2' }); expect(api.get().accent).toBe('teal');
    listener({ key: 'fund_appearance', value: JSON.stringify(setting({ theme: 'light', accent: 'rose' })) }); expect(document.documentElement.dataset.ftTheme).toBe('light');
    listener({ key: 'fund_appearance', value: JSON.stringify({ ...defaults, token: 'rejected' }) }); expect(api.get().accent).toBe('rose');
  });

  it('keeps the current preview when a peer saves, then cancels to the latest committed preference', () => {
    const api = load(); api.preview(setting({ accent: 'rose' }));
    channels[0].onmessage({ data: { channel: 'fundstrace:appearance', version: 1, type: 'saved', preference: setting({ theme: 'light', accent: 'teal' }) } });
    expect(document.documentElement.dataset.ftAccent).toBe('rose'); expect(api.get().accent).toBe('teal');
    api.cancelPreview(); expect(document.documentElement.dataset.ftTheme).toBe('light'); expect(document.documentElement.dataset.ftAccent).toBe('teal');
  });

  it('resolves system changes without overwriting the saved system preference', () => {
    const api = load(); api.save(setting({ theme: 'system' })); expect(document.documentElement.dataset.ftTheme).toBe('dark');
    media.matches = true; mediaListeners.forEach(listener => listener());
    expect(document.documentElement.dataset.ftTheme).toBe('light'); expect(api.get().theme).toBe('system');
    expect(JSON.parse(window.localStorage.getItem('fund_appearance')).theme).toBe('system'); expect(channels[0].postMessage).toHaveBeenCalledOnce();
  });

  it('accepts parent state only from the exact parent and origin in an iframe', () => {
    const listeners = new Map(), parent = { postMessage: vi.fn() }, childDocument = document.implementation.createHTMLDocument('synthetic child');
    const child = { document: childDocument, parent, location: { origin: window.location.origin }, localStorage: { getItem: () => null }, CustomEvent: window.CustomEvent, dispatchEvent: vi.fn(), addEventListener: (name, listener) => listeners.set(name, listener), removeEventListener: vi.fn(), matchMedia: () => media };
    const api = load(child); const state = { channel: 'fundstrace:appearance', version: 1, type: 'state', preference: setting({ theme: 'light', accent: 'teal' }), preview: true };
    listeners.get('message')({ origin: 'https://unrelated.example', source: parent, data: state }); listeners.get('message')({ origin: window.location.origin, source: {}, data: state });
    expect(childDocument.documentElement.dataset.ftTheme).toBe('dark');
    listeners.get('message')({ origin: window.location.origin, source: parent, data: state }); expect(childDocument.documentElement.dataset.ftTheme).toBe('light'); expect(api.get()).toEqual(defaults);
    listeners.get('message')({ origin: window.location.origin, source: parent, data: { ...state, preview: false } }); expect(api.get().accent).toBe('teal');
    expect(() => api.save(defaults)).toThrow(/主窗口设置保存/);
  });

  it('responds to ready messages only from an iframe owned by this document', () => {
    const frame = document.createElement('iframe'); document.body.append(frame); const post = vi.spyOn(frame.contentWindow, 'postMessage').mockImplementation(() => {}); load(); post.mockClear();
    const data = { channel: 'fundstrace:appearance', version: 1, type: 'ready' };
    window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, source: window, data })); expect(post).not.toHaveBeenCalled();
    window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, source: frame.contentWindow, data })); expect(post).toHaveBeenCalledOnce();
  });

  it('uses real workspace navigation and cancels unsaved appearance before leaving settings', () => {
    const navigation = document.createElement('button'); navigation.dataset.navPage = 'cases'; const clicked = vi.fn(); navigation.onclick = clicked; document.body.append(navigation);
    const api = load(); api.openSettings(); fireEvent.click(screen.getByLabelText('浅色')); fireEvent.click(screen.getByRole('tab', { name: '工作区' }));
    const panel = screen.getByRole('tabpanel', { name: '工作区' }); fireEvent.click(within(panel).getAllByRole('button', { name: '打开' })[0]);
    expect(clicked).toHaveBeenCalledOnce(); expect(document.documentElement.dataset.ftTheme).toBe('dark'); expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('supports keyboard movement between setting tabs with labelled panels', () => {
    load().openSettings(); const appearance = screen.getByRole('tab', { name: '外观' });
    fireEvent.keyDown(appearance, { key: 'ArrowDown' });
    const workspace = screen.getByRole('tab', { name: '工作区' });
    expect(workspace.getAttribute('aria-selected')).toBe('true'); expect(document.activeElement).toBe(workspace);
    expect(screen.getByRole('tabpanel', { name: '工作区' })).toBeTruthy();
  });

  it('reads update state without initiating a check and invokes check only on user action', async () => {
    const check = vi.fn(async () => ({ phase: 'current', currentVersion: '0.1.9' })); window.FundsDesktopUpdates = { state: async () => ({ phase: 'idle', currentVersion: '0.1.9' }), check, subscribe: () => () => {} };
    load().openSettings(); fireEvent.click(screen.getByRole('tab', { name: '关于与更新' })); await screen.findByText('可检查桌面更新。');
    expect(check).not.toHaveBeenCalled(); fireEvent.click(screen.getByRole('button', { name: '检查更新' })); await screen.findByText('当前已是最新版本。'); expect(check).toHaveBeenCalledOnce();
  });

  it('opens the about and update tab directly for the rail update entry without checking automatically', async () => {
    const state = vi.fn(async () => ({ phase: 'idle', currentVersion: '0.1.9' })), check = vi.fn();
    window.FundsDesktopUpdates = { state, check, subscribe: () => () => {} };
    load().openSettings('about');
    expect(screen.getByRole('tab', { name: '关于与更新' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tabpanel', { name: '关于与更新' })).toBeTruthy();
    expect(screen.queryByRole('tabpanel', { name: '外观' })).toBeNull();
    await screen.findByText('可检查桌面更新。');
    expect(state).toHaveBeenCalledOnce(); expect(check).not.toHaveBeenCalled();
  });

  it('cleans up subscriptions and the broadcast channel on pagehide', () => {
    const unsubscribe = vi.fn(); window.FundsPreferences = { getItem: () => null, setItem: vi.fn(), subscribe: () => unsubscribe }; load();
    window.dispatchEvent(new Event('pagehide')); expect(unsubscribe).toHaveBeenCalledOnce(); expect(channels[0].close).toHaveBeenCalledOnce();
  });
});
