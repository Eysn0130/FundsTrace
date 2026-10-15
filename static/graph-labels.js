/* Screen-space label selection only. Callers retain canonical labels and facts. */
(function (global) {
  'use strict';

  const CELL_SIZE = 64, MAX_CELLS = 256;
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const intersects = (a, b) => a.minX <= b.maxX && a.maxX >= b.minX
    && a.minY <= b.maxY && a.maxY >= b.minY;

  function paddedBounds(bounds, padding) {
    if (!bounds || ![bounds.minX, bounds.minY, bounds.maxX, bounds.maxY].every(finite)
      || bounds.minX > bounds.maxX || bounds.minY > bounds.maxY) return null;
    const result = { minX: bounds.minX - padding, minY: bounds.minY - padding,
      maxX: bounds.maxX + padding, maxY: bounds.maxY + padding };
    return Object.values(result).every(finite) ? result : null;
  }

  function cells(bounds) {
    const minX = Math.floor(bounds.minX / CELL_SIZE), maxX = Math.floor(bounds.maxX / CELL_SIZE);
    const minY = Math.floor(bounds.minY / CELL_SIZE), maxY = Math.floor(bounds.maxY / CELL_SIZE);
    if (![minX, maxX, minY, maxY].every(Number.isSafeInteger)
      || (maxX - minX + 1) * (maxY - minY + 1) > MAX_CELLS) return null;
    const result = [];
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) result.push(x + ',' + y);
    }
    return result;
  }

  function select(candidates, options = {}) {
    const nodeIds = new Set(), edgeKeys = new Set(), ranked = [];
    const padding = finite(options.padding) ? Math.max(0, options.padding) : 3;
    let rejected = 0;
    for (const candidate of Array.isArray(candidates) ? candidates : []) {
      if (!candidate || typeof candidate.key !== 'string' || !['node', 'edge'].includes(candidate.kind)) {
        rejected += 1; continue;
      }
      ranked.push({ key: candidate.key, kind: candidate.kind, required: candidate.required === true,
        priority: finite(candidate.priority) ? candidate.priority : 0, bounds: candidate.bounds });
    }
    ranked.sort((a, b) => Number(b.required) - Number(a.required) || b.priority - a.priority
      || compare(a.key, b.key) || compare(a.kind, b.kind));

    const grid = new Map(), oversized = [], accepted = [], seen = new Set();
    const conflicts = (bounds, keys) => {
      if (!keys) return accepted.some(other => intersects(bounds, other));
      for (const index of oversized) if (intersects(bounds, accepted[index])) return true;
      const visited = new Set();
      for (const key of keys) {
        for (const index of grid.get(key) || []) {
          if (visited.has(index)) continue;
          visited.add(index);
          if (intersects(bounds, accepted[index])) return true;
        }
      }
      return false;
    };
    const insert = (bounds, keys) => {
      const index = accepted.length; accepted.push(bounds);
      if (!keys) { oversized.push(index); return; }
      for (const key of keys) {
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(index);
      }
    };

    for (const candidate of ranked) {
      const identity = candidate.kind + '\0' + candidate.key;
      if (seen.has(identity)) { rejected += 1; continue; }
      seen.add(identity);
      if (options.disabled === true) {
        (candidate.kind === 'node' ? nodeIds : edgeKeys).add(candidate.key); continue;
      }
      const bounds = paddedBounds(candidate.bounds, padding);
      // A selected/required label must survive missing geometry. Without bounds
      // it cannot safely block other labels; ordinary unknown bounds fail closed.
      if (!bounds) {
        if (candidate.required) (candidate.kind === 'node' ? nodeIds : edgeKeys).add(candidate.key);
        else rejected += 1;
        continue;
      }
      const keys = cells(bounds);
      if (!candidate.required && conflicts(bounds, keys)) { rejected += 1; continue; }
      (candidate.kind === 'node' ? nodeIds : edgeKeys).add(candidate.key);
      insert(bounds, keys);
    }
    return { nodeIds, edgeKeys, rejected };
  }

  global.FundsGraphLabels = Object.freeze({ select });
})(typeof window === 'undefined' ? globalThis : window);
