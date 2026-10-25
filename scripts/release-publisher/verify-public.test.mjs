import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { EventEmitter } from 'node:events';
import { createServer } from 'node:net';
import { get as httpsGet } from 'node:https';
import { PassThrough } from 'node:stream';
import { fetchPublicNative, resolvePublicAddresses, verifyPublic } from './verify-public.mjs';

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
    return await respond(url, attempt, transport, options) || json(url === githubUrl ? github : document);
  };
  const verify = options => verifyPublic({ expectedTag: 'v0.1.8', run: '12345', fetchImpl: request('fetch'),
    nativeFetchImpl: request('https-system'), dohFetchImpl: request('https-doh'),
    wait: async delay => { waits.push(delay); }, report: event => events.push(event), ...options });
  return { verify, calls, counts, waits, events };
}

test('each allowed transient network error retries twice then verifies all original endpoints on the third attempt', async t => {
  const timeouts = [];
  t.mock.method(AbortSignal, 'timeout', delay => { timeouts.push(delay); return new AbortController().signal; });
  for (const code of ['UND_ERR_CONNECT_TIMEOUT', 'ECONNRESET', 'EAI_AGAIN', 'ETIMEDOUT', 'ECONNREFUSED', 'ENETUNREACH', 'EHOSTUNREACH']) {
    const h = harness((url, attempt) => { if (url === siteUrl && attempt < 3) throw networkError(code); });
    const latest = await h.verify();
    assert.equal(latest.version, '0.1.8');
    assert.equal(h.counts.get(siteUrl), 3);
    assert.equal(h.counts.get(githubUrl), 1);
    assert.equal(h.counts.get(downloadUrl), 1);
    assert.deepEqual(h.waits, [2000, 5000]);
    assert.equal(new Set(h.calls.map(call => call.options.signal)).size, 5, 'every attempt receives a fresh timeout signal');
    assert.deepEqual(h.events.filter(event => event.code), [
      { endpoint: 'site', attempt: 1, transport: 'fetch', stage: 'request', code },
      { endpoint: 'site', attempt: 2, transport: 'https-system', stage: 'request', code },
    ]);
    assert.deepEqual(h.calls.filter(call => call.url === siteUrl).map(call => call.transport),
      ['fetch', 'https-system', 'https-doh']);
  }
  assert.equal(timeouts.length, 35);
  assert.ok(timeouts.every(delay => delay === 30000), 'each GET keeps its original 30 second budget');
});

test('a persistent permitted network error stops after exactly three GET attempts', async () => {
  for (const url of [githubUrl, siteUrl, downloadUrl]) {
    const h = harness(target => { if (target === url) throw networkError('UND_ERR_CONNECT_TIMEOUT'); });
    await assert.rejects(h.verify());
    assert.equal(h.counts.get(url), 3);
    assert.deepEqual(h.waits, [2000, 5000]);
    assert.equal(h.events.filter(event => event.code).length, 3);
    assert.equal(h.events.at(-1).attempt, 3);
    assert.deepEqual([...h.counts.keys()].sort(), [githubUrl, siteUrl, downloadUrl].sort());
  }
});

