import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { Agent, createServer } from 'node:http';
import OSS from 'ali-oss';
import { pointer, prefix, selectRelease } from './publish.mjs';
import { verifyFreshUpload } from './verify-upload.mjs';

const MiB = 1024 * 1024;
const bytes = Buffer.alloc(3 * MiB + 321, 0x5a); // Public synthetic bytes, never executed.
const digest = (value, algorithm = 'sha256', encoding = 'hex') => createHash(algorithm).update(value).digest(encoding);
const selected = selectRelease({ id: 123, tag_name: 'v0.1.8', published_at: '2026-10-20T12:00:00Z',
  draft: false, prerelease: false, body: 'Synthetic verification fixture.',
  assets: [{ id: 456, name: 'FundsTrace-0.1.8-windows-x64-unsigned.exe', size: bytes.length,
    digest: `sha256:${digest(bytes)}`,
    browser_download_url: 'https://github.com/Eysn0130/FundsTrace/releases/download/v0.1.8/FundsTrace-0.1.8-windows-x64-unsigned.exe' }] });
const currentBytes = Buffer.from('{"product":"com.eysn.fundstrace","version":"0.1.7","unchanged":true}\n');
const keyFor = (run, profile) => `${prefix}/_verification/${run}/${profile}/${selected.asset.name}`;
const xmlEscape = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
const etagFor = value => `"${digest(value, 'md5')}"`;

