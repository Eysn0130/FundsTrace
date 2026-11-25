'use strict';

const path = require('node:path');
const fs = require('node:fs');

const HEADER = 'X-FundsTrace-Desktop';
const PAGES = new Set(['/', '/analysis', '/license', '/workspace', '/workspace/']);

// Electron work areas and BrowserWindow bounds both use DIP, including at 200% DPI.
function windowBounds(workArea, current) {
  // The 16:9 design minimum applies only when it fits the usable desktop.
  // Do not scale DIP again or let a taskbar/high-DPI work area strand controls.
  const inset = 32 * 2;
  const minWidth = Math.min(1280, workArea.width), minHeight = Math.min(720, workArea.height);
  const initialWidth = Math.round(Math.min(1440, workArea.width - inset, (workArea.height - inset) * 16 / 9));
  const width = Math.max(minWidth, Math.min(current?.width ?? initialWidth, workArea.width));
  const height = Math.max(minHeight, Math.min(current?.height ?? Math.round(initialWidth * 9 / 16), workArea.height));
  const x = current?.x ?? workArea.x + Math.floor((workArea.width - width) / 2);
  const y = current?.y ?? workArea.y + Math.floor((workArea.height - height) / 2);
  return { width, height, minWidth, minHeight,
    x: Math.max(workArea.x, Math.min(x, workArea.x + workArea.width - width)),
    y: Math.max(workArea.y, Math.min(y, workArea.y + workArea.height - height)) };
}

function sameOrigin(url, origin) {
  try { const parsed = new URL(url); return parsed.origin === origin && !parsed.username && !parsed.password; }
  catch { return false; }
}
function pageAllowed(url, origin) {
  return sameOrigin(url, origin) && PAGES.has(new URL(url).pathname);
}
function popupKind(url, origin) {
  if (!sameOrigin(url, origin)) return 'deny';
  const pathname = new URL(url).pathname;
  if (pathname === '/api/export/tracked.xlsx' || pathname === '/api/account-ids/template'
      || /^\/api\/cases\/[1-9]\d*\/export$/.test(pathname)) return 'download';
  return PAGES.has(pathname) ? 'page' : 'deny';
}
function requestAllowed(url, origin, webContentsId, owned) {
  return owned.has(webContentsId) && sameOrigin(url, origin);
}
function downloadAllowed(url, origin) {
  return sameOrigin(url, origin) || url.startsWith(`blob:${origin}/`);
}
function launchConfig({ packaged, platform = process.platform, arch = process.arch, resourcesPath, repoRoot, python, dataDir, synthetic = false, modelTrial = false, env = process.env }) {
  if (!path.isAbsolute(dataDir)) throw new Error('Data directory must be absolute');
  if (packaged && synthetic) throw new Error('Synthetic mode is not available in packaged applications');
  if (modelTrial && (packaged || path.basename(dataDir) !== 'data' || !path.basename(path.dirname(dataDir)).startsWith('funds-trace-model-trial-')))
    throw new Error('Model trial requires an isolated source profile');
  const modelEndpoints = modelTrial ? env.FUNDS_TRACE_MODEL_ENDPOINTS || null : undefined;
  if (modelEndpoints && Buffer.byteLength(modelEndpoints, 'utf8') > 2048) throw new Error('Invalid local model configuration');
  const referenceScope = modelTrial ? env.FUNDS_TRACE_REFERENCE_REVIEW_SCOPE_FILE : undefined;
  if (referenceScope !== undefined && (platform !== 'darwin' || !modelEndpoints
      || typeof referenceScope !== 'string' || !path.isAbsolute(referenceScope)
      || Buffer.byteLength(referenceScope, 'utf8') > 1024))
    throw new Error('Reference review requires a private Mac model trial scope');
  let command, args, cwd;
  if (packaged) {
    const backendRoot = path.join(resourcesPath, 'backend');
    const manifest = JSON.parse(fs.readFileSync(path.join(backendRoot, 'build-manifest.json'), 'utf8'));
    if (manifest.platform !== platform || manifest.arch !== arch || manifest.protocol !== 1) throw new Error('Backend platform mismatch');
    command = path.join(backendRoot, `funds-trace-backend${platform === 'win32' ? '.exe' : ''}`);
    args = []; cwd = backendRoot;
  } else {
    command = python || path.join(repoRoot, '.venv', platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    if (!path.isAbsolute(command)) throw new Error('Python executable must be an absolute path');
    args = [path.join(repoRoot, 'desktop_backend.py')]; cwd = repoRoot;
  }
  if (!fs.statSync(command).isFile()) throw new Error('Backend executable is missing');
  const cleanEnv = Object.fromEntries(Object.entries(env).filter(([key]) => !key.startsWith('FUND_') && !key.startsWith('FUNDS_TRACE_') && !key.startsWith('PYTHON')));
  const { FUND_MINERU_EXECUTABLE: mineru, FUND_MINERU_MODEL_DIR: models,
    FUND_MINERU_REVIEW_TIMEOUT_SECONDS: timeout } = env;
  if (modelTrial && (mineru || models || timeout !== undefined)) {
    if (![mineru, models].every(value => typeof value === 'string' && path.isAbsolute(value)))
      throw new Error('MinerU trial executable and model directory require absolute paths');
    cleanEnv.FUND_MINERU_EXECUTABLE = mineru;
    cleanEnv.FUND_MINERU_MODEL_DIR = models;
    if (timeout !== undefined) {
      const seconds = typeof timeout === 'string' ? Number(timeout) : NaN;
      if (!Number.isInteger(seconds) || seconds < 90 || seconds > 900 || String(seconds) !== timeout)
        throw new Error('MinerU trial timeout must be an ASCII integer from 90 to 900 seconds');
      cleanEnv.FUND_MINERU_REVIEW_TIMEOUT_SECONDS = timeout;
    }
  }
  if (!synthetic && !modelTrial && env.FUNDS_TRACE_LICENSE_SECRET) cleanEnv.FUNDS_TRACE_LICENSE_SECRET = env.FUNDS_TRACE_LICENSE_SECRET;
  if (!synthetic && !modelTrial && env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE) {
    if (!path.isAbsolute(env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE)) throw new Error('License public key path must be absolute');
    cleanEnv.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE = env.FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE;
  }
  cleanEnv.PYTHONNOUSERSITE = '1'; cleanEnv.PYTHONDONTWRITEBYTECODE = '1'; cleanEnv.PYTHONUNBUFFERED = '1'; cleanEnv.PYTHONUTF8 = '1';
  return { command, args, cwd, env: cleanEnv, dataDir, synthetic,
    ...(modelTrial ? { modelTrial: modelEndpoints } : {}),
    ...(referenceScope === undefined ? {} : { referenceReviewScopeFile: referenceScope }) };
}

module.exports = { HEADER, windowBounds, sameOrigin, pageAllowed, popupKind, requestAllowed, downloadAllowed, launchConfig };
