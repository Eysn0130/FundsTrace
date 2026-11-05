"""Synthetic guard faults must retain evidence without bypassing denied signals."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

import pytest

from scripts import validation_guard as guard


class Clock:
    now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def synthetic(monkeypatch, tmp_path, *, end=.5, start=0, signal_error=None):
    clock = Clock()
    calls = []

    class Child:
        pid = 999999
        returncode = None

        def poll(self):
            if clock.now >= end and self.returncode is None:
                self.returncode = 0
            return self.returncode

        def wait(self, timeout=None):
            if self.poll() is None:
                clock.sleep(timeout)
            if self.poll() is None:
                raise subprocess.TimeoutExpired('synthetic', timeout)
            return self.returncode

    child = Child()
    run_root = tmp_path / 'run'
    report_path = tmp_path / 'report.json'

    def popen(command, **kwargs):
        assert kwargs['start_new_session'] is True
        assert Path(kwargs['env']['TMPDIR']).parent == run_root
        clock.now = start
        (run_root / 'safe-result.json').write_text(json.dumps({
            'completed': True, 'actual_lifetime_peak_MiB': 1250,
        }))
        return child

    def killpg(pid, sig):
        assert pid == child.pid
        calls.append(sig)
        if signal_error:
            raise signal_error('synthetic private message must never be serialized')
        child.returncode = -sig

    def rss(pid):
        assert pid == child.pid
        return (1234, 1200, [pid]) if child.poll() is None else (0, 0, [])

    monkeypatch.setattr(guard.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(guard.time, 'sleep', clock.sleep)
    monkeypatch.setattr(guard.subprocess, 'Popen', popen)
    monkeypatch.setattr(guard.os, 'killpg', killpg)
    monkeypatch.setattr(guard, 'process_rss', rss)
    monkeypatch.setattr(guard, 'disk_bytes', lambda *args: 987654)
    return clock, child, calls, run_root, report_path


def test_signal_denial_preserves_peaks_and_bounded_incomplete_cleanup(monkeypatch, tmp_path):
    clock, child, calls, root, path = synthetic(
        monkeypatch, tmp_path, start=600, end=10000, signal_error=PermissionError,
    )
    result = guard.run_guarded(['synthetic'], root, path)
    saved = json.loads(path.read_text())
    assert saved == result
    assert result['finalized'] and not result['completed']
    assert result['abort_reason'] == 'deadline'
    assert result['sampled_process_tree_peak_MiB'] == 1234
    assert result['sampled_disk_peak_bytes'] == 987654
    assert result['cleanup_complete'] is False
    assert result['exit_code'] is None
    assert result['errors'] == [{'phase': 'termination', 'error_class': 'PermissionError'}]
    assert calls == [signal.SIGTERM]  # Never retry via SIGKILL after denial.
    assert 602 <= clock.now < 603
    assert 'synthetic private message' not in path.read_text()


def test_denial_can_observe_natural_exit_without_retry(monkeypatch, tmp_path):
    _, _, calls, root, path = synthetic(
        monkeypatch, tmp_path, start=600, end=601, signal_error=PermissionError,
    )
    result = guard.run_guarded(['synthetic'], root, path)
    assert result['cleanup_complete'] is True
    assert result['exit_code'] == 0
    assert not result['completed']
    assert calls == [signal.SIGTERM]


def test_success_does_not_upgrade_report_time_peak_to_final_tree_peak(monkeypatch, tmp_path):
    _, _, calls, root, path = synthetic(monkeypatch, tmp_path)
    result = guard.run_guarded(['synthetic'], root, path)
    assert result['completed'] is True
    assert result['monitor_complete'] and result['cleanup_complete']
    assert result['worker_last_report_peak_MiB'] == 1250
    assert result['worker_peak_is_final'] is False
    assert result['worker_peak_scope'] == 'single_process_high_water_at_worker_report'
    assert calls == []
    assert result['sampled_process_tree_peak_MiB'] == 1234


@pytest.mark.parametrize('fault', ['monitor', 'final_probe', 'worker_report'])
def test_late_errors_still_save_final_report(monkeypatch, tmp_path, fault):
    _, child, _, root, path = synthetic(monkeypatch, tmp_path)
    original_rss, original_disk = guard.process_rss, guard.disk_bytes
    rss_calls = 0
    disk_calls = 0

    def rss(pid):
        nonlocal rss_calls
        rss_calls += 1
        if fault == 'monitor' and rss_calls == 2:
            raise RuntimeError('private probe detail')
        return original_rss(pid)

    def disk(*args):
        nonlocal disk_calls
        disk_calls += 1
        if fault == 'final_probe' and disk_calls == 2:
            raise PermissionError('private probe detail')
        if fault == 'worker_report':
            (root / 'safe-result.json').write_text('{broken')
        return original_disk(*args)

    monkeypatch.setattr(guard, 'process_rss', rss)
    monkeypatch.setattr(guard, 'disk_bytes', disk)
    result = guard.run_guarded(['synthetic'], root, path)
    assert json.loads(path.read_text()) == result
    assert result['finalized'] and not result['completed']
    assert result['sampled_process_tree_peak_MiB'] == 1234
    assert result['sampled_disk_peak_bytes'] == 987654
    assert child.poll() is not None
    assert result['errors']
    assert 'private probe detail' not in path.read_text()


def test_exit_between_probe_and_signal_is_not_a_lost_report(monkeypatch, tmp_path):
    _, child, _, root, path = synthetic(monkeypatch, tmp_path, start=600, end=10000)

    def exit_race(pid, sig):
        child.returncode = 0
        raise ProcessLookupError('already gone')

    monkeypatch.setattr(guard.os, 'killpg', exit_race)
    result = guard.run_guarded(['synthetic'], root, path)
    assert json.loads(path.read_text()) == result
    assert result['termination'] == 'exit_race'
    assert result['cleanup_complete'] is True
    assert result['errors'] == []
    assert result['abort_reason'] == 'deadline'


def test_persistent_probe_failure_still_terminates_known_owned_worker(monkeypatch, tmp_path):
    _, child, calls, root, path = synthetic(monkeypatch, tmp_path, end=10000)
    original = guard.process_rss
    count = 0

    def failed_monitor(pid):
        nonlocal count
        count += 1
        if count > 1:
            raise RuntimeError('persistent synthetic probe failure')
        return original(pid)

    monkeypatch.setattr(guard, 'process_rss', failed_monitor)
    result = guard.run_guarded(['synthetic'], root, path)
    assert json.loads(path.read_text()) == result
    assert calls == [signal.SIGTERM]
    assert child.poll() == -signal.SIGTERM
    assert result['sampled_process_tree_peak_MiB'] == 1234
    assert result['sampled_disk_peak_bytes'] == 987654
    assert result['remaining_process_ids'] is None  # A failed probe is not zero.
    assert result['cleanup_complete'] is False  # Descendants remain unverified.
    assert result['completed'] is False


def test_open_deleted_files_deduplicate_by_inode_not_name(monkeypatch, tmp_path):
    name = str(tmp_path / 'same-name')
    listing = (f'p123\0\nf3\0tREG\0D0x1\0i21\0s100\0n{name}\0'
               f'\nf4\0tREG\0D0x1\0i21\0s100\0n{name}\0'
               f'\nf5\0tREG\0D0x1\0i22\0s200\0n{name}\0').encode()
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, listing, b''))
    assert guard.disk_bytes(tmp_path, [123]) == 300


def test_descriptor_permission_failure_is_not_zero_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, b'', b'Permission denied'))
    with pytest.raises(RuntimeError):
        guard.disk_bytes(tmp_path, [123])


def test_real_subprocess_finishes_and_leaves_a_private_report(tmp_path):
    if sys.platform == 'win32':
        pytest.skip('POSIX guard; Windows qualification uses its own process tooling')
    code = (
        'import json,os,pathlib,resource,sys,time;time.sleep(.3);'
        'root=pathlib.Path(os.environ["TMPDIR"]).parent;'
        'peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=="darwin" else 1024);'
        '(root/"safe-result.json").write_text(json.dumps({"completed":True,"actual_lifetime_peak_MiB":peak}))'
    )
    path = tmp_path / 'report.json'
    result = guard.run_guarded([sys.executable, '-c', code], tmp_path / 'run', path)
    assert result['completed'] is True, result
    assert result['exit_code'] == 0
    assert result['cleanup_complete']
    assert path.stat().st_mode & 0o777 == 0o600


def test_real_open_anonymous_file_is_counted(tmp_path):
    if sys.platform == 'win32' or not shutil.which('lsof'):
        pytest.skip('POSIX lsof probe is unavailable')
    with tempfile.TemporaryFile(dir=tmp_path) as handle:
        handle.write(b'x' * (2 * 1024**2))
        handle.flush()
        # Directory-only accounting cannot observe this still-open file.
        assert guard.disk_bytes(tmp_path) == 0
        assert guard.disk_bytes(tmp_path, [os.getpid()]) >= 2 * 1024**2
