/* Presentation preferences only: no case facts, account values or general IPC. */
(function (root) {
  'use strict';
  if (root.FundsAppearance) return;
  const doc = root.document, KEY = 'fund_appearance', CHANNEL = 'fundstrace:appearance';
  const defaults = Object.freeze({ version: 1, theme: 'dark', accent: 'blue', translucency: false });
  const themes = ['dark', 'light', 'system'], accents = ['blue', 'teal', 'violet', 'rose', 'amber'];
  const keys = ['accent', 'theme', 'translucency', 'version'];
  const media = typeof root.matchMedia === 'function' ? root.matchMedia('(prefers-color-scheme: light)') : null;
  const bridge = root.FundsPreferences || root.FundsDesktopPreferences;
  let saved = { ...defaults }, temporary = null, dialog = null, draft = null, focusBefore = null, disposed = false;
  let broadcast = null, unsubscribe = () => {}, updateUnsubscribe = () => {}, updateEpoch = 0;
  const copy = value => ({ version: 1, theme: value.theme, accent: value.accent, translucency: value.translucency });
  function valid(value) {
    return value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join() === keys.join()
      && value.version === 1 && themes.includes(value.theme) && accents.includes(value.accent) && typeof value.translucency === 'boolean';
  }
  function checked(value) { if (!valid(value)) throw new TypeError('外观设置格式无效。'); return copy(value); }
  function parse(value) { try { const item = JSON.parse(value); return valid(item) ? copy(item) : null; } catch (_) { return null; } }
  function current() { return temporary || saved; }
  function stateMessage() { return { channel: CHANNEL, version: 1, type: 'state', preference: copy(current()), preview: temporary !== null }; }
  function children(message) {
    for (const frame of doc.querySelectorAll('iframe')) {
      try { frame.contentWindow?.postMessage(message, root.location.origin); } catch (_) { /* A foreign frame cannot receive the same-origin snapshot. */ }
    }
  }
  function render() {
    const preference = current();
    const resolvedTheme = preference.theme === 'system' ? media?.matches ? 'light' : 'dark' : preference.theme;
    const element = doc.documentElement;
    element.dataset.ftTheme = resolvedTheme; element.dataset.ftAccent = preference.accent;
    element.dataset.ftTranslucency = preference.translucency ? 'true' : 'false';
    element.style.colorScheme = resolvedTheme;
    children(stateMessage());
    root.dispatchEvent(new root.CustomEvent('funds-appearance-change', { detail: { preference: copy(preference), resolvedTheme, preview: temporary !== null } }));
  }
  function apply(value) { saved = checked(value); temporary = null; render(); return copy(saved); }
  function preview(value) { temporary = checked(value); render(); return copy(temporary); }
  function cancelPreview() { temporary = null; render(); return copy(saved); }
  function committed(value) {
    if (!valid(value)) return;
    saved = copy(value); render();
    if (dialog?.open && temporary) message('外观已在其他窗口保存；取消将恢复最新的已保存设置。');
  }
  function save(value) {
    const preference = checked(value), serialized = JSON.stringify(preference);
    if (root.parent !== root && !bridge) throw new Error('请从主窗口设置保存外观。');
    try {
      if (bridge && typeof bridge.setItem === 'function') { if (bridge.setItem(KEY, serialized) === false) throw new Error('保存失败'); }
      else root.localStorage.setItem(KEY, serialized);
    } catch (_) { throw new Error('外观设置未能保存。当前仍为预览，可重试或取消。'); }
    saved = preference; temporary = null; render();
    try { broadcast?.postMessage({ channel: CHANNEL, version: 1, type: 'saved', preference: copy(saved) }); } catch (_) { /* Persistence succeeded; an unavailable peer does not undo it. */ }
    return copy(saved);
  }
  function message(text, error = false) {
    if (!dialog) return;
    const output = dialog.querySelector('[data-settings-status]'); output.textContent = text; output.dataset.error = error ? 'true' : 'false';
  }
  function setDraft(value) { draft = checked(value); preview(draft); syncControls(); message('正在预览，保存后会应用到其他窗口。'); }
  function syncControls() {
    if (!dialog || !draft) return;
    for (const input of dialog.querySelectorAll('[name="ft-theme"]')) input.checked = input.value === draft.theme;
    for (const input of dialog.querySelectorAll('[name="ft-accent"]')) input.checked = input.value === draft.accent;
    dialog.querySelector('[data-translucency]').checked = draft.translucency;
  }
  function closeSettings(cancel = true) {
    if (!dialog?.open) return;
    if (cancel) cancelPreview();
    updateEpoch++; dialog.close(); updateUnsubscribe(); updateUnsubscribe = () => {};
    if (focusBefore?.isConnected) focusBefore.focus({ preventScroll: true });
  }
  function switchSection(name) {
    for (const button of dialog.querySelectorAll('[data-settings-section]')) { const selected = button.dataset.settingsSection === name; button.setAttribute('aria-selected', String(selected)); button.tabIndex = selected ? 0 : -1; }
    for (const panel of dialog.querySelectorAll('[data-settings-panel]')) panel.hidden = panel.dataset.settingsPanel !== name;
  }
  function navigate(selector, view = false) {
    const target = doc.querySelector(selector); if (!target || target.disabled) return;
    closeSettings(true); target.click();
    if (view) doc.querySelector('[data-ribbon-tab="view"]')?.click();
  }
  function renderUpdate(state) {
    if (!dialog?.open) return;
    const text = dialog.querySelector('[data-update-state]'), button = dialog.querySelector('[data-check-updates]');
    const version = dialog.querySelector('[data-product-version]');
    if (typeof state?.currentVersion === 'string' && state.currentVersion.length < 80) version.textContent = `循迹 FundsTrace ${state.currentVersion} · 预览版`;
    const labels = { idle: '可检查桌面更新。', current: '当前已是最新版本。', checking: '正在检查更新…', available: '有可用更新。', downloading: '正在下载更新…', ready: '更新已就绪，重启后安装。', installing: '正在重启安装…', error: '更新未完成，可重试检查。', unavailable: '当前运行环境未启用桌面自动更新。' };
    text.textContent = labels[state?.phase] || '无法读取更新状态，请重试。';
    button.dataset.action = state?.phase === 'available' ? 'download' : state?.phase === 'ready' ? 'install' : 'check';
    button.textContent = state?.phase === 'available' ? '下载更新' : state?.phase === 'ready' ? '重启并更新' : '检查更新';
    button.disabled = !root.FundsDesktopUpdates || ['unavailable', 'checking', 'downloading', 'installing'].includes(state?.phase);
  }
  function connectUpdates() {
    const updates = root.FundsDesktopUpdates;
    if (!updates || typeof updates.state !== 'function') { renderUpdate({ phase: 'unavailable' }); return; }
    const epoch = ++updateEpoch; let received = false;
    if (typeof updates.subscribe === 'function') updateUnsubscribe = updates.subscribe(state => { if (epoch !== updateEpoch) return; received = true; renderUpdate(state); });
    Promise.resolve(updates.state()).then(state => { if (epoch === updateEpoch && !received) renderUpdate(state); }).catch(() => { if (epoch === updateEpoch) renderUpdate(null); });
  }
  function buildSettings() {
    dialog = doc.createElement('dialog'); dialog.className = 'fta-settings'; dialog.id = 'fundsAppearanceSettings';
    dialog.setAttribute('aria-labelledby', 'fta-settings-title');
    dialog.innerHTML = `<div class="fta-shell"><aside class="fta-nav"><h2 id="fta-settings-title">设置</h2><div role="tablist" aria-label="设置分类"><button type="button" role="tab" data-settings-section="appearance" aria-selected="true">外观</button><button type="button" role="tab" data-settings-section="workspace" aria-selected="false">工作区</button><button type="button" role="tab" data-settings-section="about" aria-selected="false">关于与更新</button></div><p>循迹 FundsTrace</p></aside><div class="fta-main"><header class="fta-heading"><span>偏好设置</span><button type="button" data-settings-close aria-label="关闭设置"><img src="/static/ui-icons/x.svg" alt=""></button></header><section data-settings-panel="appearance"><h3>外观</h3><p class="fta-description">让工作区保持清晰、舒适。更改会先在当前窗口预览。</p><fieldset class="fta-theme-options"><legend>主题</legend><label class="fta-theme-card"><input type="radio" name="ft-theme" value="light"><span class="fta-theme-sample light" aria-hidden="true"><i></i><b></b><em></em></span><span>浅色</span></label><label class="fta-theme-card"><input type="radio" name="ft-theme" value="dark"><span class="fta-theme-sample dark" aria-hidden="true"><i></i><b></b><em></em></span><span>深色</span></label><label class="fta-theme-card"><input type="radio" name="ft-theme" value="system"><span class="fta-theme-sample system" aria-hidden="true"><i></i><b></b><em></em></span><span>跟随系统</span></label></fieldset><fieldset class="fta-accent-options"><legend>重点色</legend>${[['blue','蓝色'],['teal','青绿'],['violet','紫色'],['rose','玫红'],['amber','琥珀']].map(([value,label]) => `<label class="fta-accent" data-accent="${value}" title="${label}"><input type="radio" name="ft-accent" value="${value}" aria-label="${label}"><span aria-hidden="true"></span></label>`).join('')}</fieldset><label class="fta-material"><span><strong>半透明材质</strong><small>为导航和浮层添加柔和层次，表格及金额保持清晰。</small></span><input type="checkbox" data-translucency role="switch" aria-label="半透明材质"></label><p class="fta-note">重点色仅用于操作与选中状态，不改变资金角色图例、图形样式或导出内容。</p></section><section data-settings-panel="workspace" hidden><h3>工作区</h3><p class="fta-description">按案件管理数据，在画布中追踪资金关系。</p><div class="fta-workspace-action"><div><strong>案件与导入</strong><p>管理案件、核对流水和查看导入记录。</p></div><button type="button" data-workspace-nav="cases">打开</button></div><div class="fta-workspace-action"><div><strong>账户与身份</strong><p>维护账户身份、照片和主账号资料。</p></div><button type="button" data-workspace-nav="accounts">打开</button></div><div class="fta-workspace-action"><div><strong>画布显示</strong><p>在画布的“视图”中调整标签、证件和照片显示。</p></div><button type="button" data-workspace-nav="home">打开视图设置</button></div><p class="fta-note">工作区入口会取消尚未保存的外观预览。</p></section><section data-settings-panel="about" hidden><h3>关于与更新</h3><p class="fta-product" data-product-version>循迹 FundsTrace · 预览版</p><p class="fta-description">资金追踪与案件分析工具</p><div class="fta-update"><p data-update-state role="status">正在读取更新状态…</p><button type="button" data-check-updates disabled>检查更新</button></div><a class="fta-license" href="/license" target="_blank" rel="noopener">版本与许可</a></section><p class="fta-status" data-settings-status role="status"></p><footer class="fta-footer"><button type="button" data-settings-reset>恢复默认</button><span></span><button type="button" data-settings-cancel>取消</button><button type="button" class="fta-primary" data-settings-save>保存</button></footer></div></div>`;
    for (const button of dialog.querySelectorAll('[data-settings-section]')) {
      const name = button.dataset.settingsSection; button.id = `fta-tab-${name}`; button.setAttribute('aria-controls', `fta-panel-${name}`);
      const panel = dialog.querySelector(`[data-settings-panel="${name}"]`); panel.id = `fta-panel-${name}`; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', button.id);
      button.addEventListener('keydown', event => {
        if (!['ArrowDown','ArrowUp','ArrowRight','ArrowLeft','Home','End'].includes(event.key)) return;
        event.preventDefault(); const tabs = Array.from(dialog.querySelectorAll('[data-settings-section]')), at = tabs.indexOf(button);
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (at + (['ArrowDown','ArrowRight'].includes(event.key) ? 1 : -1) + tabs.length) % tabs.length;
        switchSection(tabs[next].dataset.settingsSection); tabs[next].focus();
      });
    }
    doc.body.append(dialog);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeSettings(true); });
    dialog.addEventListener('close', () => { updateEpoch++; if (temporary) cancelPreview(); updateUnsubscribe(); updateUnsubscribe = () => {}; });
    dialog.querySelector('[data-settings-close]').onclick = () => closeSettings(true);
    dialog.querySelector('[data-settings-cancel]').onclick = () => closeSettings(true);
    dialog.querySelector('[data-settings-reset]').onclick = () => setDraft(defaults);
    dialog.querySelector('[data-settings-save]').onclick = () => { try { save(draft); message('外观设置已保存。'); closeSettings(false); } catch (error) { message(error.message, true); } };
    for (const button of dialog.querySelectorAll('[data-settings-section]')) button.onclick = () => switchSection(button.dataset.settingsSection);
    for (const input of dialog.querySelectorAll('[name="ft-theme"]')) input.onchange = () => { if (input.checked) setDraft({ ...draft, theme: input.value }); };
    for (const input of dialog.querySelectorAll('[name="ft-accent"]')) input.onchange = () => { if (input.checked) setDraft({ ...draft, accent: input.value }); };
    dialog.querySelector('[data-translucency]').onchange = event => setDraft({ ...draft, translucency: event.target.checked });
    for (const button of dialog.querySelectorAll('[data-workspace-nav]')) button.onclick = () => navigate(`[data-nav-page="${button.dataset.workspaceNav}"]`, button.dataset.workspaceNav === 'home');
    dialog.querySelector('[data-check-updates]').onclick = async event => {
      const button = event.currentTarget, updates = root.FundsDesktopUpdates, action = button.dataset.action;
      if (!updates || !['check','download','install'].includes(action) || typeof updates[action] !== 'function') return;
      button.disabled = true; const epoch = updateEpoch;
      try { const state = await updates[action](); if (epoch === updateEpoch) renderUpdate(state); } catch (_) { if (epoch === updateEpoch) renderUpdate({ phase: 'error' }); }
    };
  }
  function openSettings(section = 'appearance') {
    if (!doc.body) return;
    if (!dialog) buildSettings();
    if (dialog.open) { dialog.querySelector('[data-settings-close]').focus(); return; }
    focusBefore = doc.activeElement; draft = copy(saved); temporary = null; render(); syncControls(); switchSection(['appearance','workspace','about'].includes(section) ? section : 'appearance'); message('');
    for (const button of dialog.querySelectorAll('[data-workspace-nav]')) button.disabled = !doc.querySelector(`[data-nav-page="${button.dataset.workspaceNav}"]`);
    dialog.showModal(); dialog.querySelector('[data-settings-close]').focus(); connectUpdates();
  }
  function onMessage(event) {
    const data = event.data;
    if (event.origin !== root.location.origin || !data || data.channel !== CHANNEL || data.version !== 1) return;
    if (data.type === 'ready') {
      const frame = Array.from(doc.querySelectorAll('iframe')).find(item => item.contentWindow === event.source);
      if (frame) frame.contentWindow.postMessage(stateMessage(), root.location.origin);
    } else if (data.type === 'state' && root.parent !== root && event.source === root.parent && valid(data.preference) && typeof data.preview === 'boolean') {
      if (data.preview) preview(data.preference); else apply(data.preference);
    }
  }
  try { saved = parse(bridge?.getItem ? bridge.getItem(KEY) : root.localStorage.getItem(KEY)) || { ...defaults }; } catch (_) { /* Invalid or unavailable preference stores use dark defaults. */ }
  if (typeof root.BroadcastChannel === 'function') {
    try { broadcast = new root.BroadcastChannel(CHANNEL); broadcast.onmessage = event => { const value = event.data; if (value?.channel === CHANNEL && value.version === 1 && value.type === 'saved' && valid(value.preference)) committed(value.preference); }; } catch (_) { broadcast = null; }
  }
  if (typeof bridge?.subscribe === 'function') unsubscribe = bridge.subscribe(event => { if (event?.key === KEY) { const value = parse(event.value); if (value) committed(value); } });
  const onStorage = event => { if (!bridge && event.key === KEY && event.newValue !== null) { const value = parse(event.newValue); if (value) committed(value); } };
  const onSystem = () => { if (current().theme === 'system') render(); };
  root.addEventListener('storage', onStorage); root.addEventListener('message', onMessage); media?.addEventListener?.('change', onSystem);
  root.addEventListener('pagehide', event => { if (event.persisted) return; disposed = true; unsubscribe(); updateUnsubscribe(); broadcast?.close(); root.removeEventListener('storage', onStorage); root.removeEventListener('message', onMessage); media?.removeEventListener?.('change', onSystem); }, { once: true });
  root.FundsAppearance = Object.freeze({ get: () => copy(saved), apply, preview, cancelPreview, save, openSettings });
  render();
  if (root.parent !== root && !disposed) root.parent.postMessage({ channel: CHANNEL, version: 1, type: 'ready' }, root.location.origin);
})(window);
