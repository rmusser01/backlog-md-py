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

The browser service remains dependency-free and does not require a Node, Bun, or
frontend bundler step. The served board is rendered from packaged resources under
`backlog_py.browser.templates` and `backlog_py.browser.assets`; distribution
metadata includes those resources in wheels and sdists so installed-package
behavior matches source-tree behavior.

## Browser Requirements

| Requirement | Classification | Agent cutover impact | Rationale |
| --- | --- | --- | --- |
| Responsive Kanban board | Required for full clone | Implemented for narrow viewports | The static board includes explicit mobile viewport rules for header actions, board columns, task actions, dialogs, and form actions while preserving the dependency-free browser service. |
| drag-and-drop task movement | Required for full clone | Implemented for status changes | Native drag/drop moves tasks across status columns through a loopback browser API protected by the project write lock, with invalid-status and persistence tests. |
| Task detail dialog | Required for full clone | Implemented for inspection, Markdown rendering, Mermaid diagrams, and checklist state | Browser cards can open a detail dialog backed by `/api/tasks/<id>` for metadata, safe Markdown-rendered description, client-side Mermaid diagrams, Implementation Notes, Final Summary, Acceptance Criteria, and Definition of Done. Checklist controls update AC/DoD check state through a locked `/api/tasks/<id>/checklist` endpoint. |
| Document and decision detail | Required for full clone | Implemented for read-only inspection | Browser users can open read-only Documents and Decisions dialogs. The loopback service exposes `/api/docs`, `/api/docs/<id-or-path>`, `/api/decisions`, and `/api/decisions/<id>` for list/detail payloads, with safe Markdown HTML rendering for document bodies and decision sections. |
| Task create/edit forms | Required for full clone | Basic create/edit plus metadata, Markdown toolbar, safe preview, Rich mode v1, and rich section replacement implemented | Browser users can create tasks through a locked `/api/tasks` endpoint and edit owned task fields through a locked `/api/tasks/<id>/edit` endpoint, including assignees, labels, priority, milestone, raw Markdown replacement for Implementation Notes and Final Summary, a local Markdown formatting toolbar for raw textareas, safe server-rendered preview mode, and dependency-free Rich mode for the supported Markdown subset. Broader WYSIWYG edit flows remain deferred. |
| Acceptance criteria editor | Required for full clone | Basic replacement and check-state controls implemented | The browser edit form can replace Acceptance Criteria text through the safe core writer, and the task detail dialog can check or uncheck AC items without replacing the list. Rich text editing remains later UI work. |
| Definition of Done settings | Required for full clone | Implemented for DoD defaults | Browser users can view and update project-level Definition of Done defaults through a settings dialog backed by the same safe config writer used by CLI and MCP. |
| General project settings | Required for full clone | Implemented for safe non-shell settings | Browser users can view and update `projectName`, `defaultAssignee`, `defaultStatus`, `dateFormat`, `includeDatetimeInDates`, `defaultPort`, `autoOpenBrowser`, `zeroPaddedIds`, and `statuses` through a locked settings endpoint. Shell-hook settings remain outside the browser surface. |
| Git automation settings | Required for full clone | Implemented for safe non-shell settings | Browser users can view and update `remoteOperations`, `checkActiveBranches`, `activeBranchDays`, and `autoCommit` through the locked project settings endpoint. `onStatusChange` and `bypassGitHooks` remain rejected by the browser API because they introduce shell execution or hook-bypass risk. |
| Real-time updates | Required for full clone | Implemented with SSE, shutdown events, and polling fallback | The browser page subscribes to `/api/board/events` for deterministic board revision events and reloads when external CLI, MCP, or browser-tab edits change task state. Browsers without `EventSource` keep using conservative `/api/board` polling. When service shutdown starts, the same SSE endpoint emits a shutdown event so the client closes the EventSource and stops revision polling. |
| Archive confirmations | Required for full clone | Implemented for task archive | Browser users can archive active tasks through a confirmation dialog backed by a locked `/api/tasks/<id>/archive` endpoint. |
| Rich Markdown editing | Required for full clone | Safe rendering, Mermaid detail diagrams, Markdown toolbar, server-rendered preview, dependency-free Rich mode v1, and owned-section replacement implemented | Task detail Markdown is rendered through a safe HTML renderer, Mermaid fences are exposed to the client-side Mermaid renderer with strict security settings, and the browser edit form can replace raw Markdown Implementation Notes and Final Summary sections through the existing parser-preserving writer. Description, Implementation Notes, and Final Summary editors expose raw Edit mode, safe Preview mode, and Rich mode for headings, paragraphs, lists, links, bold, italic, inline code, and fenced code blocks while keeping textareas as the submission source of truth. Full WYSIWYG parity remains deferred. |
| mermaid rendering | Required for full clone | Implemented for task details | Mermaid fenced blocks in task detail Markdown render as browser diagrams through a client-side Mermaid loader while preserving escaped server HTML and safe fallback source text if rendering fails. |
| Custom port and no-open flags | Required for full clone | Implemented for loopback board service | `backlog-py browser --port <port> --no-open` starts a loopback board service, honors config defaults, and has port-collision and launch-policy tests. |
| service mode | Required for full clone | Implemented for status, request logging, guarded local shutdown state, and SSE shutdown policy | The Python service can start, serve health/board/HTML endpoints, expose `/api/service/status`, expose a bounded body-free `/api/service/requests` request log, mutate create/edit/status through the project write lock, and stop through a same-origin `/api/service/shutdown` dialog action. Shutdown requests are idempotent, surface pending shutdown state in the Service dialog, and notify the SSE board transport so the browser stops reconnecting or polling. |
| Mobile behavior | Required for full clone | Implemented for narrow viewport layout | Narrow viewport layout is covered by the browser HTML/CSS contract; richer device-specific visual QA can be handled as release validation instead of a missing parity feature. |

