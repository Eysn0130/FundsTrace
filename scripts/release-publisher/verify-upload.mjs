import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { manifestFor, object, pointer, prefix, safeCodes, uploadInstaller } from './publish.mjs';

const number = value => Number.isSafeInteger(value) && value >= 0 ? value : undefined;
const codeFor = error => [error?.code, error?.name].find(code => safeCodes.has(code)) || 'UNKNOWN';

// Observe the existing HTTP implementation. Do not replace agents, streams, signing or TLS policy.
export function observeTransport(client, report, profile) {
  const upstream = client.urllib;
  client.urllib = { async request(url, args) {
    const query = new URL(url).searchParams;
    const operation = query.has('partNumber') ? 'part' : query.has('uploads') ? 'init'
      : query.has('uploadId') && args.method === 'POST' ? 'complete' : args.method === 'GET' ? 'read' : 'other';
    const emitter = new EventEmitter();
    emitter.once('response', ({ error, req, res }) => {
      const socket = req.socket;
      report({ stage: 'upload.transport', event: error ? 'failed' : 'done', profile, operation,
        part: number(Number(query.get('partNumber'))) || undefined,
        contentLength: number(Number(args.headers?.['Content-Length'])),
        elapsedMs: number(res.rt), requestSentMs: number(res.timing?.requestSent),
        connectedMs: number(res.timing?.connected), waitingMs: number(res.timing?.waiting),
        stream: Boolean(args.stream), expect: Boolean(args.headers?.Expect || args.headers?.expect),
        keepAlive: Boolean(res.keepAliveSocket),
        socketBytesWritten: number(socket?.bytesWritten), socketBytesRead: number(socket?.bytesRead),
        tls: Boolean(socket?.encrypted), tlsAuthorized: Boolean(socket?.authorized),
        ...(error ? { code: codeFor(error) } : {}),
        ...(res.status >= 100 && res.status <= 599 ? { status: res.status } : {}) });
    });
    return upstream.request(url, { ...args, timing: true, emitter });
  } };
  return () => { client.urllib = upstream; };
}

// A manual diagnostic, using the real latest installer and existing GET/PUT prefix only.
// Never write release metadata, promote latest, prewarm, delete or request wider permissions.
export async function verifyFreshUpload({ client, uploadClient = client, selected, bytes, run, report = () => {},
  assertLatest = async () => {}, requestTimeout = 90000, profileTimeout = 5 * 60000 }) {
  if (!/^\d{1,20}-\d{1,5}$/.test(run)) throw new Error('Invalid verification run identity');
  const manifest = manifestFor(selected, bytes);
  const before = await object(client, pointer);
  const results = [];
  for (const partMiB of [8, 1]) {
    const profile = `${partMiB}MiB`;
    const key = `${prefix}/_verification/${run}/${profile}/${selected.asset.name}`;
    // A rerun must use a new attempt. A cached object must never count as an upload pass.
    if (await object(client, key)) throw new Error('Verification object already exists');
    const restore = observeTransport(uploadClient, report, profile);
    const started = Date.now();
    report({ stage: 'upload.verification', event: 'begin', profile });
    try {
      await uploadInstaller(uploadClient, key, bytes, { partSize: partMiB * 1024 * 1024, timeout: requestTimeout,
        progress: async () => {
          if (Date.now() - started > profileTimeout)
            throw Object.assign(new Error('Verification deadline exceeded'), { code: 'ETIMEDOUT' });
        } });
      const uploaded = await object(client, key, { timeout: requestTimeout });
      if (!uploaded || uploaded.bytes.length !== bytes.length
          || createHash('sha256').update(uploaded.bytes).digest('hex') !== manifest.checksumSha256
          || createHash('sha512').update(uploaded.bytes).digest('base64') !== manifest.sha512)
        throw new Error('Fresh upload integrity verification failed');
      results.push({ profile, verified: true });
      report({ stage: 'upload.verification', event: 'done', profile, elapsedMs: Date.now() - started,
        size: bytes.length, sha256: manifest.checksumSha256, sha512: manifest.sha512 });
    } catch (error) {
      results.push({ profile, verified: false });
      report({ stage: 'upload.verification', event: 'failed', profile,
        elapsedMs: Date.now() - started, code: codeFor(error) });
    } finally {
      restore();
    }
  }
  await assertLatest();
  const after = await object(client, pointer);
  if ((before?.etag || null) !== (after?.etag || null)) throw new Error('Download entry changed during verification');
  if (!results.some(result => result.verified)) throw new Error('No fresh upload profile passed');
  return results;
}
