# Lesson Document Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make H1-first `backlog/docs/lessons-*.md` files discoverable and safely mutable without forcing frontmatter, while adding a conditional incident-backed lesson hook to generated agent instructions.

**Architecture:** Resolve document titles once in `core/documents.py`, using frontmatter first and the first nonblank literal `# ` heading as a fallback. Keep frontmatterless mutations on a plain-Markdown path unless metadata is explicitly changed, preserve newline bytes for moves, and suppress a matching duplicate H1 only in browser-rendered HTML. Extend the existing owned agent-instruction template rather than adding configuration or new lesson-file types.

**Tech Stack:** Python 3.11+, Click, PyYAML, pytest, the existing dependency-free Markdown renderer, and existing atomic/path-safe document services.

---

## Reference Documents

- Design spec: `docs/superpowers/specs/2026-07-26-lesson-document-support-design.md`
- External motivating PR: `https://github.com/rmusser01/tldw_chatbook/pull/951`

## Worktree and Test Runner

Execute every step from:

```text
/Users/macbook-dev/Documents/GitHub/backlog-md-py/.worktrees/lesson-doc-support
```

Use the already-provisioned parent virtual environment without creating or
copying a lockfile:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest ...
```

The full suite binds loopback sockets. In a managed sandbox, request the
required loopback permission rather than treating socket-denial failures as
product failures.

Do not add or modify the untracked `uv.lock` in the main checkout.

## File Map

- Modify `src/backlog_py/core/documents.py`
  - Read document source without newline translation.
  - Derive a fallback title from a leading H1.
  - Preserve absent frontmatter on content-only updates and moves.
- Modify `src/backlog_py/browser/service.py`
  - Render browser detail HTML without a duplicate matching leading H1.
- Modify `src/backlog_py/core/agents.py`
  - Add generic lesson discovery and conditional evidence-first completion guidance.
- Modify `tests/test_documents.py`
  - Cover title resolution, CLI/MCP propagation, source preservation, CRLF moves, and absent-vs-empty frontmatter.
- Modify `tests/test_browser_service.py`
  - Cover browser content/contentHtml behavior and whitespace normalization.
- Modify `tests/test_agent_instructions.py`
  - Cover lesson guidance in every generated instruction file.
- Modify `docs/integration.md`
  - Document H1-first document compatibility and generated lesson guidance.
- Modify `CHANGELOG.md`
  - Record the compatibility extension and format-preservation fix under Unreleased.

### Task 1: Resolve H1-first document titles in the core loader

**Files:**

- Modify: `tests/test_documents.py`
- Modify: `src/backlog_py/core/documents.py`

- [ ] **Step 1: Add failing title-resolution and propagation tests**

Add focused tests near the existing document create/list tests:

```python
@pytest.mark.parametrize(
    ("source", "expected_title"),
    [
        ("\n# Lessons: testing evidence\n\nIncident-backed body.\n", "Lessons: testing evidence"),
        ("#  Lessons with spacing  \n\nBody.\n", "Lessons with spacing"),
        ("# **Evidence**\n\nBody.\n", "**Evidence**"),
        ("Intro first.\n\n# Later heading\n", ""),
        ("#\tTabbed heading\n", ""),
        (
            "---\ntitle: Frontmatter title\n---\n\n# Different body heading\n",
            "Frontmatter title",
        ),
    ],
)
def test_document_title_resolution_uses_frontmatter_then_leading_literal_h1(
    tmp_path, source, expected_title
):
    repo = _copy_fixture(tmp_path)
    document_path = repo / "backlog" / "docs" / "lessons-testing.md"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_text(source, encoding="utf-8")

    assert _service(repo).view_document("lessons-testing.md").title == expected_title


