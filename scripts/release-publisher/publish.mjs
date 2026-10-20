import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import OSS from 'ali-oss';

export const repository = 'Eysn0130/FundsTrace';
export const prefix = 'releases/fundstrace/windows';
export const pointer = 'releases/windows/site/fundstrace/latest.yml';
const cdn = 'https://download.analytix.top';
const versionPattern = /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/;
const hash = (data, algorithm = 'sha256', encoding = 'hex') => createHash(algorithm).update(data).digest(encoding);
const stages = new Set(['github.metadata', 'github.download', 'installer.integrity', 'oss.read-current',
  'oss.read-manifest', 'oss.read-installer', 'oss.upload-installer', 'oss.verify-installer',
  'oss.upload-manifest', 'github.recheck', 'oss.recheck-current', 'oss.promote',
  'oss.verify-promotion', 'cdn.prewarm']);
const safeCodes = new Set(['ABORT_ERR', 'ECONNRESET', 'ECONNREFUSED', 'ENOTFOUND', 'ETIMEDOUT', 'EAI_AGAIN',
  'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT', 'UND_ERR_SOCKET',
  'RequestTimeout', 'ResponseTimeoutError', 'ConnectionTimeoutError', 'AccessDenied', 'InvalidAccessKeyId',
  'SecurityTokenExpired', 'InvalidSecurityToken', 'SignatureDoesNotMatch', 'RequestTimeTooSkewed',
  'NoSuchKey', 'PreconditionFailed']);

export function createDiagnostics(report = () => {}, now = Date.now) {
  return async (stage, task) => {
    if (!stages.has(stage)) throw new Error('Unknown publication stage');
    const started = now();
    const elapsed = () => Math.max(0, Math.round(now() - started));
    report({ stage, event: 'begin' });
    try {
      const result = await task();
      report({ stage, event: 'done', elapsedMs: elapsed() });
      return result;
    } catch (error) {
      const candidate = [error?.code, error?.cause?.code, error?.name].find(value => safeCodes.has(value));
      const code = candidate
        || (error?.name === 'TimeoutError' ? 'TIMEOUT' : error?.name === 'AbortError' ? 'ABORTED' : 'UNKNOWN');
      const status = error?.status || error?.statusCode || error?.res?.status;
      report({ stage, event: 'failed', elapsedMs: elapsed(), code,
        ...(Number.isInteger(status) && status >= 100 && status <= 599 ? { status } : {}) });
      throw error;
    }
  };
}

export function normalizeReleaseNotes(value) {
  if (value == null) return '';
  if (typeof value !== 'string') throw new Error('Invalid release notes');
  const notes = value.replace(/\r\n?/g, '\n').trim();
  if (Buffer.byteLength(notes, 'utf8') > 16 * 1024) throw new Error('Release notes exceed 16 KiB');
  return notes;
}

const withoutNotes = ({ releaseNotes, ...metadata }) => metadata;

export function assertReleaseSnapshot(selected, latest) {
  const snapshot = value => ({ version: value.version, releaseId: value.releaseId,
    releaseDate: value.releaseDate, releaseNotes: value.releaseNotes,
    asset: { id: value.asset.id, name: value.asset.name, size: value.asset.size,
      digest: value.asset.digest, url: value.asset.browser_download_url } });
  if (!isDeepStrictEqual(snapshot(selected), snapshot(latest)))
    throw new Error('GitHub latest release changed during upload');
}

