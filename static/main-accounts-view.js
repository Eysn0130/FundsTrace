/* Anchored account ledger. The case-bound controller owns requests and locks. */
(function (root) {
  'use strict';
  function create(doc, options) {
    const el = id => doc.getElementById(id), dialog = el('mainAccountsDialog');
    let controller, state, trigger, binding, rowMenu = null, listKey = '', searchKey = '', picker = null;
    let composing = false, closingReason = '', screen = '', pickerEpoch = 0, pickerContext = '', exactQuery = false, exactAccount = '';
    const usable = item => item?.isConnected && !item.disabled && item.getClientRects().length;
    const current = context => context && options.isCurrent(context, false);
    const image = (name, className = '') => {
      const node = doc.createElement('img'); node.src = `/static/ui-icons/${name}.svg`;
      node.alt = ''; node.className = `ui-icon ${className}`; return node;
    };
    function place() {
      if (!dialog.open) return;
      if (!usable(trigger)) trigger = el('btnMainActions');
      if (usable(trigger)) {
        const anchor = trigger.getBoundingClientRect(), ribbon = el('graphToolbar');
        // The command's caption is part of the ribbon. Grow the ledger below
        // that surface, while compact mode uses its smaller live boundary.
        const bottom = ribbon?.contains(trigger) ? Math.max(anchor.bottom, ribbon.getBoundingClientRect().bottom) : anchor.bottom;
        root.FundsToolbarMenu?.place(dialog, { getBoundingClientRect: () => ({ ...anchor, left: anchor.left, top: anchor.top, width: anchor.width, height: bottom - anchor.top, bottom }) });
      }
      if (rowMenu) root.FundsToolbarMenu?.place(el('maRowMenu'), rowMenu.trigger);
    }
    function closeRow(restore = false) {
      const previous = rowMenu; rowMenu = null;
      el('maRowMenu').hidden = true; el('maRowMenu').inert = true;
      if (previous) {
        previous.trigger.setAttribute('aria-expanded', 'false');
        previous.trigger.closest('tr')?.classList.remove('is-active');
        if (restore && usable(previous.trigger)) previous.trigger.focus({ preventScroll: true });
      }
    }
    function dismiss(reason = 'close') {
      pickerEpoch++; picker?.invalidate(); exactAccount = ''; composing = false; closingReason = reason; closeRow();
      el('btnMainActions').setAttribute('aria-expanded', 'false');
      if (dialog.open) dialog.close();
    }
    function openRow(row, button) {
      if (state.pending || state.unknown) return;
      const toggle = rowMenu?.trigger === button; closeRow(); if (toggle) return;
      rowMenu = { row, trigger: button }; button.setAttribute('aria-expanded', 'true');
      button.closest('tr')?.classList.add('is-active');
      el('maConvert').hidden = row.source !== 'auto'; el('maRowSeparator').hidden = row.source !== 'auto';
      el('maRowMenu').hidden = false; el('maRowMenu').inert = false;
      place(); (row.source === 'auto' ? el('maConvert') : el('maRemove')).focus({ preventScroll: true });
    }
    function identity(row) {
      const cell = doc.createElement('td'), name = doc.createElement('strong'), account = doc.createElement('span');
      name.textContent = row.name || '未提供户名'; account.textContent = row.account;
      account.className = 'main-accounts-number'; cell.append(name, account); return cell;
    }
    function renderRows(next) {
      const filtered = next.rows.filter(row => next.filter === 'all' || row.source === next.filter);
      const key = JSON.stringify([filtered, next.pending, next.unknown, next.loading, next.listLoaded]);
      if (key !== listKey) {
        closeRow(); listKey = key;
        el('maRows').replaceChildren(...filtered.map(row => {
          const tr = doc.createElement('tr'), source = doc.createElement('td'), actions = doc.createElement('td');
          const text = doc.createElement('span'); text.className = `main-accounts-source ${row.source === 'auto' ? 'is-auto' : ''}`;
          text.append(image(row.source === 'auto' ? 'settings' : 'user-search'), doc.createTextNode(row.source === 'auto' ? '自动识别' : '手动设置'));
          source.append(text);
          const button = doc.createElement('button'); button.type = 'button'; button.className = 'btn icon-button main-accounts-more';
          button.setAttribute('aria-label', `${row.name || row.account}的主账号操作`); button.setAttribute('aria-haspopup', 'menu');
          button.setAttribute('aria-controls', 'maRowMenu'); button.setAttribute('aria-expanded', 'false');
          button.disabled = next.pending || next.unknown || next.loading || !next.listLoaded; button.append(image('dots')); button.onclick = () => openRow(row, button);
          actions.append(button); tr.append(identity(row), source, actions); return tr;
        }));
      }
      el('maListEmpty').hidden = !next.loading && filtered.length > 0;
      el('maListEmpty').textContent = next.loading ? '正在读取主账号…' : next.filter === 'all' ? '暂无主账号' : `暂无${next.filter === 'auto' ? '自动识别' : '手动设置'}的主账号`;
      el('maAllCount').textContent = next.counts.all; el('maAutoCount').textContent = next.counts.auto; el('maManualCount').textContent = next.counts.manual;
      for (const button of el('maFilters').querySelectorAll('[data-ma-filter]')) {
        const active = button.dataset.maFilter === next.filter;
        button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
        button.disabled = next.loading || next.pending;
      }
    }
    function renderCandidates(next) {
      const availability = next.candidates.map(row => {
        try { return root.FundsGraphLocator?.availability?.(row.account) || { visible: false, tracked: false, reason: '当前画布暂不可定位' }; }
        catch (_) { return { visible: false, tracked: false, reason: '当前画布暂不可定位' }; }
      });
      const key = JSON.stringify([next.candidates, next.pending, next.unknown, next.searching, availability]);
      if (key !== searchKey) {
        searchKey = key;
        el('maCandidates').replaceChildren(...next.candidates.map((row, index) => {
          const tr = doc.createElement('tr'), action = doc.createElement('td'), button = doc.createElement('button');
          const locate = doc.createElement('button'), available = availability[index];
          locate.type = 'button'; locate.className = 'btn small icon-button main-accounts-locate';
          locate.setAttribute('aria-label', `定位 ${row.name || row.account} ${row.account}`);
          locate.disabled = next.pending || next.unknown || next.searching || !available.visible;
          locate.title = available.visible ? '定位当前画布中的账户' : available.reason || (available.tracked ? '已上图，但不在当前视图' : '该账户尚未上图');
          locate.append(image('target'));
          locate.onclick = async () => {
            if (!usable(locate) || !state.visible || state.screen !== 'search' || !current(state.context) || state.pending || state.unknown || state.searching) return;
            const locator = root.FundsGraphLocator;
            try {
              if (!locator?.availability?.(row.account)?.visible) return;
              const previewBinding = locator.capture?.();
              if (previewBinding != null) await locator.previewAccount?.(row.account, { binding: previewBinding });
            } catch (_) { /* Locating an existing account never changes its mark. */ }
          };
          button.type = 'button'; button.className = 'btn small';
          button.disabled = !!row.is_main || next.pending || next.unknown || next.searching;
          button.textContent = row.is_main ? '已添加' : '添加';
          button.setAttribute('aria-label', `${row.is_main ? '已添加' : '添加'} ${row.name || row.account} ${row.account}`);
          button.onclick = () => controller.prepare('manual', row); action.append(locate, button); tr.append(identity(row), action); return tr;
        }));
      }
      el('maSearchEmpty').hidden = !next.searching && next.candidates.length > 0;
      el('maSearchEmpty').textContent = next.searching ? '正在搜索账号…' : next.query ? '没有找到匹配的账号' : '本案件暂无可添加的账号';
      const pages = Math.max(1, Math.ceil(next.total / next.pageSize));
      el('maSearchTotal').textContent = `共 ${next.total} 个账号`; el('maPage').textContent = `${next.page} / ${pages}`;
      el('maPrev').disabled = next.pending || next.searching || next.page <= 1;
      el('maNext').disabled = next.pending || next.searching || next.page >= pages;
    }
    function confirmation(next) {
      const value = next.confirmation; if (!value) return;
      const action = value.action;
      el('maConfirmTitle').textContent = action === 'remove' ? '取消这个账号的主账号标记？' : action === 'clear-auto' ? '清除自动标记？' : '自动识别主账号？';
      el('maConfirmAccount').hidden = action !== 'remove';
      el('maConfirmAccount').textContent = `${value.name || '未提供户名'}\n${value.account || ''}`;
      el('maConfirmText').textContent = action === 'remove' ? '仅取消此账号的标记。同户名的其他主账号、原始流水和画布追踪记录会保留。'
        : action === 'clear-auto' ? `清除本案件 ${next.counts.auto} 个自动识别的主账号标记，保留全部手动设置。原始流水和画布追踪记录会保留。`
          : '识别本案件的单账户流水批次，保留已有手动设置。双边流水不参与自动识别。';
      el('maConfirmSubmit').textContent = action === 'remove' ? '取消标记' : action === 'clear-auto' ? '清除自动标记' : '开始识别';
      el('maConfirmSubmit').classList.toggle('danger', action !== 'auto');
      el('maConfirmSubmit').classList.toggle('primary', action === 'auto');
    }
    function onState(next) {
      const startingSearch = next.searching && !state?.searching;
      state = next;
      if (!next.visible || !current(next.context)) { picker?.invalidate(); if (dialog.open) dismiss('context'); return; }
      binding = next.context;
      const disabled = next.pending || next.unknown;
      const changedScreen = screen !== next.screen; screen = next.screen;
      const contextKey = JSON.stringify(binding);
      if (changedScreen || pickerContext !== contextKey || disabled || next.loading || startingSearch) picker?.invalidate();
      if (changedScreen || pickerContext !== contextKey) pickerEpoch++;
      if (changedScreen || pickerContext !== contextKey) { exactQuery = false; exactAccount = ''; }
      pickerContext = contextKey;
      if (changedScreen) closeRow();
      el('maTitle').textContent = next.screen === 'search' ? '添加主账号' : '主账号管理';
      el('maCase').textContent = options.readCaseName(binding);
      el('maListScreen').hidden = next.screen !== 'list'; el('maSearchScreen').hidden = next.screen !== 'search'; el('maConfirmScreen').hidden = next.screen !== 'confirm';
      el('maBack').hidden = next.screen === 'list'; el('maBack').disabled = disabled;
      el('maSearchAdd').hidden = next.screen !== 'list'; el('maSearchAdd').disabled = disabled || next.loading || !next.listLoaded;
      el('maClose').disabled = next.pending && next.sent;
      el('btnAutoMain').disabled = disabled || next.loading || !next.listLoaded;
      el('btnClearAutoMain').disabled = disabled || next.loading || !next.listLoaded || !next.counts.auto;
      el('maConfirmCancel').disabled = next.pending; el('maConfirmSubmit').disabled = disabled;
      el('maQuery').disabled = disabled;
      el('maSearchForm').querySelector('button').disabled = disabled || next.searching;
      if (doc.activeElement !== el('maQuery') && el('maQuery').value !== next.query) el('maQuery').value = next.query;
      renderRows(next); renderCandidates(next); confirmation(next);
      const error = next.refreshError || next.error;
      el('maErrorBlock').hidden = !error && !next.unknown; el('maError').textContent = error;
      el('maRetry').hidden = next.unknown || next.pending; el('maReconcile').hidden = !next.unknown; el('maReconcile').disabled = next.pending;
      el('maAcknowledge').hidden = !next.unknown || !next.reconciled; el('maAcknowledge').disabled = next.pending;
      el('maFeedback').hidden = !next.message && !next.pending;
      el('maFeedback').textContent = next.pending ? next.phase === 'reconciling' ? '正在读取当前标记…' : next.sent ? '正在更新主账号…' : '正在确认资料状态…' : next.message || '';
      dialog.setAttribute('aria-busy', String(next.pending));
      if (!dialog.open) dialog.showModal();
      el('btnMainActions').setAttribute('aria-expanded', 'true'); place();
      if (changedScreen) (next.screen === 'search' ? el('maQuery') : next.screen === 'confirm' ? el('maConfirmCancel') : el('maSearchAdd')).focus({ preventScroll: true });
      if (!next.pending && (!dialog.contains(doc.activeElement) || !usable(doc.activeElement))) {
        (next.unknown ? el('maReconcile') : next.screen === 'search' ? el('maQuery') : next.screen === 'confirm' ? el('maConfirmCancel') : next.listLoaded ? el('maSearchAdd') : el('maClose')).focus({ preventScroll: true });
      }
    }
    function show(next, anchor) {
      root.FundsToolbarMenu?.close('command'); root.FundsRibbon?.closePopovers?.();
      trigger = usable(anchor) ? anchor : el('btnMainActions'); closingReason = '';
      screen = ''; listKey = ''; searchKey = ''; onState(next);
    }
    el('maSearchAdd').onclick = () => controller.showSearch();
    el('maBack').onclick = () => { picker?.invalidate(); controller.back(); };
    el('maClose').onclick = () => controller.close('close');
    el('btnAutoMain').onclick = () => controller.prepare('auto');
    el('btnClearAutoMain').onclick = () => controller.prepare('clear-auto');
    el('maConfirmCancel').onclick = () => controller.cancelConfirm();
    el('maConfirmSubmit').onclick = () => controller.submit();
    el('maConvert').onclick = () => { const row = rowMenu?.row; closeRow(); if (row) controller.prepare('manual', row); };
    el('maRemove').onclick = () => { const row = rowMenu?.row; closeRow(); if (row) controller.prepare('remove', row); };
    el('maRetry').onclick = () => state.screen === 'search' ? controller.search(state.query, state.page, exactQuery) : controller.refresh();
    el('maReconcile').onclick = () => controller.reconcile(); el('maAcknowledge').onclick = () => controller.acknowledge();
    el('maPrev').onclick = () => controller.search(state.query, state.page - 1, exactQuery); el('maNext').onclick = () => controller.search(state.query, state.page + 1, exactQuery);
    const queryScope = () => state?.visible && state.screen === 'search' && dialog.open && current(binding)
      && !state.pending && !state.unknown && !state.loading
      // Disabling the submitted SVG button returns focus to this input. That
      // focus must not reopen suggestions over the result being requested;
      // editing a different draft is still allowed while it is in flight.
      && !(state.searching && el('maQuery').value.trim() === state.query) ? `${pickerEpoch}:${JSON.stringify(binding)}` : null;
    const doSearch = (query = el('maQuery').value, selected = picker?.getSelected()) => {
      if (composing || !state?.visible || state.screen !== 'search' || !dialog.open || !current(binding) || state.pending || state.unknown || state.loading) return false;
      const value = String(query).trim(), selectedAccount = typeof selected?.account === 'string' ? selected.account : '';
      if (selectedAccount && value === selectedAccount) exactAccount = selectedAccount;
      exactQuery = !!exactAccount && value === exactAccount; return controller.search(value, 1, exactQuery);
    };
    picker = root.FundsEntitySearch?.mount(el('maQuery'), {
      caseId: () => binding?.caseId, scope: queryScope, source: 'flows', kind: 'account',
      onSelect(item) { exactAccount = typeof item.account === 'string' && el('maQuery').value === item.account ? item.account : ''; },
      onSubmit: (query, selected) => { void doSearch(query, selected); },
    }) || null;
    const searchButton = el('maSearchForm').querySelector('button');
    searchButton.classList.add('icon-button'); searchButton.setAttribute('aria-label', '查询本案件账号或户名');
    const leadingIcon = el('maSearchForm').querySelector('img');
    if (leadingIcon?.parentElement === el('maSearchForm')) leadingIcon.remove();
    searchButton.replaceChildren(image('search'));
    el('maSearchForm').addEventListener('submit', event => { event.preventDefault(); if (!event.isComposing) void doSearch(); });
    el('maQuery').addEventListener('input', () => { exactAccount = ''; });
    el('maQuery').addEventListener('compositionstart', () => { composing = true; });
    el('maQuery').addEventListener('compositionend', () => { composing = false; });
    el('maFilters').addEventListener('click', event => { const button = event.target.closest('[data-ma-filter]'); if (button) controller.setFilter(button.dataset.maFilter); });
    el('maFilters').addEventListener('keydown', event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      const tabs = [...el('maFilters').querySelectorAll('[role="tab"]')], index = tabs.indexOf(doc.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault(); if (!tabs[next].disabled) { controller.setFilter(tabs[next].dataset.maFilter); tabs[next].focus(); }
    });
    dialog.addEventListener('pointerdown', event => { if (rowMenu && !event.target.closest('#maRowMenu,.main-accounts-more')) closeRow(); });
    dialog.addEventListener('keydown', event => {
      if (composing || event.isComposing || event.keyCode === 229) {
        if (event.key === 'Enter') event.preventDefault();
        event.stopPropagation(); return;
      }
      if (rowMenu) {
        if (event.key === 'Escape' || event.key === 'Tab') { closeRow(event.key === 'Escape'); event.preventDefault(); }
        else if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
          const commands = [...el('maRowMenu').querySelectorAll('[role="menuitem"]')].filter(item => !item.hidden);
          const index = commands.indexOf(doc.activeElement), next = event.key === 'Home' ? 0 : event.key === 'End' ? commands.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + commands.length) % commands.length;
          event.preventDefault(); commands[next]?.focus();
        }
      } else if (event.key === 'Escape') {
        // A native search input clears itself before the dialog's cancel
        // default runs. Handle this key here, through the controller's write
        // lock, after the picker has consumed its own first Escape.
        event.preventDefault();
        if (el('maQuery').getAttribute('aria-expanded') === 'true') picker?.close();
        else controller.close('escape');
      }
      event.stopPropagation();
    });
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      if (el('maQuery').getAttribute('aria-expanded') === 'true') picker?.close();
      else if (rowMenu) closeRow(true); else controller.close('escape');
    });
    dialog.addEventListener('close', () => { if (!dialog.open && closingReason !== 'context' && current(binding) && usable(trigger)) trigger.focus({ preventScroll: true }); });
    root.addEventListener('resize', place); root.addEventListener('fundstrace:workspace-resize', place);
    dialog.addEventListener('scroll', () => closeRow(), true);
    return { show, onState, dismiss, attach(value) { controller = value; } };
  }
  root.FundsMainAccountsView = Object.freeze({ create });
})(window);