def test_frontmatterless_h1_title_flows_through_search_cli_and_mcp_without_rewrite(tmp_path):
    repo = _copy_fixture(tmp_path)
    document_path = repo / "backlog" / "docs" / "lessons-testing-evidence.md"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    source = "# Lessons: what counts as evidence\n\nIncident-backed body.\n"
    document_path.write_text(source, encoding="utf-8")
    before = document_path.read_bytes()

    service = _service(repo)
    assert [item.title for item in service.search_documents("counts as evidence")] == [
        "Lessons: what counts as evidence"
    ]

    cli = CliRunner().invoke(main, ["--cwd", str(repo), "doc", "list", "lessons"])
    assert cli.exit_code == 0
    assert "lessons-testing-evidence.md Lessons: what counts as evidence" in cli.output

    listed = document_list(_project(repo), query="lessons")
    assert listed[0]["title"] == "Lessons: what counts as evidence"
    assert document_path.read_bytes() == before
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_documents.py::test_document_title_resolution_uses_frontmatter_then_leading_literal_h1 \
  tests/test_documents.py::test_frontmatterless_h1_title_flows_through_search_cli_and_mcp_without_rewrite -q
```

Expected: FAIL because the frontmatterless documents currently expose `title == ""`,
and the CLI/MCP lines therefore omit the authored H1 title.

- [ ] **Step 3: Implement newline-preserving reads and literal H1 fallback**

In `src/backlog_py/core/documents.py`, read source verbatim:

```python
def _load_document(base: Path, path: Path) -> DocumentRecord:
    with path.open("r", encoding="utf-8", newline="") as document_file:
        raw_source = document_file.read()
    parsed = parse_task_markdown(raw_source)
    frontmatter = dict(parsed.frontmatter)
    return DocumentRecord(
        id=None if frontmatter.get("id") is None else str(frontmatter.get("id")),
        title=_document_title(frontmatter, parsed.body),
        path=path,
        path_relative=path.relative_to(base).as_posix(),
        content=parsed.body.strip(),
        body_source=parsed.body,
        frontmatter=frontmatter,
        raw_source=raw_source,
    )
```

Add the smallest title helpers:

```python
def _document_title(frontmatter: dict[str, Any], body_source: str) -> str:
    title = str(frontmatter.get("title") or "")
    return title or _leading_h1_title(body_source)


def _leading_h1_title(body_source: str) -> str:
    for line in body_source.splitlines():
        if not line.strip():
            continue
        if not line.startswith("# "):
            return ""
        return line[2:].strip()
    return ""
```

Do not strip closing hashes or inline Markdown, accept `#  Title` through the
existing substring trim, and do not scan after the first nonblank line.

- [ ] **Step 4: Run the focused title tests and verify GREEN**

Run the Step 2 command again.

Expected: PASS.

- [ ] **Step 5: Run the complete document test module**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_documents.py -q
```

Expected: all document tests pass.

- [ ] **Step 6: Commit the title-resolution slice**

```bash
git add src/backlog_py/core/documents.py tests/test_documents.py
git commit -m "feat: derive document titles from leading H1"
```

### Task 2: Preserve frontmatterless document format during updates and moves

**Files:**

- Modify: `tests/test_documents.py`
- Modify: `src/backlog_py/core/documents.py`

- [ ] **Step 1: Add failing frontmatter-preservation tests**

Add these focused behaviors:

```python
def test_content_update_preserves_frontmatterless_document_format(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "docs" / "lessons-testing.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Lessons: testing\n\nOld incident.\n", encoding="utf-8")

    updated = _service(repo).update_document(
        "lessons-testing.md",
        content="# Lessons: testing\n\nNew incident.",
    )

    assert updated.raw_source == "# Lessons: testing\n\nNew incident.\n"
    assert not updated.raw_source.startswith("---")
    assert updated.title == "Lessons: testing"


