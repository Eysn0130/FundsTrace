#Requires -Version 7.0
param([Parameter(Mandatory=$true)][int]$ApplicationPid,
    [Parameter(Mandatory=$true)][ValidateSet('later','restart')][string]$Choice)
$ErrorActionPreference = 'Stop'
$Stage = 'platform'
$Status = 'running'
$NativeReady = $false
function Write-SafeDiagnostic {
    $Record = [ordered]@{ schemaVersion = 1; choice = $Choice; stage = $Stage; status = $Status
        attempts = 0; ownedVisibleDialogCount = 0; matchingDialogCount = 0; matchingButtonCount = 0
        processAlive = $false; dialogSeen = $false; buttonSeen = $false; sendAttempted = $false
        sendMessageCompleted = $false; dialogClosedObserved = $false
        dialogVisible = $null; dialogEnabled = $null; buttonVisible = $null; buttonEnabled = $null; foregroundIsDialog = $null }
    try {
        $Application = [System.Diagnostics.Process]::GetProcessById($ApplicationPid)
        $Record.processAlive = -not $Application.HasExited
        $Application.Dispose()
    } catch { }
    if ($NativeReady) {
        foreach ($Name in @('attempts', 'ownedVisibleDialogCount', 'matchingDialogCount', 'matchingButtonCount',
            'dialogSeen', 'buttonSeen', 'sendAttempted', 'sendMessageCompleted', 'dialogClosedObserved',
            'dialogVisible', 'dialogEnabled', 'buttonVisible', 'buttonEnabled', 'foregroundIsDialog')) {
            $Record[$Name] = [FundsUpdateDialog]::$Name
        }
    }
    # Only this fixed projection leaves the helper; never emit native text or exceptions.
    [Console]::Out.WriteLine('FT_UPDATE_DIALOG ' + ($Record | ConvertTo-Json -Compress))
}
Write-SafeDiagnostic
try {
    if (-not $IsWindows -or $env:GITHUB_ACTIONS -cne 'true') { throw 'Disposable Windows CI only.' }
    $Stage = 'nativeHelper'
    Write-SafeDiagnostic
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
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder text, int n);
  [DllImport("user32.dll")] static extern IntPtr SendMessageTimeout(IntPtr h, uint msg, IntPtr w, IntPtr l, uint flags, uint timeout, out IntPtr result);
  [DllImport("user32.dll")] static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  public static int attempts, ownedVisibleDialogCount, matchingDialogCount, matchingButtonCount;
  public static bool dialogSeen, buttonSeen, sendAttempted, sendMessageCompleted, dialogClosedObserved;
  public static bool? dialogVisible, dialogEnabled, buttonVisible, buttonEnabled, foregroundIsDialog;
  public static string Stage = "waitDialog";
  static string Text(IntPtr h) { var b=new StringBuilder(256); GetWindowText(h,b,b.Capacity); return b.ToString(); }
  public static bool Closed(IntPtr h) { dialogClosedObserved=!IsWindow(h); return dialogClosedObserved; }
  public static IntPtr Click(uint pid, string button) {
    attempts++; ownedVisibleDialogCount=0; matchingDialogCount=0; matchingButtonCount=0; Stage="waitDialog";
    IntPtr dialog=IntPtr.Zero, target=IntPtr.Zero;
    EnumWindows((h,p)=>{ uint owner; GetWindowThreadProcessId(h,out owner); if(owner==pid) {
      var name=new StringBuilder(256); GetClassName(h,name,name.Capacity);
      if(IsWindowVisible(h) && name.ToString()=="#32770") ownedVisibleDialogCount++;
      if(Text(h)=="重启更新 FundsTrace") { matchingDialogCount++; dialog=h; }
    } return true; },IntPtr.Zero);
    if(dialog==IntPtr.Zero) return IntPtr.Zero;
    dialogSeen=true; dialogVisible=IsWindowVisible(dialog); dialogEnabled=IsWindowEnabled(dialog);
    foregroundIsDialog=GetForegroundWindow()==dialog; Stage="findButton";
    EnumChildWindows(dialog,(h,p)=>{ if(Text(h).Replace("&","").Trim()==button) { matchingButtonCount++; target=h; } return true; },IntPtr.Zero);
    if(target==IntPtr.Zero) return IntPtr.Zero;
    buttonSeen=true; buttonVisible=IsWindowVisible(target); buttonEnabled=IsWindowEnabled(target);
    sendAttempted=true; Stage="sendMessage";
    IntPtr result;
    if(SendMessageTimeout(target,0x00F5,IntPtr.Zero,IntPtr.Zero,2,2000,out result)==IntPtr.Zero) return IntPtr.Zero;
    sendMessageCompleted=true; Stage="waitClosed"; return dialog;
  }
}
'@
    $NativeReady = $true
    $Label = $(if ($Choice -eq 'later') { '稍后' } else { '重启更新' })
    $Deadline = [DateTime]::UtcNow.AddSeconds(20)
    $Clicked = [IntPtr]::Zero
    do {
        if ($Clicked -eq [IntPtr]::Zero) { $Clicked = [FundsUpdateDialog]::Click([uint32]$ApplicationPid, $Label) }
        $LastStage = $Stage
        $Stage = [FundsUpdateDialog]::Stage
        if ($Clicked -ne [IntPtr]::Zero -and [FundsUpdateDialog]::Closed($Clicked)) {
            $Stage = 'completed'; $Status = 'passed'; Write-SafeDiagnostic; exit 0
        }
        if ($Stage -ne $LastStage) { Write-SafeDiagnostic }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $Deadline)
    $Status = 'failed'; Write-SafeDiagnostic; exit 1
} catch {
    $Status = 'failed'; Write-SafeDiagnostic; exit 1
}
