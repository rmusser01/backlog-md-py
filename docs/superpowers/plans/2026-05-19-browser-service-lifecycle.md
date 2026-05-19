## Goal

Add browser-service lifecycle visibility and a guarded shutdown path so the Python browser mode covers another upstream service-management surface without introducing shell/git controls or non-loopback exposure.

## Design Notes

- Keep the service loopback-only and reuse the existing origin guard for the mutating shutdown endpoint.
- Add read-only service metadata at `/api/service/status` with project root, backlog directory, host, port, root URL, and shutdown capability.
- Add `POST /api/service/shutdown` as a local browser action that schedules server shutdown after the JSON response is sent.
- Expose a compact Service dialog in the static browser UI with a status refresh and explicit Stop server action.

## Stage 1: Service Status Endpoint

**Goal**: Return runtime metadata for the active browser server.
**Success Criteria**: `/api/service/status` returns project and service fields for the currently bound host/port.
**Tests**: Add a browser service test that starts on port `0` and asserts status metadata matches the actual service.
**Status**: Complete

## Stage 2: Guarded Shutdown Endpoint and UI

**Goal**: Provide an in-browser shutdown path without weakening local-only behavior.
**Success Criteria**: Cross-origin shutdown requests are rejected; same-origin/no-origin shutdown requests return accepted and stop the server thread.
**Tests**: Add endpoint tests for cross-origin rejection and scheduled shutdown, plus an HTML contract test for the Service dialog/button.
**Status**: Complete

## Stage 3: Parity Inventory and Docs

**Goal**: Record the implemented lifecycle surface in compatibility artifacts.
**Success Criteria**: Compatibility counts include `browser:service-lifecycle`; browser parity docs describe status and guarded shutdown.
**Tests**: Update compat report, CLI read-only, oracle manifest, and agent-critical matrix expectations.
**Status**: Complete

## Stage 4: Verification and Delivery

**Goal**: Prove the slice is shippable before PR/merge.
**Success Criteria**: Focused tests, full tests, diff check, Bandit, compat JSON, build, and twine checks pass; working tree is clean after cleanup.
**Tests**: Run the standard local verification commands and record results in the PR/final summary.
**Status**: Complete
