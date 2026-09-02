from pathlib import Path

import pytest
import yaml

from backlog_py.core.models import BacklogProject
from backlog_py.storage import config as config_module
from backlog_py.storage.config import load_config, set_config_value
from backlog_py.storage.project import discover_project


@pytest.fixture
def project(tmp_path: Path) -> BacklogProject:
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / "config.yml").write_text(
        "projectName: Demo\n"
        "statuses: [To Do, Done]\n"
        "defaultStatus: To Do\n"
        "zeroPaddedIds: 4\n"
        "onStatusChange: echo changed\n"
        "custom:\n"
        "  preserve: true\n",
        encoding="utf-8",
    )
    return discover_project(Path.cwd(), explicit_cwd=tmp_path)


def _record_atomic_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[Path, str, Path | None]]:
    writes: list[tuple[Path, str, Path | None]] = []
    atomic_write = config_module._atomic_write_text

    def record_write(path: Path, content: str, base: Path | None = None) -> None:
        writes.append((path, content, base))
        atomic_write(path, content, base=base)

    monkeypatch.setattr(config_module, "_atomic_write_text", record_write)
    return writes


def test_load_config_reads_optional_priorities(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("projectName: Demo\npriorities: [critical, high, normal]\n", encoding="utf-8")

    assert load_config(path).priorities == ["critical", "high", "normal"]


def test_load_config_defaults_priorities_to_none(tmp_path: Path) -> None:
    path = tmp_path / "config.yml"
    path.write_text("projectName: Demo\n", encoding="utf-8")

    assert load_config(path).priorities is None


def test_set_config_values_writes_once_and_preserves_unknown_keys(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes = _record_atomic_writes(monkeypatch)

    config = config_module.set_config_values(
        project,
        {"statuses": "[Ready, Done]", "defaultStatus": "Ready"},
    )

    assert len(writes) == 1
    assert config.statuses == ["Ready", "Done"]
    assert config.default_status == "Ready"
    assert yaml.safe_load(project.config_path.read_text(encoding="utf-8"))["custom"] == {"preserve": True}


def test_set_config_values_validates_every_value_before_writing(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = project.config_path.read_bytes()
    writes = _record_atomic_writes(monkeypatch)

    with pytest.raises(ValueError, match="valid port number"):
        config_module.set_config_values(project, {"projectName": "Changed", "defaultPort": "0"})

    assert writes == []
    assert project.config_path.read_bytes() == before


def test_set_config_values_parses_priorities_as_a_list(project: BacklogProject) -> None:
    config = config_module.set_config_values(project, {"priorities": "[critical, high]"})

    assert config.priorities == ["critical", "high"]
    assert yaml.safe_load(project.config_path.read_text(encoding="utf-8"))["priorities"] == ["critical", "high"]


def test_set_config_values_validates_complete_candidate_before_writing(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    raw["defaultAssignee"] = {"invalid": True}
    project.config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    before = project.config_path.read_bytes()
    writes = _record_atomic_writes(monkeypatch)

    with pytest.raises(ValueError, match="default_assignee must be a string"):
        config_module.set_config_values(project, {"projectName": "Changed"})

    assert writes == []
    assert project.config_path.read_bytes() == before


def test_set_config_values_empty_updates_do_not_write_or_reformat(
    project: BacklogProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = project.config_path.read_bytes()
    writes = _record_atomic_writes(monkeypatch)

    config = config_module.set_config_values(project, {})

    assert writes == []
    assert project.config_path.read_bytes() == before
    assert config == load_config(project.config_path)


def test_set_config_values_preserves_existing_alias_key(
    project: BacklogProject,
) -> None:
    raw = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    raw["default_status"] = raw.pop("defaultStatus")
    project.config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    config_module.set_config_values(project, {"defaultStatus": "Ready"})

    written = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    assert written["default_status"] == "Ready"
    assert "defaultStatus" not in written


def test_set_config_values_reconciles_duplicate_aliases(project: BacklogProject) -> None:
    raw = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    raw["default_status"] = "Stale"
    project.config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    config = config_module.set_config_values(project, {"defaultStatus": "Ready"})

    written = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    assert written["default_status"] == "Ready"
    assert written["defaultStatus"] == "Ready"
    assert config.default_status == "Ready"


def test_set_config_values_removes_optional_values_like_single_value_api(
    project: BacklogProject,
) -> None:
    raw = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    raw["zero_padded_ids"] = 2
    raw["on_status_change"] = "echo stale"
    project.config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    config = config_module.set_config_values(
        project,
        {"zeroPaddedIds": "0", "onStatusChange": "disabled"},
    )

    written = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    assert "zeroPaddedIds" not in written
    assert "zero_padded_ids" not in written
    assert "onStatusChange" not in written
    assert "on_status_change" not in written
    assert config.zero_padded_ids is None
    assert config.on_status_change is None


def test_set_config_value_keeps_optional_removal_return_contract(project: BacklogProject) -> None:
    assert set_config_value(project, "zeroPaddedIds", "0") == ("zeroPaddedIds", "(disabled)")


def test_set_config_value_keeps_requested_alias_return_with_duplicates(project: BacklogProject) -> None:
    raw = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    raw["default_status"] = "Stale"
    project.config_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    result = set_config_value(project, "defaultStatus", "Ready")

    written = yaml.safe_load(project.config_path.read_text(encoding="utf-8"))
    assert result == ("defaultStatus", "Ready")
    assert written["default_status"] == "Ready"
    assert written["defaultStatus"] == "Ready"
