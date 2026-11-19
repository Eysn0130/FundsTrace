"""Run the complete synthetic regression suite without using business state."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


PYTHON_SHARDS = (1, 2, 3)


def pytest_addoption(parser):
    parser.addoption("--ci-shard", type=int, choices=PYTHON_SHARDS,
                     help="run one of the three Python/G6 CI shards")


def pytest_collection_modifyitems(config, items):
    shard = config.getoption("--ci-shard")
    if shard is None:
        return
    # Keep all parametrizations and fixtures of each test module together.
    by_path = [(item, item.path.relative_to(config.rootpath).as_posix()) for item in items]
    paths = sorted({path for _, path in by_path})
    selected = set(paths[shard - 1::len(PYTHON_SHARDS)])
    deselected = [item for item, path in by_path if path not in selected]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = [item for item, path in by_path if path in selected]


_epoch_outcomes = []


def _write_probe(session, kind, rows):
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    for offset in range(0, len(rows), 40):
        terminal.write_line(f"FT_BENCH_{kind} " + json.dumps(rows[offset:offset+40], ensure_ascii=True))


def pytest_collection_finish(session):
    _write_probe(session, "COLLECTED", [item.nodeid for item in session.items])


def pytest_runtest_logreport(report):
    if report.when == "call" or report.outcome != "passed":
        is_subtest = type(report).__name__.lower() == "subtestreport"
        _epoch_outcomes.append([report.nodeid, report.when, report.outcome, is_subtest,
                                hasattr(report, "wasxfail")])


def pytest_sessionfinish(session, exitstatus):
    _write_probe(session, "OUTCOMES", _epoch_outcomes)


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="funds-trace-tests-") as temporary:
        env = dict(os.environ)
        env["FUND_DATA_DIR"] = str(Path(temporary) / "data")
        env["MPLCONFIGDIR"] = str(Path(temporary) / "matplotlib")
        # Public synthetic fixture configuration, never an inherited real key.
        env["FUNDS_TRACE_LICENSE_SECRET"] = "synthetic-regression-key-not-for-use"
        env.pop("FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.call(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "scripts.test", *sys.argv[1:]],
            cwd=root, env=env,
        )


if __name__ == "__main__":
    raise SystemExit(main())
