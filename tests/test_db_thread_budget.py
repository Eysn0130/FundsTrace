"""Interactive database concurrency stays bounded across request readers."""


def test_initial_thread_budget_is_bounded_and_shared_with_snapshot(database):
    threads = int(database.q_raw("select current_setting('threads') n")[0]['n'])
    assert 1 <= threads <= 4
    with database.read_snapshot():
        assert int(database.q_raw("select current_setting('threads') n")[0]['n']) == threads


def test_existing_lower_thread_budget_is_not_reset_by_later_access(database):
    database.con().execute('set threads=1')
    database.con()
    with database.read_snapshot():
        assert int(database.q_raw("select current_setting('threads') n")[0]['n']) == 1


def test_initial_buffer_budget_is_bounded_and_shared_with_snapshot(database):
    limit = database.q_raw("select current_setting('memory_limit') n")[0]['n']
    value, unit = limit.split()
    assert unit in {'byte', 'bytes', 'KiB', 'MiB'} or unit == 'GiB' and float(value) <= 1
    with database.read_snapshot():
        assert database.q_raw("select current_setting('memory_limit') n")[0]['n'] == limit


def test_initialization_preserves_a_lower_existing_buffer_budget(database, monkeypatch):
    database._con.close()
    database._con = None
    original = database.duckdb.connect

    def lower_connection(*args, **kwargs):
        connection = original(*args, **kwargs)
        if not kwargs.get('read_only', False):
            connection.execute("set memory_limit='128MiB'")
        return connection

    monkeypatch.setattr(database.duckdb, 'connect', lower_connection)
    assert database.q_raw("select current_setting('memory_limit') n")[0]['n'] == '128.0 MiB'
    with database.read_snapshot():
        assert database.q_raw("select current_setting('memory_limit') n")[0]['n'] == '128.0 MiB'
