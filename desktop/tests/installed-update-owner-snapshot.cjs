'use strict';
// A bounded post-failure observer. This never changes or retries the updater.
const { spawn } = require('node:child_process');
const path = require('node:path');

const MAX_BYTES = 65536, MAX_BUDGET_MS = 8000;
const NULLABLE_BOOLS = ['mainWindowUnique', 'mainWindowVisible', 'mainWindowEnabled', 'processResponding'];
const emptySnapshot = () => ({ schemaVersion: 1, processBound: false, querySucceeded: false,
  ...Object.fromEntries(NULLABLE_BOOLS.map(key => [key, null])), ownedVisibleDialogCount: null });

function projectOwnerSnapshot(value) {
  try {
    // Read known scalar fields once before validation, even if a caller passes
    // getters rather than the helper's ordinary parsed JSON object.
    if (!value) return null;
    value = Object.fromEntries(['schemaVersion', 'processBound', 'querySucceeded', ...NULLABLE_BOOLS,
      'ownedVisibleDialogCount'].map(key => [key, value[key]]));
    if (!value || value.schemaVersion !== 1 || typeof value.processBound !== 'boolean'
      || typeof value.querySucceeded !== 'boolean'
      || !NULLABLE_BOOLS.every(key => value[key] === null || typeof value[key] === 'boolean')
      || !(value.ownedVisibleDialogCount === null || (Number.isInteger(value.ownedVisibleDialogCount)
        && value.ownedVisibleDialogCount >= 0 && value.ownedVisibleDialogCount <= 64))) return null;
    if (value.querySucceeded) {
      if (!value.processBound || value.mainWindowUnique !== true || value.mainWindowVisible !== true
        || typeof value.mainWindowEnabled !== 'boolean' || typeof value.processResponding !== 'boolean'
        || value.ownedVisibleDialogCount === null) return null;
    } else if (value.mainWindowVisible !== null || value.mainWindowEnabled !== null
      || value.processResponding !== null || value.ownedVisibleDialogCount !== null
      || value.mainWindowUnique === true || (!value.processBound && value.mainWindowUnique !== null)) return null;
    return Object.fromEntries(['schemaVersion', 'processBound', 'querySucceeded', ...NULLABLE_BOOLS,
      'ownedVisibleDialogCount'].map(key => [key, value[key]]));
  } catch { return null; }
}

async function safeUpdateOwnerSnapshot(binding, options = {}) {
  const startedAt = Date.now();
  const empty = () => ({ ...emptySnapshot(), helperReport: 'missing', timedOut: false,
    stderrObserved: false, stdoutTruncated: false, elapsedMs: 0, exitCode: null });
  try {
    const pid = binding?.pid, installRoot = binding?.installRoot, budgetMs = options?.budgetMs ?? MAX_BUDGET_MS;
    if (process.platform !== 'win32' || process.env.CI !== 'true' || process.env.GITHUB_ACTIONS !== 'true'
      || !Number.isInteger(pid) || pid <= 0 || pid > 2147483647
      || typeof installRoot !== 'string' || !/^[A-Za-z]:[\\/]/.test(installRoot)
      || !path.win32.isAbsolute(installRoot) || /["\x00-\x1f]/.test(installRoot)
      || !Number.isInteger(budgetMs) || budgetMs <= 0 || budgetMs > MAX_BUDGET_MS) return empty();
    return await new Promise(resolve => {
      let child, timer, settled = false, raw = '', bytes = 0, stderrObserved = false, stdoutTruncated = false;
      const finish = (exitCode, timedOut = false, parse = true) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        let safe = null, helperReport = 'missing';
        if (parse && !stdoutTruncated && raw.trim()) {
          helperReport = 'invalid';
          try { safe = projectOwnerSnapshot(JSON.parse(raw.replace(/^\uFEFF/, ''))); } catch { }
          if (safe) helperReport = 'valid';
        }
        raw = '';
        // A killed or failed observer cannot contribute a successful query.
        if (timedOut || exitCode !== 0) safe = null;
        resolve({ ...(safe || emptySnapshot()), helperReport, timedOut, stderrObserved, stdoutTruncated,
          elapsedMs: Math.min(MAX_BUDGET_MS, Math.max(0, Date.now() - startedAt)),
          exitCode: Number.isInteger(exitCode) && exitCode >= -2147483648 && exitCode <= 2147483647 ? exitCode : null });
      };
      timer = setTimeout(() => {
        finish(null, true, false);
        try { child?.kill(); } catch { /* Never replace the original acceptance error. */ }
      }, budgetMs);
      try {
        child = spawn('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-File',
          path.resolve(__dirname, '../../scripts/observe-windows-update-owner.ps1'),
          '-InstallRoot', installRoot, '-ProcessId', String(pid)],
        { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
        child.stdout.on('data', chunk => {
          if (settled || stdoutTruncated) return;
          bytes += Buffer.byteLength(chunk);
          if (bytes > MAX_BYTES) { stdoutTruncated = true; raw = ''; return; }
          raw += chunk.toString('utf8');
        });
        child.stderr.on('data', chunk => { if (!settled && chunk.length) stderrObserved = true; });
        child.once('error', () => finish(null, false, false));
        child.once('close', code => finish(code));
      } catch { finish(null, false, false); }
    });
  } catch { return empty(); }
}

module.exports = { safeUpdateOwnerSnapshot, projectOwnerSnapshot };
