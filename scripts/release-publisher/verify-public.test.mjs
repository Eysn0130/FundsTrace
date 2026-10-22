import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { createServer } from 'node:net';
import { PassThrough } from 'node:stream';
import { fileURLToPath } from 'node:url';
import { fetchPublicIpv4, verifyPublic } from './verify-public.mjs';

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
  const request = transport => async (url, options) => {
    assert.ok([githubUrl, siteUrl, downloadUrl].includes(url), 'only the three fixed public endpoints are requested');
    assert.equal(options.method, 'GET');
    assert.deepEqual(options.headers, { Accept: 'application/json', 'User-Agent': 'FundsTrace-public-verifier' });
    assert.equal(options.redirect, 'error');
    assert.ok(options.signal instanceof AbortSignal);
    const attempt = (counts.get(url) || 0) + 1;
    counts.set(url, attempt); calls.push({ url, options, transport });
    return await respond(url, attempt, transport) || json(url === githubUrl ? github : document);
  };
  const verify = options => verifyPublic({ expectedTag: 'v0.1.8', run: '12345', fetchImpl: request('fetch'),
    fallbackFetchImpl: request('https-ipv4'),
    wait: async delay => { waits.push(delay); }, report: event => events.push(event), ...options });
  return { verify, calls, counts, waits, events };
}

test('a connect timeout switches only that endpoint to IPv4 HTTPS within the original three attempts', async () => {
  for (const url of [githubUrl, siteUrl, downloadUrl]) {
    const h = harness((target, attempt) => {
      if (target === url && attempt < 3) throw networkError(attempt === 1 ? 'UND_ERR_CONNECT_TIMEOUT' : 'ECONNRESET');
    });
    assert.equal((await h.verify()).version, '0.1.8');
    assert.deepEqual(h.calls.filter(call => call.url === url).map(call => call.transport),
      ['fetch', 'https-ipv4', 'https-ipv4']);
    assert.ok(h.calls.filter(call => call.url !== url).every(call => call.transport === 'fetch'));
    assert.deepEqual(h.waits, [2000, 5000]);
  }
});

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
    assert.deepEqual(h.events, [{ endpoint: 'site', attempt: 1, transport: 'fetch', code },
      { endpoint: 'site', attempt: 2, transport: code === 'UND_ERR_CONNECT_TIMEOUT' ? 'https-ipv4' : 'fetch', code },
      ...(code === 'UND_ERR_CONNECT_TIMEOUT' ? [{ endpoint: 'site', attempt: 3, transport: 'https-ipv4', event: 'json-read' }] : [])]);
    assert.deepEqual(h.calls.filter(call => call.url === siteUrl).map(call => call.transport),
      code === 'UND_ERR_CONNECT_TIMEOUT' ? ['fetch', 'https-ipv4', 'https-ipv4'] : ['fetch', 'fetch', 'fetch']);
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
    assert.deepEqual(h.calls.filter(call => call.url === url).map(call => call.transport),
      ['fetch', 'https-ipv4', 'https-ipv4']);
    assert.deepEqual([...h.counts.keys()].sort(), [githubUrl, siteUrl, downloadUrl].sort());
  }
});

