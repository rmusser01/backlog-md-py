from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import _atomic_write_text
from backlog_py.security.paths import PathContainmentError, assert_path_within_base
from backlog_py.storage.config import load_config


DEFAULT_BACKLOG_DIRS = (
    "tasks",
    "completed",
    "drafts",
    "docs",
    "decisions",
    "milestones",
    "archive/tasks",
    "archive/drafts",
    "archive/milestones",
)


class InitProjectError(ValueError):
    """Raised when a project cannot be initialized safely."""


@dataclass(frozen=True)
class InitProjectResult:
    project: BacklogProject
    config_created: bool


def init_project(
    root: Path,
    *,
    project_name: str | None = None,
    backlog_dir: str = "backlog",
    config_location: str = "local",
    task_prefix: str = "task",
) -> InitProjectResult:
    """Create a non-interactive Backlog.md project skeleton."""
    root_path = root.resolve()
    root_path.mkdir(parents=True, exist_ok=True)
    backlog_path = _safe_child(root_path, Path(backlog_dir))
    config_path = _config_path(root_path, backlog_path, config_location)
    backlog_directory = backlog_path.relative_to(root_path).as_posix()

    backlog_path.mkdir(parents=True, exist_ok=True)
    for relative_path in DEFAULT_BACKLOG_DIRS:
        _safe_child(backlog_path, Path(relative_path)).mkdir(parents=True, exist_ok=True)

    config_created = not config_path.exists()
    if config_created:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        root_config_backlog_dir = backlog_directory if config_path.name == "backlog.config.yml" else None
        _atomic_write_text(
            config_path,
            _default_config_source(
                project_name or root_path.name,
                backlog_directory=root_config_backlog_dir,
                task_prefix=task_prefix,
            ),
        )

    return InitProjectResult(
        project=BacklogProject(
            root=root_path,
            backlog_dir=backlog_path,
            config_path=config_path,
            config=load_config(config_path),
        ),
        config_created=config_created,
    )


def _safe_child(root: Path, relative_path: Path) -> Path:
    if relative_path.is_absolute():
        raise InitProjectError(f"Backlog path must be project-relative: {relative_path}")
    try:
        return assert_path_within_base(root, root / relative_path)
    except PathContainmentError as exc:
        raise InitProjectError(str(exc)) from exc


def _config_path(root: Path, backlog_path: Path, config_location: str) -> Path:
    normalized = config_location.strip().casefold()
    backlog_directory = backlog_path.relative_to(root).as_posix()
    if backlog_directory not in {"backlog", ".backlog"}:
        return _safe_child(root, Path("backlog.config.yml"))
    if normalized == "local":
        return _safe_child(backlog_path, Path("config.yml"))
    if normalized == "root":
        return _safe_child(root, Path("backlog.config.yml"))
    raise InitProjectError("config-location must be 'local' or 'root'")


def _default_config_source(
    project_name: str,
    *,
    backlog_directory: str | None = None,
    task_prefix: str = "task",
) -> str:
    normalized_task_prefix = _normalize_task_prefix(task_prefix)
    raw = {
        "projectName": project_name,
        "statuses": ["To Do", "In Progress", "Done"],
        "defaultStatus": "To Do",
        "dateFormat": "yyyy-mm-dd",
        "includeDatetimeInDates": True,
        "defaultPort": 6420,
        "autoOpenBrowser": True,
        "remoteOperations": False,
        "autoCommit": False,
        "bypassGitHooks": False,
        "checkActiveBranches": False,
        "activeBranchDays": 30,
        "prefixes": {"task": normalized_task_prefix},
    }
    if backlog_directory is not None:
        raw["backlogDirectory"] = backlog_directory
    yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=False).strip()
    return f"{yaml_text}\n"


def _normalize_task_prefix(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InitProjectError("task-prefix must be non-empty")
    if not normalized.isalpha():
        raise InitProjectError("task-prefix must contain only letters")
    return normalized
