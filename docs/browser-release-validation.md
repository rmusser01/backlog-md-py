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
