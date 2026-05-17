# Agent-Critical Parity Gate

This matrix is the cutover gate for agent workflows that currently depend on
Backlog.md. Every agent-critical CLI or MCP operation must be represented in the
built-in compatibility inventory and, when implemented, in the pinned oracle
manifest. Deferred capabilities are explicit blockers for later milestones and
do not block the first local-file agent cutover candidate.

## Implemented Golden Requirements

| Inventory item | Status | Expected command, resource, or tool | Fixture |
| --- | --- | --- | --- |
| cli:help | implemented | backlog --help | cli:help |
| cli:init | implemented | backlog init [project-name] --defaults | cli:init |
| cli:task-list-plain | implemented | backlog task list --status <status> --priority <priority> -a <assignee> -l <label> --milestone <milestone> --parent <taskId> --plain | cli:task-list-plain |
| cli:task-view-plain | implemented | backlog task <id> --plain | cli:task-view-plain |
| cli:search-plain | implemented | backlog search <query> --type <type> --status <status> --priority <priority> --modified-file <path> --limit <number> --plain; unfiltered search returns tasks, documents, and decisions | cli:search-plain |
| cli:board | implemented | backlog board | cli:board |
| cli:overview | implemented | backlog overview | cli:overview |
| cli:board-export | implemented | backlog board export [file] --readme --force --export-version <version> | cli:board-export |
| cli:config-list | implemented | backlog config list | cli:config-list |
| cli:task-create | implemented | backlog task create <title> --draft -d <text> -s <status> --plan <text> --notes <text> --final-summary <text> --parent <taskId> --milestone <milestone> --ordinal <number> --ref <item> --doc <item> --modified-file <path> -a <assignee> -l <label> --priority <priority> --ac <item> --dod <item> --no-dod-defaults --dep <id> --plain | cli:task-create |
| cli:draft-create | implemented | backlog draft create <title> -d <text> -a <assignee> -l <label> | cli:draft-create |
| cli:draft-list | implemented | backlog draft list --plain | cli:draft-list |
| cli:draft-view | implemented | backlog draft view <id> --plain | cli:draft-view |
| cli:draft-promote | implemented | backlog draft promote <id> | cli:draft-promote |
| cli:task-demote | implemented | backlog task demote <id> | cli:task-demote |
| cli:draft-archive | implemented | backlog draft archive <id> | cli:draft-archive |
| cli:task-edit | implemented | backlog task edit <id> --plan <text> --milestone <milestone> --ordinal <number> --clear-milestone --ref <item> --doc <item> --modified-file <path> -a <assignee> -l <label> --priority <priority> --ac <item> --remove-ac <index> --plain | cli:task-edit |
| cli:task-edit-rich-sections | implemented | backlog task edit <id> --notes <text> --append-notes <text> --final-summary <text> --append-final-summary <text> --clear-final-summary --plain | cli:task-edit-rich-sections |
| cli:task-edit-checklist-state | implemented | backlog task edit <id> --check-ac <index> --uncheck-ac <index> --check-dod <index> --uncheck-dod <index> --remove-ac <index> --remove-dod <index> --plain | cli:task-edit-checklist-state |
| cli:task-archive | implemented | backlog task archive <id> --plain | cli:task-archive |
| cli:cleanup | implemented | backlog cleanup | cli:cleanup |
| cli:doc-list | implemented | backlog doc list | cli:doc-list |
| cli:doc-view | implemented | backlog doc view <path-or-id> | cli:doc-view |
| cli:doc-create | implemented | backlog doc create <title> -p <path> -t <type> --tags <tags> --content <body> | cli:doc-create |
| cli:doc-update | implemented | backlog doc update <path-or-id> --title <title> -p <path> -t <type> --tags <tags> --content <body> | cli:doc-update |
| cli:decision-create | implemented | backlog decision create "Title" -s <status> | cli:decision-create |
| cli:milestone-list | implemented | backlog milestone list | cli:milestone-list |
| cli:milestone-add | implemented | backlog milestone add <name> | cli:milestone-add |
| cli:milestone-rename | implemented | backlog milestone rename <old> <new> | cli:milestone-rename |
| cli:milestone-remove | implemented | backlog milestone remove <name> | cli:milestone-remove |
| cli:milestone-archive | implemented | backlog milestone archive <name> | cli:milestone-archive |
| cli:config-get | implemented | backlog config get <key> | cli:config-get |
| cli:config-set | implemented | backlog config set <key> <value> | cli:config-set |
| cli:config-dod-defaults-get | implemented | backlog config dod-defaults-get | cli:config-dod-defaults-get |
| cli:config-dod-defaults-upsert | implemented | backlog config dod-defaults-upsert [item...] | cli:config-dod-defaults-upsert |
| cli:agents-update-instructions | implemented | backlog agents --update-instructions | cli:agents-update-instructions |
| mcp:workflow-overview | implemented | backlog://workflow/overview | mcp:workflow-overview |
| mcp:task-workflow-alias | implemented | backlog://docs/task-workflow | mcp:task-workflow-alias |
| mcp:board | implemented | task_board(project) | mcp:board |
| mcp:task-list | implemented | task_list(project, status=None, limit=100, *, assignee=None, labels=None, priority=None, milestone=None, parentTaskId=None, search=None) | mcp:task-list |
| mcp:task-search | implemented | task_search(project, query='', limit=10, *, status=None, priority=None, modified_files=None) | mcp:task-search |
| mcp:task-view | implemented | task_view(project, task_id) | mcp:task-view |
| mcp:task-create | implemented | task_create(project, ordinal=None, milestone=None, parentTaskId=None, references=None, documentation=None, modifiedFiles=None, implementationPlan=None, finalSummary=None, **kwargs) | mcp:task-create |
| mcp:task-edit | implemented | task_edit(project, task_id, ordinal=None, milestone=None, references=None, addReferences=None, documentation=None, addDocumentation=None, modifiedFiles=None, **kwargs) | mcp:task-edit |
| mcp:task-archive | implemented | task_archive(project, task_id) | mcp:task-archive |
| mcp:task-complete | implemented | task_complete(project, task_id) | mcp:task-complete |
| mcp:document-list | implemented | document_list(project, query=None, limit=100) | mcp:document-list |
| mcp:document-search | implemented | document_list(project, query=<query>, limit=100) | mcp:document-search |
| mcp:document-view | implemented | document_view(project, path_or_id) | mcp:document-view |
| mcp:document-create | implemented | document_create(project, **kwargs) | mcp:document-create |
| mcp:document-update | implemented | document_update(project, path_or_id, **kwargs) | mcp:document-update |
| mcp:milestone-list | implemented | milestone_list(project) | mcp:milestone-list |
| mcp:milestone-add | implemented | milestone_add(project, name, description='') | mcp:milestone-add |
| mcp:milestone-rename | implemented | milestone_rename(project, old_name, new_name, update_tasks=False) | mcp:milestone-rename |
| mcp:milestone-remove | implemented | milestone_remove(project, name, clear_tasks=False) | mcp:milestone-remove |
| mcp:milestone-archive | implemented | milestone_archive(project, name) | mcp:milestone-archive |
| mcp:definition-of-done-defaults-get | implemented | definition_of_done_defaults_get(project) | mcp:definition-of-done-defaults-get |
| mcp:definition-of-done-defaults-upsert | implemented | definition_of_done_defaults_upsert(project, items) | mcp:definition-of-done-defaults-upsert |

