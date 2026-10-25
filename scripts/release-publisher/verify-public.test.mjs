import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { verifyPublic } from './verify-public.mjs';

const githubUrl = 'https://api.github.com/repos/Eysn0130/FundsTrace/releases/latest';
const siteUrl = 'https://analytix.top/fundstrace/latest.yml?verify=12345';
const downloadUrl = 'https://download.analytix.top/releases/windows/site/fundstrace/latest.yml?verify=12345';
const installerUrl = 'https://download.analytix.top/releases/fundstrace/windows/0.1.8/FundsTrace-0.1.8-windows-x64-unsigned.exe';
const sha256 = 'a'.repeat(64), sha512 = Buffer.alloc(64, 7).toString('base64');
const github = { id: 123, tag_name: 'v0.1.8', draft: false, prerelease: false, published_at: '2026-10-20T12:00:00Z',
  body: '  修复节点标签。\r\n稳定公开下载。\r\n',
  assets: [{ id: 456, name: 'FundsTrace-0.1.8-windows-x64-unsigned.exe', size: 2048, digest: `sha256:${sha256}`,
    browser_download_url: 'https://github.com/Eysn0130/FundsTrace/releases/download/v0.1.8/FundsTrace-0.1.8-windows-x64-unsigned.exe' }] };
const document = { schemaVersion: 1, product: 'com.eysn.fundstrace', version: '0.1.8',
  files: [{ url: installerUrl, sha512, size: 2048 }], path: installerUrl, sha512,
  checksumSha256: sha256, releaseDate: '2026-10-20T12:00:00Z', githubReleaseId: 123, githubAssetId: 456,
  releaseNotes: '修复节点标签。\n稳定公开下载。' };
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const networkError = code => Object.assign(new TypeError('SENSITIVE_RAW_NETWORK_ERROR'), { cause: { code } });

function harness(respond = () => undefined) {
  const calls = [], waits = [], events = [], counts = new Map();
  const fetchImpl = async (url, options) => {
    assert.ok([githubUrl, siteUrl, downloadUrl].includes(url), 'only the three fixed public endpoints are requested');
    assert.equal(options.method, 'GET');
    assert.deepEqual(options.headers, { Accept: 'application/json' });
    assert.ok(options.signal instanceof AbortSignal);
    const attempt = (counts.get(url) || 0) + 1;
    counts.set(url, attempt); calls.push({ url, options });
    return await respond(url, attempt) || json(url === githubUrl ? github : document);
  };
  const verify = options => verifyPublic({ expectedTag: 'v0.1.8', run: '12345', fetchImpl,
    wait: async delay => { waits.push(delay); }, report: event => events.push(event), ...options });
  return { verify, calls, counts, waits, events };
}

test('each allowed transient network error retries twice then verifies all original endpoints on the third attempt', async t => {
  const timeouts = [];
  t.mock.method(AbortSignal, 'timeout', delay => { timeouts.push(delay); return new AbortController().signal; });
  for (const code of ['UND_ERR_CONNECT_TIMEOUT', 'ECONNRESET', 'EAI_AGAIN']) {
    const h = harness((url, attempt) => { if (url === siteUrl && attempt < 3) throw networkError(code); });
    const latest = await h.verify();
    assert.equal(latest.version, '0.1.8');
    assert.equal(h.counts.get(siteUrl), 3);
    assert.equal(h.counts.get(githubUrl), 1);
    assert.equal(h.counts.get(downloadUrl), 1);
    assert.deepEqual(h.waits, [2000, 5000]);
    assert.equal(new Set(h.calls.map(call => call.options.signal)).size, 5, 'every attempt receives a fresh timeout signal');
    assert.deepEqual(h.events, [{ endpoint: 'site', attempt: 1, code }, { endpoint: 'site', attempt: 2, code }]);
  }
  assert.equal(timeouts.length, 15);
  assert.ok(timeouts.every(delay => delay === 30000), 'each GET keeps its original 30 second budget');
});