def test_directory_move_preserves_frontmatterless_crlf_source_byte_for_byte(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "docs" / "lessons-testing.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    source = b"# Lessons: testing\r\n\r\nIncident.\r\n"
    path.write_bytes(source)

    moved = _service(repo).update_document("lessons-testing.md", directory="archive")

    assert not path.exists()
    assert moved.path.read_bytes() == source
    assert moved.raw_source == source.decode("utf-8")


def test_empty_frontmatter_block_is_not_treated_as_absent_during_update(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "docs" / "lessons-testing.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n{}\n---\n# Lessons: testing\n\nOld.\n", encoding="utf-8")

    updated = _service(repo).update_document(
        "lessons-testing.md",
        content="# Lessons: testing\n\nNew.",
    )

    assert updated.raw_source.startswith("---\n{}\n---\n")
    assert updated.frontmatter == {}


def test_explicit_title_update_adopts_managed_frontmatter_for_plain_document(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "docs" / "lessons-testing.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Lessons: testing\n\nIncident.\n", encoding="utf-8")

    updated = _service(repo).update_document("lessons-testing.md", title="Managed title")

    assert updated.raw_source.startswith("---\ntitle: Managed title\n---\n")
    assert updated.title == "Managed title"


def test_explicit_metadata_update_adopts_managed_frontmatter_for_plain_document(tmp_path):
    repo = _copy_fixture(tmp_path)
    path = repo / "backlog" / "docs" / "lessons-testing.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Lessons: testing\n\nIncident.\n", encoding="utf-8")

    updated = _service(repo).update_document(
        "lessons-testing.md",
        metadata={"type": "lesson"},
    )

    assert updated.raw_source.startswith("---\ntype: lesson\n---\n")
    assert updated.frontmatter == {"type": "lesson"}
```

- [ ] **Step 2: Run the new mutation tests and verify RED**

Run:

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_documents.py::test_content_update_preserves_frontmatterless_document_format \
  tests/test_documents.py::test_directory_move_preserves_frontmatterless_crlf_source_byte_for_byte \
  tests/test_documents.py::test_empty_frontmatter_block_is_not_treated_as_absent_during_update \
  tests/test_documents.py::test_explicit_title_update_adopts_managed_frontmatter_for_plain_document \
  tests/test_documents.py::test_explicit_metadata_update_adopts_managed_frontmatter_for_plain_document -q
```

Expected: the content update and move tests FAIL because the current updater
injects frontmatter. The empty-block and explicit-title tests may already pass;
they are characterization guards against taking the new branch too broadly.

- [ ] **Step 3: Implement the plain-Markdown mutation branch**

In `DocumentService.update_document`, name the metadata input once so explicit
metadata intent is preserved:

```python
metadata_updates = dict(metadata or {})
frontmatter = dict(document.frontmatter)
if title is not None:
    frontmatter["title"] = title
for key, value in metadata_updates.items():
    if value is None:
        frontmatter.pop(key, None)
    else:
        frontmatter[key] = value
```

Choose the source format from raw-frontmatter presence, not dictionary truthiness:

```python
has_frontmatter = parse_task_markdown(document.raw_source).raw_frontmatter is not None
preserve_plain_markdown = not has_frontmatter and title is None and not metadata_updates
if preserve_plain_markdown:
    source = (
        document.raw_source
        if content is None
        else _render_frontmatterless_document(content)
    )
else:
    source = (
        _render_document_body(frontmatter, document.body_source)
        if content is None
        else _render_document(frontmatter, content)
    )
```

Add the minimal renderer:

```python
def _render_frontmatterless_document(content: str) -> str:
    return f"{content.strip()}\n"
```

Keep the existing parse-before-write, containment check, duplicate-target
check, atomic write, and source unlink ordering unchanged.

- [ ] **Step 4: Run the mutation tests and verify GREEN**

Run the Step 2 command again.

Expected: all five tests pass.

- [ ] **Step 5: Run all document tests**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_documents.py -q
```

Expected: all document tests pass.

- [ ] **Step 6: Commit the mutation-preservation slice**

```bash
git add src/backlog_py/core/documents.py tests/test_documents.py
git commit -m "fix: preserve frontmatterless document updates"
```

### Task 3: Avoid duplicate browser headings

**Files:**

- Modify: `tests/test_browser_service.py`
- Modify: `src/backlog_py/browser/service.py`

- [ ] **Step 1: Add failing browser payload tests**

Use the pure payload helper so this test does not require a loopback server:

```python
def test_browser_document_html_omits_only_a_whitespace_equivalent_title_h1(tmp_path):
    repo = _copy_fixture_repo(tmp_path)
    docs_dir = repo / "backlog" / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    derived = docs_dir / "derived.md"
    derived.write_text(
        "# Lessons: derived title\n\nBody.\n",
        encoding="utf-8",
    )
    matching = docs_dir / "matching.md"
    matching.write_text(
        "---\ntitle: Lessons: testing evidence\n---\n"
        "# Lessons:   testing evidence\n\nBody.\n",
        encoding="utf-8",
    )
    different = docs_dir / "different.md"
    different.write_text(
        "---\ntitle: Setup Guide\n---\n# Different heading\n\nBody.\n",
        encoding="utf-8",
    )
    project = discover_project(Path.cwd(), explicit_cwd=repo)

    from backlog_py.browser.service import _document_detail_payload

    derived_payload = _document_detail_payload(
        DocumentService(project).view_document("derived.md")
    )
    matching_payload = _document_detail_payload(
        DocumentService(project).view_document("matching.md")
    )
    different_payload = _document_detail_payload(
        DocumentService(project).view_document("different.md")
    )

    assert derived_payload["title"] == "Lessons: derived title"
    assert derived_payload["content"].startswith("# Lessons: derived title")
    assert "<h1>" not in derived_payload["contentHtml"]
    assert matching_payload["content"].startswith("# Lessons:   testing evidence")
    assert "<h1>" not in matching_payload["contentHtml"]
    assert "<p>Body.</p>" in matching_payload["contentHtml"]
    assert "<h1>Different heading</h1>" in different_payload["contentHtml"]
