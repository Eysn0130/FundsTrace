import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import OSS from 'ali-oss';
import { selectRelease, manifestFor, publish, pointer, prefix, normalizeReleaseNotes,
  assertReleaseSnapshot, createDiagnostics } from './publish.mjs';
const bytes = Buffer.from('Synthetic installer bytes; never executed.');
const sha = data => createHash('sha256').update(data).digest('hex');
function release(version = '0.1.7', installer = bytes) {
  const name = `FundsTrace-${version}-windows-x64-unsigned.exe`;
  return { id: 123, tag_name: `v${version}`, published_at: '2026-10-19T12:00:00Z', draft: false, prerelease: false,
    body: '  修复节点标签显示。\r\n\r\n优化更新体验。\r\n',
    assets: [{ id: 456, name, size: installer.length, digest: `sha256:${sha(installer)}`, browser_download_url: `https://github.com/Eysn0130/FundsTrace/releases/download/v${version}/${name}` }] };
}
function store(seed = []) {
  const objects = new Map(seed), gets = [], puts = [], uploads = [], cancellations = [];
  const client = {
    async get(key) { gets.push(key); const content = objects.get(key); if (!content) throw Object.assign(new Error(), { code: 'NoSuchKey' }); return { content, res: { headers: { etag: sha(content) } } }; },
    async put(key, data, options) {
      const before = objects.get(key), headers = options.headers;
      if (headers['x-oss-forbid-overwrite'] && before) throw new Error('Conditional put rejected');
      puts.push(key); objects.set(key, Buffer.from(data));
    },
    async multipartUpload(key, data, options) {
      uploads.push(key);
      // The SDK delegates sub-100 KiB synthetic installers to put().
      return client.put(key, data, options);
    },
    cancel(...args) { cancellations.push(args); },
  };
  return { client, objects, gets, puts, uploads, cancellations };
}

