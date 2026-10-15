; Adapted from Analytix build/standard-win-installer.nsh.
; Copyright 2026 Guoqin He (GitHub: Eysn0130)
; Licensed under the Apache License, Version 2.0. See licenses/Apache-2.0.txt.
; FundsTrace modifications (2026-10-15): retain the per-user identity and normal
; NSIS UI, report the installed version, and replace forced process termination
; with install-directory-scoped normal window closing and a retry/cancel gate.

!include "LogicLib.nsh"
!include "FileFunc.nsh"

!ifndef BUILD_UNINSTALLER
  Var FundsTraceInstalledVersion
  Var FundsTraceWelcomeText
!endif
Var FundsTraceInstallerCheckRoot
Var FundsTraceInstallerCheckedRegisteredRoot
Var FundsTraceInstallerStopResult
Var FundsTraceInstallerFailureMessage

!macro customInit
  ${if} ${isForAllUsers}
    DetailPrint "FundsTrace supports installation for the current user only."
    SetErrorLevel 2
    Quit
  ${endif}
  !insertmacro setInstallModePerUser
  ReadRegStr $FundsTraceInstalledVersion HKCU "${UNINSTALL_REGISTRY_KEY}" DisplayVersion
  ${if} $FundsTraceInstalledVersion == ""
    StrCpy $FundsTraceWelcomeText "Install FundsTrace ${VERSION} for the current Windows user.$\r$\n$\r$\nCase data is stored separately in your AppData folder. Uninstalling retains it by default."
  ${else}
    StrCpy $FundsTraceWelcomeText "FundsTrace $FundsTraceInstalledVersion is installed for this Windows user.$\r$\n$\r$\nContinue to replace it with FundsTrace ${VERSION}. Close any active work before continuing. Your case data and settings are retained."
    DetailPrint "Detected FundsTrace $FundsTraceInstalledVersion; installing ${VERSION}."
  ${endif}
!macroend

!macro customWelcomePage
  !insertmacro skipPageIfUpdated
  !define MUI_WELCOMEPAGE_TITLE "FundsTrace ${VERSION}"
  !define MUI_WELCOMEPAGE_TEXT "$FundsTraceWelcomeText"
  !insertmacro MUI_PAGE_WELCOME
!macroend

; The assisted template calls this for both installer and uninstaller UI.
!macro customInstallMode
  StrCpy $isForceCurrentInstall "1"
  StrCpy $isForceMachineInstall "0"
!macroend

!macro customUnInit
  ${if} ${isForAllUsers}
    DetailPrint "FundsTrace supports uninstallation for the current user only."
    SetErrorLevel 2
    Quit
  ${endif}
  !insertmacro setInstallModePerUser
!macroend

; electron-builder 26.15.3 inserts this before replacing an old installation,
; and in un.checkAppRunning before an interactive or silent uninstall removes
; files. The uninstaller process itself is excluded by PID, never by image name.
!macro customCheckAppRunning
  InitPluginsDir
  File /oname=$PLUGINSDIR\funds-trace-installer-processes.ps1 "${PROJECT_DIR}\installer-processes.ps1"
  ; The default NSIS process is 32-bit. Native PowerShell can inspect the x64
  ; Electron process Path before sending it a normal window-close request.
  IfFileExists "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe" 0 +2
    StrCpy $PowerShellPath "$WINDIR\Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  System::Call 'kernel32::GetCurrentProcessId() i .r0'
  System::Call 'kernel32::SetEnvironmentVariable(t, t)i ("FUNDS_TRACE_INSTALLER_SELF_PID", "$0").r1'
  StrCpy $FundsTraceInstallerCheckRoot $INSTDIR
  StrCpy $FundsTraceInstallerCheckedRegisteredRoot "0"
  !ifdef BUILD_UNINSTALLER
    StrCpy $FundsTraceInstallerFailureMessage "FundsTrace is still running, or its process state could not be checked. Finish active work and close FundsTrace, then choose Retry. Cancel leaves the installation unchanged."
  !else
    StrCpy $FundsTraceInstallerFailureMessage "Close FundsTrace before retrying. If this installer EXE is inside the selected or existing installation directory, cancel and move the EXE outside that directory before running it again. Process state may also be unavailable. Cancel leaves the installation unchanged."
  !endif

  FundsTraceCheckInstallDirectory:
    System::Call 'kernel32::SetEnvironmentVariable(t, t)i ("FUNDS_TRACE_INSTALLER_APP_ROOT", "$FundsTraceInstallerCheckRoot").r0'
    DetailPrint "Waiting for FundsTrace to close normally."
    !ifdef BUILD_UNINSTALLER
      nsExec::Exec /TIMEOUT=40000 `"$PowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\funds-trace-installer-processes.ps1"`
    !else
      nsExec::Exec /TIMEOUT=40000 `"$PowerShellPath" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\funds-trace-installer-processes.ps1" -RejectInstallerInInstallDirectory`
    !endif
    Pop $FundsTraceInstallerStopResult
    ${if} $FundsTraceInstallerStopResult != 0
      ${if} ${Silent}
        DetailPrint $FundsTraceInstallerFailureMessage
        SetErrorLevel 2
        Quit
      ${endif}
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$FundsTraceInstallerFailureMessage" /SD IDCANCEL IDRETRY FundsTraceCheckInstallDirectory
      SetErrorLevel 2
      Quit
    ${endif}

  !ifndef BUILD_UNINSTALLER
    ; The directory page can select a new destination. Check the registered old
    ; location too, before the template starts even a historical uninstaller.
    ${if} $FundsTraceInstallerCheckedRegisteredRoot == "0"
      StrCpy $FundsTraceInstallerCheckedRegisteredRoot "1"
      ReadRegStr $0 HKCU "${INSTALL_REGISTRY_KEY}" InstallLocation
      ${if} $0 != ""
      ${andIf} $0 != $FundsTraceInstallerCheckRoot
        StrCpy $FundsTraceInstallerCheckRoot $0
        Goto FundsTraceCheckInstallDirectory
      ${endif}
    ${endif}
  !endif
!macroend