```

Add `DocumentService` to this test module's imports if it is not already available.

- [ ] **Step 2: Run the browser test and verify RED**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_browser_service.py::test_browser_document_html_omits_only_a_whitespace_equivalent_title_h1 -q
```

Expected: FAIL because `contentHtml` currently renders the matching H1.

- [ ] **Step 3: Implement exact whitespace normalization and display-body selection**

In `src/backlog_py/browser/service.py`, add:

```python
def _document_content_for_html(document: DocumentRecord) -> str:
    lines = document.content.splitlines(keepends=True)
    if not lines:
        return document.content
    first_line = lines[0].rstrip("\r\n")
    if not first_line.startswith("# "):
        return document.content
    heading = first_line[2:].strip()
    if _normalized_document_title(heading) != _normalized_document_title(document.title):
        return document.content
    return "".join(lines[1:]).lstrip("\r\n")


def _normalized_document_title(value: str) -> str:
    return " ".join(value.split())
```

This defines whitespace normalization as trimming and collapsing all
whitespace runs, including internal spaces and tabs, to one ASCII space.

Change only the derived HTML field:

```python
"contentHtml": _markdown_to_html(_document_content_for_html(document)),
```

Keep `content` unchanged.

- [ ] **Step 4: Run the browser test and verify GREEN**

Run the Step 2 command again.

Expected: PASS.

- [ ] **Step 5: Run the browser service tests**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest tests/test_browser_service.py -q
```

Expected: all browser service tests pass. In a managed sandbox, allow loopback
binding for endpoint tests.

- [ ] **Step 6: Commit the browser presentation slice**

```bash
git add src/backlog_py/browser/service.py tests/test_browser_service.py
git commit -m "fix: avoid duplicate browser document title"
```

### Task 4: Add conditional lesson guidance and user-facing documentation

**Files:**

- Modify: `tests/test_agent_instructions.py`
- Modify: `src/backlog_py/core/agents.py`
- Modify: `docs/integration.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a failing generated-guidance test**

```python
def test_cli_agents_update_instructions_writes_incident_backed_lesson_workflow(tmp_path):
    repo = _copy_fixture(tmp_path)

    result = CliRunner().invoke(main, ["--cwd", str(repo), "agents", "--update-instructions"])

    assert result.exit_code == 0
    for relative_path in (
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        ".github/copilot-instructions.md",
    ):
        content = (repo / relative_path).read_text(encoding="utf-8")
        for fragment in (
            "backlog/docs/lessons-*.md",
            "doc list lessons",
            "doc view <path>",
            "incident",
            "Most tasks produce no lesson",
            "do not invent",
        ):
            assert fragment in content
```

