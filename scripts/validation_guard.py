"""Fixed-budget local validation guard; reports aggregates, never command text.

The caller owns input isolation and authorization. This module neither deletes
the run directory nor retries a denied signal through another mechanism.
"""
from pathlib import Path
import json
import math
import os
import signal
import subprocess
import time


RSS_LIMIT_MIB = 4096
DISK_LIMIT_BYTES = 12 * 1024**3
DEADLINE_SECONDS = 600


def process_rss(pid):
    rows = subprocess.check_output(
        ['ps', '-axo', 'pid=,ppid=,pgid=,rss='], text=True, timeout=3,
    )
    table = {}
    for line in rows.splitlines():
        fields = line.split()
        if len(fields) == 4:
            process, parent, group, kib = map(int, fields)
            table[process] = (parent, group, kib)
    # A worker starts a fresh session. Include its remaining group members even
    # after the leader exits and the operating system reparents its children.
    ids = {pid} | {process for process, (_, group, _) in table.items() if group == pid}
    while True:
        addition = {process for process, (parent, _, _) in table.items() if parent in ids}
        if addition <= ids:
            break
        ids |= addition
    live = sorted(ids & table.keys())
    values = [table[process][2] / 1024 for process in live]
    return sum(values), max(values, default=0), live


def disk_bytes(root, process_ids=()):
    sizes = {}

    def fail(error):
        raise error

    for directory, _, files in os.walk(root, onerror=fail):
        for name in files:
            try:
                stat = os.stat(Path(directory) / name, follow_symlinks=False)
            except FileNotFoundError:
                continue  # A temporary file disappeared during the sample.
            sizes[(stat.st_dev, stat.st_ino)] = max(stat.st_size, getattr(stat, 'st_blocks', 0) * 512)
    if process_ids:
        query = subprocess.run(
            ['lsof', '-nP', '-a', '-p', ','.join(map(str, process_ids)), '+L1', '-F0fDinst'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3,
        )
        if query.returncode not in (0, 1) or query.stderr:
            raise RuntimeError('temporary_descriptor_monitor_failed')
        prefix = str(Path(root).resolve()) + os.sep
        entry = {}

        def collect():
            name = entry.get('n', '').removesuffix(' (deleted)')
            if entry.get('t') != 'REG' or not name.startswith(prefix):
                return
            # Several descriptors can refer to one file. Conversely, distinct
            # unlinked files may have the same name; never deduplicate by name.
            key = (int(entry['D'], 16), int(entry['i']))
            sizes[key] = max(sizes.get(key, 0), int(entry['s']))

        for part in query.stdout.decode('utf-8', 'replace').split('\0'):
            part = part.lstrip('\n')
            if not part:
                continue
            if part[0] in ('f', 'p'):
                collect()
                entry = {}
            entry[part[0]] = part[1:]
        collect()
    return sum(sizes.values())


def _save_report(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _wait_owned(child, timeout):
    end = time.monotonic() + timeout
    while True:
        exited = child.poll() is not None
        _, _, live = process_rss(child.pid)
        if exited and not live:
            return True
        remaining = end - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(.05, remaining))


def _stop_owned(child, report, error):
    """Only the fresh worker group; every wait is bounded, including denial."""
    try:
        if child.poll() is not None:
            if _wait_owned(child, 0):
                report['termination'] = 'already_exited'
                return True
            if report['abort_reason'] is None:
                report['abort_reason'] = 'worker_descendants_survived'
        # Popen still owns an unreaped, live leader. A failed ps probe must not
        # prevent the bounded termination attempt on its freshly created group.
        try:
            os.killpg(child.pid, signal.SIGTERM)
            report['termination'] = 'term_sent'
        except ProcessLookupError:
            report['termination'] = 'exit_race'
            return _wait_owned(child, 2)
        except OSError as exc:
            error('termination', exc)
            report['termination'] = 'signal_rejected'
            # In particular, PermissionError must not fall through to KILL or
            # an alternative signaling API. Natural exit can still be observed.
            return _wait_owned(child, 2)
        if _wait_owned(child, 2):
            return True
        try:
            os.killpg(child.pid, signal.SIGKILL)
            report['termination'] = 'kill_sent'
        except ProcessLookupError:
            report['termination'] = 'exit_race'
        except OSError as exc:
            error('termination', exc)
            report['termination'] = 'signal_rejected'
        return _wait_owned(child, 2)
    except BaseException as exc:
        error('cleanup_monitor', exc)
        # A broken tree probe cannot confirm descendant cleanup. Still reap the
        # known child, and only escalate a previously accepted TERM if that
        # child remains alive. A denied signal never reaches this escalation.
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if report['termination'] == 'term_sent' and child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                    report['termination'] = 'kill_sent'
                    child.wait(timeout=2)
                except BaseException as followup:
                    error('termination', followup)
        except BaseException as followup:
            error('child_wait', followup)
        return False


def run_guarded(command, run_root, report_path):
    """Run once under 4 GiB tree RSS / 12 GiB temporary disk / 600 seconds.

    Sampling does not measure instantaneous peaks. A worker's ru_maxrss saved
    before exit is explicitly not the final tree or lifetime peak. An incomplete
    cleanup returns a failed report and leaves the process state visible.
    """
    run_root, report_path = Path(run_root), Path(report_path)
    if not command or run_root.exists() or report_path.exists():
        raise ValueError('fresh_run_directory_and_command_required')
    run_root.mkdir(parents=True, mode=0o700)
    temporary = run_root / 'tmp'
    temporary.mkdir(mode=0o700)
    report = {
        'completed': False, 'finalized': False, 'execution_started': False,
        'rss_limit_MiB': RSS_LIMIT_MIB, 'disk_limit_bytes': DISK_LIMIT_BYTES,
        'deadline_seconds': DEADLINE_SECONDS, 'samples': 0, 'disk_samples': 0,
        'sampled_process_tree_peak_MiB': None, 'sampled_single_process_peak_MiB': None,
        'sampled_disk_peak_bytes': None, 'sampling_is_not_instantaneous_peak': True,
        'monitor_complete': False, 'cleanup_complete': False, 'exit_code': None,
        'worker_pid': None, 'remaining_process_ids': None,
        'abort_reason': None, 'errors': [], 'termination': 'not_needed',
        'worker_completed': False, 'worker_last_report_peak_MiB': None,
        'worker_peak_is_final': False,
        'worker_peak_scope': 'single_process_high_water_at_worker_report',
    }
    begin = time.monotonic()
    child = None

    def error(phase, exc):
        report['errors'].append({'phase': phase, 'error_class': type(exc).__name__})
        if report['abort_reason'] is None:
            report['abort_reason'] = phase + '_failed'

    def save():
        report['elapsed_seconds'] = time.monotonic() - begin
        try:
            _save_report(report_path, report)
            return True
        except BaseException as exc:
            error('report_write', exc)
            return False

    def sample_disk(ids=()):
        value = disk_bytes(run_root, ids)
        report['sampled_disk_peak_bytes'] = max(value, report['sampled_disk_peak_bytes'] or 0)
        report['disk_samples'] += 1

    if not save():
        report['finalized'] = True
        return report  # Do not launch an unreportable run.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               TMPDIR=str(temporary.resolve()), TEMP=str(temporary.resolve()), TMP=str(temporary.resolve()))
    try:
        with (run_root / 'worker-private.log').open('xb') as log:
            os.chmod(log.name, 0o600)
            child = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True, env=env)
            report['execution_started'] = True
            report['worker_pid'] = child.pid
            last_disk = -2
            while child.poll() is None:
                total, single, ids = process_rss(child.pid)
                report['samples'] += 1
                report['sampled_process_tree_peak_MiB'] = max(total, report['sampled_process_tree_peak_MiB'] or 0)
                report['sampled_single_process_peak_MiB'] = max(single, report['sampled_single_process_peak_MiB'] or 0)
                elapsed = time.monotonic() - begin
                if elapsed - last_disk >= 2:
                    sample_disk(ids)
                    last_disk = elapsed
                    if not save():
                        break
                if max(total, single) > RSS_LIMIT_MIB:
                    report['abort_reason'] = 'rss_limit'
                elif report['sampled_disk_peak_bytes'] > DISK_LIMIT_BYTES:
                    report['abort_reason'] = 'disk_limit'
                elif time.monotonic() - begin >= DEADLINE_SECONDS:
                    report['abort_reason'] = 'deadline'
                if report['abort_reason']:
                    break
                time.sleep(.25)
    except BaseException as exc:
        error('execution_monitor', exc)
    finally:
        # Persist the last measured peaks before any fallible signal/wait/probe.
        save()
        if child is not None:
            report['cleanup_complete'] = _stop_owned(child, report, error)
            try:
                report['exit_code'] = child.poll()
                _, _, live = process_rss(child.pid)
                report['remaining_process_ids'] = live
                sample_disk(live)
            except BaseException as exc:
                error('final_probe', exc)
        else:
            report['cleanup_complete'] = True
        try:
            metric = run_root / 'safe-result.json'
            if metric.stat().st_size > 1024**2:
                raise ValueError('worker_summary_too_large')
            worker = json.loads(metric.read_text(encoding='utf-8'))
            peak = worker.get('actual_lifetime_peak_MiB')
            if isinstance(peak, bool) or not isinstance(peak, (int, float)) or not math.isfinite(peak) or peak < 0:
                raise ValueError('invalid_worker_peak')
            report['worker_last_report_peak_MiB'] = peak
            report['worker_completed'] = worker.get('completed') is True
        except BaseException as exc:
            error('worker_report', exc)
        report['monitor_complete'] = report['samples'] > 0 and report['disk_samples'] > 0 and not report['errors']
        report['elapsed_seconds'] = time.monotonic() - begin
        if report['abort_reason'] is None:
            if report['elapsed_seconds'] > DEADLINE_SECONDS:
                report['abort_reason'] = 'deadline'
            elif not report['cleanup_complete']:
                report['abort_reason'] = 'cleanup_incomplete'
            elif report['exit_code'] != 0 or not report['worker_completed']:
                report['abort_reason'] = 'worker_incomplete'
            elif (report['worker_last_report_peak_MiB'] or 0) > RSS_LIMIT_MIB:
                report['abort_reason'] = 'rss_limit'
            elif (report['sampled_disk_peak_bytes'] or 0) > DISK_LIMIT_BYTES:
                report['abort_reason'] = 'disk_limit'
            elif not report['monitor_complete']:
                report['abort_reason'] = 'incomplete_monitor'
        report['completed'] = report['abort_reason'] is None and report['monitor_complete']
        report['finalized'] = True
        if not save():
            report['completed'] = False
            report['monitor_complete'] = False
    return report
