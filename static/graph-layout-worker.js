/* Serial display-geometry tasks. Cancellation belongs to the host's Worker lifetime. */
'use strict';

(function (root) {
  const validId = value => typeof value === 'string' && value.length > 0 || typeof value === 'number' && Number.isFinite(value);
  function load(task, prepareRoutes = false) {
    const urls = [];
    if (task === 'layout') {
      if (!root.FundsGraphLayout) urls.push('/static/graph-layout.js?v=layout-pipeline-1');
      if (!root.FundsGraphLayoutQuality) urls.push('/static/graph-layout-quality.js?v=quality-1');
    }
    if ((task === 'routes' || prepareRoutes) && !root.FundsGraphRouting) urls.push('/static/graph-routing.js?v=compact-arcs-1');
    if (urls.length) root.importScripts(...urls);
  }
  function handleMessage(event) {
    const message = event?.data, source = message && typeof message === 'object' && !Array.isArray(message) ? message : {};
    const { protocol, requestId, taskId, task, input, prepareRoutes } = source;
    const response = { protocol, requestId, taskId, task };
    try {
      if (protocol !== 1) throw new Error('unsupported graph worker protocol');
      if (!validId(requestId) || !validId(taskId)) throw new Error('graph worker requires requestId and taskId');
      if (task !== 'layout' && task !== 'routes') throw new Error('unknown graph worker task');
      if (!input || typeof input !== 'object' || Array.isArray(input) || !Array.isArray(input.nodes) ||
          input.edges !== undefined && !Array.isArray(input.edges)) throw new Error('invalid graph worker input');
      load(task, prepareRoutes === true);
      if (task === 'layout') {
        const simulation = root.FundsGraphLayout.simulation(input);
        while (simulation.step()) { /* The host terminates obsolete jobs instead of queuing cancellation. */ }
        const result = simulation.result();
        const quality = root.FundsGraphLayoutQuality.evaluate(input, result, { fitViewport: root.FundsGraphLayout.fitViewport });
        root.postMessage({ ...response, result, quality });
      } else root.postMessage({ ...response, proposal: root.FundsGraphRouting.plan(input) });
    } catch (error) {
      root.postMessage({ ...response, error: String(error?.message || error) });
    } finally {
      // The legacy layout kernel installs its own handler while importing.
      // Restore this protocol even if a later import or task throws.
      root.onmessage = handleMessage;
    }
  }
  root.onmessage = handleMessage;
})(self);
