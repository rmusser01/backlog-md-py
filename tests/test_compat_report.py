import json

from backlog_py.compat.inventory import load_builtin_inventory
from backlog_py.compat.report import build_compatibility_report


def test_compatibility_report_summarizes_inventory_statuses():
    report = build_compatibility_report(load_builtin_inventory())

    assert report["agent_cutover_ready"] is True
    assert report["full_browser_release_ready"] is False
    assert report["summary"] == {
        "implemented": 100,
        "deferred": 0,
        "total": 100,
    }
    assert report["categories"]["cli"] == {
        "implemented": 45,
        "deferred": 0,
        "total": 45,
    }
    assert report["categories"]["mcp"] == {
        "implemented": 22,
        "deferred": 0,
        "total": 22,
    }
    assert report["categories"]["browser"] == {
        "implemented": 24,
        "deferred": 0,
        "total": 24,
    }
    assert report["categories"]["config"] == {
        "implemented": 2,
        "deferred": 0,
        "total": 2,
    }
    assert report["categories"]["core"] == {
        "implemented": 3,
        "deferred": 0,
        "total": 3,
    }
    assert report["categories"]["git"] == {
        "implemented": 4,
        "deferred": 0,
        "total": 4,
    }
    assert report["release_gates"]["summary"] == {
        "passed": 2,
        "required": 2,
        "not_applicable": 1,
        "total": 5,
    }


def test_compatibility_report_lists_deferred_items_with_reasons():
    report = build_compatibility_report(load_builtin_inventory())

    items_by_name = {item["name"]: item for item in report["items"]}
    deferred_by_name = {item["name"]: item for item in report["deferred_items"]}

    assert items_by_name["cli:task-list-plain"]["status"] == "implemented"
    assert items_by_name["cli:task-list-plain"]["fixture"] == "cli:task-list-plain"
    assert items_by_name["cli:shell-completion-install"]["status"] == "implemented"
    assert items_by_name["cli:rich-colored-output"]["status"] == "implemented"
    assert items_by_name["cli:interactive-board"]["status"] == "implemented"
    assert items_by_name["cli:interactive-overview"]["status"] == "implemented"
    assert items_by_name["cli:interactive-task-view-editor"]["status"] == "implemented"
    assert items_by_name["cli:interactive-search-filters"]["status"] == "implemented"
    assert items_by_name["cli:interactive-config-wizard"]["status"] == "implemented"
    assert items_by_name["cli:interactive-date-display"]["status"] == "implemented"
    assert items_by_name["cli:task-plain-detail"]["status"] == "implemented"
    assert items_by_name["core:on-status-change"]["status"] == "implemented"
    assert items_by_name["core:task-timestamps"]["status"] == "implemented"
    assert items_by_name["core:date-only-timestamps"]["status"] == "implemented"
    assert items_by_name["git:remote-operations"]["status"] == "implemented"
    assert items_by_name["git:active-branch-accuracy"]["status"] == "implemented"
    assert items_by_name["git:active-branch-accuracy"]["expected"] == (
        "read-only active branch task snapshots"
    )
    assert items_by_name["git:auto-commit"]["status"] == "implemented"
    assert items_by_name["git:hook-bypass"]["status"] == "implemented"
    assert items_by_name["git:hook-bypass"]["expected"] == "bypassGitHooks"
    assert items_by_name["browser:custom-port-service"]["status"] == "implemented"
    assert items_by_name["browser:responsive-layout"]["status"] == "implemented"
    assert items_by_name["browser:service-lifecycle"]["status"] == "implemented"
    assert items_by_name["browser:service-request-log"]["status"] == "implemented"
    assert items_by_name["browser:service-shutdown-state"]["status"] == "implemented"
    assert items_by_name["browser:service-shutdown-state"]["expected"] == (
        "browser service shutdown state and idempotent stop scheduling"
    )
    assert items_by_name["browser:kanban-drag-drop"]["status"] == "implemented"
    assert items_by_name["browser:task-detail-view"]["status"] == "implemented"
    assert items_by_name["browser:markdown-detail-rendering"]["status"] == "implemented"
    assert items_by_name["browser:mermaid-rendering"]["status"] == "implemented"
    assert items_by_name["browser:mermaid-rendering"]["expected"] == (
        "browser Mermaid diagram rendering for task detail Markdown fences"
    )
    assert items_by_name["browser:rich-section-editing"]["status"] == "implemented"
    assert items_by_name["browser:markdown-edit-toolbar"]["status"] == "implemented"
    assert items_by_name["browser:markdown-edit-toolbar"]["expected"] == (
        "browser Markdown edit toolbar for raw Markdown textareas"
    )
    assert items_by_name["browser:rich-markdown-editor"]["status"] == "implemented"
    assert items_by_name["browser:rich-markdown-editor"]["expected"] == (
        "browser Rich mode for the supported Markdown editing subset"
    )
    assert items_by_name["browser:metadata-editing"]["status"] == "implemented"
    assert items_by_name["browser:task-create-form"]["status"] == "implemented"
    assert items_by_name["browser:task-edit-form"]["status"] == "implemented"
    assert items_by_name["browser:task-archive-confirmation"]["status"] == "implemented"
    assert items_by_name["browser:checklist-state-controls"]["status"] == "implemented"
    assert items_by_name["browser:document-decision-readonly"]["status"] == "implemented"
    assert items_by_name["browser:document-decision-readonly"]["expected"] == (
        "browser read-only document and decision list/detail endpoints and dialogs"
    )
    assert items_by_name["browser:dod-defaults-settings"]["status"] == "implemented"
    assert items_by_name["browser:general-settings"]["status"] == "implemented"
    assert items_by_name["browser:safe-git-settings"]["status"] == "implemented"
    assert items_by_name["browser:safe-git-settings"]["expected"] == (
        "browser safe git automation settings dialog and endpoint"
    )
    assert items_by_name["browser:live-refresh-polling"]["status"] == "implemented"
    assert items_by_name["browser:sse-live-refresh"]["status"] == "implemented"
    assert items_by_name["browser:sse-live-refresh"]["expected"] == (
        "browser board revision Server-Sent Events with polling fallback"
    )
    assert items_by_name["browser:service-transport-shutdown"]["status"] == "implemented"
    assert items_by_name["browser:service-transport-shutdown"]["expected"] == (
        "browser SSE shutdown event and client transport teardown policy"
    )
    assert "git:hook-bypass" not in deferred_by_name