test('HTTP, JSON, TLS and unknown errors fail on either transport without further retry', async () => {
  for (const url of [githubUrl, siteUrl, downloadUrl]) {
    for (const fallback of [false, true]) {
      for (const respond of [() => new Response('Redirect', { status: 302 }),
        () => new Response('Forbidden', { status: 403 }), () => new Response('Not found', { status: 404 }),
        () => new Response('Unavailable', { status: 503 }), () => new Response('{invalid json'),
        () => { throw new TypeError('SENSITIVE_UNKNOWN_ERROR'); },
        ...['ENOTFOUND', 'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'ABORT_ERR'].map(code => () => { throw networkError(code); })]) {
        const h = harness((target, attempt) => {
          if (target !== url) return;
          if (fallback && attempt === 1) throw networkError('UND_ERR_CONNECT_TIMEOUT');
          return respond();
        });
        await assert.rejects(h.verify());
        assert.equal(h.counts.get(url), fallback ? 2 : 1);
        assert.deepEqual(h.waits, fallback ? [2000] : []);
        assert.equal(JSON.stringify(h.events).includes('SENSITIVE'), false);
      }
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
    for (const fallback of [false, true]) {
      for (const mutate of mutations) {
        const changed = structuredClone(document); mutate(changed);
        const h = harness((target, attempt) => {
          if (target !== url) return;
          if (fallback && attempt === 1) throw networkError('UND_ERR_CONNECT_TIMEOUT');
          return json(changed);
        });
        await assert.rejects(h.verify());
        const expectedCounts = [[githubUrl, 1], [siteUrl, 1], [downloadUrl, 1]];
        expectedCounts.find(([target]) => target === url)[1] = fallback ? 2 : 1;
        assert.deepEqual([...h.counts.entries()].sort(), expectedCounts.sort());
        assert.deepEqual(h.waits, fallback ? [2000] : []);
      }
    }
  }
  const wrongTag = harness();
  await assert.rejects(wrongTag.verify({ expectedTag: 'v0.1.7' }));
  assert.deepEqual(wrongTag.waits, []);
  const changedLatest = harness(url => url === githubUrl ? json({ ...github, id: 124 }) : undefined);
  await assert.rejects(changedLatest.verify());
  assert.deepEqual(changedLatest.waits, []);
});

test('IPv4 HTTPS keeps exact URL, GET headers, TLS validation and the caller deadline', async () => {
  const signal = new AbortController().signal;
  const headers = { Accept: 'application/json', 'User-Agent': 'FundsTrace-public-verifier' };
  const response = await fetchPublicIpv4(siteUrl, { method: 'GET', headers, signal }, (url, options, receive) => {
    assert.equal(url, siteUrl);
    assert.deepEqual(options, { method: 'GET', headers, signal, family: 4, agent: false, rejectUnauthorized: true });
    queueMicrotask(() => {
      const incoming = new PassThrough(); incoming.statusCode = 200;
      receive(incoming);
      incoming.end(`\uFEFF${JSON.stringify(document)}`);
    });
    return new EventEmitter();
  });
  assert.equal(response.ok, true);
  assert.deepEqual(await response.json(), document);
});

test('IPv4 HTTPS rejects status and TLS failures without following redirects or exposing raw errors', async () => {
  for (const statusCode of [undefined, 302, 403, 404, 503]) {
    let calls = 0;
    await assert.rejects(fetchPublicIpv4(siteUrl, { method: 'GET', headers: {}, signal: new AbortController().signal },
      (_url, _options, receive) => {
        calls++;
        queueMicrotask(() => {
          const incoming = new PassThrough(); incoming.statusCode = statusCode;
          receive(incoming);
          assert.equal(incoming.destroyed, true);
        });
        return new EventEmitter();
      }), /Public release metadata is unavailable/);
    assert.equal(calls, 1);
  }
  const tlsError = Object.assign(new Error('SENSITIVE_RAW_TLS_ERROR'), { code: 'ERR_TLS_CERT_ALTNAME_INVALID' });
  await assert.rejects(fetchPublicIpv4(siteUrl, { method: 'GET', headers: {}, signal: new AbortController().signal },
    () => {
      const request = new EventEmitter();
      queueMicrotask(() => request.emit('error', tlsError));
      return request;
    }), error => error === tlsError);
});

test('the real IPv4 HTTPS request aborts a stalled local TLS handshake within the caller budget', { timeout: 2000 }, async t => {
  const sockets = new Set();
  const server = createServer(socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
  });
  await assert.rejects(fetchPublicIpv4(`https://127.0.0.1:${server.address().port}/latest.yml`, {
    method: 'GET', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(50),
  }), { code: 'ABORT_ERR' });
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
