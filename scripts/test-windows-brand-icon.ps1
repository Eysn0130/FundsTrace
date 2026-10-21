#Requires -Version 7.0
param(
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$ExpectedIco,
    [ValidateRange(0, 2147483647)][int]$ProcessId = 0,
    [string]$TaskbarScreenshot = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Read-only evidence from the disposable Actions account. Never inspect a workstation's shortcuts/windows.
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or -not $env:RUNNER_TEMP -or
    [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') {
    [Console]::Error.WriteLine('Brand icon verification requires native Windows x64 GitHub Actions.')
    '{"schemaVersion":1,"status":"failed","failedCheck":"ciPlatform","checks":{},"limits":{"windows10And11Interactive":false,"taskbarAppearance":false}}'
    exit 1
}
$Report = [ordered]@{
    schemaVersion = 1; status = 'running'; failedCheck = $null; failureCode = $null
    mode = $(if ($ProcessId -gt 0) { 'visibleWindowIcons' } else { 'shortcuts' })
    platform = [ordered]@{ osVersion = [Environment]::OSVersion.Version.ToString(); architecture = 'X64'; githubActions = $true }
    checks = [ordered]@{ inputBinding = 'not-run' }
    iconComparisons = [System.Collections.Generic.List[object]]::new()
    limits = [ordered]@{
        windows10And11Interactive = $false; taskbarAppearance = $false; systemDpi = $false
        comparison = 'Exact RGB pixels after Win32 rendering on black and white backgrounds at the actual HICON dimensions; no tolerance.'
        scope = 'Only fixed current-user FundsTrace shortcuts or visible top-level windows of the supplied installed EXE PID; no app launch, window changes or business content.'
        resampling = 'A different rasterization/resampling result fails even if visually similar; this does not establish native taskbar, pinning or shortcut appearance on Windows 10/11.'
    }
}
$Stage = 'inputBinding'
$Failed = $false

function Get-NormalizedPath([string]$Value) {
    $Value = [Environment]::ExpandEnvironmentVariables($Value.Trim())
    if ($Value.StartsWith('"') -and $Value.EndsWith('"') -and $Value.Length -gt 1) {
        $Value = $Value.Substring(1, $Value.Length - 2)
    }
    if ($Value.Contains('"') -or -not [IO.Path]::IsPathRooted($Value)) { throw 'INVALID_PATH' }
    return [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath($Value))
}

try {
    $InstallRoot = Get-NormalizedPath $InstallRoot
    $ExpectedIco = Get-NormalizedPath $ExpectedIco
    $InstalledExe = Join-Path $InstallRoot 'FundsTrace.exe'
    if (-not (Test-Path -LiteralPath $InstalledExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ExpectedIco -PathType Leaf) -or
        [IO.Path]::GetExtension($ExpectedIco) -ine '.ico') { throw 'MISSING_CANDIDATE_INPUT' }
    $Report.checks.inputBinding = 'passed'
    $Stage = 'nativeIconReader'
    Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

public static class FundsTraceBrandIcons {
    const uint WM_GETICON = 0x007F, DI_NORMAL = 3, LR_LOADFROMFILE = 0x0010;
    [StructLayout(LayoutKind.Sequential)] struct IconInfo {
        [MarshalAs(UnmanagedType.Bool)] public bool IsIcon;
        public uint XHotspot, YHotspot;
        public IntPtr Mask, Color;
    }
    [StructLayout(LayoutKind.Sequential)] struct Bitmap {
        public int Type, Width, Height, WidthBytes;
        public ushort Planes, BitsPixel;
        public IntPtr Bits;
    }
    [StructLayout(LayoutKind.Sequential)] struct BitmapHeader {
        public uint Size;
        public int Width, Height;
        public ushort Planes, BitCount;
        public uint Compression, ImageSize;
        public int XPixels, YPixels;
        public uint ColorsUsed, ColorsImportant;
    }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)] struct ShellFileInfo {
        public IntPtr Icon;
        public int IconIndex;
        public uint Attributes;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string DisplayName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 80)] public string TypeName;
    }
    [StructLayout(LayoutKind.Sequential)] struct WindowRect { public int Left, Top, Right, Bottom; }
    public sealed class Comparison {
        public string Source;
        public int Width, Height;
        public bool Matched;
        public string ActualSha256, ExpectedSha256;
    }
    public sealed class WindowEvidence {
        public int VisibleWindows;
        public List<Comparison> Icons = new List<Comparison>();
    }
    delegate bool EnumProc(IntPtr window, IntPtr state);
    [DllImport("user32.dll", SetLastError = true)] static extern bool GetIconInfo(IntPtr icon, out IconInfo info);
    [DllImport("gdi32.dll", EntryPoint = "GetObjectW", ExactSpelling = true, SetLastError = true)] static extern int GetObject(IntPtr obj, int size, out Bitmap bitmap);
    [DllImport("gdi32.dll", SetLastError = true)] static extern IntPtr CreateCompatibleDC(IntPtr dc);
    [DllImport("gdi32.dll", SetLastError = true)] static extern IntPtr CreateDIBSection(IntPtr dc, ref BitmapHeader info, uint usage, out IntPtr bits, IntPtr section, uint offset);
    [DllImport("gdi32.dll", SetLastError = true)] static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr obj);
    [DllImport("gdi32.dll")] static extern bool DeleteDC(IntPtr dc);
    [DllImport("gdi32.dll")] static extern bool GdiFlush();
    [DllImport("user32.dll", SetLastError = true)] static extern bool DrawIconEx(IntPtr dc, int x, int y, IntPtr icon, int width, int height, uint step, IntPtr brush, uint flags);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)] static extern IntPtr LoadImageW(IntPtr module, string name, uint type, int width, int height, uint flags);
    [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr icon);
    [DllImport("user32.dll", SetLastError = true)] static extern IntPtr CopyIcon(IntPtr icon);
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)] static extern IntPtr SHGetFileInfoW(string path, uint attributes, out ShellFileInfo info, uint size, uint flags);
    [DllImport("comctl32.dll", ExactSpelling = true)] static extern IntPtr ImageList_GetIcon(IntPtr imageList, int index, uint flags);
    [DllImport("user32.dll", SetLastError = true)] static extern bool EnumWindows(EnumProc callback, IntPtr state);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode, ExactSpelling = true)] static extern IntPtr FindWindowW(string className, string name);
    [DllImport("user32.dll", SetLastError = true)] static extern bool GetWindowRect(IntPtr window, out WindowRect rectangle);
    [DllImport("user32.dll", ExactSpelling = true, SetLastError = true)] static extern IntPtr SendMessageTimeoutW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam, uint flags, uint timeout, out IntPtr result);
    [DllImport("user32.dll", EntryPoint = "GetClassLongPtrW", ExactSpelling = true)] static extern IntPtr GetClassLongPtr(IntPtr window, int index);

    static int[] Dimensions(IntPtr icon) {
        IconInfo info;
        if (!GetIconInfo(icon, out info)) throw new InvalidOperationException("ICON_INFO_FAILED");
        try {
            Bitmap bitmap;
            IntPtr image = info.Color != IntPtr.Zero ? info.Color : info.Mask;
            if (image == IntPtr.Zero || GetObject(image, Marshal.SizeOf<Bitmap>(), out bitmap) == 0)
                throw new InvalidOperationException("ICON_BITMAP_FAILED");
            int height = info.Color != IntPtr.Zero ? bitmap.Height : bitmap.Height / 2;
            if (bitmap.Width < 1 || height < 1 || bitmap.Width > 1024 || height > 1024)
                throw new InvalidOperationException("ICON_DIMENSIONS_INVALID");
            return new int[] { bitmap.Width, height };
        } finally {
            if (info.Color != IntPtr.Zero) DeleteObject(info.Color);
            if (info.Mask != IntPtr.Zero) DeleteObject(info.Mask);
        }
    }
    static byte[] Render(IntPtr icon, int width, int height, byte background) {
        IntPtr dc = IntPtr.Zero, image = IntPtr.Zero, previous = IntPtr.Zero;
        try {
            dc = CreateCompatibleDC(IntPtr.Zero);
            if (dc == IntPtr.Zero) throw new InvalidOperationException("ICON_DC_FAILED");
            var header = new BitmapHeader { Size = (uint)Marshal.SizeOf<BitmapHeader>(), Width = width, Height = -height, Planes = 1, BitCount = 32 };
            IntPtr bits;
            image = CreateDIBSection(dc, ref header, 0, out bits, IntPtr.Zero, 0);
            if (image == IntPtr.Zero || bits == IntPtr.Zero) throw new InvalidOperationException("ICON_DIB_FAILED");
            previous = SelectObject(dc, image);
            if (previous == IntPtr.Zero || previous == new IntPtr(-1)) throw new InvalidOperationException("ICON_SELECT_FAILED");
            var pixels = new byte[checked(width * height * 4)];
            for (int i = 0; i < pixels.Length; i += 4) {
                pixels[i] = pixels[i + 1] = pixels[i + 2] = background; pixels[i + 3] = 255;
            }
            Marshal.Copy(pixels, 0, bits, pixels.Length);
            if (!DrawIconEx(dc, 0, 0, icon, width, height, 0, IntPtr.Zero, DI_NORMAL))
                throw new InvalidOperationException("ICON_DRAW_FAILED");
            if (!GdiFlush()) throw new InvalidOperationException("ICON_FLUSH_FAILED");
            Marshal.Copy(bits, pixels, 0, pixels.Length);
            // GDI's destination alpha byte is not a visual contract. RGB over both
            // opaque backgrounds captures the complete rendered color/transparency.
            var rgb = new byte[checked(width * height * 3)];
            for (int source = 0, target = 0; source < pixels.Length; source += 4) {
                rgb[target++] = pixels[source]; rgb[target++] = pixels[source + 1]; rgb[target++] = pixels[source + 2];
            }
            return rgb;
        } finally {
            if (dc != IntPtr.Zero && previous != IntPtr.Zero && previous != new IntPtr(-1)) SelectObject(dc, previous);
            if (dc != IntPtr.Zero) DeleteDC(dc);
            if (image != IntPtr.Zero) DeleteObject(image);
        }
    }
    static string Fingerprint(IntPtr icon, int width, int height) {
        var black = Render(icon, width, height, 0);
        var white = Render(icon, width, height, 255);
        var pixels = new byte[black.Length + white.Length];
        Buffer.BlockCopy(black, 0, pixels, 0, black.Length);
        Buffer.BlockCopy(white, 0, pixels, black.Length, white.Length);
        using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(pixels)).Replace("-", "").ToLowerInvariant();
    }
    static Comparison Compare(IntPtr actual, string expectedPath, string source) {
        int[] size = Dimensions(actual);
        IntPtr expected = LoadImageW(IntPtr.Zero, expectedPath, 1, size[0], size[1], LR_LOADFROMFILE);
        if (expected == IntPtr.Zero) throw new InvalidOperationException("EXPECTED_ICON_LOAD_FAILED");
        try {
            string actualHash = Fingerprint(actual, size[0], size[1]);
            string expectedHash = Fingerprint(expected, size[0], size[1]);
            return new Comparison { Source = source, Width = size[0], Height = size[1], Matched = actualHash == expectedHash,
                ActualSha256 = actualHash, ExpectedSha256 = expectedHash };
        } finally { DestroyIcon(expected); }
    }
    static IntPtr ShellIcon(string shortcutPath, bool small) {
        ShellFileInfo info;
        // SHGFI_ICON adds the shortcut arrow even without SHGFI_ADDOVERLAYS.
        // Resolve this real link's system image index, then copy the base image
        // with ILD_NORMAL. Never mutate or destroy the shared system image list.
        IntPtr imageList = SHGetFileInfoW(shortcutPath, 0, out info, (uint)Marshal.SizeOf<ShellFileInfo>(), 0x4000u | (small ? 1u : 0u));
        if (imageList == IntPtr.Zero || info.IconIndex < 0) throw new InvalidOperationException("SHORTCUT_ICON_FAILED");
        IntPtr icon = ImageList_GetIcon(imageList, info.IconIndex, 0);
        if (icon == IntPtr.Zero) throw new InvalidOperationException("SHORTCUT_ICON_FAILED");
        return icon;
    }
    public static Comparison Shortcut(string shortcutPath, string expectedPath, bool small) {
        IntPtr icon = ShellIcon(shortcutPath, small);
        try {
            return Compare(icon, expectedPath, small ? "shell-small" : "shell-large");
        } finally { if (icon != IntPtr.Zero) DestroyIcon(icon); }
    }
    // Failure-only regression evidence: exact GDI pixels, never application
    // content. Bound the payload to a small shell icon, not an arbitrary bitmap.
    public static byte[] ShortcutPixels(string shortcutPath, bool small) {
        IntPtr icon = ShellIcon(shortcutPath, small);
        try {
            int[] size = Dimensions(icon);
            if (size[0] > 64 || size[1] > 64) throw new InvalidOperationException("ICON_DIMENSIONS_INVALID");
            byte[] black = Render(icon, size[0], size[1], 0), white = Render(icon, size[0], size[1], 255);
            byte[] result = new byte[8 + black.Length + white.Length];
            Buffer.BlockCopy(BitConverter.GetBytes(size[0]), 0, result, 0, 4);
            Buffer.BlockCopy(BitConverter.GetBytes(size[1]), 0, result, 4, 4);
            Buffer.BlockCopy(black, 0, result, 8, black.Length);
            Buffer.BlockCopy(white, 0, result, 8 + black.Length, white.Length);
            return result;
        } finally { DestroyIcon(icon); }
    }
    static void WindowIcon(WindowEvidence evidence, IntPtr window, string expectedPath, bool small) {
        IntPtr borrowed, ignored;
        // SMTO_BLOCK | SMTO_ABORTIFHUNG, bounded to one second per read.
        IntPtr read = SendMessageTimeoutW(window, WM_GETICON, new IntPtr(small ? 2 : 1), IntPtr.Zero, 3, 1000, out borrowed);
        string source = small ? "WM_GETICON-small2" : "WM_GETICON-big";
        if (read == IntPtr.Zero) borrowed = IntPtr.Zero;
        if (small && borrowed == IntPtr.Zero) {
            read = SendMessageTimeoutW(window, WM_GETICON, IntPtr.Zero, IntPtr.Zero, 3, 1000, out ignored);
            borrowed = read != IntPtr.Zero ? ignored : IntPtr.Zero; source = "WM_GETICON-small";
        }
        if (borrowed == IntPtr.Zero) {
            borrowed = GetClassLongPtr(window, small ? -34 : -14);
            source = small ? "class-small" : "class-big";
        }
        if (borrowed == IntPtr.Zero) return;
        // WM_GETICON/class handles belong to the app. Only destroy our own copy.
        IntPtr owned = CopyIcon(borrowed);
        if (owned == IntPtr.Zero) throw new InvalidOperationException("WINDOW_ICON_COPY_FAILED");
        try { evidence.Icons.Add(Compare(owned, expectedPath, source)); }
        finally { DestroyIcon(owned); }
    }
    public static WindowEvidence Windows(uint pid, string expectedPath) {
        var evidence = new WindowEvidence();
        Exception failure = null;
        EnumProc callback = (window, state) => {
            uint owner;
            GetWindowThreadProcessId(window, out owner);
            if (owner != pid || !IsWindowVisible(window)) return true;
            try {
                evidence.VisibleWindows++;
                int previousIconCount = evidence.Icons.Count;
                WindowIcon(evidence, window, expectedPath, false);
                WindowIcon(evidence, window, expectedPath, true);
                if (evidence.Icons.Count == previousIconCount)
                    throw new InvalidOperationException("VISIBLE_WINDOW_ICON_UNAVAILABLE");
                return true;
            } catch (Exception error) { failure = error; return false; }
        };
        bool completed = EnumWindows(callback, IntPtr.Zero);
        GC.KeepAlive(callback);
        if (failure != null) throw failure;
        if (!completed) throw new InvalidOperationException("WINDOW_ENUMERATION_FAILED");
        return evidence;
    }
    public static int[] TaskbarBounds() {
        IntPtr window = FindWindowW("Shell_TrayWnd", null);
        if (window == IntPtr.Zero || !IsWindowVisible(window)) return null;
        WindowRect rectangle;
        if (!GetWindowRect(window, out rectangle)) throw new InvalidOperationException("TASKBAR_BOUNDS_FAILED");
        int width = rectangle.Right - rectangle.Left, height = rectangle.Bottom - rectangle.Top;
        if (width < 1 || height < 1 || width > 16384 || height > 16384 || (width > 512 && height > 512))
            throw new InvalidOperationException("TASKBAR_BOUNDS_INVALID");
        return new int[] { rectangle.Left, rectangle.Top, width, height };
    }
}
'@
    $Report.checks.nativeIconReader = 'passed'
    if ($ProcessId -gt 0) {
        $Stage = 'installedProcessBinding'
        $Process = $null
        try {
            $Process = [Diagnostics.Process]::GetProcessById($ProcessId)
            if ((Get-NormalizedPath $Process.MainModule.FileName) -ine $InstalledExe -or $Process.HasExited) {
                throw 'INSTALLED_PROCESS_MISMATCH'
            }
            $Report.checks.installedProcessBinding = 'passed'
            $Stage = 'visibleWindowIcons'
            $Evidence = [FundsTraceBrandIcons]::Windows([uint32]$ProcessId, $ExpectedIco)
            $Report.visibleWindowCount = $Evidence.VisibleWindows
            foreach ($Comparison in $Evidence.Icons) { $Report.iconComparisons.Add($Comparison) }
            if ($Evidence.VisibleWindows -lt 1 -or $Evidence.Icons.Count -lt 1 -or
                @($Evidence.Icons | Where-Object { -not $_.Matched }).Count -gt 0) { throw 'WINDOW_ICON_MISMATCH_OR_UNAVAILABLE' }
            $Report.checks.visibleWindowIcons = 'passed'
            if ($TaskbarScreenshot) {
                # Optional actual Shell pixels, distinct from the renderer screenshot
                # and from a successful HICON comparison. No activation or pinning.
                $Report.taskbarCapture = [ordered]@{ status = 'unavailable'; reason = 'SHELL_TASKBAR_UNAVAILABLE' }
                try {
                    $CapturePath = Get-NormalizedPath $TaskbarScreenshot
                    $AllowedOutput = $false
                    foreach ($Root in @($env:RUNNER_TEMP, $env:GITHUB_WORKSPACE)) {
                        if ($Root -and $CapturePath.StartsWith((Get-NormalizedPath $Root) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                            $AllowedOutput = $true
                        }
                    }
                    if (-not $AllowedOutput -or [IO.Path]::GetExtension($CapturePath) -ine '.png' -or
                        (Test-Path -LiteralPath $CapturePath)) { throw 'TASKBAR_OUTPUT_INVALID' }
                    $Bounds = [FundsTraceBrandIcons]::TaskbarBounds()
                    if ($null -ne $Bounds) {
                        Add-Type -AssemblyName System.Drawing.Common
                        $Bitmap = $null; $Graphics = $null; $Output = $null
                        try {
                            $Bitmap = [Drawing.Bitmap]::new($Bounds[2], $Bounds[3])
                            $Graphics = [Drawing.Graphics]::FromImage($Bitmap)
                            $Graphics.CopyFromScreen($Bounds[0], $Bounds[1], 0, 0, $Bitmap.Size)
                            $Output = [IO.File]::Open($CapturePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                            $Bitmap.Save($Output, [Drawing.Imaging.ImageFormat]::Png)
                        } finally {
                            if ($null -ne $Output) { $Output.Dispose() }
                            if ($null -ne $Graphics) { $Graphics.Dispose() }
                            if ($null -ne $Bitmap) { $Bitmap.Dispose() }
                        }
                        $Report.taskbarCapture = [ordered]@{
                            status = 'captured'; width = $Bounds[2]; height = $Bounds[3]
                            sha256 = (Get-FileHash -LiteralPath $CapturePath -Algorithm SHA256).Hash.ToLowerInvariant()
                            reviewed = $false
                        }
                    }
                } catch { $Report.taskbarCapture = [ordered]@{ status = 'unavailable'; reason = 'TASKBAR_CAPTURE_FAILED' } }
            }
        } finally { if ($null -ne $Process) { $Process.Dispose() } }
    } else {
        $Shell = $null
        try {
            $Stage = 'shortcutReader'
            $Shell = New-Object -ComObject WScript.Shell
            # Fixed product-name candidates only; no directory recursion or other shortcuts.
            $Locations = [ordered]@{
                desktopShortcut = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
                startMenuShortcut = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
            }
            foreach ($Name in $Locations.Keys) {
                $Stage = $Name
                $Directory = [string]$Locations[$Name]
                if (-not $Directory -or -not [IO.Path]::IsPathRooted($Directory)) { throw 'SHORTCUT_DIRECTORY_UNAVAILABLE' }
                $ShortcutPath = Join-Path $Directory 'FundsTrace.lnk'
                if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) { throw 'SHORTCUT_MISSING' }
                $Shortcut = $null
                try {
                    # CreateShortcut opens the existing link. Do not call Save or Resolve.
                    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
                    if ((Get-NormalizedPath ([string]$Shortcut.TargetPath)) -ine $InstalledExe) { throw 'SHORTCUT_TARGET_MISMATCH' }
                    $IconLocation = ([string]$Shortcut.IconLocation).Trim()
                    if ($IconLocation) {
                        $IconPath = $IconLocation; $IconIndex = 0
                        if ($IconLocation -match '^(.*),\s*(-?\d+)\s*$') { $IconPath = $Matches[1]; $IconIndex = [int]$Matches[2] }
                        if ($IconIndex -ne 0 -or (Get-NormalizedPath $IconPath) -ine $InstalledExe) { throw 'SHORTCUT_ICON_LOCATION_MISMATCH' }
                    }
                    foreach ($Small in @($false, $true)) {
                        $Comparison = [FundsTraceBrandIcons]::Shortcut($ShortcutPath, $ExpectedIco, $Small)
                        $Comparison.Source = $Name + '/' + $Comparison.Source
                        $Report.iconComparisons.Add($Comparison)
                        if (-not $Comparison.Matched) { throw 'SHORTCUT_ICON_PIXEL_MISMATCH' }
                    }
                    $Report.checks[$Name] = 'passed'
                } finally {
                    if ($null -ne $Shortcut) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shortcut) }
                }
            }
        } finally { if ($null -ne $Shell) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shell) } }
    }
} catch {
    $Failed = $true; $Report.failedCheck = $Stage
    # Publish only known codes. Native/COM exceptions may contain local paths.
    $KnownCodes = @('INVALID_PATH', 'MISSING_CANDIDATE_INPUT', 'INSTALLED_PROCESS_MISMATCH',
        'SHORTCUT_DIRECTORY_UNAVAILABLE', 'SHORTCUT_MISSING', 'SHORTCUT_TARGET_MISMATCH',
        'SHORTCUT_ICON_LOCATION_MISMATCH', 'SHORTCUT_ICON_PIXEL_MISMATCH',
        'WINDOW_ICON_MISMATCH_OR_UNAVAILABLE', 'ICON_INFO_FAILED', 'ICON_BITMAP_FAILED',
        'ICON_DIMENSIONS_INVALID', 'ICON_DC_FAILED', 'ICON_DIB_FAILED', 'ICON_SELECT_FAILED',
        'ICON_DRAW_FAILED', 'ICON_FLUSH_FAILED', 'EXPECTED_ICON_LOAD_FAILED',
        'SHORTCUT_ICON_FAILED', 'WINDOW_ICON_COPY_FAILED', 'VISIBLE_WINDOW_ICON_UNAVAILABLE',
        'WINDOW_ENUMERATION_FAILED')
    $Report.failureCode = 'UNCLASSIFIED'
    $Cause = $_.Exception
    while ($null -ne $Cause) {
        if ($Cause.Message -cin $KnownCodes) { $Report.failureCode = $Cause.Message; break }
        $Cause = $Cause.InnerException
    }
    [Console]::Error.WriteLine("Brand icon verification failed at $Stage.")
} finally {
    $Report.status = $(if ($Failed) { 'failed' } else { 'passed' })
    $Report | ConvertTo-Json -Depth 8
}
if ($Failed) { exit 1 }
