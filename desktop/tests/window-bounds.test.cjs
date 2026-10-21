'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { windowBounds } = require('../host-policy.cjs');

test('first-launch windows retain desktop caps and leave room around common laptop work areas', () => {
  for (const [workArea, expected] of [
    [{ x: 0, y: 0, width: 1920, height: 1040 },
      { x: 240, y: 50, width: 1440, height: 940, minWidth: 960, minHeight: 640 }],
    [{ x: 0, y: 0, width: 1366, height: 728 },
      { x: 32, y: 32, width: 1302, height: 664, minWidth: 960, minHeight: 640 }],
    [{ x: 0, y: 0, width: 1280, height: 680 },
      { x: 32, y: 20, width: 1216, height: 640, minWidth: 960, minHeight: 640 }],
  ]) assert.deepEqual(windowBounds(Object.freeze(workArea)), expected);
});

test('small logical work areas fit fully without applying a second DPI conversion', () => {
  // These are already DIP work areas, including a high-DPI equivalent small area.
  for (const [width, height, expected] of [
    [960, 500, { x: 0, y: 0, width: 960, height: 500, minWidth: 960, minHeight: 500 }],
    [683, 344, { x: 0, y: 0, width: 683, height: 344, minWidth: 683, minHeight: 344 }],
  ]) assert.deepEqual(windowBounds({ x: 0, y: 0, width, height }), expected);
});

test('first-launch centering respects negative secondary-display coordinates', () => {
  assert.deepEqual(windowBounds({ x: -1280, y: 24, width: 1280, height: 696 }),
    { x: -1248, y: 52, width: 1216, height: 640, minWidth: 960, minHeight: 640 });
  assert.deepEqual(windowBounds({ x: 0, y: -1040, width: 1920, height: 1040 }),
    { x: 240, y: -990, width: 1440, height: 940, minWidth: 960, minHeight: 640 });
});

test('valid current bounds keep the user size and position without adding first-launch margins', () => {
  const workArea = Object.freeze({ x: 0, y: 0, width: 1920, height: 1040 });
  const current = Object.freeze({ x: 75, y: 45, width: 1600, height: 980 });
  assert.deepEqual(windowBounds(workArea, current), { ...current, minWidth: 960, minHeight: 640 });
  assert.deepEqual(windowBounds(workArea, { x: 0, y: 0, width: 1920, height: 1040 }),
    { x: 0, y: 0, width: 1920, height: 1040, minWidth: 960, minHeight: 640 });
});

test('current bounds outside a changed work area shrink and return inside that display', () => {
  assert.deepEqual(windowBounds({ x: 1920, y: 20, width: 960, height: 500 },
    { x: 2100, y: 100, width: 1200, height: 760 }),
  { x: 1920, y: 20, width: 960, height: 500, minWidth: 960, minHeight: 500 });
  assert.deepEqual(windowBounds({ x: -1280, y: 24, width: 1280, height: 696 },
    { x: 200, y: -900, width: 1100, height: 650 }),
  { x: -1100, y: 24, width: 1100, height: 650, minWidth: 960, minHeight: 640 });
});

test('returning from a small work area to a large display preserves the existing small size', () => {
  assert.deepEqual(windowBounds({ x: 0, y: 0, width: 1920, height: 1040 },
    { x: 50, y: 70, width: 960, height: 500 }),
  { x: 50, y: 70, width: 960, height: 500, minWidth: 960, minHeight: 500 });
  assert.deepEqual(windowBounds({ x: 0, y: 0, width: 1920, height: 1040 },
    { x: 120, y: 80, width: 683, height: 344 }),
  { x: 120, y: 80, width: 683, height: 344, minWidth: 683, minHeight: 344 });
});
