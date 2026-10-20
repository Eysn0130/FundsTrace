import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import OSS from 'ali-oss';

export const repository = 'Eysn0130/FundsTrace';
export const prefix = 'releases/fundstrace/windows';
export const pointer = 'releases/windows/site/fundstrace/latest.yml';
const cdn = 'https://download.analytix.top';
const versionPattern = /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/;
const hash = (data, algorithm = 'sha256', encoding = 'hex') => createHash(algorithm).update(data).digest(encoding);

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
  return { version, asset, releaseId: release.id, releaseDate: release.published_at };
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
  return value;
}

async function githubLatest() {
  const response = await fetch(`https://api.github.com/repos/${repository}/releases/latest`, {
    headers: { Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    signal: AbortSignal.timeout(30000),
  });
  if (!response.ok) throw new Error(`GitHub release lookup failed (${response.status})`);
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
export async function publish({ client, selected, bytes, assertLatest = async () => {} }) {
  const manifest = manifestFor(selected, bytes);
  const serialized = Buffer.from(JSON.stringify(manifest, null, 2) + '\n');
  const before = await object(client, pointer);
  const current = before && validateCurrent(JSON.parse(before.bytes.toString('utf8')));
  if (current && compareVersions(manifest.version, current.version) < 0) throw new Error('Refusing a downgrade');
  if (current?.version === manifest.version && JSON.stringify(current) !== JSON.stringify(manifest))
    throw new Error('A published version is immutable; publish a higher version');
  const versionRoot = `${prefix}/${manifest.version}`;
  const installerKey = `${versionRoot}/${selected.asset.name}`;
  const existing = await object(client, `${versionRoot}/manifest.json`);
  if (existing && !existing.bytes.equals(serialized)) throw new Error('Immutable version metadata differs');
  const priorInstaller = await object(client, installerKey);
  if (priorInstaller) {
    if (hash(priorInstaller.bytes) !== manifest.checksumSha256 || priorInstaller.bytes.length !== bytes.length)
      throw new Error('Immutable installer differs');
  } else {
    await client.put(installerKey, bytes, { headers: { 'Content-Type': 'application/octet-stream',
      'Cache-Control': 'public, max-age=31536000, immutable', 'x-oss-forbid-overwrite': 'true' } });
    const uploaded = await object(client, installerKey);
    if (!uploaded || uploaded.bytes.length !== bytes.length || hash(uploaded.bytes) !== manifest.checksumSha256)
      throw new Error('Uploaded installer integrity verification failed');
  }
  if (!existing) await client.put(`${versionRoot}/manifest.json`, serialized, { headers: {
    'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=31536000, immutable', 'x-oss-forbid-overwrite': 'true' } });
  await assertLatest();
  const final = await object(client, pointer);
  if ((before?.etag || null) !== (final?.etag || null)) throw new Error('Current release changed during upload; retry against the new current release');
  if (current?.version === manifest.version) return manifest;
  const headers = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-cache, max-age=0, must-revalidate' };
  // OSS PutObject does not document If-Match. All publishers MUST use the same
  // non-cancelling GitHub concurrency group; the ETag reread detects changes
  // outside that writer before promotion. Do not run a second manual publisher.
  if (!before) headers['x-oss-forbid-overwrite'] = 'true';
  await client.put(pointer, serialized, { headers });
  const promoted = await object(client, pointer);
  if (!promoted?.bytes.equals(serialized)) throw new Error('Release promotion could not be verified');
  return manifest;
}

async function main() {
  const selected = await githubLatest();
  const requested = process.env.DOWNLOAD_RELEASE_TAG || '';
  if (requested && requested !== `v${selected.version}`) throw new Error('Only the currently published latest stable release may be mirrored');
  const response = await fetch(selected.asset.browser_download_url, { signal: AbortSignal.timeout(10 * 60 * 1000) });
  if (!response.ok) throw new Error(`GitHub installer download failed (${response.status})`);
  const bytes = Buffer.from(await response.arrayBuffer());
  const client = new OSS({ region: 'oss-cn-heyuan', bucket: 'analytix-hub-downloads-prod', secure: true,
    accessKeyId: process.env.ALIBABA_CLOUD_ACCESS_KEY_ID, accessKeySecret: process.env.ALIBABA_CLOUD_ACCESS_KEY_SECRET,
    stsToken: process.env.ALIBABA_CLOUD_SECURITY_TOKEN, timeout: 10 * 60 * 1000 });
  const manifest = await publish({ client, selected, bytes, assertLatest: async () => {
    const latest = await githubLatest();
    if (latest.releaseId !== selected.releaseId || latest.asset.id !== selected.asset.id
        || latest.asset.digest !== selected.asset.digest) throw new Error('GitHub latest release changed during upload');
  } });
  console.log(`FundsTrace ${manifest.version}: GitHub bytes verified; domestic installer and shared latest pointer verified.`);
  const { cdnAdapter, prewarm } = await import('./prewarm.mjs');
  const receipt = await prewarm({ client, manifest, submit: cdnAdapter({
    accessKeyId: process.env.ALIBABA_CLOUD_ACCESS_KEY_ID,
    accessKeySecret: process.env.ALIBABA_CLOUD_ACCESS_KEY_SECRET,
    securityToken: process.env.ALIBABA_CLOUD_SECURITY_TOKEN,
  }) });
  console.log(`Domestic CDN prewarm submitted: task ${receipt.taskId}. Edge propagation is asynchronous.`);
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch(() => { console.error('FundsTrace download publication or CDN prewarm failed; inspect the last successful stage and retry. Verified published files were retained.'); process.exitCode = 1; });
