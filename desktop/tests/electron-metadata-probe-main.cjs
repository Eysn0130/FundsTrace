'use strict';
// Disposable diagnostic Main only. Never loads the application's Main or backend.
const fs = require('node:fs');
const path = require('node:path');
const CODES = new Set(['ECONNREFUSED', 'ECONNRESET', 'ETIMEDOUT', 'ENOTFOUND', 'ERR_FAILED',
  'ERR_ABORTED', 'ERR_CONNECTION_REFUSED', 'ERR_CONNECTION_RESET', 'ERR_NAME_NOT_RESOLVED',
  'ERR_TIMED_OUT', 'ERR_CONNECTION_TIMED_OUT', 'ERR_PROXY_CONNECTION_FAILED', 'ERR_TUNNEL_CONNECTION_FAILED',
  'ERR_NO_SUPPORTED_PROXIES', 'ERR_PROXY_AUTH_UNSUPPORTED', 'ERR_NETWORK_CHANGED', 'ERR_ADDRESS_UNREACHABLE', 'ERR_SSL_PROTOCOL_ERROR',
  'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_COMMON_NAME_INVALID', 'ERR_CERT_DATE_INVALID',
  'ERR_UPDATER_CHANNEL_FILE_NOT_FOUND', 'ERR_UPDATER_INVALID_UPDATE_INFO', 'ERR_TOO_MANY_REDIRECTS']);
function errorCode(error) {
  try { return CODES.has(error?.code) ? error.code : [...CODES].find(code => error?.message === `net::${code}`) || 'UNKNOWN'; }
  catch { return 'UNKNOWN'; }
}
function emptyProbe() {
  return { schemaVersion: 1, transport: 'independentCopiedElectronRuntime', businessStartup: false,
    status: 'unavailable', stage: 'setup', profileIsolated: false, providerResolved: false, metadataVersionStable: false,
    metadataValid: false, targetMatch: null, requestCount: 0, finishCount: 0, responseCount: 0, closeCount: 0, responseStatus: null, responseBytes: 0,
    loginObserved: false, socketObserved: false, redirectCount: 0, lastRedirectStatus: null,
    unknownRedirectRejected: false, requestLimitExceeded: false, bodyLimitExceeded: false, errorObserved: false,
    errorCode: 'NONE', elapsedMs: 0 };
}
function allowedUrl(value, noCache) {
  try {
    const url = new URL(value);
    return url.origin === 'https://analytix.top' && url.pathname === '/fundstrace/latest.yml'
      && !url.username && !url.password && !url.hash && url.searchParams.size === 1
      && /^[0-9a-v]+$/.test(url.searchParams.get('noCache') || '')
      && (noCache === null || url.searchParams.get('noCache') === noCache);
  } catch { return false; }
}

