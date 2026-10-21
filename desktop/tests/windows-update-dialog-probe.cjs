'use strict';
// Isolate native confirmation automation from packaging, updates and case data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(check, ms = 10000) {
  const end = Date.now() + ms;
  do { if (await check()) return; await delay(100); } while (Date.now() < end);
  throw new Error('DIALOG_PROBE_TIMEOUT');
}
async function main() {
  assert.equal(process.platform, 'win32'); assert.equal(process.env.GITHUB_ACTIONS, 'true');
  assert.equal(process.env.CI, 'true');
  const reportPath = process.argv[2]; assert.ok(reportPath && path.isAbsolute(reportPath));
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'FundsTrace native dialog '));
  const report = { status: 'running', checks: {}, failedCheck: null,
    limits: { nativeConfirmationOnly: true, installedUpdate: false } };
  let application, finished = false, stage = 'electronStarted';
  try {
    await fs.mkdir(path.join(root, 'profile'));
    const source = path.join(root, 'probe.cjs');
    await fs.writeFile(source, `const { app, BrowserWindow, dialog } = require('electron');
const fs = require('node:fs');
app.setPath('userData', ${JSON.stringify(path.join(root, 'profile'))});
app.whenReady().then(async () => {
  const window = new BrowserWindow({ width: 640, height: 480, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  await window.loadURL('data:text/html,<title>FundsTrace native dialog fixture</title><p>Disposable native confirmation probe</p>');
  for (let index=0; index<2; index++) {
    const result = await dialog.showMessageBox(window, { type: 'question', title: '重启更新 FundsTrace',
      message: '更新已下载并通过完整性校验。现在重启更新？',
      detail: 'Disposable CI dialog probe. No update will be installed.',
      buttons: ['稍后','重启更新'], defaultId: 0, cancelId: 0, noLink: true });
    fs.writeFileSync(${JSON.stringify(root)} + '/response-' + index + '.json', JSON.stringify({ response: result.response }));
  }
  app.quit();
}).catch(() => app.exit(1));
`, { flag: 'wx' });
    application = spawn(require('electron'), [source], { stdio: ['ignore','pipe','pipe'], windowsHide: false });
    application.stdout.resume(); application.stderr.resume();
    const exited = new Promise(resolve => {
      application.once('exit', (code, signal) => { finished = true; resolve({ code, signal }); });
      application.once('error', () => { finished = true; resolve({ code: null, signal: null }); });
    });
    assert.ok(application.pid);
    for (const [index, choice] of ['later','restart'].entries()) {
      stage = `${choice}Helper`;
      const helper = spawn('pwsh.exe', ['-NoLogo','-NoProfile','-NonInteractive','-File',
        path.resolve(__dirname, '../../scripts/click-windows-update-dialog.ps1'),
        '-ApplicationPid', String(application.pid), '-Choice', choice], { stdio: ['ignore','pipe','pipe'], windowsHide: true });
      helper.stdout.resume(); helper.stderr.resume();
      const code = await new Promise((resolve, reject) => {
        const timer = setTimeout(() => { helper.kill(); reject(new Error('DIALOG_HELPER_TIMEOUT')); }, 25000);
        helper.once('error', () => { clearTimeout(timer); reject(new Error('DIALOG_HELPER_START')); });
        helper.once('exit', value => { clearTimeout(timer); resolve(value); });
      });
      report.checks[`${choice}HelperReturned`] = code === 0;
      assert.equal(code, 0);
      stage = `${choice}ActualResponse`;
      const responseFile = path.join(root, `response-${index}.json`);
      await waitFor(async () => fs.access(responseFile).then(() => true, () => false));
      const response = JSON.parse(await fs.readFile(responseFile, 'utf8'));
      assert.equal(response.response, index); report.checks[`${choice}ActualResponse`] = true;
    }
    stage = 'normalClose';
    await waitFor(() => finished);
    assert.deepEqual(await exited, { code: 0, signal: null }); report.checks.normalClose = true;
    report.status = 'passed';
  } catch {
    report.status = 'failed'; report.failedCheck = stage; process.exitCode = 1;
  } finally {
    if (application && !finished) {
      application.kill(); await waitFor(() => finished).catch(() => { report.checks.cleanup = false; });
    }
    await fs.rm(root, { recursive: true, force: true });
    await fs.writeFile(reportPath, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
    process.stdout.write(JSON.stringify(report) + '\n');
  }
}
main().catch(() => { process.stderr.write('Native confirmation probe failed.\n'); process.exitCode = 1; });