test('HTTP, JSON, TLS, external aborts and unknown errors fail on every transport without further retry', async () => {
  for (const url of [githubUrl, siteUrl, downloadUrl]) {
    for (const level of [1, 2, 3]) {
      for (const respond of [() => new Response('Redirect', { status: 302 }),
        () => new Response('Forbidden', { status: 403 }), () => new Response('Not found', { status: 404 }),
        () => new Response('Unavailable', { status: 503 }), () => new Response('{invalid json'),
        () => { throw new TypeError('SENSITIVE_UNKNOWN_ERROR'); },
        () => { throw new DOMException('SENSITIVE_EXTERNAL_ABORT', 'AbortError'); },
        () => { throw new DOMException('SENSITIVE_EXTERNAL_TIMEOUT', 'TimeoutError'); },
        ...['ENOTFOUND', 'ERR_TLS_CERT_ALTNAME_INVALID', 'CERT_HAS_EXPIRED', 'ABORT_ERR'].map(code =>
          () => { throw networkError(code); })]) {
        const h = harness((target, attempt) => {
          if (target !== url) return;
          if (attempt < level) throw networkError('ECONNRESET');
          return respond();
        });
        await assert.rejects(h.verify());
        assert.equal(h.counts.get(url), level);
        assert.deepEqual(h.waits, [2000, 5000].slice(0, level - 1));
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

test('a failed fetch changes transport to native system DNS then DoH within three attempts', async () => {
  const transports = [];
  const request = transport => async url => {
    if (url === siteUrl) {
      transports.push(transport);
      if (transport !== 'https-doh') throw networkError('UND_ERR_CONNECT_TIMEOUT');
    }
    return json(url === githubUrl ? github : document);
  };
  const latest = await verifyPublic({ expectedTag: 'v0.1.8', run: '12345',
    fetchImpl: request('fetch'), nativeFetchImpl: request('https-system'), dohFetchImpl: request('https-doh'),
    wait: async () => {}, report: () => {},
  });
  assert.equal(latest.version, '0.1.8');
  assert.deepEqual(transports, ['fetch', 'https-system', 'https-doh']);
});

const dnsDocument = hostname => ({ Status: 0, TC: false, CD: false, AD: false,
  Question: [{ name: `${hostname}.`, type: 1 }],
  Answer: [{ name: hostname, type: 5, data: 'cdn.example.' },
    { name: 'cdn.example.', type: 1, data: '192.0.2.10' },
    { name: 'cdn.example.', type: 1, data: '192.0.2.11' },
    { name: 'cdn.example.', type: 1, data: '192.0.2.10' }] });
const loopbackResolver = async () => [{ address: '127.0.0.1', family: 4 }];

test('fixed DoH requests preserve DNSSEC, suppress client subnet disclosure and deduplicate A addresses', async () => {
  for (const hostname of ['api.github.com', 'analytix.top', 'download.analytix.top']) {
    const signal = new AbortController().signal;
    const addresses = await resolvePublicAddresses(hostname, { signal, fetchImpl: async (value, options) => {
      const url = new URL(value);
      assert.equal(url.origin + url.pathname, 'https://dns.google/resolve');
      assert.deepEqual([...url.searchParams], [['name', hostname], ['type', 'A'], ['edns_client_subnet', '0.0.0.0/0']]);
      assert.deepEqual(options, { method: 'GET', redirect: 'error', signal,
        headers: { Accept: 'application/json', 'User-Agent': 'FundsTrace-public-verifier' } });
      return json(dnsDocument(hostname));
    } });
    assert.deepEqual(addresses, [{ address: '192.0.2.10', family: 4 }, { address: '192.0.2.11', family: 4 }]);
  }
});

test('DoH rejects foreign hosts, transport errors and incomplete or invalid DNS answers', async () => {
  const signal = new AbortController().signal;
  let calls = 0;
  await assert.rejects(resolvePublicAddresses('foreign.invalid', { signal, fetchImpl: async () => { calls++; } }));
  assert.equal(calls, 0);
  const mutations = [value => { value.Status = 2; }, value => { value.Status = '0'; },
    value => { value.TC = true; }, value => { delete value.TC; }, value => { value.CD = true; },
    value => { delete value.CD; }, value => { value.Question = []; },
    value => { value.Question.push({ name: 'analytix.top.', type: 1 }); },
    value => { value.Question[0].name = 'foreign.invalid.'; }, value => { value.Question[0].type = 28; },
    value => { value.Answer = []; }, value => { delete value.Answer; },
    value => { value.Answer = [{ type: 5, data: 'cdn.example.' }]; },
    ...['not-an-ip', '2001:db8::1', '192.0.2.999', null].map(data => value => { value.Answer[1].data = data; })];
  for (const mutate of mutations) {
    const value = dnsDocument('analytix.top'); mutate(value);
    await assert.rejects(resolvePublicAddresses('analytix.top', { signal, fetchImpl: async () => json(value) }));
  }
  const redirected = json(dnsDocument('analytix.top'));
  Object.defineProperty(redirected, 'redirected', { value: true });
  for (const response of [new Response('', { status: 302 }), new Response('', { status: 403 }),
    new Response('SENSITIVE_INVALID_JSON'), redirected]) {
    await assert.rejects(resolvePublicAddresses('analytix.top', { signal, fetchImpl: async () => response }));
  }
  await assert.rejects(resolvePublicAddresses('analytix.top', { signal,
    fetchImpl: async () => { throw networkError('ABORT_ERR'); } }));
});

test('native HTTPS keeps exact URL, GET headers, TLS validation and the caller deadline', async () => {
  const signal = new AbortController().signal;
  const headers = { Accept: 'application/json', 'User-Agent': 'FundsTrace-public-verifier' };
  const response = await fetchPublicNative(siteUrl, { method: 'GET', headers, signal }, (url, options, receive) => {
    assert.equal(url, siteUrl);
    const { lookup, ...connectionOptions } = options;
    assert.deepEqual(connectionOptions, { method: 'GET', headers, signal, family: 0, autoSelectFamily: true, agent: false, rejectUnauthorized: true });
    lookup('analytix.top', { all: true }, (error, addresses) => {
      assert.equal(error, null);
      assert.deepEqual(addresses, [{ address: '192.0.2.10', family: 4 }, { address: '192.0.2.11', family: 4 }]);
    });
    lookup('analytix.top', {}, (error, address, family) => {
      assert.equal(error, null); assert.equal(address, '192.0.2.10'); assert.equal(family, 4);
    });
    queueMicrotask(() => {
      const incoming = new PassThrough(); incoming.statusCode = 200;
      receive(incoming);
      incoming.end(`\uFEFF${JSON.stringify(document)}`);
    });
    return new EventEmitter();
  }, (hostname, options) => {
    assert.equal(hostname, 'analytix.top'); assert.equal(options.signal, signal);
    return resolvePublicAddresses(hostname, { ...options, fetchImpl: async (_url, dohOptions) => {
      assert.equal(dohOptions.signal, signal, 'DoH and HTTPS consume the same deadline');
      return json(dnsDocument(hostname));
    } });
  });
  assert.equal(response.ok, true);
  assert.deepEqual(await response.json(), document);
});

test('native HTTPS rejects status and TLS failures without following redirects or exposing raw errors', async () => {
  for (const statusCode of [undefined, 302, 403, 404, 503]) {
    let calls = 0;
    await assert.rejects(fetchPublicNative(siteUrl, { method: 'GET', headers: {}, signal: new AbortController().signal },
      (_url, _options, receive) => {
        calls++;
        queueMicrotask(() => {
          const incoming = new PassThrough(); incoming.statusCode = statusCode;
          receive(incoming);
          assert.equal(incoming.destroyed, true);
        });
        return new EventEmitter();
      }, loopbackResolver), /Public release metadata is unavailable/);
    assert.equal(calls, 1);
  }
  const tlsError = Object.assign(new Error('SENSITIVE_RAW_TLS_ERROR'), { code: 'ERR_TLS_CERT_ALTNAME_INVALID' });
  await assert.rejects(fetchPublicNative(siteUrl, { method: 'GET', headers: {}, signal: new AbortController().signal },
    () => {
      const request = new EventEmitter();
      queueMicrotask(() => request.emit('error', tlsError));
      return request;
    }, loopbackResolver), error => error === tlsError);
});

test('a failed DoH resolution never starts an HTTPS request or resets the deadline', async () => {
  let requests = 0;
  const signal = new AbortController().signal;
  await assert.rejects(fetchPublicNative(siteUrl, { method: 'GET', headers: {}, signal },
    () => { requests++; }, (hostname, options) => {
      assert.equal(hostname, 'analytix.top'); assert.equal(options.signal, signal);
      throw new Error('Invalid public DNS response');
    }), /Invalid public DNS response/);
  assert.equal(requests, 0);
});

test('the real native HTTPS request aborts a stalled local TLS handshake within the caller budget', { timeout: 2000 }, async t => {
  const sockets = new Set();
  const server = createServer(socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
  });
  await assert.rejects(fetchPublicNative(`https://127.0.0.1:${server.address().port}/latest.yml`, {
    method: 'GET', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(50),
  }, httpsGet, loopbackResolver), { code: 'ABORT_ERR' });
});

test('native HTTPS tries the second DNS address when the first local address does not accept the connection', { timeout: 2000 }, async t => {
  let accepted = 0, lookupAll;
  const sockets = new Set();
  const server = createServer(socket => {
    accepted++;
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
  });
  const get = (url, options, receive) => httpsGet(url, { ...options,
    lookup(hostname, lookupOptions, callback) {
      assert.equal(hostname, 'public-readback.test');
      lookupAll = lookupOptions.all;
      options.lookup(hostname, lookupOptions, callback);
    },
  }, receive);
  await assert.rejects(fetchPublicNative(`https://public-readback.test:${server.address().port}/latest.yml`, {
    method: 'GET', headers: { Accept: 'application/json' }, signal: AbortSignal.timeout(600),
  }, get, async hostname => {
    assert.equal(hostname, 'public-readback.test');
    return [{ address: '127.0.0.2', family: 4 }, { address: '127.0.0.1', family: 4 }];
  }), { code: 'ABORT_ERR' });
  assert.equal(lookupAll, true);
  assert.equal(accepted, 1, 'the second address reaches the local stalled TLS server');
});


test('mixed aggregate errors and disallowed outer codes cannot borrow a retryable child', async () => {
  const allowed = () => Object.assign(new Error('SENSITIVE_ADDRESS_FAILURE'), { code: 'ECONNREFUSED' });
  for (const error of [
    new AggregateError([allowed(), Object.assign(new Error('SENSITIVE_CERTIFICATE'), { code: 'CERT_HAS_EXPIRED' })]),
    Object.assign(new AggregateError([allowed(), new Error('SENSITIVE_UNKNOWN')]), { code: 'ETIMEDOUT' }),
    Object.assign(new AggregateError([allowed()]), { code: 'ERR_TLS_CERT_ALTNAME_INVALID' }),
    Object.assign(new Error('SENSITIVE_OUTER'), { code: 'ERR_TLS_CERT_ALTNAME_INVALID', cause: allowed() }),
    Object.assign(new Error('SENSITIVE_EMPTY_AGGREGATE'), { errors: [], code: 'ECONNRESET' }),
    Object.assign(new Error('SENSITIVE_MALFORMED_AGGREGATE'), { errors: 'ECONNRESET', code: 'ECONNRESET' }),
    Object.assign(new AggregateError([allowed()]), { cause: new DOMException('SENSITIVE_ABORT', 'AbortError') }),
  ]) {
    const h = harness(url => { if (url === siteUrl) throw error; });
    await assert.rejects(h.verify());
    assert.equal(h.counts.get(siteUrl), 1);
    assert.deepEqual(h.waits, []);
    assert.equal(JSON.stringify(h.events).includes('SENSITIVE'), false);
  }
  const cycle = new Error('SENSITIVE_CYCLE'); cycle.cause = cycle;
  const circular = harness(url => { if (url === siteUrl) throw cycle; });
  await assert.rejects(circular.verify());
  assert.equal(circular.counts.get(siteUrl), 1);
  const h = harness((url, attempt) => {
    if (url === siteUrl && attempt === 1)
      throw Object.assign(new AggregateError([allowed(), Object.assign(new Error(), { code: 'ETIMEDOUT' })]), { code: 'ETIMEDOUT' });
  });
  assert.equal((await h.verify()).version, '0.1.8');
  assert.deepEqual(h.calls.filter(call => call.url === siteUrl).map(call => call.transport), ['fetch', 'https-system']);
  assert.equal(h.events.find(event => event.code)?.code, 'NETWORK_AGGREGATE');
});

test('only the attempt-owned deadline permits an aborted request to advance', async t => {
  const timeout = AbortSignal.timeout;
  t.mock.method(AbortSignal, 'timeout', ms => { assert.equal(ms, 30000); return timeout(10); });
  for (const external of [false, true]) {
    const h = harness(async (url, attempt, _transport, { signal }) => {
      if (url !== siteUrl || attempt !== 1) return;
      await new Promise(resolve => setTimeout(resolve, 25));
      assert.equal(signal.aborted, true);
      if (external) throw new DOMException('SENSITIVE_FOREIGN_ABORT', 'AbortError');
      throw Object.assign(new Error('SENSITIVE_OWN_ABORT'), { name: 'AbortError', code: 'ABORT_ERR', cause: signal.reason });
    });
    if (external) {
      await assert.rejects(h.verify());
      assert.equal(h.counts.get(siteUrl), 1);
      assert.deepEqual(h.waits, []);
    } else {
      assert.equal((await h.verify()).version, '0.1.8');
      assert.equal(h.counts.get(siteUrl), 2);
      assert.equal(h.events.find(event => event.code)?.code, 'DEADLINE');
    }
  }
});

test('a terminal endpoint failure cancels and drains parallel requests without fallback', async () => {
  const aborted = [], calls = [], events = [];
  const fetchImpl = async (url, { signal }) => {
    calls.push(url);
    if (url === siteUrl) return new Response('Forbidden', { status: 403 });
    return new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => { aborted.push(url); reject(signal.reason); }, { once: true });
    });
  };
  let fallbacks = 0;
  const fallback = async () => { fallbacks++; throw new Error('Unexpected fallback'); };
  await assert.rejects(verifyPublic({ expectedTag: 'v0.1.8', run: '12345', fetchImpl,
    nativeFetchImpl: fallback, dohFetchImpl: fallback, report: value => events.push(value) }));
  assert.deepEqual(calls.sort(), [githubUrl, siteUrl, downloadUrl].sort());
  assert.deepEqual(aborted.sort(), [githubUrl, downloadUrl].sort());
  assert.equal(fallbacks, 0);
  assert.deepEqual(events, [{ endpoint: 'site', attempt: 1, transport: 'fetch', stage: 'headers', code: 'UNKNOWN' }]);
});

test('a terminal endpoint cancels another endpoint waiting before its next transport', async () => {
  let waiting, cancelled = false, fallbacks = 0;
  const ready = new Promise(resolve => { waiting = resolve; });
  await assert.rejects(verifyPublic({ expectedTag: 'v0.1.8', run: '12345',
    fetchImpl: async url => {
      if (url === siteUrl) throw networkError('ECONNRESET');
      if (url === downloadUrl) { await ready; return new Response('Forbidden', { status: 403 }); }
      return json(github);
    },
    nativeFetchImpl: async () => { fallbacks++; }, dohFetchImpl: async () => { fallbacks++; },
    wait: async (_ms, _value, { signal }) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => { cancelled = true; reject(signal.reason); }, { once: true });
      waiting();
    }), report: () => {},
  }));
  assert.equal(cancelled, true);
  assert.equal(fallbacks, 0);
});

