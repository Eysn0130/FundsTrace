/* Detail request ownership and the thin canvas entry to the shared importer. */
(function () {
  'use strict';
  const text = error => String(error?.message || error || '请求失败').slice(0, 200);
  window.FundsImportWorkflow = Object.freeze({
    canWrite: async context => context?.caseId != null,
    open: () => window.FundsUnifiedImport.open(),
    restore: id => window.FundsUnifiedImport.restore(id),
  });
  async function request(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let message = await response.text();
      try { message = JSON.parse(message).detail || message; } catch (_) { /* plain text */ }
      const error = new Error(String(message).slice(0, 200)); error.status = response.status; throw error;
    }
    return response.json();
  }

  function bindDetailRequests() {
    let drawerRequest = 0, drawerBinding = null;
    const stale = {};
    const implicitFocus = node => !node || node === document.body || node === document.documentElement || node.isConnected === false;
    function captureDrawerFocus(binding) {
      const focused = document.activeElement;
      if ($('#drawer').contains(focused)) return { id: focused.id, node: focused };
      return implicitFocus(focused) ? binding?.focusIntent || null : null;
    }
    function restoreDrawerFocus(focus) {
      if (!focus || !implicitFocus(document.activeElement)) return;
      const match = focus.id ? document.getElementById(focus.id) : null;
      const target = match && $('#drawer').contains(match) && !match.disabled ? match : $('#btnCloseDrawer');
      target?.focus();
    }
    const originalApi = api;
    api = async function (path, options = {}) {
      // These GETs belong exclusively to the existing node/edge detail views.
      // All other requests retain their original adapter and feedback contract.
      const detail = /^\/api\/accounts\/[^/]+\/profile\?/.test(path)
        || path.startsWith('/api/main-accounts?')
        || (path.startsWith('/api/flows?') && /[?&]ids=/.test(path));
      if (!detail || (options.method && options.method !== 'GET')) return originalApi(path, options);
      const token = drawerRequest, context = caseContext();
      let result;
      try { result = await request(path, options); }
      catch (error) {
        if (token !== drawerRequest || !isCurrentCase(context)) throw stale;
        throw error;
      }
      if (token !== drawerRequest || !isCurrentCase(context)) throw stale;
      if (/^\/api\/accounts\/[^/]+\/profile\?/.test(path) && drawerBinding?.token === token) drawerBinding.profile = result;
      return result;
    };

    function bindNodeActions(binding) {
      // The account workspace owns its writes, draft and photo lock together.
      if (typeof nodeWorkspace !== 'undefined' && nodeWorkspace) return;
      const { context, account, item, profile } = binding;
      const current = () => binding === drawerBinding && binding.token === drawerRequest && isCurrentCase(context);
      const node = state.lastGraph.nodes.find(value => value.account === account) || {};
      const selectors = ['#ndFocusApply', '#ndIdSave', '#ndPhotoClr', '#ndToggleMain'];
      const buttons = selectors.map(selector => $(selector));
      const write = (path, body, method = 'POST') => request(path, { method,
        ...jsonOpts({ case_id: context.caseId, account, ...body }) });

      function action(selector, failure, run) {
        $(selector).onclick = async () => {
          if (!current() || binding.busy) return;
          binding.busy = true;
          binding.focusIntent = captureDrawerFocus(binding);
          const disabled = buttons.map(button => button.disabled);
          buttons.forEach(button => { button.disabled = true; });
          try { await run(current); }
          catch (error) { if (current()) toast(failure + '：' + text(error), 'err'); }
          finally {
            if (current()) {
              binding.busy = false;
              buttons.forEach((button, index) => { button.disabled = disabled[index]; });
            }
          }
        };
      }

      action('#ndFocusApply', '设置失败', async current => {
        const scale = Number($('#ndFocusScale').value) || 1;
        const result = await write('/api/focus-nodes/set', { on: scale > 1, scale });
        if (!current()) return;
        toast(result.on ? `该节点已重点展示（放大 ${result.scale} 倍）` : '已取消重点展示，节点恢复正常大小');
        state.forceRebuild = true;
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndIdSave', '保存失败', async current => {
        const value = $('#ndIdInput').value.trim();
        await write('/api/account-ids/set', { id_no: value });
        if (!current()) return;
        const graphNode = state.lastGraph.nodes.find(value => value.account === account);
        if (graphNode) graphNode.id_no = value || null;
        toast(value ? '身份证号已保存并更新到图谱' : '已清除该账号身份证号');
        renderGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndPhotoClr', '清除失败', async current => {
        await write('/api/account-photos/clear', {});
        if (!current()) return;
        toast('已清除照片，节点恢复圆点');
        state.forceRebuild = true;
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
      action('#ndToggleMain', '主账号标记失败', async current => {
        const isMain = node.is_main === 1, name = profile?.name || node.name || '';
        await write('/api/main-accounts', { name }, isMain ? 'DELETE' : 'POST');
        if (!current()) return;
        toast(isMain ? '已取消主账号标记' : '已标记为主账号，同户名的其他账号也会自动带标');
        await loadGraph();
        if (!current()) return;
        await showNodeDrawer(account, item);
      });
    }

    for (const [name, original] of [['showNodeDrawer', showNodeDrawer], ['showEdgeDrawer', showEdgeDrawer]]) {
      const wrapped = async function (...args) {
        if (name === 'showNodeDrawer') args[1] = state.graph?.findById?.(args[0]) || args[1];
        const previous = drawerBinding;
        const focus = name === 'showNodeDrawer' && previous?.account === args[0]
          && isCurrentCase(previous.context)
          && (previous.token === drawerRequest || previous.preserveFocus && implicitFocus(document.activeElement))
          ? captureDrawerFocus(previous) : null;
        if (previous) previous.preserveFocus = false;
        const token = ++drawerRequest, context = caseContext();
        const binding = drawerBinding = { token, context, account: args[0], item: args[1], profile: null, busy: false,
          focusIntent: focus, preserveFocus: false };
        try {
          const result = await original.apply(this, args);
          if (name === 'showNodeDrawer' && token === drawerRequest && isCurrentCase(context)) {
            bindNodeActions(binding); restoreDrawerFocus(focus);
          }
          return result;
        }
        catch (error) {
          if (error === stale || token !== drawerRequest || !isCurrentCase(context)) return;
          $('#drawerBody').textContent = '详情加载失败：' + text(error) + '。请再次查看详情重试。';
        }
      };
      if (name === 'showNodeDrawer') showNodeDrawer = wrapped; else showEdgeDrawer = wrapped;
    }
    const originalClose = closeDrawer;
    closeDrawer = function (...args) {
      const binding = drawerBinding;
      if (binding) {
        const preserve = args[0] === false && binding.token === drawerRequest && isCurrentCase(binding.context);
        binding.focusIntent = preserve ? captureDrawerFocus(binding) : null;
        // A graph redraw removes the focused control before the same account is
        // reopened. Only that next request may carry its implicit blur forward.
        binding.preserveFocus = preserve && !!binding.focusIntent;
      }
      drawerRequest += 1;
      return originalClose.apply(this, args);
    };
    $('#btnCloseDrawer').onclick = () => {
      closeDrawer(false, { keepSelection: true });
      $('#graphContainer').focus({ preventScroll: true });
    };

    const originalContext = loadContext;
    loadContext = async function (...args) {
      if (!state.ctx || state.ctx.closed || state.ctx.phase === 'tracking') return;
      const context = caseContext(), selection = state.ctx = { ...state.ctx };
      try { return await originalContext.apply(this, args); }
      catch (error) {
        if (!contextIsCurrent(selection, context)) return;
        selection.phase = 'error';
        const row = document.createElement('tr'), cell = document.createElement('td');
        cell.colSpan = 8; cell.className = 'context-empty'; cell.textContent = '流水明细加载失败：' + text(error) + '。';
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'btn small'; retry.textContent = '重新加载明细';
        retry.onclick = () => { if (contextIsCurrent(selection, context)) return loadContext(); };
        cell.append(retry); row.append(cell); $('#ctxTbody').replaceChildren(row);
        $('#ctxPos').textContent = '流水明细读取失败';
        $('#ctxSuggest').textContent = '匹配线索暂不可用';
        syncContextSelection();
      }
    };

    const status = $('#ctxTrackStatus');
    $('#ctxTrack').onclick = async () => {
      const selection = state.ctx, context = selection?.context || caseContext();
      if (!contextIsCurrent(selection, context) || selection.phase !== 'ready' || selection.submission) return;
      const ids = $$('#ctxTbody .ctx-ck').filter(node => node.checked && !node.disabled).map(node => Number(node.dataset.id));
      if (!ids.length) { syncContextSelection(); return; }
      const submission = selection.submission = {};
      const current = () => contextIsCurrent(selection, context) && selection.submission === submission;
      selection.phase = 'tracking'; status.textContent = '正在上图…'; syncContextSelection();
      try {
        const applied = await trackFlows(ids);
        if (!current()) return;
        if (applied === true) { closeContext(); }
        else status.textContent = '本次上图未完成，已保留勾选。请等待当前画布操作完成后重试。';
      } catch (error) {
        if (current()) status.textContent = '上图失败：' + text(error) + '。勾选已保留，可重试。';
      } finally {
        if (current()) {
          selection.phase = 'ready'; selection.submission = null; syncContextSelection();
          if (document.activeElement === document.body) $('#ctxTrack').focus();
        }
      }
    };
  }


  window.addEventListener('DOMContentLoaded', () => {
    $('#btnImport').onclick = () => window.FundsUnifiedImport.open();
    $('#btnImport2').onclick = () => window.FundsUnifiedImport.open();
    bindDetailRequests();
  });
})();