test('a persistent permitted network error stops after exactly three GET attempts', async () => {
  for (const url of [githubUrl, siteUrl, downloadUrl]) {
    const h = harness(target => { if (target === url) throw networkError('UND_ERR_CONNECT_TIMEOUT'); });
    await assert.rejects(h.verify());
    assert.equal(h.counts.get(url), 3);
    assert.deepEqual(h.waits, [2000, 5000]);
    assert.equal(h.events.length, 3);
    assert.equal(h.events.at(-1).attempt, 3);
    assert.deepEqual([...h.counts.keys()].sort(), [githubUrl, siteUrl, downloadUrl].sort());
  }
});

test('HTTP errors, malformed JSON and unknown errors fail each public endpoint without retry', async () => {
  for (const url of [githubUrl, siteUrl, downloadUrl]) {
    for (const respond of [() => new Response('Forbidden', { status: 403 }),
      () => new Response('Not found', { status: 404 }), () => new Response('Unavailable', { status: 503 }),
      () => new Response('{invalid json'), () => { throw new TypeError('SENSITIVE_UNKNOWN_ERROR'); },
      () => { throw networkError('ENOTFOUND'); }]) {
      const h = harness(target => target === url ? respond() : undefined);
      await assert.rejects(h.verify());
      assert.equal(h.counts.get(url), 1);
      assert.deepEqual(h.waits, []);
      assert.equal(JSON.stringify(h.events).includes('SENSITIVE'), false);
    }
  }
});

test('both documents retain every existing metadata and notes comparison without retry', async () => {
  const mutations = [
    value => { value.version = '0.1.7'; }, value => { value.githubReleaseId++; }, value => { value.githubAssetId++; },
    value => { value.checksumSha256 = 'b'.repeat(64); }, value => { value.files[0].size++; },
    value => { value.releaseDate = '2026-10-20T13:00:00Z'; }, value => { value.releaseNotes = 'stale release notes'; },
    value => { delete value.releaseNotes; }, value => { value.product = 'another-product'; },
    value => { value.path = 'https://foreign.invalid/installer.exe'; },
    value => { value.sha512 = Buffer.alloc(64, 8).toString('base64'); },
  ];
  for (const url of [siteUrl, downloadUrl]) {
    for (const mutate of mutations) {
      const changed = structuredClone(document); mutate(changed);
      const h = harness(target => target === url ? json(changed) : undefined);
      await assert.rejects(h.verify());
      assert.deepEqual([...h.counts.entries()].sort(), [[githubUrl, 1], [siteUrl, 1], [downloadUrl, 1]].sort());
      assert.deepEqual(h.waits, []);
    }
  }
  const wrongTag = harness();
  await assert.rejects(wrongTag.verify({ expectedTag: 'v0.1.7' }));
  assert.deepEqual(wrongTag.waits, []);
  const changedLatest = harness(url => url === githubUrl ? json({ ...github, id: 124 }) : undefined);
  await assert.rejects(changedLatest.verify());
  assert.deepEqual(changedLatest.waits, []);
});

test('the CLI uses EXPECTED_TAG and reports failures without printing raw exceptions', () => {
  const script = fileURLToPath(new URL('./verify-public.mjs', import.meta.url));
  const bootstrap = `globalThis.fetch=async url=>new Response(JSON.stringify(url.startsWith('https://api.github.com/')?${JSON.stringify(github)}:${JSON.stringify(document)}));`;
  const run = (expectedTag, code = bootstrap) => spawnSync(process.execPath,
    ['--import', `data:text/javascript,${encodeURIComponent(code)}`, script], {
      env: { EXPECTED_TAG: expectedTag, GITHUB_RUN_ID: '12345' }, encoding: 'utf8', timeout: 10000,
    });
  const passed = run('v0.1.8');
  assert.equal(passed.status, 0, passed.stderr);
  assert.match(passed.stdout, /Public FundsTrace 0\.1\.8 version, installer and release notes verified/);
  for (const result of [run('v0.1.7'), run('v0.1.8', `globalThis.fetch=async()=>{throw new Error('SENSITIVE_RAW_CLI_ERROR')};`)]) {
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Public FundsTrace metadata verification failed/);
    assert.equal((result.stdout + result.stderr).includes('SENSITIVE'), false);
    assert.equal(result.stderr.includes('Error:'), false);
  }
});
