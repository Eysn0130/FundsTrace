#Requires -Version 7.0
param([Parameter(Mandatory=$true)][int]$ApplicationPid,
    [Parameter(Mandatory=$true)][ValidateSet('later','restart')][string]$Choice)
$ErrorActionPreference = 'Stop'
if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true') { throw 'Disposable Windows CI only.' }
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class FundsUpdateDialog {
  delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr p);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder text, int n);
  [DllImport("user32.dll")] static extern IntPtr SendMessageTimeout(IntPtr h, uint msg, IntPtr w, IntPtr l, uint flags, uint timeout, out IntPtr result);
  [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
  static string Text(IntPtr h) { var b=new StringBuilder(256); GetWindowText(h,b,b.Capacity); return b.ToString(); }
  public static bool Closed(IntPtr h) { return !IsWindow(h); }
  public static IntPtr Click(uint pid, string button) {
    IntPtr dialog=IntPtr.Zero, target=IntPtr.Zero;
    EnumWindows((h,p)=>{ uint owner; GetWindowThreadProcessId(h,out owner); if(owner==pid && Text(h)=="重启更新 FundsTrace") dialog=h; return true; },IntPtr.Zero);
    if(dialog==IntPtr.Zero) return IntPtr.Zero;
    EnumChildWindows(dialog,(h,p)=>{ if(Text(h).Replace("&","").Trim()==button) target=h; return true; },IntPtr.Zero);
    if(target==IntPtr.Zero) return IntPtr.Zero;
    IntPtr result;
    return SendMessageTimeout(target,0x00F5,IntPtr.Zero,IntPtr.Zero,2,2000,out result)!=IntPtr.Zero ? dialog : IntPtr.Zero;
  }
}
'@
$Label = $(if ($Choice -eq 'later') { '稍后' } else { '重启更新' })
$Deadline = [DateTime]::UtcNow.AddSeconds(20)
$Clicked = [IntPtr]::Zero
do {
    if ($Clicked -eq [IntPtr]::Zero) { $Clicked = [FundsUpdateDialog]::Click([uint32]$ApplicationPid, $Label) }
    if ($Clicked -ne [IntPtr]::Zero -and [FundsUpdateDialog]::Closed($Clicked)) { exit 0 }
    Start-Sleep -Milliseconds 100
} while ([DateTime]::UtcNow -lt $Deadline)
[Console]::Error.WriteLine('Owned update confirmation was not available.')
exit 1
