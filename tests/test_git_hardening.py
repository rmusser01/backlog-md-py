"""Regression tests for git subprocess hardening (no hangs / no prompts)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from backlog_py.runtime import git as git_module


def test_run_git_is_time_bounded_and_non_interactive(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    git_module._run_git(tmp_path, "status")

    kwargs = captured["kwargs"]
    assert kwargs.get("timeout"), "git invocation has no timeout (can hang forever)"
    env = kwargs.get("env") or {}
    assert env.get("GIT_TERMINAL_PROMPT") == "0", "git may block on an interactive credential prompt"


def test_run_git_timeout_is_reported_as_failure(monkeypatch, tmp_path: Path):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 1))

    monkeypatch.setattr(git_module.subprocess, "run", fake_run)

    result = git_module._run_git(tmp_path, "fetch", "--all")

    assert result.returncode != 0, "a git timeout must surface as a non-zero result, not raise"
