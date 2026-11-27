import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("check_quality_baseline", Path("scripts/check_quality_baseline.py"))
baseline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(baseline)


def test_counts_are_read_from_tool_output_and_every_change_is_reported():
    mypy = baseline.mypy_counts(
        "src/a.py:3: error: Bad  [arg-type]\n"
        "src/a.py:9:5: error: Worse  [union-attr]\n"
        "src/a.py:9: note: See docs\n"
        "src/b.py:1: error: Also bad  [misc]\n"
        "Found 3 errors in 2 files (checked 4 source files)\n"
    )
    ruff = baseline.ruff_counts("48\tE501 \t[ ] line-too-long\n 2\tB009 \t[*] get-attr\nFound 50 errors.\n")

    assert mypy == {"src/a.py": 2, "src/b.py": 1}
    assert ruff == {"E501": 48, "B009": 2}
    # Up, down, new and gone all fail: an improvement has to be recorded too.
    assert baseline.differences("mypy", {"src/a.py": 1, "src/b.py": 1, "src/c.py": 4}, {**mypy, "src/d.py": 1}) == [
        "mypy src/a.py: recorded 1, found 2",
        "mypy src/c.py: recorded 4, found 0",
        "mypy src/d.py: recorded 0, found 1",
    ]
    assert baseline.differences("ruff", {"E501": 48, "B009": 2}, ruff) == []


def test_recorded_rules_are_the_ignored_rules():
    import tomllib

    ignored = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]["lint"]["ignore"]

    assert sorted(baseline.RUFF_IGNORED) == sorted(ignored)
