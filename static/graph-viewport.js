/* Conservative display geometry. No transaction facts enter this index. */
(function (root) {
  'use strict';
  const finite = Number.isFinite;
  const intersects = (a, b) => a.minX <= b.maxX && a.maxX >= b.minX && a.minY <= b.maxY && a.maxY >= b.minY;
  const valid = b => b && ['minX','minY','maxX','maxY'].every(k => finite(b[k]));
  const union = (a,b) => valid(b) ? {minX:Math.min(a.minX,b.minX),minY:Math.min(a.minY,b.minY),maxX:Math.max(a.maxX,b.maxX),maxY:Math.max(a.maxY,b.maxY)} : a;
  function build({ nodes = [], edges = [] }) {
    const byId = new Map(), nodeBounds = new Map(), edgeBounds = new Map();
    let nodeExtent=null;
    for (const n of nodes) {
      if (!finite(n.x) || !finite(n.y)) continue;
      const radius = Math.max(16, (finite(n.size) ? n.size : 48) / 2) + 8;
      byId.set(n.id, n);
      nodeBounds.set(n.id, union({ minX: n.x - radius, minY: n.y - radius, maxX: n.x + radius, maxY: n.y + radius },n.bounds));
      nodeExtent=nodeExtent?union(nodeExtent,nodeBounds.get(n.id)):nodeBounds.get(n.id);
    }
    for (const e of edges) {
      const a = byId.get(e.source), b = byId.get(e.target);
      if (!a || !b) continue;
      // Include control points and a generous quadratic/loop/arrow envelope.
      const points = [a, b, ...(e.controlPoints || []).filter(p => finite(p.x) && finite(p.y))];
      const pad = Math.max(100, Math.hypot(a.x - b.x, a.y - b.y) * 0.3);
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const p of points) { minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y); }
      if (finite(e.lx) && finite(e.ly)) {
        minX = Math.min(minX, e.lx - (e.bw || 300) / 2); maxX = Math.max(maxX, e.lx + (e.bw || 300) / 2);
        minY = Math.min(minY, e.ly - (e.bh || 80) / 2); maxY = Math.max(maxY, e.ly + (e.bh || 80) / 2);
      }
      edgeBounds.set(e.source + '=>' + e.target, union({ minX: minX - pad, minY: minY - pad, maxX: maxX + pad, maxY: maxY + pad },e.bounds));
    }
    function query(viewport, padding = 0) {
      if (!viewport || !['minX','minY','maxX','maxY'].every(k => finite(viewport[k]))
        || viewport.maxX <= viewport.minX || viewport.maxY <= viewport.minY) {
        return { nodeIds: new Set(nodeBounds.keys()), edgeKeys: new Set(edgeBounds.keys()) };
      }
      const p = finite(padding) ? Math.max(0, padding) : 0;
      const area = { minX: viewport.minX - p, minY: viewport.minY - p, maxX: viewport.maxX + p, maxY: viewport.maxY + p };
      const nodeIds = new Set(), edgeKeys = new Set();
      for (const [id, bounds] of nodeBounds) if (intersects(bounds, area)) nodeIds.add(id);
      for (const [key, bounds] of edgeBounds) if (intersects(bounds, area)) edgeKeys.add(key);
      return { nodeIds, edgeKeys };
    }
    function include(kind,id,bounds) {
      const map=kind==='node'?nodeBounds:edgeBounds;
      if (map.has(id)) {
        map.set(id,union(map.get(id),bounds));
        if(kind==='node') nodeExtent=union(nodeExtent,map.get(id));
      }
    }
    function containsAll(viewport,padding=0) {
      if(!valid(viewport) || viewport.maxX<=viewport.minX || viewport.maxY<=viewport.minY) return false;
      if(!nodeExtent) return true;
      const p=finite(padding)?Math.max(0,padding):0;
      return viewport.minX-p<=nodeExtent.minX && viewport.maxX+p>=nodeExtent.maxX
        && viewport.minY-p<=nodeExtent.minY && viewport.maxY+p>=nodeExtent.maxY;
    }
    return Object.freeze({ query, include, containsAll });
  }
  root.FundsGraphViewport = Object.freeze({ build, intersects });
})(typeof window === 'undefined' ? globalThis : window);
