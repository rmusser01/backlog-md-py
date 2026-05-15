from pathlib import Path

from backlog_py.compat.inventory import load_builtin_inventory
from backlog_py.oracle.manifest import load_oracle_manifest


MATRIX_PATH = Path(__file__).resolve().parents[1] / "docs" / "agent-critical-parity.md"
MANIFEST_PATH = Path(__file__).parent / "fixtures" / "oracle" / "manifest.yml"

EXPECTED_AGENT_CRITICAL = {
    "cli:help",
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
    "cli:config-dod-defaults-get",
    "cli:config-dod-defaults-upsert",
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

EXPECTED_DEFERRED = {
    "browser:kanban-drag-drop",
    "cli:interactive-board",
    "cli:rich-colored-output",
    "cli:shell-completion-install",
    "core:on-status-change",
    "git:remote-operations",
    "git:auto-commit",
    "git:hook-bypass",
}


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

    assert "-p <path>" in by_name["cli:doc-create"].expected
    assert "-t <type>" in by_name["cli:doc-create"].expected
    assert "--tags <tags>" in by_name["cli:doc-create"].expected
    assert "-p <path>" in by_name["cli:doc-update"].expected
    assert "-t <type>" in by_name["cli:doc-update"].expected
    assert "--tags <tags>" in by_name["cli:doc-update"].expected


def test_agent_critical_inventory_tracks_task_cli_alias_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "-d <text>" in by_name["cli:task-create"].expected
    assert "-s <status>" in by_name["cli:task-create"].expected
    assert "--draft" in by_name["cli:task-create"].expected
    assert "--no-dod-defaults" in by_name["cli:task-create"].expected


def test_agent_critical_inventory_tracks_draft_cli_surface():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert "draft create <title>" in by_name["cli:draft-create"].expected
    assert "draft list --plain" in by_name["cli:draft-list"].expected
    assert "draft view <id> --plain" in by_name["cli:draft-view"].expected
    assert "draft promote <id>" in by_name["cli:draft-promote"].expected
    assert "task demote <id>" in by_name["cli:task-demote"].expected
    assert "draft archive <id>" in by_name["cli:draft-archive"].expected


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
        detail = item.fixture if item.classification == "golden-required" else item.deferred_reason
        assert any(
            item.name in line
            and item.expected in line
            and item.status in line
            and detail in line
            for line in matrix_lines
        ), f"Missing or mismatched matrix row for {item.name}"
