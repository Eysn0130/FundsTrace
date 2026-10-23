'use strict';
// Post-failure evidence only. This never retries or changes the installed updater.
const https = require('node:https');
const { createHash } = require('node:crypto');

const VERSION = /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/;
const SHA256 = /^[a-f0-9]{64}$/;
const SHA512 = /^[A-Za-z0-9+/]{86}==$/;
const ERROR_CODES = new Set(['ENOTFOUND', 'EAI_AGAIN', 'ECONNREFUSED', 'ECONNRESET', 'ECONNABORTED',
  'ETIMEDOUT', 'EHOSTUNREACH', 'ENETUNREACH', 'EPIPE', 'ERR_STREAM_PREMATURE_CLOSE',
  'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'CERT_NOT_YET_VALID',
  'DEPTH_ZERO_SELF_SIGNED_CERT', 'SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE',
  'UNABLE_TO_GET_ISSUER_CERT_LOCALLY', 'ERR_SSL_WRONG_VERSION_NUMBER', 'ERR_SSL_TLSV1_ALERT_INTERNAL_ERROR']);
function safeErrorCode(error) {
  try { return ERROR_CODES.has(error?.code) ? error.code : 'UNKNOWN'; }
  catch { return 'UNKNOWN'; }
}
const emptyResult = () => ({ kind: 'notRun', statusCode: null, bytes: 0, elapsedMs: 0,
  completed: false, tlsEstablished: false, responseStarted: false, budgetLimited: false, errorCode: 'NONE',
  expectedSizeMatch: null, sha256Match: null, sha512Match: null, metadataValid: null,
  versionMatch: null, productMatch: null, installerUrlMatch: null });

function validConfig(value) {
  return !!value && typeof value.version === 'string' && VERSION.test(value.version)
    && typeof value.currentVersion === 'string' && VERSION.test(value.currentVersion)
    && typeof value.installerSha256 === 'string' && SHA256.test(value.installerSha256)
    && typeof value.installerSha512 === 'string' && SHA512.test(value.installerSha512)
    && Number.isSafeInteger(value.installerBytes) && value.installerBytes > 0 && value.installerBytes <= 1024 ** 3;
}

function inspectMetadata(body, config, installerUrl) {
  let info;
  try { info = JSON.parse(body.toString('utf8')); }
  catch { return { kind: 'invalidMetadata', metadataValid: false }; }
  const file = info && Array.isArray(info.files) && info.files.length === 1 ? info.files[0] : null;
  const metadataValid = !!info && typeof info.version === 'string' && VERSION.test(info.version) && info.product === 'com.eysn.fundstrace'
    && !info.packages && !!file && typeof file.url === 'string' && typeof file.sha512 === 'string' && SHA512.test(file.sha512)
    && Number.isSafeInteger(file.size) && file.size > 0 && file.size <= 1024 ** 3;
  const checks = { metadataValid, versionMatch: info?.version === config.version,
    productMatch: info?.product === 'com.eysn.fundstrace', installerUrlMatch: file?.url === installerUrl,
    expectedSizeMatch: file?.size === config.installerBytes, sha512Match: file?.sha512 === config.installerSha512 };
  return { ...checks, kind: Object.values(checks).every(value => value === true) ? 'verified' : 'mismatch' };
}

