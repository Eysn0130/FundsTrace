#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [ValidateSet('Inspect', 'Resize', 'UserResize', 'Capture', 'CaptionProbe', 'CaptionClose', 'CaptionCloseForeground', 'ZoomIn', 'ZoomReset')][string]$Action = 'Inspect',
    [int]$WidthDip = 0,
    [int]$HeightDip = 0,
    [string]$ScreenshotPath = '',
    [int]$DragXDip = 0,
    [int]$MenuXDip = 0
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

# This helper is only for the disposable installer's synthetic account. It never
# launches an app, changes a display setting, reads a title, or inspects a renderer.
# Caption/input actions are bound to this installed PID and a foreground hit test.
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
    if ($null -eq $Rectangle) { return $null }
    return [ordered]@{ x = $Rectangle.X; y = $Rectangle.Y; width = $Rectangle.Width; height = $Rectangle.Height }
}
function Convert-Evidence($Evidence) {
    $ElectronMinTrack = $null
    $FrameInsets = $null
    if ($Evidence.NormalizationAvailable) {
        $ElectronMinTrack = [ordered]@{ width = $Evidence.ElectronMinTrackWidth; height = $Evidence.ElectronMinTrackHeight }
        $FrameInsets = [ordered]@{ left = $Evidence.FrameInsets.Left; top = $Evidence.FrameInsets.Top; right = $Evidence.FrameInsets.Right; bottom = $Evidence.FrameInsets.Bottom }
    }
    return [ordered]@{
        window = (Convert-Rect $Evidence.Window)
        client = (Convert-Rect $Evidence.Client)
        workArea = (Convert-Rect $Evidence.WorkArea)
        dpi = $Evidence.Dpi
        scaleFactor = $Evidence.Dpi / 96.0
        minTrack = [ordered]@{ width = $Evidence.MinTrackWidth; height = $Evidence.MinTrackHeight }
        normalizationAvailable = $Evidence.NormalizationAvailable
        electronWindow = (Convert-Rect $Evidence.ElectronWindow)
        electronMinTrack = $ElectronMinTrack
        frameInsets = $FrameInsets
        frameInsetsSource = $Evidence.FrameInsetsSource
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
    if ($Action -in @('Resize', 'UserResize') -and ($WidthDip -lt 1 -or $HeightDip -lt 1 -or
        $WidthDip -gt 16384 -or $HeightDip -gt 16384)) { throw 'INVALID_RESIZE_INPUT' }
    if ($Action -eq 'CaptionProbe' -and ($DragXDip -le 0 -or $MenuXDip -le 0 -or
        $DragXDip -gt 16384 -or $MenuXDip -gt 16384)) { throw 'INVALID_CAPTION_INPUT' }

    $Stage = 'nativeWindowReader'
    Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

public static class FundsTraceStartupWindow {
    static readonly IntPtr PerMonitorV2 = new IntPtr(-4);
    static IntPtr selectedForegroundWindow, remainingOwnedWindow;
    static uint selectedForegroundProcess;
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
    public sealed class Insets {
        public int Left, Top, Right, Bottom;
    }
    public sealed class Evidence {
        public Rectangle Window, Client, WorkArea;
        public Rectangle CaptureFrame;
        public uint Dpi;
        public int MinTrackWidth, MinTrackHeight;
        public Rectangle ElectronWindow;
        public Insets FrameInsets;
        public int ElectronMinTrackWidth, ElectronMinTrackHeight;
        public bool NormalizationAvailable;
        public string FrameInsetsSource = "restored-hidden-thick-frame; GetWindowRect-and-screen-client";
    }
    [StructLayout(LayoutKind.Sequential)] struct MouseInput {
        public int X, Y;
        public uint MouseData, Flags, Time;
        public UIntPtr Extra;
    }
    [StructLayout(LayoutKind.Sequential)] struct KeyInput {
        public ushort Key, Scan;
        public uint Flags, Time;
        public UIntPtr Extra;
    }
    [StructLayout(LayoutKind.Explicit)] struct InputUnion {
        [FieldOffset(0)] public MouseInput Mouse;
        [FieldOffset(0)] public KeyInput Keyboard;
    }
    [StructLayout(LayoutKind.Sequential)] struct Input { public uint Type; public InputUnion Value; }
    public sealed class CaptionEvidence {
        public string InputSource = "SendInput-on-bound-visible-caption";
        public bool PointerInputInjected = true, MinimizeButtonClicked, MinimizedObserved, MinimizeRestoredObserved;
        public bool MaximizeButtonClicked, MaximizedObserved, RestoreButtonClicked, RestoredObserved;
        public bool Dragged, CloseButtonClicked, WindowClosedObserved;
        public string MinimizeRestoreSource = "ShowWindow-SW_RESTORE; not a caption button click";
        public int CaptionHit, MenuHit;
        public Evidence Before, After;
    }
    delegate bool EnumProc(IntPtr window, IntPtr state);
    [DllImport("user32.dll", SetLastError = true)] static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
    [DllImport("user32.dll")] static extern IntPtr GetThreadDpiAwarenessContext();
    [DllImport("user32.dll")] static extern bool AreDpiAwarenessContextsEqual(IntPtr first, IntPtr second);
    [DllImport("user32.dll", SetLastError = true)] static extern bool EnumWindows(EnumProc callback, IntPtr state);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsZoomed(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(Point point);
    [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr window, uint flags);
    [DllImport("user32.dll")] static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")] static extern bool GetCursorPos(out Point point);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll", SetLastError = true)] static extern uint SendInput(uint count, Input[] inputs, int size);
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
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr window, uint attribute, out NativeRect value, uint size);
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
        if (window == IntPtr.Zero || !IsWindow(window) || GetWindowThreadProcessId(window, out actual) == 0 || actual != processId ||
            !IsWindowVisible(window) || IsIconic(window)) throw new InvalidOperationException("WINDOW_BINDING_FAILED");
    }
    static List<IntPtr> OwnedVisibleWindows(uint processId) {
        var windows = new List<IntPtr>();
        EnumProc callback = (window, state) => {
            uint actual;
            NativeRect bounds;
            if (GetWindowThreadProcessId(window, out actual) != 0 && actual == processId && IsWindowVisible(window) &&
                GetWindowRect(window, out bounds) && bounds.Right > bounds.Left && bounds.Bottom > bounds.Top) windows.Add(window);
            return true;
        };
        if (!EnumWindows(callback, IntPtr.Zero)) throw new InvalidOperationException("WINDOW_ENUMERATION_FAILED");
        return windows;
    }
    // Pure selection guard for independently supplied synthetic HWND fixtures.
    // Native callers supply only the process-bound enumeration above.
    public static IntPtr SelectOwnedCandidate(IntPtr[] windows, IntPtr foreground, bool foregroundOfTwo) {
        if (windows == null || windows.Length != (foregroundOfTwo ? 2 : 1))
            throw new InvalidOperationException(foregroundOfTwo ? "TWO_OWNED_WINDOWS_REQUIRED" : "UNIQUE_WINDOW_REQUIRED");
        var unique = new HashSet<IntPtr>();
        foreach (IntPtr window in windows)
            if (window == IntPtr.Zero || !unique.Add(window)) throw new InvalidOperationException("WINDOW_BINDING_FAILED");
        if (foregroundOfTwo && (foreground == IntPtr.Zero || !unique.Contains(foreground)))
            throw new InvalidOperationException("OWNED_FOREGROUND_REQUIRED");
        return foregroundOfTwo ? foreground : windows[0];
    }
    public static IntPtr UniqueWindow(uint processId) {
        var windows = OwnedVisibleWindows(processId);
        IntPtr selected = SelectOwnedCandidate(windows.ToArray(), IntPtr.Zero, false);
        AssertOwner(selected, processId);
        return selected;
    }
    public static IntPtr ForegroundWindowOfTwo(uint processId) {
        AssertInputDesktopAvailable();
        var windows = OwnedVisibleWindows(processId);
        foreach (IntPtr window in windows) AssertOwner(window, processId);
        IntPtr selected = SelectOwnedCandidate(windows.ToArray(), GetForegroundWindow(), true);
        selectedForegroundWindow = selected; selectedForegroundProcess = processId;
        remainingOwnedWindow = windows[0] == selected ? windows[1] : windows[0];
        AssertSelection(selected, processId);
        return selected;
    }
    static void AssertSelection(IntPtr window, uint processId) {
        AssertOwner(window, processId);
        if (selectedForegroundWindow != IntPtr.Zero) {
            if (window != selectedForegroundWindow || processId != selectedForegroundProcess)
                throw new InvalidOperationException("WINDOW_BINDING_CHANGED");
            var windows = OwnedVisibleWindows(processId);
            foreach (IntPtr candidate in windows) AssertOwner(candidate, processId);
            if (SelectOwnedCandidate(windows.ToArray(), GetForegroundWindow(), true) != window || !windows.Contains(remainingOwnedWindow))
                throw new InvalidOperationException("WINDOW_BINDING_CHANGED");
        } else if (UniqueWindow(processId) != window) throw new InvalidOperationException("WINDOW_BINDING_CHANGED");
    }
    static void AssertRemainingWindow(uint processId) {
        if (selectedForegroundWindow == IntPtr.Zero) return;
        var windows = OwnedVisibleWindows(processId);
        if (processId != selectedForegroundProcess || windows.Count != 1 || windows[0] != remainingOwnedWindow)
            throw new InvalidOperationException("WINDOW_BINDING_CHANGED");
        AssertOwner(remainingOwnedWindow, processId);
    }
    public static void NormalizeRestoredGeometry(Evidence value, bool restored) {
        value.NormalizationAvailable = false;
        value.ElectronWindow = null; value.FrameInsets = null;
        value.ElectronMinTrackWidth = 0; value.ElectronMinTrackHeight = 0;
        // Electron's restored hidden/thick-frame widget is its screen client
        // rectangle. Maximized/fullscreen GetBounds follows another contract.
        if (!restored) return;
        if (value.Window == null || value.Client == null || !Contains(value.Window, value.Client))
            throw new InvalidOperationException("FRAME_INSETS_UNAVAILABLE");
        var insets = new Insets {
            Left = checked(value.Client.X - value.Window.X),
            Top = checked(value.Client.Y - value.Window.Y),
            Right = checked((int)((long)value.Window.X + value.Window.Width - value.Client.X - value.Client.Width)),
            Bottom = checked((int)((long)value.Window.Y + value.Window.Height - value.Client.Y - value.Client.Height))
        };
        // This CI helper serves only the candidate's restored hidden/thick frame:
        // no native caption inset, with the same resize inset at left/right/bottom.
        if (insets.Top != 0 || insets.Left != insets.Right || insets.Left != insets.Bottom)
            throw new InvalidOperationException("FRAME_INSETS_UNAVAILABLE");
        int minimumWidth = checked(value.MinTrackWidth - insets.Left - insets.Right);
        int minimumHeight = checked(value.MinTrackHeight - insets.Top - insets.Bottom);
        if (minimumWidth < 1 || minimumHeight < 1)
            throw new InvalidOperationException("MIN_TRACK_UNAVAILABLE");
        value.ElectronWindow = new Rectangle { X = value.Client.X, Y = value.Client.Y, Width = value.Client.Width, Height = value.Client.Height };
        value.FrameInsets = insets;
        value.ElectronMinTrackWidth = minimumWidth; value.ElectronMinTrackHeight = minimumHeight;
        value.NormalizationAvailable = true;
    }
    public static Rectangle ResizeBounds(Evidence before, int widthDip, int heightDip) {
        if (!before.NormalizationAvailable || before.FrameInsets == null || before.WorkArea == null)
            throw new InvalidOperationException("RESIZE_RESTORED_WINDOW_REQUIRED");
        if (widthDip < 1 || heightDip < 1 || before.Dpi < 48 || before.Dpi > 960)
            throw new InvalidOperationException("INVALID_RESIZE_INPUT");
        int width = Math.Min(before.WorkArea.Width, checked((int)Math.Round(widthDip * (before.Dpi / 96.0), MidpointRounding.AwayFromZero)));
        int height = Math.Min(before.WorkArea.Height, checked((int)Math.Round(heightDip * (before.Dpi / 96.0), MidpointRounding.AwayFromZero)));
        int x = checked(before.WorkArea.X + (before.WorkArea.Width - width) / 2 - before.FrameInsets.Left);
        int y = checked(before.WorkArea.Y + (before.WorkArea.Height - height) / 2 - before.FrameInsets.Top);
        return new Rectangle { X = x, Y = y,
            Width = checked(width + before.FrameInsets.Left + before.FrameInsets.Right),
            Height = checked(height + before.FrameInsets.Top + before.FrameInsets.Bottom) };
    }
    public static Evidence Read(IntPtr window, uint processId) {
        AssertSelection(window, processId);
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
        // A resize/DPI transition between these calls would invalidate measured
        // insets. Keep the original readback and reject a mixed observation.
        NativeRect finalFrame, finalClient;
        var finalOrigin = new Point();
        if (!GetWindowRect(window, out finalFrame) || !GetClientRect(window, out finalClient) || !ClientToScreen(window, ref finalOrigin))
            throw new InvalidOperationException("BOUNDS_UNAVAILABLE");
        if (!Same(result.Window, Rect(finalFrame)) || !Same(Rect(client), Rect(finalClient)) ||
            origin.X != finalOrigin.X || origin.Y != finalOrigin.Y || GetDpiForWindow(window) != dpi)
            throw new InvalidOperationException("BOUNDS_CHANGED");
        Rectangle monitor = Rect(info.Monitor);
        NormalizeRestoredGeometry(result, !IsZoomed(window) && !(Same(result.Window, monitor) && Same(result.Client, monitor)));
        AssertSelection(window, processId);
        return result;
    }
    public static Evidence Resize(IntPtr window, uint processId, int widthDip, int heightDip) {
        Evidence before = Read(window, processId);
        Rectangle target = ResizeBounds(before, widthDip, heightDip);
        // SetWindowPos is not an interactive sizing operation: WM_GETMINMAXINFO
        // is evidence, not a promise that this API clamps a below-minimum request.
        // Never overwrite actual readback with the requested DIP dimensions.
        if (!SetWindowPos(window, IntPtr.Zero, target.X, target.Y, target.Width, target.Height, 0x0004 | 0x0010))
            throw new InvalidOperationException("RESIZE_FAILED");
        DwmFlush();
        return Read(window, processId);
    }
    static void Wait(Func<bool> ready) {
        for (int i = 0; i < 100; i++) { if (ready()) return; Thread.Sleep(30); }
        throw new InvalidOperationException("CAPTION_STATE_TIMEOUT");
    }
    static void AssertInputDesktopAvailable() {
        if (!Environment.UserInteractive) throw new InvalidOperationException("INPUT_DESKTOP_UNAVAILABLE");
        IntPtr desktop = OpenInputDesktop(0, false, 1);
        if (desktop == IntPtr.Zero) throw new InvalidOperationException("INPUT_DESKTOP_UNAVAILABLE");
        CloseDesktop(desktop);
    }
    static void InputDesktop(IntPtr window, uint processId) {
        AssertInputDesktopAvailable();
        AssertOwner(window, processId);
        if (selectedForegroundWindow != IntPtr.Zero) AssertSelection(window, processId);
        else if (GetForegroundWindow() != window && !SetForegroundWindow(window))
            throw new InvalidOperationException("INPUT_FOREGROUND_UNAVAILABLE");
        Wait(() => GetForegroundWindow() == window);
        foreach (int key in new int[] {1,2,4,16,17,18,91,92})
            if (GetAsyncKeyState(key) < 0) throw new InvalidOperationException("INPUT_KEYS_ALREADY_PRESSED");
    }
    static void Inject(params Input[] inputs) {
        if (SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<Input>()) != inputs.Length)
            throw new InvalidOperationException("INPUT_INJECTION_FAILED");
    }
    static Input Mouse(uint flags, int x = 0, int y = 0) {
        return new Input { Type = 0, Value = new InputUnion { Mouse = new MouseInput { X=x, Y=y, Flags=flags } } };
    }
    static Input Key(ushort key, bool up = false) {
        return new Input { Type = 1, Value = new InputUnion { Keyboard = new KeyInput { Key=key, Flags=up ? 2u : 0u } } };
    }
    static void Move(Point point) {
        int left=GetSystemMetrics(76), top=GetSystemMetrics(77), width=GetSystemMetrics(78), height=GetSystemMetrics(79);
        if (width < 2 || height < 2 || point.X < left || point.Y < top || point.X >= left+width || point.Y >= top+height)
            throw new InvalidOperationException("INPUT_POINT_UNAVAILABLE");
        int x=(int)Math.Round((point.X-left)*65535.0/(width-1)), y=(int)Math.Round((point.Y-top)*65535.0/(height-1));
        Inject(Mouse(0x0001u|0x8000u|0x4000u,x,y));
    }
    static int Hit(IntPtr window, Point point) {
        if (point.X < short.MinValue || point.X > short.MaxValue || point.Y < short.MinValue || point.Y > short.MaxValue)
            throw new InvalidOperationException("CAPTION_POINT_UNAVAILABLE");
        IntPtr result;
        long packed=(uint)((ushort)point.X | ((uint)(ushort)point.Y << 16));
        if (SendMessageTimeoutW(window,0x0084,IntPtr.Zero,new IntPtr(packed),SMTO_ABORTIFHUNG,1000,out result)==IntPtr.Zero)
            throw new InvalidOperationException("CAPTION_HIT_UNAVAILABLE");
        return unchecked((int)result.ToInt64());
    }
    static Point Button(IntPtr window, uint processId, int expectedHit) {
        Evidence value=Read(window,processId);
        int y=value.Client.Y+(int)Math.Round(16*value.Dpi/96.0), sum=0,count=0;
        for (int inset=3;inset<=Math.Min(240,value.Client.Width*96/(int)value.Dpi);inset+=3) {
            int x=value.Client.X+value.Client.Width-(int)Math.Round(inset*value.Dpi/96.0);
            if (Hit(window,new Point{X=x,Y=y})==expectedHit) {sum+=x;count++;}
        }
        if(count==0) throw new InvalidOperationException("NATIVE_CAPTION_BUTTON_HIT_MISSING");
        return new Point{X=sum/count,Y=y};
    }
    static void Click(IntPtr window,uint processId,Point point,int expectedHit) {
        InputDesktop(window,processId);
        if (Hit(window,point)!=expectedHit || GetAncestor(WindowFromPoint(point),2)!=window)
            throw new InvalidOperationException("CAPTION_INPUT_HIT_MISMATCH");
        Move(point); Thread.Sleep(40);
        if (selectedForegroundWindow != IntPtr.Zero) AssertSelection(window, processId);
        if (GetForegroundWindow()!=window || GetAncestor(WindowFromPoint(point),2)!=window)
            throw new InvalidOperationException(selectedForegroundWindow != IntPtr.Zero ? "WINDOW_BINDING_CHANGED" : "INPUT_FOREGROUND_UNAVAILABLE");
        try { Inject(Mouse(2)); } finally { Inject(Mouse(4)); }
    }
    static Point ResizePoint(IntPtr window,Evidence value,bool horizontal) {
        int hit=horizontal?11:15;
        int start=horizontal?value.Client.X+value.Client.Width-4:value.Client.Y+value.Client.Height-4;
        int end=horizontal?value.Window.X+value.Window.Width:value.Window.Y+value.Window.Height;
        int edge=horizontal?value.WorkArea.X+value.WorkArea.Width:value.WorkArea.Y+value.WorkArea.Height;
        for(int position=start;position<Math.Min(end,edge);position++) {
            var point=horizontal?new Point{X=position,Y=value.Client.Y+value.Client.Height/2}
                :new Point{X=value.Client.X+value.Client.Width/2,Y=position};
            if(Hit(window,point)==hit && GetAncestor(WindowFromPoint(point),2)==window) return point;
        }
        throw new InvalidOperationException("RESIZE_INPUT_EDGE_UNAVAILABLE");
    }
    static void DragResizeEdge(IntPtr window,uint processId,bool horizontal,int targetSize) {
        Evidence before=Read(window,processId);
        int size=horizontal?before.Client.Width:before.Client.Height;
        if(size==targetSize) return;
        Point point=ResizePoint(window,before,horizontal);
        int delta=targetSize-size, hit=horizontal?11:15;
        InputDesktop(window,processId);
        if(Hit(window,point)!=hit || GetAncestor(WindowFromPoint(point),2)!=window)
            throw new InvalidOperationException("RESIZE_INPUT_EDGE_UNAVAILABLE");
        Move(point);Thread.Sleep(40);
        if(GetForegroundWindow()!=window || GetAncestor(WindowFromPoint(point),2)!=window)
            throw new InvalidOperationException("INPUT_FOREGROUND_UNAVAILABLE");
        Inject(Mouse(2));
        try {
            for(int step=1;step<=8;step++) {
                if(GetForegroundWindow()!=window) throw new InvalidOperationException("INPUT_FOREGROUND_UNAVAILABLE");
                Move(new Point{X=point.X+(horizontal?delta*step/8:0),Y=point.Y+(horizontal?0:delta*step/8)});
                Thread.Sleep(25);
            }
        } finally {Inject(Mouse(4));}
        Wait(()=>{NativeRect client;return GetClientRect(window,out client)
            && Math.Abs((horizontal?client.Right-client.Left:client.Bottom-client.Top)-targetSize)<=1;});
    }
    public static Evidence UserResize(IntPtr window,uint processId,int widthDip,int heightDip) {
        InputDesktop(window,processId);
        Evidence before=Read(window,processId);
        if(!before.NormalizationAvailable || IsZoomed(window) || widthDip<1 || heightDip<1)
            throw new InvalidOperationException("USER_RESIZE_RESTORED_WINDOW_REQUIRED");
        int width=(int)Math.Round(widthDip*before.Dpi/96.0,MidpointRounding.AwayFromZero);
        int height=(int)Math.Round(heightDip*before.Dpi/96.0,MidpointRounding.AwayFromZero);
        if(width<before.ElectronMinTrackWidth || height<before.ElectronMinTrackHeight
            || width>before.WorkArea.Width || height>before.WorkArea.Height)
            throw new InvalidOperationException("INVALID_USER_RESIZE_INPUT");
        Point cursor;if(!GetCursorPos(out cursor)) throw new InvalidOperationException("INPUT_CURSOR_UNAVAILABLE");
        try {
            // Position only: NOSIZE is required. The actual size changes below
            // come exclusively from native nonclient mouse dragging, so Electron
            // receives the same will-resize events as an interactive user.
            if(!SetWindowPos(window,IntPtr.Zero,before.WorkArea.X-before.FrameInsets.Left,
                before.WorkArea.Y-before.FrameInsets.Top,0,0,0x0001|0x0004|0x0010))
                throw new InvalidOperationException("USER_RESIZE_POSITION_FAILED");
            DwmFlush();Thread.Sleep(60);
            DragResizeEdge(window,processId,true,width);
            DragResizeEdge(window,processId,false,height);
            DwmFlush();return Read(window,processId);
        } finally {Move(cursor);}
    }
    public static void AssertCaptionHits(CaptionEvidence result) {
        if(result.CaptionHit!=2 || result.MenuHit!=2)
            throw new InvalidOperationException("CAPTION_DRAG_PREVIEW_HIT_MISMATCH");
    }
    public static CaptionEvidence Caption(IntPtr window,uint processId,int dragXDip,int menuXDip,bool close) {
        InputDesktop(window,processId);
        Point cursor; if(!GetCursorPos(out cursor)) throw new InvalidOperationException("INPUT_CURSOR_UNAVAILABLE");
        var result=new CaptionEvidence{Before=Read(window,processId)};
        try {
            if(close) {
                Click(window,processId,Button(window,processId,20),20);
                result.CloseButtonClicked=true;
                Wait(()=>!IsWindow(window));
                AssertRemainingWindow(processId);
                result.WindowClosedObserved=true;return result;
            }
            Evidence before=result.Before;
            if(IsZoomed(window)) throw new InvalidOperationException("CAPTION_EXPECTED_NORMAL_WINDOW");
            var drag=new Point{X=before.Client.X+(int)Math.Round(dragXDip*before.Dpi/96.0),Y=before.Client.Y+(int)Math.Round(16*before.Dpi/96.0)};
            var menu=new Point{X=before.Client.X+(int)Math.Round(menuXDip*before.Dpi/96.0),Y=drag.Y};
            result.CaptionHit=Hit(window,drag);result.MenuHit=Hit(window,menu);
            AssertCaptionHits(result);
            int right=before.WorkArea.X+before.WorkArea.Width-before.Window.X-before.Window.Width;
            int bottom=before.WorkArea.Y+before.WorkArea.Height-before.Window.Y-before.Window.Height;
            int dx=right>=24?24:before.Window.X-before.WorkArea.X>=24?-24:0;
            int dy=bottom>=12?12:before.Window.Y-before.WorkArea.Y>=12?-12:0;
            if(dx!=0 || dy!=0) {
                if(GetAncestor(WindowFromPoint(drag),2)!=window) throw new InvalidOperationException("CAPTION_INPUT_HIT_MISMATCH");
                Move(drag);Inject(Mouse(2));
                try { for(int step=1;step<=6;step++) {Move(new Point{X=drag.X+dx*step/6,Y=drag.Y+dy*step/6});Thread.Sleep(20);} }
                finally {Inject(Mouse(4));}
                Wait(()=>{NativeRect r;return GetWindowRect(window,out r) && (Math.Abs(r.Left-before.Window.X)>=4 || Math.Abs(r.Top-before.Window.Y)>=4);});
                Evidence moved=Read(window,processId);
                if(moved.Window.Width!=before.Window.Width || moved.Window.Height!=before.Window.Height)
                    throw new InvalidOperationException("CAPTION_DRAG_RESIZED_WINDOW");
                result.Dragged=true;
                // Cleanup only; this bounds reset is never reported as a drag.
                if(!SetWindowPos(window,IntPtr.Zero,before.Window.X,before.Window.Y,before.Window.Width,before.Window.Height,0x0004|0x0010))
                    throw new InvalidOperationException("CAPTION_RESTORE_BOUNDS_FAILED");
            }
            Click(window,processId,Button(window,processId,8),8);result.MinimizeButtonClicked=true;
            Wait(()=>IsIconic(window));result.MinimizedObserved=true;
            ShowWindow(window,9);Wait(()=>!IsIconic(window));result.MinimizeRestoredObserved=true;
            Click(window,processId,Button(window,processId,9),9);result.MaximizeButtonClicked=true;
            Wait(()=>IsZoomed(window));result.MaximizedObserved=true;
            Click(window,processId,Button(window,processId,9),9);result.RestoreButtonClicked=true;
            Wait(()=>!IsZoomed(window) && !IsIconic(window));result.RestoredObserved=true;
            result.After=Read(window,processId);return result;
        } finally { Move(cursor); }
    }
    public static void Zoom(IntPtr window,uint processId,bool reset) {
        InputDesktop(window,processId);
        // Actual key input reaches the installed application's accelerators.
        // This is not CSS/Emulation scaling and never changes Windows DPI.
        try {
            if(reset) Inject(Key(0x11),Key(0x30),Key(0x30,true),Key(0x11,true));
            else Inject(Key(0x11),Key(0x10),Key(0xBB),Key(0xBB,true),Key(0x10,true),Key(0x11,true));
        } finally {
            // Release only the keys this guarded helper injected, even after a
            // partial SendInput result. Existing pressed keys were rejected.
            Inject(Key(reset?(ushort)0x30:(ushort)0xBB,true),Key(0x10,true),Key(0x11,true));
        }
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
    static Rectangle VisibleFrame(IntPtr window, Evidence evidence) {
        NativeRect frame;
        // GetWindowRect contains invisible resize borders on modern Windows.
        // DWM's extended frame rectangle is already in physical screen pixels;
        // never fall back to copying desktop pixels from those invisible edges.
        if (DwmGetWindowAttribute(window, 9, out frame, (uint)Marshal.SizeOf<NativeRect>()) != 0)
            throw new InvalidOperationException("CAPTURE_FRAME_UNAVAILABLE");
        Rectangle visible = Rect(frame);
        if (!Contains(evidence.Window, visible) || !Contains(evidence.WorkArea, visible))
            throw new InvalidOperationException("CAPTURE_FRAME_UNAVAILABLE");
        return visible;
    }
    static void AssertCaptureScene(IntPtr window, uint processId, Evidence expected) {
        if (UniqueWindow(processId) != window || GetForegroundWindow() != window)
            throw new InvalidOperationException("CAPTURE_FOREGROUND_UNAVAILABLE");
        Evidence actual = Read(window, processId);
        Rectangle frame = VisibleFrame(window, actual);
        if (!Same(actual.Window, expected.Window) || expected.CaptureFrame == null || !Same(frame, expected.CaptureFrame))
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
            if (Intersects(Rect(bounds), frame)) throw new InvalidOperationException("CAPTURE_OCCLUDED");
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
        result.CaptureFrame = VisibleFrame(window, result);
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
    if ($Action -eq 'CaptionCloseForeground') {
        $Stage = 'nativeCaptionInteraction'
        $Report.selection = 'foreground-of-two-owned-windows'
        $Window = [FundsTraceStartupWindow]::ForegroundWindowOfTwo([uint32]$ProcessId)
    } else {
        $Window = [FundsTraceStartupWindow]::UniqueWindow([uint32]$ProcessId)
    }
    $Stage = 'nativeWindowInspect'
    $Evidence = [FundsTraceStartupWindow]::Read($Window, [uint32]$ProcessId)
    foreach ($Entry in (Convert-Evidence $Evidence).GetEnumerator()) { $Report[$Entry.Key] = $Entry.Value }
    if ($Action -eq 'Resize') {
        $Stage = 'nativeWindowResize'
        $Evidence = [FundsTraceStartupWindow]::Resize($Window, [uint32]$ProcessId, $WidthDip, $HeightDip)
        $Report.requestedDip = [ordered]@{ width = $WidthDip; height = $HeightDip }
        $Report.readback = Convert-Evidence $Evidence
        $Report.resizePolicy = 'Electron client geometry centered in current work area; raw SetWindowPos bounds include measured resize insets. Actual readback is authoritative; SetWindowPos does not guarantee interactive minimum tracking enforcement.'
    }
    if ($Action -eq 'UserResize') {
        $Stage = 'nativeWindowResize'
        $BeforeResize = $Evidence
        $Evidence = [FundsTraceStartupWindow]::UserResize($Window, [uint32]$ProcessId, $WidthDip, $HeightDip)
        $Report.requestedDip = [ordered]@{ width = $WidthDip; height = $HeightDip }
        $Report.interaction = [ordered]@{ source = 'SendInput-native-resize-edge';
            userResizeObserved = ($Evidence.Client.Width -ne $BeforeResize.Client.Width -or $Evidence.Client.Height -ne $BeforeResize.Client.Height) }
        $Report.resizePolicy = 'Position-only preparation with NOSIZE, followed by actual native right/bottom edge mouse dragging. Actual readback is authoritative.'
    }
    if ($Action -in @('CaptionProbe', 'CaptionClose', 'CaptionCloseForeground')) {
        $Stage = 'nativeCaptionInteraction'
        Assert-ProcessBinding
        $Report.interaction = [FundsTraceStartupWindow]::Caption($Window, [uint32]$ProcessId, $DragXDip, $MenuXDip, $Action -in @('CaptionClose', 'CaptionCloseForeground'))
        if ($Action -eq 'CaptionProbe') { $Evidence = $Report.interaction.After }
    }
    if ($Action -in @('ZoomIn', 'ZoomReset')) {
        $Stage = 'nativeCaptionInteraction'
        [FundsTraceStartupWindow]::Zoom($Window, [uint32]$ProcessId, $Action -eq 'ZoomReset')
        $Report.interaction = [ordered]@{ source = 'SendInput-keyboard'; action = $Action; windowsDpiChanged = $false }
    }
    foreach ($Entry in (Convert-Evidence $Evidence).GetEnumerator()) { $Report[$Entry.Key] = $Entry.Value }
    if ($Action -ne 'CaptionClose') { Assert-ProcessBinding }
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
            $Bounds = $CaptureEvidence.CaptureFrame
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
                source = 'native-window-screen-composite'; frame = 'DWM-extended-frame-bounds'
                bounds = (Convert-Rect $Bounds); reviewed = $false
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
    $SafeExceptionCode = $_.Exception.GetBaseException().Message
    $InputUnavailable = @('INPUT_DESKTOP_UNAVAILABLE', 'INPUT_FOREGROUND_UNAVAILABLE',
        'INPUT_KEYS_ALREADY_PRESSED', 'INPUT_INJECTION_FAILED', 'INPUT_CURSOR_UNAVAILABLE')
    if ($Stage -eq 'nativeCaptionInteraction') {
        $FailureCodes = @{
            TWO_OWNED_WINDOWS_REQUIRED = 'OWNED_WINDOW_COUNT'; UNIQUE_WINDOW_REQUIRED = 'OWNED_WINDOW_COUNT'
            WINDOW_ENUMERATION_FAILED = 'OWNED_WINDOW_COUNT'
            WINDOW_BINDING_FAILED = 'FOREGROUND_BINDING'; WINDOW_BINDING_CHANGED = 'FOREGROUND_BINDING'
            OWNED_FOREGROUND_REQUIRED = 'FOREGROUND_BINDING'
            NATIVE_CAPTION_BUTTON_HIT_MISSING = 'HTCLOSE'; CAPTION_INPUT_HIT_MISMATCH = 'HTCLOSE'
            CAPTION_HIT_UNAVAILABLE = 'HTCLOSE'; CAPTION_POINT_UNAVAILABLE = 'HTCLOSE'
            CAPTION_DRAG_PREVIEW_HIT_MISMATCH = 'CAPTION_HIT'
            CAPTION_EXPECTED_NORMAL_WINDOW = 'CAPTION_STATE'
            CAPTION_DRAG_RESIZED_WINDOW = 'CAPTION_GEOMETRY'; CAPTION_RESTORE_BOUNDS_FAILED = 'CAPTION_GEOMETRY'
            CAPTION_STATE_TIMEOUT = 'STATE_TIMEOUT'
            PROCESS_BINDING_FAILED = 'PROCESS_BINDING'; PATH_BINDING_FAILED = 'PROCESS_BINDING'
            BOUNDS_UNAVAILABLE = 'NATIVE_GEOMETRY'; BOUNDS_CHANGED = 'NATIVE_GEOMETRY'
            MIN_TRACK_UNAVAILABLE = 'NATIVE_GEOMETRY'; FRAME_INSETS_UNAVAILABLE = 'NATIVE_GEOMETRY'
            WORK_AREA_UNAVAILABLE = 'NATIVE_GEOMETRY'; DPI_UNAVAILABLE = 'NATIVE_GEOMETRY'
            DPI_CONTEXT_UNAVAILABLE = 'NATIVE_GEOMETRY'; INPUT_POINT_UNAVAILABLE = 'CAPTION_POINT'
        }
        $Report.failureCode = if ($FailureCodes.ContainsKey($SafeExceptionCode)) { $FailureCodes[$SafeExceptionCode] }
            elseif ($SafeExceptionCode -in $InputUnavailable) { $SafeExceptionCode }
            elseif ($Action -eq 'CaptionCloseForeground') { 'NATIVE_CLOSE_FAILED' } else { 'NATIVE_CAPTION_FAILED' }
    }
    if ($Stage -eq 'nativeCaptionInteraction' -and $SafeExceptionCode -in $InputUnavailable) {
        $Report.status = 'unavailable'
        $Report.reason = $SafeExceptionCode
        $ExitCode = 2
    }
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
