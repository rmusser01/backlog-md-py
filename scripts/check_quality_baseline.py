#!/usr/bin/env python3
"""Fail when the repository's ignored static-analysis diagnostics change."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
TIMEOUT_SECONDS = 180
MYPY_BASELINE = {
    "src/backlog_py/browser/service.py": 24,
    "src/backlog_py/cli/main.py": 3,
    "src/backlog_py/core/repository.py": 4,
    "src/backlog_py/mcp/protocol.py": 1,
    "src/backlog_py/mcp/tools.py": 2,
    "src/backlog_py/orchestration/reports.py": 2,
    "src/backlog_py/orchestration/service.py": 1,
    "src/backlog_py/runtime/locks.py": 4,
    "src/backlog_py/tui/app.py": 1,
    "src/backlog_py/tui/data.py": 11,
    "src/backlog_py/tui/models.py": 3,
    "src/backlog_py/tui/screens.py": 1,
    "src/backlog_py/tui/widgets.py": 2,
}
RUFF_BASELINE = {
    "E501": 45,
    "I001": 61,
    "UP017": 32,
    "UP035": 21,
    "UP037": 13,
    "B904": 4,
    "B009": 2,
}
_MYPY_ERROR = re.compile(r"^(.*):\d+(?::\d+)?: error:")
_RUFF_STATISTIC = re.compile(r"^\s*(\d+)\s+([A-Z][A-Z0-9]+)\b")


class ToolFailure(RuntimeError):
    """A quality tool could not produce trustworthy diagnostics."""


def load_configured_ruff_ignores(path: Path = PYPROJECT_PATH) -> set[str]:
    """Load the ignored Ruff rules that must have exact baselines."""
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
        ignores = config["tool"]["ruff"]["lint"]["ignore"]
        if not isinstance(ignores, list) or not all(isinstance(rule, str) for rule in ignores):
            raise TypeError("tool.ruff.lint.ignore must be a list of strings")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ToolFailure(f"unable to read Ruff ignores from {path}: {exc}") from exc
    return set(ignores)


def parse_mypy_output(output: str, repo_root: Path = REPO_ROOT) -> Counter[str]:
    """Count mypy errors by repository-relative POSIX path."""
    counts: Counter[str] = Counter()
    root = repo_root.resolve()
    for line in output.splitlines():
        match = _MYPY_ERROR.match(line)
        if match is None:
            continue
        reported = Path(match.group(1))
        absolute = reported.resolve() if reported.is_absolute() else (root / reported).resolve()
        counts[absolute.relative_to(root).as_posix()] += 1
    return counts


def parse_ruff_statistics(output: str) -> Counter[str]:
    """Count Ruff statistics by rule code."""
    counts: Counter[str] = Counter()
    for line in output.splitlines():
        if match := _RUFF_STATISTIC.match(line):
            counts[match.group(2)] += int(match.group(1))
    return counts


def baseline_deltas(expected: Mapping[str, int], actual: Mapping[str, int]) -> list[str]:
    """Describe every count that differs, including reductions."""
    deltas = []
    for key in sorted(set(expected) | set(actual)):
        expected_count = expected.get(key, 0)
        actual_count = actual.get(key, 0)
        if expected_count != actual_count:
            change = actual_count - expected_count
            deltas.append(f"{key}: expected {expected_count}, found {actual_count} ({change:+d})")
    return deltas


def run_tool(command: list[str]) -> str:
    """Run one diagnostic command and return strictly decoded output."""
    try:
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolFailure(f"unable to run {' '.join(command)}: {exc}") from exc
    try:
        output = result.stdout.decode("utf-8") + result.stderr.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolFailure(f"{' '.join(command)} produced non-UTF-8 output") from exc
    if result.returncode not in {0, 1} or "No module named" in output:
        raise ToolFailure(f"{' '.join(command)} failed with exit {result.returncode}: {output.strip()}")
    return output


def main() -> int:
    """Compare current diagnostics with the complete checked-in baselines."""
    try:
        configured_ignores = load_configured_ruff_ignores()
        baseline_rules = set(RUFF_BASELINE)
        if configured_ignores != baseline_rules:
            raise ToolFailure(
                "Ruff baseline rules do not match tool.ruff.lint.ignore: "
                f"configured={sorted(configured_ignores)}, baseline={sorted(baseline_rules)}"
            )
        mypy_counts = parse_mypy_output(run_tool([sys.executable, "-m", "mypy"]))
        ruff_counts: Counter[str] = Counter()
        for rule in RUFF_BASELINE:
            command = [
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
            ruff_counts.update(parse_ruff_statistics(run_tool(command)))
    except (ToolFailure, ValueError) as exc:
        print(f"quality baseline check failed: {exc}", file=sys.stderr)
        return 2

    failures = [("mypy", baseline_deltas(MYPY_BASELINE, mypy_counts)), ("Ruff", baseline_deltas(RUFF_BASELINE, ruff_counts))]
    mismatched = False
    for label, deltas in failures:
        if deltas:
            mismatched = True
            print(f"{label} baseline changed:", file=sys.stderr)
            for delta in deltas:
                print(f"  {delta}", file=sys.stderr)
    if mismatched:
        print("Update the exact baseline in scripts/check_quality_baseline.py with the reviewed change.", file=sys.stderr)
        return 1
    print("Quality baselines match.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
