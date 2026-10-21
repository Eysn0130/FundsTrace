'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const startupDirectory = path.join(__dirname, '../startup');
const rendererSource = fs.readFileSync(path.join(startupDirectory, 'renderer.js'), 'utf8');

function renderer({ reduced = false, earlyReady = false } = {}) {
  let now = 1000, nextFrame = 1, ready, completed = 0, unsubscribed = 0, layoutReads = 0;
  const frames = new Map(), properties = new Map(), writes = [], animations = [], windowListeners = new Map();
  const surface = {
    clientWidth:1200, clientHeight:800, dataset:{phase:'waiting'},
    style:{setProperty(name, value) { properties.set(name, value); writes.push(name); }},
  };
  const brand = {
    get offsetWidth() { layoutReads++; return 360; },
    get offsetHeight() { layoutReads++; return 387; },
  };
  const animatedElement = { animate(keyframes, options) {
    const animation = {keyframes, options, currentTime:null, paused:false, canceled:false,
      pause() { this.paused = true; }, cancel() { this.canceled = true; }};
    animations.push(animation);
    return animation;
  }};
  const elements = new Map([
    ['#startup-surface',surface], ['.startup-brand',brand],
    ['.startup-original-sweep',animatedElement], ['.startup-amber-light',animatedElement],
  ]);
  const motionListeners = new Set();
  const media = {matches:reduced,
    addEventListener(type, callback) { assert.equal(type, 'change'); motionListeners.add(callback); },
    removeEventListener(type, callback) { assert.equal(type, 'change'); motionListeners.delete(callback); },
  };
  let observer;
  const document = {documentElement:{dataset:{}},querySelector:selector => {
    assert.ok(elements.has(selector), selector);
    return elements.get(selector);
  }};
  vm.runInNewContext(rendererSource, {
    document, performance:{now:() => now}, matchMedia:() => media,
    ResizeObserver:class {
      constructor(callback) { this.callback = callback; this.observed = []; this.disconnected = false; observer = this; }
      observe(element) { this.observed.push(element); }
      disconnect() { this.disconnected = true; }
    },
    requestAnimationFrame(callback) { const id = nextFrame++; frames.set(id, callback); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
    window:{
      FundsTraceStartup:{
        onReady(callback) { ready = callback; if (earlyReady) ready(); return () => { unsubscribed++; }; },
        complete() { completed++; },
      },
      addEventListener(type, callback) { windowListeners.set(type, callback); },
    },
  });
  return {
    surface, document, animations, writes, properties, observer,
    get completed() { return completed; },
    get unsubscribed() { return unsubscribed; },
    get layoutReads() { return layoutReads; },
    get pendingFrames() { return frames.size; },
    value:name => Number.parseFloat(properties.get(`--${name}`)),
    frame(time) {
      now = time;
      const callbacks = Array.from(frames.values()); frames.clear();
      for (const callback of callbacks) callback(now);
    },
    ready(time = now) { now = time; ready(); },
    resize(width, height) { surface.clientWidth = width; surface.clientHeight = height; observer.callback(); },
    motion(value) { media.matches = value; for (const callback of motionListeners) callback(); },
    pagehide() { windowListeners.get('pagehide')(); },
  };
}

test('waiting uses real elapsed time, holds A, then reveals forty percent without declaring readiness', () => {
  const view = renderer();
  assert.equal(view.value('backdrop-opacity'), 1);
  assert.equal(view.value('brand-scale'), 1);
  view.frame(2400);
  assert.equal(view.value('backdrop-opacity'), 1, 'hold the pure brand surface for 1400ms');
  view.frame(4950);
  assert.equal(view.value('backdrop-opacity'), .8);
  view.frame(7500);
  assert.equal(view.value('backdrop-opacity'), .6);
  view.frame(90000);
  assert.equal(view.value('backdrop-opacity'), .6);
  assert.equal(view.value('wait-opacity'), .75);
  assert.equal(view.surface.dataset.phase, 'waiting');
  assert.equal(view.completed, 0);
  assert.ok(view.animations.every(animation => animation.paused));
  assert.ok(view.animations.every(animation => animation.currentTime === 89000), 'shine and breathing share the real clock');
  assert.equal(view.layoutReads, 2, 'animation frames never measure layout');
});

test('early readiness fades the current surface together for 1100ms and completes exactly once', () => {
  const view = renderer();
  view.frame(1500);
  const waitOpacity = view.value('wait-opacity');
  view.ready(1500);
  assert.equal(view.surface.dataset.phase, 'handoff');
  assert.equal(view.value('backdrop-opacity'), 1);
  assert.equal(view.properties.has('--exit-opacity'), false, 'the initial CSS opacity is still one');
  view.frame(2050);
  assert.equal(view.value('exit-opacity'), .5);
  assert.equal(view.value('wait-opacity'), waitOpacity, 'loading feedback inherits the same parent fade');
  assert.equal(view.value('backdrop-opacity'), 1, 'handoff freezes the captured loading backdrop');
  assert.ok(view.animations.every(animation => animation.currentTime === 1050));
  view.ready(2100);
  view.frame(2599);
  assert.equal(view.completed, 0, 'duplicate ready cannot reset or shorten the handoff');
  view.frame(2600);
  assert.equal(view.completed, 1);
  assert.equal(view.value('exit-opacity'), 0);
  assert.equal(view.surface.dataset.phase, 'complete');
  assert.equal(view.unsubscribed, 1);
  assert.equal(view.pendingFrames, 0);
  assert.equal(view.observer.disconnected, true);
  assert.ok(view.animations.every(animation => animation.canceled));
  view.ready(4000);
  view.frame(9000);
  assert.equal(view.completed, 1);
});

test('long waiting and handoff stay continuous, and the indeterminate line keeps its clock', () => {
  const view = renderer();
  view.frame(7600);
  const backdrop = view.value('backdrop-opacity'), line = view.value('wait-x');
  view.ready(7600);
  assert.equal(view.value('backdrop-opacity'), backdrop);
  assert.equal(view.value('wait-x'), line);
  view.frame(8150);
  assert.equal(view.value('backdrop-opacity'), .6);
  assert.equal(view.value('exit-opacity'), .5);
  assert.notEqual(view.value('wait-x'), line);
  view.frame(8700);
  assert.equal(view.completed, 1);
});

test('viewport changes only rescale the fixed brand, including during the handoff', () => {
  const view = renderer();
  view.frame(7500);
  view.ready(7500);
  view.frame(8050);
  const opacity = view.value('exit-opacity');
  view.writes.length = 0;
  view.resize(400, 300);
  assert.equal(view.value('brand-scale'), Number(((300-48)/387).toFixed(5)));
  assert.equal(view.value('exit-opacity'), opacity);
  assert.deepEqual(view.writes, ['--brand-scale']);
  view.resize(1200, 800);
  assert.equal(view.value('brand-scale'), 1);
  assert.equal(view.value('exit-opacity'), opacity);
  view.frame(8600);
  assert.equal(view.completed, 1, 'resize never restarts the ready clock');
});

test('reduced motion keeps loading static and allows immediate 140ms readiness', () => {
  const view = renderer({reduced:true, earlyReady:true});
  assert.equal(view.document.documentElement.dataset.motion, 'reduce');
  assert.equal(view.surface.dataset.phase, 'handoff');
  assert.equal(view.value('backdrop-opacity'), 1);
  assert.equal(view.value('wait-x'), 10);
  assert.equal(view.value('wait-opacity'), .75);
  view.frame(1070);
  assert.equal(view.value('exit-opacity'), .5);
  assert.ok(view.animations.every(animation => animation.currentTime === 0));
  view.frame(1139);
  assert.equal(view.completed, 0);
  view.frame(1140);
  assert.equal(view.completed, 1);
});

test('closing the local document releases animation and subscriptions without reporting completion', () => {
  const view = renderer();
  view.pagehide();
  assert.equal(view.pendingFrames, 0);
  assert.equal(view.observer.disconnected, true);
  assert.equal(view.unsubscribed, 1);
  view.ready(2000);
  view.frame(9000);
  assert.equal(view.completed, 0);
});

test('startup preload exposes only its narrow bridge, buffers readiness and never exposes an IPC event', () => {
  const listeners = new Map(), sends = [];
  let api;
  vm.runInNewContext(fs.readFileSync(path.join(startupDirectory, 'preload.cjs'), 'utf8'), {
    require(name) {
      assert.equal(name, 'electron');
      return {
        contextBridge:{exposeInMainWorld(name, value) { assert.equal(name, 'FundsTraceStartup'); api = value; }},
        ipcRenderer:{on(channel, listener) { listeners.set(channel, listener); },send(...args) { sends.push(args); }},
      };
    },
  });
  assert.equal(Object.isFrozen(api), true);
  assert.deepEqual(Object.keys(api), ['onReady','complete']);
  api.complete();
  assert.deepEqual(sends, [], 'completion is unavailable until Main confirms readiness');
  const notify = listeners.get('funds-trace:startup');
  let ignored = 0;
  const remove = api.onReady(() => { ignored++; });
  notify({sensitive:'not exposed'}, 'other');
  remove();
  notify({sensitive:'not exposed'}, 'ready');
  assert.equal(ignored, 0);
  let received = 0;
  api.onReady((...args) => { assert.equal(args.length, 0); received++; });
  notify({}, 'ready');
  assert.equal(received, 1);
  api.complete(); api.complete();
  assert.deepEqual(sends, [['funds-trace:startup','complete']]);
});

test('local surface has one shared fade, transparent document and no remote or synthetic workspace assets', () => {
  const html = fs.readFileSync(path.join(startupDirectory, 'index.html'), 'utf8');
  const css = fs.readFileSync(path.join(startupDirectory, 'style.css'), 'utf8');
  assert.match(html, /connect-src 'none'/);
  assert.match(html, /script-src 'self'; style-src 'self'; img-src 'self'/);
  assert.match(html, /src="\.\.\/assets\/icon\.png"/);
  assert.doesNotMatch(html, /https?:|workspace\.png|<button|<select|<input/);
  assert.match(css, /html,body\{[^}]*background:transparent/);
  assert.match(css, /\.startup-surface\{[^}]*opacity:var\(--exit-opacity\)/);
  assert.match(css, /\.startup-mark\{[^}]*width:176px;height:176px/);
  assert.doesNotMatch(css, /\btransition\s*:|\banimation\s*:|filter\s*:/);
});
