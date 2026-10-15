'use strict';

const { spawn } = require('node:child_process');
const http = require('node:http');
const { randomBytes } = require('node:crypto');
const { HEADER } = require('./host-policy.cjs');

function verifyHealth(port, token, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const req = http.get({ hostname: '127.0.0.1', port, path: '/__desktop/health', headers: { [HEADER]: token }, timeout }, response => {
      let body = '';
      response.on('data', chunk => { body += chunk; if (body.length > 4096) req.destroy(new Error('Invalid backend health')); });
      response.on('end', () => {
        try {
          const value = JSON.parse(body);
          if (response.statusCode !== 200 || value.protocol !== 1 || value.service !== 'FundsTrace' || value.ready !== true) throw new Error('Invalid backend health');
          resolve();
        } catch { reject(new Error('Backend identity check failed')); }
      });
    });
    req.on('timeout', () => req.destroy(new Error('Backend health timed out')));
    req.on('error', () => reject(new Error('Backend health unavailable')));
  });
}

function startBackend(config, { spawnProcess = spawn, health = verifyHealth, readyTimeout = 65000, stopTimeout = 12000, onExit = () => {} } = {}) {
  const token = randomBytes(32).toString('hex');
  const child = spawnProcess(config.command, config.args, { cwd: config.cwd, env: config.env, shell: false, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  let stopped = false, exited = false, stopPromise, exitResolve;
  const didExit = new Promise(resolve => { exitResolve = resolve; });
  child.stderr.on('data', () => {}); // Drain without exposing process diagnostics or case data.
  child.stdin.on('error', () => {});
  child.on('exit', () => { exited = true; exitResolve(); if (!stopped) onExit(); });
  function stop() {
    if (stopPromise) return stopPromise;
    stopped = true;
    stopPromise = (async () => {
      if (exited) return;
      child.stdin.end();
      let timer;
      const timeout = new Promise(resolve => { timer = setTimeout(resolve, stopTimeout); });
      await Promise.race([didExit, timeout]); clearTimeout(timer);
      if (!exited) { child.kill();
        const forceTimer = setTimeout(() => { if (!exited) child.kill('SIGKILL'); }, 1000);
        let finalTimer;
        await Promise.race([didExit, new Promise(resolve => { finalTimer = setTimeout(resolve, 1500); })]); clearTimeout(forceTimer); clearTimeout(finalTimer);
      }
    })();
    return stopPromise;
  }
  const ready = new Promise((resolve, reject) => {
    let buffer = '', settled = false;
    const timer = setTimeout(() => fail('FundsTrace backend startup timed out'), readyTimeout);
    function fail(message) { if (settled) return; settled = true; clearTimeout(timer); void stop(); reject(new Error(message)); }
    child.on('error', () => fail('FundsTrace backend could not start'));
    child.on('exit', () => fail('FundsTrace backend stopped before readiness'));
    child.stdout.on('data', chunk => {
      if (settled) return;
      buffer += chunk.toString('utf8');
      if (buffer.length > 4096) { fail('FundsTrace backend returned invalid readiness'); return; }
      const newline = buffer.indexOf('\n');
      if (newline < 0) return;
      let value;
      try { value = JSON.parse(buffer.slice(0, newline)); } catch { fail('FundsTrace backend returned invalid readiness'); return; }
      if (buffer.slice(newline + 1).trim() || value.protocol !== 1 || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535 || Object.keys(value).some(key => !['protocol', 'port'].includes(key))) { fail('FundsTrace backend returned invalid readiness'); return; }
      // Only the child-held endpoint is checked; never discover or reuse a known port.
      health(value.port, token).then(() => {
        if (settled || stopped || exited) return;
        settled = true; clearTimeout(timer);
        resolve({ origin: `http://127.0.0.1:${value.port}`, token });
      }, () => fail('FundsTrace backend identity check failed'));
    });
    child.stdin.write(`${JSON.stringify({ protocol: 1, token, dataDir: config.dataDir, synthetic: config.synthetic })}\n`);
  });
  return { ready, stop };
}

module.exports = { startBackend, verifyHealth };
