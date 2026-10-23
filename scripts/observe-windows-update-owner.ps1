#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][int]$ProcessId
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

# Post-failure evidence only. No direct control/geometry messages, activation,
# input, renderer access or window text are used by this observer. The requested
# .NET Process.Responding read may use its passive WM_NULL responsiveness probe.
$Report = [ordered]@{
    schemaVersion = 1; processBound = $false; querySucceeded = $false
    mainWindowUnique = $null; mainWindowVisible = $null; mainWindowEnabled = $null
    processResponding = $null; ownedVisibleDialogCount = $null
}
$Process = $null

function Get-NormalizedAbsolutePath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Contains('"') -or
        -not [IO.Path]::IsPathFullyQualified($Value) -or $Value -match '[\x00-\x1f]') { throw 'INVALID_PATH' }
    $Result = [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath($Value))
    if ([IO.Path]::GetPathRoot($Result) -notmatch '^[A-Za-z]:\\$') { throw 'LOCAL_PATH_REQUIRED' }
    return $Result
}
function Test-WithinRoot([string]$Path, [string]$Root) {
    return $Path.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}
function Assert-ProcessBinding {
    $Process.Refresh()
    if ($Process.HasExited -or -not [FundsTraceUpdateOwner]::CanonicalPath($Process.MainModule.FileName, $false).Equals(
        $script:ExpectedExecutable, [StringComparison]::OrdinalIgnoreCase)) { throw 'PROCESS_BINDING_FAILED' }
}

