from __future__ import annotations

from dataclasses import dataclass


Classification = str


@dataclass(frozen=True)
class CompatibilityItem:
    name: str
    classification: Classification
    upstream_reference: str
    expected: str
    status: str
    fixture: str | None = None
    deferred_reason: str | None = None


@dataclass(frozen=True)
class CompatibilityInventory:
    items: tuple[CompatibilityItem, ...]


def load_builtin_inventory() -> CompatibilityInventory:
    items = (
        _golden("cli:help", "CLI-INSTRUCTIONS.md", "backlog --help"),
        _golden("cli:init", "CLI-INSTRUCTIONS.md", "backlog init [project-name] --defaults"),
        _golden(
            "cli:task-list-plain",
            "CLI-INSTRUCTIONS.md",
            "backlog task list --status <status> --priority <priority> -a <assignee> -l <label> --milestone <milestone> --parent <taskId> --plain",
        ),
        _golden("cli:task-view-plain", "CLI-INSTRUCTIONS.md", "backlog task <id> --plain"),
        _golden(
            "cli:search-plain",
            "CLI-INSTRUCTIONS.md",
            "backlog search <query> --type <type> --status <status> --priority <priority> --modified-file <path> --limit <number> --plain; unfiltered search returns tasks, documents, and decisions",
        ),
        _golden("cli:board", "CLI-INSTRUCTIONS.md", "backlog board"),
        _golden("cli:overview", "CLI-INSTRUCTIONS.md", "backlog overview"),
        _golden(
            "cli:board-export",
            "CLI-INSTRUCTIONS.md",
            "backlog board export [file] --readme --force --export-version <version>",
        ),
        _golden("cli:config-list", "ADVANCED-CONFIG.md", "backlog config list"),
        _golden(
            "cli:task-create",
            "CLI-INSTRUCTIONS.md",
            "backlog task create <title> --draft -d <text> -s <status> --plan <text> --notes <text> --final-summary <text> --parent <taskId> --milestone <milestone> --ordinal <number> --ref <item> --doc <item> --modified-file <path> -a <assignee> -l <label> --priority <priority> --ac <item> --dod <item> --no-dod-defaults --dep <id> --plain",
        ),
        _golden(
            "cli:draft-create",
            "CLI-INSTRUCTIONS.md",
            "backlog draft create <title> -d <text> -a <assignee> -l <label>",
        ),
        _golden("cli:draft-list", "CLI-INSTRUCTIONS.md", "backlog draft list --plain"),
        _golden("cli:draft-view", "CLI-INSTRUCTIONS.md", "backlog draft view <id> --plain"),
        _golden("cli:draft-promote", "CLI-INSTRUCTIONS.md", "backlog draft promote <id>"),
        _golden("cli:task-demote", "CLI-INSTRUCTIONS.md", "backlog task demote <id>"),
        _golden("cli:draft-archive", "CLI-INSTRUCTIONS.md", "backlog draft archive <id>"),
        _golden(
            "cli:task-edit",
            "CLI-INSTRUCTIONS.md",
            "backlog task edit <id> --plan <text> --milestone <milestone> --ordinal <number> --clear-milestone --ref <item> --doc <item> --modified-file <path> -a <assignee> -l <label> --priority <priority> --ac <item> --remove-ac <index> --plain",
        ),
        _golden(
            "cli:task-edit-rich-sections",
            "CLI-INSTRUCTIONS.md",
            "backlog task edit <id> --notes <text> --append-notes <text> --final-summary <text> --append-final-summary <text> --clear-final-summary --plain",
        ),
        _golden(
            "cli:task-edit-checklist-state",
            "CLI-INSTRUCTIONS.md",
            "backlog task edit <id> --check-ac <index> --uncheck-ac <index> --check-dod <index> --uncheck-dod <index> --remove-ac <index> --remove-dod <index> --plain",
        ),
        _golden("cli:task-archive", "CLI-INSTRUCTIONS.md", "backlog task archive <id> --plain"),
        _golden("cli:cleanup", "CLI-INSTRUCTIONS.md", "backlog cleanup"),
        _golden("cli:doc-list", "CLI-INSTRUCTIONS.md", "backlog doc list"),
        _golden("cli:doc-view", "CLI-INSTRUCTIONS.md", "backlog doc view <path-or-id>"),
        _golden(
            "cli:doc-create",
            "CLI-INSTRUCTIONS.md",
            "backlog doc create <title> -p <path> -t <type> --tags <tags> --content <body>",
        ),
        _golden(
            "cli:doc-update",
            "CLI-INSTRUCTIONS.md",
            "backlog doc update <path-or-id> --title <title> -p <path> -t <type> --tags <tags> --content <body>",
        ),
        _golden(
            "cli:decision-create",
            "CLI-INSTRUCTIONS.md",
            'backlog decision create "Title" -s <status>',
        ),
        _golden("cli:milestone-list", "CLI-INSTRUCTIONS.md", "backlog milestone list"),
        _golden("cli:milestone-add", "CLI-INSTRUCTIONS.md", "backlog milestone add <name>"),
        _golden("cli:milestone-rename", "CLI-INSTRUCTIONS.md", "backlog milestone rename <old> <new>"),
        _golden("cli:milestone-remove", "CLI-INSTRUCTIONS.md", "backlog milestone remove <name>"),
        _golden("cli:milestone-archive", "CLI-INSTRUCTIONS.md", "backlog milestone archive <name>"),
        _golden("cli:config-get", "CLI-INSTRUCTIONS.md", "backlog config get <key>"),
        _golden("cli:config-set", "CLI-INSTRUCTIONS.md", "backlog config set <key> <value>"),
        _golden("cli:config-dod-defaults-get", "ADVANCED-CONFIG.md", "backlog config dod-defaults-get"),
        _golden(
            "cli:config-dod-defaults-upsert",
            "ADVANCED-CONFIG.md",
            "backlog config dod-defaults-upsert [item...]",
        ),
        _golden("cli:agents-update-instructions", "CLI-INSTRUCTIONS.md", "backlog agents --update-instructions"),
        _implemented(
            "config:extended-options",
            "config-implemented",
            "ADVANCED-CONFIG.md",
            "config get/set/list defaultAssignee, dateFormat, includeDatetimeInDates, defaultEditor, defaultPort, autoOpenBrowser, onStatusChange, zeroPaddedIds",
        ),
        _implemented(
            "config:task-prefix",
            "config-implemented",
            "CLI init/config source",
            "init --task-prefix, config list taskPrefix read-only, and generated task/subtask IDs use prefixes.task",
        ),
        _golden("mcp:workflow-overview", "agent-nudge.md", "backlog://workflow/overview"),
        _golden("mcp:task-workflow-alias", "agent-nudge.md", "backlog://docs/task-workflow"),
        _golden("mcp:board", "MCP tools", "task_board(project)"),
        _golden(
            "mcp:task-list",
            "MCP tools",
            "task_list(project, status=None, limit=100, *, assignee=None, labels=None, priority=None, milestone=None, parentTaskId=None, search=None)",
        ),
        _golden(
            "mcp:task-search",
            "MCP tools",
            "task_search(project, query='', limit=10, *, status=None, priority=None, modified_files=None)",
        ),
        _golden("mcp:task-view", "MCP tools", "task_view(project, task_id)"),
        _golden(
            "mcp:task-create",
            "MCP tools",
            "task_create(project, ordinal=None, milestone=None, parentTaskId=None, references=None, documentation=None, modifiedFiles=None, implementationPlan=None, finalSummary=None, **kwargs)",
        ),
        _golden(
            "mcp:task-edit",
            "MCP tools",
            "task_edit(project, task_id, ordinal=None, milestone=None, references=None, addReferences=None, documentation=None, addDocumentation=None, modifiedFiles=None, **kwargs)",
        ),
        _golden("mcp:task-archive", "MCP tools", "task_archive(project, task_id)"),
        _golden("mcp:task-complete", "MCP tools", "task_complete(project, task_id)"),
        _golden("mcp:document-list", "MCP tools", "document_list(project, query=None, limit=100)"),
        _golden("mcp:document-search", "MCP tools", "document_list(project, query=<query>, limit=100)"),
        _golden("mcp:document-view", "MCP tools", "document_view(project, path_or_id)"),
        _golden("mcp:document-create", "MCP tools", "document_create(project, **kwargs)"),
        _golden("mcp:document-update", "MCP tools", "document_update(project, path_or_id, **kwargs)"),
        _golden("mcp:milestone-list", "MCP tools", "milestone_list(project)"),
        _golden("mcp:milestone-add", "MCP tools", "milestone_add(project, name, description='')"),
        _golden(
            "mcp:milestone-rename",
            "MCP tools",
            "milestone_rename(project, old_name, new_name, update_tasks=False)",
        ),
        _golden("mcp:milestone-remove", "MCP tools", "milestone_remove(project, name, clear_tasks=False)"),
        _golden("mcp:milestone-archive", "MCP tools", "milestone_archive(project, name)"),
        _golden(
            "mcp:definition-of-done-defaults-get",
            "MCP tools",
            "definition_of_done_defaults_get(project)",
        ),
        _golden(
            "mcp:definition-of-done-defaults-upsert",
            "MCP tools",
            "definition_of_done_defaults_upsert(project, items)",
        ),
        _implemented(
            "browser:kanban-drag-drop",
            "browser-implemented",
            "README.md",
            "backlog browser",
        ),
        _implemented(
            "browser:custom-port-service",
            "browser-implemented",
            "CLI-INSTRUCTIONS.md",
            "backlog browser --port <port> --no-open and browser service lifecycle",
        ),
        _implemented(
            "browser:task-detail-view",
            "browser-implemented",
            "web TaskDetailsModal",
            "read-only browser task detail endpoint and dialog",
        ),
        _implemented(
            "browser:markdown-detail-rendering",
            "browser-implemented",
            "web TaskDetailsModal",
            "safe browser Markdown rendering for task detail description, implementation notes, and final summary",
        ),
        _implemented(
            "browser:task-create-form",
            "browser-implemented",
            "web task create form",
            "basic browser task create endpoint and form",
        ),
        _implemented(
            "browser:task-edit-form",
            "browser-implemented",
            "web task edit form",
            "basic browser task edit endpoint and form",
        ),
        _implemented(
            "browser:task-archive-confirmation",
            "browser-implemented",
            "web task archive confirmation",
            "browser task archive endpoint and confirmation dialog",
        ),
        _implemented(
            "browser:checklist-state-controls",
            "browser-implemented",
            "web task checklist controls",
            "browser checklist state endpoint and task detail controls",
        ),
        _implemented(
            "browser:dod-defaults-settings",
            "browser-implemented",
            "web settings",
            "browser Definition of Done defaults settings dialog and endpoint",
        ),
        _implemented(
            "browser:general-settings",
            "browser-implemented",
            "web settings",
            "browser safe general project settings dialog and endpoint",
        ),
        _implemented(
            "browser:live-refresh-polling",
            "browser-implemented",
            "web live updates",
            "browser board revision polling detects external task changes",
        ),
        _implemented(
            "cli:interactive-board",
            "interactive-implemented",
            "CLI-INSTRUCTIONS.md",
            "backlog board interactive controls",
        ),
        _implemented(
            "cli:interactive-overview",
            "interactive-implemented",
            "CLI-INSTRUCTIONS.md",
            "backlog overview interactive project statistics dashboard",
        ),
        _implemented(
            "cli:rich-colored-output",
            "terminal-implemented",
            "CLI-INSTRUCTIONS.md",
            "ANSI-rich terminal rendering",
        ),
        _implemented(
            "cli:shell-completion-install",
            "completion-implemented",
            "CLI-INSTRUCTIONS.md",
            "backlog completion install --shell bash, zsh, fish, pwsh",
        ),
        _implemented(
            "cli:interactive-task-view-editor",
            "interactive-implemented",
            "CLI-INSTRUCTIONS.md",
            "backlog task <id> interactive task view and editor launch",
        ),
        _implemented(
            "cli:interactive-search-filters",
            "interactive-implemented",
            "CLI-INSTRUCTIONS.md",
            "interactive search filters and live filtering",
        ),
        _implemented(
            "cli:interactive-config-wizard",
            "interactive-implemented",
            "ADVANCED-CONFIG.md",
            "backlog config interactive advanced wizard",
        ),
        _implemented(
            "cli:task-plain-detail",
            "cli-implemented",
            "CLI-INSTRUCTIONS.md",
            "task and draft plain detail output with file path, status, dates, and checklist sections",
        ),
        _implemented(
            "core:on-status-change",
            "automation-implemented",
            "ADVANCED-CONFIG.md",
            "onStatusChange hooks",
        ),
        _implemented(
            "core:task-timestamps",
            "core-implemented",
            "CLI task serialization",
            "created_date on task/draft create and updated_date on task edits",
        ),
        _implemented(
            "core:date-only-timestamps",
            "core-implemented",
            "ADVANCED-CONFIG.md",
            "includeDatetimeInDates controls created_date and updated_date timestamp precision",
        ),
        _implemented(
            "git:remote-operations",
            "git-implemented",
            "ADVANCED-CONFIG.md",
            "remote git operations",
        ),
        _implemented(
            "git:auto-commit",
            "git-implemented",
            "ADVANCED-CONFIG.md",
            "autoCommit",
        ),
        _deferred(
            "git:hook-bypass",
            "git-deferred",
            "ADVANCED-CONFIG.md",
            "bypassGitHooks",
            "Hook bypass remains unsupported for safety.",
        ),
    )
    return CompatibilityInventory(items=items)


def _golden(name: str, upstream_reference: str, expected: str) -> CompatibilityItem:
    return CompatibilityItem(
        name=name,
        classification="golden-required",
        upstream_reference=upstream_reference,
        expected=expected,
        status="implemented",
        fixture=name,
    )


def _implemented(name: str, classification: str, upstream_reference: str, expected: str) -> CompatibilityItem:
    return CompatibilityItem(
        name=name,
        classification=classification,
        upstream_reference=upstream_reference,
        expected=expected,
        status="implemented",
        fixture=name,
    )


def _deferred(
    name: str,
    classification: str,
    upstream_reference: str,
    expected: str,
    deferred_reason: str,
) -> CompatibilityItem:
    return CompatibilityItem(
        name=name,
        classification=classification,
        upstream_reference=upstream_reference,
        expected=expected,
        status="deferred",
        deferred_reason=deferred_reason,
    )
