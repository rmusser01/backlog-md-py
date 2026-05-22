# Browser Rich Markdown Editor Design

## Decision

Add a dependency-free Rich mode to the browser Markdown editor as the next safe
step after Edit and Preview mode. Rich mode is a convenience layer for the
Markdown subset the browser renderer already supports. The hidden textarea
remains the source of truth for form submission, storage, and compatibility
with existing task mutation paths.

## Boundaries

- Do not add a third-party JavaScript editor or external browser dependency.
- Do not change task storage, frontmatter, or the locked create/edit endpoints.
- Do not expose `onStatusChange`, `bypassGitHooks`, or any browser shell
  execution setting.
- Do not claim complete WYSIWYG parity. This v1 supports a small round-trip
  subset and keeps raw Edit mode as the escape hatch.

## Supported V1 Surface

Rich mode applies to the existing Markdown editor fields: task create
description, task edit description, Implementation Notes, and Final Summary.
It supports headings, paragraphs, bullet lists, numbered lists, links, bold,
italic, inline code, and fenced code blocks. Link rendering must preserve
relative links and reject unsafe schemes. Unknown or complex Markdown can be
edited in raw Edit mode and previewed through the existing safe server renderer.

## Runtime Flow

1. The board HTML renders each Markdown editor with Edit, Preview, and Rich
   mode buttons.
2. Rich mode converts the current textarea Markdown into a `contenteditable`
   pane using browser-safe DOM construction, not unsanitized `innerHTML`.
3. While users type in the Rich pane, the paired textarea is updated with
   Markdown converted from the pane DOM.
4. Switching from Rich to Edit serializes the pane to Markdown before showing
   the textarea.
5. Switching to Preview serializes Rich edits first, then uses the existing
   `/api/markdown/preview` endpoint.
6. Opening create/edit dialogs resets editors back to Edit mode.

## Verification

Tests must cover the HTML/JS contract for Rich mode, client helpers that convert
Markdown to a rich DOM and back, and preservation of raw form submission
through the hidden textarea. Existing endpoint tests continue proving server
storage, same-origin preview rendering, and write-lock behavior.
