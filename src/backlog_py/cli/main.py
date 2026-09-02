from __future__ import annotations

import json
import os
import shlex
import subprocess  # nosec B404
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Callable, TypeVar

import click

from backlog_py import __version__
from backlog_py.browser.service import run_browser_service_foreground
from backlog_py.cli.completion import CompletionInstallError, install_completion
from backlog_py.compat.inventory import load_builtin_inventory
from backlog_py.compat.report import build_compatibility_report, build_release_evidence_manifest
from backlog_py.core.agents import AgentInstructionError, AgentInstructionUpdate, update_agent_instruction_files
from backlog_py.core.board_export import export_board_to_file, update_readme_with_board
from backlog_py.core.decisions import DecisionMutationError, DecisionRecord, DecisionService
from backlog_py.core.documents import DocumentMutationError, DocumentRecord, DocumentService
from backlog_py.core.drafts import DraftService
from backlog_py.core.init import InitProjectError, InitProjectResult, init_project
from backlog_py.core.milestones import MilestoneMutationError, MilestoneRecord, MilestoneService
from backlog_py.core import editing
from backlog_py.core.editing import EditorAbort, edit_via_scratch_copy
from backlog_py.core.models import BacklogProject
from backlog_py.core.errors import NotFoundError
# Hosts the daemon may bind without an explicit remote-exposure opt-in. Sourced
# from the server so the CLI cannot accept a spelling the server then rejects
# with a raw ValueError.
from backlog_py.mcp.http_server import LOOPBACK_HOSTS as _LOOPBACK_HOSTS
from backlog_py.core.repository import (
    MutableRepository,
    ReadOnlyRepository,
    TaskMutationError,
    TaskRecord,
    normalize_ordinal_value,
)
from backlog_py.daemon.lifecycle import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DaemonNotRunningError,
    DaemonStartError,
    daemon_ensure,
    daemon_start,
    daemon_status,
    daemon_stop,
)
from backlog_py.integration.legacy_shim import install_legacy_mcp_shim
from backlog_py.orchestration import (
    OrchestrationIdempotencyConflict,
    OrchestrationMutationResult,
    OrchestrationQueueItem,
    OrchestrationService,
    OrchestrationStateUpdate,
    RunHistoryParseError,
    TaskSplitItem,
    ValidationIssue,
    parse_run_history,
)
from backlog_py.orchestration.models import OrchestrationError
from backlog_py.storage.config import (
    get_config_value,
    get_definition_of_done_defaults,
    replace_definition_of_done_defaults,
    set_config_value,
)
from backlog_py.runtime.locks import LockTimeoutError, with_init_lock, with_project_write_lock
from backlog_py.runtime.state import RuntimeRecord, runtime_status
from backlog_py.security.paths import PathContainmentError
from backlog_py.storage.project import discover_project

T = TypeVar("T")


# Domain errors that should surface as a clean "Error: ..." message and a
# non-zero exit code rather than a raw Python traceback.
_CLI_DOMAIN_ERRORS = (
    TaskMutationError,
    MilestoneMutationError,
    DecisionMutationError,
    DocumentMutationError,
    InitProjectError,
    OrchestrationError,
    AgentInstructionError,
    CompletionInstallError,
    DaemonNotRunningError,
    DaemonStartError,
    PathContainmentError,
    LockTimeoutError,
    NotFoundError,
)


# Wording only. An editor that reports "done" without changing anything and
# returned this fast almost certainly did not wait for the user — GUI editors
# return as soon as they hand the file to an already-running instance — so the
# message can say so. Nothing about *keeping* the user's copy depends on this
# number: an unchanged copy is always kept, because no threshold can tell a
# still-open editor from a closed one.
#
# Deliberately duplicated from backlog_py.tui.app: the TUI cannot import this
# module at import time (it would drag the CLI into the optional Textual
# dependency graph), and the CLI cannot import the TUI for the same reason in
# reverse. Hoisting both onto a shared editor module is the right follow-up.
# Re-exported from the shared editor flow; kept so existing references resolve.
NON_BLOCKING_EDITOR_SECONDS = editing.NON_BLOCKING_EDITOR_SECONDS


class _EditorConflictError(click.ClickException):
    """An edit could not be applied and the user's bytes were kept.

    A ClickException so it renders as a clean "Error: ..." line naming the
    preserved copy rather than a traceback.
    """


_TASK_USAGE = (
    "Usage: task <task-id> | task list | task create TITLE | "
    "task edit TASK_ID | task archive TASK_ID | task demote TASK_ID"
)



def _clean_error_message(exc: Exception) -> str:
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    message = str(exc).strip()
    return message or exc.__class__.__name__


def _validate_bind_host(ctx: click.Context, param: click.Parameter, value: str | None) -> str | None:
    """Reject non-loopback bind hosts unless --allow-remote was passed.

    The daemon serves the full MCP JSON-RPC surface, so binding 0.0.0.0 hands
    project write access to anyone on the network. --allow-remote is eager, so
    it is always parsed before this callback runs.
    """
    if value is None:
        return value
    normalized = value.strip()
    if ctx.params.get("allow_remote"):
        return normalized
    # Return the spelling the server accepts, not the user's. Accepting LOCALHOST
    # or [::1] here and passing it through unchanged made the server reject it
    # with a bare ValueError after the CLI had already approved it.
    canonical = normalized.casefold().strip("[]")
    for allowed in _LOOPBACK_HOSTS:
        if canonical == allowed.casefold().strip("[]"):
            return allowed
    raise click.BadParameter(
        f"{value} is not a loopback host. The daemon exposes the MCP JSON-RPC surface, so it must bind "
        "127.0.0.1, localhost, or ::1. Pass --allow-remote to bind a non-loopback host deliberately.",
        ctx=ctx,
        param=param,
    )


