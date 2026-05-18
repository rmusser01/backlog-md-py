# Browser Parity Decision

This document records browser UI parity requirements for a full Backlog.md clone
and separates them from the first local-file agent cutover candidate. Browser
support is valuable for human project management, but it is not required for
agent workflows that use plain CLI output and pure MCP helpers.

## Decision

Browser parity is intentionally deferred for the first agent cutover candidate.
The Python clone must not silently pretend to support browser behavior until the
items below are implemented and tested. Each browser item remains required for a
full clone unless explicitly rejected.

## Browser Requirements

| Requirement | Classification | Agent cutover impact | Rationale |
| --- | --- | --- | --- |
| Responsive Kanban board | Required for full clone | Not required for agent cutover | Agents use `board`, task listing, and MCP tools; responsive browser layout is a human UI requirement. |
| drag-and-drop task movement | Required for full clone | Implemented for status changes | Native drag/drop moves tasks across status columns through a loopback browser API protected by the project write lock, with invalid-status and persistence tests. |
| Read-only task detail dialog | Required for full clone | Implemented for inspection | Browser cards can open a read-only detail dialog backed by `/api/tasks/<id>` for metadata, description, Acceptance Criteria, and Definition of Done. |
| Task create/edit forms | Required for full clone | Basic create/edit implemented | Browser users can create tasks through a locked `/api/tasks` endpoint and edit owned task fields through a locked `/api/tasks/<id>/edit` endpoint. Rich edit flows remain deferred. |
| Acceptance criteria editor | Required for full clone | Basic replacement implemented | The browser edit form can replace Acceptance Criteria text through the safe core writer; rich AC check-state editing remains later UI work. |
| Definition of Done settings | Required for full clone | Not required for agent cutover | DoD defaults exist through config helpers, CLI, and MCP; browser settings can layer on the same safe core later. |
| Real-time updates | Required for full clone | Intentionally deferred | Live browser state needs a service process or polling contract and concurrent mutation tests. |
| Archive confirmations | Required for full clone | Implemented for task archive | Browser users can archive active tasks through a confirmation dialog backed by a locked `/api/tasks/<id>/archive` endpoint. |
| Rich Markdown editing | Required for full clone | Intentionally deferred | Rich editing must preserve unknown Markdown and frontmatter exactly, so it needs round-trip visual and parser tests. |
| mermaid rendering | Required for full clone | Intentionally deferred | Mermaid rendering is browser-only presentation behavior and does not affect CLI/MCP file correctness. |
| Custom port and no-open flags | Required for full clone | Implemented for loopback board service | `backlog-py browser --port <port> --no-open` starts a loopback board service, honors config defaults, and has port-collision and launch-policy tests. |
| service mode | Required for full clone | Basic foreground lifecycle implemented; richer UI service deferred | The Python service can start, serve health/board/HTML endpoints, mutate create/edit/status through the project write lock, and shut down cleanly; live-update, logging, and rich editing service behavior remains part of the browser milestone. |
| Mobile behavior | Required for full clone | Intentionally deferred | Mobile layout should be verified with real browser screenshots after the browser implementation exists. |

## Acceptance For Full Browser Clone

A later browser milestone should not be marked complete until it has:

- End-to-end browser tests for settings, rich edit, and checklist-state flows
  beyond the implemented drag-and-drop status movement, basic create/edit
  forms, archive confirmation, and read-only task detail inspection.
- Responsive checks for desktop and mobile viewports.
- A clear service mode lifecycle for any richer browser UI behavior, including
  logging and live-update shutdown policy.
- Round-trip tests proving rich Markdown editing does not damage frontmatter,
  owned sections, unknown body text, mermaid blocks, or checklist markers.
- Documentation that states whether the browser uses polling, server-sent
  events, WebSockets, or static reloads for real-time updates.

## Rejected For Agent Cutover

No browser-only capability is allowed to block the first agent cutover. The
first candidate is limited to local file operations through plain CLI output and
pure MCP helper functions.
