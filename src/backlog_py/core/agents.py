from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backlog_py.core.models import BacklogProject
from backlog_py.core.repository import _atomic_write_text
from backlog_py.security.paths import PathContainmentError, assert_path_within_base


START_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_START -->"
END_MARKER = "<!-- BACKLOG_MD_INSTRUCTIONS_END -->"
AGENT_INSTRUCTION_FILES = (
    Path("AGENTS.md"),
    Path("CLAUDE.md"),
    Path("GEMINI.md"),
    Path(".github/copilot-instructions.md"),
)


class AgentInstructionError(ValueError):
    """Raised when agent instruction files cannot be updated safely."""


@dataclass(frozen=True)
class AgentInstructionUpdate:
    path: Path
    path_relative: str
    created: bool


def update_agent_instruction_files(project: BacklogProject) -> list[AgentInstructionUpdate]:
    """Create or refresh Backlog.md guidance in common agent instruction files."""
    updates: list[AgentInstructionUpdate] = []
    section = _instruction_section(project.config.project_name)
    for relative_path in AGENT_INSTRUCTION_FILES:
        target = _instruction_path(project.root, relative_path)
        previous = target.read_text(encoding="utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, _replace_owned_section(previous, section))
        updates.append(
            AgentInstructionUpdate(
                path=target,
                path_relative=relative_path.as_posix(),
                created=not bool(previous),
            )
        )
    return updates


def _instruction_path(root: Path, relative_path: Path) -> Path:
    try:
        return assert_path_within_base(root, root / relative_path)
    except PathContainmentError as exc:
        raise AgentInstructionError(str(exc)) from exc


def _replace_owned_section(previous: str, section: str) -> str:
    start_index = previous.find(START_MARKER)
    end_index = previous.find(END_MARKER, start_index + len(START_MARKER))
    if start_index != -1 and end_index != -1:
        end_index += len(END_MARKER)
        return f"{previous[:start_index]}{section}{previous[end_index:].lstrip()}"
    if previous.strip():
        return f"{previous.rstrip()}\n\n{section}"
    return section


def _instruction_section(project_name: str) -> str:
    return (
        f"{START_MARKER}\n"
        "## Backlog.md Workflow\n\n"
        f"This project uses Backlog.md task tracking for {project_name}.\n\n"
        "- Before editing project files, find or create a Backlog.md task.\n"
        "- Keep the task status, implementation notes, and final summary current.\n"
        "- Use `backlog-py --cwd <repo> task list --plain` for local CLI task lookup.\n"
        "- Use `backlog-py --cwd <repo> task create \"Title\" --plain` for new tracked work.\n"
        "- MCP agents should read `backlog://docs/task-workflow` before mutating tasks.\n"
        f"{END_MARKER}\n"
    )
