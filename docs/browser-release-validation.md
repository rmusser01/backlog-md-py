# Browser Release Validation

`backlog-py compat status` separates implemented feature coverage from browser
release readiness. A clone can report all audited features as implemented while
still requiring browser release evidence before advertising full browser parity.

## Evidence Manifest

Pass a JSON manifest with `--release-evidence`:

```bash
backlog-py compat status --release-evidence browser-release-evidence.json
backlog-py compat status --json --release-evidence browser-release-evidence.json
```

The manifest must use release gate names as keys:

```json
{
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
output.

## Latest Validation Result

Validated on 2026-05-22 against the `basic-fixture` project after the browser
task edit metadata-field fix merged.

Command:

```bash
PYTHONPATH=src .venv/bin/python -m backlog_py compat status --release-evidence /private/tmp/backlog-browser-release-evidence/browser-release-evidence.json
```

Result:

```text
agentCutoverReady: true
fullBrowserReleaseReady: true
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
