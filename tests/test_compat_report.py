from backlog_py.compat.inventory import load_builtin_inventory
from backlog_py.compat.report import build_compatibility_report


def test_compatibility_report_summarizes_inventory_statuses():
    report = build_compatibility_report(load_builtin_inventory())

    assert report["agent_cutover_ready"] is True
    assert report["summary"] == {
        "implemented": 66,
        "deferred": 6,
        "total": 72,
    }
    assert report["categories"]["cli"] == {
        "implemented": 39,
        "deferred": 3,
        "total": 42,
    }
    assert report["categories"]["mcp"] == {
        "implemented": 22,
        "deferred": 0,
        "total": 22,
    }
    assert report["categories"]["browser"] == {
        "implemented": 1,
        "deferred": 1,
        "total": 2,
    }
    assert report["categories"]["config"] == {
        "implemented": 2,
        "deferred": 0,
        "total": 2,
    }
    assert report["categories"]["core"] == {
        "implemented": 1,
        "deferred": 0,
        "total": 1,
    }
    assert report["categories"]["git"] == {
        "implemented": 1,
        "deferred": 2,
        "total": 3,
    }


def test_compatibility_report_lists_deferred_items_with_reasons():
    report = build_compatibility_report(load_builtin_inventory())

    items_by_name = {item["name"]: item for item in report["items"]}
    deferred_by_name = {item["name"]: item for item in report["deferred_items"]}

    assert items_by_name["cli:task-list-plain"]["status"] == "implemented"
    assert items_by_name["cli:task-list-plain"]["fixture"] == "cli:task-list-plain"
    assert items_by_name["cli:shell-completion-install"]["status"] == "implemented"
    assert items_by_name["cli:rich-colored-output"]["status"] == "implemented"
    assert items_by_name["cli:interactive-config-wizard"]["status"] == "implemented"
    assert items_by_name["core:on-status-change"]["status"] == "implemented"
    assert items_by_name["git:auto-commit"]["status"] == "implemented"
    assert items_by_name["browser:custom-port-service"]["status"] == "implemented"
    assert deferred_by_name["browser:kanban-drag-drop"] == {
        "name": "browser:kanban-drag-drop",
        "classification": "browser-deferred",
        "expected": "backlog browser",
        "reason": "Browser UI parity is tracked in the browser deferral milestone.",
    }
    assert deferred_by_name["git:hook-bypass"]["reason"] == "Hook bypass remains unsupported for safety."
