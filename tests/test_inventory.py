from backlog_py.compat.inventory import load_builtin_inventory


def test_inventory_starts_with_agent_critical_commands():
    inventory = load_builtin_inventory()
    names = [item.name for item in inventory.items]

    assert names[:6] == [
        "cli:help",
        "cli:init",
        "cli:task-list-plain",
        "cli:task-view-plain",
        "cli:search-plain",
        "cli:board",
    ]


def test_inventory_classifies_browser_and_interactive_deferrals():
    inventory = load_builtin_inventory()
    by_name = {item.name: item for item in inventory.items}

    assert by_name["browser:kanban-drag-drop"].classification == "browser-implemented"
    assert by_name["browser:kanban-drag-drop"].status == "implemented"
    assert by_name["browser:custom-port-service"].classification == "browser-implemented"
    assert by_name["browser:custom-port-service"].status == "implemented"
    assert by_name["cli:interactive-board"].classification == "interactive-implemented"
    assert by_name["cli:interactive-board"].status == "implemented"
    assert by_name["cli:interactive-overview"].classification == "interactive-implemented"
    assert by_name["cli:interactive-overview"].status == "implemented"
    assert by_name["cli:interactive-task-view-editor"].classification == "interactive-implemented"
    assert by_name["cli:interactive-task-view-editor"].status == "implemented"
    assert by_name["cli:interactive-search-filters"].classification == "interactive-implemented"
    assert by_name["cli:interactive-search-filters"].status == "implemented"
    assert by_name["cli:rich-colored-output"].classification == "terminal-implemented"
