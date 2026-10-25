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
    const nodePlacements = new Map(), edgePlacements = new Map();
    const padding = finite(options.padding) ? Math.max(0, options.padding) : 3;
    const obstaclePadding = finite(options.obstaclePadding) ? Math.max(0, options.obstaclePadding) : 2;
    const viewport = paddedBounds(options.viewport, 0);
    let rejected = 0, conflicts = 0, unknownGeometry = 0, unplaced = 0;
    for (const candidate of Array.isArray(candidates) ? candidates : []) {
      if (!candidate || typeof candidate.key !== 'string' || !['node', 'edge'].includes(candidate.kind)) {
        rejected += 1; continue;
      }
      ranked.push({ key: candidate.key, kind: candidate.kind, required: candidate.required === true,
        priority: finite(candidate.priority) ? candidate.priority : 0, bounds: candidate.bounds,
        placements: candidate.placements, ignoreObstacleKeys: candidate.ignoreObstacleKeys });
    }
    ranked.sort((a, b) => Number(b.required) - Number(a.required) || b.priority - a.priority
      || compare(a.key, b.key) || compare(a.kind, b.kind));

    // Node bodies and accepted text share one spatial grid. Each candidate
    // visits nearby cells, rather than scanning every node for every label.
    const grid = new Map(), oversized = [], occupied = [], seen = new Set();
    const collides = (bounds, keys, ignored) => {
      const hits = index => {
        const other = occupied[index];
        return !(other.obstacle && ignored.has(other.key)) && intersects(bounds, other.bounds);
      };
      if (!keys) return occupied.some((_, index) => hits(index));
      for (const index of oversized) if (hits(index)) return true;
      const visited = new Set();
      for (const key of keys) {
        for (const index of grid.get(key) || []) {
          if (visited.has(index)) continue;
          visited.add(index);
          if (hits(index)) return true;
        }
      }
      return false;
    };
    const insert = (bounds, keys, metadata = {}) => {
      const index = occupied.length; occupied.push({ bounds, ...metadata });
      if (!keys) { oversized.push(index); return; }
      for (const key of keys) {
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(index);
      }
    };

    for (const obstacle of Array.isArray(options.obstacles) ? options.obstacles : []) {
      const bounds = paddedBounds(obstacle?.bounds, obstaclePadding);
      if (bounds) insert(bounds, cells(bounds), { obstacle: true, key: obstacle.key });
    }

    for (const candidate of ranked) {
      const identity = candidate.kind + '\0' + candidate.key;
      if (seen.has(identity)) { rejected += 1; continue; }
      seen.add(identity);
      const ignored = new Set(candidate.ignoreObstacleKeys && typeof candidate.ignoreObstacleKeys !== 'string'
        && typeof candidate.ignoreObstacleKeys[Symbol.iterator] === 'function' ? candidate.ignoreObstacleKeys : []);
      // placements is an ordered, complete list in screen coordinates. Offsets
      // are relative to the caller's original position and never mutate facts.
      const inputs = Array.isArray(candidate.placements) && candidate.placements.length
        ? candidate.placements : [{ bounds: candidate.bounds, dx: 0, dy: 0 }];
      const base = paddedBounds(candidate.bounds, 0), placements = [];
      for (let index = 0; index < inputs.length; index += 1) {
        const input = inputs[index], bounds = paddedBounds(input?.bounds, 0);
        if (!bounds) continue;
        placements.push({ bounds, dx: finite(input.dx) ? input.dx : base ? bounds.minX - base.minX : 0,
          dy: finite(input.dy) ? input.dy : base ? bounds.minY - base.minY : 0, index });
      }
      let placement = null;
      for (const option of placements) {
        const bounds = paddedBounds(option.bounds, padding);
        const visibleInViewport = !viewport || intersects(option.bounds, viewport);
        if (options.disabled === true || (visibleInViewport && !collides(bounds, cells(bounds), ignored))) {
          placement = { ...option, conflicted: false }; break;
        }
      }
      if (!placement && (candidate.required || options.retainAll === true || options.disabled === true)) {
        placement = placements.length ? { ...placements[0], conflicted: options.disabled !== true }
          : { bounds: null, dx: 0, dy: 0, index: -1, conflicted: true };
        if (placement.bounds) { if (placement.conflicted) conflicts += 1; }
        else unknownGeometry += 1;
      }
      if (!placement) {
        rejected += 1; unplaced += 1;
        continue;
      }
      (candidate.kind === 'node' ? nodeIds : edgeKeys).add(candidate.key);
      (candidate.kind === 'node' ? nodePlacements : edgePlacements).set(candidate.key, placement);
      if (placement.bounds && options.disabled !== true) {
        const bounds = paddedBounds(placement.bounds, padding);
        insert(bounds, cells(bounds));
      }
    }
    return { nodeIds, edgeKeys, rejected, nodePlacements, edgePlacements, conflicts, unknownGeometry, unplaced };
  }

  global.FundsGraphLabels = Object.freeze({ select });
})(typeof window === 'undefined' ? globalThis : window);
