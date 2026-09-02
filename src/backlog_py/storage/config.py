from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from backlog_py.core.errors import NotFoundError
from backlog_py.core.models import BacklogConfig, BacklogProject
from backlog_py.security.paths import PathContainmentError, assert_path_within_base


_KEY_ALIASES = {
    "project_name": ("project_name", "projectName"),
    "default_assignee": ("default_assignee", "defaultAssignee"),
    "statuses": ("statuses",),
    "default_status": ("default_status", "defaultStatus"),
    "date_format": ("date_format", "dateFormat"),
    "include_datetime_in_dates": (
        "include_datetime_in_dates",
        "include_date_time_in_dates",
        "includeDateTimeInDates",
        "includeDatetimeInDates",
    ),
    "default_editor": ("default_editor", "defaultEditor"),
    "auto_open_browser": ("auto_open_browser", "autoOpenBrowser"),
    "default_port": ("default_port", "defaultPort"),
    "remote_operations": ("remote_operations", "remoteOperations"),
    "auto_commit": ("auto_commit", "autoCommit"),
    "bypass_git_hooks": ("bypass_git_hooks", "bypassGitHooks"),
    "on_status_change": ("on_status_change", "onStatusChange"),
    "zero_padded_ids": ("zero_padded_ids", "zeroPaddedIds"),
    "task_prefix": ("task_prefix", "taskPrefix"),
    "check_active_branches": ("check_active_branches", "checkActiveBranches"),
    "task_frontmatter_status_callbacks": (
        "task_frontmatter_status_callbacks",
        "taskFrontmatterStatusCallbacks",
    ),
    "active_branch_days": ("active_branch_days", "activeBranchDays"),
    "definition_of_done": ("definition_of_done", "definitionOfDone"),
}
_NORMALIZED_KEY_BY_ALIAS = {
    alias.casefold(): normalized_key
    for normalized_key, aliases in _KEY_ALIASES.items()
    for alias in aliases
}
_BOOLEAN_CONFIG_KEYS = {
    "include_datetime_in_dates",
    "auto_open_browser",
    "remote_operations",
    "auto_commit",
    "bypass_git_hooks",
    "check_active_branches",
    "task_frontmatter_status_callbacks",
}
_INTEGER_CONFIG_KEYS = {"active_branch_days"}
_PORT_CONFIG_KEYS = {"default_port"}
_OPTIONAL_PADDING_KEYS = {"zero_padded_ids"}
_LIST_CONFIG_KEYS = {"statuses", "definition_of_done"}
_READ_ONLY_CONFIG_KEYS = {"task_prefix"}
_READ_ONLY_CONFIG_ALIASES = {"prefixes"}


