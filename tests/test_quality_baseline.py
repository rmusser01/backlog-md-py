import runpy
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

import pytest

SCRIPT = Path("scripts/check_quality_baseline.py")


@pytest.fixture
def checker():
    return runpy.run_path(SCRIPT)


def test_parse_mypy_output_counts_repo_relative_files(checker, tmp_path):
    root = tmp_path / "project"
    output = "\n".join(
        (
            "src/backlog_py/a.py:1: error: first [arg-type]",
            f"{root}/src/backlog_py/a.py:2: error: second [attr-defined]",
            "./src/backlog_py/b.py:3: note: context",
            "./src/backlog_py/b.py:4: error: third [assignment]",
        )
    )

    assert checker["parse_mypy_output"](output, root) == Counter(
        {
            "src/backlog_py/a.py": 2,
            "src/backlog_py/b.py": 1,
        }
    )


def test_parse_ruff_statistics_counts_rules(checker):
    output = "45\tE501\tline-too-long\n2 B009 [*] get-attr-with-constant\nFound 47 errors.\n"

    assert checker["parse_ruff_statistics"](output) == Counter({"E501": 45, "B009": 2})


def test_ruff_baseline_rules_exactly_match_configured_ignores(checker):
    assert checker["load_configured_ruff_ignores"]() == set(checker["RUFF_BASELINE"])


def test_reviewed_baselines_record_branch_fixes_instead_of_omitting_diagnostics(checker):
    design = Path("docs/superpowers/specs/2026-08-21-review-findings-fixes-design.md").read_text(encoding="utf-8")
    plan = Path("docs/superpowers/plans/2026-08-21-review-findings-fixes.md").read_text(encoding="utf-8")

    assert checker["RUFF_BASELINE"]["I001"] == 59
    assert sum(checker["MYPY_BASELINE"].values()) == 59
    assert "src/backlog_py/mcp/http_server.py" not in checker["MYPY_BASELINE"]
    for document in (design, plan):
        assert "mcp/http_server.py" in document
        assert "str-bytes-safe" in document
        assert "intentionally" in document


def test_main_fails_before_running_tools_when_ruff_rule_sets_drift(checker, monkeypatch, capsys):
    globals_ = checker["main"].__globals__
    monkeypatch.setitem(globals_, "load_configured_ruff_ignores", lambda: {"E501", "UNKNOWN"})
    monkeypatch.setitem(globals_, "run_tool", lambda command: pytest.fail(f"unexpected tool call: {command}"))

    assert checker["main"]() == 2
    assert "Ruff baseline rules do not match" in capsys.readouterr().err


def test_baseline_deltas_report_increases_and_improvements(checker):
    expected = {"a": 2, "b": 1}

    assert checker["baseline_deltas"](expected, {"a": 3, "b": 1}) == ["a: expected 2, found 3 (+1)"]
    assert checker["baseline_deltas"](expected, {"a": 1}) == [
        "a: expected 2, found 1 (-1)",
        "b: expected 1, found 0 (-1)",
    ]


@pytest.mark.parametrize(
    "failure",
    (
        FileNotFoundError("missing tool"),
        subprocess.TimeoutExpired(["tool"], 1),
    ),
)
def test_run_tool_wraps_missing_tools_and_timeouts(checker, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(checker["subprocess"], "run", fail)

    with pytest.raises(checker["ToolFailure"]):
        checker["run_tool"](["tool"])


@pytest.mark.parametrize(
    "completed",
    (
        subprocess.CompletedProcess(["tool"], 2, b"", b"fatal"),
        subprocess.CompletedProcess(["tool"], -9, b"", b"terminated"),
        subprocess.CompletedProcess(["tool"], 1, b"", b"No module named quality_tool"),
        subprocess.CompletedProcess(["tool"], 0, b"\xff", b""),
    ),
)
def test_run_tool_rejects_failed_tools_and_undecodable_output(checker, monkeypatch, completed):
    monkeypatch.setattr(checker["subprocess"], "run", lambda *args, **kwargs: completed)

    with pytest.raises(checker["ToolFailure"]):
        checker["run_tool"](["tool"])


def test_run_tool_accepts_diagnostics_and_pins_execution_to_repo(checker, monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 1, b"diagnostic\n", b"")

    monkeypatch.setattr(checker["subprocess"], "run", fake_run)

    assert checker["run_tool"](["tool"]) == "diagnostic\n"
    assert captured == {
        "cwd": checker["REPO_ROOT"],
        "capture_output": True,
        "timeout": checker["TIMEOUT_SECONDS"],
        "check": False,
    }


def test_main_runs_exact_mypy_and_per_rule_ruff_commands(checker, monkeypatch):
    calls = []
    mypy_output = "\n".join(
        f"{path}:1: error: diagnostic" for path, count in checker["MYPY_BASELINE"].items() for _ in range(count)
    )

    def fake_run(command):
        calls.append(command)
        if command == [sys.executable, "-m", "mypy"]:
            return mypy_output
        rule = command[5]
        return f"{checker['RUFF_BASELINE'][rule]}\t{rule}\tdiagnostic\n"

    monkeypatch.setitem(checker["main"].__globals__, "run_tool", fake_run)

    assert checker["main"]() == 0
    assert calls[0] == [sys.executable, "-m", "mypy"]
    assert calls[1:] == [
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            rule,
            "--config",
            "lint.ignore=[]",
            "--statistics",
            "src",
            "tests",
        ]
        for rule in checker["RUFF_BASELINE"]
    ]


def test_mypy_configuration_keeps_src_as_its_only_target():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["tool"]["mypy"]["files"] == ["src"]
