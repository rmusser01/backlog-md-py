"""Regression tests for clean CLI error handling (no raw tracebacks)."""
from __future__ import annotations

import shutil
from pathlib import Path

import click
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
