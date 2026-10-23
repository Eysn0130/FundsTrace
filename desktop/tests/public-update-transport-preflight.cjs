'use strict';
// Disposable CI preflight only; it does not populate the installed updater cache.
const fs = require('node:fs/promises');
const path = require('node:path');
const baseline = require('../upgrade-baseline.json');
const { version } = require('../package.json');
const { safeProductionTransportDiagnostic } = require('./installed-update-transport-diagnostic.cjs');

const VERSION = /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/;
const ERROR_CODES = new Set(['NONE', 'UNKNOWN', 'ENOTFOUND', 'EAI_AGAIN', 'ECONNREFUSED', 'ECONNRESET', 'ECONNABORTED',
  'ETIMEDOUT', 'EHOSTUNREACH', 'ENETUNREACH', 'EPIPE', 'ERR_STREAM_PREMATURE_CLOSE',
  'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'CERT_NOT_YET_VALID', 'DEPTH_ZERO_SELF_SIGNED_CERT',
  'SELF_SIGNED_CERT_IN_CHAIN', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY',
  'ERR_SSL_WRONG_VERSION_NUMBER', 'ERR_SSL_TLSV1_ALERT_INTERNAL_ERROR']);
const RESULT_KINDS = new Set(['notRun', 'verified', 'mismatch', 'invalidMetadata', 'httpError', 'tooLarge',
  'connectTimeout', 'responseTimeout', 'readTimeout', 'transportError', 'budgetExhausted']);
const BOOLS = ['lookupObserved', 'tcpEstablished', 'completed', 'tlsEstablished', 'responseStarted', 'budgetLimited'];
const NULLABLE = ['lookupSucceeded', 'expectedSizeMatch', 'sha256Match', 'sha512Match', 'metadataValid', 'versionMatch', 'productMatch', 'installerUrlMatch'];
function safeEndpoint(value) {
  if (!value || !RESULT_KINDS.has(value.kind) || !ERROR_CODES.has(value.errorCode) || !ERROR_CODES.has(value.lookupErrorCode)
    || !(value.statusCode === null || Number.isInteger(value.statusCode) && value.statusCode >= 100 && value.statusCode <= 599)
    || !Number.isSafeInteger(value.bytes) || value.bytes < 0 || value.bytes > 1024 ** 3 + 65536
    || !Number.isInteger(value.elapsedMs) || value.elapsedMs < 0 || value.elapsedMs > 300000
    || !BOOLS.every(key => typeof value[key] === 'boolean') || !NULLABLE.every(key => [null, true, false].includes(value[key]))) return null;
  return Object.fromEntries(['kind', 'statusCode', 'bytes', 'elapsedMs', ...BOOLS, 'errorCode', 'lookupErrorCode', ...NULLABLE].map(key => [key, value[key]]));
}
function safeDiagnostic(value) {
  try {
    const metadata = safeEndpoint(value?.metadata), installer = safeEndpoint(value?.installer);
    if (!metadata || !installer || value.schemaVersion !== 1 || value.transport !== 'independentNodeHttps'
      || typeof value.metadataNoCacheQuery !== 'boolean' || typeof value.configurationValid !== 'boolean'
      || typeof value.tlsVerificationEnabled !== 'boolean'
      || !['configurationRejected', 'environmentRejected', 'budgetRejected', 'budgetExhausted', 'completed', 'diagnosticError'].includes(value.kind)) return null;
    return { schemaVersion: 1, transport: 'independentNodeHttps', metadataNoCacheQuery: value.metadataNoCacheQuery,
      kind: value.kind, configurationValid: value.configurationValid, tlsVerificationEnabled: value.tlsVerificationEnabled, metadata, installer };
  } catch { return null; }
}
function passed(value, bytes) {
  if (!value || value.kind !== 'completed' || value.configurationValid !== true || value.tlsVerificationEnabled !== true
    || value.metadataNoCacheQuery !== true) return false;
  const metadata = value.metadata, installer = value.installer;
  const complete = result => result.kind === 'verified' && result.statusCode === 200 && result.completed === true
    && result.tlsEstablished === true && result.responseStarted === true && result.errorCode === 'NONE';
  return complete(metadata) && complete(installer) && metadata.bytes > 0 && metadata.bytes <= 65536
    && ['metadataValid', 'versionMatch', 'productMatch', 'installerUrlMatch', 'expectedSizeMatch', 'sha512Match'].every(key => metadata[key] === true)
    && installer.bytes === bytes && ['expectedSizeMatch', 'sha256Match', 'sha512Match'].every(key => installer[key] === true);
}
function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 10 || !argv.every(value => typeof value === 'string')) return null;
  const flags = new Set(['--report', '--version', '--sha256', '--sha512', '--bytes']), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]; if (!flags.has(key) || Object.hasOwn(values, key)) return null;
    values[key] = argv[index + 1];
  }
  const size = values['--bytes'];
  if (!path.isAbsolute(values['--report']) || values['--report'].includes('\0') || values['--report'].length > 32767
    || !VERSION.test(values['--version']) || values['--version'] !== version || values['--version'] !== baseline.targetVersion
    || !VERSION.test(baseline.prior.version) || !/^[a-f0-9]{64}$/.test(values['--sha256'])
    || !/^[A-Za-z0-9+/]{86}==$/.test(values['--sha512']) || !/^[1-9]\d{0,9}$/.test(size)
    || !Number.isSafeInteger(Number(size)) || Number(size) > 1024 ** 3) return null;
  return { report: values['--report'], config: { version: values['--version'], currentVersion: baseline.prior.version,
    installerSha256: values['--sha256'], installerSha512: values['--sha512'], installerBytes: Number(size) } };
}

async function runPreflight(argv) {
  let handle, exitCode = 2;
  try {
    if (process.platform !== 'win32' || process.env.GITHUB_ACTIONS !== 'true' || process.env.NODE_TLS_REJECT_UNAUTHORIZED === '0') return 2;
    const input = parseArguments(argv); if (!input) return 2;
    // Reserve the report before network work; an existing report is never replaced.
    handle = await fs.open(input.report, 'wx');
    let raw;
    try { raw = await safeProductionTransportDiagnostic(input.config, { budgetMs: 285000 }); } catch { }
    const diagnostic = safeDiagnostic(raw), success = passed(diagnostic, input.config.installerBytes);
    const report = { schemaVersion: 1, kind: 'publicUpdateTransportPreflight',
      status: success ? 'passed' : 'failed', passed: success,
      failureKind: success ? 'NONE' : diagnostic ? 'transport' : 'diagnosticUnavailable', diagnostic };
    await handle.writeFile(`${JSON.stringify(report)}\n`, 'utf8');
    exitCode = success ? 0 : 1;
  } catch { exitCode = 2; }
  finally { if (handle) { try { await handle.close(); } catch { exitCode = 2; } } }
  return exitCode;
}

if (require.main === module) runPreflight(process.argv.slice(2)).then(code => { process.exitCode = code; }, () => { process.exitCode = 2; });
module.exports = { runPreflight, parseArguments };
