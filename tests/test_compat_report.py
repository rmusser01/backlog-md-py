import json
from datetime import date

from backlog_py.compat.inventory import load_builtin_inventory
from backlog_py.compat.report import build_compatibility_report


def _release_evidence(
    *,
    generated_at: str = "2026-05-29",
    max_age_days: int = 14,
    rich_artifacts: list[str] | None = None,
    screenshot_artifacts: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "upstream_baseline": {
            "package": "backlog.md",
            "version": "1.45.1",
            "audit_date": "2026-05-16",
        },
        "command": {
            "argv": ["backlog-py", "compat", "evidence-template"],
            "cwd": ".",
        },
        "freshness": {
            "max_age_days": max_age_days,
        },
        "release_gates": {
            "browser:rich-edit-e2e-release-check": {
                "status": "passed",
                "artifacts": rich_artifacts or ["artifacts/browser-rich-edit-e2e.txt"],
            },
            "browser:desktop-mobile-screenshot-release-check": {
                "status": "passed",
                "artifacts": screenshot_artifacts
                or [
                    "artifacts/browser-desktop.png",
                    "artifacts/browser-mobile.png",
                ],
            },
        },
    }


def test_compatibility_report_summarizes_inventory_statuses():
    report = build_compatibility_report(load_builtin_inventory())

    assert report["agent_cutover_ready"] is True
    assert report["full_browser_release_ready"] is False
    assert report["release_evidence"] == {
        "status": "missing",
        "path": None,
        "generated_at": None,
        "age_days": None,
        "max_age_days": None,
        "upstream_baseline": None,
        "command": None,
        "error": None,
    }
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
    evidence_path.write_text(json.dumps(_release_evidence()), encoding="utf-8")

    report = build_compatibility_report(
        load_builtin_inventory(),
        release_evidence_path=evidence_path,
        today=date(2026, 5, 29),
    )
    gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

    assert report["full_browser_release_ready"] is True
    assert report["release_evidence"]["status"] == "fresh"
    assert report["release_evidence"]["generated_at"] == "2026-05-29"
    assert report["release_evidence"]["age_days"] == 0
    assert report["release_evidence"]["max_age_days"] == 14
    assert report["release_evidence"]["upstream_baseline"] == {
        "package": "backlog.md",
        "version": "1.45.1",
        "audit_date": "2026-05-16",
    }
    assert report["release_evidence"]["command"]["argv"] == [
        "backlog-py",
        "compat",
        "evidence-template",
    ]
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


def test_compatibility_report_keeps_release_gates_required_with_stale_evidence(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence_path.write_text(
        json.dumps(_release_evidence(generated_at="2026-05-01", max_age_days=7)),
        encoding="utf-8",
    )

    report = build_compatibility_report(
        load_builtin_inventory(),
        release_evidence_path=evidence_path,
        today=date(2026, 5, 29),
    )
    gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

    assert report["full_browser_release_ready"] is False
    assert report["release_evidence"]["status"] == "stale"
    assert report["release_evidence"]["age_days"] == 28
    assert "stale" in report["release_evidence"]["error"]
    assert gates_by_name["browser:rich-edit-e2e-release-check"]["status"] == "required"
    assert "stale" in gates_by_name["browser:rich-edit-e2e-release-check"]["evidence_error"]
    assert gates_by_name["browser:desktop-mobile-screenshot-release-check"]["status"] == "required"


def test_compatibility_report_requires_release_evidence_metadata(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence_path.write_text(
        json.dumps({"release_gates": _release_evidence()["release_gates"]}),
        encoding="utf-8",
    )

    try:
        build_compatibility_report(load_builtin_inventory(), release_evidence_path=evidence_path)
    except ValueError as exc:
        assert "generated_at" in str(exc)
    else:
        raise AssertionError("Expected missing release evidence metadata to fail validation")


def test_compatibility_report_requires_release_evidence_schema_version(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence = _release_evidence()
    evidence["schema_version"] = 2
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    try:
        build_compatibility_report(load_builtin_inventory(), release_evidence_path=evidence_path)
    except ValueError as exc:
        assert "schema_version" in str(exc)
    else:
        raise AssertionError("Expected incompatible release evidence schema to fail validation")


def test_compatibility_report_keeps_release_gates_required_with_mismatched_upstream_baseline(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence = _release_evidence()
    baseline = evidence["upstream_baseline"]
    assert isinstance(baseline, dict)
    baseline["version"] = "1.44.0"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    report = build_compatibility_report(
        load_builtin_inventory(),
        release_evidence_path=evidence_path,
        today=date(2026, 5, 29),
    )
    gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

    assert report["full_browser_release_ready"] is False
    assert report["release_evidence"]["status"] == "stale"
    assert "upstream_baseline" in report["release_evidence"]["error"]
    assert gates_by_name["browser:rich-edit-e2e-release-check"]["status"] == "required"


def test_compatibility_report_rejects_absolute_artifact_paths(tmp_path):
    for index, artifact_path in enumerate(
        [
            "/private/tmp/browser-rich-edit-e2e.txt",
            "C:\\tmp\\browser-rich-edit-e2e.txt",
        ]
    ):
        evidence_path = tmp_path / f"browser-release-evidence-{index}.json"
        evidence_path.write_text(
            json.dumps(
                _release_evidence(
                    rich_artifacts=[artifact_path],
                )
            ),
            encoding="utf-8",
        )

        report = build_compatibility_report(
            load_builtin_inventory(),
            release_evidence_path=evidence_path,
            today=date(2026, 5, 29),
        )
        gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

        assert report["full_browser_release_ready"] is False
        assert gates_by_name["browser:rich-edit-e2e-release-check"]["status"] == "required"
        assert "relative artifact paths" in gates_by_name["browser:rich-edit-e2e-release-check"]["evidence_error"]


def test_compatibility_report_keeps_screenshot_gate_required_without_desktop_and_mobile(tmp_path):
    evidence_path = tmp_path / "browser-release-evidence.json"
    evidence_path.write_text(
        json.dumps(_release_evidence(screenshot_artifacts=["artifacts/browser-desktop.png"])),
        encoding="utf-8",
    )

    report = build_compatibility_report(
        load_builtin_inventory(),
        release_evidence_path=evidence_path,
        today=date(2026, 5, 29),
    )
    gates_by_name = {gate["name"]: gate for gate in report["release_gates"]["gates"]}

    assert report["full_browser_release_ready"] is False
    assert gates_by_name["browser:rich-edit-e2e-release-check"]["status"] == "passed"
    assert gates_by_name["browser:desktop-mobile-screenshot-release-check"]["status"] == "required"
    assert (
        "desktop and mobile"
        in gates_by_name["browser:desktop-mobile-screenshot-release-check"]["evidence_error"]
    )