## Acceptance For Full Browser Clone

`backlog-py compat status` tracks these release checks separately from feature
coverage. The browser feature inventory can be implemented while
`fullBrowserReleaseReady` remains false until the release-validation gates below
have evidence. The machine-readable evidence format is documented in
`docs/browser-release-validation.md`.

The current browser release-readiness milestone is complete when a fresh
`backlog-py compat status --release-evidence <manifest.json>` reports
`fullBrowserReleaseReady: true`. Historical validation runs are recorded in
`docs/browser-release-validation.md`, but current release candidates should
regenerate the portable evidence manifest and attach repo-relative browser
artifacts.

Future browser milestones should not be marked complete until they have:

- End-to-end browser tests for rich edit flows beyond the
  implemented drag-and-drop status movement, basic create/edit forms, Markdown
  edit toolbar, safe server-rendered preview, Rich mode v1, archive
  confirmation, checklist-state controls, document/decision read-only
  inspection, task detail inspection, safe
  Markdown rendering, Mermaid detail diagrams, raw Markdown Implementation
  Notes/Final Summary replacement, metadata editing, Definition of Done
  default settings, safe general project settings, safe git automation
  settings, and responsive narrow-viewport layout.
- Browser screenshot checks for desktop and mobile viewports before a tagged
  release that advertises full browser parity.
- A clear service mode lifecycle for any future non-SSE persistent transport
  beyond the implemented SSE revision/shutdown events, local stop action,
  shutdown state, and bounded request log.
- Round-trip tests proving a future full WYSIWYG editor does not damage
  frontmatter, owned sections, unknown body text, mermaid blocks, or checklist
  markers. Rich mode v1 remains intentionally limited to the supported Markdown
  subset and keeps raw Edit mode available for complex Markdown.
- Documentation for any future move beyond the implemented
  SSE/polling/reload/shutdown contract, such as WebSockets.

## Rejected For Agent Cutover

No browser-only capability is allowed to block the first agent cutover. The
first candidate is limited to local file operations through plain CLI output and
pure MCP helper functions.