## Implemented Full-Parity Extensions

| Inventory item | Status | Expected command, resource, or tool | Fixture |
| --- | --- | --- | --- |
| config:extended-options | implemented | config get/set/list defaultAssignee, dateFormat, includeDatetimeInDates, defaultEditor, defaultPort, autoOpenBrowser, onStatusChange, zeroPaddedIds | config:extended-options |
| config:task-prefix | implemented | init --task-prefix, config list taskPrefix read-only, and generated task/subtask IDs use prefixes.task | config:task-prefix |
| cli:rich-colored-output | implemented | ANSI-rich terminal rendering | cli:rich-colored-output |
| cli:shell-completion-install | implemented | backlog completion install --shell bash, zsh, fish, pwsh | cli:shell-completion-install |
| cli:interactive-config-wizard | implemented | backlog config interactive advanced wizard | cli:interactive-config-wizard |
| cli:interactive-task-view-editor | implemented | backlog task <id> interactive task view and editor launch | cli:interactive-task-view-editor |
| cli:interactive-search-filters | implemented | interactive search filters and live filtering | cli:interactive-search-filters |
| cli:interactive-board | implemented | backlog board interactive controls | cli:interactive-board |
| cli:interactive-overview | implemented | backlog overview interactive project statistics dashboard | cli:interactive-overview |
| cli:task-plain-detail | implemented | task and draft plain detail output with file path, status, dates, and checklist sections | cli:task-plain-detail |
| browser:kanban-drag-drop | implemented | backlog browser | browser:kanban-drag-drop |
| browser:custom-port-service | implemented | backlog browser --port <port> --no-open and browser service lifecycle | browser:custom-port-service |
| core:on-status-change | implemented | onStatusChange hooks | core:on-status-change |
| core:task-timestamps | implemented | created_date on task/draft create and updated_date on task edits | core:task-timestamps |
| git:remote-operations | implemented | remote git operations | git:remote-operations |
| git:auto-commit | implemented | autoCommit | git:auto-commit |

## Explicit Deferred Blockers

| Inventory item | Status | Expected behavior | Deferred reason |
| --- | --- | --- | --- |
| git:hook-bypass | deferred | bypassGitHooks | Hook bypass remains unsupported for safety. |

## Validation Commands

Run the matrix test with:

```bash
uv run --extra dev python -m pytest tests/test_agent_critical_matrix.py -v
```

Run the full local cutover validation with:

```bash
uv run --extra dev python -m pytest tests -v
uv run --extra dev python -m bandit -r src -f json -o /tmp/bandit_backlog_py.json
git diff --check
```

Mutation smoke tests must run only against a copied fixture or temporary
repository, never against the live project backlog.
