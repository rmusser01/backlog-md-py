#!/usr/bin/env python3
"""Fail when mypy, or a Ruff rule pyproject.toml ignores, reports a count other than the one recorded here.

Exact, not at-most: a fix has to lower its number in the same change, so the
ratchet only turns one way. The counts hold for the CI lint environment,
``pip install -e ".[dev,tui]"`` with the mypy that pins: another mypy, or no
textual, reports different errors.
"""

from __future__ import annotations

import re
import subprocess  # nosec B404
import sys
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# `python -m mypy`, errors per file.
MYPY_ERRORS = {
    "src/backlog_py/browser/service.py": 23,
    "src/backlog_py/cli/main.py": 4,
    "src/backlog_py/core/repository.py": 4,
    "src/backlog_py/mcp/http_server.py": 1,
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
# Violations of each rule in `[tool.ruff.lint] ignore`, over src and tests.
RUFF_IGNORED = {
    "E501": 48,
    "I001": 63,
    "UP017": 41,
    "UP035": 23,
    "UP037": 14,
    "B904": 4,
    "B009": 2,
}


def _run(*args: str) -> str:
    result = subprocess.run(  # nosec B603
        [sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=600
    )
    if result.returncode not in (0, 1):  # 1 is "found problems"; above that the tool itself failed
        sys.exit(f"{args[0]} failed with exit {result.returncode}:\n{result.stdout}{result.stderr}")
    return result.stdout


def mypy_counts(output: str) -> Counter[str]:
    return Counter(match[1] for match in re.finditer(r"^(.+?):\d+(?::\d+)?: error:", output, re.M))


def ruff_counts(output: str) -> Counter[str]:
    """Counts from `ruff check --statistics`, whose lines start "<count> <rule>"."""
    return Counter({match[2]: int(match[1]) for match in re.finditer(r"^\s*(\d+)\s+([A-Z]+\d+)\b", output, re.M)})


def differences(tool: str, recorded: Mapping[str, int], found: Mapping[str, int]) -> list[str]:
    return [
        f"{tool} {key}: recorded {recorded.get(key, 0)}, found {found.get(key, 0)}"
        for key in sorted(recorded.keys() | found.keys())
        if recorded.get(key, 0) != found.get(key, 0)
    ]


def main() -> int:
    ignored = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]["lint"]["ignore"]
    ruff = Counter[str]()
    if ignored:
        select = ",".join(ignored)
        statistics = _run("ruff", "check", "--select", select, "--config", "lint.ignore=[]", "--statistics", "src", "tests")
        ruff = ruff_counts(statistics)
    changed = differences("mypy", MYPY_ERRORS, mypy_counts(_run("mypy"))) + differences("ruff", RUFF_IGNORED, ruff)
    for line in changed:
        print(line, file=sys.stderr)
    if changed:
        print(
            f"Fix what is new, or record the new counts in {Path(__file__).relative_to(ROOT)}."
            ' Counts are for `pip install -e ".[dev,tui]"`.',
            file=sys.stderr,
        )
        return 1
    print("mypy and the ignored Ruff rules match their recorded counts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
