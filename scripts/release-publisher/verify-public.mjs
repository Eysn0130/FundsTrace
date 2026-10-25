import { pathToFileURL } from 'node:url';
import { get as httpsGet } from 'node:https';
import { isIPv4 } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';
import { selectRelease, validateCurrent } from './publish.mjs';

const networkCodes = new Set(['UND_ERR_CONNECT_TIMEOUT', 'ECONNRESET', 'EAI_AGAIN',
  'ETIMEDOUT', 'ECONNREFUSED', 'ENETUNREACH', 'EHOSTUNREACH']);
const diagnosticCodes = new Set([...networkCodes, 'ABORT_ERR', 'ENOTFOUND',
  'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE']);
const delays = [2000, 5000];
const publicHosts = new Set(['api.github.com', 'analytix.top', 'download.analytix.top']);
const failureStages = new WeakMap();

// Aggregate connection attempts may contain different errors. Every branch,
// including an explicit outer code, must permit retry; never cherry-pick one.
function classifyFailure(error, deadline, seen = new Set()) {
  if (!error || typeof error !== 'object' || seen.has(error)) return { code: 'UNKNOWN', retry: false };
  seen.add(error);
  if (deadline.aborted && error === deadline.reason) return { code: 'DEADLINE', retry: true };
  const code = diagnosticCodes.has(error.code) ? error.code
    : error.name === 'AbortError' ? 'ABORTED' : error.name === 'TimeoutError' ? 'TIMEOUT' : 'UNKNOWN';
  if (error.code !== undefined && !networkCodes.has(error.code) && error.code !== 'ABORT_ERR')
    return { code, retry: false };
  if (error.errors !== undefined) {
    if (!Array.isArray(error.errors) || !error.errors.length) return { code: 'UNKNOWN', retry: false };
    const children = [...error.errors, ...(error.cause === undefined ? [] : [error.cause])]
      .map(value => classifyFailure(value, deadline, new Set(seen)));
    return children.every(value => value.retry) && (error.code === undefined || networkCodes.has(error.code))
      ? { code: 'NETWORK_AGGREGATE', retry: true } : { code: 'AGGREGATE_REJECTED', retry: false };
  }
  if (deadline.aborted && error.cause === deadline.reason
      && (error.code === undefined || error.code === 'ABORT_ERR')) return { code: 'DEADLINE', retry: true };
  if (error.name === 'AbortError' || error.name === 'TimeoutError' || error.code === 'ABORT_ERR')
    return { code, retry: false };
  if (error.cause !== undefined) {
    const cause = classifyFailure(error.cause, deadline, seen);
    if (!cause.retry) return cause;
    return { code: networkCodes.has(error.code) ? error.code : cause.code, retry: true };
  }
  return { code, retry: networkCodes.has(error.code) };
}

export async function resolvePublicAddresses(hostname, { signal, fetchImpl = fetch } = {}) {
  if (!publicHosts.has(hostname) || !(signal instanceof AbortSignal)) throw new Error('Invalid public DNS request');
  const url = new URL('https://dns.google/resolve');
  url.searchParams.set('name', hostname);
  url.searchParams.set('type', 'A');
  url.searchParams.set('edns_client_subnet', '0.0.0.0/0');
  const response = await fetchImpl(url.href, { method: 'GET', redirect: 'error', signal,
    headers: { Accept: 'application/json', 'User-Agent': 'FundsTrace-public-verifier' } });
  if (!response.ok || response.redirected) throw new Error('Public DNS response is unavailable');
  const document = await response.json();
  const question = document?.Question;
  if (document?.Status !== 0 || document.TC !== false || document.CD !== false
      || !Array.isArray(question) || question.length !== 1 || question[0]?.type !== 1
      || typeof question[0].name !== 'string' || question[0].name.toLowerCase().replace(/\.$/, '') !== hostname
      || !Array.isArray(document.Answer)) throw new Error('Invalid public DNS response');
  const records = document.Answer.filter(answer => answer?.type === 1);
  if (!records.length || records.some(answer => !isIPv4(answer.data))) throw new Error('Invalid public DNS addresses');
  signal.throwIfAborted();
  return [...new Set(records.map(answer => answer.data))].map(address => ({ address, family: 4 }));
}

