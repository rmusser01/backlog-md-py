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
| Task detail dialog | Required for full clone | Implemented for inspection, Markdown rendering, and checklist state | Browser cards can open a detail dialog backed by `/api/tasks/<id>` for metadata, safe Markdown-rendered description, Implementation Notes, Final Summary, Acceptance Criteria, and Definition of Done. Checklist controls update AC/DoD check state through a locked `/api/tasks/<id>/checklist` endpoint. |
| Task create/edit forms | Required for full clone | Basic create/edit plus rich section replacement implemented | Browser users can create tasks through a locked `/api/tasks` endpoint and edit owned task fields through a locked `/api/tasks/<id>/edit` endpoint, including raw Markdown replacement for Implementation Notes and Final Summary. Broader rich edit flows remain deferred. |
| Acceptance criteria editor | Required for full clone | Basic replacement and check-state controls implemented | The browser edit form can replace Acceptance Criteria text through the safe core writer, and the task detail dialog can check or uncheck AC items without replacing the list. Rich text editing remains later UI work. |
| Definition of Done settings | Required for full clone | Implemented for DoD defaults | Browser users can view and update project-level Definition of Done defaults through a settings dialog backed by the same safe config writer used by CLI and MCP. |
| General project settings | Required for full clone | Implemented for safe non-shell settings | Browser users can view and update `projectName`, `defaultAssignee`, `defaultStatus`, `dateFormat`, `includeDatetimeInDates`, `defaultPort`, `autoOpenBrowser`, `zeroPaddedIds`, and `statuses` through a locked settings endpoint. Shell-hook and git automation settings remain outside the browser surface. |
| Real-time updates | Required for full clone | Implemented with conservative polling | The browser page polls `/api/board` for a deterministic board revision and reloads when external CLI, MCP, or browser-tab edits change task state. Richer SSE/WebSocket updates remain out of scope. |
| Archive confirmations | Required for full clone | Implemented for task archive | Browser users can archive active tasks through a confirmation dialog backed by a locked `/api/tasks/<id>/archive` endpoint. |
| Rich Markdown editing | Required for full clone | Safe rendering and owned-section replacement implemented | Task detail Markdown is rendered through a dependency-free safe HTML renderer, and the browser edit form can replace raw Markdown Implementation Notes and Final Summary sections through the existing parser-preserving writer. Broader rich editing and visual Markdown tools remain deferred. |
| mermaid rendering | Required for full clone | Intentionally deferred | Mermaid rendering is browser-only presentation behavior and does not affect CLI/MCP file correctness. |
| Custom port and no-open flags | Required for full clone | Implemented for loopback board service | `backlog-py browser --port <port> --no-open` starts a loopback board service, honors config defaults, and has port-collision and launch-policy tests. |
| service mode | Required for full clone | Implemented for status, request logging, and guarded local shutdown | The Python service can start, serve health/board/HTML endpoints, expose `/api/service/status`, expose a bounded body-free `/api/service/requests` request log, mutate create/edit/status through the project write lock, and stop through a same-origin `/api/service/shutdown` dialog action. Richer live-update shutdown policy remains part of the browser milestone. |
| Mobile behavior | Required for full clone | Intentionally deferred | Mobile layout should be verified with real browser screenshots after the browser implementation exists. |

## Acceptance For Full Browser Clone

A later browser milestone should not be marked complete until it has:

- End-to-end browser tests for rich edit flows beyond the
  implemented drag-and-drop status movement, basic create/edit forms, archive
  confirmation, checklist-state controls, task detail inspection, safe
  Markdown rendering, raw Markdown Implementation Notes/Final Summary
  replacement, Definition of Done default settings, and safe general project
  settings.
- Responsive checks for desktop and mobile viewports.
- A clear service mode lifecycle for any richer browser UI behavior, including
  live-update shutdown policy beyond the implemented local stop action and
  bounded request log.
- Round-trip tests proving rich Markdown editing does not damage frontmatter,
  owned sections, unknown body text, mermaid blocks, or checklist markers.
- Documentation for any future move beyond the implemented polling/reload
  contract, such as server-sent events or WebSockets.

## Rejected For Agent Cutover

No browser-only capability is allowed to block the first agent cutover. The
first candidate is limited to local file operations through plain CLI output and
pure MCP helper functions.
