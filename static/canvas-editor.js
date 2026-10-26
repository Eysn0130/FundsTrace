/* Classic script loaded after app.js. Its shared lexical bindings are the command
 * adapter boundary; transient selection stays outside persistent history.
 * History contains IDs, display choices and geometry only, never graph facts/photos. */
(function () {
  'use strict';

  const copy = value => JSON.parse(JSON.stringify(value));
  const order = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const point = value => value && Number.isFinite(value.x) && Number.isFinite(value.y)
    ? { x: value.x, y: value.y } : null;
  const cameraOffset = () => point(state.graphCamera?.getOffset?.()) || { x: 0, y: 0 };
  function cameraTransform(value, offset) {
    return value && Number.isFinite(value.scale) && value.scale > 0 && point(value)
      ? { scale: value.scale, x: value.x, y: value.y } : { scale: 1, ...offset };
  }
  function restoreCamera(snapshot) {
    state.graphCamera?.resize?.();
    state.graphCamera?.cancelFocus?.('history');
    const matrix = copy(snapshot.camera), current = cameraTransform(state.graphCamera?.getTransform?.(), cameraOffset());
    const captured = cameraTransform(snapshot.cameraTransform, point(snapshot.cameraOffset) || { x: 0, y: 0 });
    const scale = current.scale / captured.scale;
    // Apply current chrome affine × inverse(captured chrome affine). This keeps
    // both composition zoom and translation outside geometry undo/rollback.
    for (const i of [0, 1, 3, 4]) matrix[i] *= scale;
    matrix[6] = (matrix[6] - captured.x) * scale + current.x;
    matrix[7] = (matrix[7] - captured.y) * scale + current.y;
    state.graph?.get?.('group')?.setMatrix?.(matrix);
  }
  let gesture = null, bound = false;
  let membershipToken = 0, membershipSequence = 0, membershipDraining = false;
  let membershipContext = caseContext(), membershipDesired = new Map(), membershipActive = new Map();
  let membershipFailures = new Map(), membershipWaiters = [];
  const membershipListeners = new Set(), trackedWrites = new Set();
  const controls = '#btnBulkTrack,#btnClearGraph,#layoutSel,#btnRelayout,#btnArrangeLabels,#showIdCk,#showPhotoCk,#graphLabelMode,#btnNeighborhood,#btnExpandNeighbors,#btnGraphOverview,#neighborAccount,#neighborDirection,#edgeLabelGroup .edge-ck, #toolbarDisplayExtra .edge-ck';

  function capture() {
    // Async commands can finish between animation/ResizeObserver samples.
    state.graphCamera?.resize?.();
    const positions = {};
    for (const [id, value] of Object.entries(state.positions)) {
      const p = point(value); if (p) positions[id] = p;
    }
    for (const node of state.graph?.getNodes() || []) {
      const model = node.getModel(), p = point(model); if (p) positions[model.id] = p;
    }
    const edgeSnapshotLabels = {};
    for (const [id, value] of Object.entries(state.edgeSnapshotLabels)) {
      const geometry = {};
      for (const key of ['lx', 'ly', 'bw', 'bh']) if (Number.isFinite(value[key])) geometry[key] = value[key];
      if(typeof value.manual==='boolean')geometry.manual=value.manual;
      if (Object.keys(geometry).length) edgeSnapshotLabels[id] = geometry;
    }
    const edgeLabelPos = {}, edgeBendPos = {};
    for (const [id, ratio] of Object.entries(state.edgeLabelPos)) if (Number.isFinite(ratio)) edgeLabelPos[id] = ratio;
    for (const [id, values] of Object.entries(state.edgeBendPos)) edgeBendPos[id] = values.map(point).filter(Boolean);
    const matrix = state.graph?.get?.('group')?.getMatrix?.();
    const view = state.graphView;
    const edgeTypes = Object.fromEntries(Object.entries(state.edgeTypes).filter(([, type]) => ['line', 'polyline', 'quadratic', 'loop'].includes(type)));
    const autoRoutes={};
    for(const [id,route]of Object.entries(state.graphView.autoRoutes||{})) {
      const source=point(route.source),target=point(route.target),controlPoints=(route.controlPoints||[]).map(point).filter(Boolean);
      if(!source||!target||controlPoints.length!==1)continue;
      autoRoutes[id]={source,target,controlPoints};
      if(['screen-right','compact-arc','obstacle-aware'].includes(route.curvePolicy))autoRoutes[id].curvePolicy=route.curvePolicy;
    }
    return { trackedIds: [...state.tracked].sort(order), resolutionRevision: state.lastGraph.resolution_revision ?? null,
      positions, edgeLabelPos, edgeBendPos, edgeSnapshotLabels, edgeTypes,
      autoRoutes,
      layout: state.layout, edgeType: state.edgeType, edgeLabels: [...state.edgeLabels].sort(order),
      showIds: state.showIds, showPhotos: state.showPhotos, presentation: newGraphPresentation(state.presentation),
      graphView: { visibleIds: view.visibleIds ? [...view.visibleIds].sort(order) : null,
        root: view.root, direction: view.direction, labelMode: view.labelMode },
      camera: matrix ? Array.from(matrix) : [1, 0, 0, 0, 1, 0, 0, 0, 1],
      cameraOffset: cameraOffset(), cameraTransform: cameraTransform(state.graphCamera?.getTransform?.(), cameraOffset()) };
  }

  function syncControls() {
    syncEdgeTypeControl();
    for (const [selector, value] of [['#layoutSel', state.layout],
      ['#graphLabelMode', state.graphView.labelMode], ['#neighborAccount', state.graphView.root || ''],
      ['#neighborDirection', state.graphView.direction]]) {
      const node = $(selector); if (node && (!selector.startsWith('#neighbor') || !window.FundsNeighborhoodActions)) node.value = value;
    }
    if ($('#showIdCk')) $('#showIdCk').checked = state.showIds;
    if ($('#showPhotoCk')) $('#showPhotoCk').checked = state.showPhotos;
    $$('#edgeLabelGroup .edge-ck, #toolbarDisplayExtra .edge-ck').forEach(node => { node.checked = state.edgeLabels.has(node.dataset.em); });
    window.FundsRibbonCommands?.sync();
    window.FundsNeighborhoodActions?.update();
  }

  const mainAccountsBlocked = () => !!window.FundsImportWorkflow?.externalStatus?.(caseContext())?.length || !!window.FundsAccountActions?.blocked(caseContext()) || !!window.FundsMainAccountsActions?.blocked(caseContext());

  function update(status = history.status()) {
    const unavailable = status.busy || state.graphLoading || !state.caseId || !!layoutJob || !!gesture || mainAccountsBlocked();
    for (const [selector, available, label, name, shortcut] of [
      ['#btnUndo', status.canUndo, status.undoLabel, '撤销', 'Ctrl+Z'], ['#btnRedo', status.canRedo, status.redoLabel, '重做', 'Ctrl+Shift+Z'],
    ]) {
      const node = $(selector); if (!node) continue;
      node.disabled = unavailable || !available;
      node.title = `${name}（${shortcut}）${label ? '：' + label : ''}${status.message ? '；' + status.message : ''}`;
    }
    if (!bound) { publishMembership(); return; }
    $$(controls).forEach(node => { node.disabled = unavailable; });
    if ($('#btnNeighborhood')) $('#btnNeighborhood').disabled = unavailable || !state.lastGraph.nodes.length;
    if ($('#btnExpandNeighbors')) $('#btnExpandNeighbors').disabled = unavailable || !state.graphView.visibleIds || !state.graphView.remaining;
    if ($('#btnGraphOverview')) $('#btnGraphOverview').disabled = unavailable || (!state.graphView.visibleIds && !graphViewIsFiltered());
    $$('#flowTbody .act-track,#drawer .act-track').forEach(node => { node.disabled = unavailable; });
    const container = $('#graphContainer');
    if (container) container.style.pointerEvents = status.busy&&layoutJob?.phase!=='computing' ? 'none' : '';
    if ($('#btnFit')) $('#btnFit').disabled=!graphCameraInteractionAllowed();
    syncMinimapNavigation();
    for(const selector of ['#btnCanvasSave','#btnCanvasClone','#btnExportPng'])if($(selector))$(selector).disabled=unavailable;
    window.FundsCanvasActions?.sync();
    syncEdgeTypeControl();
    window.FundsRibbonCommands?.sync();
    publishMembership();
    window.FundsNeighborhoodActions?.update();
    window.FundsNodeFinderActions?.update();
  }

  function waitLayout(context) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + 30000;
      let timer;
      const done = (value, error) => {
        clearTimeout(timer); window.removeEventListener('funds:graph-metric', check);
        if (error) reject(error); else resolve(value);
      };
      function check() {
        clearTimeout(timer);
        if (!isCurrentCase(context)) return done(false);
        if (!layoutJob) return done(true);
        if (Date.now() >= deadline) {
          cancelGraphLayout({notify:false}); return done(false, new Error('布局未能完成，请重新布局后重试。'));
        }
        timer = setTimeout(check, 50);
      }
      window.addEventListener('funds:graph-metric', check);
      // Existing renderer zero-delay camera/save work must finish before capture-after.
      timer = setTimeout(check, 0);
    });
  }

  async function refreshRows(context) {
    try { await doSearch(state.search.page || 1); }
    catch (error) {
      if (isCurrentCase(context)) toast('画布已更新，流水表格刷新失败：' + error.message, 'err');
      return;
    }
  }

  function revealMembership(ids) {
    // Appending is a membership change, not a request to shrink the view.
    // The renderer records genuinely new accounts for explicit navigation.
    if(ids.size&&state.graphView.recentNodeIds?.size)updateGraphViewStatus();
  }

  function trackedWrite(context, write) {
    const record = { context, promise: null };
    // Retain the sent operation independently of any open table or history binding.
    record.promise = Promise.resolve().then(() => {
      if (isCurrentCase(context)) revokeGraphReads();
      return write();
    }).finally(() => {
      // Includes compensation writes: no read taken before or during the
      // transaction may restore membership that it has just replaced.
      if (isCurrentCase(context)) revokeGraphReads();
    });
    trackedWrites.add(record);
    return record.promise.finally(() => { trackedWrites.delete(record); });
  }
  const replaceTracked = (context, expected, desired) => trackedWrite(context, () => api('/api/track/state', { method: 'PUT',
    ...jsonOpts({ case_id: context.caseId, canvas_id: context.canvasId || 0,
      expected_ids: expected, desired_ids: desired }) }));

  function uncertain(context) {
    if (!isCurrentCase(context)) return;
    // Revoke the unconfirmed facts as well as their undo chain. Re-selecting the
    // canvas (or reloading) fetches authoritative state before editing resumes.
    activateCanvas(state.canvasId);
    toast('追踪状态无法确认，请重新加载当前画布。', 'err');
  }

  async function mutateTracked(context, write, prepare) {
    const before = capture();
    const previous = { forceFit: state.forceFit, justAdded: state.justAdded,
      lastGraph: state.lastGraph, mainAccounts: state.mainAccounts, focusNodes: state.focusNodes };
    let result;
    try { result = await write(before.trackedIds); }
    catch (error) {
      // HTTP rejections are explicit failures. A missing fetch/JSON receipt may
      // follow a committed write, so the old graph cannot remain authoritative.
      if (error.name === 'TypeError' || error.name === 'SyntaxError') uncertain(context);
      throw error;
    }
    if (!isCurrentCase(context)) return null;
    const afterIds = result.tracked_ids;
    if (!Array.isArray(afterIds)) {
      uncertain(context);
      throw new Error('追踪响应缺少完整集合，状态无法确认，请重新加载当前画布。');
    }
    try {
      if (prepare) prepare();
      await loadGraph(true, context);
      if (isCurrentCase(context) && JSON.stringify([...state.tracked].sort(order)) !== JSON.stringify([...afterIds].sort(order))) {
        throw new Error('刷新期间追踪集合已变化，无法确认本次操作。');
      }
      if (isCurrentCase(context)) await waitLayout(context);
    } catch (error) {
      if (!isCurrentCase(context)) return null;
      try { await replaceTracked(context, afterIds, before.trackedIds); }
      catch (_) {
        uncertain(context);
        throw new Error('追踪状态无法确认，请重新加载当前画布。');
      }
      if (!isCurrentCase(context)) return null;
      if (before.resolutionRevision !== (state.lastGraph.resolution_revision ?? null)) {
        // Compensation changes tracked membership only. It cannot undo newly
        // imported account evidence or make the previous graph authoritative.
        try {
          await loadGraph(false, context);
          if (isCurrentCase(context)) applyGraphOverview();
        } catch (_) { uncertain(context); }
        throw error;
      }
      // Failed reads leave the old drawing intact; layout/render failure can
      // occur after publication, so that case also redraws the old geometry.
      const published = state.lastGraph !== previous.lastGraph;
      for (const key of ['positions', 'edgeLabelPos', 'edgeBendPos', 'edgeSnapshotLabels', 'edgeTypes']) state[key] = copy(before[key]);
      Object.assign(state, previous);
      state.tracked = new Set(before.trackedIds);
      state.graphView.fullIndex = FundsGraphView.createIndex(previous.lastGraph);
      const view = state.graphView, saved = before.graphView;
      view.autoRoutes=copy(before.autoRoutes||{});
      view.visibleIds = saved.visibleIds ? new Set(saved.visibleIds) : null;
      view.root = saved.root; view.direction = saved.direction; view.labelMode = saved.labelMode;
      const adjacent = view.direction === 'in' ? view.fullIndex.incoming : view.direction === 'out' ? view.fullIndex.outgoing : view.fullIndex.neighbors;
      view.remaining = view.visibleIds ? [...(adjacent.get(view.root) || [])].filter(id => !view.visibleIds.has(id)).length : 0;
      if (published) {
        try {
          cancelGraphLayout(); state.forceFit = false; state.justAdded = null; state.forceRebuild = true;
          renderGraph(); state.forceRebuild = false;
          restoreCamera(before); state.graph?.paint();
          scheduleGraphViewport(); scheduleGraphLabels();
          state.forceFit = previous.forceFit; state.justAdded = previous.justAdded;
        } catch (_) {
          uncertain(context);
          throw new Error('追踪状态无法确认，请重新加载当前画布。');
        }
      }
      throw error;
    }
    if (!isCurrentCase(context)) return null;
    await refreshRows(context);
    return result;
  }

  async function restore(target, expected) {
    const context = caseContext();
    const selectedIds = new Set(state.graphSelection?.ids || []);
    if (!state.caseId || state.graphLoading) return false;
    const revision = state.lastGraph.resolution_revision ?? null;
    if (target.resolutionRevision !== revision || expected.resolutionRevision !== revision) {
      invalidateResolution(); return false;
    }
    try { await replaceTracked(context, expected.trackedIds, target.trackedIds); }
    catch (error) {
      if (error.name === 'TypeError' || error.name === 'SyntaxError') uncertain(context);
      throw error;
    }
    if (!isCurrentCase(context)) return false;
    let read;
    try {
      do { read = await readGraphData(false, context); }
      while (read && isCurrentCase(context) && read.sequence !== graphRequestSequence);
    }
    catch (error) {
      if (isCurrentCase(context)) {
        try {
          await replaceTracked(context, target.trackedIds, expected.trackedIds);
        } catch (_) {
          uncertain(context);
          throw new Error('恢复失败且未能确认追踪集合回滚，请重新打开当前画布。');
        }
      }
      throw error;
    }
    if (!isCurrentCase(context)) return false;
    if (!read) return false;
    const graph = read.graph;
    if (!Array.isArray(graph.tracked_ids)
      || JSON.stringify([...graph.tracked_ids].sort(order)) !== JSON.stringify([...target.trackedIds].sort(order))) {
      uncertain(context);
      return false;
    }
    try {
      if ((graph.resolution_revision ?? null) !== target.resolutionRevision) {
        await loadGraph(false, context);
        if (isCurrentCase(context)) { invalidateResolution(); applyGraphOverview(); }
        return false;
      }
      closeDrawer(); cancelGraphLayout(); resetGraphView();
      state.lastGraph = graph; state.tracked = new Set(graph.tracked_ids);
      state.mainAccounts = new Set(graph.main_accounts || []);
      state.focusNodes = new Map((graph.focus_nodes || []).map(value => [value.account, value.scale || 1.6]));
      for (const key of ['positions', 'edgeLabelPos', 'edgeBendPos', 'edgeSnapshotLabels', 'edgeTypes']) state[key] = copy(target[key] || {});
      for (const key of ['layout', 'edgeType', 'showIds', 'showPhotos']) state[key] = target[key];
      state.edgeLabels = new Set(target.edgeLabels);
      state.presentation = newGraphPresentation(target.presentation);
      const view = state.graphView, saved = target.graphView;
      view.autoRoutes=copy(target.autoRoutes||{});
      view.fullIndex = FundsGraphView.createIndex(graph);
      view.visibleIds = saved.visibleIds ? new Set(saved.visibleIds.filter(id => view.fullIndex.nodeById.has(id))) : null;
      view.root = saved.root; view.direction = saved.direction; view.labelMode = saved.labelMode;
      const adjacent = saved.direction === 'in' ? view.fullIndex.incoming : saved.direction === 'out' ? view.fullIndex.outgoing : view.fullIndex.neighbors;
      view.remaining = view.visibleIds ? [...(adjacent.get(saved.root) || [])].filter(id => !view.visibleIds.has(id)).length : 0;
      state.forceFit = false; state.justAdded = null; state.forceRebuild = true;
      renderGraph(); state.forceRebuild = false;
      restoreCamera(target);
      state.graphSelection?.rebind(selectedIds);
      state.graphSelection?.refresh();
      state.graph?.paint(); scheduleGraphViewport(); scheduleGraphLabels();
      syncControls(); saveEdgeLabels();
      preferenceStorage.setItem('fund_edge_type', state.edgeType);
      preferenceStorage.setItem('fund_show_ids', state.showIds ? '1' : '0');
      preferenceStorage.setItem('fund_show_photos', state.showPhotos ? '1' : '0');
      updateHeader(); updateGraphViewStatus();
      publishedGraphSequence = read.sequence;
      await refreshRows(context);
      return isCurrentCase(context);
    } catch (error) {
      // The tracked-set write is already committed. Revoke a partial display
      // instead of retaining an undo chain or overwriting a concurrent writer.
      uncertain(context);
      throw error;
    }
  }

  const history = window.FundsCanvasHistory.create({ capture, restore, onChange: update });

  function membershipSnapshot() {
    const current = isCurrentCase(membershipContext);
    const confirmed = new Set(current ? [...state.tracked].map(String) : []);
    const desired = new Set(confirmed), pending = new Set();
    if (current) {
      for (const id of membershipActive.keys()) pending.add(id);
      for (const [id, intent] of membershipDesired) {
        intent.included ? desired.add(id) : desired.delete(id);
        pending.add(id);
      }
    }
    const failures = {};
    if (current) for (const [id, failure] of membershipFailures) failures[id] = failure.message;
    return Object.freeze({ token: membershipToken, bindingToken: membershipToken,
      context: Object.freeze({ ...membershipContext }),
      confirmedIds: Object.freeze([...confirmed].sort(order)), desiredIds: Object.freeze([...desired].sort(order)),
      pendingIds: Object.freeze([...pending].sort(order)), failures: Object.freeze(failures),
      busy: current && (membershipDraining || history.status().busy),
      available: current && !!state.caseId && !state.graphLoading && !!state.graph && !gesture && !mainAccountsBlocked() });
  }

  function publishMembership() {
    for (const listener of membershipListeners) {
      try { listener(membershipSnapshot()); } catch (_) { /* The view does not own commands. */ }
    }
  }

  function finishMembershipWaiter(waiter, status, error) {
    if (waiter.done) return;
    waiter.done = true;
    waiter.resolve({ ok: status === 'confirmed', status, ids: [...waiter.ids], ...(error ? { error } : {}) });
  }

  function settleMembershipWaiters() {
    for (const waiter of membershipWaiters) {
      if (waiter.done) continue;
      if (waiter.token !== membershipToken || !isCurrentCase(waiter.context)) {
        finishMembershipWaiter(waiter, 'stale', '案件或画布已切换，未发送的上图操作已取消。');
        continue;
      }
      const failure = waiter.ids.map(id => membershipFailures.get(id))
        .find(value => value && value.sequence >= waiter.sequence);
      if (failure) { finishMembershipWaiter(waiter, 'failed', failure.message); continue; }
      if (waiter.ids.some(id => membershipDesired.has(id) || membershipActive.has(id))) continue;
      if (waiter.ids.every(id => state.tracked.has(Number(id)) === waiter.included)) {
        finishMembershipWaiter(waiter, 'confirmed');
      }
    }
    membershipWaiters = membershipWaiters.filter(waiter => !waiter.done);
  }

  function invalidateMembership() {
    for (const waiter of membershipWaiters) {
      finishMembershipWaiter(waiter, 'stale', '案件或画布已切换，未发送的上图操作已取消。');
    }
    membershipToken += 1;
    membershipContext = caseContext(); membershipDesired = new Map(); membershipActive = new Map();
    membershipFailures = new Map(); membershipWaiters = []; membershipDraining = false;
  }

  async function drainMembership() {
    if (membershipDraining || !membershipDesired.size) return;
    const token = membershipToken, context = membershipContext;
    membershipDraining = true; publishMembership();
    try {
      while (token === membershipToken && isCurrentCase(context) && membershipDesired.size) {
        let batch = [];
        try {
          // History owns serialization against layout, undo and the existing commands.
          // The batch is captured when its turn starts, so unsent intermediate intents
          // collapse to the latest value without bypassing that command boundary.
          await history.run('更新流水上图状态', async () => {
            if (token !== membershipToken || !isCurrentCase(context)) return;
            if (state.graphLoading || gesture || layoutJob || mainAccountsBlocked()) throw new Error('画布尚未就绪，请稍后重试。');
            for (const [id, intent] of membershipDesired) {
              if (state.tracked.has(Number(id)) === intent.included) membershipDesired.delete(id);
              else batch.push([id, intent]);
            }
            if (!batch.length) return;
            membershipActive = new Map(batch); publishMembership();
            const before = capture();
            const result = await mutateTracked(context, expected => {
              const desired = new Set(expected);
              for (const [id, intent] of batch) intent.included ? desired.add(Number(id)) : desired.delete(Number(id));
              return replaceTracked(context, expected, [...desired].sort(order));
            }, () => {
              state.positions = copy(before.positions);
              // Keep the complete camera, including offscreen additions.
              state.forceFit = false; state.justAdded = null;
            });
            if (!result || token !== membershipToken || !isCurrentCase(context)) return;
            try {
              if (before.resolutionRevision === (state.lastGraph.resolution_revision ?? null)) restoreCamera(before);
              revealMembership(new Set(batch.filter(([, intent]) => intent.included).map(([id]) => Number(id))));
              state.graph?.paint(); scheduleGraphViewport(); scheduleGraphLabels();
            } catch (error) { uncertain(context); throw error; }
            for (const [id, intent] of batch) {
              if (membershipDesired.get(id) === intent) membershipDesired.delete(id);
              membershipFailures.delete(id);
            }
          });
        } catch (error) {
          if (token !== membershipToken || !isCurrentCase(context)) break;
          // An explicit failure rolls back only this flight. Later intent remains
          // queued; successfully confirmed changes from earlier flights stay intact.
          const failed = batch.length ? batch : [...membershipDesired];
          for (const [id, intent] of failed) {
            if (membershipDesired.get(id) !== intent) continue;
            membershipDesired.delete(id);
            membershipFailures.set(id, { sequence: intent.sequence, message: error.message || '上图操作失败，请重试。' });
          }
        }
        if (token !== membershipToken || !isCurrentCase(context)) break;
        membershipActive = new Map(); settleMembershipWaiters(); publishMembership();
      }
    } finally {
      if (token === membershipToken) {
        membershipActive = new Map(); membershipDraining = false;
        settleMembershipWaiters(); publishMembership();
      }
    }
  }

  function setMembership(ids, included) {
    if (!Array.isArray(ids) || typeof included !== 'boolean') {
      return Promise.resolve({ ok: false, status: 'failed', ids: [], error: '上图操作需要流水ID数组和明确的勾选状态。' });
    }
    const normalized = [...new Set(Array.from(ids, id => {
      if ((typeof id !== 'number' && typeof id !== 'string') || !/^\d+$/.test(String(id))) return null;
      const value = Number(id);
      return Number.isSafeInteger(value) && value > 0 ? String(value) : null;
    }))];
    if (normalized.includes(null)) {
      return Promise.resolve({ ok: false, status: 'failed', ids: [], error: '流水ID无效，未发送上图操作。' });
    }
    const snapshot = membershipSnapshot();
    if (!snapshot.available || (layoutJob && !history.status().busy)) {
      return Promise.resolve({ ok: false, status: 'unavailable', ids: normalized, error: '当前画布尚未就绪，请稍后重试。' });
    }
    if (!normalized.length) return Promise.resolve({ ok: true, status: 'confirmed', ids: [] });
    const sequence = ++membershipSequence, changed = new Set(normalized);
    for (const waiter of membershipWaiters) {
      if (waiter.included !== included && waiter.ids.some(id => changed.has(id))) finishMembershipWaiter(waiter, 'superseded');
    }
    for (const id of normalized) {
      membershipDesired.set(id, { included, sequence }); membershipFailures.delete(id);
    }
    const result = new Promise(resolve => {
      membershipWaiters.push({ token: membershipToken, context: membershipContext, sequence,
        ids: normalized, included, resolve, done: false });
    });
    settleMembershipWaiters(); publishMembership(); void drainMembership();
    return result;
  }

  const membership = Object.freeze({ set: setMembership, snapshot: membershipSnapshot, subscribe(listener) {
    if (typeof listener !== 'function') throw new TypeError('Membership subscription requires a function.');
    membershipListeners.add(listener);
    try { listener(membershipSnapshot()); } catch (_) { /* Isolate view observers. */ }
    return () => { membershipListeners.delete(listener); };
  } });
  function invalidateResolution() {
    gesture = null;
    history.invalidate();
    syncControls();
  }
  window.FundsCanvasEditor = Object.freeze({ history, membership, command, syncControls, invalidateResolution, captureDisplay: () => { const {trackedIds, ...display} = capture(); return display; }, beginDisplayGesture, finishDisplayGesture });

  const readGraph = loadGraph;
  loadGraph = async function (retry = true, context = caseContext()) {
    // A -> B -> A must read after any already-sent old A write settles. Its old
    // callback still cannot publish into the newly created binding generation.
    const previous = [...trackedWrites].filter(record => record.context.caseId === context.caseId
      && record.context.canvasId === context.canvasId
      && (record.context.generation !== context.generation || record.context.canvasGeneration !== context.canvasGeneration));
    if (previous.length) await Promise.allSettled(previous.map(record => record.promise));
    if (!isCurrentCase(context)) return;
    const result = await readGraph.call(this, retry, context);
    if (isCurrentCase(context)) publishMembership();
    return result;
  };

  function reset() { gesture = null; invalidateMembership(); history.reset(caseContext()); syncControls(); }
  for (const [name, original] of [['selectCase', selectCase], ['activateCanvas', activateCanvas]]) {
    const wrapped = function (...args) { const result = original.apply(this, args); reset(); return result; };
    if (name === 'selectCase') selectCase = wrapped; else activateCanvas = wrapped;
  }

  function command(label, action) {
    const context = caseContext();
    if (history.status().busy || gesture || state.graphLoading || !state.caseId || layoutJob || mainAccountsBlocked()) {
      syncControls(); return Promise.resolve(false);
    }
    return history.run(label, async () => {
      if (!isCurrentCase(context)) return;
      await action(context);
      if (isCurrentCase(context)) await waitLayout(context);
    }).catch(error => { if (isCurrentCase(context)) toast(error.message || '画布操作失败', 'err'); return false; });
  }

  function track(ids, remove) {
    if (!ids?.length) { if (!remove) toast('未选择流水', 'err'); return Promise.resolve(false); }
    ids = [...ids];
    return command(remove ? '移除流水' : '流水上图', async context => {
      const result = await mutateTracked(context, before => {
        const desired = new Set(before);
        for (const id of ids) remove ? desired.delete(id) : desired.add(id);
        return replaceTracked(context, before, [...desired].sort(order));
      }, () => { if (!remove) { state.justAdded = new Set(ids); state.forceFit = false; } });
      if (result && isCurrentCase(context)) {
        if (remove) closeDrawer();
        toast(remove ? `已移除 ${ids.length} 笔` : `已上图 ${ids.length} 笔流水`);
      }
    });
  }
  trackFlows = ids => track(ids, false);
  untrackFlows = ids => track(ids, true);
  const neighborhood = exploreNeighborhood, overview = showGraphOverview;
  exploreNeighborhood = async (...args) => {
    let applied = false;
    const accepted = await command('查看账号邻域', async () => { applied = await neighborhood(...args) === true; });
    return accepted === true && applied;
  };
  showGraphOverview = (...args) => command('显示完整图谱', () => overview(...args));

  function homePageActive() {
    const page = $('#graphPage');
    return !page?.inert && !page?.classList.contains('page-inactive');
  }

  async function travel(redo) {
    if (!homePageActive() || history.status().busy || gesture || state.graphLoading || layoutJob || mainAccountsBlocked()) return false;
    const context = caseContext();
    try { return await (redo ? history.redo() : history.undo()); }
    catch (error) { if (isCurrentCase(context)) toast('无法恢复画布：' + error.message, 'err'); return false; }
  }

  function wrap(selector, property, label) {
    $$(selector).forEach(node => {
      const original = node[property]; if (typeof original !== 'function') return;
      node[property] = function (event) {
        const value = node.value, checked = node.checked;
        return command(label, async () => {
          node.value = value; node.checked = checked;
          await original.call(node, event);
        });
      };
    });
  }

  const geometry = snapshot => JSON.stringify([snapshot.positions, snapshot.edgeLabelPos, snapshot.edgeBendPos, snapshot.edgeSnapshotLabels,snapshot.autoRoutes,snapshot.presentation]);
  // Display gestures share the normal command lock and snapshot boundary.
  // The token prevents a late pointer from finishing another binding's gesture.
  function beginDisplayGesture() {
    if (gesture || history.status().busy || state.graphLoading || !state.caseId || layoutJob || mainAccountsBlocked()) return null;
    const token = {};
    gesture = { context: caseContext(), before: capture(), token };
    update();
    return token;
  }
  function finishDisplayGesture(token, label, commit = true) {
    if (!token || gesture?.token !== token) return false;
    if (!commit) { gesture = null; update(); return false; }
    endGesture(label);
    return true;
  }
  function startGesture(event) {
    // Node gestures are captured only after crossing the drag threshold. A
    // selection click must not traverse the entire graph or create undo work.
    if (event.button !== undefined && event.button !== 0) return;
    // Native blank-canvas input belongs to the camera while computation runs.
    // Custom node/edge gestures still hit the geometry/history lock below.
    if(event.type==='mousedown'&&layoutJob?.phase==='computing'&&!state.graphLoading)return;
    if (history.status().busy || state.graphLoading || layoutJob) {
      event.preventDefault(); event.stopImmediatePropagation(); return;
    }
    if (state.graphSelection && event.type === 'mousedown') return;
    gesture = { context: caseContext(), before: capture() };
  }
  function endGesture(label = '调整节点或连线') {
    const current = gesture; gesture = null;
    if (current && isCurrentCase(current.context) && !history.status().busy) {
      const after = capture();
      if (geometry(current.before) !== geometry(after)) history.record(label, current.before, after);
    }
    update();
  }

  window.addEventListener('DOMContentLoaded', () => {
    bound = true;
    $('#btnUndo').onclick = () => travel(false); $('#btnRedo').onclick = () => travel(true);
    wrap('#layoutSel', 'onchange', '更改布局'); wrap('#btnRelayout', 'onclick', '重新布局');
    wrap('#btnArrangeLabels','onclick','按文字整理布局');
    // Selected-edge line type owns its command; wrapping it again nests history.
    wrap('#edgeLabelGroup .edge-ck, #toolbarDisplayExtra .edge-ck', 'onchange', '更改连线标注');
    wrap('#showIdCk', 'onchange', '切换证件号显示'); wrap('#showPhotoCk', 'onchange', '切换照片显示');
    wrap('#graphLabelMode', 'onchange', '更改标注模式');
    $('#btnBulkTrack').onclick = () => {
      const ids = $$('#flowTbody .flow-ck').filter(node => node.checked).map(node => Number(node.dataset.id));
      if (!ids.length) { toast('请先勾选要追踪的流水', 'err'); return Promise.resolve(false); }
      const direction = $('#bulkDir').value, x = Math.max(1, Math.min(50, Number($('#bulkX').value) || 5));
      return command('批量追踪', async context => {
        const result = await mutateTracked(context, before => trackedWrite(context, () => api('/api/track/bulk', { method: 'POST', ...jsonOpts({ case_id: context.caseId,
          canvas_id: context.canvasId || 0, flow_ids: ids, direction, x, expected_ids: before }) })), () => {
          state.justAdded = new Set(ids); state.forceFit = false;
        });
        if (result && isCurrentCase(context)) {
          const dirLabel = direction === 'source' ? '来源' : '去向';
          const notMain = (result.skipped_not_main || []).length;
          const zero = (result.detail || []).filter(item => item.picked === 0 && item.is_main).length;
          let message = `批量追踪完成：${result.anchors} 笔交易及其${dirLabel}流水共上图 ${result.tracked} 笔`;
          if (notMain) message += `；${notMain} 笔被跳过（${dirLabel === '来源' ? '付款' : '收款'}方不是主账号，无法追踪）`;
          if (zero) message += `；${zero} 笔锚点在本方账单中无${dirLabel}流水（仅上图了本笔）`;
          toast(message);
        }
      });
    };
    $('#btnClearGraph').onclick = () => {
      const where = state.canvasName ? `画布「${state.canvasName}」` : '默认画布';
      if (!confirm(`清空${where}的全部追踪（已上图流水）？其他画布不受影响。`)) return Promise.resolve(false);
      return command('清空画布', async context => {
        const result = await mutateTracked(context, before => replaceTracked(context, before, []), () => {
          state.positions = {}; state.edgeLabelPos = {}; state.edgeBendPos = {}; state.edgeSnapshotLabels = {}; state.edgeTypes = {};state.graphView.autoRoutes={};
        });
        if (result && isCurrentCase(context)) toast(`${where}已清空`);
      });
    };
    const container = $('#graphContainer');
    container.addEventListener('mousedown', startGesture, true);
    container.addEventListener('dblclick', event => {
      if (history.status().busy || state.graphLoading || layoutJob) return;
      const context = caseContext(), before = capture();
      Promise.resolve().then(() => {
        if (!isCurrentCase(context) || history.status().busy) return;
        const after = capture();
        if (geometry(before) !== geometry(after)) history.record('调整连线折点', before, after);
      });
    }, true);
    window.addEventListener('mouseup', () => {
      // Window listeners registered at pointer-down run after this listener.
      // Let the selection controller commit its preview before ending history.
      if (state.graphSelection?.status?.()?.dragging || gesture?.token) return;
      Promise.resolve().then(() => endGesture());
    });
    window.addEventListener('funds:selection-drag-start', startGesture);
    window.addEventListener('funds:edge-drag-start', startGesture);
    window.addEventListener('funds:selection-drag-end', () => endGesture('移动选中节点'));
    window.addEventListener('funds:edge-drag-end', () => endGesture('调整连线或标注'));
    window.addEventListener('keydown', event => {
      if (!homePageActive() || !(event.ctrlKey || event.metaKey) || event.altKey || event.defaultPrevented || window.FundsToolbarMenu?.isOpen()) return;
      const target = event.target;
      if (target?.closest?.('input,select,textarea,[contenteditable]:not([contenteditable="false"])')) return;
      if ($$('.modal,dialog,[role="dialog"]').some(node => node.open || (!node.hidden && node.style.display !== 'none' && node.getClientRects().length))) return;
      // 焦点在图谱容器上只是常见情形，不是必要条件：文档承诺"对话框关闭且焦点不在
      // 输入控件内"即可撤销。原先额外要求容器持有焦点，会让刚点过功能区或侧栏后
      // 按下的 Ctrl+Z 静默失效（例如双击进邻域后焦点落到按钮上就撤不回去）。
      // 文本编辑与打开的对话框仍由上面的两个判断保留原生键盘行为。
      const key = event.key.toLowerCase();
      if (key !== 'z' && key !== 'y') return;
      event.preventDefault(); travel(key === 'y' || event.shiftKey);
    });
    window.addEventListener('funds:graph-metric', () => update());
    window.addEventListener('funds:main-accounts-state', () => update());
    window.addEventListener('funds:account-state', () => update());
    window.addEventListener('funds:import-state', () => update());
    reset();
  });
})();
