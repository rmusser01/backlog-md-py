from pathlib import Path

from backlog_py.compat.inventory import load_builtin_inventory
from backlog_py.oracle.manifest import load_oracle_manifest


MATRIX_PATH = Path(__file__).resolve().parents[1] / "docs" / "agent-critical-parity.md"
MANIFEST_PATH = Path(__file__).parent / "fixtures" / "oracle" / "manifest.yml"

EXPECTED_AGENT_CRITICAL = {
    "cli:help",
    "cli:init",
    "cli:task-list-plain",
    "cli:task-view-plain",
    "cli:search-plain",
    "cli:board",
    "cli:overview",
    "cli:board-export",
    "cli:config-list",
    "cli:task-create",
    "cli:draft-create",
    "cli:draft-list",
    "cli:draft-view",
    "cli:draft-promote",
    "cli:task-demote",
    "cli:draft-archive",
    "cli:task-edit",
    "cli:task-edit-rich-sections",
    "cli:task-edit-checklist-state",
    "cli:task-archive",
    "cli:cleanup",
    "cli:doc-list",
    "cli:doc-view",
    "cli:doc-create",
    "cli:doc-update",
    "cli:decision-create",
    "cli:milestone-list",
    "cli:milestone-add",
    "cli:milestone-rename",
    "cli:milestone-remove",
    "cli:milestone-archive",
    "cli:config-get",
    "cli:config-set",
    "cli:config-dod-defaults-get",
    "cli:config-dod-defaults-upsert",
    "cli:agents-update-instructions",
    "mcp:workflow-overview",
    "mcp:task-workflow-alias",
    "mcp:board",
    "mcp:task-list",
    "mcp:task-search",
    "mcp:task-view",
    "mcp:task-create",
    "mcp:task-edit",
    "mcp:task-archive",
    "mcp:task-complete",
    "mcp:document-list",
    "mcp:document-search",
    "mcp:document-view",
    "mcp:document-create",
    "mcp:document-update",
    "mcp:milestone-list",
    "mcp:milestone-add",
    "mcp:milestone-rename",
    "mcp:milestone-remove",
    "mcp:milestone-archive",
    "mcp:definition-of-done-defaults-get",
    "mcp:definition-of-done-defaults-upsert",
}

EXPECTED_DEFERRED: set[str] = set()


def test_agent_critical_inventory_enumerates_cutover_and_deferral_scope():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    actual_agent_critical = {
        item.name for item in inventory.items if item.classification == "golden-required"
    }
    actual_deferred = {item.name for item in inventory.items if item.status == "deferred"}

    assert actual_agent_critical == EXPECTED_AGENT_CRITICAL
    assert actual_deferred == EXPECTED_DEFERRED

    for name in EXPECTED_AGENT_CRITICAL:
        item = by_name[name]
        assert item.classification == "golden-required"
        assert item.status == "implemented"
        assert item.fixture == name
        assert item.expected

    for name in EXPECTED_DEFERRED:
        item = by_name[name]
        assert item.classification != "golden-required"
        assert item.status == "deferred"
        assert item.deferred_reason


def test_agent_critical_inventory_tracks_task_ordinal_mutation_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "--ordinal <number>" in by_name["cli:task-create"].expected
    assert "--ordinal <number>" in by_name["cli:task-edit"].expected
    assert "ordinal=None" in by_name["mcp:task-create"].expected
    assert "ordinal=None" in by_name["mcp:task-edit"].expected


def test_agent_critical_inventory_tracks_task_create_explicit_id_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "--id <id>" in by_name["cli:task-create"].expected


def test_agent_critical_inventory_tracks_draft_create_status_compatibility_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "-s <status>" in by_name["cli:draft-create"].expected


def test_agent_critical_inventory_tracks_milestone_mutation_option_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "--description <text>" in by_name["cli:milestone-add"].expected
    assert "--update-tasks" in by_name["cli:milestone-rename"].expected
    assert "--clear-tasks" in by_name["cli:milestone-remove"].expected


def test_agent_critical_inventory_tracks_task_plan_mutation_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}
    task_edit = by_name["cli:task-edit"].expected

    assert "--plan <text>" in task_edit
    assert "--append-plan <text>" in task_edit
    assert "--clear-plan" in task_edit


def test_agent_critical_inventory_tracks_task_edit_core_field_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}
    task_edit = by_name["cli:task-edit"].expected

    assert "--title <title>" in task_edit
    assert "-s <status>" in task_edit
    assert "-d <text>" in task_edit
    assert "--desc <text>" in task_edit
    assert "--dep <id>" in task_edit


