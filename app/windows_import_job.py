"""Own a Windows import tool and its subprocesses for one worker."""

from contextlib import ExitStack
import math
import os
from pathlib import Path
import subprocess
import sys
import time


_CREATE_SUSPENDED = 0x00000004
_KILL_ON_JOB_CLOSE = 0x00002000
_BASIC_ACCOUNTING_INFORMATION = 1
_EXTENDED_LIMIT_INFORMATION = 9
_RESUME_FAILED = 0xFFFFFFFF
_TERMINATED_EXIT_CODE = 1
_STOP_SECONDS = 2.0

if sys.platform == "win32":
    import _winapi
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _kernel.SetInformationJobObject.restype = wintypes.BOOL
    _kernel.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    _kernel.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel.ResumeThread.restype = wintypes.DWORD
    _kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel.TerminateJobObject.restype = wintypes.BOOL


def _check(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


def _active_processes(job):
    accounting = _BasicAccountingInformation()
    _check(_kernel.QueryInformationJobObject(job, _BASIC_ACCOUNTING_INFORMATION,
                                            ctypes.byref(accounting), ctypes.sizeof(accounting), None))
    return accounting.ActiveProcesses


class _ImportJob:
    def __init__(self, command, job, process):
        self._command = command
        self._job = job
        self._process = process
        self.returncode = None

    def _collect(self, milliseconds):
        if (self.returncode is None and self._process is not None
                and _winapi.WaitForSingleObject(self._process, milliseconds) == _winapi.WAIT_OBJECT_0):
            self.returncode = _winapi.GetExitCodeProcess(self._process)
        return self.returncode

    def poll(self):
        return self._collect(0)

    def wait(self, timeout=None):
        milliseconds = (_winapi.INFINITE if timeout is None
                        else min(_winapi.INFINITE - 1, math.ceil(max(0, timeout) * 1000)))
        code = self._collect(milliseconds)
        if code is None:
            raise subprocess.TimeoutExpired(self._command, timeout)
        return code

    def stop(self, timeout=_STOP_SECONDS):
        """End and reap the tree. Closing the Job also kills it after errors."""
        if self._job is None:
            return
        job = self._job
        deadline = time.monotonic() + timeout
        try:
            if _active_processes(job):
                _check(_kernel.TerminateJobObject(job, _TERMINATED_EXIT_CODE))
                while _active_processes(job):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(self._command, timeout)
                    time.sleep(min(0.01, remaining))
            self.wait(max(0, deadline - time.monotonic()))
        finally:
            process = self._process
            self._job = self._process = None
            try:
                _winapi.CloseHandle(job)
            finally:
                _winapi.CloseHandle(process)


def _abort_start(job, process):
    with ExitStack() as cleanup:
        if process is not None:
            cleanup.callback(_winapi.CloseHandle, process)
            cleanup.callback(_winapi.WaitForSingleObject, process, int(_STOP_SECONDS * 1000))
        cleanup.callback(_winapi.CloseHandle, job)
        # Assignment may have failed, leaving a suspended process outside this Job.
        if process is not None and _winapi.WaitForSingleObject(process, 0) == _winapi.WAIT_TIMEOUT:
            try:
                _winapi.TerminateProcess(process, _TERMINATED_EXIT_CODE)
            except PermissionError:
                if _winapi.WaitForSingleObject(process, 0) != _winapi.WAIT_OBJECT_0:
                    raise


def start(command, *, cwd, env):
    """Launch an explicit Windows executable. The caller owns stop() in finally."""
    if sys.platform != "win32":
        raise OSError("Windows import jobs require Windows")
    command = tuple(os.fspath(argument) for argument in command)
    executable = Path(command[0])
    if not executable.is_absolute() or executable.suffix.lower() != ".exe":
        raise OSError("An absolute Windows executable path is required")

    job = _check(_kernel.CreateJobObjectW(None, None))
    process = None
    try:
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        _check(_kernel.SetInformationJobObject(job, _EXTENDED_LIMIT_INFORMATION,
                                              ctypes.byref(limits), ctypes.sizeof(limits)))
        with ExitStack() as setup:
            stream = setup.enter_context(open(os.devnull, "r+b", buffering=0))
            sink = msvcrt.get_osfhandle(stream.fileno())
            os.set_handle_inheritable(sink, True)
            startup = subprocess.STARTUPINFO(dwFlags=_winapi.STARTF_USESTDHANDLES, hStdInput=sink,
                                             hStdOutput=sink, hStdError=sink,
                                             lpAttributeList={"handle_list": [sink]})
            # CPython marshals the explicit environment as UTF-16 for CreateProcessW.
            process, thread, _, _ = _winapi.CreateProcess(
                command[0], subprocess.list2cmdline(command), None, None, True,
                _CREATE_SUSPENDED | _winapi.CREATE_NO_WINDOW, dict(env), os.fspath(cwd), startup,
            )
            setup.callback(_winapi.CloseHandle, thread)
            _check(_kernel.AssignProcessToJobObject(job, process))
            owned = _ImportJob(command, job, process)
            suspended = _kernel.ResumeThread(thread)
            if suspended == _RESUME_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
            if suspended != 1:
                raise OSError("Unexpected initial Windows thread suspend count")
        return owned
    except BaseException:
        _abort_start(job, process)
        raise
