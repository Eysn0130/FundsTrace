import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { prewarm } from './prewarm.mjs';

const url = 'https://download.analytix.top/releases/fundstrace/windows/0.1.8/FundsTrace-0.1.8-windows-x64-unsigned.exe';
const sha512 = createHash('sha512').update('synthetic').digest('base64');
const manifest = { schemaVersion: 1, product: 'com.eysn.fundstrace', version: '0.1.8', path: url, sha512,
  files: [{ url, sha512, size: 9 }], checksumSha256: 'a'.repeat(64) };
function harness() {
  const objects = new Map(), submissions = [];
  const client = {
    async get(key) { if (!objects.has(key)) throw Object.assign(new Error(), { code: 'NoSuchKey' }); return { content: objects.get(key) }; },
    async put(key, bytes, options) { assert.equal(options.headers['x-oss-forbid-overwrite'], 'true'); assert.ok(!objects.has(key)); objects.set(key, bytes); },
  };
  return { client, objects, submissions, submit: async url => { submissions.push(url); return '123456789'; } };
}
test('preheats only the validated installer once and verifies a durable retry receipt', async () => {
  const h = harness(), first = await prewarm({ ...h, manifest });
  assert.deepEqual(h.submissions, [url]); assert.equal(first.taskId, '123456789');
  assert.deepEqual(await prewarm({ ...h, manifest }), first); assert.equal(h.submissions.length, 1);
});
test('foreign or mutable URLs and a changed same-version digest cannot reuse preheat authority', async () => {
  const h = harness();
  await assert.rejects(prewarm({ ...h, manifest: { ...manifest, path: 'https://foreign.test/a.exe' } }));
  assert.deepEqual(h.submissions, []);
  await prewarm({ ...h, manifest });
  await assert.rejects(prewarm({ ...h, manifest: { ...manifest, checksumSha256: 'b'.repeat(64) } }), /receipt differs/);
  assert.equal(h.submissions.length, 1);
});
test('provider errors or invalid task ids cannot be recorded as successful prewarming', async () => {
  for (const submit of [async () => { throw new Error('Provider failed'); }, async () => null]) {
    const h = harness(); await assert.rejects(prewarm({ ...h, manifest, submit })); assert.equal(h.objects.size, 0);
  }
});
