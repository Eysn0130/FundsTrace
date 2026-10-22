'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { pathToFileURL } = require('node:url');
const { createStartup, CHANNEL } = require('../startup.cjs');

test('native closed releases the startup renderer and settles its handoff without a destroyed contentView getter', async () => {
  const win = new EventEmitter(), contentView = new EventEmitter(), ipcMain = new EventEmitter();
  const failures = [], views = [], timers = [];
  let destroyed = false, closedObserved = false;
  contentView.children = [];
  contentView.addChildView = view => contentView.children.push(view);
  contentView.removeChildView = view => { contentView.children = contentView.children.filter(child => child !== view); };
  Object.defineProperty(win, 'contentView', { get() {
    if (destroyed) throw new Error('Object has been destroyed');
    return contentView;
  } });
  win.webContents = { focus() {} };
  win.isDestroyed = () => destroyed;
  win.getContentBounds = () => ({ width: 960, height: 640 });
  const local = new EventEmitter();
  local.setPermissionRequestHandler = local.setPermissionCheckHandler = () => {};
  local.webRequest = { onBeforeRequest() {}, onBeforeSendHeaders() {} };
  class View {
    constructor() {
      this.webContents = new EventEmitter(); const contents = this.webContents;
      contents.id = 17; contents.mainFrame = { url: '' };
      contents.isDestroyed = () => Boolean(this.destroyed);
      contents.close = () => { this.closeCount = (this.closeCount || 0) + 1; this.destroyed = true; contents.emit('render-process-gone'); };
      contents.setWindowOpenHandler = () => {};
      contents.loadFile = async file => { contents.mainFrame.url = pathToFileURL(file).href; };
      contents.getURL = () => contents.mainFrame.url;
      contents.send = () => {};
      views.push(this);
    }
    setBackgroundColor() {}
    setBounds(bounds) { this.bounds = bounds; }
  }
  const startup = createStartup(win, { onFailure: () => failures.push('failed'), contentInset: 32 }, {
    WebContentsView: View, session: { fromPartition: () => local }, ipcMain,
    setTimer(callback) { const timer = { callback, unref() {} }; timers.push(timer); return timer; },
    clearTimer(timer) { if (timer) timer.cleared = true; },
  });
  await startup.loaded;
  startup.finish();
  win.once('closed', () => { closedObserved = true; });
  assert.doesNotThrow(() => { destroyed = true; win.emit('closed'); });
  assert.equal(closedObserved, true, 'teardown must not interrupt later Main closed listeners');
  assert.equal(views[0].closeCount, 1);
  assert.equal(await startup.finished, false);
  assert.equal(contentView.listenerCount('bounds-changed'), 0);
  assert.equal(win.listenerCount('resize'), 0);
  assert.equal(ipcMain.listenerCount(CHANNEL), 0);
  assert.equal(timers[0].cleared, true);
  startup.dispose(); assert.equal(views[0].closeCount, 1);
  assert.deepEqual(failures, []);
});