test('native diagnostics distinguish DNS, TCP, TLS, headers and body without raw network messages', async () => {
  for (const failureStage of ['dns', 'tcp', 'tls', 'headers', 'body']) {
    const get = (_url, options, receive) => {
      assert.equal('lookup' in options, false, 'system DNS stays owned by Node');
      const request = new EventEmitter();
      queueMicrotask(() => {
        const socket = new EventEmitter(); request.emit('socket', socket);
        if (failureStage !== 'dns') socket.emit('lookup', null, '192.0.2.10', 4, 'analytix.top');
        if (['tls', 'headers', 'body'].includes(failureStage)) socket.emit('connect');
        if (['headers', 'body'].includes(failureStage)) socket.emit('secureConnect');
        if (failureStage === 'body') {
          const response = new PassThrough(); response.statusCode = 200; receive(response);
          response.emit('error', Object.assign(new Error('SENSITIVE_BODY_ERROR'), { code: 'ECONNRESET' }));
          response.destroy();
        } else request.emit('error', Object.assign(new Error('SENSITIVE_NATIVE_ERROR'), { code: 'ECONNRESET' }));
      });
      return request;
    };
    const events = [];
    const value = await verifyPublic({ expectedTag: 'v0.1.8', run: '12345',
      fetchImpl: async url => { if (url === siteUrl) throw networkError('ECONNRESET'); return json(url === githubUrl ? github : document); },
      nativeFetchImpl: (url, options) => fetchPublicNative(url, options, get),
      dohFetchImpl: async () => json(document), wait: async () => {}, report: event => events.push(event),
    });
    assert.equal(value.version, '0.1.8');
    assert.deepEqual(events.find(event => event.transport === 'https-system'),
      { endpoint: 'site', attempt: 2, transport: 'https-system', stage: failureStage, code: 'ECONNRESET' });
    assert.equal(JSON.stringify(events).includes('SENSITIVE'), false);
  }
});

