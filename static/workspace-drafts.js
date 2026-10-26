/* Session-only UI drafts. This store has no persistence or graph-write actions.
 * Authoritative graph facts must be loaded for the current binding before use. */
(function (root) {
  'use strict';

  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const point = value => object(value) && finite(value.x) && finite(value.y) ? { x: value.x, y: value.y } : null;
  const ids = value => Array.isArray(value) ? [...new Set(value.filter(item => typeof item === 'string' && item !== ''))] : [];
  // Legacy callers may already have safe numeric case/canvas IDs. Never parse a
  // string ID as Number, and reject a numeric ID whose precision is already lost.
  const scopeId = value => typeof value === 'string' && value !== '' ? value
    : Number.isSafeInteger(value) && value >= 0 ? String(value) : null;
  const clone = value => Array.isArray(value) ? value.map(clone)
    : object(value) ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, clone(item)])) : value;

  function map(input, sanitize) {
    if (!object(input)) return {};
    const result = [];
    for (const [key, value] of Object.entries(input)) {
      if (!key) continue;
      const safe = sanitize(value);
      if (safe !== null) result.push([key, safe]);
    }
    // Account IDs such as "__proto__" remain ordinary own keys.
    return Object.fromEntries(result);
  }

  function labelGeometry(value) {
    if (!object(value)) return null;
    const safe = {};
    for (const key of ['lx', 'ly', 'bw', 'bh']) if (finite(value[key])) safe[key] = value[key];
    if (typeof value.manual === 'boolean') safe.manual = value.manual;
    return Object.keys(safe).length ? safe : null;
  }

  function autoRoute(value) {
    if (!object(value)) return null;
    const source = point(value.source), target = point(value.target);
    const controlPoints = Array.isArray(value.controlPoints) ? value.controlPoints.map(point).filter(Boolean) : [];
    if (!source || !target || controlPoints.length !== 1) return null;
    const result = { source, target, controlPoints };
    if (['screen-right', 'compact-arc', 'obstacle-aware'].includes(value.curvePolicy)) result.curvePolicy = value.curvePolicy;
    return result;
  }

  function queryDraft(input) {
    const source = object(input) ? input : {}, result = {};
    for (const key of ['account', 'name', 'counter', 'kw', 'min_amount', 'max_amount', 'tfrom', 'tto', 'bulkCount']) {
      if (typeof source[key] === 'string') result[key] = source[key];
    }
    for (const key of ['page', 'size']) if (Number.isSafeInteger(source[key]) && source[key] > 0) result[key] = source[key];
    if (typeof source.tracked === 'boolean') result.tracked = source.tracked;
    if (typeof source.accountExact === 'boolean') result.accountExact = source.accountExact;
    if (Array.isArray(source.anchorIds)) result.anchorIds = ids(source.anchorIds);
    if (object(source.viewport)) {
      const view = source.viewport;
      if (typeof view.rowId === 'string' && view.rowId !== '' && Number.isSafeInteger(view.page) && view.page > 0
          && finite(view.offset) && finite(view.scrollLeft) && view.scrollLeft >= 0) {
        result.viewport = { rowId: view.rowId, page: view.page, offset: view.offset, scrollLeft: view.scrollLeft };
      }
    }
    if (['source', 'target'].includes(source.bulkDir)) result.bulkDir = source.bulkDir;
    if (['compact', 'comfortable'].includes(source.density)) result.density = source.density;
    if (['single', 'wrap'].includes(source.wrap)) result.wrap = source.wrap;
    return result;
  }

  function sanitize(input) {
    const source = object(input) ? input : {};
    const result = {
      positions: map(source.positions, point),
      edgeLabelPos: map(source.edgeLabelPos, value => finite(value) && value >= 0 && value <= 1 ? value : null),
      edgeBendPos: map(source.edgeBendPos, value => Array.isArray(value) ? value.map(point).filter(Boolean) : null),
      edgeSnapshotLabels: map(source.edgeSnapshotLabels, labelGeometry),
      edgeTypes: map(source.edgeTypes, value => ['line', 'polyline', 'quadratic', 'loop'].includes(value) ? value : null),
      autoRoutes: map(source.autoRoutes, autoRoute),
    };
    if (['auto', 'force', 'structural', 'dagre', 'partition'].includes(source.layout)) result.layout = source.layout;
    if (['quadratic', 'line', 'polyline'].includes(source.edgeType)) result.edgeType = source.edgeType;
    if (Array.isArray(source.edgeLabels)) result.edgeLabels = [...new Set(source.edgeLabels.filter(value => ['money', 'time', 'summary', 'currency'].includes(value)))];
    for (const key of ['showIds', 'showPhotos']) if (typeof source[key] === 'boolean') result[key] = source[key];
    if (object(source.presentation)) {
      if (typeof root.FundsGraphPresentation?.create !== 'function') throw new Error('工作草稿需要先加载图谱显示设置模块');
      result.presentation = clone(root.FundsGraphPresentation.create(source.presentation));
    }
    if (object(source.graphView)) {
      const view = source.graphView, safe = {};
      if (view.visibleIds === null || Array.isArray(view.visibleIds)) safe.visibleIds = view.visibleIds === null ? null : ids(view.visibleIds);
      if (view.root === null || typeof view.root === 'string') safe.root = view.root;
      if (['both', 'in', 'out'].includes(view.direction)) safe.direction = view.direction;
      if (['auto', 'focus', 'all'].includes(view.labelMode)) safe.labelMode = view.labelMode;
      result.graphView = safe;
    }
    if (Array.isArray(source.camera) && source.camera.length === 9 && source.camera.every(finite)) result.camera = source.camera.slice();
    if (point(source.cameraOffset)) result.cameraOffset = point(source.cameraOffset);
    if (point(source.cameraTransform) && finite(source.cameraTransform.scale) && source.cameraTransform.scale > 0) {
      result.cameraTransform = { scale: source.cameraTransform.scale, ...point(source.cameraTransform) };
    }
    if (object(source.query)) result.query = queryDraft(source.query);
    if (object(source.selection)) {
      const selection = source.selection, safe = {};
      if (Array.isArray(selection.nodeIds)) safe.nodeIds = ids(selection.nodeIds);
      for (const key of ['account', 'edgeKey']) if (selection[key] === null || typeof selection[key] === 'string') safe[key] = selection[key];
      result.selection = safe;
    }
    if (Array.isArray(source.nodeDrafts)) {
      result.nodeDrafts = source.nodeDrafts.filter(value => object(value) && typeof value.account === 'string' && value.account !== '')
        .map(value => {
          const draft = { account: value.account };
          if (typeof value.idValue === 'string') draft.idValue = value.idValue;
          if (finite(value.focusValue) && value.focusValue >= 1 && value.focusValue <= 4) draft.focusValue = value.focusValue;
          return draft;
        }).filter(value => Object.keys(value).length > 1);
    }
    return result;
  }

  function create() {
    const cases = new Map();
    function capture(context, value) {
      const caseId = scopeId(context?.caseId), canvasId = scopeId(context?.canvasId);
      if (caseId === null || canvasId === null) throw new TypeError('工作草稿必须绑定明确的案件与画布');
      const draft = sanitize(value);
      let canvases = cases.get(caseId);
      if (!canvases) { canvases = new Map(); cases.set(caseId, canvases); }
      // Insertion order records the most recently captured surviving canvas.
      canvases.delete(canvasId); canvases.set(canvasId, draft);
      return true;
    }
    function get(caseId, canvasId) {
      const draft = cases.get(scopeId(caseId))?.get(scopeId(canvasId));
      return draft ? clone(draft) : null;
    }
    function lastCanvas(caseId) {
      const canvases = cases.get(scopeId(caseId));
      return canvases?.size ? [...canvases.keys()].at(-1) : null;
    }
    function removeCanvas(caseId, canvasId) {
      const key = scopeId(caseId), canvases = cases.get(key);
      if (!canvases?.delete(scopeId(canvasId))) return false;
      if (!canvases.size) cases.delete(key);
      return true;
    }
    return Object.freeze({ capture, get, lastCanvas, retainedCaseIds: () => [...cases.keys()],
      removeCase: caseId => cases.delete(scopeId(caseId)), removeCanvas, clear: () => cases.clear() });
  }

  root.FundsWorkspaceDrafts = Object.freeze({ create });
})(typeof window === 'undefined' ? globalThis : window);
