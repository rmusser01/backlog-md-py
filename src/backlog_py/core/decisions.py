from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml

from backlog_py.core.ids import format_numbered_id
from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import _atomic_write_text
from backlog_py.markdown.task_parser import parse_task_markdown
from backlog_py.search.simple import ranked_matches
from backlog_py.security.paths import PathContainmentError, assert_path_within_base


VALID_DECISION_STATUSES = {"proposed", "accepted", "rejected", "superseded"}


class DecisionMutationError(ValueError):
    """Raised when a decision mutation request is invalid or unsafe."""


@dataclass(frozen=True)
class DecisionRecord:
    id: str
    title: str
    date: str
    status: str
    context: str
    decision: str
    consequences: str
    alternatives: str | None
    path: Path
    path_relative: str
    raw_source: str
    frontmatter: dict[str, Any]


class DecisionService:
    def __init__(self, project: BacklogProject) -> None:
        self.project = project
        self.decisions_dir = project.backlog_dir / "decisions"

    def create_decision(self, title: str, *, status: str = "proposed") -> DecisionRecord:
        decision_id = self._next_decision_id()
        frontmatter = {
            "id": decision_id,
            "title": title,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "status": _normalize_decision_status(status),
        }
        target = self._decision_path(decision_id, title)
        source = _render_decision(frontmatter)
        parse_task_markdown(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, source)
        return _load_decision(self.decisions_dir, target)

    def list_decisions(self) -> list[DecisionRecord]:
        if not self.decisions_dir.is_dir():
            return []
        decisions = [
            self._load_decision(path)
            for path in sorted(self.decisions_dir.glob("decision-*.md"))
            if path.name.casefold() != "readme.md"
        ]
        return sorted(decisions, key=_decision_sort_key)

    def search_decisions(self, query: str) -> list[DecisionRecord]:
        return ranked_matches(self.list_decisions(), query, _decision_search_text)

    def view_decision(self, decision_id: str) -> DecisionRecord:
        normalized = _normalize_decision_id(decision_id)
        for decision in self.list_decisions():
            if decision.id.casefold() == normalized.casefold():
                return decision
        raise KeyError(f"Decision not found: {decision_id}")

    def _load_decision(self, path: Path) -> DecisionRecord:
        try:
            assert_path_within_base(self.decisions_dir, path)
        except PathContainmentError as exc:
            raise DecisionMutationError(str(exc)) from exc
        return _load_decision(self.decisions_dir, path)

    def _decision_path(self, decision_id: str, title: str) -> Path:
        filename = f"{decision_id} - {_slug_title(title)}.md"
        try:
            return assert_path_within_base(self.decisions_dir, self.decisions_dir / filename)
        except PathContainmentError as exc:
            raise DecisionMutationError(f"Invalid decision path: {decision_id}") from exc

    def _next_decision_id(self) -> str:
        max_id = 0
        for decision in self.list_decisions():
            match = re.fullmatch(r"decision-(\d+)", decision.id.casefold())
            if match is not None:
                max_id = max(max_id, int(match.group(1)))
        return format_numbered_id("decision-", max_id + 1, self.project.config.zero_padded_ids)


def _load_decision(base: Path, path: Path) -> DecisionRecord:
    raw_source = path.read_text(encoding="utf-8")
    parsed = parse_task_markdown(raw_source)
    frontmatter = dict(parsed.frontmatter)
    body = parsed.body
    return DecisionRecord(
        id=str(frontmatter.get("id") or ""),
        title=str(frontmatter.get("title") or ""),
        date=str(frontmatter.get("date") or ""),
        status=str(frontmatter.get("status") or "proposed"),
        context=_extract_section(body, "Context"),
        decision=_extract_section(body, "Decision"),
        consequences=_extract_section(body, "Consequences"),
        alternatives=_extract_section(body, "Alternatives") or None,
        path=path,
        path_relative=path.relative_to(base).as_posix(),
        raw_source=raw_source,
        frontmatter=frontmatter,
    )


def _render_decision(frontmatter: dict[str, Any]) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    body = "\n## Context\n\n\n## Decision\n\n\n## Consequences\n"
    return f"---\n{yaml_text}\n---\n{body}"


def _normalize_decision_status(status: str) -> str:
    normalized = status.strip().casefold()
    if normalized not in VALID_DECISION_STATUSES:
        values = ", ".join(sorted(VALID_DECISION_STATUSES))
        raise DecisionMutationError(f"Invalid decision status: {status}. Valid values are: {values}")
    return normalized


def _normalize_decision_id(decision_id: str) -> str:
    normalized = decision_id.strip()
    if re.fullmatch(r"\d+", normalized):
        return f"decision-{normalized}"
    return normalized


def _extract_section(body: str, section_name: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(section_name)}\s*\n(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(body)
    return "" if match is None else match.group(1).strip()


def _slug_title(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", title.strip()).strip("-")
    return slug or "Decision"


def _decision_sort_key(decision: DecisionRecord) -> tuple[int, str]:
    match = re.fullmatch(r"decision-(\d+)", decision.id.casefold())
    return (int(match.group(1)) if match is not None else 0, decision.id)


def _decision_search_text(decision: DecisionRecord) -> str:
    fields: Sequence[str] = (
        decision.path_relative,
        decision.id,
        decision.title,
        decision.status,
        decision.context,
        decision.decision,
        decision.consequences,
        decision.alternatives or "",
    )
    return "\n".join(fields)
