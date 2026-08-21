from __future__ import annotations

import os
from pathlib import Path

import yaml

from backlog_py.core.models import BacklogProject
from backlog_py.security.paths import PathContainmentError, assert_path_within_base, assert_trusted_subpath
from backlog_py.storage.config import load_config


def discover_project(cwd: Path, explicit_cwd: Path | None = None) -> BacklogProject:
    start = _effective_cwd(cwd, explicit_cwd).resolve()
    root, backlog_dir, config_path = _find_project_paths(start)

    return BacklogProject(
        root=root,
        backlog_dir=backlog_dir,
        config_path=config_path,
        config=load_config(config_path),
    )


def _effective_cwd(cwd: Path, explicit_cwd: Path | None) -> Path:
    if explicit_cwd is not None:
        return explicit_cwd

    env_cwd = os.environ.get("BACKLOG_CWD")
    if env_cwd:
        return Path(env_cwd)

    return cwd


def _find_project_paths(start: Path) -> tuple[Path, Path, Path]:
    for candidate_root in (start, *start.parents):
        discovered = _config_for_root(candidate_root)
        if discovered is not None:
            return discovered

    raise FileNotFoundError(f"No Backlog.md config found from {start}")


def _config_for_root(root: Path) -> tuple[Path, Path, Path] | None:
    root_config = root / "backlog.config.yml"
    if root_config.is_file():
        return root, _backlog_dir_for_root_config(root, root_config), root_config

    backlog_config = root / "backlog" / "config.yml"
    if backlog_config.is_file():
        return root, _trusted_backlog_dir(root, root / "backlog"), backlog_config

    dot_backlog_config = root / ".backlog" / "config.yml"
    if dot_backlog_config.is_file():
        return root, _trusted_backlog_dir(root, root / ".backlog"), dot_backlog_config

    return None


def _backlog_dir_for_root_config(root: Path, config_path: Path) -> Path:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Backlog config must contain a mapping: {config_path}")

    configured = raw.get("backlogDirectory", raw.get("backlog_directory"))
    if configured is None:
        return _trusted_backlog_dir(root, root / "backlog")
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError(f"Backlog config value backlogDirectory must be a non-empty string: {config_path}")

    relative_path = Path(configured)
    if relative_path.is_absolute():
        raise ValueError(f"Backlog config value backlogDirectory must be project-relative: {config_path}")
    try:
        return assert_path_within_base(root, root / relative_path)
    except PathContainmentError as exc:
        raise ValueError(str(exc)) from exc


def _trusted_backlog_dir(root: Path, candidate: Path) -> Path:
    try:
        return assert_trusted_subpath(root, candidate)
    except PathContainmentError as exc:
        raise ValueError(f"Backlog directory anchor cannot be a symlink: {exc}") from exc
