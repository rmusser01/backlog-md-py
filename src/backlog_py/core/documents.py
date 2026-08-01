from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml
from loguru import logger

from backlog_py.core.ids import format_numbered_id
from backlog_py.core.errors import NotFoundError
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import _atomic_write_text
from backlog_py.markdown.task_parser import parse_task_markdown, salvage_frontmatter_id
from backlog_py.search.simple import ranked_matches
from backlog_py.security.paths import (
    PathContainmentError,
    assert_path_within_base,
    assert_trusted_subpath,
)


class DocumentMutationError(ValueError):
    """Raised when a document mutation request is invalid or unsafe."""


@dataclass(frozen=True)
class DocumentRecord:
    id: str | None
    title: str
    path: Path
    path_relative: str
    content: str
    body_source: str
    frontmatter: dict[str, Any]
    raw_source: str


class DocumentService:
    def __init__(self, project: BacklogProject) -> None:
        self.project = project
        # Validated per level: a repo can ship backlog/docs as a symlink, and a
        # resolved attacker-controlled anchor would pass containment.
        self.docs_dir = assert_trusted_subpath(project.root, project.backlog_dir / "docs")

    def create_document(
        self,
        path: str,
        *,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentRecord:
        target = self._document_path(path)
        if target.exists():
            raise DocumentMutationError(f"Document already exists: {path}")
        frontmatter = dict(metadata or {})
        if frontmatter.get("id") is None:
            frontmatter["id"] = self._next_document_id()
        elif self._document_id_exists(str(frontmatter["id"])):
            raise DocumentMutationError(f"Document id already exists: {frontmatter['id']}")
        frontmatter["title"] = title
        source = _render_document(frontmatter, content)
        parse_task_markdown(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, source)
        return _load_document(self.docs_dir, target)

    def create_document_from_title(
        self,
        title: str,
        *,
        directory: str | None = None,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> DocumentRecord:
        frontmatter = dict(metadata or {})
        document_id = str(frontmatter.get("id") or self._next_document_id())
        frontmatter["id"] = document_id
        path = self._generated_document_path(document_id, title, directory)
        return self.create_document(path, title=title, content=content, metadata=frontmatter)

    def list_documents(self) -> list[DocumentRecord]:
        documents, _ = self._scan_documents()
        return sorted(documents, key=lambda document: document.path_relative)

    def _scan_documents(self) -> tuple[list[DocumentRecord], list[Path]]:
        """Read every document file, returning parsed records and skipped paths.

        Id allocation needs the skipped paths as well as the records: a file that
        cannot be parsed still occupies whatever id it holds.
        """
        if not self.docs_dir.is_dir():
            return ([], [])
        documents: list[DocumentRecord] = []
        unparsed: list[Path] = []
        for path in sorted(self.docs_dir.rglob("*.md")):
            try:
                documents.append(self._load_document(path))
            except DocumentMutationError:
                # Containment failures are security signals, not bad content.
                raise
            except (ValueError, OSError) as exc:
                # A single unparsable file must not disable every document
                # operation; skip it and warn, as the task repository does.
                logger.warning("Skipping unreadable document file {}: {}", path, exc)
                unparsed.append(path)
        return (documents, unparsed)

    def search_documents(self, query: str) -> list[DocumentRecord]:
        return ranked_matches(self.list_documents(), query, _document_search_text)

    def view_document(self, path_or_id: str) -> DocumentRecord:
        path_match = self._try_view_by_path(path_or_id)
        if path_match is not None:
            return path_match
        normalized_id = path_or_id.casefold()
        for document in self.list_documents():
            if document.id is not None and document.id.casefold() == normalized_id:
                return document
        raise NotFoundError(f"Document not found: {path_or_id}")

    def update_document(
        self,
        path_or_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        directory: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentRecord:
        document = self.view_document(path_or_id)
        frontmatter = dict(document.frontmatter)
        metadata_updates = dict(metadata or {})
        if title is not None:
            frontmatter["title"] = title
        for key, value in metadata_updates.items():
            if value is None:
                frontmatter.pop(key, None)
            else:
                frontmatter[key] = value
        has_frontmatter = parse_task_markdown(document.raw_source).raw_frontmatter is not None
        if not has_frontmatter and title is None and not metadata_updates:
            source = document.raw_source if content is None else _render_plain_document(content)
        else:
            source = (
                _render_document_body(frontmatter, document.body_source)
                if content is None
                else _render_document(frontmatter, content)
            )
        parse_task_markdown(source)
        target = document.path if directory is None else self._moved_document_path(document, directory)
        if target != document.path and target.exists():
            raise DocumentMutationError(f"Document already exists: {target.relative_to(self.docs_dir).as_posix()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target == document.path:
            _atomic_write_text(target, source)
        else:
            _move_document(document.path, target, source)
        return _load_document(self.docs_dir, target)

    def _try_view_by_path(self, path_or_id: str) -> DocumentRecord | None:
        path = self._document_path(path_or_id)
        if path.is_file():
            return self._load_document(path)
        return None

    def _load_document(self, path: Path) -> DocumentRecord:
        try:
            assert_path_within_base(self.docs_dir, path)
        except PathContainmentError as exc:
            raise DocumentMutationError(str(exc)) from exc
        return _load_document(self.docs_dir, path)

    def _document_path(self, path: str) -> Path:
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts or relative.name in {"", "."}:
            raise DocumentMutationError(f"Invalid document path: {path}")
        if relative.suffix != ".md":
            relative = relative.with_suffix(".md")
        try:
            return assert_path_within_base(self.docs_dir, self.docs_dir / relative)
        except PathContainmentError as exc:
            raise DocumentMutationError(f"Invalid document path: {path}") from exc

    def _generated_document_path(self, document_id: str, title: str, directory: str | None) -> str:
        relative_directory = self._document_directory(directory)
        filename = f"{document_id.lower()} - {_slug_title(title)}.md"
        if str(relative_directory) == ".":
            return filename
        return (relative_directory / filename).as_posix()

    def _moved_document_path(self, document: DocumentRecord, directory: str) -> Path:
        relative_directory = self._document_directory(directory)
        target = self.docs_dir / relative_directory / document.path.name
        try:
            return assert_path_within_base(self.docs_dir, target)
        except PathContainmentError as exc:
            raise DocumentMutationError(f"Invalid document path: {directory}") from exc

    def _document_directory(self, directory: str | None) -> Path:
        if directory is None or not directory.strip():
            return Path(".")
        relative = Path(directory)
        if relative.is_absolute() or ".." in relative.parts or relative.suffix == ".md":
            raise DocumentMutationError(f"Invalid document path: {directory}")
        try:
            assert_path_within_base(self.docs_dir, self.docs_dir / relative / ".keep")
        except PathContainmentError as exc:
            raise DocumentMutationError(f"Invalid document path: {directory}") from exc
        return relative

    def _reserved_document_ids(self) -> set[str]:
        """Casefolded ids claimed on disk, including ids inside unparsable files.

        ``list_documents()`` skips a file it cannot parse, which hides that file's
        id from allocation. Documents can live at any caller-chosen path, so the
        filename says nothing about the id; the id is instead salvaged straight
        out of the raw frontmatter.
        """
        documents, unparsed = self._scan_documents()
        reserved = {document.id.casefold() for document in documents if document.id}
        for path in unparsed:
            salvaged = salvage_frontmatter_id(path)
            if salvaged is not None:
                reserved.add(salvaged.casefold())
        return reserved

    def _next_document_id(self) -> str:
        max_id = 0
        for document_id in self._reserved_document_ids():
            match = re.fullmatch(r"doc-(\d+)", document_id)
            if match is not None:
                max_id = max(max_id, int(match.group(1)))
        # Filenames are the last resort: a file that neither parses nor exposes a
        # readable id still owns the number in its generated name, and reissuing
        # it would put two files under one id.
        if self.docs_dir.is_dir():
            for path in self.docs_dir.rglob("*.md"):
                match = re.match(r"doc-(\d+)", path.stem.casefold())
                if match is not None:
                    max_id = max(max_id, int(match.group(1)))
        return format_numbered_id("DOC-", max_id + 1, self.project.config.zero_padded_ids)

    def _document_id_exists(self, document_id: str) -> bool:
        return document_id.casefold() in self._reserved_document_ids()


def _move_document(source_path: Path, target: Path, source: str) -> None:
    """Move a document to ``target`` without ever leaving a duplicate-id shadow.

    The updated content is written in place first and then renamed, so an
    interrupted move leaves exactly one intact file rather than the same
    document id in two places.
    """
    _atomic_write_text(source_path, source)
    try:
        os.replace(source_path, target)
        return
    except OSError as exc:
        logger.warning("Atomic move of document {} to {} failed: {}", source_path, target, exc)
    # Cross-device (or otherwise non-atomic) move: write the new location first
    # so content can never be lost, then drop the old file best effort.
    _atomic_write_text(target, source)
    try:
        source_path.unlink()
    except OSError as exc:
        logger.warning("Failed to remove moved document {}: {}", source_path, exc)


def _load_document(base: Path, path: Path) -> DocumentRecord:
    # newline="" keeps original CRLF bytes intact through directory-only moves;
    # utf-8-sig drops a BOM that would otherwise hide the frontmatter.
    with path.open("r", encoding="utf-8-sig", newline="") as source_file:
        raw_source = source_file.read()
    parsed = parse_task_markdown(raw_source)
    frontmatter = dict(parsed.frontmatter)
    title = str(frontmatter.get("title") or "")
    if not title:
        for line in parsed.body.splitlines():
            if not line.strip():
                continue
            if line.startswith("# "):
                title = line[2:].strip()
            break
    return DocumentRecord(
        id=None if frontmatter.get("id") is None else str(frontmatter.get("id")),
        title=title,
        path=path,
        path_relative=path.relative_to(base).as_posix(),
        content=parsed.body.strip(),
        body_source=parsed.body,
        frontmatter=frontmatter,
        raw_source=raw_source,
    )


def _render_document(frontmatter: dict[str, Any], content: str) -> str:
    return _render_document_body(frontmatter, f"\n{content.strip()}\n")


def _render_plain_document(content: str) -> str:
    return f"{content.strip()}\n"


def _render_document_body(frontmatter: dict[str, Any], body_source: str) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{yaml_text}\n---\n{body_source}"


def _slug_title(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", title.strip()).strip("-")
    return slug or "Document"


def _document_search_text(document: DocumentRecord) -> str:
    fields: Sequence[str] = (
        document.path_relative,
        document.id or "",
        document.title,
        document.content,
    )
    return "\n".join(fields)