// Exercise the unmodified SDK request, signing, multipart streams and urllib
// against an owned loopback server. No live OSS account or credential is used.
async function localOss(t, { failProfiles = [], corruptProfiles = [] } = {}) {
  const objects = new Map([[pointer, Buffer.from(currentBytes)]]), requests = [], uploads = new Map();
  const completed = [], serverErrors = [], transport = [], sockets = new Set();
  let nextUpload = 0;
  const server = createServer(async (request, response) => {
    const send = (status, body, headers = {}) => {
      response.writeHead(status, { 'Content-Type': 'application/xml', 'x-oss-request-id': 'synthetic-request', ...headers });
      response.end(body);
    };
    const fail = (status, code) => send(status, `<Error><Code>${code}</Code><Message>Synthetic test response</Message><RequestId>synthetic-request</RequestId></Error>`);
    try {
      const url = new URL(request.url, 'http://127.0.0.1');
      const key = decodeURIComponent(url.pathname.slice(1));
      const profile = key.includes('/8MiB/') ? '8MiB' : key.includes('/1MiB/') ? '1MiB' : null;
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const body = Buffer.concat(chunks);
      const record = { method: request.method, key, profile, query: [...url.searchParams.keys()].sort(),
        contentLength: request.headers['content-length'], bytes: body.length };
      requests.push(record);
      if (request.method === 'GET' && !url.search) {
        const stored = objects.get(key);
        if (!stored) return fail(404, 'NoSuchKey');
        const content = Buffer.from(stored);
        if (corruptProfiles.includes(profile)) content[0] ^= 1;
        return send(200, content, { 'Content-Type': 'application/octet-stream', ETag: etagFor(content),
          'Content-Length': String(content.length) });
      }
      if (!profile || !key.startsWith(`${prefix}/_verification/`)) return fail(403, 'AccessDenied');
      if (request.method === 'POST' && url.searchParams.has('uploads')) {
        if (failProfiles.includes(profile)) return fail(503, 'ServiceUnavailable');
        const uploadId = `synthetic-upload-${++nextUpload}`;
        uploads.set(uploadId, { key, parts: new Map() });
        return send(200, `<InitiateMultipartUploadResult><Bucket>synthetic-verification</Bucket><Key>${xmlEscape(key)}</Key><UploadId>${uploadId}</UploadId></InitiateMultipartUploadResult>`);
      }
      const upload = uploads.get(url.searchParams.get('uploadId'));
      if (!upload || upload.key !== key) return fail(404, 'NoSuchUpload');
      if (request.method === 'PUT' && url.searchParams.has('partNumber')) {
        const number = Number(url.searchParams.get('partNumber'));
        assert.equal(Number(record.contentLength), body.length, 'the HTTP part contains its declared full byte count');
        const etag = etagFor(body);
        upload.parts.set(number, { bytes: body, etag });
        Object.assign(record, { part: number, etag });
        return send(200, '', { ETag: etag });
      }
      if (request.method === 'POST' && url.searchParams.has('uploadId')) {
        assert.equal(Number(record.contentLength), body.length, 'the completion XML has its actual content length');
        const declared = [...body.toString('utf8').matchAll(/<Part>\s*<PartNumber>(\d+)<\/PartNumber>\s*<ETag>([^<]+)<\/ETag>\s*<\/Part>/g)]
          .map(match => ({ number: Number(match[1]), etag: match[2] }));
        assert.equal(declared.length, upload.parts.size, 'completion acknowledges every received part');
        assert.ok(declared.length > 0);
        for (const part of declared) assert.equal(part.etag, upload.parts.get(part.number)?.etag,
          'the SDK completion uses the actual server ETag for each part');
        const content = Buffer.concat(declared.map(part => upload.parts.get(part.number).bytes));
        assert.equal(objects.has(key), false, 'a verification upload may only create a fresh object');
        objects.set(key, content);
        completed.push({ key, profile, declared, bytes: content.length, sha256: digest(content),
          sha512: digest(content, 'sha512', 'base64') });
        return send(200, `<CompleteMultipartUploadResult><Bucket>synthetic-verification</Bucket><Key>${xmlEscape(key)}</Key><ETag>${etagFor(content)}</ETag></CompleteMultipartUploadResult>`, { ETag: etagFor(content) });
      }
      return fail(403, 'AccessDenied');
    } catch (error) {
      serverErrors.push(error);
      if (!response.headersSent) fail(500, 'InternalError'); else response.destroy();
    }
  });
  server.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)); });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const agent = new Agent({ keepAlive: true });
  const client = new OSS({ endpoint: `http://127.0.0.1:${server.address().port}`, secure: false,
    bucket: 'synthetic-verification', accessKeyId: 'synthetic-only-key', accessKeySecret: 'synthetic-only-secret',
    agent, retryMax: 0, timeout: 5000 });
  const upstream = client.urllib;
  t.after(async () => {
    agent.destroy();
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  });
  const verify = (run = '12345-1', options = {}) => verifyFreshUpload({ client, selected, bytes, run,
    report: event => transport.push(event), requestTimeout: 5000, profileTimeout: 15000, ...options });
  const assertReadWriteBoundary = () => {
    assert.deepEqual(serverErrors, [], 'the synthetic OSS server accepted the real SDK protocol');
    for (const item of requests) {
      assert.ok(['GET', 'PUT', 'POST'].includes(item.method), 'verification never sends DELETE or cloud Abort');
      if (item.method === 'GET') assert.deepEqual(item.query, [], 'verification never lists multipart uploads or objects');
      else {
        assert.match(item.key, new RegExp(`^${prefix}/_verification/[0-9]+-[0-9]+/(?:8|1)MiB/FundsTrace-0\\.1\\.8-windows-x64-unsigned\\.exe$`));
        assert.ok(item.query.includes('uploads') || item.query.includes('uploadId'));
        assert.notEqual(item.key, pointer, 'latest and version metadata are never written');
        assert.equal(item.key.endsWith('/manifest.json'), false);
      }
    }
    assert.ok(objects.get(pointer).equals(currentBytes), 'the original download entry bytes remain unchanged');
    assert.equal(client.urllib, upstream, 'the transport observer restores the SDK HTTP implementation');
    assert.equal(client.agent, agent, 'the observer never replaces the configured agent');
    assert.equal(client.request, OSS.prototype.request, 'uploads retain the real SDK request implementation');
    const logs = JSON.stringify(transport);
    assert.equal(logs.includes('synthetic-only-key'), false);
    assert.equal(logs.includes('synthetic-only-secret'), false);
    assert.equal(logs.includes('127.0.0.1'), false, 'timing events do not expose signed request URLs');
  };
  return { client, objects, requests, completed, transport, verify, assertReadWriteBoundary };
}

