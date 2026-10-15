/* Display-only graph policy. Raw transaction/amount values are never transformed. */
(function (global) {
  'use strict';

  const cachedOrder = new WeakMap();
  const compareId = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const accountId = node => typeof node.account === 'string' ? node.account
    : typeof node.id === 'string' ? node.id : null;
  const edgeKey = edge => edge.source + '=>' + edge.target;

  function createIndex(graph) {
    const nodeById = new Map(), edgeByKey = new Map(), mainNodeIds = new Set();
    const incoming = new Map(), outgoing = new Map(), neighbors = new Map(), incidentEdges = new Map();
    for (const node of graph.nodes || []) {
      const id = accountId(node);
      if (id === null || nodeById.has(id)) continue;
      nodeById.set(id, node);
      if (node.is_main === 1 || node.data?.is_main === 1) mainNodeIds.add(id);
      incoming.set(id, new Set()); outgoing.set(id, new Set());
      neighbors.set(id, new Set()); incidentEdges.set(id, new Set());
    }
    for (const edge of graph.edges || []) {
      const { source, target } = edge;
      // A dangling edge must not create a new account or connect two case indexes.
      if (!nodeById.has(source) || !nodeById.has(target)) continue;
      const key = edgeKey(edge);
      edgeByKey.set(key, edge);
      incidentEdges.get(source).add(key); incidentEdges.get(target).add(key);
      // A self-loop is an edge, but never an additional one-hop neighbor.
      if (source === target) continue;
      outgoing.get(source).add(target); incoming.get(target).add(source);
      neighbors.get(source).add(target); neighbors.get(target).add(source);
    }
    const index = { nodeById, edgeByKey, mainNodeIds, nodeIds: new Set(nodeById.keys()),
      edgeKeys: new Set(edgeByKey.keys()), incoming, outgoing, neighbors, incidentEdges };
    cachedOrder.set(index, { nodes: null, edges: null, mains: null, adjacent: new Map(), incident: new Map() });
    return index;
  }

  function ordered(index, kind) {
    const cache = cachedOrder.get(index);
    if (!cache[kind]) cache[kind] = [...(kind === 'nodes' ? index.nodeIds
      : kind === 'mains' ? index.mainNodeIds : index.edgeKeys)].sort(compareId);
    return cache[kind];
  }

  function orderedAdjacent(index, root, direction = 'both') {
    const cache = cachedOrder.get(index).adjacent;
    const key = direction + ':' + root;
    if (!cache.has(key)) {
      const map = direction === 'in' ? index.incoming : direction === 'out' ? index.outgoing : index.neighbors;
      cache.set(key, [...(map.get(root) || [])].sort(compareId));
    }
    return cache.get(key);
  }

  function orderedIncident(index, root) {
    const cache = cachedOrder.get(index).incident;
    if (!cache.has(root)) cache.set(root, [...(index.incidentEdges.get(root) || [])].sort(compareId));
    return cache.get(root);
  }

  function expansionLimit(value) {
    return typeof value === 'number' && Number.isFinite(value)
      ? Math.max(1, Math.min(200, Math.floor(value))) : 50;
  }

  function neighborhood(index, root, options = {}) {
    // Ignore foreign/stale visible IDs. The caller owns the returned set and history.
    const nodeIds = new Set();
    for (const id of options.visible || []) if (index.nodeById.has(id)) nodeIds.add(id);
    if (!index.nodeById.has(root)) {
      return { nodeIds, remaining: 0, totalNeighbors: 0, added: 0, rootFound: false };
    }
    nodeIds.add(root);
    const direction = options.direction === 'in' || options.direction === 'out' ? options.direction : 'both';
    const adjacent = orderedAdjacent(index, root, direction), limit = expansionLimit(options.limit);
    let added = 0, remaining = 0;
    for (const id of adjacent) {
      if (nodeIds.has(id)) continue;
      if (added < limit) { nodeIds.add(id); added += 1; }
      else remaining += 1;
    }
    return { nodeIds, remaining, totalNeighbors: adjacent.length, added, rootFound: true };
  }

  function labels(index, options = {}) {
    const eligible = options.eligibleNodeIds || index.nodeIds;
    const zoom = typeof options.zoom === 'number' && Number.isFinite(options.zoom) ? options.zoom : 1;
    const mode = options.mode === 'all' || options.mode === 'focus' ? options.mode : 'auto';
    const level = zoom < 0.6 ? 'overview' : zoom < 1 ? 'names' : 'detail';
    if (mode === 'all' || (mode === 'auto' && index.nodeIds.size <= 20)) {
      return { nodeIds: new Set([...index.nodeIds].filter(id => eligible.has(id))), edgeKeys: new Set(index.edgeKeys),
        level: 'detail', nodeCap: index.nodeIds.size, edgeCap: index.edgeKeys.size };
    }
    const nodeCap = level === 'overview' ? 20 : level === 'names' ? 80 : 160;
    const edgeCap = level === 'overview' ? 1 : level === 'names' ? 40 : 80;
    const nodeIds = new Set(), edgeKeys = new Set(), roots = [];
    const selectedKey = typeof options.selectedEdge === 'string' ? options.selectedEdge
      : options.selectedEdge ? edgeKey(options.selectedEdge) : null;
    const selectedEdge = index.edgeByKey.get(selectedKey);
    const addRoot = id => {
      if (index.nodeById.has(id) && !roots.includes(id)) roots.push(id);
    };
    addRoot(options.selectedAccount); addRoot(options.hoveredAccount);
    if (selectedEdge) { addRoot(selectedEdge.source); addRoot(selectedEdge.target); edgeKeys.add(selectedKey); }
    const addNode = id => { if (nodeIds.size < nodeCap && eligible.has(id)) nodeIds.add(id); };
    roots.forEach(addNode);
    for (const root of roots) {
      for (const id of orderedAdjacent(index, root)) {
        if (nodeIds.size >= nodeCap) break;
        addNode(id);
      }
    }
    if (mode === 'auto' && nodeIds.size < nodeCap) {
      const nodeOrder = ordered(index, 'nodes');
      for (const id of ordered(index, 'mains')) {
        if (nodeIds.size >= nodeCap) break;
        addNode(id);
      }
      for (const id of nodeOrder) {
        if (nodeIds.size >= nodeCap) break;
        addNode(id);
      }
    }
    if (level !== 'overview') {
      for (const root of roots) {
        for (const key of orderedIncident(index, root)) {
          if (edgeKeys.size >= edgeCap) break;
          edgeKeys.add(key);
        }
      }
      if (mode === 'auto' && edgeKeys.size < edgeCap) {
        for (const key of ordered(index, 'edges')) {
          if (edgeKeys.size >= edgeCap) break;
          edgeKeys.add(key);
        }
      }
    }
    return { nodeIds, edgeKeys, level, nodeCap, edgeCap };
  }

  global.FundsGraphView = Object.freeze({ createIndex, neighborhood, labels });
})(typeof window === 'undefined' ? globalThis : window);
