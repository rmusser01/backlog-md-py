from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BacklogConfig:
    project_name: str
    default_assignee: str | None = None
    statuses: list[str] | None = None
    default_status: str = "To Do"
    date_format: str = "yyyy-mm-dd"
    include_datetime_in_dates: bool = True
    default_editor: str | None = None
    auto_open_browser: bool = True
    default_port: int = 6420
    remote_operations: bool = True
    auto_commit: bool = False
    bypass_git_hooks: bool = False
    on_status_change: str | None = None
    zero_padded_ids: int | None = None
    task_prefix: str = "task"
    check_active_branches: bool = True
    # onStatusChange carried in a task file's own frontmatter executes a shell
    # command. Task markdown arrives from clones, branches, and PRs, so that is
    # opt-in; config-level onStatusChange is reviewed by its owner and stays on.
    task_frontmatter_status_callbacks: bool = False
    active_branch_days: int = 30
    definition_of_done: list[str] | None = None
    priorities: list[str] | None = None


@dataclass(frozen=True)
class BacklogProject:
    root: Path
    backlog_dir: Path
    config_path: Path
    config: BacklogConfig


@dataclass(frozen=True)
class ChecklistItem:
    raw_line: str
    checked: bool
    text: str
    item_id: str | None = None


@dataclass(frozen=True)
class TaskMarkdownSection:
    name: str
    marker: str
    raw: str
    content: str
    # Offsets of ``raw`` within the full task source. Writers splice by offset
    # rather than searching for ``raw``, so duplicated identical blocks cannot
    # make an edit land in a block the parser does not read back.
    start: int = -1
    end: int = -1


@dataclass(frozen=True)
class ParsedTaskMarkdown:
    raw_source: str
    raw_frontmatter: str | None
    frontmatter: dict[str, Any]
    body: str
    sections: dict[str, TaskMarkdownSection]
    checklists: dict[str, list[ChecklistItem]]