def test_agent_critical_inventory_tracks_interactive_task_view_editor_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}
    item = by_name["cli:interactive-task-view-editor"]

    assert item.status == "implemented"
    assert item.classification == "interactive-implemented"
    assert item.expected == "backlog task <id> interactive task view and editor launch"
    assert item.fixture == "cli:interactive-task-view-editor"


def test_agent_critical_inventory_tracks_interactive_search_filter_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}
    item = by_name["cli:interactive-search-filters"]

    assert item.status == "implemented"
    assert item.classification == "interactive-implemented"
    assert item.expected == "interactive search filters and live filtering"
    assert item.fixture == "cli:interactive-search-filters"


def test_agent_critical_inventory_tracks_interactive_board_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}
    item = by_name["cli:interactive-board"]

    assert item.status == "implemented"
    assert item.classification == "interactive-implemented"
    assert item.expected == "backlog board interactive controls"
    assert item.fixture == "cli:interactive-board"


def test_agent_critical_inventory_tracks_interactive_overview_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}
    item = by_name["cli:interactive-overview"]

    assert item.status == "implemented"
    assert item.classification == "interactive-implemented"
    assert item.expected == "backlog overview interactive project statistics dashboard"
    assert item.fixture == "cli:interactive-overview"


def test_agent_critical_inventory_tracks_status_change_callback_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["core:on-status-change"]
    assert item.status == "implemented"
    assert item.classification == "automation-implemented"
    assert item.expected == "onStatusChange hooks"


def test_agent_critical_inventory_tracks_task_timestamp_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["core:task-timestamps"]
    assert item.status == "implemented"
    assert item.classification == "core-implemented"
    assert item.expected == "created_date on task/draft create and updated_date on task edits"


def test_agent_critical_inventory_tracks_date_only_timestamp_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["core:date-only-timestamps"]
    assert item.status == "implemented"
    assert item.classification == "core-implemented"
    assert item.expected == "includeDatetimeInDates controls created_date and updated_date timestamp precision"


def test_agent_critical_inventory_tracks_interactive_config_wizard_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["cli:interactive-config-wizard"]
    assert item.status == "implemented"
    assert item.classification == "interactive-implemented"
    assert item.expected == "backlog config interactive advanced wizard"


def test_agent_critical_inventory_tracks_interactive_date_display_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["cli:interactive-date-display"]
    assert item.status == "implemented"
    assert item.classification == "interactive-implemented"
    assert item.expected == "interactive task detail respects dateFormat and includeDatetimeInDates"


def test_agent_critical_inventory_tracks_plain_task_detail_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["cli:task-plain-detail"]
    assert item.status == "implemented"
    assert item.classification == "cli-implemented"
    assert item.expected == "task and draft plain detail output with file path, status, dates, and checklist sections"


def test_agent_critical_inventory_tracks_auto_commit_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["git:auto-commit"]
    assert item.status == "implemented"
    assert item.classification == "git-implemented"
    assert item.expected == "autoCommit"


def test_agent_critical_inventory_tracks_hook_bypass_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["git:hook-bypass"]
    assert item.status == "implemented"
    assert item.classification == "git-implemented"
    assert item.expected == "bypassGitHooks"


def test_agent_critical_inventory_tracks_remote_operations_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["git:remote-operations"]
    assert item.status == "implemented"
    assert item.classification == "git-implemented"
    assert item.expected == "remote git operations"


def test_agent_critical_inventory_tracks_active_branch_accuracy_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["git:active-branch-accuracy"]
    assert item.status == "implemented"
    assert item.classification == "git-implemented"
    assert item.expected == "read-only active branch task snapshots"


def test_agent_critical_inventory_tracks_browser_service_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:custom-port-service"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "backlog browser --port <port> --no-open and browser service lifecycle"


def test_agent_critical_inventory_tracks_browser_responsive_layout_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:responsive-layout"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "responsive browser board layout for narrow viewports"


def test_agent_critical_inventory_tracks_browser_lifecycle_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:service-lifecycle"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser service status endpoint and guarded local shutdown dialog"


def test_agent_critical_inventory_tracks_browser_service_request_log_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:service-request-log"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "bounded browser service request log endpoint and Service dialog list"


def test_agent_critical_inventory_tracks_browser_shutdown_state_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:service-shutdown-state"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser service shutdown state and idempotent stop scheduling"


def test_agent_critical_inventory_tracks_browser_drag_drop_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:kanban-drag-drop"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "backlog browser"


def test_agent_critical_inventory_tracks_browser_task_detail_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:task-detail-view"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "read-only browser task detail endpoint and dialog"