def load_config(path: Path) -> BacklogConfig:
    """Load a Backlog.md YAML config file into the normalized Python model."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Backlog config must contain a mapping: {path}")

    return BacklogConfig(
        project_name=_string_value(raw, "project_name", _default_project_name(path)),
        default_assignee=_optional_string_value(raw, "default_assignee"),
        statuses=_optional_string_list(_get(raw, "statuses", None)),
        default_status=_string_value(raw, "default_status", "To Do"),
        date_format=_string_value(raw, "date_format", "yyyy-mm-dd"),
        include_datetime_in_dates=_bool_value(raw, "include_datetime_in_dates", True),
        default_editor=_optional_string_value(raw, "default_editor"),
        auto_open_browser=_bool_value(raw, "auto_open_browser", True),
        default_port=_int_value(raw, "default_port", 6420),
        remote_operations=_bool_value(raw, "remote_operations", True),
        auto_commit=_bool_value(raw, "auto_commit", False),
        bypass_git_hooks=_bool_value(raw, "bypass_git_hooks", False),
        on_status_change=_optional_on_status_change_value(raw, "on_status_change"),
        zero_padded_ids=_optional_positive_int(raw, "zero_padded_ids"),
        task_prefix=_task_prefix_value(raw),
        check_active_branches=_bool_value(raw, "check_active_branches", True),
        task_frontmatter_status_callbacks=_bool_value(raw, "task_frontmatter_status_callbacks", False),
        active_branch_days=_int_value(raw, "active_branch_days", 30),
        definition_of_done=_optional_definition_of_done_defaults(_get(raw, "definition_of_done", None)),
        priorities=_optional_string_list(raw.get("priorities")),
    )


def get_definition_of_done_defaults(project: BacklogProject) -> list[str]:
    """Return current project-level Definition of Done defaults from disk."""
    return list(load_config(project.config_path).definition_of_done or [])


def replace_definition_of_done_defaults(project: BacklogProject, items: object) -> BacklogConfig:
    """Persist Definition of Done defaults and return the refreshed config."""
    normalized = normalize_definition_of_done_defaults(items)
    raw = _load_raw_config(project.config_path)
    key = "definition_of_done" if "definition_of_done" in raw else "definitionOfDone"
    raw[key] = normalized
    yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).strip()
    _atomic_write_text(project.config_path, f"{yaml_text}\n", base=project.root)
    return load_config(project.config_path)


def normalize_definition_of_done_defaults(items: object) -> list[str]:
    """Normalize Definition of Done defaults from any supported interface."""
    if not isinstance(items, (list, tuple)):
        raise ValueError("Definition of Done defaults must be a list of strings")
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("Definition of Done defaults must be strings")
        text = item.strip()
        if text:
            normalized.append(text)
    return normalized


def get_config_value(project: BacklogProject, key: str) -> Any:
    """Return a raw config value or known effective default by CLI key."""
    raw = _load_raw_config(project.config_path)
    normalized_key = _normalized_config_key(key)
    raw_key = _find_existing_config_key(raw, key)
    if raw_key is not None:
        if normalized_key == "zero_padded_ids":
            return _zero_padded_ids_display(raw[raw_key])
        if normalized_key == "on_status_change":
            return _on_status_change_display(raw[raw_key])
        return raw[raw_key]

    if normalized_key is None:
        raise NotFoundError(f"Unknown config key: {key}")
    value = getattr(load_config(project.config_path), normalized_key)
    if normalized_key == "zero_padded_ids":
        return _zero_padded_ids_display(value)
    if normalized_key == "on_status_change":
        return _on_status_change_display(value)
    return value


def set_config_value(project: BacklogProject, key: str, value: str) -> tuple[str, Any]:
    """Persist one config value from CLI text and return the written key/value."""
    raw = _load_raw_config(project.config_path)
    raw_key, display_value = _apply_config_value(raw, key, value)
    yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).strip()
    _atomic_write_text(project.config_path, f"{yaml_text}\n", base=project.root)
    return raw_key, display_value


def set_config_values(project: BacklogProject, updates: Mapping[str, str]) -> BacklogConfig:
    """Persist multiple config values with one atomic replacement."""
    raw = _load_raw_config(project.config_path)
    for key, value in updates.items():
        _apply_config_value(raw, key, value)
    yaml_text = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True).strip()
    _atomic_write_text(project.config_path, f"{yaml_text}\n", base=project.root)
    return load_config(project.config_path)


def _apply_config_value(raw: dict[Any, Any], key: str, value: str) -> tuple[str, Any]:
    normalized_key = _normalized_config_key(key)
    if normalized_key in _READ_ONLY_CONFIG_KEYS or key.casefold() in _READ_ONLY_CONFIG_ALIASES:
        raise ValueError(
            "Task prefix cannot be changed after initialization. "
            "The prefix is set during 'backlog init' and is permanent to avoid breaking existing task IDs."
        )
    parsed_value = _parse_config_value(normalized_key, value)
    raw_key = _target_config_key(raw, key, normalized_key)
    if normalized_key in {"zero_padded_ids", "on_status_change"} and parsed_value is None:
        existing_key = _find_existing_config_key(raw, key)
        if existing_key is not None:
            raw.pop(existing_key, None)
        display_value = (
            _zero_padded_ids_display(parsed_value)
            if normalized_key == "zero_padded_ids"
            else _on_status_change_display(parsed_value)
        )
    else:
        raw[raw_key] = parsed_value
        display_value = parsed_value
    return raw_key, display_value


def _get(raw: dict[Any, Any], normalized_key: str, default: Any) -> Any:
    for key in _KEY_ALIASES[normalized_key]:
        if key in raw:
            return raw[key]
    return default


def _load_raw_config(path: Path) -> dict[Any, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Backlog config must contain a mapping: {path}")
    return raw


def _atomic_write_text(path: Path, content: str, base: Path | None = None) -> None:
    # Anchor containment on a trusted base (the project root) when the caller can
    # supply one: checking a file against its own parent is vacuous, so a symlink
    # planted at backlog/ would otherwise redirect the write outside the project.
    try:
        safe_path = assert_path_within_base(base if base is not None else path.parent, path)
    except PathContainmentError as exc:
        raise ValueError(str(exc)) from exc
    temp_name: str | None = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",  # write content verbatim; no platform newline translation
        dir=safe_path.parent,
        prefix=f".{safe_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp_file:
        temp_name = temp_file.name
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
    try:
        os.replace(temp_name, safe_path)
    except Exception:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
        raise


def _optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("Backlog config list values must be lists")
    return [str(item) for item in value]


def _optional_definition_of_done_defaults(value: Any) -> list[str] | None:
    if value is None:
        return None
    return normalize_definition_of_done_defaults(value)


def _string_value(raw: dict[Any, Any], normalized_key: str, default: str) -> str:
    value = _get(raw, normalized_key, default)
    if not isinstance(value, str):
        raise ValueError(f"Backlog config value {normalized_key} must be a string")
    return value


def _optional_string_value(raw: dict[Any, Any], normalized_key: str) -> str | None:
    value = _get(raw, normalized_key, None)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Backlog config value {normalized_key} must be a string")
    return value


def _optional_on_status_change_value(raw: dict[Any, Any], normalized_key: str) -> str | None:
    value = _get(raw, normalized_key, None)
    if value is None:
        return None
    if isinstance(value, bool):
        if value:
            raise ValueError(f"Backlog config value {normalized_key} must be a command string or false")
        return None
    if not isinstance(value, str):
        raise ValueError(f"Backlog config value {normalized_key} must be a command string")
    return _parse_on_status_change_value(value)


def _bool_value(raw: dict[Any, Any], normalized_key: str, default: bool) -> bool:
    value = _get(raw, normalized_key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Backlog config value {normalized_key} must be a boolean")
    return value


def _optional_positive_int(raw: dict[Any, Any], normalized_key: str) -> int | None:
    value = _get(raw, normalized_key, None)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Backlog config value {normalized_key} must be an integer")
    if value <= 0:
        return None
    return value


def _task_prefix_value(raw: dict[Any, Any]) -> str:
    for direct_key in _KEY_ALIASES["task_prefix"]:
        if direct_key in raw:
            return _normalize_task_prefix(raw[direct_key])
    prefixes = raw.get("prefixes")
    if prefixes is None:
        return "task"
    if not isinstance(prefixes, dict):
        raise ValueError("Backlog config value prefixes must be a mapping")
    return _normalize_task_prefix(prefixes.get("task", "task"))


def _normalize_task_prefix(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Backlog config value task_prefix must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("Backlog config value task_prefix must be non-empty")
    if not normalized.isalpha():
        raise ValueError("Backlog config value task_prefix must contain only letters")
    return normalized


def _int_value(raw: dict[Any, Any], normalized_key: str, default: int) -> int:
    value = _get(raw, normalized_key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Backlog config value {normalized_key} must be an integer")
    return value


def _find_existing_config_key(raw: dict[Any, Any], key: str) -> Any | None:
    if key in raw:
        return key
    normalized_key = _normalized_config_key(key)
    if normalized_key is None:
        return None
    for alias in _KEY_ALIASES[normalized_key]:
        if alias in raw:
            return alias
    return None


def _target_config_key(raw: dict[Any, Any], key: str, normalized_key: str | None) -> str:
    existing = _find_existing_config_key(raw, key)
    if existing is not None:
        return str(existing)
    if normalized_key is None:
        return key
    return _KEY_ALIASES[normalized_key][-1]


def _normalized_config_key(key: str) -> str | None:
    return _NORMALIZED_KEY_BY_ALIAS.get(key.casefold())


def _parse_config_value(normalized_key: str | None, value: str) -> Any:
    if normalized_key in _BOOLEAN_CONFIG_KEYS:
        return _parse_bool_config_value(normalized_key, value)
    if normalized_key in _PORT_CONFIG_KEYS:
        return _parse_port_config_value(normalized_key, value)
    if normalized_key in _OPTIONAL_PADDING_KEYS:
        return _parse_zero_padded_ids_value(normalized_key, value)
    if normalized_key in _INTEGER_CONFIG_KEYS:
        return _parse_int_config_value(normalized_key, value)
    if normalized_key == "definition_of_done":
        return _parse_definition_of_done_config_value(value)
    if normalized_key in _LIST_CONFIG_KEYS:
        return _parse_list_config_value(value)
    if normalized_key == "on_status_change":
        return _parse_on_status_change_value(value)
    return value


def _parse_bool_config_value(normalized_key: str | None, value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Backlog config value {normalized_key} must be a boolean")


def _parse_int_config_value(normalized_key: str | None, value: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as exc:
        raise ValueError(f"Backlog config value {normalized_key} must be an integer") from exc
    return parsed


def _parse_port_config_value(normalized_key: str | None, value: str) -> int:
    parsed = _parse_int_config_value(normalized_key, value)
    if parsed < 1 or parsed > 65535:
        raise ValueError(f"Backlog config value {normalized_key} must be a valid port number (1-65535)")
    return parsed


def _parse_zero_padded_ids_value(normalized_key: str | None, value: str) -> int | None:
    parsed = _parse_int_config_value(normalized_key, value)
    if parsed < 0:
        raise ValueError(f"Backlog config value {normalized_key} must be a non-negative number")
    return parsed or None


def _zero_padded_ids_display(value: Any) -> Any:
    if value is None:
        return "(disabled)"
    if isinstance(value, int) and not isinstance(value, bool) and value <= 0:
        return "(disabled)"
    return value


def _parse_on_status_change_value(value: str) -> str | None:
    normalized = value.strip()
    if normalized.casefold() in {"", "false", "0", "no", "disabled", "(disabled)"}:
        return None
    return normalized


def _on_status_change_display(value: Any) -> str:
    if value is None or value is False:
        return "(disabled)"
    normalized = str(value).strip()
    return normalized or "(disabled)"


def _parse_list_config_value(value: str) -> list[str]:
    parsed = yaml.safe_load(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    if isinstance(parsed, str):
        return [item.strip() for item in parsed.split(",") if item.strip()]
    raise ValueError("Backlog config list values must be lists")


def _parse_definition_of_done_config_value(value: str) -> list[str]:
    parsed = yaml.safe_load(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return normalize_definition_of_done_defaults(parsed)
    if isinstance(parsed, str):
        return normalize_definition_of_done_defaults(parsed.split(","))
    raise ValueError("Definition of Done defaults must be a list of strings")


def _default_project_name(path: Path) -> str:
    if path.name == "backlog.config.yml":
        return path.parent.name
    if path.parent.name in {"backlog", ".backlog"}:
        return path.parent.parent.name
    return path.parent.name
