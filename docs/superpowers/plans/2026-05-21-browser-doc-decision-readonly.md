## Stage 1: Upstream Browser Surface Check
**Goal**: Verify the next browser parity slice against upstream `backlog.md@1.45.1`.
**Success Criteria**: Source evidence identifies read-only documentation and decision browser routes as an untracked Python browser gap.
**Tests**: N/A.
**Status**: Complete

## Stage 2: Read-Only Browser APIs
**Goal**: Add `/api/docs`, `/api/docs/<id-or-path>`, `/api/decisions`, and `/api/decisions/<id>` list/detail payloads.
**Success Criteria**: Endpoints return safe Markdown-rendered payloads without write paths.
**Tests**: Focused browser service endpoint tests.
**Status**: Complete

## Stage 3: Static Browser Dialogs
**Goal**: Expose Documents and Decisions dialogs from the loopback browser page.
**Success Criteria**: HTML contains controls, lists, detail containers, and client code for read-only list/detail inspection.
**Tests**: Focused browser HTML contract test.
**Status**: Complete

## Stage 4: Parity Tracking And Verification
**Goal**: Track the new browser surface in inventory, oracle manifest, and parity docs.
**Success Criteria**: Compatibility counts include `browser:document-decision-readonly`.
**Tests**: Inventory, compatibility, browser, Bandit, diff, build, and package checks.
**Status**: Complete