try {
    if (-not $IsWindows -or $env:CI -cne 'true' -or $env:GITHUB_ACTIONS -cne 'true' -or
        [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') { throw 'CI_PLATFORM_REQUIRED' }
    if (-not $env:APPDATA) { throw 'SYNTHETIC_USER_REQUIRED' }
    $UserRoot = Get-NormalizedAbsolutePath (Join-Path $env:APPDATA 'FundsTrace')
    $Sentinel = Join-Path $UserRoot 'installer-lifecycle-sentinel.txt'
    if (-not [IO.File]::Exists($Sentinel) -or
        [IO.File]::ReadAllText($Sentinel) -cne "FundsTrace synthetic installer lifecycle v1`n") { throw 'SYNTHETIC_USER_REQUIRED' }
    $InstallRoot = Get-NormalizedAbsolutePath $InstallRoot
    $InstalledExe = Join-Path $InstallRoot 'FundsTrace.exe'
    if ($ProcessId -le 0 -or -not [IO.File]::Exists($InstalledExe)) { throw 'INVALID_CANDIDATE_INPUT' }

    Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class FundsTraceUpdateOwner {
    delegate bool EnumProc(IntPtr window, IntPtr state);
    [DllImport("user32.dll", SetLastError = true)] static extern bool EnumWindows(EnumProc callback, IntPtr state);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr window);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    static extern int GetClassNameW(IntPtr window, StringBuilder name, int capacity);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    static extern SafeFileHandle CreateFileW(string path, uint access, uint sharing, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder name, uint capacity, uint flags);

    public sealed class Observation {
        public bool QuerySucceeded, MainWindowUnique;
        public bool? MainWindowVisible, MainWindowEnabled;
        public int? OwnedVisibleDialogCount;
    }
    public static string CanonicalPath(string path, bool directory) {
        using (var file = CreateFileW(path, 0, 7, IntPtr.Zero, 3, directory ? 0x02000000u : 0u, IntPtr.Zero)) {
            if (file.IsInvalid) throw new InvalidOperationException("PATH_BINDING_FAILED");
            var name = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandleW(file, name, (uint)name.Capacity, 0);
            if (length == 0 || length >= name.Capacity) throw new InvalidOperationException("PATH_BINDING_FAILED");
            string result = name.ToString();
            if (result.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) result = @"\\" + result.Substring(8);
            else if (result.StartsWith(@"\\?\", StringComparison.Ordinal)) result = result.Substring(4);
            return System.IO.Path.TrimEndingDirectorySeparator(System.IO.Path.GetFullPath(result));
        }
    }
    static bool IsChromeRoot(string name) {
        const string prefix = "Chrome_WidgetWin_";
        if (!name.StartsWith(prefix, StringComparison.Ordinal) || name.Length == prefix.Length) return false;
        for (int i = prefix.Length; i < name.Length; i++) if (name[i] < '0' || name[i] > '9') return false;
        return true;
    }
    static string ClassName(IntPtr window) {
        var name = new StringBuilder(256);
        int length = GetClassNameW(window, name, name.Capacity);
        if (length == 0 || length >= name.Capacity - 1) throw new InvalidOperationException("CLASS_UNAVAILABLE");
        return name.ToString();
    }
    public static Observation Observe(uint processId) {
        var mainWindows = new List<IntPtr>();
        int enumerated = 0, visibleOwned = 0, dialogs = 0;
        bool failed = false, unknownOwnedClass = false;
        EnumProc callback = (window, state) => {
            try {
                if (++enumerated > 10000) throw new InvalidOperationException("ENUMERATION_LIMIT");
                uint actual;
                if (GetWindowThreadProcessId(window, out actual) == 0) throw new InvalidOperationException("OWNER_UNAVAILABLE");
                if (actual != processId || !IsWindowVisible(window)) return true;
                if (++visibleOwned > 64) throw new InvalidOperationException("OWNED_WINDOW_LIMIT");
                string name = ClassName(window);
                if (IsChromeRoot(name)) mainWindows.Add(window);
                else if (name == "#32770") dialogs++;
                else unknownOwnedClass = true;
                return true;
            } catch { failed = true; return false; }
        };
        if (!EnumWindows(callback, IntPtr.Zero) || failed) throw new InvalidOperationException("ENUMERATION_UNAVAILABLE");
        var result = new Observation();
        result.MainWindowUnique = mainWindows.Count == 1 && !unknownOwnedClass;
        // An ambiguous or absent main window is unavailable, never a zero-dialog
        // observation. Additional native dialog windows are allowed and counted.
        if (!result.MainWindowUnique) return result;
        IntPtr main = mainWindows[0];
        uint owner;
        if (GetWindowThreadProcessId(main, out owner) == 0 || owner != processId ||
            !IsChromeRoot(ClassName(main)) || !IsWindowVisible(main)) throw new InvalidOperationException("MAIN_BINDING_CHANGED");
        result.MainWindowVisible = true;
        result.MainWindowEnabled = IsWindowEnabled(main);
        result.OwnedVisibleDialogCount = dialogs;
        result.QuerySucceeded = true;
        return result;
    }
}
'@
    $CanonicalTemp = [FundsTraceUpdateOwner]::CanonicalPath((Get-NormalizedAbsolutePath ([IO.Path]::GetTempPath())), $true)
    $CanonicalInstall = [FundsTraceUpdateOwner]::CanonicalPath($InstallRoot, $true)
    $script:ExpectedExecutable = [FundsTraceUpdateOwner]::CanonicalPath($InstalledExe, $false)
    if (-not (Test-WithinRoot $CanonicalInstall $CanonicalTemp) -or
        -not [IO.Path]::GetDirectoryName($script:ExpectedExecutable).Equals($CanonicalInstall, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'TEMPORARY_INSTALL_REQUIRED'
    }
    $Process = [Diagnostics.Process]::GetProcessById($ProcessId)
    # Hold the actual process, so a reused numeric PID cannot become the target.
    $null = $Process.Handle
    Assert-ProcessBinding
    $Observation = [FundsTraceUpdateOwner]::Observe([uint32]$ProcessId)
    Assert-ProcessBinding
    $Responding = [bool]$Process.Responding
    Assert-ProcessBinding
    $Report.processBound = $true
    $Report.mainWindowUnique = [bool]$Observation.MainWindowUnique
    if ($Observation.QuerySucceeded) {
        $Report.mainWindowVisible = [bool]$Observation.MainWindowVisible
        $Report.mainWindowEnabled = [bool]$Observation.MainWindowEnabled
        $Report.processResponding = $Responding
        $Report.ownedVisibleDialogCount = [int]$Observation.OwnedVisibleDialogCount
        $Report.querySucceeded = $true
    }
} catch {
    # Exception text, process IDs, handles, paths and window contents never leave
    # this process. An interrupted observation must not look like a zero count.
    $Report.querySucceeded = $false
    $Report.mainWindowUnique = $null
    $Report.mainWindowVisible = $null
    $Report.mainWindowEnabled = $null
    $Report.processResponding = $null
    $Report.ownedVisibleDialogCount = $null
} finally {
    if ($null -ne $Process) { try { $Process.Dispose() } catch { } }
}
$Report | ConvertTo-Json -Compress
