from pathlib import Path

from backlog_py.storage.config import load_config


def test_load_config_reads_optional_priorities(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("projectName: Demo\npriorities: [critical, high, normal]\n", encoding="utf-8")

    assert load_config(path).priorities == ["critical", "high", "normal"]


def test_load_config_defaults_priorities_to_none(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("projectName: Demo\n", encoding="utf-8")

    assert load_config(path).priorities is None