test('a real system-DNS native connection that stalls in TLS advances to DoH within the owned deadline', { timeout: 3000 }, async t => {
  const sockets = new Set();
  let accepted = 0, lookupAll = false;
  const server = createServer(socket => {
    accepted++; sockets.add(socket); socket.on('close', () => sockets.delete(socket));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
  });
  const timeout = AbortSignal.timeout;
  t.mock.method(AbortSignal, 'timeout', ms => { assert.equal(ms, 30000); return timeout(600); });
  const get = (_url, options, receive) => {
    assert.equal('lookup' in options, false, 'the system-native transport does not install a DoH lookup');
    return httpsGet(`https://native-readback.test:${server.address().port}/latest.yml`, { ...options,
      lookup(_hostname, lookupOptions, callback) {
        lookupAll = lookupOptions.all;
        callback(null, [{ address: '127.0.0.2', family: 4 }, { address: '127.0.0.1', family: 4 }]);
      },
    }, receive);
  };
  const events = [], transports = [];
  const latest = await verifyPublic({ expectedTag: 'v0.1.8', run: '12345',
    fetchImpl: async url => {
      if (url === siteUrl) { transports.push('fetch'); throw networkError('UND_ERR_CONNECT_TIMEOUT'); }
      return json(url === githubUrl ? github : document);
    },
    nativeFetchImpl: (url, options) => { transports.push('https-system'); return fetchPublicNative(url, options, get); },
    dohFetchImpl: async () => { transports.push('https-doh'); return json(document); },
    wait: async () => {}, report: event => events.push(event),
  });
  assert.equal(latest.version, '0.1.8');
  assert.deepEqual(transports, ['fetch', 'https-system', 'https-doh']);
  assert.equal(lookupAll, true);
  assert.equal(accepted, 1, 'the refused first address must not prevent connection to the second address');
  assert.deepEqual(events.find(event => event.transport === 'https-system'),
    { endpoint: 'site', attempt: 2, transport: 'https-system', stage: 'tls', code: 'DEADLINE' });
});


test('the default retry timer is cancelled promptly when another endpoint terminates', { timeout: 1500 }, async () => {
  let fallbacks = 0;
  const started = performance.now();
  await assert.rejects(verifyPublic({ expectedTag: 'v0.1.8', run: '12345',
    fetchImpl: async url => {
      if (url === siteUrl) throw networkError('ECONNRESET');
      if (url === downloadUrl) {
        await new Promise(resolve => setTimeout(resolve, 20));
        return new Response('Forbidden', { status: 403 });
      }
      return json(github);
    },
    nativeFetchImpl: async () => { fallbacks++; }, dohFetchImpl: async () => { fallbacks++; }, report: () => {},
  }));
  assert.equal(fallbacks, 0);
  assert.ok(performance.now() - started < 1000, 'the original two-second wait must not survive cancellation');
});