def test_compatibility_report_separates_release_validation_from_feature_counts():
    report = build_compatibility_report(load_builtin_inventory())

    gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

    assert report["summary"]["implemented"] == 100
    assert report["summary"]["deferred"] == 0
    assert report["full_browser_release_ready"] is False
    assert gates_by_name["browser:rich-edit-e2e-release-check"]["status"] == "required"
    assert gates_by_name["browser:desktop-mobile-screenshot-release-check"]["status"] == "required"
    assert gates_by_name["browser:complex-wysiwyg-round-trip"]["status"] == "not_applicable"
    assert gates_by_name["browser:shell-hook-settings"]["status"] == "passed"
    assert gates_by_name["browser:shell-hook-settings"]["scope"] == "rejected-in-browser"


def test_compatibility_report_marks_browser_release_ready_with_evidence_manifest(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "release_gates": {
                    "browser:rich-edit-e2e-release-check": {
                        "status": "passed",
                        "artifacts": ["artifacts/browser-rich-edit-e2e.txt"],
                    },
                    "browser:desktop-mobile-screenshot-release-check": {
                        "status": "passed",
                        "artifacts": [
                            "artifacts/browser-desktop.png",
                            "artifacts/browser-mobile.png",
                        ],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = build_compatibility_report(
        load_builtin_inventory(),
        release_evidence_path=evidence_path,
    )
    gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

    assert report["full_browser_release_ready"] is True
    assert report["release_gates"]["summary"] == {
        "passed": 4,
        "required": 0,
        "not_applicable": 1,
        "total": 5,
    }
    assert gates_by_name["browser:rich-edit-e2e-release-check"]["status"] == "passed"
    assert gates_by_name["browser:rich-edit-e2e-release-check"]["artifacts"] == [
        "artifacts/browser-rich-edit-e2e.txt"
    ]
    assert gates_by_name["browser:desktop-mobile-screenshot-release-check"]["status"] == "passed"
    assert gates_by_name["browser:desktop-mobile-screenshot-release-check"]["artifacts"] == [
        "artifacts/browser-desktop.png",
        "artifacts/browser-mobile.png",
    ]


def test_compatibility_report_keeps_screenshot_gate_required_without_desktop_and_mobile(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "release_gates": {
                    "browser:rich-edit-e2e-release-check": {
                        "status": "passed",
                        "artifacts": ["artifacts/browser-rich-edit-e2e.txt"],
                    },
                    "browser:desktop-mobile-screenshot-release-check": {
                        "status": "passed",
                        "artifacts": ["artifacts/browser-desktop.png"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = build_compatibility_report(
        load_builtin_inventory(),
        release_evidence_path=evidence_path,
    )
    gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

    assert report["full_browser_release_ready"] is False
    assert gates_by_name["browser:rich-edit-e2e-release-check"]["status"] == "passed"
    assert gates_by_name["browser:desktop-mobile-screenshot-release-check"]["status"] == "required"
    assert (
        "desktop and mobile"
        in gates_by_name["browser:desktop-mobile-screenshot-release-check"]["evidence_error"]
    )
