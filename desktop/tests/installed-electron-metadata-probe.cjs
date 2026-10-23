'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');

const PREFIX = 'FT_ELECTRON_METADATA ';
function probeEnvironment(configuration) {
  const env = {};
  for (const key of Object.keys(process.env)) {
    if (!/^(?:ELECTRON_|FUND_|FUNDS_TRACE_)/i.test(key)
      && !['NODE_OPTIONS', 'FUNDS_METADATA_PROBE_CONFIG'].includes(key.toUpperCase())) env[key] = process.env[key];
  }
  if (configuration) env.FUNDS_METADATA_PROBE_CONFIG = configuration;
  return env;
}
const COUNTS = ['requestCount', 'finishCount', 'responseCount', 'closeCount', 'redirectCount'];
const BOOLS = ['profileIsolated', 'providerResolved', 'metadataVersionStable', 'metadataValid', 'loginObserved', 'socketObserved',
  'unknownRedirectRejected', 'requestLimitExceeded', 'bodyLimitExceeded', 'errorObserved'];
const CODES = new Set(['NONE', 'UNKNOWN', 'ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'ERR_FAILED',
  'ERR_ABORTED', 'ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_RESET', 'ERR_NAME_NOT_RESOLVED',
  'ERR_TIMED_OUT', 'ERR_CONNECTION_TIMED_OUT', 'ERR_PROXY_CONNECTION_FAILED', 'ERR_TUNNEL_CONNECTION_FAILED',
  'ERR_NO_SUPPORTED_PROXIES', 'ERR_PROXY_AUTH_UNSUPPORTED', 'ERR_NETWORK_CHANGED', 'ERR_ADDRESS_UNREACHABLE', 'ERR_SSL_PROTOCOL_ERROR',
  'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_COMMON_NAME_INVALID', 'ERR_CERT_DATE_INVALID',
  'ERR_UPDATER_CHANNEL_FILE_NOT_FOUND', 'ERR_UPDATER_INVALID_UPDATE_INFO', 'ERR_TOO_MANY_REDIRECTS']);
function safeProbeResult(value) {
  try {
    if (!value || value.schemaVersion !== 1 || value.transport !== 'independentCopiedElectronRuntime' || value.businessStartup !== false
      || !['unavailable', 'failed', 'timeout', 'completed'].includes(value.status)
      || !['setup', 'ready', 'request', 'complete'].includes(value.stage)
      || !COUNTS.every(key => Number.isInteger(value[key]) && value[key] >= 0 && value[key] <= 16)
      || !BOOLS.every(key => typeof value[key] === 'boolean') || !CODES.has(value.errorCode)
      || ![null, true, false].includes(value.targetMatch)
      || !Number.isInteger(value.responseBytes) || value.responseBytes < 0 || value.responseBytes > 65537
      || !Number.isInteger(value.elapsedMs) || value.elapsedMs < 0 || value.elapsedMs > 60000
      || ![value.responseStatus, value.lastRedirectStatus].every(code => code === null || Number.isInteger(code) && code >= 100 && code <= 599)) return null;
    return Object.fromEntries(['schemaVersion', 'transport', 'businessStartup', 'status', 'stage', ...BOOLS,
      'targetMatch', ...COUNTS, 'responseStatus', 'responseBytes', 'lastRedirectStatus', 'errorCode', 'elapsedMs'].map(key => [key, value[key]]));
  } catch { return null; }
}
function collect(child) {
  let buffer = '', bytes = 0, latest = null, stderrObserved = false;
  child.stdout.on('data', chunk => {
    bytes += chunk.length; if (bytes > 65536) { buffer = ''; return; }
    buffer += chunk.toString('utf8');
    let end;
    while ((end = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, end).trim(); buffer = buffer.slice(end + 1);
      if (!line.startsWith(PREFIX)) continue;
      try { const value = safeProbeResult(JSON.parse(line.slice(PREFIX.length))); if (value) latest = value; } catch { }
    }
  });
  child.stderr.on('data', chunk => { if (chunk.length) stderrObserved = true; });
  return { get result() { return latest; }, get stderrObserved() { return stderrObserved; } };
}
const RUNTIME_FILES = ['FundsTrace.exe', 'chrome_100_percent.pak', 'chrome_200_percent.pak', 'd3dcompiler_47.dll',
  'ffmpeg.dll', 'icudtl.dat', 'libEGL.dll', 'libGLESv2.dll', 'resources.pak', 'snapshot_blob.bin',
  'v8_context_snapshot.bin', 'vk_swiftshader.dll', 'vk_swiftshader_icd.json', 'vulkan-1.dll', 'chrome_elf.dll',
  'dxcompiler.dll', 'dxil.dll', 'LICENSE.electron.txt', 'LICENSES.chromium.html'];