export function compareVersions(a, b) {
  if (!versionPattern.test(a) || !versionPattern.test(b)) throw new Error('Invalid version');
  const left = a.split('.').map(Number), right = b.split('.').map(Number);
  for (let i = 0; i < 3; i++) if (left[i] !== right[i]) return Math.sign(left[i] - right[i]);
  return 0;
}
export function selectRelease(release) {
  const version = String(release.tag_name || '').replace(/^v/, '');
  if (!versionPattern.test(version) || release.tag_name !== `v${version}` || release.draft || release.prerelease
      || !Number.isSafeInteger(release.id) || !Number.isFinite(Date.parse(release.published_at))) throw new Error('Not a published stable FundsTrace release');
  const name = `FundsTrace-${version}-windows-x64-unsigned.exe`;
  const matches = (release.assets || []).filter(asset => asset.name === name);
  if (matches.length !== 1) throw new Error('Exactly one Windows x64 NSIS installer is required');
  const asset = matches[0];
  if (!Number.isSafeInteger(asset.id) || !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > 1024 ** 3
      || !/^sha256:[a-f0-9]{64}$/.test(asset.digest)
      || asset.browser_download_url !== `https://github.com/${repository}/releases/download/v${version}/${name}`)
    throw new Error('Missing or invalid GitHub asset integrity metadata');
  return { version, asset, releaseId: release.id, releaseDate: release.published_at,
    releaseNotes: normalizeReleaseNotes(release.body) };
}
export function manifestFor(selected, bytes) {
  if (bytes.length !== selected.asset.size || `sha256:${hash(bytes)}` !== selected.asset.digest)
    throw new Error('Installer bytes do not match the published GitHub asset');
  const url = `${cdn}/${prefix}/${selected.version}/${selected.asset.name}`;
  const sha512 = hash(bytes, 'sha512', 'base64');
  return { schemaVersion: 1, product: 'com.eysn.fundstrace', version: selected.version,
    files: [{ url, sha512, size: bytes.length }], path: url, sha512,
    checksumSha256: hash(bytes), releaseDate: selected.releaseDate,
    githubReleaseId: selected.releaseId, githubAssetId: selected.asset.id };
}
export function validateCurrent(value) {
  if (value?.product !== 'com.eysn.fundstrace' || value.schemaVersion !== 1 || !versionPattern.test(value.version)
      || !/^[a-f0-9]{64}$/.test(value.checksumSha256) || !Array.isArray(value.files) || value.files.length !== 1)
    throw new Error('Invalid existing FundsTrace pointer; manual investigation required');
  const file = value.files[0], expected = `${cdn}/${prefix}/${value.version}/FundsTrace-${value.version}-windows-x64-unsigned.exe`;
  if (file.url !== expected || value.path !== expected || file.sha512 !== value.sha512
      || !/^[A-Za-z0-9+/]{86}==$/.test(file.sha512) || !Number.isSafeInteger(file.size) || file.size <= 0)
    throw new Error('Invalid existing FundsTrace file metadata');
  if (Object.hasOwn(value, 'releaseNotes')) {
    if (typeof value.releaseNotes !== 'string') throw new Error('Invalid existing release notes');
    normalizeReleaseNotes(value.releaseNotes);
  }
  return value;
}

async function githubLatest() {
  const response = await fetch(`https://api.github.com/repos/${repository}/releases/latest`, {
    headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    signal: AbortSignal.timeout(30000),
  });
  if (!response.ok) throw Object.assign(new Error('GitHub release lookup failed'), { status: response.status });
  return selectRelease(await response.json());
}
async function object(client, key) {
  try {
    const result = await client.get(key);
    return { bytes: result.content, etag: result.res.headers.etag };
  } catch (error) { if (error.code === 'NoSuchKey') return null; throw error; }
}

