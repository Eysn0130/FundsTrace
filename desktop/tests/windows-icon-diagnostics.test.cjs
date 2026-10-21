'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const nativeCi = process.platform === 'win32' && process.env.GITHUB_ACTIONS === 'true';

test('Windows icon failure diagnostics retain only approved synthetic fields and reject unsafe reports', {
  skip: !nativeCi, timeout: 65000,
}, () => {
  const marker = 'SYNTHETIC_ICON_DIAGNOSTIC_PRIVATE_MARKER';
  const syntheticPath = `C:\\${marker}\\not-a-real-user\\fixture.txt`;
  const frame = {
    Source: 'desktopShortcut/shell-large', Width: 32, Height: 32, Matched: false,
    ActualSha256: 'a'.repeat(64), ExpectedSha256: 'b'.repeat(64),
  };
  const payload = {
    schemaVersion: 1, status: 'failed', failedCheck: 'desktopShortcut', mode: 'shortcuts',
    failureCode: 'SHORTCUT_ICON_PIXEL_MISMATCH',
    checks: { inputBinding: 'passed', nativeIconReader: 'passed', desktopShortcut: 'failed', arbitrary: syntheticPath },
    iconComparisons: [{ ...frame, path: syntheticPath, extra: { rawError: marker } }],
    path: syntheticPath, extra: { rawStdout: marker, rawStderr: marker },
  };
  const pathBindings = {
    desktopShortcut: { targetTextMatch: false, targetFileMatch: true, iconLocationTextMatch: false, iconLocationFileMatch: true },
    startMenuShortcut: { targetTextMatch: true, targetFileMatch: true, iconLocationTextMatch: true, iconLocationFileMatch: true },
  };
  const rejectedBinding = {
    targetTextMatch: false, targetFileMatch: false, iconLocationTextMatch: false, iconLocationFileMatch: false,
  };
  const cases = [
    JSON.stringify(payload),
    JSON.stringify({ ...payload, failureCode: marker }),
    JSON.stringify({ ...payload, schemaVersion: 99 }),
    '{"schemaVersion":',
    JSON.stringify({ ...payload, padding: 'S'.repeat(65536) }),
    JSON.stringify({ ...payload, failedCheck: syntheticPath }),
    JSON.stringify({ ...payload, iconComparisons: [
      { ...frame, Source: syntheticPath }, { ...frame, Width: 1025 },
      { ...frame, ActualSha256: syntheticPath }, { ...frame, Matched: 'false' },
    ] }),
    JSON.stringify({ ...payload, pathBindings: {
      desktopShortcut: { ...pathBindings.desktopShortcut, rawTarget: syntheticPath },
      startMenuShortcut: { ...pathBindings.startMenuShortcut, extra: { rawIcon: syntheticPath } },
      arbitrarySource: { targetFileMatch: true, rawTarget: syntheticPath },
    } }),
    JSON.stringify({ ...payload, pathBindings: {
      desktopShortcut: { targetTextMatch: syntheticPath, targetFileMatch: true },
      startMenuShortcut: { iconLocationFileMatch: 'false', targetTextMatch: false },
    } }),
    JSON.stringify({ ...payload, pathBindings: {
      desktopShortcut: syntheticPath, startMenuShortcut: [true, false], arbitrarySource: { targetFileMatch: true },
    } }),
    JSON.stringify({ ...payload, pathBindings: [{ desktopShortcut: pathBindings.desktopShortcut }] }),
    JSON.stringify({ ...payload, pathBindings: { desktopShortcut: rejectedBinding } }),
  ];
  const command = String.raw`
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
try {
    $Tokens = $null; $Errors = $null
    $Ast = [System.Management.Automation.Language.Parser]::ParseFile($env:FUNDS_TRACE_TEST_ICON_INSTALLER, [ref]$Tokens, [ref]$Errors)
    if ($Errors.Count -ne 0) { throw 'SYNTHETIC_PARSE_FAILED' }
    $Definitions = @($Ast.FindAll({ param($Node)
        $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -ceq 'Get-SafeIconFailure'
    }, $false))
    if ($Definitions.Count -ne 1) { throw 'SYNTHETIC_FUNCTION_NOT_UNIQUE' }
    # Define only the actual pure projection function. Never execute the installer script.
    . ([ScriptBlock]::Create($Definitions[0].Extent.Text))
    $Cases = ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()) -AsHashtable -Depth 12
    $Results = @(foreach ($CaseText in $Cases) { Get-SafeIconFailure -Text ([string]$CaseText) })
    [Console]::Out.Write(($Results | ConvertTo-Json -Depth 12 -Compress))
} catch {
    [Console]::Error.WriteLine('Synthetic icon diagnostic projection failed.')
    exit 1
}
`;
  const result = spawnSync('pwsh.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', command], {
    env: { ...process.env, FUNDS_TRACE_TEST_ICON_INSTALLER: path.resolve(__dirname, '../../scripts/test-windows-installer.ps1') },
    input: JSON.stringify(cases), encoding: 'utf8', timeout: 60000, windowsHide: true, maxBuffer: 1024 * 1024,
  });
  assert.equal(Boolean(result.error), false, 'PowerShell projection did not complete; raw process diagnostics withheld');
  assert.equal(result.status, 0, 'PowerShell projection failed; raw stdout/stderr withheld');
  let results;
  try { results = JSON.parse(result.stdout); }
  catch { assert.fail('PowerShell projection did not return safe JSON'); }
  assert.equal(JSON.stringify(results).includes(marker), false, 'Synthetic unapproved fields must never survive projection');

  const safe = {
    kind: 'brand-icon', schemaVersion: 1, status: 'failed', failedCheck: 'desktopShortcut',
    failureCode: 'SHORTCUT_ICON_PIXEL_MISMATCH',
    checks: { inputBinding: 'passed', nativeIconReader: 'passed', desktopShortcut: 'failed' }, iconComparisons: [frame],
  };
  const unavailable = { kind: 'brand-icon', status: 'diagnostic-unavailable' };
  assert.deepEqual(results, [
    safe, { ...safe, failureCode: 'UNCLASSIFIED' }, unavailable, unavailable, unavailable, unavailable,
    { ...safe, iconComparisons: [] }, { ...safe, pathBindings }, safe, safe, safe,
    { ...safe, pathBindings: { desktopShortcut: rejectedBinding } },
  ]);
});
