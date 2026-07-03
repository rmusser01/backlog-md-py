import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from backlog_py import __version__
from backlog_py.cli.main import main
from backlog_py.daemon.lifecycle import daemon_stop
from backlog_py.runtime.locks import ProjectWriteLock
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

    with pytest.raises(TimeoutError, match="did not stop"):
        daemon_stop(timeout=0)

    assert kills == [(12345, signal.SIGTERM)]
    assert read_runtime_record(layout) is not None


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
