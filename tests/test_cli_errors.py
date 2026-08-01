"""Regression tests for clean CLI error handling (no raw tracebacks)."""
from __future__ import annotations

import shutil
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from backlog_py.cli.main import main

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "repos" / "basic"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def _invoke(repo: Path, *args: str):
    return CliRunner().invoke(main, ["--cwd", str(repo), *args])


def _assert_clean_error(result) -> None:
    assert result.exit_code != 0
    # A raw domain exception escaping to the top is the bug; a ClickException
    # is caught by click and rendered as "Error: ..." with SystemExit.
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"uncaught {type(result.exception).__name__}: {result.exception}"
    )


def test_view_missing_task_reports_clean_error(tmp_path):
    result = _invoke(_repo(tmp_path), "task", "nonexistent-99")
    _assert_clean_error(result)
    assert "not found" in result.output.casefold()


def test_edit_unknown_status_reports_clean_error(tmp_path):
    result = _invoke(_repo(tmp_path), "task", "edit", "task-1", "-s", "Bogus Status")
    _assert_clean_error(result)


def test_edit_with_parent_is_rejected_not_silently_ignored(tmp_path):
    # edit_task cannot reparent, so --parent must error rather than report success.
    result = _invoke(_repo(tmp_path), "task", "edit", "task-1", "--parent", "task-1")
    _assert_clean_error(result)
    assert "parent" in result.output.casefold()


def test_create_with_edit_only_flag_is_rejected(tmp_path):
    result = _invoke(_repo(tmp_path), "task", "create", "New task", "--check-ac", "1")
    _assert_clean_error(result)


def test_create_with_title_flag_is_rejected(tmp_path):
    result = _invoke(_repo(tmp_path), "task", "create", "New task", "--title", "Other")
    _assert_clean_error(result)


def test_config_get_unknown_key_prints_clean_message(tmp_path):
    result = _invoke(_repo(tmp_path), "config", "get", "bogus")

    _assert_clean_error(result)
    assert result.output == "Error: Unknown config key: bogus\n"


def test_orchestration_record_run_unknown_task_prints_clean_message(tmp_path):
    result = _invoke(
        _repo(tmp_path),
        "orchestration",
        "record-run",
        "task-999",
        "--result",
        "succeeded",
    )

    _assert_clean_error(result)
    assert result.output.startswith("Error: ")
    assert "'" not in result.output


def test_daemon_run_rejects_non_loopback_host(tmp_path):
    result = _invoke(_repo(tmp_path), "daemon", "run", "--foreground", "--host", "0.0.0.0")

    _assert_clean_error(result)
    assert "loopback" in result.output.casefold()
    assert "0.0.0.0" in result.output


def test_daemon_start_rejects_non_loopback_host(tmp_path):
    result = _invoke(_repo(tmp_path), "daemon", "start", "--host", "10.0.0.5")

    _assert_clean_error(result)
    assert "loopback" in result.output.casefold()


def _record_foreground_service(monkeypatch) -> dict[str, object]:
    """Patch the daemon service boundary so no test ever really binds a socket."""
    seen: dict[str, object] = {}

    def fake_run_foreground_service(*, host: str, port: int, allow_remote: bool = False) -> None:
        seen["host"] = host
        seen["port"] = port
        seen["allow_remote"] = allow_remote

    monkeypatch.setattr(
        "backlog_py.daemon.service.run_foreground_service",
        fake_run_foreground_service,
    )
    return seen


@pytest.mark.parametrize(
    "option_order",
    [
        ("--host", "0.0.0.0", "--allow-remote"),
        ("--allow-remote", "--host", "0.0.0.0"),
    ],
)
def test_daemon_run_allow_remote_reaches_the_service_in_any_option_order(tmp_path, monkeypatch, option_order):
    seen = _record_foreground_service(monkeypatch)

    result = _invoke(_repo(tmp_path), "daemon", "run", "--foreground", *option_order)

    assert result.exit_code == 0, result.output
    assert seen["host"] == "0.0.0.0"
    # The server layer is default-deny, so the opt-in has to be forwarded.
    assert seen["allow_remote"] is True


def test_daemon_run_accepts_loopback_hosts(tmp_path, monkeypatch):
    seen = _record_foreground_service(monkeypatch)

    result = _invoke(_repo(tmp_path), "daemon", "run", "--foreground", "--host", "localhost")

    assert result.exit_code == 0, result.output
    assert seen["host"] == "localhost"
    assert seen["allow_remote"] is False


@pytest.mark.parametrize(
    "option_order",
    [
        ("--host", "0.0.0.0", "--allow-remote"),
        ("--allow-remote", "--host", "0.0.0.0"),
    ],
)
def test_daemon_start_forwards_allow_remote_in_any_option_order(tmp_path, monkeypatch, option_order):
    seen: dict[str, object] = {}

    def fake_daemon_start(*, host: str, port: int, allow_remote: bool = False):
        seen["host"] = host
        seen["port"] = port
        seen["allow_remote"] = allow_remote
        raise click.ClickException("stopped before starting a real daemon")

    monkeypatch.setattr("backlog_py.cli.main.daemon_start", fake_daemon_start)

    result = _invoke(_repo(tmp_path), "daemon", "start", *option_order)

    assert result.exit_code != 0
    assert seen["host"] == "0.0.0.0"
    assert seen["allow_remote"] is True


def test_daemon_cli_matches_the_daemon_service_signatures():
    """Pin the cross-module contract the --allow-remote plumbing depends on."""
    import inspect

    from backlog_py.daemon.lifecycle import daemon_start
    from backlog_py.daemon.service import run_foreground_service

    inspect.signature(run_foreground_service).bind(host="0.0.0.0", port=18765, allow_remote=True)
    inspect.signature(daemon_start).bind(host="0.0.0.0", port=18765, allow_remote=True)


def test_daemon_ensure_forwards_allow_remote_to_daemon_start(monkeypatch):
    """The Python API opt-in must reach daemon_start instead of being dropped."""
    from backlog_py.daemon import lifecycle

    seen: dict[str, object] = {}

    def fake_daemon_status():
        raise lifecycle.DaemonNotRunningError("Daemon not running")

    def fake_daemon_start(host=lifecycle.DEFAULT_HOST, port=lifecycle.DEFAULT_PORT, *, allow_remote=False):
        seen.update(host=host, port=port, allow_remote=allow_remote)
        return "started"

    monkeypatch.setattr(lifecycle, "daemon_status", fake_daemon_status)
    monkeypatch.setattr(lifecycle, "daemon_start", fake_daemon_start)

    assert lifecycle.daemon_ensure(host="0.0.0.0", port=18765, allow_remote=True) == "started"
    assert seen == {"host": "0.0.0.0", "port": 18765, "allow_remote": True}
