# Browser Release Validation

`backlog-py compat status` separates implemented feature coverage from browser
release readiness. A clone can report all audited features as implemented while
still requiring browser release evidence before advertising full browser parity.
The browser HTML template and its CSS/JavaScript assets are package resources,
so package build validation should continue to confirm that source
distributions and wheels include `src/backlog_py/browser/templates` and
`src/backlog_py/browser/assets` without adding a frontend build step.

## Evidence Manifest

Generate a portable manifest template, attach artifact paths from the browser
validation run, then pass it to `compat status`:

```bash
backlog-py compat evidence-template \
  --output release-evidence/browser-release-evidence.json \
  --rich-edit-artifact artifacts/browser-rich-edit-e2e.txt \
  --desktop-artifact artifacts/browser-desktop.png \
  --mobile-artifact artifacts/browser-mobile.png \
  --command "manual browser release validation"

backlog-py compat status --release-evidence release-evidence/browser-release-evidence.json
backlog-py compat status --json --release-evidence release-evidence/browser-release-evidence.json
```

The manifest records the upstream audit baseline, generation date, command
provenance, freshness policy, and release gate evidence:

```json
{
  "schema_version": 1,
  "generated_at": "2026-05-31",
  "upstream_baseline": {
    "package": "backlog.md",
    "version": "1.45.2",
    "audit_date": "2026-05-31"
  },
  "command": {
    "argv": [
      "manual browser release validation"
    ],
    "cwd": "."
  },
  "freshness": {
    "max_age_days": 14
  },
  "release_gates": {
    "browser:rich-edit-e2e-release-check": {
      "status": "passed",
      "artifacts": [
        "artifacts/browser-rich-edit-e2e.txt"
      ]
    },
    "browser:desktop-mobile-screenshot-release-check": {
      "status": "passed",
      "artifacts": [
        "artifacts/browser-desktop.png",
        "artifacts/browser-mobile.png"
      ]
    }
  }
}
```

## Validation Rules

- `generated_at`, `upstream_baseline`, `command.argv`, `command.cwd`,
  `freshness.max_age_days`, and `release_gates` are required.
- `schema_version` must be `1`, and `upstream_baseline` must match the current
  compatibility baseline (`backlog.md` `1.45.2`, audited `2026-05-31`).
- Evidence is `fresh` only when `generated_at` is not in the future and its age
  is less than or equal to `freshness.max_age_days`.
- Artifact paths must be repo-relative or artifact-bundle-relative. Absolute
  workstation paths such as `/private/tmp/...` are rejected.
- `browser:rich-edit-e2e-release-check` passes only when the manifest marks it
  `passed` and includes at least one artifact reference.
- `browser:desktop-mobile-screenshot-release-check` passes only when the
  manifest marks it `passed` and includes artifact references containing both
  `desktop` and `mobile`.
- `browser:complex-wysiwyg-round-trip` remains `not_applicable` until a future
  milestone claims full WYSIWYG editing for complex Markdown.
- Browser shell-hook settings and current SSE shutdown policy are passed by
  documented project policy, not by external artifact files.

When both required browser release gates pass, `fullBrowserReleaseReady` becomes
`true` in plain output and `full_browser_release_ready` becomes `true` in JSON
output. Plain output also reports `releaseEvidence: missing`, `fresh`, or
`stale`; JSON output includes the same metadata under `release_evidence`. When
fresh release evidence leaves a gate unsatisfied, both plain and JSON output
include the gate-specific evidence error.

## CI Evidence Artifacts

The CI package job publishes a `compatibility-release-evidence` artifact with:

- `browser-release-evidence-template.json`: a fresh manifest template generated
  from the built wheel.
- `compat-status.json`: compatibility status without external browser evidence.
- `compat-status-with-release-evidence-template.json`: compatibility status with
  the generated template attached. This proves the evidence schema and
  freshness handling, but does not by itself satisfy browser gates because it
  does not include manual browser artifacts.

Release candidates that advertise full browser parity should replace or extend
the template with fresh browser artifacts and attach the completed manifest
plus referenced files to the release evidence bundle.

## Historical Validation Record

Validated on 2026-05-22 against the `basic-fixture` project after the browser
task edit metadata-field fix merged. This predates the portable manifest
metadata contract, so the original workstation-local evidence path is
historical only. Current releases should regenerate evidence with
`backlog-py compat evidence-template` and publish repo-relative artifact paths.

Result:

```text
agentCutoverReady: true
fullBrowserReleaseReady: true
releaseEvidence: fresh
implemented: 100
deferred: 0
total: 100
releaseGates:
  - browser:rich-edit-e2e-release-check: passed (full-browser-release)
  - browser:desktop-mobile-screenshot-release-check: passed (full-browser-release)
  - browser:complex-wysiwyg-round-trip: not_applicable (deferred-until-full-wysiwyg-scope)
  - browser:shell-hook-settings: passed (rejected-in-browser)
  - browser:service-transport-shutdown: passed (implemented-sse-contract)
```

Evidence:

- Rich edit E2E: opened `TASK-1`, switched Description to Rich mode, entered
  release-evidence text, switched to Preview, verified the preview text, saved,
  and verified the board reloaded with `TASK-1`.
- Screenshot release evidence: captured desktop and 390x844 mobile browser
  screenshots.
- Console health: no new browser warning/error entries during the fixed rich
  edit flow.
