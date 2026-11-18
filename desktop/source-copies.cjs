'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { randomUUID } = require('node:crypto');
const { HEADER } = require('./host-policy.cjs');
const CHANNEL = 'funds-trace:source-copy';
const MAX_BYTES = 300 * 1024 * 1024;

// Renderer supplies registered IDs, never paths. Only verified original bytes
// from the owned backend can become a read-only, explicitly exported copy.
function sourceCopies({ origin, token, shell, app, fetchSource = fetch, temporaryDirectory = os.tmpdir() }) {
  const tickets = new Map();
  const pending = new Set();
  let closed = false;
  const remove = async entry => { if (entry.directory && !entry.exposed) await fs.rm(entry.directory, { recursive: true, force: true }); };
  async function prepare(event, payload) {
    if (closed || !payload || typeof payload.caseId !== 'string' || typeof payload.sourceId !== 'string' || !/^[1-9]\d*$/.test(payload.caseId) || !/^[a-f0-9]{12,32}$/.test(payload.sourceId)) throw new Error('来源标识无效');
    for (const [ticket, existing] of tickets) if (existing.sender === event.sender && existing.frame === event.senderFrame && existing.url === event.senderFrame.url && existing.caseId === payload.caseId && existing.sourceId === payload.sourceId) return { ticket, icon: existing.icon };
    const entry = { exposed: false, sender: event.sender, frame: event.senderFrame, url: event.senderFrame.url, caseId: payload.caseId, sourceId: payload.sourceId, controller: new AbortController() };
    let finished; entry.finished = new Promise(resolve => { finished = resolve; }); pending.add(entry);
    try {
      entry.directory = await fs.mkdtemp(path.join(temporaryDirectory, 'FundsTrace-source-'));
      await fs.chmod(entry.directory, 0o700);
      if (closed) throw new Error('文件操作已结束');
      const response = await fetchSource(`${origin}/api/v1/cases/${payload.caseId}/sources/${payload.sourceId}/file`, { headers: { [HEADER]: token }, redirect: 'error', signal: AbortSignal.any([entry.controller.signal, AbortSignal.timeout(180000)]) });
      if (!response.ok) throw new Error('留存原件不可用');
      const disposition = response.headers.get('content-disposition') || '';
      const filename = decodeURIComponent(/filename\*=utf-8''([^;]+)/i.exec(disposition)?.[1] || '');
      if (!filename || filename !== path.basename(filename) || /[\\/:*?"<>|\x00-\x1f]/.test(filename) || !/\.(csv|tsv|txt|xlsx?|pdf)$/i.test(filename)) throw new Error('此文件类型请使用另存原件');
      entry.filename = path.join(entry.directory, filename);
      const output = await fs.open(entry.filename, 'wx', 0o600);
      let total = 0;
      try {
        if (!response.body) throw new Error('留存原件不可用');
        for await (const chunk of response.body) {
          total += chunk.byteLength;
          if (closed || event.sender.isDestroyed() || event.senderFrame !== event.sender.mainFrame || event.senderFrame.url !== entry.url || total > MAX_BYTES) throw new Error('文件操作已结束或文件过大');
          await output.writeFile(chunk);
        }
      } finally { await output.close(); }
      await fs.chmod(entry.filename, 0o400);
      let icon;
      try { icon = (await app.getFileIcon(entry.filename, { size: 'small' })).toDataURL(); } catch { /* The command remains available without an OS icon. */ }
      if (closed || event.sender.isDestroyed()) throw new Error('文件操作已结束');
      entry.icon = icon;
      const ticket = randomUUID(); tickets.set(ticket, entry);
      return { ticket, icon };
    } catch { await remove(entry); throw new Error('无法读取原件副本，请使用另存原件或重试。'); }
    finally { pending.delete(entry); finished(); }
  }
  async function perform(event, payload) {
    const entry = tickets.get(payload?.ticket);
    if (closed || !entry || entry.sender !== event.sender || entry.frame !== event.senderFrame || entry.url !== event.senderFrame.url || !['open','reveal'].includes(payload.action)) throw new Error('文件操作已过期，请重新选择');
    // No await between the final ownership check and dispatch to the OS.
    entry.exposed = true;
    if (payload.action === 'reveal') shell.showItemInFolder(entry.filename);
    else {
      const error = await shell.openPath(entry.filename);
      if (error) throw new Error('默认应用未能打开副本，请使用另存原件。');
    }
  }
  async function release(event, ticket) {
    const entry = tickets.get(ticket);
    if (!entry || entry.sender !== event.sender || entry.frame !== event.senderFrame) return;
    tickets.delete(ticket); await remove(entry);
  }
  async function dispose() { closed = true; for (const entry of pending) entry.controller.abort(); await Promise.all([...pending].map(entry => entry.finished)); const entries = [...tickets.values()]; tickets.clear(); await Promise.all(entries.map(remove)); }
  return { prepare, perform, release, dispose };
}
module.exports = { CHANNEL, sourceCopies };
