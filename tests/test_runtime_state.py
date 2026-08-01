import json
import os
import stat
from pathlib import Path

import pytest

from backlog_py.runtime.state import (
    RuntimeRecord,
    StateLayout,
    allocate_log_path,
    delete_runtime_record,
    ensure_state_layout,
    prune_daemon_logs,
    read_runtime_record,
    resolve_state_dir,
    runtime_record_path,
    runtime_status,
    write_runtime_record,
)


def test_state_dir_uses_env_override(tmp_path):
    env = {"BACKLOG_PY_STATE_DIR": str(tmp_path / "state")}

    assert resolve_state_dir(env=env, platform="linux", home=tmp_path / "home") == tmp_path / "state"


def test_state_dir_uses_macos_application_support(tmp_path):
    home = tmp_path / "home"

    assert resolve_state_dir(env={}, platform="darwin", home=home) == (
        home / "Library" / "Application Support" / "backlog-md-py"
    )


def test_state_dir_uses_xdg_state_home_on_linux(tmp_path):
    env = {"XDG_STATE_HOME": str(tmp_path / "xdg-state")}

    assert resolve_state_dir(env=env, platform="linux", home=tmp_path / "home") == (
        tmp_path / "xdg-state" / "backlog-md-py"
    )


def test_state_dir_uses_local_state_fallback_on_linux(tmp_path):
    home = tmp_path / "home"

    assert resolve_state_dir(env={}, platform="linux", home=home) == home / ".local" / "state" / "backlog-md-py"


def test_state_dir_uses_local_app_data_on_windows(tmp_path):
    env = {"LOCALAPPDATA": str(tmp_path / "LocalAppData")}

    assert resolve_state_dir(env=env, platform="win32", home=tmp_path / "home") == (
        tmp_path / "LocalAppData" / "backlog-md-py"
    )


def test_state_dir_uses_windows_home_fallback(tmp_path):
    home = tmp_path / "home"

    assert resolve_state_dir(env={}, platform="win32", home=home) == (
        home / "AppData" / "Local" / "backlog-md-py"
    )


def test_state_layout_creates_expected_subdirectories(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))

    layout = ensure_state_layout()

    assert layout == StateLayout(
        root=tmp_path / "state",
        runtime_dir=tmp_path / "state" / "runtime",
        locks_dir=tmp_path / "state" / "locks",
        logs_dir=tmp_path / "state" / "logs",
    )
    assert layout.runtime_dir.is_dir()
    assert layout.locks_dir.is_dir()
    assert layout.logs_dir.is_dir()


def test_runtime_record_round_trips_json(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    record = _record(log_path=layout.logs_dir / "daemon.log")

    write_runtime_record(record, layout)

    assert read_runtime_record(layout) == record
    raw = json.loads(runtime_record_path(layout).read_text(encoding="utf-8"))
    assert raw["pid"] == record.pid
    assert raw["log_path"] == str(record.log_path)


def test_read_runtime_record_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()

    assert read_runtime_record(layout) is None


def test_delete_runtime_record_removes_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    write_runtime_record(_record(log_path=layout.logs_dir / "daemon.log"), layout)

    delete_runtime_record(layout)

    assert read_runtime_record(layout) is None
    assert not runtime_record_path(layout).exists()


def test_delete_runtime_record_ignores_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()

    delete_runtime_record(layout)

    assert read_runtime_record(layout) is None


def test_runtime_status_omits_token_material(tmp_path):
    record = _record(token="super-secret-token", log_path=tmp_path / "daemon.log")

    status = runtime_status(record)

    assert status["pid"] == record.pid
    assert status["endpoint"] == record.endpoint
    assert status["version"] == record.version
    assert status["log_path"] == str(record.log_path)
    assert "token" not in status
    assert "super-secret-token" not in json.dumps(status)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not meaningful on Windows")
def test_runtime_record_file_uses_user_only_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()

    write_runtime_record(_record(log_path=layout.logs_dir / "daemon.log"), layout)

    mode = stat.S_IMODE(runtime_record_path(layout).stat().st_mode)
    assert mode == 0o600


def test_allocate_log_path_returns_daemon_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()

    first = allocate_log_path(layout)
    second = allocate_log_path(layout)

    assert first.parent == layout.logs_dir
    assert first.name.startswith("backlog-md-py-daemon-")
    assert first.suffix == ".log"
    assert second != first


def test_prune_daemon_logs_keeps_only_the_newest_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    logs = [_daemon_log(layout, index) for index in range(6)]
    foreground_log = layout.logs_dir / "backlog-md-py-daemon.log"
    foreground_log.write_text("foreground", encoding="utf-8")
    os.utime(foreground_log, (1_000, 1_000))

    removed = prune_daemon_logs(layout, keep=2)

    assert sorted(removed) == sorted(logs[:4])
    assert [log.exists() for log in logs] == [False, False, False, False, True, True]
    assert foreground_log.exists(), "pruned the unmanaged foreground daemon log"


def test_prune_daemon_logs_never_removes_excluded_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    logs = [_daemon_log(layout, index) for index in range(4)]

    removed = prune_daemon_logs(layout, keep=1, exclude=[logs[0]])

    assert logs[0].exists()
    assert logs[3].exists()
    assert sorted(removed) == sorted(logs[1:3])


def test_prune_daemon_logs_is_a_noop_below_the_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLOG_PY_STATE_DIR", str(tmp_path / "state"))
    layout = ensure_state_layout()
    logs = [_daemon_log(layout, index) for index in range(3)]

    assert prune_daemon_logs(layout, keep=5) == []
    assert all(log.exists() for log in logs)


def _daemon_log(layout: StateLayout, index: int) -> Path:
    path = layout.logs_dir / f"backlog-md-py-daemon-2026010{index}T000000Z-1-abcd{index}.log"
    path.write_text(f"log {index}", encoding="utf-8")
    stamp = 1_000_000 + index
    os.utime(path, (stamp, stamp))
    return path


def _record(*, token: str = "token", log_path: Path) -> RuntimeRecord:
    return RuntimeRecord(
        pid=12345,
        host="127.0.0.1",
        port=8765,
        endpoint="http://127.0.0.1:8765/mcp",
        token=token,
        started_at="2026-05-15T12:00:00Z",
        version="0.1.0",
        log_path=log_path,
    )
