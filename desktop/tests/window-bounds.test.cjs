'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { windowBounds } = require('../host-policy.cjs');

test('normal windows enforce the approved 1280 by 720 minimum without locking the aspect ratio', () => {
  for (const [workArea, expected] of [
    [{ x: 0, y: 0, width: 1920, height: 1040 },
      { x: 240, y: 115, width: 1440, height: 810, minWidth: 1280, minHeight: 720 }],
    [{ x: 0, y: 0, width: 1366, height: 728 },
      { x: 43, y: 4, width: 1280, height: 720, minWidth: 1280, minHeight: 720 }],
    [{ x: 0, y: 0, width: 1280, height: 680 },
      { x: 0, y: 0, width: 1280, height: 680, minWidth: 1280, minHeight: 680 }],
  ]) assert.deepEqual(windowBounds(Object.freeze(workArea)), expected);
});

test('taskbar and scaling are already included in DIP work areas, including 125 and 150 percent examples', () => {
  for (const [width, height, expected] of [
    [1536, 824, { x: 92, y: 32, width: 1351, height: 760, minWidth: 1280, minHeight: 720 }],
    [1280, 688, { x: 0, y: 0, width: 1280, height: 688, minWidth: 1280, minHeight: 688 }],
    [960, 500, { x: 0, y: 0, width: 960, height: 500, minWidth: 960, minHeight: 500 }],
    [683, 344, { x: 0, y: 0, width: 683, height: 344, minWidth: 683, minHeight: 344 }],
  ]) assert.deepEqual(windowBounds({ x: 0, y: 0, width, height }), expected);
});

test('centering respects negative display coordinates and taskbars on either edge', () => {
  assert.deepEqual(windowBounds({ x: -1280, y: 24, width: 1280, height: 696 }),
    { x: -1280, y: 24, width: 1280, height: 696, minWidth: 1280, minHeight: 696 });
  assert.deepEqual(windowBounds({ x: 0, y: -1040, width: 1920, height: 1040 }),
    { x: 240, y: -925, width: 1440, height: 810, minWidth: 1280, minHeight: 720 });
  assert.deepEqual(windowBounds({ x: 48, y: 0, width: 1318, height: 768 }),
    { x: 67, y: 24, width: 1280, height: 720, minWidth: 1280, minHeight: 720 });
});

test('valid current bounds keep user size and position without first-launch margins', () => {
  const workArea = Object.freeze({ x: 0, y: 0, width: 1920, height: 1040 });
  const current = Object.freeze({ x: 75, y: 45, width: 1600, height: 980 });
  assert.deepEqual(windowBounds(workArea, current), { ...current, minWidth: 1280, minHeight: 720 });
  assert.deepEqual(windowBounds(workArea, { x: 0, y: 0, width: 1920, height: 1040 }),
    { x: 0, y: 0, width: 1920, height: 1040, minWidth: 1280, minHeight: 720 });
});

test('changed work areas clamp both current bounds and minima to the available desktop', () => {
  assert.deepEqual(windowBounds({ x: 1920, y: 20, width: 960, height: 500 },
    { x: 2100, y: 100, width: 1400, height: 760 }),
  { x: 1920, y: 20, width: 960, height: 500, minWidth: 960, minHeight: 500 });
  assert.deepEqual(windowBounds({ x: -1280, y: 24, width: 1280, height: 696 },
    { x: 200, y: -900, width: 1100, height: 650 }),
  { x: -1280, y: 24, width: 1280, height: 696, minWidth: 1280, minHeight: 696 });
});

test('returning from a small desktop restores the normal minimum instead of retaining an unusably narrow window', () => {
  for (const [width, height] of [[960, 500], [683, 344]]) {
    assert.deepEqual(windowBounds({ x: 0, y: 0, width: 1920, height: 1040 },
      { x: 120, y: 80, width, height }),
    { x: 120, y: 80, width: 1280, height: 720, minWidth: 1280, minHeight: 720 });
  }
});