def test_agent_critical_inventory_tracks_browser_task_create_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:task-create-form"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "basic browser task create endpoint and form"


def test_agent_critical_inventory_tracks_browser_rich_section_editing_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:rich-section-editing"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser edit form updates Implementation Notes and Final Summary Markdown sections"


def test_agent_critical_inventory_tracks_browser_markdown_edit_toolbar_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:markdown-edit-toolbar"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser Markdown edit toolbar for raw Markdown textareas"


def test_agent_critical_inventory_tracks_browser_rich_markdown_editor_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:rich-markdown-editor"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser Rich mode for the supported Markdown editing subset"


def test_agent_critical_inventory_tracks_browser_mermaid_rendering_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:mermaid-rendering"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser Mermaid diagram rendering for task detail Markdown fences"


def test_agent_critical_inventory_tracks_browser_metadata_editing_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:metadata-editing"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser edit form updates assignees, labels, priority, and milestone"


def test_agent_critical_inventory_tracks_browser_task_edit_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:task-edit-form"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "basic browser task edit endpoint and form"


def test_agent_critical_inventory_tracks_browser_task_archive_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:task-archive-confirmation"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser task archive endpoint and confirmation dialog"


def test_agent_critical_inventory_tracks_browser_checklist_state_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:checklist-state-controls"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser checklist state endpoint and task detail controls"


def test_agent_critical_inventory_tracks_browser_document_decision_readonly_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:document-decision-readonly"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser read-only document and decision list/detail endpoints and dialogs"


def test_agent_critical_inventory_tracks_browser_sse_live_refresh_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:sse-live-refresh"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser board revision Server-Sent Events with polling fallback"


def test_agent_critical_inventory_tracks_browser_service_transport_shutdown_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:service-transport-shutdown"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser SSE shutdown event and client transport teardown policy"


def test_agent_critical_inventory_tracks_browser_safe_git_settings_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    item = by_name["browser:safe-git-settings"]
    assert item.status == "implemented"
    assert item.classification == "browser-implemented"
    assert item.expected == "browser safe git automation settings dialog and endpoint"


def test_agent_critical_inventory_tracks_init_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}
    init_surface = by_name["cli:init"].expected

    assert init_surface.startswith("backlog init [project-name] --defaults [--no-git]")
    assert "--backlog-dir <path>" in init_surface
    assert "--task-prefix <prefix>" in init_surface
    assert "--config-location <location>" in init_surface
    assert "--agent-instructions" in init_surface


def test_agent_critical_inventory_tracks_cli_search_file_and_limit_filters():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "--modified-file <path>" in by_name["cli:search-plain"].expected
    assert "--limit <number>" in by_name["cli:search-plain"].expected
    assert "--type <type>" in by_name["cli:search-plain"].expected
    assert "tasks, documents, and decisions" in by_name["cli:search-plain"].expected


def test_agent_critical_inventory_tracks_overview_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert by_name["cli:overview"].expected == "backlog overview"


def test_agent_critical_inventory_tracks_document_path_type_and_tags_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "[query]" in by_name["cli:doc-list"].expected
    assert "--title <title>" in by_name["cli:doc-create"].expected
    assert "-p <path>" in by_name["cli:doc-create"].expected
    assert "--path <path>" in by_name["cli:doc-create"].expected
    assert "-t <type>" in by_name["cli:doc-create"].expected
    assert "--type <type>" in by_name["cli:doc-create"].expected
    assert "--tags <tags>" in by_name["cli:doc-create"].expected
    assert "-p <path>" in by_name["cli:doc-update"].expected
    assert "--path <path>" in by_name["cli:doc-update"].expected
    assert "-t <type>" in by_name["cli:doc-update"].expected
    assert "--type <type>" in by_name["cli:doc-update"].expected
    assert "--tags <tags>" in by_name["cli:doc-update"].expected


def test_agent_critical_inventory_tracks_task_cli_alias_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "-p <taskId>" in by_name["cli:task-list-plain"].expected
    assert "--parent <taskId>" in by_name["cli:task-list-plain"].expected
    assert "-m <milestone>" in by_name["cli:task-list-plain"].expected
    assert "-d <text>" in by_name["cli:task-create"].expected
    assert "--desc <text>" in by_name["cli:task-create"].expected
    assert "-p <taskId>" in by_name["cli:task-create"].expected
    assert "--parent <taskId>" in by_name["cli:task-create"].expected
    assert "-m <milestone>" in by_name["cli:task-create"].expected
    assert "-s <status>" in by_name["cli:task-create"].expected
    assert "--draft" in by_name["cli:task-create"].expected
    assert "--notes <text>" in by_name["cli:task-create"].expected
    assert "--definition-of-done <item>" in by_name["cli:task-create"].expected
    assert "--definition-of-done-add <item>" in by_name["cli:task-create"].expected
    assert "--disable-definition-of-done-defaults" in by_name["cli:task-create"].expected
    assert "--no-dod-defaults" in by_name["cli:task-create"].expected
    assert "--dependency <id>" in by_name["cli:task-create"].expected


