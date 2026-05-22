# Browser Markdown Editor Preview Design

## Decision

Add a browser Markdown editor preview mode as the next safe step toward richer
browser editing parity. The browser keeps raw Markdown textareas as the source
of truth, but each Markdown editor can switch between Edit and Preview. Preview
HTML is rendered by the loopback service through the same safe Markdown
renderer used by task, document, and decision detail views.

## Boundaries

- Do not introduce a third-party WYSIWYG editor or external client dependency.
- Do not change task storage. The edit form still submits raw Markdown strings
  to the existing locked task create/edit endpoints.
- Do not expose shell-hook or hook-bypass settings in the browser.
- Do not claim full WYSIWYG parity. This is a safer preview/edit milestone that
  improves editing without adding rich DOM-to-Markdown conversion risk.

## Runtime Flow

1. The board HTML renders each Markdown textarea inside a Markdown editor
   container with Edit and Preview mode buttons.
2. Edit mode shows the existing toolbar and textarea.
3. Preview mode posts the current textarea value to `/api/markdown/preview`.
4. The server validates same-origin access for the POST request, renders the
   text with `_markdown_to_html()`, and returns `{ "html": "<safe html>" }`.
5. The browser inserts the returned HTML into the preview panel and runs the
   existing Mermaid renderer on that panel.
6. Switching back to Edit mode shows the unchanged textarea so form submission
   continues using raw Markdown.

## Verification

Tests must cover the new preview endpoint, cross-origin rejection, unsafe HTML
escaping, the editor HTML contract, and the client-side mode-switching hooks.
Existing task create/edit endpoint tests continue proving raw Markdown storage
and project write-lock behavior.