async function runMetadataProbe({ app, GenericProvider, ElectronHttpExecutor, emit, expected, profileIsolated = false }) {
  const result = emptyProbe(), requests = new Set(), startedAt = Date.now();
  result.profileIsolated = profileIsolated === true;
  let finished = false, noCache = null;
  const finish = (status, code = 'NONE') => {
    if (finished) return;
    finished = true; clearTimeout(timer);
    result.status = status; result.errorCode = code;
    result.elapsedMs = Math.min(60000, Math.max(0, Date.now() - startedAt));
    for (const request of requests) { try { request.abort(); } catch { } }
    emit(result);
  };
  const timer = setTimeout(() => finish('timeout'), 45000);
  const blocked = () => { const error = new Error('PROBE_REQUEST_REJECTED'); error.code = 'ERR_ABORTED'; return error; };
  const permit = options => {
    try {
      const url = new URL(options.path, `${options.protocol}//${options.hostname}${options.port ? `:${options.port}` : ''}`);
      if (!allowedUrl(url.href, noCache)) return false;
      if (noCache === null) noCache = url.searchParams.get('noCache');
      return true;
    } catch { return false; }
  };
  class ObservedExecutor extends ElectronHttpExecutor {
    createRequest(options, callback) {
      if (finished || !permit(options)) { result.unknownRedirectRejected = true; finish('failed', 'ERR_ABORTED'); throw blocked(); }
      if (result.requestCount >= 16) { result.requestLimitExceeded = true; finish('failed', 'ERR_ABORTED'); throw blocked(); }
      result.requestCount++;
      const request = super.createRequest(options, response => {
        result.responseCount = Math.min(16, result.responseCount + 1);
        result.responseStatus = Number.isInteger(response.statusCode) && response.statusCode >= 100 && response.statusCode <= 599 ? response.statusCode : null;
        response.on('data', chunk => {
          if (finished) return;
          result.responseBytes = Math.min(65537, result.responseBytes + chunk.length);
          if (result.responseBytes > 65536) { result.bodyLimitExceeded = true; finish('failed', 'ERR_ABORTED'); }
        });
        response.on('error', error => { if (!finished) { result.errorObserved = true; result.errorCode = errorCode(error); } });
        callback(response);
      });
      requests.add(request);
      request.on('finish', () => { result.finishCount = Math.min(16, result.finishCount + 1); });
      request.on('login', () => { result.loginObserved = true; }); // Do not inspect authInfo or invoke its callback.
      request.on('socket', () => { result.socketObserved = true; });
      request.on('redirect', status => {
        result.redirectCount = Math.min(16, result.redirectCount + 1);
        result.lastRedirectStatus = Number.isInteger(status) && status >= 300 && status <= 399 ? status : null;
      });
      request.on('error', error => { if (!finished) { result.errorObserved = true; result.errorCode = errorCode(error); } });
      request.on('close', () => { result.closeCount = Math.min(16, result.closeCount + 1); requests.delete(request); });
      return request;
    }
    addRedirectHandlers(request, options, reject, count, handler) {
      return super.addRedirectHandlers(request, options, reject, count, next => {
        if (!permit(next)) { result.unknownRedirectRejected = true; reject(blocked()); finish('failed', 'ERR_ABORTED'); return; }
        handler(next);
      });
    }
  }
  try {
    result.stage = 'ready'; await app.whenReady();
    if (finished) return;
    result.stage = 'request';
    const provider = new GenericProvider({ provider: 'generic', url: 'https://analytix.top/fundstrace/' },
      { channel: null, isAddNoCacheQuery: true }, { executor: new ObservedExecutor(), platform: 'win32', isUseMultipleRangeRequest: false });
    provider.setRequestHeaders({ 'User-Agent': 'electron-builder', 'Cache-Control': 'no-cache' });
    const info = await provider.getLatestVersion();
    if (finished) return;
    result.providerResolved = true;
    result.metadataVersionStable = typeof info?.version === 'string' && /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(info.version);
    const file = Array.isArray(info?.files) && info.files.length === 1 ? info.files[0] : null;
    result.metadataValid = result.metadataVersionStable && info.product === 'com.eysn.fundstrace' && !info.packages && !!file
      && file.url === `https://download.analytix.top/releases/fundstrace/windows/${info.version}/FundsTrace-${info.version}-windows-x64-unsigned.exe`
      && typeof file.sha512 === 'string' && /^[A-Za-z0-9+/]{86}==$/.test(file.sha512)
      && Number.isSafeInteger(file.size) && file.size > 0 && file.size <= 1024 ** 3;
    result.targetMatch = expected ? result.metadataValid && info.version === expected.version
      && file.sha512 === expected.installerSha512 && file.size === expected.installerBytes : null;
    result.stage = 'complete'; finish('completed');
  } catch (error) { finish(result.stage === 'request' ? 'failed' : 'unavailable', errorCode(error)); }
}

function boot() {
  let app;
  const emit = result => {
    process.stdout.write(`FT_ELECTRON_METADATA ${JSON.stringify(result)}\n`, () => app ? app.exit(0) : process.exit(0));
  };
  try {
    ({ app } = require('electron'));
    const filename = process.env.FUNDS_METADATA_PROBE_CONFIG;
    const bytes = fs.readFileSync(filename);
    if (bytes.length > 16384) throw new Error('PROBE_CONFIGURATION');
    const config = JSON.parse(bytes.toString('utf8'));
    if (!path.isAbsolute(config.asar) || path.basename(config.asar) !== 'app.asar'
      || !path.isAbsolute(config.userData) || !path.isAbsolute(config.sessionData)) throw new Error('PROBE_CONFIGURATION');
    app.setName('FundsTrace metadata diagnostic');
    app.setPath('userData', config.userData); app.setPath('sessionData', config.sessionData);
    const base = path.join(config.asar, 'node_modules/electron-updater/out');
    const { GenericProvider } = require(path.join(base, 'providers/GenericProvider.js'));
    const { ElectronHttpExecutor } = require(path.join(base, 'electronHttpExecutor.js'));
    if (typeof GenericProvider !== 'function' || typeof ElectronHttpExecutor !== 'function'
      || !['getLatestVersion', 'setRequestHeaders'].every(name => typeof GenericProvider.prototype[name] === 'function')
      || !['createRequest', 'addRedirectHandlers', 'request'].every(name => typeof ElectronHttpExecutor.prototype[name] === 'function')) throw new Error('PROBE_DEPENDENCY');
    runMetadataProbe({ app, GenericProvider, ElectronHttpExecutor, emit, expected: config.expected, profileIsolated: true }).catch(() => emit(emptyProbe()));
  } catch { emit(emptyProbe()); }
}

if (require.main === module) boot();
module.exports = { runMetadataProbe, emptyProbe, allowedUrl };
