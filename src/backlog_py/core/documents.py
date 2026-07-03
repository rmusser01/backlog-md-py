from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml

from backlog_py.core.ids import format_numbered_id
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import _atomic_write_text
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.search.simple import ranked_matches
from backlog_py.security.paths import PathContainmentError, assert_path_within_base


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
        self.docs_dir = project.backlog_dir / "docs"

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
        if not self.docs_dir.is_dir():
            return []
        documents = [self._load_document(path) for path in sorted(self.docs_dir.rglob("*.md"))]
        return sorted(documents, key=lambda document: document.path_relative)

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
        raise KeyError(f"Document not found: {path_or_id}")

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
        if title is not None:
            frontmatter["title"] = title
        for key, value in (metadata or {}).items():
            if value is None:
                frontmatter.pop(key, None)
            else:
                frontmatter[key] = value
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
        _atomic_write_text(target, source)
        if target != document.path:
            document.path.unlink()
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

    def _next_document_id(self) -> str:
        max_id = 0
        for document in self.list_documents():
            if document.id is None:
                continue
            match = re.fullmatch(r"DOC-(\d+)", document.id.upper())
            if match is not None:
                max_id = max(max_id, int(match.group(1)))
        return format_numbered_id("DOC-", max_id + 1, self.project.config.zero_padded_ids)

    def _document_id_exists(self, document_id: str) -> bool:
        normalized_id = document_id.casefold()
        return any(
            document.id is not None and document.id.casefold() == normalized_id
            for document in self.list_documents()
        )


def _load_document(base: Path, path: Path) -> DocumentRecord:
    raw_source = path.read_text(encoding="utf-8")
    parsed = parse_task_markdown(raw_source)
    frontmatter = dict(parsed.frontmatter)
    return DocumentRecord(
        id=None if frontmatter.get("id") is None else str(frontmatter.get("id")),
        title=str(frontmatter.get("title") or ""),
        path=path,
        path_relative=path.relative_to(base).as_posix(),
        content=parsed.body.strip(),
        body_source=parsed.body,
        frontmatter=frontmatter,
        raw_source=raw_source,
    )


def _render_document(frontmatter: dict[str, Any], content: str) -> str:
    return _render_document_body(frontmatter, f"\n{content.strip()}\n")


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