// The default uses system DNS. The optional resolver changes only addresses;
// the original URL still owns SNI and certificate verification in both modes.
export async function fetchPublicNative(url, { method, headers, signal }, getImpl = httpsGet, resolveImpl) {
  let stage = 'dns';
  try {
    const hostname = new URL(url).hostname;
    const addresses = resolveImpl ? await resolveImpl(hostname, { signal }) : undefined;
    signal.throwIfAborted();
    return await new Promise((resolve, reject) => {
      let settled = false;
      const fail = error => { if (!settled) { settled = true; reject(error); } };
      const options = { method, headers, signal, family: 0, autoSelectFamily: true, agent: false,
        rejectUnauthorized: true };
      if (addresses) options.lookup = (name, lookupOptions, callback) => {
        if (name !== hostname) return callback(new Error('Invalid public DNS hostname'));
        if (lookupOptions.all) callback(null, addresses);
        else callback(null, addresses[0].address, addresses[0].family);
      };
      const request = getImpl(url, options, response => {
        stage = 'body';
        response.on('error', fail);
        if (!Number.isInteger(response.statusCode) || response.statusCode < 200 || response.statusCode >= 300) {
          stage = 'headers';
          fail(new Error('Public release metadata is unavailable'));
          response.destroy();
          return;
        }
        const chunks = [];
        response.on('data', chunk => chunks.push(Buffer.from(chunk)));
        response.on('end', () => {
          if (settled) return;
          settled = true;
          resolve(new Response(Buffer.concat(chunks)));
        });
      });
      request.on('socket', socket => {
        if (addresses || isIPv4(hostname)) stage = 'tcp';
        socket.once('lookup', error => { if (!error) stage = 'tcp'; });
        socket.once('connect', () => { stage = 'tls'; });
        socket.once('secureConnect', () => { stage = 'headers'; });
      });
      request.on('error', fail);
    });
  } catch (error) {
    if (error && typeof error === 'object') failureStages.set(error, stage);
    throw error;
  }
}

export async function verifyPublic({ expectedTag = process.env.EXPECTED_TAG, run = process.env.GITHUB_RUN_ID,
  fetchImpl = fetch, nativeFetchImpl = fetchPublicNative,
  dohFetchImpl = (url, options) => fetchPublicNative(url, options, httpsGet, resolvePublicAddresses),
  wait = sleep, report = value => console.log(JSON.stringify(value)) } = {}) {
  const endpoints = [
    { label: 'github', url: 'https://api.github.com/repos/Eysn0130/FundsTrace/releases/latest' },
    { label: 'site', url: `https://analytix.top/fundstrace/latest.yml?verify=${run}` },
    { label: 'download', url: `https://download.analytix.top/releases/windows/site/fundstrace/latest.yml?verify=${run}` },
  ];
  const strategies = [['fetch', fetchImpl], ['https-system', nativeFetchImpl], ['https-doh', dohFetchImpl]];
  const operation = new AbortController();
  const requests = endpoints.map(async ({ label, url }) => {
    for (let attempt = 1; attempt <= strategies.length; attempt++) {
      operation.signal.throwIfAborted();
      const [transport, request] = strategies[attempt - 1];
      const deadline = AbortSignal.timeout(30000);
      const signal = AbortSignal.any([deadline, operation.signal]);
      let stage = 'request';
      try {
        const response = await request(url, { method: 'GET',
          headers: { Accept: 'application/json', 'User-Agent': 'FundsTrace-public-verifier' },
          redirect: 'error', signal });
        stage = 'headers';
        if (!response.ok || response.redirected) throw new Error('Public release metadata is unavailable');
        stage = 'body';
        const document = await response.json();
        signal.throwIfAborted();
        report({ endpoint: label, attempt, transport, stage, event: 'json-read' });
        return document;
      } catch (error) {
        if (operation.signal.aborted) throw error;
        const failure = classifyFailure(error, deadline);
        report({ endpoint: label, attempt, transport, stage: failureStages.get(error) || stage, code: failure.code });
        if (!failure.retry || attempt === strategies.length) {
          operation.abort(new DOMException('Public verification stopped', 'AbortError'));
          throw error;
        }
        await wait(delays[attempt - 1], undefined, { signal: operation.signal });
      }
    }
  });
  let github, documents;
  try {
    [github, ...documents] = await Promise.all(requests);
  } catch (error) {
    operation.abort(new DOMException('Public verification stopped', 'AbortError'));
    await Promise.allSettled(requests);
    throw error;
  }
  const latest = selectRelease(github);
  for (const document of documents) {
    const value = validateCurrent(document);
    if (`v${value.version}` !== expectedTag || value.version !== latest.version
        || value.githubReleaseId !== latest.releaseId || value.githubAssetId !== latest.asset.id
        || value.checksumSha256 !== latest.asset.digest.slice(7) || value.files[0].size !== latest.asset.size
        || value.releaseDate !== latest.releaseDate || value.releaseNotes !== latest.releaseNotes)
      throw new Error('Public version, installer or release notes differ from GitHub');
  }
  return latest;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  verifyPublic().then(latest => {
    console.log(`Public FundsTrace ${latest.version} version, installer and release notes verified`);
  }).catch(() => {
    console.error('Public FundsTrace metadata verification failed; inspect the fixed endpoint diagnostics.');
    process.exitCode = 1;
  });
}
