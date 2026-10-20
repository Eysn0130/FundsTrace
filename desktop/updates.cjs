'use strict';

const { pageAllowed } = require('./host-policy.cjs');
const CHANNEL = 'funds-trace:updates';
const FEED = 'https://analytix.top/fundstrace/';
const CDN = 'https://download.analytix.top/releases/fundstrace/windows/';

function releaseInfo(info) {
  if (!info || !/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(info.version) || info.product !== 'com.eysn.fundstrace'
      || !Array.isArray(info.files) || info.files.length !== 1 || info.packages) throw new Error('Invalid update');
  const file = info.files[0];
  const expected = `${CDN}${info.version}/FundsTrace-${info.version}-windows-x64-unsigned.exe`;
  if (file.url !== expected || !/^[A-Za-z0-9+/]{86}==$/.test(file.sha512)
      || !Number.isSafeInteger(file.size) || file.size <= 0 || file.size > 1024 ** 3) throw new Error('Invalid update');
  return { version: info.version, file };
}

function allowed(event, origin, owned) {
  const sender = event.sender, frame = event.senderFrame;
  return !!sender && !sender.isDestroyed() && owned.has(sender.id) && !!frame
    && frame === sender.mainFrame && pageAllowed(frame.url, origin);
}

// Main owns the feed, cached installer and install command. Renderer messages
// contain only an action, never a URL, path, token or executable argument.
function updateController({ updater, version, enabled, notify = () => {}, restart = async () => false }) {
  let state = { phase: enabled ? 'idle' : 'unavailable', currentVersion: version, version: '', percent: 0 };
  let operation, accepted, installing = false;
  const snapshot = () => ({ ...state });
  const set = next => { state = { ...state, ...next }; notify(snapshot()); };
  const failed = () => { accepted = undefined; set({ phase: 'error', percent: 0 }); };
  if (enabled) {
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.allowPrerelease = false;
    updater.allowDowngrade = false;
    updater.disableWebInstaller = true;
    // Full installers avoid depending on old cached installers/blockmaps.
    updater.disableDifferentialDownload = true;
    updater.logger = null;
    updater.on('error', failed);
    updater.on('download-progress', progress => {
      if (state.phase !== 'downloading') return;
      set({ percent: Math.min(100, Math.max(0, Number(progress.percent) || 0)) });
    });
  }
  function run(task) {
    if (operation) return operation;
    operation = Promise.resolve().then(task).catch(failed).finally(() => { operation = undefined; });
    return operation;
  }
  return {
    snapshot,
    async action(action) {
      if (!enabled) return snapshot();
      if (action === 'check' && !['ready', 'installing'].includes(state.phase)) await run(async () => {
        accepted = undefined;
        set({ phase: 'checking', version: '', percent: 0 });
        const result = await updater.checkForUpdates();
        if (!result?.updateInfo) throw new Error('No update response');
        const info = releaseInfo(result.updateInfo);
        // electron-updater determines semver availability; independently reject
        // equal/older metadata before permitting an executable download.
        const compare = info.version.split('.').map(Number), current = version.split('.').map(Number);
        const different = compare.findIndex((value, index) => value !== current[index]);
        if (different === -1 || compare[different] < current[different]) {
          set({ phase: 'current', version: '', percent: 0 }); return;
        }
        accepted = info;
        set({ phase: 'available', version: info.version });
      });
      else if (action === 'download' && state.phase === 'available') await run(async () => {
        set({ phase: 'downloading', percent: 0 });
        const files = await updater.downloadUpdate();
        if (!accepted || !Array.isArray(files) || files.length !== 1) throw new Error('Download failed');
        // The updater has verified SHA-512 (and Authenticode when configured).
        set({ phase: 'ready', percent: 100 });
      });
      else if (action === 'install' && state.phase === 'ready' && !installing) {
        installing = true;
        try {
          if (await restart()) set({ phase: 'installing' });
        } catch { failed(); }
        finally { installing = false; }
      }
      return snapshot();
    },
    // Called only after the owned backend has finished its normal shutdown.
    install() { updater.quitAndInstall(false, true); },
  };
}

module.exports = { CHANNEL, FEED, CDN, releaseInfo, allowed, updateController };