async function copyRuntime(installRoot, temporary, deadline) {
  const runtime = path.join(temporary, 'runtime'); await fs.mkdir(runtime);
  for (const name of RUNTIME_FILES) {
    if (Date.now() >= deadline) throw new Error('PROBE_COPY_DEADLINE');
    const original = path.join(installRoot, name);
    const stat = await fs.lstat(original).catch(error => { if (error.code === 'ENOENT') return null; throw error; });
    if (!stat) { if (['FundsTrace.exe', 'icudtl.dat', 'resources.pak'].includes(name)) throw new Error('PROBE_RUNTIME'); continue; }
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('PROBE_RUNTIME');
    await fs.copyFile(original, path.join(runtime, name));
  }
  await fs.mkdir(path.join(runtime, 'locales'));
  for (const name of await fs.readdir(path.join(installRoot, 'locales'))) {
    if (!/^[A-Za-z0-9-]+\.pak$/.test(name)) continue;
    if (Date.now() >= deadline) throw new Error('PROBE_COPY_DEADLINE');
    const original = path.join(installRoot, 'locales', name), stat = await fs.lstat(original);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('PROBE_RUNTIME');
    await fs.copyFile(original, path.join(runtime, 'locales', name));
  }
  const application = path.join(runtime, 'resources', 'app'); await fs.mkdir(application, { recursive: true });
  await fs.copyFile(path.join(__dirname, 'electron-metadata-probe-main.cjs'), path.join(application, 'main.cjs'));
  await fs.writeFile(path.join(application, 'package.json'), JSON.stringify({ name: 'fundstrace-metadata-probe', version: '0.0.0', main: 'main.cjs' }));
  return runtime;
}
async function worker(config) {
  let child;
  try {
    const runtime = await copyRuntime(config.installRoot, config.temporary, config.deadline - 47000);
    if (Date.now() + 47000 > config.deadline) return;
    const userData = path.join(config.temporary, 'user-data'), sessionData = path.join(config.temporary, 'session-data');
    await fs.mkdir(userData); await fs.mkdir(sessionData);
    const asar = path.join(config.installRoot, 'resources', 'app.asar'), stat = await fs.lstat(asar);
    if (!stat.isFile() || stat.isSymbolicLink()) return;
    const filename = path.join(config.temporary, 'configuration.json');
    await fs.writeFile(filename, JSON.stringify({ asar, userData, sessionData, expected: config.expected }));
    child = spawn(path.join(runtime, 'FundsTrace.exe'), [`--user-data-dir=${userData}`],
      { cwd: runtime, env: probeEnvironment(filename), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    const observation = collect(child);
    await new Promise(resolve => { child.once('error', resolve); child.once('close', resolve); });
    if (observation.result) process.stdout.write(`${PREFIX}${JSON.stringify(observation.result)}\n`);
    if (observation.stderrObserved) process.stderr.write('PROBE_STDERR_OBSERVED');
  } catch { /* Parent reports unavailable, never the raw exception. */ }
}

async function safeInstalledElectronMetadataProbe(input, options = {}) {
  const outerDeadline = Date.now() + 60000;
  const report = { schemaVersion: 1, transport: 'independentCopiedElectronRuntime', businessStartup: false,
    status: 'unavailable', reason: 'configuration', runtimeCopied: false, profileIsolated: false,
    workerExited: false, treeTerminationSucceeded: null, cleanupSucceeded: false, stderrObserved: false, probe: null };
  let temporary, child, exited = false, timer;
  try {
    const installRoot = input?.installRoot, budgetMs = options?.budgetMs ?? 60000;
    if (process.platform !== 'win32' || process.env.GITHUB_ACTIONS !== 'true' || process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') { report.reason = 'environment'; return report; }
    if (typeof installRoot !== 'string' || !path.isAbsolute(installRoot) || installRoot.includes('\0')) return report;
    if (!Number.isInteger(budgetMs) || budgetMs < 60000) { report.status = 'notRun'; report.reason = 'budgetInsufficient'; return report; }
    const expected = input.version === undefined ? undefined : { version: input.version, installerSha512: input.installerSha512, installerBytes: input.installerBytes };
    if (expected && (typeof expected.version !== 'string' || !/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(expected.version)
      || typeof expected.installerSha512 !== 'string' || !/^[A-Za-z0-9+/]{86}==$/.test(expected.installerSha512)
      || !Number.isSafeInteger(expected.installerBytes) || expected.installerBytes <= 0 || expected.installerBytes > 1024 ** 3)) return report;
    const deadline = Date.now() + 55000;
    temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'fundstrace-metadata-probe-'));
    report.reason = 'launch';
    child = spawn(process.execPath, [__filename, '--metadata-probe-worker'],
      { env: probeEnvironment(), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe', 'ipc'] });
    const observation = collect(child);
    child.once('exit', () => { exited = true; });
    const outcome = await new Promise(resolve => {
      timer = setTimeout(() => resolve('timeout'), Math.max(0, deadline - Date.now()));
      child.once('error', () => resolve('launch')); child.once('close', () => resolve('closed'));
      child.stdin.end(JSON.stringify({ installRoot, temporary, deadline, expected }));
    });
    report.stderrObserved = observation.stderrObserved;
    report.workerExited = exited;
    report.probe = safeProbeResult(observation.result);
    report.runtimeCopied = !!report.probe; report.profileIsolated = report.probe?.profileIsolated === true;
    const available = report.probe && report.probe.status !== 'unavailable';
    report.reason = available ? 'completed' : outcome === 'timeout' ? 'timeout' : 'unavailable';
    report.status = available ? 'completed' : 'unavailable';
  } catch { report.reason = 'unavailable'; }
  finally {
    clearTimeout(timer);
    if (child && !exited && child.exitCode === null && child.signalCode === null) {
      report.treeTerminationSucceeded = false;
      try {
        // Keep the worker/Electron descendant tree intact until taskkill takes
        // ownership of the whole tree. Killing Electron first can orphan helpers.
        await new Promise(resolve => {
          const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
          const timer = setTimeout(() => { if (killer.exitCode === null && killer.signalCode === null) { try { killer.kill(); } catch { } } resolve(); }, 2000);
          killer.once('error', () => { clearTimeout(timer); resolve(); });
          killer.once('close', code => {
            report.treeTerminationSucceeded = code === 0;
            if (exited || code !== 0) { clearTimeout(timer); resolve(); }
            else child.once('exit', () => { clearTimeout(timer); resolve(); });
          });
        });
      } catch { }
    }
    if (temporary) {
      try {
        let cleanupTimer;
        report.cleanupSucceeded = await Promise.race([
          fs.rm(temporary, { recursive: true, force: true, maxRetries: 2, retryDelay: 100 }).then(() => true, () => false),
          new Promise(resolve => { cleanupTimer = setTimeout(() => resolve(false), Math.max(0, Math.min(2500, outerDeadline - Date.now()))); }),
        ]);
        clearTimeout(cleanupTimer);
      }
      catch { report.cleanupSucceeded = false; }
    }
    report.workerExited = exited;
    if (temporary && (!report.cleanupSucceeded || child && !exited || report.treeTerminationSucceeded === false)) { report.status = 'unavailable'; report.reason = 'cleanup'; }
  }
  return report;
}

if (require.main === module && process.argv[2] === '--metadata-probe-worker') {
  let bytes = 0, input = '';
  process.stdin.on('data', chunk => { bytes += chunk.length; if (bytes <= 16384) input += chunk.toString('utf8'); });
  process.stdin.on('end', () => {
    if (bytes > 16384) { process.exit(0); return; }
    Promise.resolve().then(() => worker(JSON.parse(input))).finally(() => process.exit(0));
  });
}
module.exports = { safeInstalledElectronMetadataProbe, safeProbeResult, copyRuntime, probeEnvironment };
