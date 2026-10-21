import { fork } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { stages, safeCodes } from './publish.mjs';

// Dependency timeout warnings can bypass our reporter and contain raw URLs/errors.
// Only fixed-schema IPC crosses the process boundary; never relay library stdout/stderr.
export function safeEvent(value) {
  if (!value || ![...stages, 'upload.transport', 'upload.verification'].includes(value.stage)
      || !['begin', 'done', 'failed'].includes(value.event)) return null;
  const result = { stage: value.stage, event: value.event };
  for (const key of ['elapsedMs', 'part', 'contentLength', 'requestSentMs', 'connectedMs', 'waitingMs',
    'socketBytesWritten', 'socketBytesRead', 'size'])
    if (Number.isSafeInteger(value[key]) && value[key] >= 0) result[key] = value[key];
  if (Number.isInteger(value.status) && value.status >= 100 && value.status <= 599) result.status = value.status;
  if ([...safeCodes, 'UNKNOWN', 'TIMEOUT', 'ABORTED'].includes(value.code)) result.code = value.code;
  if (['8MiB', '1MiB'].includes(value.profile)) result.profile = value.profile;
  if (['init', 'part', 'complete', 'read', 'other'].includes(value.operation)) result.operation = value.operation;
  for (const key of ['keepAlive', 'tls', 'tlsAuthorized', 'stream', 'expect'])
    if (typeof value[key] === 'boolean') result[key] = value[key];
  if (typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.sha256)) result.sha256 = value.sha256;
  if (typeof value.sha512 === 'string' && /^[A-Za-z0-9+/]{86}==$/.test(value.sha512)) result.sha512 = value.sha512;
  return result;
}

export function runPublisher(script = new URL('./publish.mjs', import.meta.url), report = console.log) {
  return new Promise(resolve => {
    const child = fork(script, [], { stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
      env: { ...process.env, DEBUG: '', NODE_DEBUG: '', NODE_DEBUG_NATIVE: '' } });
    child.on('message', value => {
      const event = safeEvent(value);
      if (event) report(JSON.stringify(event));
    });
    child.on('error', () => resolve(1));
    child.on('exit', code => resolve(code === 0 ? 0 : 1));
  });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await runPublisher();
  if (process.exitCode) console.error('FundsTrace download publication/verification failed; inspect the safe stage diagnostics. Existing published files were retained.');
}
