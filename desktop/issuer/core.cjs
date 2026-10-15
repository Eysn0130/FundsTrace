'use strict';
const crypto = require('node:crypto');
const fs = require('node:fs/promises');

const DOMAIN = Buffer.from('FundsTrace-license-v2\0', 'utf8');
const LIMIT = 16 * 1024;
const MESSAGES = {
  machine: '请输入完整的机器码，例如 ABCD-1234-5678-90EF。',
  expiry: '请选择今天或之后的真实日期，或选择永久授权。',
  key: '私钥无法加载。请检查文件格式、口令与 Ed25519 密钥类型。',
  password: '新私钥口令至少需要 12 个字符。',
  missing: '请先创建或加载签发私钥。',
  save: '文件未保存。请选择不同的新文件名，并确认目录可写。',
};
class IssuerError extends Error { constructor(code) { super(MESSAGES[code]); this.code = code; } }
function machineCode(value) {
  const machine = typeof value === 'string' ? value.trim().toUpperCase() : '';
  if (!/^([0-9A-F]{4}-){3}[0-9A-F]{4}$/.test(machine)) throw new IssuerError('machine');
  return machine;
}
function expiryCode(value, today = new Date()) {
  if (value === '0') return value;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new IssuerError('expiry');
  const [y, m, d] = value.split('-').map(Number), date = new Date(0);
  date.setFullYear(y, m - 1, d); date.setHours(0, 0, 0, 0);
  const localToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  if (y < 1 || date.getFullYear() !== y || date.getMonth() !== m - 1 || date.getDate() !== d || date < localToday) throw new IssuerError('expiry');
  return value.replaceAll('-', '');
}
function payloadBytes(machine, expiry, today) {
  const hash = crypto.createHash('sha256').update(machineCode(machine), 'utf8').digest('hex').slice(0, 16).toUpperCase();
  return Buffer.from(JSON.stringify({ v: 2, p: 'FundsTrace', m: hash, e: expiryCode(expiry, today) }), 'utf8');
}
function issueCode(privateKey, { machineCode: machine, expires }, today = new Date()) {
  if (!privateKey || privateKey.type !== 'private' || privateKey.asymmetricKeyType !== 'ed25519') throw new IssuerError('key');
  const payload = payloadBytes(machine, expires, today);
  const signature = crypto.sign(null, Buffer.concat([DOMAIN, payload]), privateKey);
  return `FT2.${payload.toString('base64url')}.${signature.toString('base64url')}`;
}
async function saveNewFiles(entries) {
  const created = [];
  try {
    if (new Set(entries.map(([file]) => file)).size !== entries.length) throw new Error('Duplicate destinations');
    for (const [file, contents] of entries) {
      const handle = await fs.open(file, 'wx', 0o600); created.push(file);
      try { await handle.writeFile(contents); } finally { await handle.close(); }
    }
  } catch {
    await Promise.all(created.map(file => fs.unlink(file).catch(() => {})));
    throw new IssuerError('save');
  }
}
function createIssuer({ today = () => new Date() } = {}) {
  let privateKey = null, lastCode = '';
  function state() {
    if (!privateKey) return { loaded: false, fingerprint: '', publicKey: '' };
    const publicKey = crypto.createPublicKey(privateKey);
    return { loaded: true, fingerprint: crypto.createHash('sha256').update(publicKey.export({ type: 'spki', format: 'der' })).digest('hex').slice(0, 16).toUpperCase(), publicKey: publicKey.export({ type: 'spki', format: 'pem' }) };
  }
  function requireKey() { if (!privateKey) throw new IssuerError('missing'); }
  return {
    state,
    clear() { privateKey = null; lastCode = ''; return state(); },
    async create(passphrase, privatePath, publicPath) {
      if (typeof passphrase !== 'string' || [...passphrase].length < 12) throw new IssuerError('password');
      const pair = crypto.generateKeyPairSync('ed25519');
      const encrypted = Buffer.from(pair.privateKey.export({ type: 'pkcs8', format: 'pem', cipher: 'aes-256-cbc', passphrase }));
      try { await saveNewFiles([[privatePath, encrypted], [publicPath, pair.publicKey.export({ type: 'spki', format: 'pem' })]]); }
      finally { encrypted.fill(0); }
      privateKey = pair.privateKey; lastCode = ''; return state();
    },
    async load(file, passphrase = '') {
      let bytes;
      try {
        if (typeof passphrase !== 'string' || passphrase.length > 4096) throw new Error('Invalid passphrase');
        const handle = await fs.open(file, 'r');
        try {
          const stat = await handle.stat();
          if (!stat.isFile() || stat.size > LIMIT) throw new Error('Invalid file');
          bytes = Buffer.alloc(LIMIT + 1);
          const { bytesRead } = await handle.read(bytes, 0, bytes.length, 0);
          if (bytesRead > LIMIT) throw new Error('Invalid file');
          bytes = bytes.subarray(0, bytesRead);
        } finally { await handle.close(); }
        if (!/^-----BEGIN (?:ENCRYPTED )?PRIVATE KEY-----/.test(bytes.toString('ascii'))) throw new Error('PKCS8 required');
        const candidate = crypto.createPrivateKey({ key: bytes, format: 'pem', passphrase });
        if (candidate.asymmetricKeyType !== 'ed25519') throw new Error('Ed25519 required');
        privateKey = candidate; lastCode = ''; return state();
      } catch { throw new IssuerError('key'); }
      finally { bytes?.fill(0); }
    },
    async exportPublic(file) { requireKey(); await saveNewFiles([[file, state().publicKey]]); return state(); },
    issue(machine, expiry) {
      requireKey(); lastCode = issueCode(privateKey, { machineCode: machine, expires: expiry }, today());
      return { code: lastCode, ...state() };
    },
    copyValue(kind) {
      if (kind === 'public') { requireKey(); return state().publicKey; }
      if (kind === 'code' && lastCode) return lastCode;
      throw new IssuerError('missing');
    },
  };
}
module.exports = { createIssuer, machineCode, expiryCode, payloadBytes, issueCode, saveNewFiles, IssuerError, DOMAIN };