- [ ] **Step 2: Run the new guidance test and verify RED**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_agent_instructions.py::test_cli_agents_update_instructions_writes_incident_backed_lesson_workflow -q
```

Expected: FAIL because the generated block does not mention lesson files.

- [ ] **Step 3: Add the generic lesson workflow to the owned instruction block**

In `_instruction_section()` after the task-lifecycle bullets, add a focused
subsection equivalent to:

```python
"### Project lessons\n\n"
"- If `backlog/docs/lessons-*.md` files exist, list them with "
"`backlog-py --cwd <repo> doc list lessons` and read the relevant themed "
"file with `backlog-py --cwd <repo> doc view <path>` before starting.\n"
"- Before completing a task, if it exposed a reusable trap, costly wrong "
"assumption, or verification constraint, add or update the relevant themed "
"lesson through the normal document workflow. Include the incident that "
"produced it so the lesson remains evidence-backed. Most tasks produce no "
"lesson; do not invent one to satisfy this hook.\n\n"
```

Do not seed files, mutate `definitionOfDone`, or add project-specific themes.

- [ ] **Step 4: Run agent instruction tests and verify GREEN**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_agent_instructions.py tests/test_init_project.py -q
```

Expected: all tests pass, including idempotent replacement and init-generated
instructions.

- [ ] **Step 5: Update the integration guide**

In `docs/integration.md`:

- Add `doc list lessons` and `doc view lessons-testing-evidence.md` examples
  near the existing document commands.
- Explain that a non-empty frontmatter title remains authoritative; otherwise
  an unindented H1 on the first nonblank body line supplies the displayed
  title.
- State that content-only updates and moves preserve an absent frontmatter
  block.
- Extend “Generated Agent Instructions” with the conditional themed-lesson
  discovery and incident-backed completion hook, including that most tasks
  should add nothing.

- [ ] **Step 6: Update the Unreleased changelog**

Under `## Unreleased`, add:

```markdown
### Added

- Discover H1-first documents without title frontmatter across CLI, MCP,
  search, browser, and TUI consumers, and teach generated agent instructions
  to use conditional incident-backed `lessons-*.md` files.

### Fixed

- Preserve absent frontmatter during content-only document updates and
  preserve original CRLF bytes during directory-only moves when neither
  operation requests metadata changes.
```

Keep the wording explicit that this is a compatibility extension; do not claim
upstream parity.

- [ ] **Step 7: Run focused tests and documentation checks**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_agent_instructions.py tests/test_init_project.py \
  tests/test_package_metadata.py -q
git diff --check
```

Expected: tests pass and `git diff --check` prints nothing.

- [ ] **Step 8: Commit guidance and documentation**

```bash
git add src/backlog_py/core/agents.py tests/test_agent_instructions.py \
  docs/integration.md CHANGELOG.md
git commit -m "docs: add incident-backed lesson workflow"
```

### Task 5: Run the complete validation gate

**Files:**

- Verify all modified files; create no new product files.

- [ ] **Step 1: Run the focused regression set**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest \
  tests/test_documents.py tests/test_browser_service.py \
  tests/test_agent_instructions.py tests/test_init_project.py \
  tests/test_package_metadata.py -q
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the full test suite**

```bash
PYTHONPATH=src ../../.venv/bin/python -m pytest -q
```

Expected: 838 baseline tests plus the new regression tests pass. Permit
loopback socket binding in a managed sandbox.

- [ ] **Step 3: Run static security checks**

```bash
../../.venv/bin/python -m bandit -q -r src
```

Expected: exit 0 with no new findings.

- [ ] **Step 4: Build and validate package artifacts**

```bash
../../.venv/bin/python -m build
../../.venv/bin/python -m twine check dist/*
```

Expected: wheel and source distribution build successfully and `twine check`
passes both.

- [ ] **Step 5: Inspect the final diff and worktree**

```bash
git diff --check
git status --short
git log --oneline --decorate -8
```

Expected: no whitespace errors, no uncommitted files, and separate commits for
title resolution, mutation preservation, browser presentation, and lesson
guidance/docs after the committed spec and plan.

- [ ] **Step 6: Perform a final requirement review**

Confirm directly from tests and diff that:

- Frontmatter titles still win.
- Only the first nonblank literal `# ` line can supply a fallback title.
- Reads and directory-only moves preserve CRLF.
- Empty frontmatter is not confused with absent frontmatter.
- Browser `content` remains unchanged while matching title H1 is omitted only
  from `contentHtml`.
- Generated guidance is conditional, evidence-first, theme-agnostic, and says
  not to invent lessons.
- No lesson templates, DoD defaults, append API, dependency, or unrelated
  refactor was added.