class _BacklogGroup(click.Group):
    """Top-level group that maps known domain errors to clean CLI errors."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except click.ClickException:
            raise
        except _CLI_DOMAIN_ERRORS as exc:
            raise click.ClickException(_clean_error_message(exc)) from exc


@click.group(cls=_BacklogGroup)
@click.option("--cwd", type=click.Path(path_type=Path), default=None, help="Backlog project directory.")
@click.version_option(__version__, prog_name="backlog-py")
@click.pass_context
def main(ctx: click.Context, cwd: Path | None) -> None:
    """Python compatibility clone of Backlog.md."""
    ctx.obj = {"cwd": cwd}


@main.command("init")
@click.argument("project_name", required=False)
@click.option("--defaults", is_flag=True, help="Use non-interactive default settings.")
@click.option("--no-git", is_flag=True, help="Initialize a filesystem-only project with git-dependent settings disabled.")
@click.option("--backlog-dir", default="backlog", help="Project-relative backlog directory.")
@click.option("--task-prefix", default="task", help="Task ID prefix to set during first initialization.")
@click.option(
    "--config-location",
    type=click.Choice(["local", "root"], case_sensitive=False),
    default="local",
    help="Write config under the backlog folder or project root.",
)
@click.option("--agent-instructions", is_flag=True, help="Create common agent instruction files.")
@click.pass_context
def init_command(
    ctx: click.Context,
    project_name: str | None,
    defaults: bool,
    no_git: bool,
    backlog_dir: str,
    task_prefix: str,
    config_location: str,
    agent_instructions: bool,
) -> None:
    """Initialize a Backlog.md project, interactively when a terminal is available."""
    if not defaults:
        if not _stdin_is_interactive():
            raise click.ClickException(
                "Interactive init requires a terminal. Pass --defaults to use non-interactive defaults."
            )
        project_name, backlog_dir, task_prefix, config_location, no_git, agent_instructions = (
            _prompt_init_options(
                ctx,
                project_name=project_name,
                backlog_dir=backlog_dir,
                task_prefix=task_prefix,
                config_location=config_location,
                no_git=no_git,
                agent_instructions=agent_instructions,
            )
        )
    try:
        result, instruction_updates = _locked_init(
            ctx,
            "init_project",
            lambda: _initialize_project(
                ctx,
                project_name=project_name,
                backlog_dir=backlog_dir,
                task_prefix=task_prefix,
                config_location=config_location,
                no_git=no_git,
                agent_instructions=agent_instructions,
            ),
        )
    except InitProjectError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Initialized Backlog.md project at {result.project.backlog_dir}")
    if not result.config_created:
        click.echo(f"Preserved existing config at {result.project.config_path}")
    for update in instruction_updates:
        click.echo(f"Updated {update.path_relative}")


def _prompt_init_options(
    ctx: click.Context,
    *,
    project_name: str | None,
    backlog_dir: str,
    task_prefix: str,
    config_location: str,
    no_git: bool,
    agent_instructions: bool,
) -> tuple[str, str, str, str, bool, bool]:
    """Collect init settings interactively; each parsed flag seeds its prompt default.

    Runs before the init lock is acquired so user think-time never holds the lock.
    """
    click.echo("Interactive Backlog.md project setup")
    name = click.prompt("Project name", default=project_name or _cwd(ctx).resolve().name).strip()
    # Whitespace-only answers fall back to the default so an accidental space
    # does not turn Path("") into the project root as the backlog directory.
    directory = click.prompt("Backlog directory", default=backlog_dir).strip() or backlog_dir
    while True:
        prefix = click.prompt("Task ID prefix", default=task_prefix).strip()
        if prefix.isalpha():
            break
        click.echo("Task ID prefix must be non-empty and contain only letters.")
    location = click.prompt(
        "Config location",
        default=config_location,
        type=click.Choice(["local", "root"], case_sensitive=False),
    )
    disable_git = click.confirm("Disable git integration?", default=no_git)
    instructions = click.confirm("Create agent instruction files?", default=agent_instructions)
    return name, directory, prefix, location, disable_git, instructions


def _reject_edit_only_create_flags(
    *,
    title: str | None,
    append_plan: tuple[str, ...],
    clear_plan: bool,
    append_notes: str | None,
    append_final_summary: tuple[str, ...],
    clear_final_summary: bool,
    check_ac: tuple[int, ...],
    check_dod: tuple[int, ...],
    uncheck_ac: tuple[int, ...],
    uncheck_dod: tuple[int, ...],
    remove_ac: tuple[int, ...],
    remove_dod: tuple[int, ...],
) -> None:
    """Reject flags that only apply to 'task edit' so they never silently no-op on create."""
    offenders: list[str] = []
    if title is not None:
        offenders.append("--title (use the TITLE argument)")
    if append_notes is not None:
        offenders.append("--append-notes")
    if clear_plan:
        offenders.append("--clear-plan")
    if clear_final_summary:
        offenders.append("--clear-final-summary")
    for name, value in (
        ("--append-plan", append_plan),
        ("--append-final-summary", append_final_summary),
        ("--check-ac", check_ac),
        ("--check-dod", check_dod),
        ("--uncheck-ac", uncheck_ac),
        ("--uncheck-dod", uncheck_dod),
        ("--remove-ac", remove_ac),
        ("--remove-dod", remove_dod),
    ):
        if value:
            offenders.append(name)
    if offenders:
        raise click.UsageError(
            "These options apply to 'task edit', not 'task create': " + ", ".join(offenders)
        )


def _reject_unsupported_edit_flags(*, parent_task_id: str | None, task_id: str | None, draft: bool) -> None:
    """Reject flags 'task edit' cannot honor so they never silently no-op."""
    offenders: list[str] = []
    if parent_task_id is not None:
        offenders.append("--parent")
    if task_id is not None:
        offenders.append("--id")
    if draft:
        offenders.append("--draft")
    if offenders:
        raise click.UsageError(
            "These options are not supported by 'task edit': " + ", ".join(offenders)
        )


@main.command("task")
@click.argument("args", nargs=-1)
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.option("--id", "task_id", default=None, help="Task id for task creation.")
@click.option("--draft", is_flag=True, help="Create the task as a draft.")
@click.option("--title", default=None, help="Replacement task title for task edit.")
@click.option("-s", "--status", default=None, help="Task status for create/edit/list.")
@click.option("-d", "--desc", "--description", "description", default=None, help="Description for task creation.")
@click.option("--plan", default=None, help="Implementation plan for task create/edit.")
@click.option("--append-plan", multiple=True, help="Append text to the implementation plan.")
@click.option("--clear-plan", is_flag=True, help="Clear the implementation plan section.")
@click.option("--notes", default=None, help="Implementation notes for task create/edit.")
@click.option(
    "--ac",
    "--acceptance-criteria",
    "acceptance_criteria",
    multiple=True,
    help="Acceptance criterion for task creation.",
)
@click.option("--definition-of-done", multiple=True, help="Definition of Done item for task creation.")
@click.option(
    "--dod",
    "--definition-of-done-add",
    "definition_of_done_add",
    multiple=True,
    help="Additional Definition of Done item for task creation.",
)
@click.option(
    "--disable-definition-of-done-defaults",
    "--no-dod-defaults",
    "disable_definition_of_done_defaults",
    is_flag=True,
    help="Do not inherit project Definition of Done defaults.",
)
@click.option("--dep", "--dependency", "dependencies", multiple=True, help="Task dependency id for task create/edit.")
@click.option("-a", "--assignee", "assignees", multiple=True, help="Task assignee for create/edit/list.")
@click.option("-l", "--label", "labels", multiple=True, help="Task label for create/edit/list.")
@click.option("--priority", default=None, help="Task priority for create/edit/list.")
@click.option("-m", "--milestone", default=None, help="Task milestone for create/edit/list.")
@click.option("--ordinal", default=None, help="Set task ordinal for custom ordering.")
@click.option("-p", "--parent", "parent_task_id", default=None, help="Parent task id for create/list.")
@click.option("--clear-milestone", is_flag=True, help="Clear the task milestone on edit.")
@click.option("--ref", "references", multiple=True, help="Task reference URL or file path for create/edit.")
@click.option("--doc", "documentation", multiple=True, help="Task documentation URL or file path for create/edit.")
@click.option("--modified-file", "modified_files", multiple=True, help="Modified file path for create/edit.")
@click.option("--append-notes", default=None, help="Append text to implementation notes.")
@click.option("--check-ac", multiple=True, type=int, help="Mark acceptance criteria index complete.")
@click.option("--check-dod", multiple=True, type=int, help="Mark Definition of Done index complete.")
@click.option("--uncheck-ac", multiple=True, type=int, help="Mark acceptance criteria index incomplete.")
@click.option("--uncheck-dod", multiple=True, type=int, help="Mark Definition of Done index incomplete.")
@click.option("--remove-ac", multiple=True, type=int, help="Remove acceptance criteria by index.")
@click.option("--remove-dod", multiple=True, type=int, help="Remove Definition of Done items by index.")
@click.option("--final-summary", default=None, help="Replace the final summary section.")
@click.option("--append-final-summary", multiple=True, help="Append text to the final summary section.")
@click.option("--clear-final-summary", is_flag=True, help="Clear the final summary section.")
@click.pass_context
def task_command(
    ctx: click.Context,
    args: tuple[str, ...],
    plain: bool,
    task_id: str | None,
    draft: bool,
    title: str | None,
    status: str | None,
    description: str | None,
    plan: str | None,
    append_plan: tuple[str, ...],
    clear_plan: bool,
    notes: str | None,
    acceptance_criteria: tuple[str, ...],
    definition_of_done: tuple[str, ...],
    definition_of_done_add: tuple[str, ...],
    disable_definition_of_done_defaults: bool,
    dependencies: tuple[str, ...],
    assignees: tuple[str, ...],
    labels: tuple[str, ...],
    priority: str | None,
    milestone: str | None,
    ordinal: str | None,
    parent_task_id: str | None,
    clear_milestone: bool,
    references: tuple[str, ...],
    documentation: tuple[str, ...],
    modified_files: tuple[str, ...],
    append_notes: str | None,
    check_ac: tuple[int, ...],
    check_dod: tuple[int, ...],
    uncheck_ac: tuple[int, ...],
    uncheck_dod: tuple[int, ...],
    remove_ac: tuple[int, ...],
    remove_dod: tuple[int, ...],
    final_summary: str | None,
    append_final_summary: tuple[str, ...],
    clear_final_summary: bool,
) -> None:
    """View tasks."""
    if args and args[0] == "archive":
        if len(args) != 2:
            raise click.UsageError("Usage: task archive TASK_ID")
        task_record = _locked_write(ctx, "task_archive", lambda: _mutable_repository(ctx).archive_task(args[1]))
        click.echo(f"{_format_task_line(task_record, plain=plain)} archived")
        return
    if args and args[0] == "demote":
        if len(args) != 2:
            raise click.UsageError("Usage: task demote TASK_ID")
        draft_record = _locked_write(ctx, "draft_demote", lambda: _draft_service(ctx).demote_task(args[1]))
        if plain:
            click.echo(_format_task_line(draft_record, plain=True))
        else:
            click.echo(f"Demoted task {args[1]} to {draft_record.id}")
        return
    if args and args[0] == "create":
        if len(args) != 2:
            raise click.UsageError("Usage: task create TITLE")
        _reject_edit_only_create_flags(
            title=title,
            append_plan=append_plan,
            clear_plan=clear_plan,
            append_notes=append_notes,
            append_final_summary=append_final_summary,
            clear_final_summary=clear_final_summary,
            check_ac=check_ac,
            check_dod=check_dod,
            uncheck_ac=uncheck_ac,
            uncheck_dod=uncheck_dod,
            remove_ac=remove_ac,
            remove_dod=remove_dod,
        )
        if draft and status is not None:
            raise click.UsageError("Drafts are always created with status 'Draft'; --status is not allowed with --draft.")
        if clear_milestone:
            raise click.UsageError("Cannot use --clear-milestone with task create.")
        if draft:
            task_record = _locked_write(
                ctx,
                "draft_create",
                lambda: _draft_service(ctx).create_draft(
                    title=args[1],
                    draft_id=task_id,
                    description=description or "",
                    plan=plan or "",
                    notes=notes or "",
                    final_summary=final_summary or "",
                    acceptance_criteria=acceptance_criteria,
                    definition_of_done=definition_of_done or None,
                    definition_of_done_add=definition_of_done_add,
                    disable_definition_of_done_defaults=disable_definition_of_done_defaults,
                    dependencies=dependencies,
                    assignees=assignees,
                    labels=labels,
                    priority=priority,
                    milestone=milestone,
                    ordinal=_parse_ordinal(ordinal),
                    parent_task_id=parent_task_id,
                    references=references,
                    documentation=documentation,
                    modified_files=modified_files,
                ),
            )
            click.echo(_format_task_line(task_record, plain=plain))
            return
        task_record = _locked_write(
            ctx,
            "task_create",
            lambda: _mutable_repository(ctx).create_task(
                title=args[1],
                task_id=task_id,
                status=status,
                description=description or "",
                plan=plan or "",
                notes=notes or "",
                final_summary=final_summary or "",
                acceptance_criteria=acceptance_criteria,
                definition_of_done=definition_of_done or None,
                definition_of_done_add=definition_of_done_add,
                disable_definition_of_done_defaults=disable_definition_of_done_defaults,
                dependencies=dependencies,
                assignees=assignees,
                labels=labels,
                priority=priority,
                milestone=milestone,
                ordinal=_parse_ordinal(ordinal),
                parent_task_id=parent_task_id,
                references=references,
                documentation=documentation,
                modified_files=modified_files,
            ),
        )
        click.echo(_format_task_line(task_record, plain=plain))
        return
    if args and args[0] == "edit":
        if len(args) != 2:
            raise click.UsageError("Usage: task edit TASK_ID")
        _reject_unsupported_edit_flags(parent_task_id=parent_task_id, task_id=task_id, draft=draft)
        if milestone is not None and clear_milestone:
            raise click.UsageError("Cannot use --milestone and --clear-milestone together.")
        task_record = _locked_write(
            ctx,
            "task_edit",
            lambda: _mutable_repository(ctx).edit_task(
                args[1],
                title=title,
                description=description,
                plan=plan,
                append_plan=append_plan,
                clear_plan=clear_plan,
                notes=notes,
                status=status,
                append_notes=append_notes,
                acceptance_criteria_add=acceptance_criteria,
                definition_of_done_add=definition_of_done_add,
                check_ac=check_ac,
                check_dod=check_dod,
                uncheck_ac=uncheck_ac,
                uncheck_dod=uncheck_dod,
                remove_ac=remove_ac,
                remove_dod=remove_dod,
                dependencies=dependencies if dependencies else None,
                assignees=assignees if assignees else None,
                labels=labels if labels else None,
                priority=priority,
                milestone=milestone,
                ordinal=_parse_ordinal(ordinal),
                clear_milestone=clear_milestone,
                references=references if references else None,
                documentation=documentation if documentation else None,
                modified_files=modified_files if modified_files else None,
                final_summary=final_summary,
                append_final_summary=append_final_summary,
                clear_final_summary=clear_final_summary,
            ),
        )
        click.echo(_format_task_line(task_record, plain=plain))
        return
    if args and args[0] == "list":
        if len(args) != 1:
            raise click.UsageError(f"Unexpected extra arguments for 'task list': {' '.join(args[1:])}")
        for task_record in _repository(ctx).list_tasks(
            status=status,
            assignee=assignees,
            labels=labels,
            priority=_priority_filter(priority),
            milestone=milestone,
            parent_task_id=parent_task_id,
        ):
            click.echo(_format_task_line(task_record, plain=plain))
        return
    if not args:
        raise click.UsageError(f"Missing task id. {_TASK_USAGE}")
    if len(args) != 1:
        raise click.UsageError(f"Unknown task command: '{args[0]}'. {_TASK_USAGE}")
    task_id = args[0]
    task_record = _repository(ctx).get_task(task_id)
    if plain:
        click.echo(_format_task_detail(_project(ctx), task_record, plain=True))
        return
    _run_interactive_task_view(ctx, task_record)


@main.command("search")
@click.argument("query")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.option("--status", default=None, help="Filter matching tasks by status.")
@click.option("--priority", default=None, help="Filter matching tasks by priority.")
@click.option("--modified-file", "modified_files", multiple=True, help="Filter by modified file path substring.")
@click.option("--type", "result_types", multiple=True, help="Limit results to type: task, document, decision.")
@click.option("--limit", type=int, default=None, help="Limit the number of search results.")
@click.pass_context
def search_command(
    ctx: click.Context,
    query: str,
    plain: bool,
    status: str | None,
    priority: str | None,
    modified_files: tuple[str, ...],
    result_types: tuple[str, ...],
    limit: int | None,
) -> None:
    """Search tasks, documents, and decisions."""
    if plain:
        for line in _search_output_lines(
            ctx,
            query,
            plain=True,
            status=status,
            priority=priority,
            modified_files=modified_files,
            result_types=result_types,
            limit=limit,
        ):
            click.echo(line)
        return
    _run_interactive_search_view(
        ctx,
        query,
        status=status,
        priority=priority,
        modified_files=modified_files,
        result_types=result_types,
        limit=limit,
    )


def _search_output_lines(
    ctx: click.Context,
    query: str,
    *,
    plain: bool,
    status: str | None,
    priority: str | None,
    modified_files: tuple[str, ...],
    result_types: tuple[str, ...],
    limit: int | None,
) -> list[str]:
    selected_types = _search_result_types(result_types)
    task_filters_present = status is not None or priority is not None or bool(modified_files)
    if selected_types is None:
        selected_types = {"task"} if task_filters_present else {"task", "document", "decision"}

    lines: list[str] = []
    if "task" in selected_types:
        task_results = _repository(ctx).search_tasks(
            query,
            status=status,
            priority=_priority_filter(priority),
            modified_files=modified_files,
        )
        lines.extend(_format_task_line(task_record, plain=plain) for task_record in task_results)
    if "document" in selected_types:
        lines.extend(
            _format_document_line(document, plain=plain)
            for document in _document_service(ctx).search_documents(query)
        )
    if "decision" in selected_types:
        lines.extend(
            _format_decision_line(decision, plain=plain)
            for decision in _decision_service(ctx).search_decisions(query)
        )
    if limit is not None:
        lines = lines[: max(limit, 0)]
    return lines


def _run_interactive_search_view(
    ctx: click.Context,
    query: str,
    *,
    status: str | None,
    priority: str | None,
    modified_files: tuple[str, ...],
    result_types: tuple[str, ...],
    limit: int | None,
) -> None:
    _echo_search_panel(
        "Search results for",
        query,
        _search_output_lines(
            ctx,
            query,
            plain=False,
            status=status,
            priority=priority,
            modified_files=modified_files,
            result_types=result_types,
            limit=limit,
        ),
    )
    if not _stdin_is_interactive():
        return
    key = _read_interactive_key()
    if key == "s":
        refined_status = _optional_prompt("Filter status", status)
        _echo_filtered_search_panel(
            ctx,
            query,
            f"Filter status: {refined_status or '(cleared)'}",
            status=refined_status,
            priority=priority,
            modified_files=modified_files,
            result_types=result_types,
            limit=limit,
        )
    elif key == "p":
        refined_priority = _optional_prompt("Filter priority", priority)
        _echo_filtered_search_panel(
            ctx,
            query,
            f"Filter priority: {refined_priority or '(cleared)'}",
            status=status,
            priority=refined_priority,
            modified_files=modified_files,
            result_types=result_types,
            limit=limit,
        )
    elif key == "t":
        refined_type = _optional_prompt("Filter type", ",".join(result_types))
        _echo_filtered_search_panel(
            ctx,
            query,
            f"Filter type: {refined_type or '(cleared)'}",
            status=status,
            priority=priority,
            modified_files=modified_files,
            result_types=(refined_type,) if refined_type is not None else (),
            limit=limit,
        )
    elif key == "m":
        refined_file = _optional_prompt("Filter modified file", ",".join(modified_files))
        _echo_filtered_search_panel(
            ctx,
            query,
            f"Filter modified file: {refined_file or '(cleared)'}",
            status=status,
            priority=priority,
            modified_files=(refined_file,) if refined_file is not None else (),
            result_types=result_types,
            limit=limit,
        )


def _echo_filtered_search_panel(
    ctx: click.Context,
    query: str,
    filter_label: str,
    *,
    status: str | None,
    priority: str | None,
    modified_files: tuple[str, ...],
    result_types: tuple[str, ...],
    limit: int | None,
) -> None:
    click.echo("")
    click.echo(filter_label)
    _echo_search_panel(
        "Filtered results",
        query,
        _search_output_lines(
            ctx,
            query,
            plain=False,
            status=status,
            priority=priority,
            modified_files=modified_files,
            result_types=result_types,
            limit=limit,
        ),
    )


def _echo_search_panel(heading: str, query: str, lines: list[str]) -> None:
    click.echo(f"{heading}: {query}")
    if lines:
        for line in lines:
            click.echo(line)
    else:
        click.echo("(no results)")
    click.echo("Actions: [S]tatus  [P]riority  [T]ype  [M]odified file  [Q]uit")


def _optional_prompt(label: str, current: str | None) -> str | None:
    value = click.prompt(label, default=current or "", show_default=bool(current)).strip()
    return value or None


@main.command("board")
@click.argument("args", nargs=-1)
@click.option("--force", is_flag=True, help="Overwrite an existing board export file.")
@click.option("--readme", is_flag=True, help="Export the board into README.md markers.")
@click.option("--export-version", default=None, help="Version string to include in README board exports.")
@click.pass_context
def board_command(
    ctx: click.Context,
    args: tuple[str, ...],
    force: bool,
    readme: bool,
    export_version: str | None,
) -> None:
    """Print task board grouped by status."""
    if args and args[0] == "export":
        if len(args) > 2:
            raise click.UsageError("Usage: board export [filename]")
        if readme:
            _locked_write(
                ctx,
                "board_export_readme",
                lambda: update_readme_with_board(
                    _project(ctx),
                    _repository(ctx).list_tasks(),
                    version=export_version or __version__,
                ),
            )
            click.echo("Updated README.md with Kanban board.")
            return
        output_file = args[1] if len(args) == 2 else "Backlog.md"
        output_path = _project(ctx).root / output_file
        if output_path.exists() and not force:
            if not click.confirm(f'File "{output_path}" already exists. Overwrite?', default=False):
                click.echo("Export cancelled.")
                return
        target = _locked_write(
            ctx,
            "board_export_file",
            lambda: export_board_to_file(_project(ctx), _repository(ctx).list_tasks(), output_file),
        )
        click.echo(f"Exported board to {target}")
        return
    if args and args != ("view",):
        raise click.UsageError("Usage: board [view] | board export [filename]")
    _run_interactive_board_view(ctx)


@main.command("tui")
@click.pass_context
def tui_command(ctx: click.Context) -> None:
    """Launch the optional Textual board."""
    runner = _load_tui_runner()
    runner(_project(ctx))


def _load_tui_runner() -> Callable[[BacklogProject], None]:
    try:
        from backlog_py.tui import app as tui_app
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise click.ClickException("Install with backlog-md-py[tui] to use the Textual TUI.") from exc
        raise
    except RuntimeError as exc:
        if exc.__class__.__name__ == "TuiDependencyError":
            raise click.ClickException(str(exc)) from exc
        raise

    def runner(project: BacklogProject) -> None:
        try:
            tui_app.run_tui_app(project)
        except tui_app.TuiDependencyError as exc:
            raise click.ClickException(str(exc)) from exc
        except RuntimeError as exc:
            if str(exc) == "Textual TUI app is not implemented yet.":
                raise click.ClickException(str(exc)) from exc
            raise

    return runner


def _run_interactive_board_view(ctx: click.Context) -> None:
    interactive = _stdin_is_interactive()
    _echo_board(_repository(ctx).board(), show_actions=interactive)
    if not interactive:
        return
    key = _read_interactive_key()
    if key == "m":
        _move_board_task(ctx)
    elif key == "v":
        _view_board_task(ctx)
    elif key == "e":
        _edit_board_task(ctx)


def _echo_board(board: dict[str, list[TaskRecord]], *, show_actions: bool = False) -> None:
    for status, tasks in board.items():
        click.echo(f"{_style_status(status)}:")
        for task_record in tasks:
            click.echo(f"  {_format_board_task_line(task_record)}")
    if show_actions:
        click.echo("Actions: [V]iew task  [E]dit task  [M]ove task  [Q]uit")


def _move_board_task(ctx: click.Context) -> None:
    task_id = click.prompt("Task id").strip()
    status = click.prompt("Status").strip()
    if not task_id:
        raise click.ClickException("Task id is required.")
    if not status:
        raise click.ClickException("Status is required.")
    task_record = _locked_write(
        ctx,
        "board_move_task",
        lambda: _mutable_repository(ctx).edit_task(task_id, status=status),
    )
    click.echo(f"Moved {task_record.id} to {task_record.status}")


def _view_board_task(ctx: click.Context) -> None:
    task_id = click.prompt("Task id").strip()
    if not task_id:
        raise click.ClickException("Task id is required.")
    click.echo(_format_interactive_task_detail(_project(ctx), _repository(ctx).get_task(task_id)))


def _edit_board_task(ctx: click.Context) -> None:
    task_id = click.prompt("Task id").strip()
    if not task_id:
        raise click.ClickException("Task id is required.")
    _edit_task_in_configured_editor(ctx, _repository(ctx).get_task(task_id))


@main.command("overview")
@click.pass_context
def overview_command(ctx: click.Context) -> None:
    """Print a deterministic project summary."""
    project = _project(ctx)
    repository = _repository(ctx)
    board = repository.board()
    completed_tasks = repository.list_completed_tasks()
    completed_count = len(completed_tasks)

    if _stdin_is_interactive():
        _echo_overview_dashboard(project, board, completed_tasks)
        _read_interactive_key()
        return

    _echo_overview_plain(project, board, completed_count)


def _echo_overview_plain(
    project: BacklogProject,
    board: dict[str, list[TaskRecord]],
    completed_count: int,
) -> None:
    active_count = sum(len(tasks) for tasks in board.values())

    click.echo(f"Project: {project.config.project_name}")
    click.echo(f"Active tasks: {active_count}")
    click.echo(f"Completed tasks: {completed_count}")
    click.echo(f"Total tasks: {active_count + completed_count}")
    click.echo("")
    click.echo("Statuses:")
    for status, tasks in board.items():
        click.echo(f"  {status}: {len(tasks)}")


def _echo_overview_dashboard(
    project: BacklogProject,
    board: dict[str, list[TaskRecord]],
    completed_tasks: list[TaskRecord],
) -> None:
    active_tasks = [task_record for tasks in board.values() for task_record in tasks]
    total_count = len(active_tasks) + len(completed_tasks)
    completion_percent = round((len(completed_tasks) / total_count) * 100) if total_count else 0
    priority_counts = _overview_priority_counts(active_tasks)
    blocked_count = sum(
        1
        for task_record in active_tasks
        if _frontmatter_string_list(task_record, "dependencies")
    )

    click.echo(click.style(f"{project.config.project_name} - Project Overview", fg="cyan", bold=True))
    click.echo("")
    click.echo("Status Overview")
    for status, tasks in board.items():
        click.echo(f"  {status}: {len(tasks)} tasks")
    click.echo(f"  Completed: {len(completed_tasks)} tasks")
    click.echo(f"  Total Tasks: {total_count}")
    click.echo(f"  Completion: {completion_percent}%")
    click.echo("")
    click.echo("Priority Breakdown")
    if priority_counts:
        for priority in ("high", "medium", "low", "none"):
            count = priority_counts.get(priority, 0)
            if count:
                click.echo(f"  {_overview_priority_label(priority)}: {count} tasks")
    else:
        click.echo("  No active tasks")
    click.echo("")
    click.echo("Recent Activity")
    for task_record in active_tasks[:5]:
        click.echo(f"  {task_record.id}: {task_record.title}")
    if not active_tasks:
        click.echo("  No active tasks")
    click.echo("")
    click.echo("Project Health")
    click.echo(f"  Blocked Tasks: {blocked_count}")
    if blocked_count == 0:
        click.echo("  No blocked tasks")
    click.echo("")
    click.echo("Actions: [Q]uit")


def _overview_priority_counts(tasks: list[TaskRecord]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for task_record in tasks:
        priority = str(task_record.parsed.frontmatter.get("priority") or "none").strip().casefold()
        counts[priority or "none"] += 1
    return counts


def _overview_priority_label(priority: str) -> str:
    if priority == "none":
        return "None"
    return priority.title()


@main.command("browser")
@click.option("--port", default=None, type=click.IntRange(1, 65535), help="Browser service port.")
@click.option("--no-open", is_flag=True, help="Do not open the browser after starting the service.")
@click.pass_context
def browser_command(ctx: click.Context, port: int | None, no_open: bool) -> None:
    """Run the loopback browser board service."""
    project = _project(ctx)
    selected_port = port if port is not None else project.config.default_port
    open_browser = project.config.auto_open_browser and not no_open
    run_browser_service_foreground(
        project,
        host="127.0.0.1",
        port=selected_port,
        open_browser=open_browser,
    )


@main.command("cleanup")
@click.option("--dry-run", is_flag=True, help="List the Done tasks that would be moved without moving them.")
@click.option("-y", "--yes", is_flag=True, help="Skip the interactive confirmation prompt.")
@click.pass_context
def cleanup_command(ctx: click.Context, dry_run: bool, yes: bool) -> None:
    """Move active Done tasks into backlog/completed."""
    prompted = False
    if dry_run or (not yes and _stdin_is_interactive()):
        # Only the confirmation preview reads outside the lock; the tasks that
        # actually move are re-scanned while the project write lock is held.
        candidates = [task for task in _mutable_repository(ctx).list_tasks() if _is_completed_status(task.status)]
        if not candidates:
            click.echo("No completed tasks to move.")
            return
        _echo_cleanup_candidates(candidates)
        if dry_run:
            click.echo("Dry run: no changes made.")
            return
        click.confirm("Move these tasks?", abort=True)
        prompted = True
    done_tasks = _locked_write(ctx, "cleanup_complete_done", lambda: _cleanup_completed_tasks(ctx))
    if not done_tasks:
        click.echo("No completed tasks to move.")
        return
    if not prompted:
        _echo_cleanup_candidates(done_tasks)
    count = len(done_tasks)
    noun = "task" if count == 1 else "tasks"
    click.echo(f"Moved {count} completed {noun} to backlog/completed.")


@main.command("agents")
@click.option("--update-instructions", is_flag=True, help="Update common agent instruction files.")
@click.pass_context
def agents_command(ctx: click.Context, update_instructions: bool) -> None:
    """Update agent instruction files with Backlog.md workflow guidance."""
    if not update_instructions:
        raise click.UsageError("Usage: agents --update-instructions")
    try:
        updates = _locked_write(
            ctx,
            "agents_update_instructions",
            lambda: update_agent_instruction_files(_project(ctx)),
        )
    except AgentInstructionError as exc:
        raise click.ClickException(str(exc)) from exc
    for update in updates:
        click.echo(f"Updated {update.path_relative}")


@main.group("completion")
def completion_group() -> None:
    """Manage shell completion scripts."""


@completion_group.command("install")
@click.option("--shell", default=None, help="Shell type: bash, zsh, fish, or pwsh.")
def completion_install_command(shell: str | None) -> None:
    """Install a user-scoped shell completion script."""
    try:
        result = install_completion(main, target=shell)
    except CompletionInstallError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Installed {result.shell_name} completion for backlog-py CLI.")
    click.echo(f"Completion script written to {result.install_path}")
    click.echo(result.instructions)


@main.group("integration")
def integration_group() -> None:
    """Install opt-in integration helpers."""


@integration_group.command("install-legacy-mcp-shim")
@click.option("--target", type=click.Path(path_type=Path), required=True, help="Existing backlog command to wrap.")
@click.option("--mcp-command", type=click.Path(path_type=Path), default=None, help="backlog-py-mcp command path.")
@click.option("--backup", type=click.Path(path_type=Path), default=None, help="Path for the original command backup.")
def install_legacy_mcp_shim_command(target: Path, mcp_command: Path | None, backup: Path | None) -> None:
    """Route cached 'backlog mcp start' launches to backlog-py-mcp."""
    try:
        result = install_legacy_mcp_shim(target=target, mcp_command=mcp_command, backup=backup)
    except (FileNotFoundError, FileExistsError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Installed legacy MCP shim at {result.target}")
    click.echo(f"Original command backup: {result.backup}")
    click.echo(f"backlog mcp start now routes to: {result.mcp_command}")


@main.group("orchestration")
def orchestration_group() -> None:
    """Manage task orchestration metadata."""


@orchestration_group.command("record-run")
@click.argument("task_id")
@click.option("--actor", default=None, help="Agent or user recording this run.")
@click.option("--result", "run_result", required=True, help="Run result, such as succeeded or failed.")
@click.option("--summary", default="", help="Short run summary.")
@click.option("--file", "--files", "files", multiple=True, help="Project-relative file changed by the run.")
@click.option("--verification", multiple=True, help="Verification command or check executed by the run.")
@click.option("--idempotency-key", default=None, help="Client-supplied idempotency key.")
@click.option("--expected-version", type=int, default=None, help="Expected orchestration state version.")
@click.option("--status-key", default=None, help="New orchestration status key.")
@click.option("--lease-owner", default=None, help="New orchestration lease owner.")
@click.option("--lease-expires-at", default=None, help="New orchestration lease expiry timestamp.")
@click.option("--correlation-id", default=None, help="New orchestration correlation id.")
@click.option("--review-state", default=None, help="New review state.")
@click.option("--reviewer", default=None, help="New reviewer.")
@click.option("--review-attempts", type=int, default=None, help="New review attempt count.")
@click.option("--review-max-attempts", type=int, default=None, help="New maximum review attempts.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print concise plain text output.")
@click.pass_context
def orchestration_record_run_command(
    ctx: click.Context,
    task_id: str,
    actor: str | None,
    run_result: str,
    summary: str,
    files: tuple[str, ...],
    verification: tuple[str, ...],
    idempotency_key: str | None,
    expected_version: int | None,
    status_key: str | None,
    lease_owner: str | None,
    lease_expires_at: str | None,
    correlation_id: str | None,
    review_state: str | None,
    reviewer: str | None,
    review_attempts: int | None,
    review_max_attempts: int | None,
    as_json: bool,
    plain: bool,
) -> None:
    """Append a run-history event and optional orchestration state update."""
    project = _project(ctx)
    service = OrchestrationService(project)
    state_update = _orchestration_state_update_or_none(
        status_key=status_key,
        lease_owner=lease_owner,
        lease_expires_at=lease_expires_at,
        correlation_id=correlation_id,
        review_state=review_state,
        reviewer=reviewer,
        review_attempts=review_attempts,
        review_max_attempts=review_max_attempts,
    )
    try:
        result = service.record_run(
            task_id,
            actor=actor,
            result=run_result,
            summary=summary,
            files=files,
            verification=verification,
            idempotency_key=idempotency_key,
            expected_version=expected_version,
            state_update=state_update,
        )
        payload = _orchestration_record_run_payload(project, task_id, result, detailed=as_json)
    except RunHistoryParseError as exc:
        location = exc.location or "run_history"
        raise click.ClickException(
            f"{task_id}: malformed run history at {location} ({exc.code}). "
            f"Fix the run history section before recording a new run: {exc.message}"
        ) from exc
    except OrchestrationIdempotencyConflict as exc:
        raise click.ClickException(
            f"{task_id}: {exc}. Use a new --idempotency-key or repeat the original run metadata."
        ) from exc
    except OrchestrationError as exc:
        raise click.ClickException(_format_orchestration_error(task_id, exc)) from exc
    # NotFoundError (a KeyError subclass) is mapped to a clean "Error: ..."
    # message by _BacklogGroup; catching it here would print the KeyError repr.

    _echo_orchestration_mutation(
        payload,
        as_json=as_json,
        plain=plain,
        verb="recorded",
        human_line=f"{payload['taskId']} recorded run {payload['eventId']}",
    )


@orchestration_group.command("status")
@click.option("--include-completed", is_flag=True, help="Include completed tasks in the orchestration report.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def orchestration_status_command(ctx: click.Context, include_completed: bool, as_json: bool, plain: bool) -> None:
    """Print orchestration queue status."""
    report = OrchestrationService(_project(ctx)).queue(include_completed=include_completed)
    payload = _orchestration_queue_report_payload(report)
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    _echo_orchestration_counts(payload, plain=plain)


@orchestration_group.command("queue")
@click.option("--include-completed", is_flag=True, help="Include completed tasks in the queue report.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def orchestration_queue_command(ctx: click.Context, include_completed: bool, as_json: bool, plain: bool) -> None:
    """Print orchestration queue items."""
    report = OrchestrationService(_project(ctx)).queue(include_completed=include_completed)
    payload = _orchestration_queue_report_payload(report)
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    _echo_orchestration_items(payload["items"], plain=plain)


@orchestration_group.command("eligible")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def orchestration_eligible_command(ctx: click.Context, as_json: bool, plain: bool) -> None:
    """Print claimable orchestration tasks."""
    items = _orchestration_items_by_category(_project(ctx), "eligible")
    payload = {"items": items}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    _echo_orchestration_items(items, plain=plain)


@orchestration_group.command("claims")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def orchestration_claims_command(ctx: click.Context, as_json: bool, plain: bool) -> None:
    """Print active orchestration claims."""
    items = _orchestration_items_by_category(_project(ctx), "claimed")
    payload = {"items": items}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    _echo_orchestration_items(items, plain=plain)


@orchestration_group.command("stale-leases")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def orchestration_stale_leases_command(ctx: click.Context, as_json: bool, plain: bool) -> None:
    """Print stale orchestration leases."""
    items = _orchestration_items_by_category(_project(ctx), "stale_claim")
    payload = {"items": items}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    _echo_orchestration_items(items, plain=plain)


@orchestration_group.command("split")
@click.argument("task_id")
@click.option("--mode", required=True, type=click.Choice(["child", "continuation"]), help="Split mode.")
@click.option("--actor", required=True, help="Agent or user splitting the task.")
@click.option("--expected-version", type=int, required=True, help="Expected orchestration state version.")
@click.option("--idempotency-key", default=None, help="Client-supplied idempotency key.")
@click.option("--item", "items", multiple=True, required=True, help="Title for a generated split task.")
@click.option(
    "--inherit-dependencies/--no-inherit-dependencies",
    default=True,
    help="Copy parent dependencies to generated tasks.",
)
@click.option(
    "--link-sequence/--no-link-sequence",
    default=True,
    help="Link continuation tasks in dependency order.",
)
@click.option("--transition-to-status", default=None, help="Optional parent orchestration status after split.")
@click.option("--reason", default=None, help="Split reason to store in run history.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print concise plain text output.")
@click.pass_context
def orchestration_split_command(
    ctx: click.Context,
    task_id: str,
    mode: str,
    actor: str,
    expected_version: int,
    idempotency_key: str | None,
    items: tuple[str, ...],
    inherit_dependencies: bool,
    link_sequence: bool,
    transition_to_status: str | None,
    reason: str | None,
    as_json: bool,
    plain: bool,
) -> None:
    """Split a task into child or continuation tasks."""
    project = _project(ctx)
    split_items = tuple(TaskSplitItem(title=item) for item in items)
    try:
        result = OrchestrationService(project).split_task(
            task_id,
            mode=mode,
            actor=actor,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            items=split_items,
            inherit_dependencies=inherit_dependencies,
            link_sequence=link_sequence,
            transition_to_status=transition_to_status,
            reason=reason,
        )
        payload = _orchestration_record_run_payload(project, task_id, result, detailed=as_json)
    except OrchestrationIdempotencyConflict as exc:
        raise click.ClickException(
            f"{task_id}: {exc}. Use a new --idempotency-key or repeat the original split metadata."
        ) from exc
    except OrchestrationError as exc:
        raise click.ClickException(_format_orchestration_error(task_id, exc)) from exc
    except RunHistoryParseError as exc:
        raise click.ClickException(_format_run_history_error(task_id, exc)) from exc
    _echo_orchestration_mutation(payload, as_json=as_json, plain=plain, verb="split")


@orchestration_group.command("claim")
@click.argument("task_id")
@click.option("--actor", required=True, help="Agent or user claiming the task.")
@click.option("--expected-version", type=int, required=True, help="Expected orchestration state version.")
@click.option("--idempotency-key", default=None, help="Client-supplied idempotency key.")
@click.option("--lease-ttl-seconds", type=int, default=None, help="Lease TTL in seconds.")
@click.option("--reason", default=None, help="Claim reason to store in run history.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print concise plain text output.")
@click.pass_context
def orchestration_claim_command(
    ctx: click.Context,
    task_id: str,
    actor: str,
    expected_version: int,
    idempotency_key: str | None,
    lease_ttl_seconds: int | None,
    reason: str | None,
    as_json: bool,
    plain: bool,
) -> None:
    """Claim a task for orchestration work."""
    project = _project(ctx)
    try:
        result = OrchestrationService(project).claim_task(
            task_id,
            actor=actor,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            lease_ttl_seconds=lease_ttl_seconds,
            reason=reason,
        )
        payload = _orchestration_record_run_payload(project, task_id, result, detailed=as_json)
    except OrchestrationIdempotencyConflict as exc:
        raise click.ClickException(
            f"{task_id}: {exc}. Use a new --idempotency-key or repeat the original mutation metadata."
        ) from exc
    except OrchestrationError as exc:
        raise click.ClickException(_format_orchestration_error(task_id, exc)) from exc
    except RunHistoryParseError as exc:
        raise click.ClickException(_format_run_history_error(task_id, exc)) from exc
    _echo_orchestration_mutation(payload, as_json=as_json, plain=plain, verb="claimed")


@orchestration_group.command("release")
@click.argument("task_id")
@click.option("--actor", required=True, help="Agent or user releasing the task.")
@click.option("--expected-version", type=int, required=True, help="Expected orchestration state version.")
@click.option("--idempotency-key", default=None, help="Client-supplied idempotency key.")
@click.option("--reason", default=None, help="Release reason to store in run history.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print concise plain text output.")
@click.pass_context
def orchestration_release_command(
    ctx: click.Context,
    task_id: str,
    actor: str,
    expected_version: int,
    idempotency_key: str | None,
    reason: str | None,
    as_json: bool,
    plain: bool,
) -> None:
    """Release a task orchestration claim."""
    project = _project(ctx)
    try:
        result = OrchestrationService(project).release_task(
            task_id,
            actor=actor,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            reason=reason,
        )
        payload = _orchestration_record_run_payload(project, task_id, result, detailed=as_json)
    except OrchestrationIdempotencyConflict as exc:
        raise click.ClickException(
            f"{task_id}: {exc}. Use a new --idempotency-key or repeat the original mutation metadata."
        ) from exc
    except OrchestrationError as exc:
        raise click.ClickException(_format_orchestration_error(task_id, exc)) from exc
    except RunHistoryParseError as exc:
        raise click.ClickException(_format_run_history_error(task_id, exc)) from exc
    _echo_orchestration_mutation(payload, as_json=as_json, plain=plain, verb="released")


@orchestration_group.command("transition")
@click.argument("task_id")
@click.argument("to_status")
@click.option("--actor", required=True, help="Agent or user transitioning the task.")
@click.option("--expected-version", type=int, required=True, help="Expected orchestration state version.")
@click.option("--idempotency-key", default=None, help="Client-supplied idempotency key.")
@click.option("--reason", default=None, help="Transition reason to store in run history.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option("--plain", is_flag=True, help="Print concise plain text output.")
@click.pass_context
def orchestration_transition_command(
    ctx: click.Context,
    task_id: str,
    to_status: str,
    actor: str,
    expected_version: int,
    idempotency_key: str | None,
    reason: str | None,
    as_json: bool,
    plain: bool,
) -> None:
    """Transition orchestration state."""
    project = _project(ctx)
    try:
        result = OrchestrationService(project).transition_task(
            task_id,
            to_status,
            actor=actor,
            expected_version=expected_version,
            idempotency_key=idempotency_key,
            reason=reason,
        )
        payload = _orchestration_record_run_payload(project, task_id, result, detailed=as_json)
    except OrchestrationIdempotencyConflict as exc:
        raise click.ClickException(
            f"{task_id}: {exc}. Use a new --idempotency-key or repeat the original mutation metadata."
        ) from exc
    except OrchestrationError as exc:
        raise click.ClickException(_format_orchestration_error(task_id, exc)) from exc
    except RunHistoryParseError as exc:
        raise click.ClickException(_format_run_history_error(task_id, exc)) from exc
    _echo_orchestration_mutation(payload, as_json=as_json, plain=plain, verb="transitioned")


@main.group("daemon")
def daemon_group() -> None:
    """Manage the local singleton daemon."""


@daemon_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
def daemon_status_command(as_json: bool) -> None:
    """Print singleton daemon status."""
    try:
        status = daemon_status()
    except DaemonNotRunningError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_daemon_status(status.record, as_json=as_json)


@daemon_group.command("ensure")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
def daemon_ensure_command(as_json: bool) -> None:
    """Start the daemon if needed, then print status."""
    status = daemon_ensure()
    _echo_daemon_status(status.record, as_json=as_json)


@daemon_group.command("start")
@click.option(
    "--allow-remote",
    is_flag=True,
    is_eager=True,
    help="Allow binding a non-loopback host (exposes the MCP JSON-RPC surface to the network).",
)
@click.option(
    "--host",
    default=DEFAULT_HOST,
    show_default=True,
    callback=_validate_bind_host,
    help="Loopback host to bind.",
)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int, help="Loopback port to bind.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
def daemon_start_command(allow_remote: bool, host: str, port: int, as_json: bool) -> None:
    """Start the singleton daemon."""
    status = daemon_start(host=host, port=port, allow_remote=allow_remote)
    _echo_daemon_status(status.record, as_json=as_json)


@daemon_group.command("stop")
@click.option("--force", is_flag=True, help="Force daemon termination when graceful stop does not complete.")
def daemon_stop_command(force: bool) -> None:
    """Stop the singleton daemon."""
    try:
        stopped = daemon_stop(force=force)
    except TimeoutError as exc:
        raise click.ClickException(str(exc)) from exc
    if stopped:
        click.echo("Stopped daemon.")
    else:
        click.echo("Daemon not running.")


@daemon_group.command("run")
@click.option("--foreground", is_flag=True, help="Run the daemon in the foreground.")
@click.option(
    "--allow-remote",
    is_flag=True,
    is_eager=True,
    help="Allow binding a non-loopback host (exposes the MCP JSON-RPC surface to the network).",
)
@click.option(
    "--host",
    default=DEFAULT_HOST,
    show_default=True,
    callback=_validate_bind_host,
    help="Loopback host to bind.",
)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int, help="Loopback port to bind.")
def daemon_run_command(foreground: bool, allow_remote: bool, host: str, port: int) -> None:
    """Run the singleton daemon service."""
    if not foreground:
        raise click.UsageError("Usage: daemon run --foreground")
    from backlog_py.daemon.service import run_foreground_service

    run_foreground_service(host=host, port=port, allow_remote=allow_remote)


@main.group("compat")
def compat_group() -> None:
    """Inspect Backlog.md compatibility coverage."""


@compat_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
@click.option(
    "--release-evidence",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="JSON evidence manifest for browser release-validation gates.",
)
def compat_status_command(as_json: bool, release_evidence: Path | None) -> None:
    """Print implemented and deferred compatibility coverage."""
    try:
        report = build_compatibility_report(
            load_builtin_inventory(),
            release_evidence_path=release_evidence,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    summary = report["summary"]
    click.echo(f"agentCutoverReady: {_bool_text(report['agent_cutover_ready'])}")
    click.echo(f"fullBrowserReleaseReady: {_bool_text(report['full_browser_release_ready'])}")
    baseline = report["upstream_baseline"]
    click.echo(
        "upstreamBaseline: "
        f"{baseline['package']} {baseline['version']} audited {baseline['audit_date']}"
    )
    coverage_scope = report["coverage_scope"]
    click.echo(f"coverageScope: {coverage_scope['kind']}")
    click.echo(f"coverageNote: {coverage_scope['note']}")
    evidence = report["release_evidence"]
    click.echo(f"releaseEvidence: {evidence['status']}")
    if evidence["generated_at"] is not None:
        click.echo(
            "releaseEvidenceGeneratedAt: "
            f"{evidence['generated_at']} ({evidence['age_days']} days old, max {evidence['max_age_days']})"
        )
    if evidence["upstream_baseline"] is not None:
        baseline = evidence["upstream_baseline"]
        click.echo(
            "releaseEvidenceUpstream: "
            f"{baseline['package']} {baseline['version']} audited {baseline['audit_date']}"
        )
    if evidence["error"] is not None:
        click.echo(f"releaseEvidenceError: {evidence['error']}")
    click.echo(f"implemented: {summary['implemented']}")
    click.echo(f"deferred: {summary['deferred']}")
    click.echo(f"total: {summary['total']}")
    click.echo("categories:")
    for category, counts in report["categories"].items():
        click.echo(
            f"  {category}: {counts['implemented']} implemented, "
            f"{counts['deferred']} deferred, {counts['total']} total"
        )
    if report["deferred_items"]:
        click.echo("deferredItems:")
        for item in report["deferred_items"]:
            click.echo(f"  - {item['name']}: {item['reason']}")
    click.echo("releaseGates:")
    for gate in report["release_gates"]["gates"]:
        line = f"  - {gate['name']}: {gate['status']} ({gate['scope']})"
        if gate["evidence_error"] is not None:
            line += f" - evidenceError: {gate['evidence_error']}"
        click.echo(line)


@compat_group.command("evidence-template")
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
    help="Path to write the release evidence manifest template.",
)
@click.option(
    "--rich-edit-artifact",
    multiple=True,
    help="Repo-relative artifact path proving the rich-edit browser release check.",
)
@click.option(
    "--desktop-artifact",
    multiple=True,
    help="Repo-relative desktop screenshot artifact path.",
)
@click.option(
    "--mobile-artifact",
    multiple=True,
    help="Repo-relative mobile screenshot artifact path.",
)
@click.option(
    "--command",
    "command_text",
    default=None,
    help="Command provenance to store in the manifest.",
)
@click.option(
    "--max-age-days",
    type=click.IntRange(min=1),
    default=14,
    show_default=True,
    help="Maximum acceptable evidence age for release gates.",
)
def compat_evidence_template_command(
    output: Path,
    rich_edit_artifact: tuple[str, ...],
    desktop_artifact: tuple[str, ...],
    mobile_artifact: tuple[str, ...],
    command_text: str | None,
    max_age_days: int,
) -> None:
    """Write a portable browser release-evidence manifest template."""
    try:
        manifest = build_release_evidence_manifest(
            rich_edit_artifacts=rich_edit_artifact,
            desktop_artifacts=desktop_artifact,
            mobile_artifacts=mobile_artifact,
            command_argv=(command_text,) if command_text else (),
            max_age_days=max_age_days,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    click.echo(f"Wrote release evidence template to {output}")


@main.group("config", invoke_without_command=True, no_args_is_help=False)
@click.pass_context
def config_group(ctx: click.Context) -> None:
    """Inspect or interactively edit Backlog.md configuration."""
    if ctx.invoked_subcommand is None:
        _run_config_wizard(ctx)


@config_group.command("list")
@click.pass_context
def config_list(ctx: click.Context) -> None:
    """Print effective configuration."""
    project = _project(ctx)
    config = project.config
    click.echo(f"projectName: {config.project_name}")
    click.echo(f"defaultAssignee: {config.default_assignee or '(not set)'}")
    click.echo(f"defaultStatus: {config.default_status}")
    click.echo(f"dateFormat: {config.date_format}")
    click.echo(f"includeDatetimeInDates: {_bool_text(config.include_datetime_in_dates)}")
    click.echo(f"defaultEditor: {config.default_editor or '(not set)'}")
    click.echo(f"defaultPort: {config.default_port}")
    click.echo(f"autoOpenBrowser: {_bool_text(config.auto_open_browser)}")
    click.echo(f"remoteOperations: {_bool_text(config.remote_operations)}")
    click.echo(f"autoCommit: {_bool_text(config.auto_commit)}")
    click.echo(f"bypassGitHooks: {_bool_text(config.bypass_git_hooks)}")
    click.echo(f"onStatusChange: {config.on_status_change or '(disabled)'}")
    click.echo(f"zeroPaddedIds: {config.zero_padded_ids if config.zero_padded_ids is not None else '(disabled)'}")
    click.echo(f"taskPrefix: {config.task_prefix} (read-only)")
    click.echo(f"checkActiveBranches: {_bool_text(config.check_active_branches)}")
    click.echo(f"activeBranchDays: {config.active_branch_days}")
    if config.statuses is not None:
        click.echo("statuses:")
        for status in config.statuses:
            click.echo(f"  - {status}")
    if config.definition_of_done is not None:
        click.echo("definitionOfDone:")
        for item in config.definition_of_done:
            click.echo(f"  - {item}")


@config_group.command("get")
@click.argument("key")
@click.pass_context
def config_get(ctx: click.Context, key: str) -> None:
    """Print one effective config value."""
    # NotFoundError (a KeyError subclass) is mapped to a clean "Error: ..."
    # message by _BacklogGroup; catching it here would print the KeyError repr.
    click.echo(_format_config_value(get_config_value(_project(ctx), key)))


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str) -> None:
    """Persist one project config value."""
    try:
        raw_key, parsed_value = _locked_write(
            ctx,
            "config_set",
            lambda: set_config_value(_project(ctx), key, value),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"{raw_key}: {_format_config_value(parsed_value)}")


@config_group.command("dod-defaults-get")
@click.pass_context
def config_dod_defaults_get(ctx: click.Context) -> None:
    """Print project Definition of Done defaults."""
    for item in get_definition_of_done_defaults(_project(ctx)):
        click.echo(item)


@config_group.command("dod-defaults-upsert")
@click.argument("items", nargs=-1, required=False)
@click.pass_context
def config_dod_defaults_upsert(ctx: click.Context, items: tuple[str, ...]) -> None:
    """Replace project Definition of Done defaults."""
    config = _locked_write(
        ctx,
        "definition_of_done_defaults_upsert",
        lambda: replace_definition_of_done_defaults(_project(ctx), list(items)),
    )
    for item in config.definition_of_done or []:
        click.echo(item)


def _run_config_wizard(ctx: click.Context) -> None:
    try:
        _run_config_wizard_inner(ctx)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _run_config_wizard_inner(ctx: click.Context) -> None:
    """Prompt for every config key, then persist the changed ones under one lock."""
    project = _project(ctx)
    config = project.config
    click.echo("Interactive Backlog.md configuration")

    answers: list[tuple[str, str]] = []

    def text(key: str, label: str, default: str) -> None:
        _prompt_config_text(answers, key, label, default)

    def boolean(key: str, label: str, default: bool) -> None:
        _prompt_config_bool(answers, key, label, default)

    text("projectName", "Project name", config.project_name)
    text("defaultAssignee", "Default assignee", config.default_assignee or "")
    text("defaultStatus", "Default status", config.default_status)
    text("dateFormat", "Date format", config.date_format)
    boolean("includeDatetimeInDates", "Include time in dates", config.include_datetime_in_dates)
    text("defaultEditor", "Default editor", config.default_editor or "")
    text("defaultPort", "Browser port", str(config.default_port))
    boolean("autoOpenBrowser", "Auto-open browser", config.auto_open_browser)
    boolean("remoteOperations", "Enable remote git operations", config.remote_operations)
    boolean("autoCommit", "Enable auto-commit", config.auto_commit)
    boolean("bypassGitHooks", "Bypass git hooks", config.bypass_git_hooks)
    text("onStatusChange", "Status change hook command", config.on_status_change or "")
    text("zeroPaddedIds", "Zero-padded ID width (0 disables)", str(config.zero_padded_ids or 0))
    boolean("checkActiveBranches", "Check active branches", config.check_active_branches)
    text("activeBranchDays", "Active branch days", str(config.active_branch_days))
    text("statuses", "Statuses (comma-separated)", ",".join(config.statuses or []))

    dod_defaults = get_definition_of_done_defaults(project)
    dod_text = click.prompt(
        "Definition of Done defaults (comma-separated)",
        default=",".join(dod_defaults),
        show_default=bool(dod_defaults),
    )
    dod_items = _split_csv_items(dod_text)
    dod_changed = dod_items != dod_defaults

    if answers or dod_changed:
        _locked_write(
            ctx,
            "config_wizard",
            lambda: _write_config_wizard_answers(project, answers, dod_items if dod_changed else None),
        )
    click.echo(f"Updated config at {project.config_path}")


def _write_config_wizard_answers(
    project: BacklogProject,
    answers: list[tuple[str, str]],
    dod_items: list[str] | None,
) -> None:
    for key, value in answers:
        set_config_value(project, key, value)
    if dod_items is not None:
        replace_definition_of_done_defaults(project, dod_items)


def _prompt_config_text(answers: list[tuple[str, str]], key: str, label: str, default: str) -> None:
    value = click.prompt(label, default=default, show_default=bool(default))
    _validate_config_wizard_value(key, value)
    if value != default:
        answers.append((key, value))


def _prompt_config_bool(answers: list[tuple[str, str]], key: str, label: str, default: bool) -> None:
    value = click.confirm(label, default=default)
    if value != default:
        answers.append((key, _bool_text(value)))


# Numeric wizard keys validated at the prompt so a bad answer fails immediately
# instead of after every remaining question. storage.config re-validates on write.
_WIZARD_NUMERIC_KEYS = {
    "defaultPort": ("default_port", "port"),
    "zeroPaddedIds": ("zero_padded_ids", "non_negative"),
    "activeBranchDays": ("active_branch_days", "integer"),
}


def _validate_config_wizard_value(key: str, value: str) -> None:
    entry = _WIZARD_NUMERIC_KEYS.get(key)
    if entry is None:
        return
    normalized_key, kind = entry
    try:
        parsed = int(value.strip(), 10)
    except ValueError as exc:
        raise ValueError(f"Backlog config value {normalized_key} must be an integer") from exc
    if kind == "port" and not 1 <= parsed <= 65535:
        raise ValueError(f"Backlog config value {normalized_key} must be a valid port number (1-65535)")
    if kind == "non_negative" and parsed < 0:
        raise ValueError(f"Backlog config value {normalized_key} must be a non-negative number")


def _split_csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@main.group("doc")
def document_group() -> None:
    """Create and inspect Backlog.md documents."""


@document_group.command("list")
@click.argument("query", required=False)
@click.pass_context
def document_list_command(ctx: click.Context, query: str | None) -> None:
    """List documents, optionally filtered by query."""
    service = _document_service(ctx)
    documents = service.list_documents() if query is None else service.search_documents(query)
    for document in documents:
        click.echo(_format_document_line(document))


@document_group.command("view")
@click.argument("path_or_id")
@click.pass_context
def document_view_command(ctx: click.Context, path_or_id: str) -> None:
    """Print a document by docs-relative path or frontmatter id."""
    document = _document_service(ctx).view_document(path_or_id)
    click.echo(document.raw_source.rstrip())


@document_group.command("create")
@click.argument("path_or_title")
@click.option("--title", default=None, help="Document title for explicit-path create.")
@click.option("-p", "--path", "document_path", default=None, help="Docs-relative directory for title-based create.")
@click.option("--content", default="", help="Document body content.")
@click.option("-t", "--type", "document_type", default=None, help="Document type metadata.")
@click.option("--tags", multiple=True, help="Comma-separated document tags metadata.")
@click.pass_context
def document_create_command(
    ctx: click.Context,
    path_or_title: str,
    title: str | None,
    document_path: str | None,
    content: str,
    document_type: str | None,
    tags: tuple[str, ...],
) -> None:
    """Create a document under backlog/docs."""
    service = _document_service(ctx)
    metadata = _document_metadata(document_type, tags)
    if title is None:
        document = _locked_write(
            ctx,
            "document_create",
            lambda: service.create_document_from_title(
                path_or_title,
                directory=document_path,
                content=content,
                metadata=metadata,
            ),
        )
    else:
        if document_path is not None:
            raise click.UsageError("Use --path only with title-based document create.")
        document = _locked_write(
            ctx,
            "document_create",
            lambda: service.create_document(
                path_or_title,
                title=title,
                content=content,
                metadata=metadata,
            ),
        )
    click.echo(_format_document_line(document))


@document_group.command("update")
@click.argument("path_or_id")
@click.option("--title", default=None, help="Replacement document title.")
@click.option("--content", default=None, help="Replacement document body content.")
@click.option("-p", "--path", "document_path", default=None, help="Move document to a docs-relative directory.")
@click.option("-t", "--type", "document_type", default=None, help="Replacement document type metadata.")
@click.option("--tags", multiple=True, help="Comma-separated replacement document tags metadata.")
@click.pass_context
def document_update_command(
    ctx: click.Context,
    path_or_id: str,
    title: str | None,
    content: str | None,
    document_path: str | None,
    document_type: str | None,
    tags: tuple[str, ...],
) -> None:
    """Update a document while preserving omitted metadata."""
    document = _locked_write(
        ctx,
        "document_update",
        lambda: _document_service(ctx).update_document(
            path_or_id,
            title=title,
            content=content,
            directory=document_path,
            metadata=_document_metadata(document_type, tags),
        ),
    )
    click.echo(_format_document_line(document))


@main.group("draft")
def draft_group() -> None:
    """Create and inspect Backlog.md drafts."""


@draft_group.command("list")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def draft_list_command(ctx: click.Context, plain: bool) -> None:
    """List draft tasks."""
    drafts = _draft_service(ctx).list_drafts()
    if not drafts:
        click.echo("No drafts found.")
        return
    if plain:
        click.echo("Drafts:")
        for draft in drafts:
            click.echo(f"  {draft.id} - {draft.title}")
        return
    for draft in drafts:
        click.echo(_format_task_line(draft, plain=False))


@draft_group.command("create")
@click.argument("title")
@click.option("-d", "--desc", "--description", "description", default="", help="Description for draft creation.")
@click.option("-a", "--assignee", "assignees", multiple=True, help="Draft assignee.")
@click.option("-l", "--label", "labels", multiple=True, help="Draft label.")
@click.option("-s", "--status", default=None, help="Accepted for upstream CLI compatibility; drafts remain Draft.")
@click.pass_context
def draft_create_command(
    ctx: click.Context,
    title: str,
    description: str,
    assignees: tuple[str, ...],
    labels: tuple[str, ...],
    status: str | None,
) -> None:
    """Create a draft task."""
    _ = status
    draft = _locked_write(
        ctx,
        "draft_create",
        lambda: _draft_service(ctx).create_draft(
            title=title,
            description=description,
            assignees=assignees,
            labels=labels,
        ),
    )
    click.echo(f"Created draft {draft.id}")
    click.echo(f"File: {draft.path}")


@draft_group.command("promote")
@click.argument("draft_id")
@click.pass_context
def draft_promote_command(ctx: click.Context, draft_id: str) -> None:
    """Promote a draft task to an active task."""
    task = _locked_write(ctx, "draft_promote", lambda: _draft_service(ctx).promote_draft(draft_id))
    click.echo(f"Promoted draft {draft_id} to {task.id}")


@draft_group.command("archive")
@click.argument("draft_id")
@click.pass_context
def draft_archive_command(ctx: click.Context, draft_id: str) -> None:
    """Archive a draft task."""
    draft = _locked_write(ctx, "draft_archive", lambda: _draft_service(ctx).archive_draft(draft_id))
    click.echo(f"Archived draft {draft.id}")


@draft_group.command("view")
@click.argument("draft_id")
@click.option("--plain", is_flag=True, help="Print plain text output.")
@click.pass_context
def draft_view_command(ctx: click.Context, draft_id: str, plain: bool) -> None:
    """Print a draft by id."""
    draft = _draft_service(ctx).view_draft(draft_id)
    click.echo(_format_task_detail(_project(ctx), draft, plain=plain))


@main.group("decision")
def decision_group() -> None:
    """Create and inspect Backlog.md decisions."""


@decision_group.command("create")
@click.argument("title")
@click.option("-s", "--status", default="proposed", help="Decision status.")
@click.pass_context
def decision_create_command(ctx: click.Context, title: str, status: str) -> None:
    """Create an architectural decision record."""
    decision = _locked_write(
        ctx,
        "decision_create",
        lambda: _decision_service(ctx).create_decision(title, status=status),
    )
    click.echo(f"Created decision {decision.id}")


@main.group("milestone")
def milestone_group() -> None:
    """Create and inspect milestone files."""


@milestone_group.command("list")
@click.pass_context
def milestone_list_command(ctx: click.Context) -> None:
    """List active milestones."""
    for milestone in _milestone_service(ctx).list_milestones():
        click.echo(_format_milestone_line(milestone))


@milestone_group.command("add")
@click.argument("name")
@click.option("--description", default="", help="Milestone body content.")
@click.pass_context
def milestone_add_command(ctx: click.Context, name: str, description: str) -> None:
    """Create a milestone file."""
    milestone = _locked_write(
        ctx,
        "milestone_add",
        lambda: _milestone_service(ctx).add_milestone(name, description=description),
    )
    click.echo(_format_milestone_line(milestone))


@milestone_group.command("rename")
@click.argument("old_name")
@click.argument("new_name")
@click.option("--update-tasks", is_flag=True, help="Update task milestone frontmatter references.")
@click.pass_context
def milestone_rename_command(ctx: click.Context, old_name: str, new_name: str, update_tasks: bool) -> None:
    """Rename a milestone file."""
    milestone = _locked_write(
        ctx,
        "milestone_rename",
        lambda: _milestone_service(ctx).rename_milestone(old_name, new_name, update_tasks=update_tasks),
    )
    click.echo(_format_milestone_line(milestone))


@milestone_group.command("remove")
@click.argument("name")
@click.option("--clear-tasks", is_flag=True, help="Clear matching task milestone frontmatter references.")
@click.pass_context
def milestone_remove_command(ctx: click.Context, name: str, clear_tasks: bool) -> None:
    """Remove a milestone file."""
    milestone = _locked_write(
        ctx,
        "milestone_remove",
        lambda: _milestone_service(ctx).remove_milestone(name, clear_tasks=clear_tasks),
    )
    click.echo(_format_milestone_line(milestone))


@milestone_group.command("archive")
@click.argument("name")
@click.pass_context
def milestone_archive_command(ctx: click.Context, name: str) -> None:
    """Move a milestone file to backlog/archive/milestones."""
    milestone = _locked_write(
        ctx,
        "milestone_archive",
        lambda: _milestone_service(ctx).archive_milestone(name),
    )
    click.echo(f"{_format_milestone_line(milestone)} archived")


def _project(ctx: click.Context) -> BacklogProject:
    """Resolve the project once per CLI invocation.

    Project discovery walks parent directories and parses the YAML config, so
    the result is memoized on ``ctx.obj`` (shared by every sub-context of one
    invocation). Config mutations stay correct because the storage layer
    re-reads the raw config file from ``project.config_path`` on every write.
    """
    cache = ctx.obj if isinstance(ctx.obj, dict) else None
    if cache is not None:
        cached_project = cache.get("project")
        if isinstance(cached_project, BacklogProject):
            return cached_project
    project = _discover_project(ctx)
    if cache is not None:
        cache["project"] = project
    return project


def _discover_project(ctx: click.Context) -> BacklogProject:
    try:
        return discover_project(Path.cwd(), explicit_cwd=_explicit_cwd(ctx))
    except FileNotFoundError as exc:
        raise click.ClickException(
            f"{exc}. Run from a Backlog.md project or pass --cwd /path/to/project."
        ) from exc


def _cwd(ctx: click.Context) -> Path:
    return _explicit_cwd(ctx) or Path.cwd()


def _explicit_cwd(ctx: click.Context) -> Path | None:
    return ctx.obj.get("cwd") if ctx.obj else None


def _repository(ctx: click.Context) -> ReadOnlyRepository:
    return ReadOnlyRepository(_project(ctx))


def _mutable_repository(ctx: click.Context) -> MutableRepository:
    return MutableRepository(_project(ctx))


def _document_service(ctx: click.Context) -> DocumentService:
    return DocumentService(_project(ctx))


def _draft_service(ctx: click.Context) -> DraftService:
    return DraftService(_project(ctx))


def _decision_service(ctx: click.Context) -> DecisionService:
    return DecisionService(_project(ctx))


def _milestone_service(ctx: click.Context) -> MilestoneService:
    return MilestoneService(_project(ctx))


def _locked_write(ctx: click.Context, operation: str, fn: Callable[[], T]) -> T:
    return with_project_write_lock(_project(ctx), operation, fn)


def _locked_init(ctx: click.Context, operation: str, fn: Callable[[], T]) -> T:
    return with_init_lock(_cwd(ctx), operation, fn)


def _initialize_project(
    ctx: click.Context,
    *,
    project_name: str | None,
    backlog_dir: str,
    task_prefix: str,
    config_location: str,
    no_git: bool,
    agent_instructions: bool,
) -> tuple[InitProjectResult, list[AgentInstructionUpdate]]:
    result = init_project(
        _cwd(ctx),
        project_name=project_name,
        backlog_dir=backlog_dir,
        task_prefix=task_prefix,
        config_location=config_location,
        no_git=no_git,
    )
    instruction_updates = update_agent_instruction_files(result.project) if agent_instructions else []
    return result, instruction_updates


def _echo_cleanup_candidates(tasks: list[TaskRecord]) -> None:
    noun = "task" if len(tasks) == 1 else "tasks"
    click.echo(f"{len(tasks)} completed {noun} will be moved to backlog/completed:")
    for task in tasks:
        click.echo(f"  {task.id} - {task.title}")


def _cleanup_completed_tasks(ctx: click.Context) -> list[TaskRecord]:
    repository = _mutable_repository(ctx)
    done_tasks = [task for task in repository.list_tasks() if _is_completed_status(task.status)]
    for task in done_tasks:
        repository.complete_task(task.id)
    return done_tasks


def _format_task_line(task_record: TaskRecord, *, plain: bool) -> str:
    if plain:
        line = f"{task_record.id} [{task_record.status}] {task_record.title}"
        metadata = _format_task_metadata_lines(task_record)
        return "\n".join([line, *metadata])
    return f"{_style_identifier(task_record.id)} - {task_record.title} ({_style_status(task_record.status)})"


def _format_task_detail(project: BacklogProject, task_record: TaskRecord, *, plain: bool) -> str:
    if plain:
        return _format_plain_task_detail(project, task_record)
    return task_record.raw_source


def _format_plain_task_detail(project: BacklogProject, task_record: TaskRecord) -> str:
    try:
        path_display = task_record.path.relative_to(project.root).as_posix()
    except ValueError:
        path_display = task_record.path.as_posix()

    lines = [
        f"File: {path_display}",
        "",
        f"Task {task_record.id} - {task_record.title}",
        "=" * 50,
        "",
        f"Status: {_format_status_with_icon(task_record.status)}",
    ]

    for label, value in _plain_task_metadata(task_record):
        lines.append(f"{label}: {value}")

    lines.extend(["", "Description:", _plain_task_description(task_record)])
    _append_plain_section(lines, "Acceptance Criteria", _format_plain_checklist(task_record, "AC"))
    _append_plain_section(lines, "Implementation Notes", _section_content(task_record, "IMPLEMENTATION_NOTES"))
    _append_plain_section(lines, "Final Summary", _section_content(task_record, "FINAL_SUMMARY"))
    _append_plain_section(lines, "Definition of Done", _format_plain_checklist(task_record, "DOD"))
    return "\n".join(lines).rstrip()


def _plain_task_metadata(task_record: TaskRecord) -> list[tuple[str, str]]:
    frontmatter = task_record.parsed.frontmatter
    metadata: list[tuple[str, str]] = []
    scalar_fields = [
        ("priority", "Priority"),
        ("ordinal", "Ordinal"),
        ("created_date", "Created"),
        ("updated_date", "Updated"),
        ("milestone", "Milestone"),
        ("parent_task_id", "Parent"),
    ]
    list_fields = [
        ("assignee", "Assignee"),
        ("labels", "Labels"),
        ("dependencies", "Dependencies"),
        ("references", "References"),
        ("documentation", "Documentation"),
        ("modified_files", "Modified files"),
    ]

    for key, label in scalar_fields:
        value = frontmatter.get(key)
        if value is not None and value != "":
            metadata.append((label, str(value)))
    for key, label in list_fields:
        values = _frontmatter_string_list(task_record, key)
        if values:
            metadata.append((label, ", ".join(values)))
    return metadata


def _append_plain_section(lines: list[str], heading: str, body: str) -> None:
    lines.extend(["", f"{heading}:"])
    lines.append(body or "(none)")


def _plain_task_description(task_record: TaskRecord) -> str:
    return task_record.description_or_legacy_body or "(empty)"


def _section_content(task_record: TaskRecord, section_name: str) -> str:
    section = task_record.parsed.sections.get(section_name)
    return "" if section is None else section.content.strip()


def _format_plain_checklist(task_record: TaskRecord, checklist_name: str) -> str:
    items = task_record.parsed.checklists.get(checklist_name, [])
    return "\n".join(item.raw_line for item in items)


def _format_status_with_icon(status: str) -> str:
    status_icons = {
        "Done": "✔",
        "In Progress": "◒",
        "Blocked": "●",
        "To Do": "○",
        "Review": "◆",
        "Testing": "▣",
        "Draft": "○",
    }
    return f"{status_icons.get(status, '○')} {status}"


def _run_interactive_task_view(ctx: click.Context, task_record: TaskRecord) -> None:
    click.echo(_format_interactive_task_detail(_project(ctx), task_record))
    if not _stdin_is_interactive():
        return
    key = _read_interactive_key()
    if key == "e":
        _edit_task_in_configured_editor(ctx, task_record)


def _format_interactive_task_detail(project: BacklogProject, task_record: TaskRecord) -> str:
    try:
        path_display = task_record.path.relative_to(project.root).as_posix()
    except ValueError:
        path_display = task_record.path.as_posix()
    description = task_record.description_or_legacy_body or "(empty)"
    lines = [
        click.style(f"Task {task_record.id}", fg="cyan", bold=True),
        f"Title: {task_record.title}",
        f"Status: {task_record.status}",
        f"File: {path_display}",
        *_interactive_task_date_metadata(project, task_record),
        "",
        "Description:",
        description,
        "",
        "Actions: [E]dit in editor  [Q]uit",
    ]
    return "\n".join(lines).rstrip()


def _interactive_task_date_metadata(project: BacklogProject, task_record: TaskRecord) -> list[str]:
    metadata: list[str] = []
    for key, label in (("created_date", "Created"), ("updated_date", "Updated")):
        value = task_record.parsed.frontmatter.get(key)
        if value is not None and value != "":
            metadata.append(f"{label}: {_format_configured_date(value, project=project)}")
    return metadata


def _format_configured_date(value: object, *, project: BacklogProject) -> str:
    parsed = _parse_frontmatter_date(value)
    if parsed is None:
        return str(value)
    date_text = _format_date_tokens(parsed, project.config.date_format)
    if project.config.include_datetime_in_dates and _frontmatter_value_includes_time(value):
        return f"{date_text} {parsed.strftime('%H:%M')}"
    return date_text


def _parse_frontmatter_date(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_date_tokens(value: datetime, date_format: str) -> str:
    template = (date_format or "yyyy-mm-dd").strip().casefold()
    if not template:
        template = "yyyy-mm-dd"
    for token, replacement in (
        ("yyyy", f"{value.year:04d}"),
        ("yy", f"{value.year % 100:02d}"),
        ("mm", f"{value.month:02d}"),
        ("dd", f"{value.day:02d}"),
    ):
        template = template.replace(token, replacement)
    return template


def _frontmatter_value_includes_time(value: object) -> bool:
    if isinstance(value, datetime):
        return True
    if isinstance(value, date):
        return False
    text = str(value)
    return ":" in text or "T" in text


def _edit_task_in_configured_editor(ctx: click.Context, task_record: TaskRecord) -> None:
    """Edit a task file without holding the project write lock while the user types.

    The flow lives in :mod:`backlog_py.core.editing` so the CLI and the TUI
    cannot drift on it; this only supplies the two surface-specific pieces (how
    to launch the editor, how to take the lock — auto-commit included, since it
    runs inside ``with_project_write_lock``) and maps the shared abort onto a
    ClickException so it renders as a clean error rather than a traceback.
    """
    project = _project(ctx)
    editor_command = _configured_editor_command(project)

    def run_editor(scratch_path: Path) -> None:
        _run_editor_command(editor_command, scratch_path)

    def apply_locked(apply: Callable[[], None]) -> None:
        _locked_write(ctx, "task_editor", apply)

    try:
        edit_via_scratch_copy(
            task_record.path,
            project.root,
            editor_label=editor_command[0],
            run_editor=run_editor,
            apply_locked=apply_locked,
        )
    except EditorAbort as exc:
        raise _EditorConflictError(str(exc)) from exc
    click.echo(f"Edited {task_record.id}")


def _configured_editor_command(project: BacklogProject) -> list[str]:
    editor = project.config.default_editor or os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor is None or not editor.strip():
        raise click.ClickException("No editor configured. Set defaultEditor, VISUAL, or EDITOR.")
    try:
        command = shlex.split(editor)
    except ValueError as exc:
        raise click.ClickException(f"Invalid editor command: {exc}") from exc
    if not command:
        raise click.ClickException("No editor configured. Set defaultEditor, VISUAL, or EDITOR.")
    return command


def _run_editor_command(command: list[str], path: Path) -> None:
    try:
        result = subprocess.run([*command, str(path)], check=False)  # nosec B603
    except FileNotFoundError as exc:
        raise click.ClickException(f"Editor command not found: {command[0]}") from exc
    if result.returncode != 0:
        raise click.ClickException(f"Editor exited with status {result.returncode}: {command[0]}")


def _stdin_is_interactive() -> bool:
    return click.get_text_stream("stdin").isatty()


def _read_interactive_key() -> str:
    return click.getchar().strip().casefold()


def _format_board_task_line(task_record: TaskRecord) -> str:
    return f"{_style_identifier(task_record.id)} [{_style_status(task_record.status)}] {task_record.title}"


def _format_document_line(document: DocumentRecord, *, plain: bool = False) -> str:
    path = str(document.path_relative) if plain else _style_identifier(str(document.path_relative))
    return f"{path} {document.title}".rstrip()


def _format_decision_line(decision: DecisionRecord, *, plain: bool = False) -> str:
    identifier = decision.id if plain else _style_identifier(decision.id)
    status = decision.status if plain else _style_status(decision.status)
    return f"{identifier} [{status}] {decision.title}".rstrip()


def _format_milestone_line(milestone: MilestoneRecord) -> str:
    return f"{_style_identifier(milestone.name)} {milestone.path_relative}".rstrip()


def _style_identifier(value: str) -> str:
    return click.style(value, fg="cyan", bold=True)


def _style_status(status: str) -> str:
    return click.style(status, fg=_status_color(status), bold=True)


def _status_color(status: str) -> str:
    normalized = status.strip().casefold()
    if "done" in normalized or "complete" in normalized:
        return "green"
    if "progress" in normalized or "review" in normalized:
        return "blue"
    if "block" in normalized:
        return "red"
    if "todo" in normalized or "to do" in normalized or "backlog" in normalized:
        return "yellow"
    if "draft" in normalized:
        return "magenta"
    return "white"


def _format_config_value(value: object) -> str:
    if isinstance(value, bool):
        return _bool_text(value)
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    if value is None:
        return "null"
    return str(value)


def _orchestration_state_update_or_none(
    *,
    status_key: str | None,
    lease_owner: str | None,
    lease_expires_at: str | None,
    correlation_id: str | None,
    review_state: str | None,
    reviewer: str | None,
    review_attempts: int | None,
    review_max_attempts: int | None,
) -> OrchestrationStateUpdate | None:
    values = {
        "status_key": status_key,
        "lease_owner": lease_owner,
        "lease_expires_at": lease_expires_at,
        "correlation_id": correlation_id,
        "review_state": review_state,
        "reviewer": reviewer,
        "review_attempts": review_attempts,
        "review_max_attempts": review_max_attempts,
    }
    if all(value is None for value in values.values()):
        return None
    return OrchestrationStateUpdate(**values)


def _orchestration_record_run_payload(
    project: BacklogProject,
    task_id: str,
    result: OrchestrationMutationResult,
    *,
    detailed: bool = True,
) -> dict[str, object]:
    """Build the mutation response payload.

    The service already resolved the task and returned its id, project-relative
    path and version, so nothing here re-fetches the task to look those up
    again. The queue decoration (category, validation issues) plus the run
    history ids cost a full project scan and a task read, so they are only
    computed for --json output, which is the only rendering that shows them.
    """
    payload: dict[str, object] = {
        "taskId": result.task_id,
        "path": result.path,
        "version": result.version,
        "eventId": result.event.event_id,
    }
    if detailed:
        queue_item = _orchestration_queue_item(project, result.task_id)
        if queue_item is not None:
            payload["path"] = queue_item.path
            payload["version"] = queue_item.version
        history = parse_run_history(_read_task_source(project, result.task_id, str(payload["path"])))
        payload["runHistoryEventIds"] = [event.event_id for event in history.events]
        payload["queueCategory"] = queue_item.category if queue_item is not None else None
        payload["validationIssues"] = _orchestration_validation_issues_payload(
            queue_item.validation_issues if queue_item is not None else []
        )
    created_task_ids = getattr(result, "created_task_ids", None)
    if created_task_ids is not None:
        payload["createdTaskIds"] = list(created_task_ids)
        payload["parentEventId"] = getattr(result, "parent_event_id", result.event.event_id)
        payload["splitMode"] = result.event.split_mode
    return payload


def _orchestration_queue_report_payload(report: object) -> dict[str, object]:
    items = getattr(report, "items")
    by_category = getattr(report, "by_category")
    return {
        "byCategory": dict(by_category),
        "items": [_orchestration_queue_item_payload(item) for item in items],
    }


def _orchestration_queue_item_payload(item: OrchestrationQueueItem) -> dict[str, object]:
    return {
        "taskId": item.task_id,
        "path": item.path,
        "title": item.title,
        "version": item.version,
        "effectiveStatus": item.effective_status,
        "queueCategory": item.category,
        "validationIssues": _orchestration_validation_issues_payload(item.validation_issues),
        "dependencyIds": list(item.dependency_ids),
        "leaseOwner": item.lease_owner,
        "leaseExpiresAt": item.lease_expires_at,
    }


def _orchestration_items_by_category(project: BacklogProject, category: str) -> list[dict[str, object]]:
    report = OrchestrationService(project).queue(include_completed=True)
    return [_orchestration_queue_item_payload(item) for item in report.items if item.category == category]


def _read_task_source(project: BacklogProject, task_id: str, relative_path: str) -> str:
    """Read one known task file instead of re-scanning the project for its id."""
    path = Path(relative_path)
    if not path.is_absolute():
        path = project.root / path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"{task_id}: could not read task file at {relative_path}: {exc}") from exc


def _orchestration_queue_item(project: BacklogProject, task_id: str) -> OrchestrationQueueItem | None:
    report = OrchestrationService(project).queue(include_completed=True)
    normalized = task_id.casefold()
    for item in report.items:
        if item.task_id.casefold() == normalized:
            return item
    return None


def _orchestration_validation_issues_payload(issues: list[ValidationIssue]) -> list[dict[str, str]]:
    return [
        {
            "code": issue.code,
            "message": issue.message,
            "path": issue.path,
            "severity": issue.severity,
        }
        for issue in issues
    ]


def _format_orchestration_error(task_id: str, error: OrchestrationError) -> str:
    message = f"{task_id}: {error}"
    if error.details:
        details = ", ".join(f"{key}={value}" for key, value in sorted(error.details.items()))
        message = f"{message} ({details})"
    return message


def _format_run_history_error(task_id: str, error: RunHistoryParseError) -> str:
    location = error.location or "run_history"
    return (
        f"{task_id}: malformed run history at {location} ({error.code}). "
        f"Fix the run history section before recording a new run: {error.message}"
    )


def _echo_orchestration_mutation(
    payload: dict[str, object],
    *,
    as_json: bool,
    plain: bool,
    verb: str,
    human_line: str | None = None,
) -> None:
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    if plain:
        # Tab-separated so agents can parse it: taskId, verb, eventId, version.
        click.echo(f"{payload['taskId']}\t{verb}\t{payload['eventId']}\t{payload['version']}")
        return
    click.echo(human_line if human_line is not None else f"{payload['taskId']} {verb} via {payload['eventId']}")


def _echo_orchestration_counts(payload: dict[str, object], *, plain: bool = False) -> None:
    by_category = payload["byCategory"]
    if isinstance(by_category, dict):
        for category, count in sorted(by_category.items()):
            click.echo(f"{category}\t{count}" if plain else f"{category}: {count}")


def _echo_orchestration_items(items: object, *, plain: bool = False) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if isinstance(item, dict):
            click.echo(_plain_orchestration_item_line(item) if plain else _orchestration_item_line(item))


def _orchestration_item_line(item: dict[str, object]) -> str:
    return (
        f"{item['taskId']} [{item['queueCategory']}] v{item['version']} "
        f"{item['title']} ({item['path']})"
    )


def _plain_orchestration_item_line(item: dict[str, object]) -> str:
    """Tab-separated queue record for agents that parse orchestration output.

    Fields: taskId, queueCategory, version, effectiveStatus, leaseOwner, path, title.
    """
    fields = [
        item["taskId"],
        item["queueCategory"],
        item["version"],
        item["effectiveStatus"],
        item["leaseOwner"] or "",
        item["path"],
        item["title"],
    ]
    return "\t".join("" if field is None else str(field) for field in fields)


def _echo_daemon_status(record: RuntimeRecord, *, as_json: bool) -> None:
    status = runtime_status(record)
    if as_json:
        click.echo(json.dumps(status, sort_keys=True))
        return
    click.echo(f"Daemon running at {status['endpoint']}")
    click.echo(f"PID: {status['pid']}")
    click.echo(f"Log: {status['log_path']}")
    known_projects = status.get("known_projects")
    if isinstance(known_projects, list) and known_projects:
        click.echo("Known projects:")
        for project_root in known_projects:
            click.echo(f"  - {project_root}")
    locks = status.get("locks")
    if isinstance(locks, list) and locks:
        active_count = sum(1 for lock in locks if isinstance(lock, dict) and lock.get("active") is True)
        click.echo(f"Locks: {active_count} active / {len(locks)} tracked")


def _format_task_metadata_lines(task_record: TaskRecord) -> list[str]:
    lines: list[str] = []
    milestone = task_record.parsed.frontmatter.get("milestone")
    references = _frontmatter_string_list(task_record, "references")
    documentation = _frontmatter_string_list(task_record, "documentation")
    modified_files = _frontmatter_string_list(task_record, "modified_files")
    if milestone:
        lines.append(f"Milestone: {milestone}")
    if references:
        lines.append(f"References: {', '.join(references)}")
    if documentation:
        lines.append(f"Documentation: {', '.join(documentation)}")
    if modified_files:
        lines.append(f"Modified files: {', '.join(modified_files)}")
    return lines


def _frontmatter_string_list(task_record: TaskRecord, key: str) -> list[str]:
    value = task_record.parsed.frontmatter.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _document_metadata(document_type: str | None, tags: tuple[str, ...]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if document_type is not None:
        metadata["type"] = document_type
    normalized_tags = _split_csv_values(tags)
    if normalized_tags:
        metadata["tags"] = normalized_tags
    return metadata


def _split_csv_values(values: tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if item:
                normalized.append(item)
    return normalized


def _search_result_types(values: tuple[str, ...]) -> set[str] | None:
    if not values:
        return None
    allowed_types = {"task", "document", "decision"}
    supported = ", ".join(sorted(allowed_types))
    selected: set[str] = set()
    unsupported: list[str] = []
    for value in _split_csv_values(values):
        normalized = value.casefold()
        if normalized not in allowed_types:
            unsupported.append(value)
            click.echo(f"Ignoring unsupported type '{value}'. Supported: {supported}", err=True)
            continue
        selected.add(normalized)
    if not selected:
        if not unsupported:
            # `--type ""` names no type at all -- the shape `--type "$TYPES"`
            # takes when the variable is unset. There is nothing to reject, so
            # treat it as "no filter" rather than a usage error.
            return None
        # Every requested type was unsupported: searching nothing and exiting 0
        # would look like "no matches", so fail as a usage error instead.
        rejected = ", ".join(f"'{value}'" for value in unsupported)
        raise click.UsageError(f"No supported --type values: {rejected}. Supported types: {supported}.")
    return selected


def _priority_filter(priority: str | None) -> str | None:
    if priority is None:
        return None
    normalized = priority.strip().casefold()
    if not normalized:
        return None
    valid_priorities = {"high", "medium", "low"}
    if normalized not in valid_priorities:
        raise click.ClickException(
            f"Invalid priority: {priority}\nValid values are: high, medium, low"
        )
    return normalized


def _parse_ordinal(ordinal: str | None) -> int | float | None:
    try:
        return normalize_ordinal_value(ordinal)
    except TaskMutationError as exc:
        raise click.ClickException(str(exc)) from exc


def _is_completed_status(status: str) -> bool:
    # Whole-status match so "Not Done"/"Incomplete" are never swept into
    # completed/ by cleanup.
    normalized = "".join(character for character in status.casefold() if character.isalnum())
    return normalized in {"done", "complete", "completed"}


def _bool_text(value: bool) -> str:
    return "true" if value else "false"
