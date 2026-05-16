from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, TypeVar

import click

from backlog_py import __version__
from backlog_py.cli.completion import CompletionInstallError, install_completion
from backlog_py.compat.inventory import load_builtin_inventory
from backlog_py.compat.report import build_compatibility_report
from backlog_py.core.agents import AgentInstructionError, AgentInstructionUpdate, update_agent_instruction_files
from backlog_py.core.board_export import export_board_to_file, update_readme_with_board
from backlog_py.core.decisions import DecisionRecord, DecisionService
from backlog_py.core.documents import DocumentRecord, DocumentService
from backlog_py.core.drafts import DraftService
from backlog_py.core.init import InitProjectError, InitProjectResult, init_project
from backlog_py.core.milestones import MilestoneRecord, MilestoneService
from backlog_py.core.models import BacklogProject
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
    daemon_ensure,
    daemon_start,
    daemon_status,
    daemon_stop,
)
from backlog_py.integration.legacy_shim import install_legacy_mcp_shim
from backlog_py.storage.config import (
    get_config_value,
    get_definition_of_done_defaults,
    replace_definition_of_done_defaults,
    set_config_value,
)
from backlog_py.runtime.locks import with_init_lock, with_project_write_lock
from backlog_py.runtime.state import RuntimeRecord, runtime_status
from backlog_py.storage.project import discover_project

T = TypeVar("T")


@click.group()
@click.option("--cwd", type=click.Path(path_type=Path), default=None, help="Backlog project directory.")
@click.version_option(__version__, prog_name="backlog-py")
@click.pass_context
def main(ctx: click.Context, cwd: Path | None) -> None:
    """Python compatibility clone of Backlog.md."""
    ctx.obj = {"cwd": cwd}


@main.command("init")
@click.argument("project_name", required=False)
@click.option("--defaults", is_flag=True, help="Use non-interactive default settings.")
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
    backlog_dir: str,
    task_prefix: str,
    config_location: str,
    agent_instructions: bool,
) -> None:
    """Initialize a Backlog.md project with non-interactive defaults."""
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
    if args == ("list",):
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
    if len(args) != 1:
        raise click.UsageError("Missing task id.")
    task_id = args[0]
    task_record = _repository(ctx).get_task(task_id)
    click.echo(_format_task_detail(task_record, plain=plain))


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
            _format_document_line(document)
            for document in _document_service(ctx).search_documents(query)
        )
    if "decision" in selected_types:
        lines.extend(
            _format_decision_line(decision)
            for decision in _decision_service(ctx).search_decisions(query)
        )
    if limit is not None:
        lines = lines[: max(limit, 0)]
    for line in lines:
        click.echo(line)


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
    for status, tasks in _repository(ctx).board().items():
        click.echo(f"{status}:")
        for task_record in tasks:
            click.echo(f"  {_format_task_line(task_record, plain=True)}")


@main.command("overview")
@click.pass_context
def overview_command(ctx: click.Context) -> None:
    """Print a deterministic project summary."""
    project = _project(ctx)
    repository = _repository(ctx)
    board = repository.board()
    active_count = sum(len(tasks) for tasks in board.values())
    completed_count = len(repository.list_completed_tasks())

    click.echo(f"Project: {project.config.project_name}")
    click.echo(f"Active tasks: {active_count}")
    click.echo(f"Completed tasks: {completed_count}")
    click.echo(f"Total tasks: {active_count + completed_count}")
    click.echo("")
    click.echo("Statuses:")
    for status, tasks in board.items():
        click.echo(f"  {status}: {len(tasks)}")


@main.command("cleanup")
@click.pass_context
def cleanup_command(ctx: click.Context) -> None:
    """Move active Done tasks into backlog/completed."""
    done_tasks = _locked_write(ctx, "cleanup_complete_done", lambda: _cleanup_completed_tasks(ctx))
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
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Loopback host to bind.")
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int, help="Loopback port to bind.")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
def daemon_start_command(host: str, port: int, as_json: bool) -> None:
    """Start the singleton daemon."""
    status = daemon_start(host=host, port=port)
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
@click.option("--host", default=DEFAULT_HOST, show_default=True, help="Loopback host to bind.")
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int, help="Loopback port to bind.")
def daemon_run_command(foreground: bool, host: str, port: int) -> None:
    """Run the singleton daemon service."""
    if not foreground:
        raise click.UsageError("Usage: daemon run --foreground")
    from backlog_py.daemon.service import run_foreground_service

    run_foreground_service(host=host, port=port)


@main.group("compat")
def compat_group() -> None:
    """Inspect Backlog.md compatibility coverage."""


@compat_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="Print machine-readable JSON output.")
def compat_status_command(as_json: bool) -> None:
    """Print implemented and deferred compatibility coverage."""
    report = build_compatibility_report(load_builtin_inventory())
    if as_json:
        click.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    summary = report["summary"]
    click.echo(f"agentCutoverReady: {_bool_text(report['agent_cutover_ready'])}")
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


@main.group("config")
def config_group() -> None:
    """Inspect Backlog.md configuration."""


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
    try:
        value = get_config_value(_project(ctx), key)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_format_config_value(value))


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
    click.echo(_format_task_detail(draft, plain=plain))


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
    return discover_project(Path.cwd(), explicit_cwd=_explicit_cwd(ctx))


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
    agent_instructions: bool,
) -> tuple[InitProjectResult, list[AgentInstructionUpdate]]:
    result = init_project(
        _cwd(ctx),
        project_name=project_name,
        backlog_dir=backlog_dir,
        task_prefix=task_prefix,
        config_location=config_location,
    )
    instruction_updates = update_agent_instruction_files(result.project) if agent_instructions else []
    return result, instruction_updates


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
    return f"{task_record.id} - {task_record.title} ({task_record.status})"


def _format_task_detail(task_record: TaskRecord, *, plain: bool) -> str:
    if plain:
        parts = [
            f"{task_record.id} [{task_record.status}] {task_record.title}",
            "",
            task_record.description or task_record.body.strip(),
        ]
        return "\n".join(parts).rstrip()
    return task_record.raw_source


def _format_document_line(document: DocumentRecord) -> str:
    return f"{document.path_relative} {document.title}".rstrip()


def _format_decision_line(decision: DecisionRecord) -> str:
    return f"{decision.id} [{decision.status}] {decision.title}".rstrip()


def _format_milestone_line(milestone: MilestoneRecord) -> str:
    return f"{milestone.name} {milestone.path_relative}".rstrip()


def _format_config_value(value: object) -> str:
    if isinstance(value, bool):
        return _bool_text(value)
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    if value is None:
        return "null"
    return str(value)


def _echo_daemon_status(record: RuntimeRecord, *, as_json: bool) -> None:
    status = runtime_status(record)
    if as_json:
        click.echo(json.dumps(status, sort_keys=True))
        return
    click.echo(f"Daemon running at {status['endpoint']}")
    click.echo(f"PID: {status['pid']}")
    click.echo(f"Log: {status['log_path']}")


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
    selected: set[str] = set()
    for value in _split_csv_values(values):
        normalized = value.casefold()
        if normalized not in allowed_types:
            supported = ", ".join(sorted(allowed_types))
            click.echo(f"Ignoring unsupported type '{value}'. Supported: {supported}", err=True)
            continue
        selected.add(normalized)
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
    normalized = status.casefold()
    return "done" in normalized or "complete" in normalized


def _bool_text(value: bool) -> str:
    return "true" if value else "false"
