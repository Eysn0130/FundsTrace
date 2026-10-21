import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { runPublisher, safeEvent } from './run.mjs';

test('IPC output accepts only diagnostic enums, numeric counters and integrity hashes', () => {
  const marker = 'SENSITIVE_URL_OR_CREDENTIAL';
  assert.equal(safeEvent({ stage: marker, event: 'failed' }), null);
  const event = safeEvent({ stage: 'upload.transport', event: 'failed', profile: '8MiB', operation: 'part',
    code: 'ResponseTimeoutError', status: -2, elapsedMs: 90, part: 1, tls: true,
    requestSentMs: marker, socketBytesWritten: Infinity, sha256: marker, sha512: marker,
    headers: { authorization: marker }, message: marker, url: marker });
  assert.deepEqual(event, { stage: 'upload.transport', event: 'failed', elapsedMs: 90, part: 1,
    code: 'ResponseTimeoutError', profile: '8MiB', operation: 'part', tls: true });
  assert.equal(JSON.stringify(event).includes(marker), false);
});

test('real urllib timeout warnings and raw child output cannot bypass IPC log filtering', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'fundstrace-safe-publisher-'));
  const server = createServer((request, response) => {
    request.resume(); // Deliberately never return a response; all traffic is loopback synthetic.
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const marker = 'SENSITIVE_SYNTHETIC_URL_OR_CREDENTIAL';
  const script = join(directory, 'timeout.mjs');
  const sdk = new URL('./node_modules/ali-oss/lib/client.js', import.meta.url).href;
  const lines = [];
  try {
    await writeFile(script, `
      import OSS from ${JSON.stringify(sdk)};
      const client = new OSS({ bucket: 'synthetic-publisher-test', accessKeyId: '${marker}',
        accessKeySecret: '${marker}', endpoint: 'http://127.0.0.1:${server.address().port}',
        cname: true, timeout: 30 });
      console.log('${marker}');
      console.warn('${marker}');
      process.send({ stage: 'oss.upload-installer', event: 'begin', url: '${marker}' });
      try { await client.put('${marker}', Buffer.from('synthetic installer')); }
      catch { process.send({ stage: 'oss.upload-installer', event: 'failed', code: 'ResponseTimeoutError', message: '${marker}' }); }
      await new Promise(resolve => setTimeout(resolve, 50));
      process.exitCode = 1;
    `);
    assert.equal(await runPublisher(pathToFileURL(script), line => lines.push(line)), 1);
    assert.deepEqual(lines.map(line => JSON.parse(line)), [
      { stage: 'oss.upload-installer', event: 'begin' },
      { stage: 'oss.upload-installer', event: 'failed', code: 'ResponseTimeoutError' },
    ]);
    assert.equal(lines.join('\n').includes(marker), false);
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    await rm(directory, { recursive: true, force: true });
  }
});