test('fresh verification uploads both profiles through real OSS HTTP multipart and verifies complete bytes without moving latest', async t => {
  const h = await localOss(t);
  let latestChecks = 0;
  const result = await h.verify('12345-1', { assertLatest: async () => { latestChecks++; } });
  assert.deepEqual(result, [{ profile: '8MiB', verified: true }, { profile: '1MiB', verified: true }]);
  assert.equal(latestChecks, 1);
  assert.equal(h.requests.filter(item => item.key === pointer).length, 2);
  assert.equal(h.completed.length, 2);
  for (const profile of ['8MiB', '1MiB']) {
    const uploaded = h.objects.get(keyFor('12345-1', profile));
    assert.ok(uploaded.equals(bytes), 'the server really received the full input, independent of timing events');
    assert.equal(digest(uploaded), selected.asset.digest.slice(7));
    assert.equal(digest(uploaded, 'sha512', 'base64'), digest(bytes, 'sha512', 'base64'));
    const lengths = h.requests.filter(item => item.profile === profile && item.method === 'PUT')
      .sort((a, b) => a.part - b.part).map(item => item.bytes);
    assert.deepEqual(lengths, profile === '8MiB' ? [3 * MiB + 321] : [MiB, MiB, MiB, 321]);
    assert.ok(h.requests.some(item => item.profile === profile && item.method === 'GET' && item.bytes === 0),
      'each completed object is read back over HTTP');
    const verified = h.transport.find(event => event.stage === 'upload.verification' && event.event === 'done' && event.profile === profile);
    assert.equal(verified.size, bytes.length); assert.equal(verified.sha256, digest(bytes));
    assert.equal(verified.sha512, digest(bytes, 'sha512', 'base64'));
  }
  assert.ok(h.transport.some(event => event.stage === 'upload.transport' && event.operation === 'part'),
    'the observer is exercised against real urllib response events');
  const writes = h.requests.filter(item => item.method !== 'GET').length;
  await assert.rejects(h.verify('12345-1'), /already exists/);
  assert.equal(h.requests.filter(item => item.method !== 'GET').length, writes, 'an existing run cannot be mistaken for a fresh upload');
  h.assertReadWriteBoundary();
});

test('a failed first profile still tries and verifies the second profile', async t => {
  const h = await localOss(t, { failProfiles: ['8MiB'] });
  assert.deepEqual(await h.verify(), [{ profile: '8MiB', verified: false }, { profile: '1MiB', verified: true }]);
  assert.deepEqual(h.requests.filter(item => item.query.includes('uploads')).map(item => item.profile), ['8MiB', '1MiB']);
  assert.equal(h.objects.has(keyFor('12345-1', '8MiB')), false);
  assert.ok(h.objects.get(keyFor('12345-1', '1MiB')).equals(bytes));
  h.assertReadWriteBoundary();
});

test('two failed profiles reject and never request Abort, deletion or metadata writes', async t => {
  const h = await localOss(t, { failProfiles: ['8MiB', '1MiB'] });
  await assert.rejects(h.verify(), /No fresh upload profile passed/);
  assert.deepEqual(h.requests.filter(item => item.query.includes('uploads')).map(item => item.profile), ['8MiB', '1MiB']);
  assert.equal(h.completed.length, 0);
  assert.deepEqual(h.transport.filter(event => event.stage === 'upload.verification' && event.event === 'failed').map(event => event.profile), ['8MiB', '1MiB']);
  h.assertReadWriteBoundary();
});

test('corrupt HTTP readback never counts as a verified upload even when transport and completion succeeded', async t => {
  const h = await localOss(t, { corruptProfiles: ['8MiB', '1MiB'] });
  await assert.rejects(h.verify(), /No fresh upload profile passed/);
  assert.equal(h.completed.length, 2, 'both profiles reached actual successful multipart completion');
  for (const result of h.completed) {
    assert.equal(result.bytes, bytes.length); assert.equal(result.sha256, digest(bytes));
  }
  assert.ok(h.transport.some(event => event.stage === 'upload.transport' && event.event === 'done'));
  assert.equal(h.transport.some(event => event.stage === 'upload.verification' && event.event === 'done'), false,
    'socket timing is not an integrity or service receipt');
  h.assertReadWriteBoundary();
});

test('invalid run identities and installer bytes are rejected before any OSS request', async t => {
  const h = await localOss(t);
  for (const run of ['', '12345', '../123-1', '12345-1/other', '1-2?uploads', '1'.repeat(21) + '-1']) {
    await assert.rejects(h.verify(run), /Invalid verification run identity/);
  }
  await assert.rejects(h.verify('12345-1', { bytes: Buffer.from('different public synthetic installer') }), /do not match/);
  const sameSizeCorrupt = Buffer.from(bytes); sameSizeCorrupt[0] ^= 1;
  await assert.rejects(h.verify('12345-1', { bytes: sameSizeCorrupt }), /do not match/);
  assert.equal(h.requests.length, 0, 'invalid inputs do not even read a cloud pointer');
  assert.equal(h.completed.length, 0);
  h.assertReadWriteBoundary();
});