function multipartStore(seed, fault) {
  const h = store(seed), requests = [], parts = new Map();
  const sdk = new OSS({ region: 'oss-cn-hangzhou', bucket: 'synthetic-publisher-test',
    accessKeyId: 'synthetic-only', accessKeySecret: 'synthetic-only' });
  let active = 0, maxActive = 0;
  // Replace the request boundary before calling any SDK method: no network or credentials are used.
  sdk.request = async params => {
    const request = { method: params.method, subres: params.subres,
      headers: structuredClone(params.headers), mime: params.mime };
    requests.push(request);
    if (params.method === 'POST' && params.subres === 'uploads')
      return { data: { Bucket: sdk.options.bucket, Key: params.object, UploadId: 'synthetic-upload' },
        res: { headers: {} } };
    if (params.method === 'PUT' && params.subres.partNumber) {
      active++; maxActive = Math.max(maxActive, active);
      try {
        await new Promise(resolve => setImmediate(resolve));
        if (fault === 'part' && params.subres.partNumber === 2)
          throw Object.assign(new Error('SENSITIVE_MULTIPART_URL_TOKEN'), { name: 'ConnectionTimeoutError', status: -2 });
        const chunks = [];
        for await (const chunk of params.stream) chunks.push(chunk);
        const part = Buffer.concat(chunks);
        assert.equal(Number(params.headers['Content-Length']), part.length);
        parts.set(params.subres.partNumber, part);
        request.bytes = part.length;
        return { res: { headers: { etag: `synthetic-etag-${params.subres.partNumber}` } } };
      } finally { active--; }
    }
    assert.equal(params.method, 'POST', 'multipart SDK must not request cloud abort/list permissions');
    assert.equal(params.subres.uploadId, 'synthetic-upload');
    assert.equal(params.headers['x-oss-forbid-overwrite'], 'true');
    if (fault === 'complete')
      throw Object.assign(new Error('SENSITIVE_MULTIPART_URL_TOKEN'), { name: 'ResponseTimeoutError', status: -2 });
    const numbers = [...params.content.matchAll(/<PartNumber>(\d+)<\/PartNumber>/g)].map(match => Number(match[1]));
    assert.deepEqual(numbers, [...parts.keys()].sort((a, b) => a - b));
    assert.equal(h.objects.has(params.object), false, 'completion must never overwrite an existing installer');
    const assembled = Buffer.concat(numbers.map(number => parts.get(number)));
    if (fault === 'corrupt') assembled[0] ^= 1;
    h.objects.set(params.object, assembled);
    return { res: { headers: { etag: 'synthetic-complete-etag' } } };
  };
  h.client.multipartUpload = async (key, data, options) => {
    h.uploads.push(key);
    return sdk.multipartUpload(key, data, options);
  };
  h.client.cancel = (...args) => {
    h.cancellations.push(args);
    assert.equal(args.length, 0, 'local cancellation must not invoke cloud abort');
    sdk.cancel(...args);
  };
  return { ...h, requests, parts, sdk, maxActive: () => maxActive };
}
test('published installer is checked before upload and site/updater switch together only after verification', async () => {
  const selected = selectRelease(release()), { client, objects, puts } = store();
  const result = await publish({ client, selected, bytes });
  assert.equal(puts.at(-1), pointer);
  assert.ok(objects.get(`${prefix}/0.1.7/${selected.asset.name}`).equals(bytes));
  const current = JSON.parse(objects.get(pointer));
  assert.equal(current.product, 'com.eysn.fundstrace'); assert.equal(current.checksumSha256, sha(bytes));
  assert.equal(current.files[0].url, 'https://download.analytix.top/releases/fundstrace/windows/0.1.7/FundsTrace-0.1.7-windows-x64-unsigned.exe');
  assert.equal(current.releaseNotes, '修复节点标签显示。\n\n优化更新体验。');
  const immutable = JSON.parse(objects.get(`${prefix}/0.1.7/manifest.json`));
  assert.equal(Object.hasOwn(immutable, 'releaseNotes'), false);
  assert.deepEqual(immutable, manifestFor(selected, bytes));
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

test('release notes preserve Markdown content with normalized newlines and a UTF-8 byte limit', () => {
  assert.equal(normalizeReleaseNotes(' \r\n- **修复**\r第二项\r\n '), '- **修复**\n第二项');
  assert.equal(normalizeReleaseNotes(null), '');
  const legacy = release(); delete legacy.body;
  assert.equal(selectRelease(legacy).releaseNotes, '');
  assert.equal(normalizeReleaseNotes('a'.repeat(16 * 1024)).length, 16 * 1024);
  assert.throws(() => normalizeReleaseNotes('a'.repeat(16 * 1024 + 1)), /16 KiB/);
  assert.throws(() => normalizeReleaseNotes('界'.repeat(6000)), /16 KiB/);
  assert.throws(() => selectRelease({ ...release(), body: {} }), /notes/);
});

test('same-version notes corrections update only latest and leave immutable bytes untouched', async () => {
  const original = release(), selected = selectRelease(original), h = store();
  await publish({ ...h, selected, bytes });
  const installerKey = `${prefix}/0.1.7/${selected.asset.name}`;
  const manifestKey = `${prefix}/0.1.7/manifest.json`;
  const installer = Buffer.from(h.objects.get(installerKey)), manifest = Buffer.from(h.objects.get(manifestKey));
  const count = h.puts.length;
  const uploadCount = h.uploads.length;
  const corrected = selectRelease({ ...original, body: '修复节点标签显示。\n改善稳定性。' });
  const result = await publish({ ...h, selected: corrected, bytes });
  assert.deepEqual(h.puts.slice(count), [pointer]);
  assert.equal(h.uploads.length, uploadCount);
  assert.ok(h.objects.get(installerKey).equals(installer));
  assert.ok(h.objects.get(manifestKey).equals(manifest));
  assert.equal(result.releaseNotes, corrected.releaseNotes);
  assert.deepEqual(JSON.parse(h.objects.get(pointer)), result);
  const correctedCount = h.puts.length;
  await publish({ ...h, selected: corrected, bytes });
  assert.equal(h.puts.length, correctedCount);
});

test('legacy latest without notes supports a notes correction or a newer release', async () => {
  const selected = selectRelease(release());
  const binary = manifestFor(selected, bytes);
  const serialized = Buffer.from(JSON.stringify(binary, null, 2) + '\n');
  for (const version of ['0.1.7', '0.1.8']) {
    const h = store([[pointer, serialized], [`${prefix}/0.1.7/manifest.json`, serialized],
      [`${prefix}/0.1.7/${selected.asset.name}`, bytes]]);
    await publish({ ...h, selected: selectRelease(release(version)), bytes });
    const current = JSON.parse(h.objects.get(pointer));
    assert.equal(current.version, version);
    assert.equal(current.releaseNotes, selected.releaseNotes);
    assert.ok(h.objects.get(`${prefix}/0.1.7/manifest.json`).equals(serialized));
    if (version === '0.1.7') assert.deepEqual(h.puts, [pointer]);
  }
});

test('same-version metadata changes beyond release notes cannot be promoted', async () => {
  const selected = selectRelease(release()), manifest = manifestFor(selected, bytes);
  const mutations = [
    value => { value.githubReleaseId++; },
    value => { value.githubAssetId++; },
    value => { value.releaseDate = '2026-10-19T13:00:00Z'; },
    value => { value.checksumSha256 = 'a'.repeat(64); },
    value => { value.files[0].size++; },
    value => { value.sha512 = value.files[0].sha512 = Buffer.alloc(64, 1).toString('base64'); },
    value => { value.unexpectedMetadata = 'cannot be dropped'; },
  ];
  for (const mutate of mutations) {
    const value = structuredClone(manifest); mutate(value);
    const before = Buffer.from(JSON.stringify(value));
    const h = store([[pointer, before]]);
    await assert.rejects(publish({ ...h, selected, bytes }), /immutable/);
    assert.equal(h.puts.length, 0);
    assert.ok(h.objects.get(pointer).equals(before));
  }
});

test('a release notes edit during upload fails the final GitHub snapshot check before promotion', async () => {
  const old = Buffer.from(JSON.stringify(manifestFor(selectRelease(release()), bytes)));
  const h = store([[pointer, old]]), selected = selectRelease(release('0.1.8')), events = [];
  const edited = selectRelease({ ...release('0.1.8'), body: '上传期间修正的说明。' });
  await assert.rejects(publish({ ...h, selected, bytes, report: event => events.push(event),
    assertLatest: async () => assertReleaseSnapshot(selected, edited) }), /changed during upload/);
  assert.ok(h.objects.get(pointer).equals(old));
  assert.equal(h.puts.includes(pointer), false);
  assert.equal(events.at(-1).stage, 'github.recheck');
  assert.equal(events.at(-1).event, 'failed');
  assert.doesNotThrow(() => assertReleaseSnapshot(selected, structuredClone(selected)));
  const downloaded = structuredClone(selected); downloaded.asset.download_count = 1;
  assert.doesNotThrow(() => assertReleaseSnapshot(selected, downloaded));
  for (const mutate of [value => { value.releaseDate = '2026-10-19T13:00:00Z'; },
    value => { value.asset.size++; }, value => { value.asset.id++; }, value => { value.releaseId++; },
    value => { value.asset.digest = `sha256:${'a'.repeat(64)}`; }]) {
    const changed = structuredClone(selected); mutate(changed);
    assert.throws(() => assertReleaseSnapshot(selected, changed), /changed during upload/);
  }
});

test('fixed diagnostic stages expose only elapsed time and safe error code/status metadata', async () => {
  const marker = 'SENSITIVE_TOKEN_IN_URL_AND_ERROR';
  const errors = [
    { value: Object.assign(new Error(`https://signed.invalid/${marker}`), { code: 'ETIMEDOUT', statusCode: 503 }), code: 'ETIMEDOUT', status: 503 },
    { value: Object.assign(new Error(marker), { code: marker, status: marker }), code: 'UNKNOWN' },
    { value: Object.assign(new Error(marker), { name: 'TimeoutError' }), code: 'TIMEOUT' },
    { value: Object.assign(new Error(marker), { cause: { code: 'ECONNRESET', message: marker } }), code: 'ECONNRESET' },
    { value: Object.assign(new Error(marker), { code: marker, name: 'ResponseTimeoutError', status: -2 }), code: 'ResponseTimeoutError' },
    { value: Object.assign(new Error(marker), { name: 'ConnectionTimeoutError', status: -2 }), code: 'ConnectionTimeoutError' },
  ];
  for (const stage of ['github.metadata', 'github.download', 'cdn.prewarm']) {
    for (const { value, code, status } of errors) {
      const events = []; let clock = 10;
      const run = createDiagnostics(event => events.push(event), () => clock += 5);
      await assert.rejects(run(stage, async () => { throw value; }), error => error === value);
      assert.deepEqual(events, [{ stage, event: 'begin' },
        { stage, event: 'failed', elapsedMs: 5, code, ...(status ? { status } : {}) }]);
      assert.equal(JSON.stringify(events).includes(marker), false);
      assert.equal(JSON.stringify(events).includes('signed.invalid'), false);
    }
  }
  const events = [], run = createDiagnostics(event => events.push(event));
  assert.equal(await run('github.metadata', async () => 42), 42);
  assert.equal(events.at(-1).event, 'done');
  await assert.rejects(run(`https://signed.invalid/${marker}`, async () => {}), /Unknown publication stage/);
  assert.equal(events.length, 2);
});

test('the locked SDK uploads Buffers in bounded parts and preserves immutable headers through completion', async () => {
  const installer = Buffer.alloc(3 * 8 * 1024 * 1024 + 321, 0x5a);
  installer.write('MZ');
  const selected = selectRelease(release('0.1.8', installer));
  const h = multipartStore([]);
  const result = await publish({ ...h, selected, bytes: installer });
  assert.equal(h.sdk.options.retryMax, 0);
  assert.equal(h.maxActive(), 3);
  assert.deepEqual([...h.parts.keys()].sort((a, b) => a - b), [1, 2, 3, 4]);
  assert.ok([...h.parts.values()].every(part => part.length <= 8 * 1024 * 1024));
  assert.equal(h.requests.filter(request => request.method === 'PUT').length, 4);
  assert.ok(h.requests.filter(request => request.method === 'PUT').every(request => request.subres.partNumber));
  const init = h.requests[0], complete = h.requests.at(-1);
  assert.equal(init.subres, 'uploads');
  assert.equal(init.mime, 'application/octet-stream');
  assert.equal(complete.subres.uploadId, 'synthetic-upload');
  for (const request of [init, complete]) {
    assert.equal(request.headers['x-oss-forbid-overwrite'], 'true');
    assert.equal(request.headers['Cache-Control'], 'public, max-age=31536000, immutable');
    assert.equal(request.headers['Content-Type'], 'application/octet-stream');
  }
  const installerKey = `${prefix}/0.1.8/${selected.asset.name}`;
  assert.ok(h.objects.get(installerKey).equals(installer));
  assert.equal(result.checksumSha256, sha(installer));
  assert.equal(result.files[0].size, installer.length);
  assert.equal(h.puts.at(-1), pointer);
  const requestCount = h.requests.length, immutable = Buffer.from(h.objects.get(`${prefix}/0.1.8/manifest.json`));
  await publish({ ...h, selected: { ...selected, releaseNotes: '修正说明。' }, bytes: installer });
  assert.equal(h.requests.length, requestCount, 'same-version edits must not retransmit any part');
  assert.equal(h.uploads.length, 1);
  assert.ok(h.objects.get(`${prefix}/0.1.8/manifest.json`).equals(immutable));
  assert.equal(h.cancellations.length, 0);
});

test('a separate upload client receives only installer multipart while the regional client verifies bytes and publishes metadata', async () => {
  const installer = Buffer.alloc(8 * 1024 * 1024 + 321, 0x5a);
  const selected = selectRelease(release('0.1.8', installer)), h = multipartStore([]);
  const client = { get: h.client.get, put: h.client.put,
    async multipartUpload() { assert.fail('regional requests must not upload installer parts'); },
    cancel() { assert.fail('only the actual uploader may be cancelled'); } };
  const uploadClient = { multipartUpload: h.client.multipartUpload, cancel: h.client.cancel,
    async get() { assert.fail('installer reads must use the regional endpoint'); },
    async put() { assert.fail('metadata writes must use the regional endpoint'); } };
  const result = await publish({ client, uploadClient, selected, bytes: installer });
  const installerKey = `${prefix}/0.1.8/${selected.asset.name}`;
  assert.deepEqual(h.uploads, [installerKey]);
  assert.deepEqual(h.puts, [`${prefix}/0.1.8/manifest.json`, pointer]);
  assert.deepEqual(h.gets, [pointer, `${prefix}/0.1.8/manifest.json`, installerKey, installerKey, pointer, pointer]);
  assert.ok(h.objects.get(installerKey).equals(installer));
  assert.equal(result.checksumSha256, sha(installer));
  assert.equal(result.sha512, createHash('sha512').update(installer).digest('base64'));
  assert.equal(JSON.parse(h.objects.get(pointer)).checksumSha256, sha(installer));
  assert.equal(h.requests[0].subres, 'uploads');
  assert.equal(h.requests.at(-1).subres.uploadId, 'synthetic-upload');
  assert.equal(h.cancellations.length, 0);
});

test('multipart part/completion failures and corrupt assembled bytes cannot promote latest', async () => {
  const installer = Buffer.alloc(8 * 1024 * 1024 + 321, 0x5a);
  const selected = selectRelease(release('0.1.8', installer));
  const old = Buffer.from(JSON.stringify(manifestFor(selectRelease(release()), bytes)));
  for (const fault of ['part', 'complete', 'corrupt']) {
    const h = multipartStore([[pointer, old]], fault), events = [];
    await assert.rejects(publish({ ...h, selected, bytes: installer, report: event => events.push(event) }));
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(h.objects.get(pointer).equals(old));
    assert.equal(h.puts.includes(pointer), false);
    assert.equal(h.objects.has(`${prefix}/0.1.8/manifest.json`), false);
    assert.equal(h.requests.some(request => request.method === 'DELETE' || request.method === 'GET'), false);
    assert.equal(JSON.stringify(events).includes('SENSITIVE_MULTIPART_URL_TOKEN'), false);
    const failed = events.at(-1);
    assert.equal(failed.event, 'failed');
    assert.equal(failed.stage, fault === 'corrupt' ? 'oss.verify-installer' : 'oss.upload-installer');
    if (fault !== 'corrupt') {
      assert.deepEqual(h.cancellations, [[]]);
      assert.equal(failed.code, fault === 'part' ? 'ConnectionTimeoutError' : 'ResponseTimeoutError');
      assert.ok(h.parts.size > 0, 'failed multipart uploads can retain completed fragments');
    }
    if (fault === 'part') assert.equal(h.requests.some(request => request.subres?.uploadId && request.method === 'POST'), false);
  }
});

test('OSS transport failures identify the exact stage without leaking errors or promoting latest', async () => {
  const old = Buffer.from(JSON.stringify(manifestFor(selectRelease(release()), bytes)));
  const selected = selectRelease(release('0.1.8')), marker = 'SENSITIVE_OSS_REQUEST_TOKEN';
  const installerKey = `${prefix}/0.1.8/${selected.asset.name}`;
  for (const expectedStage of ['oss.read-installer', 'oss.upload-installer', 'oss.verify-installer', 'oss.promote']) {
    const h = store([[pointer, old]]), events = [], get = h.client.get, put = h.client.put;
    let installerReads = 0;
    const failure = Object.assign(new Error(`https://oss.invalid/?token=${marker}`), { code: 'ETIMEDOUT', status: 504 });
    h.client.get = async key => {
      if (key === installerKey) {
        installerReads++;
        if (expectedStage === 'oss.read-installer' || (expectedStage === 'oss.verify-installer' && installerReads === 2)) throw failure;
      }
      return get(key);
    };
    h.client.put = async (key, ...args) => {
      if ((expectedStage === 'oss.upload-installer' && key === installerKey)
          || (expectedStage === 'oss.promote' && key === pointer)) throw failure;
      return put(key, ...args);
    };
    await assert.rejects(publish({ ...h, selected, bytes, report: event => events.push(event) }), error => error === failure);
    assert.ok(h.objects.get(pointer).equals(old));
    assert.equal(events.at(-1).stage, expectedStage);
    assert.equal(events.at(-1).event, 'failed');
    assert.equal(events.at(-1).code, 'ETIMEDOUT');
    assert.equal(events.at(-1).status, 504);
    assert.equal(JSON.stringify(events).includes(marker), false);
    assert.equal(JSON.stringify(events).includes('oss.invalid'), false);
    for (const event of events.filter(event => event.event === 'done'))
      assert.ok(events.some(begin => begin.stage === event.stage && begin.event === 'begin'));
  }
});
