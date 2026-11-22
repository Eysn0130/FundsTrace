"""Exercise Windows import job ownership using only the standard library."""

from contextlib import ExitStack
import ctypes
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

if sys.platform == "win32":
    import _winapi


def _write_pid(directory, name):
    pending = directory / (name + ".tmp")
    pending.write_text(str(os.getpid()), encoding="ascii")
    pending.replace(directory / name)


def _hold_source(directory):
    with (directory / "private-source.pdf").open("wb"):
        _write_pid(directory, "descendant.pid")
        time.sleep(60)


def _spawn_descendant(directory):
    child = multiprocessing.get_context("spawn").Process(target=_hold_source, args=(directory,))
    child.start()
    _write_pid(directory, "initial.pid")
    child.join()


def _terminate_bystander(child):
    if child.poll() is None:
        child.terminate()
    child.wait(timeout=5)


@unittest.skipUnless(sys.platform == "win32", "requires native Windows Job Objects")
class WindowsImportJobTests(unittest.TestCase):
    def setUp(self):
        from app import windows_import_job

        self.jobs = windows_import_job
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.directory = root / "识别 测试"
        self.directory.mkdir()
        self.env = {"SystemRoot": os.environ["SystemRoot"], "FT_JOB_SAMPLE": "合成记录"}

    def command(self, mode):
        return [sys.executable, "-I", str(Path(__file__).resolve()), mode, str(self.directory)]

    def start(self, mode):
        child = self.jobs.start(self.command(mode), cwd=self.directory, env=self.env)
        self.addCleanup(child.stop)
        return child

    def test_stop_releases_the_initial_process_and_spawned_descendant(self):
        bystander = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(90)"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(_terminate_bystander, bystander)
        child = self.start("--job-tree")
        markers = [self.directory / name for name in ("initial.pid", "descendant.pid")]
        deadline = time.monotonic() + 15
        while not all(marker.is_file() for marker in markers):
            self.assertIsNone(child.poll(), "fixture tree exited before reporting both processes")
            self.assertLess(time.monotonic(), deadline, "fixture tree did not report both processes")
            time.sleep(.025)

        with ExitStack() as ownership:
            handles = []
            for marker in markers:
                pid = int(marker.read_text(encoding="ascii"))
                handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
                ownership.callback(_winapi.CloseHandle, handle)
                handles.append(handle)
                self.assertEqual(_winapi.WaitForSingleObject(handle, 0), _winapi.WAIT_TIMEOUT)
            with self.assertRaises(subprocess.TimeoutExpired):
                child.wait(timeout=0)
            child.stop()
            child.stop()
            self.assertIsNotNone(child.poll())
            self.assertIsNone(bystander.poll())
            for handle in handles:
                self.assertEqual(_winapi.WaitForSingleObject(handle, 0), _winapi.WAIT_OBJECT_0)
        (self.directory / "private-source.pdf").unlink()

    def test_normal_exit_preserves_the_exit_code_and_uses_explicit_stdio_and_environment(self):
        with patch.dict(os.environ, {"FT_JOB_PARENT_ONLY": "synthetic parent value"}):
            child = self.start("--job-exit")
        # 259 is also the Win32 STILL_ACTIVE value for a running process.
        self.assertEqual(child.wait(timeout=10), 259)
        self.assertEqual(child.poll(), 259)
        child.stop()
        self.assertEqual(child.poll(), 259)

    def test_assignment_failure_reaps_the_unstarted_process(self):
        original = _winapi.CreateProcess
        handles = []
        with ExitStack() as ownership:
            def create(*args):
                result = original(*args)
                current = _winapi.GetCurrentProcess()
                handle = _winapi.DuplicateHandle(current, result[0], current, _winapi.SYNCHRONIZE, False)
                ownership.callback(_winapi.CloseHandle, handle)
                handles.append(handle)
                return result

            def reject(*_args):
                ctypes.set_last_error(5)
                return 0

            ownership.enter_context(patch.object(_winapi, "CreateProcess", create))
            ownership.enter_context(patch.object(self.jobs._kernel, "AssignProcessToJobObject", reject))
            resume = ownership.enter_context(patch.object(self.jobs._kernel, "ResumeThread"))
            with self.assertRaises(OSError):
                self.start("--job-tree")
            resume.assert_not_called()
            [handle] = handles
            self.assertEqual(_winapi.WaitForSingleObject(handle, 0), _winapi.WAIT_OBJECT_0)
            self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == "__main__":
    if sys.argv[1:2] == ["--job-tree"]:
        _spawn_descendant(Path(sys.argv[2]))
    elif sys.argv[1:2] == ["--job-exit"]:
        assert Path.cwd() == Path(sys.argv[2])
        assert os.environ["FT_JOB_SAMPLE"] == "合成记录"
        assert "FT_JOB_PARENT_ONLY" not in os.environ
        assert sys.stdin.read(1) == ""
        print("synthetic stdout")
        print("synthetic stderr", file=sys.stderr)
        raise SystemExit(259)
    else:
        unittest.main()
