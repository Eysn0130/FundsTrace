import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { selectRelease, manifestFor, publish, pointer, prefix } from './publish.mjs';
const bytes = Buffer.from('Synthetic installer bytes; never executed.');
const sha = data => createHash('sha256').update(data).digest('hex');
function release(version = '0.1.7') {
  const name = `FundsTrace-${version}-windows-x64-unsigned.exe`;
  return { id: 123, tag_name: `v${version}`, published_at: '2026-10-19T12:00:00Z', draft: false, prerelease: false,
    assets: [{ id: 456, name, size: bytes.length, digest: `sha256:${sha(bytes)}`, browser_download_url: `https://github.com/Eysn0130/FundsTrace/releases/download/v${version}/${name}` }] };
}
function store(seed = []) {
  const objects = new Map(seed), puts = [];
  const client = {
    async get(key) { const content = objects.get(key); if (!content) throw Object.assign(new Error(), { code: 'NoSuchKey' }); return { content, res: { headers: { etag: sha(content) } } }; },
    async put(key, data, options) {
      const before = objects.get(key), headers = options.headers;
      if (headers['x-oss-forbid-overwrite'] && before) throw new Error('Conditional put rejected');
      puts.push(key); objects.set(key, Buffer.from(data));
    },
  };
  return { client, objects, puts };
}
test('published installer is checked before upload and site/updater switch together only after verification', async () => {
  const selected = selectRelease(release()), { client, objects, puts } = store();
  const result = await publish({ client, selected, bytes });
  assert.equal(puts.at(-1), pointer);
  assert.ok(objects.get(`${prefix}/0.1.7/${selected.asset.name}`).equals(bytes));
  const current = JSON.parse(objects.get(pointer));
  assert.equal(current.product, 'com.eysn.fundstrace'); assert.equal(current.checksumSha256, sha(bytes));
  assert.equal(current.files[0].url, 'https://download.analytix.top/releases/fundstrace/windows/0.1.7/FundsTrace-0.1.7-windows-x64-unsigned.exe');
  assert.equal(result.version, current.version);
  const count = puts.length; await publish({ client, selected, bytes }); assert.equal(puts.length, count);
});
test('drafts, prereleases, missing digest, ambiguous or foreign assets are rejected', () => {
  for (const mutate of [r => { r.draft = true; }, r => { r.prerelease = true; }, r => { delete r.assets[0].digest; },
    r => { r.assets.push(r.assets[0]); }, r => { r.assets[0].browser_download_url = 'https://example.com/app.exe'; }]) {
    const value = release(); mutate(value); assert.throws(() => selectRelease(value));
  }
  assert.throws(() => manifestFor(selectRelease(release()), Buffer.from('wrong bytes')), /do not match/);
});
test('upload failure, changed latest release and stale concurrent publisher retain the old pointer', async () => {
  const old = Buffer.from(JSON.stringify(manifestFor(selectRelease(release()), bytes)));
  for (const failure of ['upload', 'latest', 'concurrent']) {
    const { client, objects } = store([[pointer, old]]);
    if (failure === 'upload') client.put = async () => { throw new Error('Network failure'); };
    const assertLatest = async () => {
      if (failure === 'latest') throw new Error('latest changed');
      if (failure === 'concurrent') objects.set(pointer, Buffer.from(JSON.stringify(manifestFor(selectRelease(release('0.1.9')), bytes))));
    };
    await assert.rejects(publish({ client, selected: selectRelease(release('0.1.8')), bytes, assertLatest }));
    assert.equal(JSON.parse(objects.get(pointer)).version, failure === 'concurrent' ? '0.1.9' : '0.1.7');
  }
});
test('downgrades, replacing existing version bytes and corrupt uploads fail closed', async () => {
  const newer = Buffer.from(JSON.stringify(manifestFor(selectRelease(release('0.1.8')), bytes)));
  const first = store([[pointer, newer]]);
  await assert.rejects(publish({ client: first.client, selected: selectRelease(release()), bytes }), /downgrade/);
  const second = store([[`${prefix}/0.1.7/${selectRelease(release()).asset.name}`, Buffer.from('other bytes')]]);
  await assert.rejects(publish({ client: second.client, selected: selectRelease(release()), bytes }), /differs/);
  const third = store(); const put = third.client.put;
  third.client.put = async (...args) => { await put(...args); third.objects.set(args[0], Buffer.from('corrupt')); };
  await assert.rejects(publish({ client: third.client, selected: selectRelease(release()), bytes }), /integrity/);
  assert.equal(third.objects.has(pointer), false);
});
