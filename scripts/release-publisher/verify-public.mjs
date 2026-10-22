import { pathToFileURL } from 'node:url';
import { get as httpsGet } from 'node:https';
import { setTimeout as sleep } from 'node:timers/promises';
import { selectRelease, validateCurrent } from './publish.mjs';

const networkCodes = new Set(['UND_ERR_CONNECT_TIMEOUT', 'ECONNRESET', 'EAI_AGAIN']);
const diagnosticCodes = new Set([...networkCodes, 'ABORT_ERR', 'ENOTFOUND', 'ECONNREFUSED', 'ETIMEDOUT',
  'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE']);
const delays = [2000, 5000];

// Keep TLS hostname/certificate verification while bypassing Undici's separate
// 10-second connector timer after a connection timeout. The request signal owns
// the complete DNS, connection, TLS and response-body budget.
export function fetchPublicNative(url, { method, headers, signal }, getImpl = httpsGet) {
  return new Promise((resolve, reject) => {
    const request = getImpl(url, { method, headers, signal, family: 0, autoSelectFamily: true, agent: false,
      rejectUnauthorized: true }, response => {
      response.on('error', reject);
      if (!Number.isInteger(response.statusCode) || response.statusCode < 200 || response.statusCode >= 300) {
        response.destroy();
        reject(new Error('Public release metadata is unavailable'));
        return;
      }
      const chunks = [];
      response.on('data', chunk => chunks.push(Buffer.from(chunk)));
      response.on('end', () => resolve(new Response(Buffer.concat(chunks))));
    });
    request.on('error', reject);
  });
}

export async function verifyPublic({ expectedTag = process.env.EXPECTED_TAG, run = process.env.GITHUB_RUN_ID,
  fetchImpl = fetch, fallbackFetchImpl = fetchPublicNative, wait = sleep,
  report = value => console.log(JSON.stringify(value)) } = {}) {
  const endpoints = [
    { label: 'github', url: 'https://api.github.com/repos/Eysn0130/FundsTrace/releases/latest' },
    { label: 'site', url: `https://analytix.top/fundstrace/latest.yml?verify=${run}` },
    { label: 'download', url: `https://download.analytix.top/releases/windows/site/fundstrace/latest.yml?verify=${run}` },
  ];
  const [github, ...documents] = await Promise.all(endpoints.map(async ({ label, url }) => {
    let request = fetchImpl, transport = 'fetch';
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const response = await request(url, { method: 'GET',
          headers: { Accept: 'application/json', 'User-Agent': 'FundsTrace-public-verifier' },
          redirect: 'error', signal: AbortSignal.timeout(30000) });
        if (!response.ok) throw new Error('Public release metadata is unavailable');
        const document = await response.json();
        if (transport === 'https-native') report({ endpoint: label, attempt, transport, event: 'json-read' });
        return document;
      } catch (error) {
        const code = [error?.code, error?.cause?.code].find(value => diagnosticCodes.has(value))
          || (error?.name === 'TimeoutError' ? 'TIMEOUT' : error?.name === 'AbortError' ? 'ABORTED' : 'UNKNOWN');
        report({ endpoint: label, attempt, transport, code });
        if (!networkCodes.has(code) || attempt === 3) throw error;
        if (code === 'UND_ERR_CONNECT_TIMEOUT') {
          request = fallbackFetchImpl;
          transport = 'https-native';
        }
        await wait(delays[attempt - 1]);
      }
    }
  }));
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
