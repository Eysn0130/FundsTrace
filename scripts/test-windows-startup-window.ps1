#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [ValidateSet('Inspect', 'Resize', 'Capture')][string]$Action = 'Inspect',
    [int]$WidthDip = 0,
    [int]$HeightDip = 0,
    [string]$ScreenshotPath = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

# This helper is only for the disposable installer's synthetic account. It never
# launches an app, changes a display setting, reads a title, or inspects a renderer.
$Report = [ordered]@{ schemaVersion = 1; status = 'failed'; failedCheck = 'ciPlatform'; processBound = $false }
$Stage = 'ciPlatform'
$ExitCode = 1
$Process = $null
$PreviousDpi = [IntPtr]::Zero
$DpiScopeEntered = $false

function Get-NormalizedAbsolutePath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Contains('"') -or
        -not [IO.Path]::IsPathFullyQualified($Value)) { throw 'INVALID_PATH' }
    return [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath($Value))
}
function Test-WithinRoot([string]$Path, [string]$Root) {
    return $Path.Equals($Root, [StringComparison]::OrdinalIgnoreCase) -or
        $Path.StartsWith($Root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
}
function Convert-Rect($Rectangle) {
    return [ordered]@{ x = $Rectangle.X; y = $Rectangle.Y; width = $Rectangle.Width; height = $Rectangle.Height }
}
function Convert-Evidence($Evidence) {
    return [ordered]@{
        window = (Convert-Rect $Evidence.Window)
        client = (Convert-Rect $Evidence.Client)
        workArea = (Convert-Rect $Evidence.WorkArea)
        dpi = $Evidence.Dpi
        scaleFactor = $Evidence.Dpi / 96.0
        minTrack = [ordered]@{ width = $Evidence.MinTrackWidth; height = $Evidence.MinTrackHeight }
    }
}
function Assert-ProcessBinding {
    $Process.Refresh()
    if ($Process.HasExited -or -not [FundsTraceStartupWindow]::CanonicalPath($Process.MainModule.FileName, $false).Equals(
        $script:ExpectedExecutable, [StringComparison]::OrdinalIgnoreCase)) { throw 'PROCESS_BINDING_FAILED' }
}

try {
    if (-not $IsWindows -or $env:CI -cne 'true' -or $env:GITHUB_ACTIONS -cne 'true' -or
        [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') { throw 'CI_PLATFORM_REQUIRED' }
    $Stage = 'syntheticUserBinding'
    if (-not $env:APPDATA) { throw 'SYNTHETIC_USER_REQUIRED' }
    $UserRoot = Get-NormalizedAbsolutePath (Join-Path $env:APPDATA 'FundsTrace')
    $Sentinel = Join-Path $UserRoot 'installer-lifecycle-sentinel.txt'
    if (-not [IO.File]::Exists($Sentinel) -or
        [IO.File]::ReadAllText($Sentinel) -cne "FundsTrace synthetic installer lifecycle v1`n") { throw 'SYNTHETIC_USER_REQUIRED' }
    $Stage = 'inputBinding'
    $InstallRoot = Get-NormalizedAbsolutePath $InstallRoot
    $InstalledExe = Join-Path $InstallRoot 'FundsTrace.exe'
    if ($ProcessId -le 0 -or -not [IO.File]::Exists($InstalledExe)) { throw 'INVALID_CANDIDATE_INPUT' }
    if ($Action -eq 'Resize' -and ($WidthDip -lt 1 -or $HeightDip -lt 1 -or
        $WidthDip -gt 16384 -or $HeightDip -gt 16384)) { throw 'INVALID_RESIZE_INPUT' }

    $Stage = 'nativeWindowReader'
    Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class FundsTraceStartupWindow {
    static readonly IntPtr PerMonitorV2 = new IntPtr(-4);
    const uint WM_GETMINMAXINFO = 0x0024, SMTO_ABORTIFHUNG = 0x0002;
    [StructLayout(LayoutKind.Sequential)] struct NativeRect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct Point { public int X, Y; }
    [StructLayout(LayoutKind.Sequential)] struct MinMaxInfo {
        public Point Reserved, MaxSize, MaxPosition, MinTrackSize, MaxTrackSize;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct MonitorInfo {
        public uint Size;
        public NativeRect Monitor, Work;
        public uint Flags;
    }
    public sealed class Rectangle {
        public int X, Y, Width, Height;
    }
    public sealed class Evidence {
        public Rectangle Window, Client, WorkArea;
        public uint Dpi;
        public int MinTrackWidth, MinTrackHeight;
    }
    delegate bool EnumProc(IntPtr window, IntPtr state);
    [DllImport("user32.dll", SetLastError = true)] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
    [DllImport("user32.dll", SetLastError = true)] static extern bool EnumWindows(EnumProc callback, IntPtr state);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
    [DllImport("user32.dll", SetLastError = true)] static extern bool GetWindowRect(IntPtr window, out NativeRect rectangle);
    [DllImport("user32.dll", SetLastError = true)] static extern bool GetClientRect(IntPtr window, out NativeRect rectangle);
    [DllImport("user32.dll", SetLastError = true)] static extern bool ClientToScreen(IntPtr window, ref Point point);
    [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr window, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)] static extern bool GetMonitorInfoW(IntPtr monitor, ref MonitorInfo info);
    [DllImport("user32.dll", ExactSpelling = true, SetLastError = true)] static extern IntPtr SendMessageTimeoutW(
        IntPtr window, uint message, IntPtr wParam, IntPtr lParam, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", SetLastError = true)] static extern bool SetWindowPos(
        IntPtr window, IntPtr insertAfter, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll", SetLastError = true)] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr window, uint attribute, out int value, uint size);
    [DllImport("dwmapi.dll")] static extern int DwmFlush();
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)]
    static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder name, uint capacity, uint flags);

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
    public static IntPtr EnterPhysicalCoordinates() {
        IntPtr previous = SetThreadDpiAwarenessContext(PerMonitorV2);
        if (previous == IntPtr.Zero) throw new InvalidOperationException("DPI_CONTEXT_UNAVAILABLE");
        if (!AreDpiAwarenessContextsEqual(GetThreadDpiAwarenessContext(), PerMonitorV2)) {
            SetThreadDpiAwarenessContext(previous);
            throw new InvalidOperationException("DPI_CONTEXT_UNAVAILABLE");
        }
        return previous;
    }
    public static void RestoreDpiContext(IntPtr previous) {
        if (previous != IntPtr.Zero) SetThreadDpiAwarenessContext(previous);
    }
    static Rectangle Rect(NativeRect value) {
        long width = (long)value.Right - value.Left, height = (long)value.Bottom - value.Top;
        if (width < 1 || height < 1 || width > 32768 || height > 32768) throw new InvalidOperationException("BOUNDS_UNAVAILABLE");
        return new Rectangle { X = value.Left, Y = value.Top, Width = (int)width, Height = (int)height };
    }
    static void AssertOwner(IntPtr window, uint processId) {
        uint actual;
        if (window == IntPtr.Zero || GetWindowThreadProcessId(window, out actual) == 0 || actual != processId ||
            !IsWindowVisible(window) || IsIconic(window)) throw new InvalidOperationException("WINDOW_BINDING_FAILED");
    }
    public static IntPtr UniqueWindow(uint processId) {
        var windows = new List<IntPtr>();
        EnumProc callback = (window, state) => {
            uint actual;
            NativeRect bounds;
            if (GetWindowThreadProcessId(window, out actual) != 0 && actual == processId && IsWindowVisible(window) &&
                GetWindowRect(window, out bounds) && bounds.Right > bounds.Left && bounds.Bottom > bounds.Top) windows.Add(window);
            return true;
        };
        if (!EnumWindows(callback, IntPtr.Zero) || windows.Count != 1) throw new InvalidOperationException("UNIQUE_WINDOW_REQUIRED");
        AssertOwner(windows[0], processId);
        return windows[0];
    }
    public static Evidence Read(IntPtr window, uint processId) {
        AssertOwner(window, processId);
        NativeRect frame, client;
        var origin = new Point();
        if (!GetWindowRect(window, out frame) || !GetClientRect(window, out client) || !ClientToScreen(window, ref origin))
            throw new InvalidOperationException("BOUNDS_UNAVAILABLE");
        var info = new MonitorInfo { Size = (uint)Marshal.SizeOf<MonitorInfo>() };
        if (!GetMonitorInfoW(MonitorFromWindow(window, 2), ref info)) throw new InvalidOperationException("WORK_AREA_UNAVAILABLE");
        uint dpi = GetDpiForWindow(window);
        if (dpi < 48 || dpi > 960) throw new InvalidOperationException("DPI_UNAVAILABLE");
        var result = new Evidence { Window = Rect(frame), Client = Rect(client), WorkArea = Rect(info.Work), Dpi = dpi };
        result.Client.X = origin.X; result.Client.Y = origin.Y;
        IntPtr buffer = Marshal.AllocHGlobal(Marshal.SizeOf<MinMaxInfo>());
        try {
            Marshal.StructureToPtr(new MinMaxInfo(), buffer, false);
            IntPtr returned;
            if (SendMessageTimeoutW(window, WM_GETMINMAXINFO, IntPtr.Zero, buffer, SMTO_ABORTIFHUNG, 5000, out returned) == IntPtr.Zero)
                throw new InvalidOperationException("MIN_TRACK_UNAVAILABLE");
            var sizes = Marshal.PtrToStructure<MinMaxInfo>(buffer);
            if (sizes.MinTrackSize.X < 1 || sizes.MinTrackSize.Y < 1 || sizes.MinTrackSize.X > 32768 || sizes.MinTrackSize.Y > 32768)
                throw new InvalidOperationException("MIN_TRACK_UNAVAILABLE");
            result.MinTrackWidth = sizes.MinTrackSize.X; result.MinTrackHeight = sizes.MinTrackSize.Y;
        } finally { Marshal.FreeHGlobal(buffer); }
        AssertOwner(window, processId);
        if (UniqueWindow(processId) != window) throw new InvalidOperationException("WINDOW_BINDING_CHANGED");
        return result;
    }
    public static Evidence Resize(IntPtr window, uint processId, int widthDip, int heightDip) {
        Evidence before = Read(window, processId);
        int width = Math.Min(before.WorkArea.Width, checked((int)Math.Round(widthDip * (before.Dpi / 96.0), MidpointRounding.AwayFromZero)));
        int height = Math.Min(before.WorkArea.Height, checked((int)Math.Round(heightDip * (before.Dpi / 96.0), MidpointRounding.AwayFromZero)));
        int x = before.WorkArea.X + (before.WorkArea.Width - width) / 2;
        int y = before.WorkArea.Y + (before.WorkArea.Height - height) / 2;
        // SetWindowPos is not an interactive sizing operation: WM_GETMINMAXINFO
        // is evidence, not a promise that this API clamps a below-minimum request.
        // Never overwrite actual readback with the requested DIP dimensions.
        if (!SetWindowPos(window, IntPtr.Zero, x, y, width, height, 0x0004 | 0x0010))
            throw new InvalidOperationException("RESIZE_FAILED");
        DwmFlush();
        return Read(window, processId);
    }
    static bool Contains(Rectangle outer, Rectangle inner) {
        return inner.X >= outer.X && inner.Y >= outer.Y &&
            (long)inner.X + inner.Width <= (long)outer.X + outer.Width &&
            (long)inner.Y + inner.Height <= (long)outer.Y + outer.Height;
    }
    static bool Intersects(Rectangle a, Rectangle b) {
        return a.X < (long)b.X + b.Width && (long)a.X + a.Width > b.X &&
            a.Y < (long)b.Y + b.Height && (long)a.Y + a.Height > b.Y;
    }
    static bool Same(Rectangle a, Rectangle b) {
        return a.X == b.X && a.Y == b.Y && a.Width == b.Width && a.Height == b.Height;
    }
    static void AssertCaptureScene(IntPtr window, uint processId, Evidence expected) {
        if (UniqueWindow(processId) != window || GetForegroundWindow() != window)
            throw new InvalidOperationException("CAPTURE_FOREGROUND_UNAVAILABLE");
        Evidence actual = Read(window, processId);
        if (!Contains(actual.WorkArea, actual.Window) || !Same(actual.Window, expected.Window))
            throw new InvalidOperationException("CAPTURE_BOUNDS_UNAVAILABLE");
        // Foreground ownership alone does not exclude an unrelated topmost
        // window. Reject any visible, non-cloaked overlapping window above it.
        int visited = 0;
        for (IntPtr above = GetWindow(window, 3); above != IntPtr.Zero; above = GetWindow(above, 3)) {
            if (++visited > 4096) throw new InvalidOperationException("CAPTURE_SCENE_UNAVAILABLE");
            if (!IsWindowVisible(above)) continue;
            int cloaked;
            if (DwmGetWindowAttribute(above, 14, out cloaked, 4) == 0 && cloaked != 0) continue;
            NativeRect bounds;
            if (!GetWindowRect(above, out bounds)) throw new InvalidOperationException("CAPTURE_SCENE_UNAVAILABLE");
            if (bounds.Right <= bounds.Left || bounds.Bottom <= bounds.Top) continue;
            if (Intersects(Rect(bounds), actual.Window)) throw new InvalidOperationException("CAPTURE_OCCLUDED");
        }
    }
    public static Evidence PrepareCapture(IntPtr window, uint processId) {
        if (!Environment.UserInteractive) throw new InvalidOperationException("INTERACTIVE_DESKTOP_UNAVAILABLE");
        IntPtr desktop = OpenInputDesktop(0, false, 0x0001);
        if (desktop == IntPtr.Zero) throw new InvalidOperationException("INTERACTIVE_DESKTOP_UNAVAILABLE");
        CloseDesktop(desktop);
        AssertOwner(window, processId);
        if (GetForegroundWindow() != window) {
            if (!SetForegroundWindow(window)) throw new InvalidOperationException("CAPTURE_FOREGROUND_UNAVAILABLE");
        }
        if (DwmFlush() != 0) throw new InvalidOperationException("COMPOSITOR_UNAVAILABLE");
        Evidence result = Read(window, processId);
        AssertCaptureScene(window, processId, result);
        return result;
    }
    public static void VerifyCaptureScene(IntPtr window, uint processId, Evidence expected) {
        AssertCaptureScene(window, processId, expected);
    }
}
'@

    $Stage = 'installedProcessBinding'
    $script:ExpectedExecutable = [FundsTraceStartupWindow]::CanonicalPath($InstalledExe, $false)
    $Process = [Diagnostics.Process]::GetProcessById($ProcessId)
    # Retain an actual process handle during enumeration and capture, reducing
    # the PID-reuse race rather than trusting the number supplied by the caller.
    $null = $Process.Handle
    Assert-ProcessBinding
    $Report.processBound = $true
    $Stage = 'physicalDpiContext'
    $PreviousDpi = [FundsTraceStartupWindow]::EnterPhysicalCoordinates()
    $DpiScopeEntered = $true
    $Stage = 'uniqueVisibleWindow'
    $Window = [FundsTraceStartupWindow]::UniqueWindow([uint32]$ProcessId)
    $Stage = 'nativeWindowInspect'
    $Evidence = [FundsTraceStartupWindow]::Read($Window, [uint32]$ProcessId)
    if ($Action -eq 'Resize') {
        $Stage = 'nativeWindowResize'
        $Evidence = [FundsTraceStartupWindow]::Resize($Window, [uint32]$ProcessId, $WidthDip, $HeightDip)
        $Report.requestedDip = [ordered]@{ width = $WidthDip; height = $HeightDip }
        $Report.readback = Convert-Evidence $Evidence
        $Report.resizePolicy = 'Centered in current work area; actual readback is authoritative. SetWindowPos does not guarantee interactive minimum tracking enforcement.'
    }
    foreach ($Entry in (Convert-Evidence $Evidence).GetEnumerator()) { $Report[$Entry.Key] = $Entry.Value }
    Assert-ProcessBinding
    $Report.status = 'passed'
    $Report.failedCheck = $null
    $ExitCode = 0

    if ($Action -eq 'Capture') {
        $Stage = 'screenshotOutputBinding'
        $CapturePath = Get-NormalizedAbsolutePath $ScreenshotPath
        if ([IO.Path]::GetExtension($CapturePath) -ine '.png' -or (Test-Path -LiteralPath $CapturePath)) {
            throw 'INVALID_SCREENSHOT_OUTPUT'
        }
        $CaptureParent = [FundsTraceStartupWindow]::CanonicalPath([IO.Path]::GetDirectoryName($CapturePath), $true)
        $CanonicalOutput = Join-Path $CaptureParent ([IO.Path]::GetFileName($CapturePath))
        if ([IO.Path]::GetPathRoot($CanonicalOutput) -notmatch '^[A-Za-z]:\\$') { throw 'LOCAL_SCREENSHOT_OUTPUT_REQUIRED' }
        foreach ($ProtectedRoot in @(
            [FundsTraceStartupWindow]::CanonicalPath($InstallRoot, $true),
            [FundsTraceStartupWindow]::CanonicalPath($UserRoot, $true)
        )) {
            if (Test-WithinRoot $CanonicalOutput $ProtectedRoot) { throw 'PROTECTED_SCREENSHOT_OUTPUT' }
        }
        $Stage = 'nativeWindowCapture'
        $Report.screenshot = [ordered]@{ captured = $false; source = 'native-window-screen-composite'; reviewed = $false }
        $Bitmap = $null; $Graphics = $null; $Output = $null; $OwnOutput = $false
        try {
            Add-Type -AssemblyName System.Drawing.Common
            $CaptureEvidence = [FundsTraceStartupWindow]::PrepareCapture($Window, [uint32]$ProcessId)
            $Bounds = $CaptureEvidence.Window
            $Bitmap = [Drawing.Bitmap]::new($Bounds.Width, $Bounds.Height)
            $Graphics = [Drawing.Graphics]::FromImage($Bitmap)
            $Graphics.CopyFromScreen($Bounds.X, $Bounds.Y, 0, 0, $Bitmap.Size)
            [FundsTraceStartupWindow]::VerifyCaptureScene($Window, [uint32]$ProcessId, $CaptureEvidence)
            Assert-ProcessBinding
            $Output = [IO.File]::Open($CanonicalOutput, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $OwnOutput = $true
            $Bitmap.Save($Output, [Drawing.Imaging.ImageFormat]::Png)
            $Output.Dispose(); $Output = $null
            $Report.screenshot = [ordered]@{
                captured = $true; width = $Bounds.Width; height = $Bounds.Height
                sha256 = (Get-FileHash -LiteralPath $CanonicalOutput -Algorithm SHA256).Hash.ToLowerInvariant()
                source = 'native-window-screen-composite'; reviewed = $false
            }
            foreach ($Entry in (Convert-Evidence $CaptureEvidence).GetEnumerator()) { $Report[$Entry.Key] = $Entry.Value }
        } catch {
            $Report.status = 'unavailable'
            $Report.failedCheck = 'nativeWindowCapture'
            $ExitCode = 2
            if ($null -ne $Output) { $Output.Dispose(); $Output = $null }
            if ($OwnOutput) { [IO.File]::Delete($CanonicalOutput) }
        } finally {
            if ($null -ne $Output) { $Output.Dispose() }
            if ($null -ne $Graphics) { $Graphics.Dispose() }
            if ($null -ne $Bitmap) { $Bitmap.Dispose() }
        }
    }
} catch {
    $Report.status = 'failed'
    $Report.failedCheck = $Stage
    $ExitCode = 1
    if ($Action -eq 'Capture' -and -not $Report.Contains('screenshot')) {
        $Report.screenshot = [ordered]@{ captured = $false; source = 'native-window-screen-composite'; reviewed = $false }
    }
} finally {
    if ($DpiScopeEntered) { [FundsTraceStartupWindow]::RestoreDpiContext($PreviousDpi) }
    if ($null -ne $Process) { $Process.Dispose() }
}
# No exception text, process arguments, titles, handles, or filesystem paths enter
# this report. A saved image remains unreviewed evidence until visually checked.
$Report | ConvertTo-Json -Depth 8 -Compress
exit $ExitCode