// Upload exact published bytes first. A single OSS PutObject then
// promotes the JSON document (also valid YAML) used by BOTH site and updater.
// No CDN/ECS upload credentials ever enter a desktop installer or renderer.
export async function publish({ client, selected, bytes, assertLatest = async () => {}, report }) {
  const stage = createDiagnostics(report);
  const manifest = await stage('installer.integrity', () => manifestFor(selected, bytes));
  const serialized = Buffer.from(JSON.stringify(manifest, null, 2) + '\n');
  const latest = { ...manifest, releaseNotes: normalizeReleaseNotes(selected.releaseNotes) };
  const latestBytes = Buffer.from(JSON.stringify(latest, null, 2) + '\n');
  const { before, current } = await stage('oss.read-current', async () => {
    const before = await object(client, pointer);
    const current = before && validateCurrent(JSON.parse(before.bytes.toString('utf8')));
    if (current && compareVersions(manifest.version, current.version) < 0) throw new Error('Refusing a downgrade');
    if (current?.version === manifest.version && !isDeepStrictEqual(withoutNotes(current), manifest))
      throw new Error('A published version is immutable; publish a higher version');
    return { before, current };
  });
  const versionRoot = `${prefix}/${manifest.version}`;
  const installerKey = `${versionRoot}/${selected.asset.name}`;
  const existing = await stage('oss.read-manifest', async () => {
    const existing = await object(client, `${versionRoot}/manifest.json`);
    if (existing && !existing.bytes.equals(serialized)) throw new Error('Immutable version metadata differs');
    return existing;
  });
  const priorInstaller = await stage('oss.read-installer', async () => {
    const priorInstaller = await object(client, installerKey);
    if (priorInstaller && (hash(priorInstaller.bytes) !== manifest.checksumSha256 || priorInstaller.bytes.length !== bytes.length))
      throw new Error('Immutable installer differs');
    return priorInstaller;
  });
  if (!priorInstaller) {
    await stage('oss.upload-installer', async () => {
      try {
        await client.multipartUpload(installerKey, bytes, { parallel: 3, partSize: 8 * 1024 * 1024,
          mime: 'application/octet-stream', headers: { 'Content-Type': 'application/octet-stream',
            'Cache-Control': 'public, max-age=31536000, immutable', 'x-oss-forbid-overwrite': 'true' } });
      } catch (error) {
        // No abort argument: stop local streams without requiring cloud DELETE permissions.
        client.cancel();
        throw error;
      }
    });
    await stage('oss.verify-installer', async () => {
      const uploaded = await object(client, installerKey);
      if (!uploaded || uploaded.bytes.length !== bytes.length || hash(uploaded.bytes) !== manifest.checksumSha256)
        throw new Error('Uploaded installer integrity verification failed');
    });
  }
  if (!existing) await stage('oss.upload-manifest', () => client.put(`${versionRoot}/manifest.json`, serialized, { headers: {
    'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=31536000, immutable', 'x-oss-forbid-overwrite': 'true' } }));
  await stage('github.recheck', assertLatest);
  await stage('oss.recheck-current', async () => {
    const final = await object(client, pointer);
    if ((before?.etag || null) !== (final?.etag || null)) throw new Error('Current release changed during upload; retry against the new current release');
  });
  if (isDeepStrictEqual(current, latest)) return latest;
  const headers = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-cache, max-age=0, must-revalidate' };
  // OSS PutObject does not document If-Match. All publishers MUST use the same
  // non-cancelling GitHub concurrency group; the ETag reread detects changes
  // outside that writer before promotion. Do not run a second manual publisher.
  if (!before) headers['x-oss-forbid-overwrite'] = 'true';
  await stage('oss.promote', () => client.put(pointer, latestBytes, { headers }));
  await stage('oss.verify-promotion', async () => {
    const promoted = await object(client, pointer);
    if (!promoted?.bytes.equals(latestBytes)) throw new Error('Release promotion could not be verified');
  });
  return latest;
}

async function main() {
  const report = value => console.log(JSON.stringify(value));
  const stage = createDiagnostics(report);
  const selected = await stage('github.metadata', githubLatest);
  const requested = process.env.DOWNLOAD_RELEASE_TAG || '';
  if (requested && requested !== `v${selected.version}`) throw new Error('Only the currently published latest stable release may be mirrored');
  const bytes = await stage('github.download', async () => {
    const response = await fetch(selected.asset.browser_download_url, { signal: AbortSignal.timeout(10 * 60 * 1000) });
    if (!response.ok) throw Object.assign(new Error('GitHub installer download failed'), { status: response.status });
    return Buffer.from(await response.arrayBuffer());
  });
  const client = new OSS({ region: 'oss-cn-heyuan', bucket: 'analytix-hub-downloads-prod', secure: true,
    accessKeyId: process.env.ALIBABA_CLOUD_ACCESS_KEY_ID, accessKeySecret: process.env.ALIBABA_CLOUD_ACCESS_KEY_SECRET,
    stsToken: process.env.ALIBABA_CLOUD_SECURITY_TOKEN, timeout: 10 * 60 * 1000 });
  const manifest = await publish({ client, selected, bytes, report,
    assertLatest: async () => assertReleaseSnapshot(selected, await githubLatest()) });
  console.log(`FundsTrace ${manifest.version}: GitHub bytes verified; domestic installer and shared latest pointer verified.`);
  const receipt = await stage('cdn.prewarm', async () => {
    const { cdnAdapter, prewarm } = await import('./prewarm.mjs');
    return prewarm({ client, manifest, submit: cdnAdapter({
      accessKeyId: process.env.ALIBABA_CLOUD_ACCESS_KEY_ID,
      accessKeySecret: process.env.ALIBABA_CLOUD_ACCESS_KEY_SECRET,
      securityToken: process.env.ALIBABA_CLOUD_SECURITY_TOKEN,
    }) });
  });
  console.log(`Domestic CDN prewarm submitted: task ${receipt.taskId}. Edge propagation is asynchronous.`);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch(() => { console.error('FundsTrace download publication or CDN prewarm failed; inspect the last successful stage and retry. Verified published files were retained.'); process.exitCode = 1; });