function readEndpoint(url, { limit, timeoutMs, budgetLimited, config, installerUrl, metadata }) {
  return new Promise(resolve => {
    const startedAt = Date.now(), result = emptyResult(), chunks = [];
    result.budgetLimited = budgetLimited;
    const sha256 = metadata ? null : createHash('sha256');
    const sha512 = metadata ? null : createHash('sha512');
    let request, response, settled = false, timer;
    const finish = (kind, extra = {}) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      Object.assign(result, extra, { kind, elapsedMs: Math.min(300000, Math.max(0, Date.now() - startedAt)) });
      if (!result.completed) {
        try { response?.destroy(); } catch { }
        try { request?.destroy(); } catch { }
      }
      resolve(result);
    };
    timer = setTimeout(() => finish(budgetLimited ? 'budgetExhausted'
      : result.responseStarted ? 'readTimeout' : result.tlsEstablished ? 'responseTimeout' : 'connectTimeout',
    { errorCode: budgetLimited ? 'NONE' : 'ETIMEDOUT' }), timeoutMs);
    try {
      // Match only the updater's public request shape. Transport remains independent
      // Node HTTPS; no runtime headers, credentials, proxy, redirects or TLS overrides.
      const headers = { 'User-Agent': 'electron-builder', 'Cache-Control': 'no-cache',
        ...(metadata ? {} : { accept: '*/*' }) };
      request = https.get(url, { headers }, incoming => {
        response = incoming; result.responseStarted = true; result.tlsEstablished = true;
        result.statusCode = Number.isInteger(incoming.statusCode) && incoming.statusCode >= 100 && incoming.statusCode <= 599
          ? incoming.statusCode : null;
        incoming.on('data', chunk => {
          if (settled) return;
          try {
            result.bytes += chunk.length;
            if (result.bytes > limit) { finish('tooLarge', { expectedSizeMatch: false }); return; }
            if (metadata) chunks.push(chunk);
            else { sha256.update(chunk); sha512.update(chunk); }
          } catch (error) { finish('transportError', { errorCode: safeErrorCode(error) }); }
        });
        incoming.once('aborted', () => finish('transportError', { errorCode: 'ERR_STREAM_PREMATURE_CLOSE' }));
        incoming.once('error', error => finish('transportError', { errorCode: safeErrorCode(error) }));
        incoming.once('close', () => { if (!settled) finish('transportError', { errorCode: 'ERR_STREAM_PREMATURE_CLOSE' }); });
        incoming.once('end', () => {
          if (settled) return;
          try {
            result.completed = true;
            if (metadata) {
              if (result.statusCode !== 200) { finish('httpError'); return; }
              const checks = inspectMetadata(Buffer.concat(chunks), config, installerUrl);
              finish(checks.kind, checks);
            } else {
              const checks = { expectedSizeMatch: result.bytes === config.installerBytes,
                sha256Match: sha256.digest('hex') === config.installerSha256,
                sha512Match: sha512.digest('base64') === config.installerSha512 };
              finish(result.statusCode !== 200 ? 'httpError'
                : Object.values(checks).every(value => value === true) ? 'verified' : 'mismatch', checks);
            }
          } catch (error) { finish('transportError', { errorCode: safeErrorCode(error) }); }
        });
      });
      request.once('socket', socket => socket.once('secureConnect', () => { result.tlsEstablished = true; }));
      request.once('error', error => finish('transportError', { errorCode: safeErrorCode(error) }));
    } catch (error) { finish('transportError', { errorCode: safeErrorCode(error) }); }
  });
}

async function safeProductionTransportDiagnostic(config, options = {}) {
  const startedAt = Date.now();
  const report = { schemaVersion: 1, transport: 'independentNodeHttps', metadataNoCacheQuery: true,
    kind: 'configurationRejected', configurationValid: false,
    tlsVerificationEnabled: true, metadata: emptyResult(), installer: emptyResult() };
  try {
    // Freeze only the expected scalar inputs before validating or constructing URLs.
    config = { version: config?.version, currentVersion: config?.currentVersion,
      installerSha256: config?.installerSha256, installerSha512: config?.installerSha512, installerBytes: config?.installerBytes };
    if (!validConfig(config)) return report;
    report.configurationValid = true;
    const suppliedBudget = options?.budgetMs;
    const budgetMs = suppliedBudget === undefined ? 285000 : suppliedBudget;
    if (!Number.isInteger(budgetMs) || budgetMs < 0 || budgetMs > 285000) { report.kind = 'budgetRejected'; return report; }
    if (process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') {
      report.kind = 'environmentRejected'; report.tlsVerificationEnabled = false; return report;
    }
    const installerUrl = `https://download.analytix.top/releases/fundstrace/windows/${config.version}/FundsTrace-${config.version}-windows-x64-unsigned.exe`;
    const remaining = () => Math.max(0, budgetMs - Math.max(0, Date.now() - startedAt));
    for (const [key, url, limit, maximum] of [
      ['metadata', `https://analytix.top/fundstrace/latest.yml?noCache=${Date.now().toString(32)}`, 65536, 45000],
      ['installer', installerUrl, config.installerBytes, 240000],
    ]) {
      const available = remaining();
      if (!available) { report[key].kind = 'budgetExhausted'; report[key].budgetLimited = true; continue; }
      report[key] = await readEndpoint(url, { limit, timeoutMs: Math.min(maximum, available),
        budgetLimited: available < maximum, config, installerUrl, metadata: key === 'metadata' });
    }
    report.kind = [report.metadata, report.installer].some(value => value.kind === 'budgetExhausted') ? 'budgetExhausted' : 'completed';
    return report;
  } catch {
    report.kind = 'diagnosticError';
    return report;
  }
}

module.exports = { safeProductionTransportDiagnostic };
