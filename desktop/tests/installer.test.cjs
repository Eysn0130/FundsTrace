'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn, spawnSync } = require('node:child_process');
const { once } = require('node:events');

const desktop = path.resolve(__dirname, '..');
const installer = fs.readFileSync(path.join(desktop, 'installer.nsh'), 'utf8').replace(/\r\n/g, '\n');
const helper = path.join(desktop, 'installer-processes.ps1');
const processes = fs.readFileSync(helper, 'utf8');
const templates = path.join(desktop, 'node_modules/app-builder-lib/templates/nsis');

test('installer keeps FundsTrace identity, per-user scope, case data, and license distribution', () => {
  const builder = require('../electron-builder.cjs');
  assert.equal(builder.appId, 'com.eysn.fundstrace');
  assert.equal(builder.productName, 'FundsTrace');
  assert.equal(builder.nsis.guid, undefined, 'retain the GUID derived from the existing appId');
  assert.equal(builder.nsis.oneClick, false);
  assert.equal(builder.nsis.perMachine, false);
  assert.equal(builder.nsis.allowElevation, false);
  assert.equal(builder.nsis.deleteAppDataOnUninstall, false);
  assert.equal(builder.nsis.include, path.join(desktop, 'installer.nsh'));
  assert.deepEqual(builder.extraResources.find(item => item.to === 'licenses/Analytix-Apache-2.0.txt'),
    { from: path.join(desktop, 'licenses/Apache-2.0.txt'), to: 'licenses/Analytix-Apache-2.0.txt' });
  assert.match(installer, /ReadRegStr \$FundsTraceInstalledVersion HKCU "\$\{UNINSTALL_REGISTRY_KEY\}" DisplayVersion/);
  assert.match(installer, /!macro customInstallMode\s+StrCpy \$isForceCurrentInstall "1"/);
  for (const hook of ['customInit', 'customUnInit']) {
    const body = installer.split(`!macro ${hook}\n`)[1].split('!macroend')[0];
    assert.match(body, /\$\{isForAllUsers\}[\s\S]*SetErrorLevel 2[\s\S]*Quit/);
    assert.match(body, /!insertmacro setInstallModePerUser/);
  }
});

test('installed electron-builder hooks gate replacement and both uninstall entry paths', () => {
  // Verify the pinned template contract: a hook name alone cannot prove wiring.
  assert.equal(require('app-builder-lib/package.json').version, '26.15.3');
  const check = fs.readFileSync(path.join(templates, 'include/allowOnlyOneInstallerInstance.nsh'), 'utf8');
  assert.match(check, /!ifmacrodef customCheckAppRunning\s+!insertmacro customCheckAppRunning\s+!else/);
  const install = fs.readFileSync(path.join(templates, 'installSection.nsh'), 'utf8');
  assert.ok(install.indexOf('!insertmacro CHECK_APP_RUNNING') < install.indexOf('!insertmacro uninstallOldVersion'));
  const uninstall = fs.readFileSync(path.join(templates, 'uninstaller.nsh'), 'utf8');
  assert.match(uninstall, /Function un\.checkAppRunning\s+!insertmacro CHECK_APP_RUNNING/);
  assert.match(uninstall, /\$\{If\} \$\{Silent\}\s+call un\.checkAppRunning/);
  assert.match(uninstall, /Section "un\.\$\{UNINSTALL_SECTION_NAME\}"[\s\S]*call un\.checkAppRunning[\s\S]*RMDir \/r \$INSTDIR/);
  assert.match(installer, /ReadRegStr \$0 HKCU "\$\{INSTALL_REGISTRY_KEY\}" InstallLocation/,
    'check the old location before invoking even a historical uninstaller');
  assert.match(installer, /IfFileExists "\$WINDIR\\Sysnative\\WindowsPowerShell\\v1\.0\\powershell\.exe"/,
    'a 32-bit NSIS host must use native PowerShell to inspect the x64 Electron path');
  assert.match(installer, /\$\{if\} \$\{Silent\}[\s\S]*SetErrorLevel 2\s+Quit/);
  assert.match(installer, /MessageBox MB_RETRYCANCEL[^\n]+IDRETRY FundsTraceCheckInstallDirectory/);
});

