# Lesson Document Support Design

## Summary

Backlog repositories may keep durable, incident-backed working knowledge in
themed `backlog/docs/lessons-*.md` files. PR
[`rmusser01/tldw_chatbook#951`](https://github.com/rmusser01/tldw_chatbook/pull/951)
establishes that convention with H1-first Markdown files that deliberately do
not carry YAML frontmatter.

`backlog-md-py` already discovers every Markdown file below `backlog/docs/`,
but its document title comes only from frontmatter. H1-first documents
therefore appear as a path followed by a blank title in CLI, MCP, browser, and
TUI consumers. Its update path also serializes every document through the
frontmatter renderer, which can add an empty frontmatter block to a
frontmatterless file.

This change makes H1-first documents discoverable without requiring a format
migration and adds generic lesson-file guidance to generated agent
instructions.

## Goals

- Give H1-first documents a useful title everywhere `DocumentRecord.title` is
  consumed.
- Preserve frontmatter as the authoritative title when it exists.
- Avoid rewriting frontmatterless documents merely because their content is
  replaced or their directory changes.
- Avoid rendering the same derived title twice in browser document details.
- Teach generated agent instructions to discover relevant themed lesson files
  before work and to record only incident-backed, reusable lessons at
  completion.
- Keep the workflow generic so each project chooses its own lesson themes.

## Non-goals

- Do not create a single `LESSONS.md` or seed fixed lesson categories.
- Do not migrate existing documents to frontmatter.
- Do not add a mandatory project-level Definition of Done default.
- Do not infer titles from filenames, later headings, Setext headings, or
  arbitrary Markdown structure.
- Do not add document versioning, append APIs, or concurrent-edit conflict
  detection.
- Do not change document path containment, ID allocation, or malformed
  frontmatter handling.

## Document Title Resolution

Title resolution belongs in the core document loader so CLI, MCP, search,
browser, and TUI consumers receive the same value.

For each document:

1. Convert the existing `title` frontmatter value using the current semantics.
   If it is non-empty, use it unchanged.
2. Otherwise inspect the first nonblank body line.
3. If that line is an unindented ATX H1 of the form `# Title`, use its trimmed
   text.
4. Otherwise leave the title empty, matching current behavior.

Only the first nonblank line is eligible. This avoids interpreting a section
heading, fenced-code example, or later content as the document title. The H1
remains part of `content`, `body_source`, and `raw_source`; title derivation is
read-only metadata.

The fallback grammar is deliberately literal: the line must begin with `# `
(hash plus ASCII space), with no indentation. Additional spaces after that
prefix are allowed because the extracted text is trimmed. A tab after the
hash does not match, optional Markdown closing hashes are not removed, and
inline Markdown is not rendered or stripped. For example, `# **Evidence**`
produces the literal title `**Evidence**`. Supporting richer Markdown title
parsing is outside this compatibility fix.

Frontmatter precedence also covers documents that have both a frontmatter
title and an H1. The H1 is not required to match the authoritative
frontmatter value.

## Frontmatterless Mutation Preservation

Document moves and content replacements must not silently opt an H1-first
file into a new storage format.

When the existing document has no frontmatter and an update does not
explicitly request a title or metadata change:

- A directory-only move writes the original source bytes to the new safe path.
- A content replacement writes the replacement Markdown body without adding
  frontmatter. Existing content normalization may still ensure a terminal
  newline.

When an update explicitly supplies a title or metadata change, the existing
managed frontmatter renderer remains authoritative. That explicit metadata
operation may introduce frontmatter. This keeps title and metadata mutation
semantics predictable without inventing an H1 editor.

“No frontmatter” means the parser returned no raw frontmatter block. It must
not be inferred from `frontmatter == {}` because an explicit empty block and
an absent block produce the same metadata dictionary. An empty-but-present
frontmatter block remains in the managed-frontmatter path.

The document loader must also read text with newline translation disabled,
matching the repository's atomic writer and task loader. Otherwise a
directory-only move would normalize CRLF input to LF before writing even when
the source string is otherwise reused unchanged.

All resulting sources continue through the existing Markdown parser before
the atomic write. Path containment, duplicate-target checks, and safe move
ordering remain unchanged.

## Browser Presentation

The browser API continues returning its current parsed body value in
`content`. For the derived `contentHtml` presentation, it omits the leading H1
only when that heading text exactly matches the document title after
whitespace normalization. The dialog already displays `document.title` in its
own title element, so retaining both would duplicate the same visible
heading.

If the H1 differs from the frontmatter title, both remain visible. No source
content is removed or rewritten.

## Generated Agent Guidance

The owned Backlog.md workflow section generated into `AGENTS.md`, `CLAUDE.md`,
`GEMINI.md`, and `.github/copilot-instructions.md` gains generic lesson
guidance:

- If `backlog/docs/lessons-*.md` files exist, list them with
  `backlog-py --cwd <repo> doc list lessons`, then read the relevant themed
  file with `backlog-py --cwd <repo> doc view <path>` before starting work.
- At completion, if the task exposed a reusable trap, costly wrong assumption,
  or verification constraint, add or update the relevant themed lesson through
  the normal document workflow.
- Include the incident that produced the lesson so the claim remains
  evidence-backed.
- State explicitly that most tasks produce no lesson and agents must not
  invent one to satisfy the hook.

This is a conditional completion hook in agent guidance, not a new
`definitionOfDone` configuration default. Existing projects keep control of
their task checklist policy.

## User-facing Documentation

The integration guide will document that `doc list` and document search use a
leading H1 when frontmatter has no title, and that content-only updates and
moves preserve a frontmatterless document format. Its generated-agent section
will summarize the conditional lesson discovery and evidence-first completion
hook.

The Unreleased changelog will record the compatibility extension and format
preservation fix. Parity documentation will not claim this as upstream
behavior because upstream remains frontmatter-only.

## Data Flow

```text
backlog/docs/**/*.md
        |
        v
parse_task_markdown
        |
        v
DocumentService._load_document
  frontmatter title -> leading H1 fallback
        |
        +--> CLI doc list / global search
        +--> MCP document_list / document_view
        +--> TUI global search
        +--> browser list and detail payloads
```

The lesson workflow itself is documentation-driven; it adds no new service,
database, index, or configuration type.

## Error Handling and Compatibility

- Invalid or unterminated YAML frontmatter continues to raise the existing
  parse error.
- Documents without frontmatter and without a leading H1 remain valid and keep
  an empty title.
- Frontmatter IDs remain optional; H1-first lesson files are addressed by
  their docs-relative path.
- Search continues to include path and full content, so lesson files remain
  findable even when no title can be derived.
- Document ordering remains path-based.
- Generated instruction replacement remains idempotent inside the existing
  owned markers.

The upstream Backlog.md parser currently derives document titles from
frontmatter only. H1 fallback is therefore an intentional compatibility
extension for established repositories, not an upstream parity claim.

## Verification

Tests will prove:

- A frontmatterless document with a leading H1 exposes that H1 as its title.
- Leading blank lines are tolerated, but a later H1 after nonblank content is
  not treated as a title.
- A non-empty frontmatter title wins over a different H1.
- CLI and MCP document listings expose the derived title.
- Search can return the document using the derived title.
- Read-only operations leave the source byte-for-byte unchanged.
- Content-only updates and directory-only moves preserve the lack of
  frontmatter.
- A directory-only move preserves a frontmatterless CRLF source
  byte-for-byte.
- An empty-but-present frontmatter block is not mistaken for an absent block
  during an update.
- Explicit title or metadata updates continue using managed frontmatter.
- Browser `content` retains the H1 while `contentHtml` avoids an identical
  duplicate title.
- Generated instructions contain the conditional discovery, evidence-first
  completion hook, and “do not invent” safeguard.
- Agent instruction generation remains idempotent.

The focused document, browser, and agent-instruction tests run first, followed
by the full test suite.
