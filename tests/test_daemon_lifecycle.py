import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py import __version__
from backlog_py.cli.main import main
from backlog_py.daemon import DaemonOwnershipError
from backlog_py.daemon.lifecycle import (
    DaemonNotRunningError,
    DaemonStartError,
    _wait_for_daemon_healthy,
    daemon_start,
    daemon_status,
    daemon_stop,
)
from backlog_py.mcp.http_server import start_mcp_http_server
from backlog_py.runtime.locks import DaemonRuntimeLock, ProjectWriteLock
from backlog_py.runtime.state import (
    RuntimeRecord,
    ensure_state_layout,
    read_runtime_record,
    runtime_record_path,
    write_runtime_record,
)


def test_daemon_status_reports_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    result = CliRunner().invoke(main, ["daemon", "status"])

    assert result.exit_code != 0
    assert "not running" in result.output.lower()


def test_daemon_status_json_omits_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    write_runtime_record(_record(pid=os.getpid(), token="secret-token"), ensure_state_layout())
    monkeypatch.setattr("backlog_py.daemon.lifecycle._daemon_endpoint_owned", lambda record: True)

    result = CliRunner().invoke(main, ["daemon", "status", "--json"])

    assert result.exit_code == 0
    assert "secret-token" not in result.output
    payload = json.loads(result.output)
    assert payload["endpoint"] == "http://127.0.0.1:18765/mcp"
    assert payload["version"] == __version__


def test_daemon_status_json_reports_known_project_and_lock_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    project_root = tmp_path / "repo"
    project_root.mkdir()
    write_runtime_record(_record(pid=os.getpid(), token="secret-token"), ensure_state_layout())
    monkeypatch.setattr("backlog_py.daemon.lifecycle._daemon_endpoint_owned", lambda record: True)

    with ProjectWriteLock(project_root, operation="task_edit").acquire(timeout=0.1):
        result = CliRunner().invoke(main, ["daemon", "status", "--json"])

    assert result.exit_code == 0
    assert "secret-token" not in result.output
    payload = json.loads(result.output)
    assert payload["known_projects"] == [str(project_root.resolve())]
    assert payload["locks"] == [
        {
            "acquired_at": payload["locks"][0]["acquired_at"],
            "active": True,
            "key": payload["locks"][0]["key"],
            "kind": "project",
            "lock_path": payload["locks"][0]["lock_path"],
            "metadata_path": payload["locks"][0]["metadata_path"],
            "operation": "task_edit",
            "pid": os.getpid(),
            "project_root": str(project_root.resolve()),
        }
    ]