def test_agent_critical_inventory_tracks_task_edit_rich_sections_and_checklists():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    rich_sections = by_name["cli:task-edit-rich-sections"].expected
    assert "--notes <text>" in rich_sections
    assert "--append-notes <text>" in rich_sections
    assert "--final-summary <text>" in rich_sections
    assert "--append-final-summary <text>" in rich_sections
    assert "--clear-final-summary" in rich_sections

    checklist_state = by_name["cli:task-edit-checklist-state"].expected
    assert "-m <milestone>" in by_name["cli:task-edit"].expected
    assert "--dod <item>" in by_name["cli:task-edit"].expected
    assert "--dependency <id>" in by_name["cli:task-edit"].expected
    assert "--check-ac <index>" in checklist_state
    assert "--uncheck-ac <index>" in checklist_state
    assert "--check-dod <index>" in checklist_state
    assert "--uncheck-dod <index>" in checklist_state
    assert "--remove-ac <index>" in checklist_state
    assert "--remove-dod <index>" in checklist_state


def test_agent_critical_inventory_tracks_draft_cli_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "draft create <title>" in by_name["cli:draft-create"].expected
    assert "draft list --plain" in by_name["cli:draft-list"].expected
    assert "draft view <id> --plain" in by_name["cli:draft-view"].expected
    assert "draft promote <id>" in by_name["cli:draft-promote"].expected
    assert "task demote <id>" in by_name["cli:task-demote"].expected
    assert "draft archive <id>" in by_name["cli:draft-archive"].expected


def test_agent_critical_inventory_tracks_config_get_set_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert by_name["cli:config-get"].expected == "backlog config get <key>"
    assert by_name["cli:config-set"].expected == "backlog config set <key> <value>"
    assert by_name["config:extended-options"].status == "implemented"
    assert "defaultAssignee" in by_name["config:extended-options"].expected
    assert "onStatusChange" in by_name["config:extended-options"].expected
    assert "zeroPaddedIds" in by_name["config:extended-options"].expected
    assert by_name["config:task-prefix"].status == "implemented"
    assert "taskPrefix" in by_name["config:task-prefix"].expected
    assert "prefixes.task" in by_name["config:task-prefix"].expected
    assert by_name["cli:rich-colored-output"].status == "implemented"
    assert by_name["cli:rich-colored-output"].classification == "terminal-implemented"


def test_agent_critical_inventory_tracks_agent_instruction_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert by_name["cli:agents-update-instructions"].expected == "backlog agents --update-instructions"


def test_agent_critical_inventory_tracks_decision_create_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert 'decision create "Title"' in by_name["cli:decision-create"].expected
    assert "-s <status>" in by_name["cli:decision-create"].expected


def test_agent_critical_inventory_has_fixture_coverage():
    inventory = load_builtin_inventory()
    manifest = load_oracle_manifest(MANIFEST_PATH)
    fixture_names = {fixture.name for fixture in manifest.fixtures}

    missing = [
        item.name
        for item in inventory.items
        if item.classification == "golden-required" and item.name not in fixture_names
    ]

    assert missing == []


def test_agent_critical_manifest_tracks_all_inventory_items():
    inventory = load_builtin_inventory()
    manifest = load_oracle_manifest(MANIFEST_PATH)
    fixture_by_name = {fixture.name: fixture for fixture in manifest.fixtures}

    assert sorted({item.name for item in inventory.items} - fixture_by_name.keys()) == []

    for item in inventory.items:
        fixture = fixture_by_name[item.name]
        assert fixture.classification == item.classification
        assert fixture.agent_critical is (item.classification == "golden-required")


def test_agent_critical_matrix_doc_matches_inventory():
    inventory = load_builtin_inventory()
    matrix = MATRIX_PATH.read_text(encoding="utf-8")
    matrix_lines = matrix.splitlines()

    for item in inventory.items:
        detail = item.fixture if item.status == "implemented" else item.deferred_reason
        assert any(
            item.name in line
            and item.expected in line
            and item.status in line
            and detail in line
            for line in matrix_lines
        ), f"Missing or mismatched matrix row for {item.name}"
