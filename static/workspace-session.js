/* Session drafts retain editing intent only. Graph facts are always reread from
 * the selected case/canvas; returning never replays a write or a saved receipt. */
(function () {
  'use strict';
  const drafts = window.FundsWorkspaceDrafts.create();
  const fields = { account:'fAccount', name:'fName', counter:'fCounter', kw:'fKw', min_amount:'fMin', max_amount:'fMax', tfrom:'fFrom', tto:'fTo', bulkDir:'bulkDir', bulkCount:'bulkX', density:'flowTableDensity', wrap:'flowTableWrap' };
  const defaults = Object.fromEntries(Object.entries(fields).map(([key,id])=>[key,$('#'+id)?.value || '']));
  function resetQuery() {
    for (const [key,id] of Object.entries(fields)) if ($('#'+id)) $('#'+id).value=defaults[key];
    $('#fTracked').checked=false;
    if ($('#fAccount')?.dataset) delete $('#fAccount').dataset.exactAccount;
  }
  let restoring = false;
  function capture() {
    if (restoring || !state.caseId || state.graphLoading || document.querySelector('#graphPage')?.inert) return false;
    captureNodeDraft();
    const query = Object.fromEntries(Object.entries(fields).map(([key,id]) => [key,$('#'+id)?.value || '']));
    Object.assign(query, { tracked:$('#fTracked').checked, page:state.search.page, size:state.search.size,
      anchorIds:window.FundsTableDrafts?.capture() || [], accountExact:$('#fAccount').dataset.exactAccount===$('#fAccount').value && !!$('#fAccount').value });
    const inputs = new Map(retainedNodeInputs);
    for (const [key,draft] of nodeDrafts) if (nodeDraftDirty(draft)) {
      const account = JSON.parse(key).at(-1);
      inputs.set(account,{account,idValue:draft.idValue,focusValue:draft.focusValue});
    }
    return drafts.capture(caseContext(), { ...window.FundsCanvasEditor.captureDisplay(), query,
      resolutionRevision: state.lastGraph.resolution_revision ?? null,
      selection:{nodeIds:[...(state.graphSelection?.ids || [])],edgeKey:state.graphView.selectedEdge,account:state.graphView.selectedAccount},
      nodeDrafts:[...inputs.values()] });
  }
  function restore() {
    const draft = drafts.get(state.caseId,state.canvasId);
    if (!draft || state.graphLoading) return false;
    restoring = true;
    try {
      cancelGraphLayout();
      const nodes = new Set(state.lastGraph.nodes.map(node => String(node.account ?? node.id)));
      state.positions = Object.fromEntries(Object.entries(draft.positions).filter(([id]) => nodes.has(id)));
      for (const key of ['edgeLabelPos','edgeBendPos','edgeSnapshotLabels','edgeTypes']) state[key] = draft[key];
      for (const key of ['layout','edgeType','showIds','showPhotos']) state[key] = draft[key];
      state.edgeLabels = new Set(draft.edgeLabels);
      const view = state.graphView, saved = draft.graphView, presentation = newGraphPresentation(draft.presentation);
      const sameResolution = Object.prototype.hasOwnProperty.call(draft, 'resolutionRevision')
        && draft.resolutionRevision === (state.lastGraph.resolution_revision ?? null);
      const identityFiltered = presentation.hiddenAccounts.length > 0 || presentation.onlyAccounts !== null;
      const expiredScope = (!sameResolution && (saved.visibleIds !== null || identityFiltered))
        || (saved.visibleIds !== null && !nodes.has(saved.root));
      state.presentation = newGraphPresentation(expiredScope ? {
        ...presentation, relationFilter: {}, hiddenAccounts: [], onlyAccounts: null,
        timeWindow: { start: '', end: '', includeUndated: true },
      } : presentation);
      view.autoRoutes = draft.autoRoutes;
      view.visibleIds = expiredScope ? null : saved.visibleIds ? new Set(saved.visibleIds.filter(id => nodes.has(id))) : null;
      view.root = expiredScope ? null : nodes.has(saved.root) ? saved.root : null;
      view.direction = expiredScope ? 'both' : saved.direction; view.labelMode = saved.labelMode;
      state.forceFit = expiredScope; state.forceRebuild = true; state.justAdded = null;
      renderGraph();
      // Invalidates the opening snapshot's delayed fit, while respecting chrome
      // resizing already accounted for by the shared camera controller.
      state.graphCamera?.cancelFocus?.('workspace-restore');
      const matrix = expiredScope ? null : draft.camera?.slice(), current = state.graphCamera?.getTransform?.() || {scale:1,x:0,y:0};
      const captured = draft.cameraTransform || {scale:1,...draft.cameraOffset};
      if (matrix && captured.scale > 0) {
        const scale = current.scale/captured.scale;
        for (const index of [0,1,3,4]) matrix[index] *= scale;
        matrix[6] = (matrix[6]-captured.x)*scale+current.x;
        matrix[7] = (matrix[7]-captured.y)*scale+current.y;
        state.graph?.get?.('group')?.setMatrix?.(matrix);
      }
      state.graphSelection?.rebind(expiredScope ? [] : draft.selection.nodeIds.filter(id => nodes.has(id)));
      state.graphView.selectedAccount = !expiredScope && nodes.has(draft.selection.account) ? draft.selection.account : null;
      state.graphView.selectedEdge = !expiredScope && state.graphView.edgeItems.has(draft.selection.edgeKey) ? draft.selection.edgeKey : null;
      if (state.graphView.selectedEdge && !state.graphSelection?.ids.size) {
        selectItem(state.graphView.edgeItems.get(state.graphView.selectedEdge)); refreshSelectedEdgeAppearance();
      }
      retainedNodeInputs.clear();
      for (const input of draft.nodeDrafts) retainedNodeInputs.set(input.account,input);
      for (const [key,id] of Object.entries(fields)) if (draft.query[key] != null && $('#'+id)) $('#'+id).value = draft.query[key];
      $('#fTracked').checked = draft.query.tracked;
      if ($('#fAccount')?.dataset) { if (draft.query.accountExact) $('#fAccount').dataset.exactAccount=$('#fAccount').value; else delete $('#fAccount').dataset.exactAccount; }
      state.search.page = draft.query.page || 1;
      window.FundsTableDrafts?.restore(draft.query.anchorIds);
      for (const id of ['flowTableDensity','flowTableWrap']) $('#'+id)?.dispatchEvent(new Event('change'));
      window.FundsCanvasEditor.syncControls();
      state.graph?.paint(); rebuildGraphViewport(); scheduleGraphViewport(); scheduleGraphLabels();
      return true;
    } finally { restoring = false; }
  }
  // Capture before table-workspace invalidates its query selection and rows.
  const originalSelect = selectCase, originalActivate = activateCanvas;
  selectCase = function (...args) { capture(); const result=originalSelect.apply(this,args); resetQuery(); return result; };
  activateCanvas = function (...args) { capture(); const result=originalActivate.apply(this,args); resetQuery(); return result; };
  window.FundsWorkspaceSession = Object.freeze({ capture,restore,
    lastCanvas:caseId => drafts.lastCanvas(caseId),retainedCaseIds:()=>drafts.retainedCaseIds(),
    removeCase:caseId=>drafts.removeCase(caseId),removeCanvas:(caseId,canvasId)=>drafts.removeCanvas(caseId,canvasId) });
})();