test('process gate requires a directory boundary and requests normal closure without name killing', () => {
  assert.match(processes, /\$prefix = \$root \+ \[System\.IO\.Path\]::DirectorySeparatorChar/);
  assert.match(processes, /\.StartsWith\(\$prefix, \[System\.StringComparison\]::OrdinalIgnoreCase\)/);
  assert.match(processes, /\$application\.CloseMainWindow\(\)/);
  assert.match(processes, /\$application\.Path, \$appPath/);
  assert.match(processes, /ProcessId -eq \$InstallerProcessId/);
  assert.doesNotMatch(installer + processes, /Stop-Process|taskkill|nsProcess::_KillProcess|\.Kill\(/i);
  assert.doesNotMatch(processes, /Get-Process\s+-Name/i);
});

const powershell = process.platform === 'win32'
  ? path.join(process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe')
  : null;

function checkDirectory(directory, excludedPid = process.pid, rejectInstallerInInstallDirectory = false) {
  return spawnSync(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
    '-File', helper, '-TimeoutSeconds', '0', ...(rejectInstallerInInstallDirectory ? ['-RejectInstallerInInstallDirectory'] : [])], {
    env: { ...process.env, FUNDS_TRACE_INSTALLER_APP_ROOT: directory, FUNDS_TRACE_INSTALLER_SELF_PID: String(excludedPid) },
    encoding: 'utf8', timeout: 15000, windowsHide: true
  });
}

test('Windows process gate treats an unreadable possible FundsTrace path as check failure without guessing ownership', { skip: !powershell }, t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'FundsTrace unreadable gate '));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  // Replace only WMI enumeration. Run the actual installer helper, including
  // its failure exit code, with fixed synthetic process records and no real PID.
  function inspect(records) {
    const command = `function Get-CimInstance { [CmdletBinding()] param([string]$ClassName) ConvertFrom-Json $env:FUNDS_TRACE_TEST_PROCESSES }; & $env:FUNDS_TRACE_TEST_HELPER -InstallDirectory $env:FUNDS_TRACE_TEST_ROOT -InstallerProcessId 123 -TimeoutSeconds 0; exit $LASTEXITCODE`;
    return spawnSync(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command], {
      env: { ...process.env, FUNDS_TRACE_TEST_HELPER: helper, FUNDS_TRACE_TEST_ROOT: directory, FUNDS_TRACE_TEST_PROCESSES: JSON.stringify(records) },
      encoding: 'utf8', timeout: 15000, windowsHide: true,
    });
  }
  for (const name of ['FundsTrace.exe', 'funds-trace-backend.exe', 'FUNDSTRACE.EXE']) {
    const result = inspect([{ ProcessId: 456, Name: name, ExecutablePath: null }]);
    assert.equal(result.status, 3, result.stderr || result.stdout);
    assert.match(result.stdout, /process state could not be checked/);
  }
  for (const records of [
    [{ ProcessId: 456, Name: 'unrelated.exe', ExecutablePath: null }],
    [{ ProcessId: 123, Name: 'FundsTrace.exe', ExecutablePath: null }],
    [{ ProcessId: 456, Name: 'FundsTrace.exe', ExecutablePath: path.join(`${directory}-other`, 'FundsTrace.exe') }],
  ]) {
    const result = inspect(records);
    assert.equal(result.status, 0, result.stderr || result.stdout);
  }
});

test('Windows process gate rejects unsafe roots and allows an idle Unicode/space directory', { skip: !powershell }, t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'FundsTrace 安装 gate '));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const root of ['', 'relative', path.parse(directory).root]) {
    const result = checkDirectory(root);
    assert.equal(result.status, 3, result.stderr || result.stdout);
  }
  const result = checkDirectory(directory);
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test('Windows installer gate rejects an installer executable inside the installation while the uninstaller keeps its PID exemption', { skip: !powershell }, async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'FundsTrace installer location '));
  const executable = path.join(directory, 'setup.exe');
  fs.copyFileSync(process.execPath, executable);
  const child = spawn(executable, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore', windowsHide: true });
  t.after(async () => {
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit'); child.kill(); await exited;
    }
    fs.rmSync(directory, { recursive: true, force: true });
  });
  await once(child, 'spawn');
  const installerCheck = checkDirectory(directory, child.pid, true);
  assert.equal(installerCheck.status, 2, installerCheck.stderr || installerCheck.stdout);
  assert.match(installerCheck.stdout, /Move the installer EXE outside the installation directory/);
  assert.equal(child.exitCode, null, 'reject the location without terminating the installer');
  assert.equal(checkDirectory(directory, child.pid).status, 0, 'an in-place uninstaller remains exempt by its own PID');
});

test('Windows process gate scopes exact directories, waits for owned executables, and never terminates them', { skip: !powershell }, async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'FundsTrace 进程 gate '));
  const root = path.join(directory, 'FundsTrace');
  const adjacent = `${root}-unrelated`;
  fs.mkdirSync(root); fs.mkdirSync(adjacent);
  const children = [];
  t.after(async () => {
    for (const child of children) if (child.pid && child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit'); child.kill(); await exited;
    }
    fs.rmSync(directory, { recursive: true, force: true });
  });
  async function startIn(folder, filename) {
    const executable = path.join(folder, filename);
    fs.copyFileSync(process.execPath, executable);
    const child = spawn(executable, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore', windowsHide: true });
    children.push(child);
    await once(child, 'spawn');
    return child;
  }
  const unrelated = await startIn(adjacent, 'FundsTrace.exe');
  assert.equal(checkDirectory(root).status, 0, 'an adjacent prefix is outside the installation');
  assert.equal(unrelated.exitCode, null);

  for (const filename of ['FundsTrace.exe', 'funds-trace-backend.exe']) {
    const child = await startIn(root, filename);
    const busy = checkDirectory(root);
    assert.equal(busy.status, 2, busy.stderr || busy.stdout);
    assert.equal(child.exitCode, null, 'a process without a closeable window must stay alive');
    assert.equal(checkDirectory(root, child.pid).status, 0, 'exclude only the installer PID');
    const exited = once(child, 'exit'); child.kill(); await exited;
  }
  assert.equal(checkDirectory(root).status, 0);
  assert.equal(unrelated.exitCode, null);
});
