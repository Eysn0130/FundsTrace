import OpenApi from '@alicloud/openapi-client';
import Util from '@alicloud/tea-util';
import { prefix, validateCurrent } from './publish.mjs';

export function cdnAdapter(credentials) {
  const { accessKeyId, accessKeySecret, securityToken } = credentials;
  if (!accessKeyId || !accessKeySecret || !securityToken) throw new Error('Temporary publisher credentials required');
  const client = new OpenApi.default(new OpenApi.Config({ ...credentials, endpoint: 'cdn.aliyuncs.com' }));
  return async url => {
    const result = await client.callApi(new OpenApi.Params({ action: 'PushObjectCache', version: '2018-05-10',
      protocol: 'HTTPS', pathname: '/', method: 'POST', authType: 'AK', style: 'RPC', reqBodyType: 'formData', bodyType: 'json' }),
    new OpenApi.OpenApiRequest({ body: { ObjectPath: url, Area: 'domestic', L2Preload: true,
      WithHeader: JSON.stringify({ 'Accept-Encoding': [' '] }) } }),
    new Util.RuntimeOptions({ readTimeout: 30000, connectTimeout: 10000, autoretry: false }));
    return result.body?.PushTaskId;
  };
}

// Repeated release-job retries reuse a durable receipt rather than repeatedly
// loading every edge from the origin. Submission is asynchronous, not proof
// that every CDN node has finished warming.
export async function prewarm({ client, manifest, submit, now = new Date() }) {
  validateCurrent(manifest);
  const url = manifest.files[0].url;
  const key = `${prefix}/${manifest.version}/cdn-prewarm.json`;
  let previous;
  try { previous = await client.get(key); }
  catch (error) { if (error.code !== 'NoSuchKey') throw error; }
  const valid = value => value?.schemaVersion === 1 && value.version === manifest.version && value.url === url
    && value.checksumSha256 === manifest.checksumSha256 && /^[0-9,]{1,200}$/.test(value.taskId)
    && Number.isFinite(Date.parse(value.submittedAt));
  if (previous) {
    const receipt = JSON.parse(previous.content.toString('utf8'));
    if (!valid(receipt)) throw new Error('Prewarm receipt differs from the immutable installer');
    return receipt;
  }
  const taskId = await submit(url);
  const receipt = { schemaVersion: 1, version: manifest.version, url, checksumSha256: manifest.checksumSha256,
    taskId, submittedAt: now.toISOString() };
  if (!valid(receipt)) throw new Error('CDN did not return a valid prewarm task');
  const bytes = Buffer.from(JSON.stringify(receipt, null, 2) + '\n');
  await client.put(key, bytes, { headers: { 'Content-Type': 'application/json',
    'Cache-Control': 'no-store', 'x-oss-forbid-overwrite': 'true' } });
  const stored = await client.get(key);
  if (!stored.content.equals(bytes)) throw new Error('Prewarm receipt could not be verified');
  return receipt;
}
