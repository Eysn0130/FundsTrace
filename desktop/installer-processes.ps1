# FundsTrace process gate, adapted from Analytix's NSIS process-scope design.
# Copyright 2026 Guoqin He (GitHub: Eysn0130)
# Licensed under Apache-2.0; see licenses/Apache-2.0.txt. Modified 2026-10-15
# to request normal window closure and wait, without forced process termination.
# Returns 0 when the installation is idle, 2 when busy, and 3 on check failure.
[CmdletBinding()]
param(
    [string]$InstallDirectory = $env:FUNDS_TRACE_INSTALLER_APP_ROOT,
    [int]$InstallerProcessId = $env:FUNDS_TRACE_INSTALLER_SELF_PID,
    [ValidateRange(0, 60)][int]$TimeoutSeconds = 25,
    [switch]$RejectInstallerInInstallDirectory
)

$ErrorActionPreference = 'Stop'

try {
    if ([string]::IsNullOrWhiteSpace($InstallDirectory) -or
        -not [System.IO.Path]::IsPathRooted($InstallDirectory) -or
        $InstallerProcessId -le 0) {
        throw 'Invalid installer process scope.'
    }
    $root = [System.IO.Path]::GetFullPath($InstallDirectory).TrimEnd([char[]]@('\', '/'))
    if ($root -eq [System.IO.Path]::GetPathRoot($root).TrimEnd([char[]]@('\', '/'))) {
        throw 'A drive root is not an application installation.'
    }
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    $appPath = [System.IO.Path]::Combine($root, 'FundsTrace.exe')

    if ($RejectInstallerInInstallDirectory) {
        $installer = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $InstallerProcessId" -ErrorAction Stop
        if (-not $installer -or -not $installer.ExecutablePath) {
            throw 'The installer executable path could not be checked.'
        }
        $installerPath = [System.IO.Path]::GetFullPath($installer.ExecutablePath)
        if ($installerPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Output 'Move the installer EXE outside the installation directory, then run it again.'
            exit 2
        }
    }

    function Get-InstalledProcesses {
        @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop | Where-Object {
            if ($_.ProcessId -eq $InstallerProcessId -or -not $_.ExecutablePath) { return $false }
            $executable = [System.IO.Path]::GetFullPath($_.ExecutablePath)
            $executable.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
        })
    }

    $deadline = [System.DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $running = @(Get-InstalledProcesses)
        if ($running.Count -eq 0) { exit 0 }

        # Only the exact installed Electron executable can receive WM_CLOSE.
        # Repeating CloseMainWindow also lets multiple FundsTrace windows close.
        # Main's existing before-quit handler then sends EOF to its owned backend.
        foreach ($entry in $running) {
            if ([System.String]::Equals($entry.ExecutablePath, $appPath, [System.StringComparison]::OrdinalIgnoreCase)) {
                try {
                    $application = Get-Process -Id $entry.ProcessId -ErrorAction Stop
                    # Recheck the held process path before requesting normal close.
                    if ([System.String]::Equals($application.Path, $appPath, [System.StringComparison]::OrdinalIgnoreCase)) {
                        $null = $application.CloseMainWindow()
                    }
                } catch {
                    # A process can exit between enumeration and window lookup.
                    # An inaccessible window remains busy; never force it closed.
                }
            }
        }
        if ([System.DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Milliseconds 250
    } while ($true)

    # Recheck after the final close request before deciding that files are busy.
    if (@(Get-InstalledProcesses).Count -eq 0) { exit 0 }
    Write-Output 'FundsTrace is still running. Close the application and retry.'
    exit 2
} catch {
    Write-Output 'FundsTrace process state could not be checked. Close the application and retry.'
    exit 3
}
