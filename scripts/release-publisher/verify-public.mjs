import { pathToFileURL } from 'node:url';
import { setTimeout as sleep } from 'node:timers/promises';
import { selectRelease, validateCurrent } from './publish.mjs';

const networkCodes = new Set(['UND_ERR_CONNECT_TIMEOUT', 'ECONNRESET', 'EAI_AGAIN']);
const delays = [2000, 5000];

export async function verifyPublic({ expectedTag = process.env.EXPECTED_TAG, run = process.env.GITHUB_RUN_ID,
  fetchImpl = fetch, wait = sleep, report = value => console.log(JSON.stringify(value)) } = {}) {
  const endpoints = [
    { label: 'github', url: 'https://api.github.com/repos/Eysn0130/FundsTrace/releases/latest' },
    { label: 'site', url: `https://analytix.top/fundstrace/latest.yml?verify=${run}` },
    { label: 'download', url: `https://download.analytix.top/releases/windows/site/fundstrace/latest.yml?verify=${run}` },
  ];
  const [github, ...documents] = await Promise.all(endpoints.map(async ({ label, url }) => {
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const response = await fetchImpl(url, { method: 'GET', headers: { Accept: 'application/json' },
          signal: AbortSignal.timeout(30000) });
        if (!response.ok) throw new Error('Public release metadata is unavailable');
        return await response.json();
      } catch (error) {
        const code = [error?.code, error?.cause?.code].find(value => networkCodes.has(value)) || 'UNKNOWN';
        report({ endpoint: label, attempt, code });
        if (code === 'UNKNOWN' || attempt === 3) throw error;
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