def test_daemon_status_cleans_stale_runtime_record(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    write_runtime_record(_record(pid=999999), layout)

    result = CliRunner().invoke(main, ["daemon", "status"])

    assert result.exit_code != 0
    assert "not running" in result.output.lower()
    assert read_runtime_record(layout) is None


def test_daemon_ensure_reuses_existing_runtime_record(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    write_runtime_record(_record(pid=os.getpid()), ensure_state_layout())
    monkeypatch.setattr("backlog_py.daemon.lifecycle._daemon_endpoint_owned", lambda record: True)

    def fail_popen(*args, **kwargs):
        raise AssertionError("ensure should not start a healthy daemon")

    monkeypatch.setattr("backlog_py.daemon.lifecycle.subprocess.Popen", fail_popen)

    result = CliRunner().invoke(main, ["daemon", "ensure", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["pid"] == os.getpid()


def test_daemon_start_launches_foreground_service_and_writes_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    launches = []

    class FakeProcess:
        pid = 43210

    def fake_popen(command, **kwargs):
        launches.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr("backlog_py.daemon.lifecycle.subprocess.Popen", fake_popen)
    monkeypatch.setattr("backlog_py.daemon.lifecycle._wait_for_daemon_healthy", lambda *a, **k: True)

    result = CliRunner().invoke(main, ["daemon", "start", "--host", "127.0.0.1", "--port", "18888", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["pid"] == 43210
    assert payload["endpoint"] == "http://127.0.0.1:18888/mcp"
    assert "token" not in payload
    command = launches[0][0]
    assert command[-5:] == ["--foreground", "--host", "127.0.0.1", "--port", "18888"]
    assert read_runtime_record(ensure_state_layout()).pid == 43210


def test_daemon_start_brackets_ipv6_runtime_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    class FakeProcess:
        pid = 43215

    monkeypatch.setattr(
        "backlog_py.daemon.lifecycle.subprocess.Popen",
        lambda command, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "backlog_py.daemon.lifecycle._wait_for_daemon_healthy", lambda *args, **kwargs: True
    )

    status = daemon_start(host="::1", port=18892)

    assert status.record.endpoint == "http://[::1]:18892/mcp"


def test_daemon_start_forwards_the_remote_opt_in_to_the_child(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    launches = []

    class FakeProcess:
        pid = 43214

    monkeypatch.setattr(
        "backlog_py.daemon.lifecycle.subprocess.Popen",
        lambda command, **kwargs: launches.append(command) or FakeProcess(),
    )
    monkeypatch.setattr("backlog_py.daemon.lifecycle._wait_for_daemon_healthy", lambda *a, **k: True)

    daemon_start(host="127.0.0.1", port=18891, allow_remote=True)

    assert "--allow-remote" in launches[0]


def test_daemon_start_does_not_record_unhealthy_child(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    terminated = []

    class FakeProcess:
        pid = 43211

        def poll(self):
            return 1  # already exited (e.g. port collision)

        def terminate(self):
            terminated.append(self.pid)

        def kill(self):
            terminated.append(self.pid)

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr("backlog_py.daemon.lifecycle.subprocess.Popen", lambda command, **kwargs: FakeProcess())

    result = CliRunner().invoke(main, ["daemon", "start", "--host", "127.0.0.1", "--port", "18889", "--json"])

    assert result.exit_code != 0, result.output
    assert read_runtime_record(ensure_state_layout()) is None, "recorded a daemon that never became healthy"


def test_daemon_start_prunes_accumulated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    old_logs = []
    for index in range(15):
        path = layout.logs_dir / f"backlog-md-py-daemon-2026010{index % 10}T000000Z-1-old{index}.log"
        path.write_text("old", encoding="utf-8")
        os.utime(path, (1_000 + index, 1_000 + index))
        old_logs.append(path)
    dead_lock = layout.locks_dir / "project-deadbeef.lock"
    dead_metadata = layout.locks_dir / "project-deadbeef.json"
    dead_lock.write_text("", encoding="utf-8")
    dead_metadata.write_text(
        json.dumps({"active": False, "key": "project-deadbeef", "kind": "project", "pid": 999999}),
        encoding="utf-8",
    )
    stamp = time.time() - 30 * 24 * 60 * 60
    os.utime(dead_metadata, (stamp, stamp))

    class FakeProcess:
        pid = 43213

    monkeypatch.setattr("backlog_py.daemon.lifecycle.subprocess.Popen", lambda command, **kwargs: FakeProcess())
    monkeypatch.setattr("backlog_py.daemon.lifecycle._wait_for_daemon_healthy", lambda *a, **k: True)

    daemon_start(host="127.0.0.1", port=18890)

    remaining = list(layout.logs_dir.glob("backlog-md-py-daemon-*.log"))
    assert len(remaining) <= 11, "daemon logs are not bounded on start"
    assert not any(log.exists() for log in old_logs[:4])
    assert not dead_lock.exists()
    assert not dead_metadata.exists()


def test_daemon_stop_removes_stale_runtime_record(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    write_runtime_record(_record(pid=999999), layout)

    result = CliRunner().invoke(main, ["daemon", "stop"])

    assert result.exit_code == 0, result.output
    assert "not running" in result.output.lower()
    assert not runtime_record_path(layout).exists()


def test_daemon_stop_keeps_runtime_record_when_graceful_stop_times_out(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    write_runtime_record(_record(pid=12345), layout)
    kills = []

    monkeypatch.setattr("backlog_py.daemon.lifecycle.is_pid_alive", lambda pid: True)
    monkeypatch.setattr("backlog_py.daemon.lifecycle.os.kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr("backlog_py.daemon.lifecycle._daemon_endpoint_owned", lambda record: True)

    with pytest.raises(TimeoutError, match="did not stop"):
        daemon_stop(timeout=0)

    assert kills == [(12345, signal.SIGTERM)]
    assert read_runtime_record(layout) is not None


def test_daemon_stop_does_not_signal_a_reused_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    write_runtime_record(_record(pid=12345), layout)
    kills = []

    monkeypatch.setattr("backlog_py.daemon.lifecycle.is_pid_alive", lambda pid: True)
    monkeypatch.setattr("backlog_py.daemon.lifecycle.os.kill", lambda pid, sig: kills.append((pid, sig)))
    # The recorded PID now belongs to an unrelated process, so its endpoint is
    # not served by our daemon.
    monkeypatch.setattr("backlog_py.daemon.lifecycle._daemon_endpoint_owned", lambda record: False)

    result = daemon_stop(force=True)

    assert result is False
    assert kills == [], "signalled a PID that is no longer our daemon"
    assert read_runtime_record(layout) is None


def test_daemon_stop_does_not_signal_when_ownership_is_uncertain(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    record = _record(pid=12345)
    write_runtime_record(record, layout)
    kills = []

    monkeypatch.setattr("backlog_py.daemon.lifecycle.is_pid_alive", lambda pid: True)
    monkeypatch.setattr("backlog_py.daemon.lifecycle.os.kill", lambda pid, sig: kills.append((pid, sig)))
    monkeypatch.setattr("backlog_py.daemon.lifecycle._daemon_endpoint_owned", lambda record: None)

    with pytest.raises(DaemonOwnershipError, match="unable to verify daemon ownership"):
        daemon_stop(force=True)

    assert kills == []
    assert read_runtime_record(layout) == record


def test_daemon_status_treats_reused_pid_as_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    write_runtime_record(_record(pid=12345), layout)
    monkeypatch.setattr("backlog_py.daemon.lifecycle.is_pid_alive", lambda pid: True)
    monkeypatch.setattr("backlog_py.daemon.lifecycle._daemon_endpoint_owned", lambda record: False)

    with pytest.raises(DaemonNotRunningError):
        daemon_status()
    assert read_runtime_record(layout) is None


def test_daemon_status_does_not_delete_a_record_written_by_a_concurrent_start(tmp_path, monkeypatch):
    """Stale cleanup must not orphan a daemon another process just started."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    stale = _record(pid=os.getpid(), token="stale-token")
    write_runtime_record(stale, layout)

    probing = threading.Event()
    resume = threading.Event()

    def blocking_endpoint_owned(record):
        # Simulates a slow /status probe against the stale endpoint.
        probing.set()
        assert resume.wait(10)
        return False

    monkeypatch.setattr(
        "backlog_py.daemon.lifecycle._daemon_endpoint_owned", blocking_endpoint_owned
    )

    outcome: list[str] = []

    def run_status() -> None:
        try:
            daemon_status()
            outcome.append("running")
        except DaemonNotRunningError:
            outcome.append("not-running")

    thread = threading.Thread(target=run_status, name="daemon-status-probe")
    thread.start()
    try:
        assert probing.wait(10), "status never probed the stale endpoint"
        # A concurrent daemon_start wins the lock and records a live daemon.
        fresh = _record(pid=os.getpid(), token="fresh-token")
        with DaemonRuntimeLock(operation="daemon_start").acquire(timeout=10):
            write_runtime_record(fresh, layout)
    finally:
        resume.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert outcome == ["not-running"]
    assert read_runtime_record(layout) == fresh, "orphaned the freshly started daemon"


def test_daemon_start_rejects_health_answered_by_a_port_squatter(tmp_path, monkeypatch):
    """A foreign listener on the port must not be mistaken for our child."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    squatter = start_mcp_http_server(host="127.0.0.1", port=0, token="squatter-token")
    polls = iter([None, 1])

    class FakeProcess:
        pid = 43212

        def poll(self):
            return next(polls, 1)

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(
        "backlog_py.daemon.lifecycle.subprocess.Popen", lambda command, **kwargs: FakeProcess()
    )
    try:
        with pytest.raises(DaemonStartError):
            daemon_start(host="127.0.0.1", port=squatter.port)
    finally:
        squatter.shutdown()

    assert read_runtime_record(ensure_state_layout()) is None, "recorded a PID that never bound the port"


def test_wait_for_daemon_healthy_rejects_a_child_that_died_after_answering():
    service = start_mcp_http_server(host="127.0.0.1", port=0, token="child-token")
    polls = iter([None, 0])

    class FakeProcess:
        pid = 1

        def poll(self):
            return next(polls, 0)

    try:
        healthy = _wait_for_daemon_healthy(
            "127.0.0.1", service.port, FakeProcess(), token="child-token", timeout=2
        )
    finally:
        service.shutdown()

    assert healthy is False


def test_daemon_run_foreground_exits_on_sigterm(tmp_path):
    state_dir = tmp_path / "state"
    layout = ensure_state_layout_for_env(state_dir)
    env = {
        **os.environ,
        "BACKLOG_PY_STATE_DIR": str(state_dir),
        "BACKLOG_PY_DAEMON_TOKEN": "secret",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "backlog_py",
            "daemon",
            "run",
            "--foreground",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_runtime_record(layout, process.pid)
        os.kill(process.pid, signal.SIGTERM)

        assert process.wait(timeout=3) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_daemon_start_status_and_stop_round_trip_against_a_real_child(tmp_path, monkeypatch):
    """End-to-end guard for the token-authenticated startup health check."""
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    port = _free_port()

    status = daemon_start(host="127.0.0.1", port=port)
    try:
        assert status.record.pid != os.getpid()
        assert status.record.port == port
        assert read_runtime_record(layout) == status.record
        assert daemon_status().record == status.record
    finally:
        stopped = daemon_stop(timeout=10)

    assert stopped is True
    assert read_runtime_record(layout) is None


def test_daemon_run_foreground_refuses_to_clobber_a_live_daemon_record(tmp_path):
    """A manual foreground run must not strand a daemon that owns the record."""
    state_dir = tmp_path / "state"
    layout = ensure_state_layout_for_env(state_dir)
    # Stand in for the already-running managed daemon: a live PID whose endpoint
    # answers /status with the recorded token.
    existing_daemon = start_mcp_http_server(host="127.0.0.1", port=0, token="existing-token")
    existing = RuntimeRecord(
        pid=os.getpid(),
        host="127.0.0.1",
        port=existing_daemon.port,
        endpoint=existing_daemon.endpoint,
        token="existing-token",
        started_at="2026-05-15T12:00:00Z",
        version=__version__,
        log_path=layout.logs_dir / "existing.log",
    )
    write_runtime_record(existing, layout)
    port = _free_port()
    env = {
        **os.environ,
        "BACKLOG_PY_STATE_DIR": str(state_dir),
        "BACKLOG_PY_DAEMON_TOKEN": "foreground-token",
    }
    env.pop("BACKLOG_PY_DAEMON_MANAGED", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "backlog_py", "daemon", "run", "--foreground", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    try:
        _wait_for_health(port)

        assert read_runtime_record(layout) == existing, "foreground run clobbered the live daemon record"
    finally:
        existing_daemon.shutdown()
        if process.poll() is None:
            process.terminate()
        output = process.communicate(timeout=5)[0]
        if process.poll() is None:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=3)

    assert "already running" in output.lower(), output


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int, *, timeout: float = 10.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    raise AssertionError(f"daemon never became healthy on port {port}")


def _record(pid: int = 12345, token: str = "token") -> RuntimeRecord:
    return RuntimeRecord(
        pid=pid,
        host="127.0.0.1",
        port=18765,
        endpoint="http://127.0.0.1:18765/mcp",
        token=token,
        started_at="2026-05-15T12:00:00Z",
        version=__version__,
        log_path=Path("/tmp/backlog-md-py-daemon.log"),
    )


def ensure_state_layout_for_env(state_dir: Path):
    previous = os.environ.get("BACKLOG_PY_STATE_DIR")
    os.environ["BACKLOG_PY_STATE_DIR"] = str(state_dir)
    try:
        return ensure_state_layout()
    finally:
        if previous is None:
            os.environ.pop("BACKLOG_PY_STATE_DIR", None)
        else:
            os.environ["BACKLOG_PY_STATE_DIR"] = previous


def _wait_for_runtime_record(layout, pid: int) -> RuntimeRecord:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        record = read_runtime_record(layout)
        if record is not None and record.pid == pid:
            return record
        time.sleep(0.05)
    raise AssertionError("daemon runtime record was not written")
