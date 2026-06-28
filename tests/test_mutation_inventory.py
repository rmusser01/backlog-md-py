from backlog_py.runtime.mutations import MUTATION_SURFACES, mutation_by_name


def test_mutation_inventory_covers_known_write_surfaces():
    names = {surface.name for surface in MUTATION_SURFACES}

    assert {
        "init_project",
        "task_create",
        "task_edit",
        "task_archive",
        "task_complete",
        "cleanup_complete_done",
        "draft_create",
        "draft_promote",
        "draft_demote",
        "draft_archive",
        "document_create",
        "document_update",
        "decision_create",
        "milestone_add",
        "milestone_rename",
        "milestone_remove",
        "milestone_archive",
        "config_set",
        "definition_of_done_defaults_upsert",
        "agents_update_instructions",
        "board_export_file",
        "board_export_readme",
        "orchestration_claim",
        "orchestration_release",
        "orchestration_transition",
        "orchestration_record_run",
        "orchestration_split",
    } <= names


def test_mutation_inventory_records_lock_scopes():
    surfaces = {surface.name: surface for surface in MUTATION_SURFACES}

    assert surfaces["init_project"].lock_scope == "init-root"
    assert surfaces["task_create"].lock_scope == "project"
    assert surfaces["board_export_file"].lock_scope == "project"
    assert surfaces["orchestration_record_run"].lock_scope == "project"


def test_mutation_by_name_returns_named_surface():
    surface = mutation_by_name("task_create")

    assert surface.name == "task_create"
    assert "backlog_py.cli.main" in surface.entrypoints
    assert "backlog_py.browser.service" in surface.entrypoints


def test_task_edit_mutation_records_browser_entrypoint():
    surface = mutation_by_name("task_edit")

    assert surface.lock_scope == "project"
    assert "backlog_py.browser.service" in surface.entrypoints


def test_task_checklist_mutation_records_browser_entrypoint():
    surface = mutation_by_name("task_checklist_state")

    assert surface.lock_scope == "project"
    assert "backlog_py.browser.service" in surface.entrypoints


def test_task_archive_mutation_records_browser_entrypoint():
    surface = mutation_by_name("task_archive")

    assert surface.lock_scope == "project"
    assert "backlog_py.browser.service" in surface.entrypoints


def test_orchestration_mutations_record_cli_and_mcp_entrypoints():
    for name in (
        "orchestration_claim",
        "orchestration_release",
        "orchestration_transition",
        "orchestration_record_run",
        "orchestration_split",
    ):
        surface = mutation_by_name(name)

        assert surface.lock_scope == "project"
        assert surface.entrypoints == ("backlog_py.cli.main", "backlog_py.mcp.tools")


def test_mutation_by_name_rejects_unknown_surface():
    try:
        mutation_by_name("unknown")
    except KeyError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("Expected KeyError for unknown mutation surface")
