import shutil
from pathlib import Path

import pytest

from backlog_py.security.paths import (
    PathContainmentError,
    assert_path_within_base,
    assert_trusted_subpath,
)


def test_assert_path_within_base_allows_backlog_child(tmp_path):
    base = tmp_path / "repo" / "backlog"
    task_path = base / "tasks" / "task-2 - New.md"
    base.mkdir(parents=True)

    assert assert_path_within_base(base, task_path) == task_path.resolve()


def test_assert_path_within_base_rejects_parent_traversal(tmp_path):
    base = tmp_path / "repo" / "backlog"
    base.mkdir(parents=True)
    escaped = base / ".." / "outside.md"

    with pytest.raises(PathContainmentError, match="outside allowed base"):
        assert_path_within_base(base, escaped)


def test_assert_path_within_base_rejects_sibling_prefix(tmp_path):
    base = tmp_path / "repo" / "backlog"
    sibling = tmp_path / "repo" / "backlog-other" / "task.md"
    base.mkdir(parents=True)
    sibling.parent.mkdir(parents=True)

    with pytest.raises(PathContainmentError):
        assert_path_within_base(base, sibling)


def test_assert_path_within_base_allows_child_of_symlinked_base(tmp_path):
    """A project reached through a symlink (macOS /tmp, bind mounts) is legitimate."""
    real_root = tmp_path / "real"
    base = real_root / "backlog"
    base.mkdir(parents=True)
    link_root = tmp_path / "linked"
    try:
        link_root.symlink_to(real_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    linked_base = link_root / "backlog"
    task_path = linked_base / "tasks" / "task-1 - Example.md"

    assert assert_path_within_base(linked_base, task_path) == task_path.resolve()


def test_assert_path_within_base_rejects_symlink_inside_base_escaping(tmp_path):
    """A symlink planted inside the tree must not smuggle a path outside it.

    The base itself is trusted caller input (it is where the project was
    discovered, and it may legitimately be reached through a symlink); the
    candidate is what carries untrusted segments, so it is the candidate's
    real location that has to stay inside the base's real location.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("secret", encoding="utf-8")
    base = tmp_path / "repo" / "backlog"
    base.mkdir(parents=True)
    escape = base / "escape.md"
    try:
        escape.symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(PathContainmentError):
        assert_path_within_base(base, escape)


def test_assert_path_within_base_rejects_symlinked_directory_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "repo" / "backlog"
    base.mkdir(parents=True)
    linked_dir = base / "linked"
    try:
        linked_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(PathContainmentError):
        assert_path_within_base(base, linked_dir / "task.md")


def _symlink(link: Path, target: Path, *, directory: bool = True) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")


def test_assert_trusted_subpath_allows_plain_nested_path(tmp_path):
    root = tmp_path / "proj"
    anchor = root / "backlog" / "docs"
    anchor.mkdir(parents=True)

    assert assert_trusted_subpath(root, anchor) == anchor.resolve()


def test_assert_trusted_subpath_allows_missing_directory(tmp_path):
    """Service constructors compute their anchor before the directory exists.

    ``Path.resolve()`` is non-strict, so a component that is simply absent
    resolves to itself and must not be mistaken for a redirected one.
    """
    root = tmp_path / "proj"
    root.mkdir()
    missing = root / "backlog" / "archive" / "milestones"

    assert assert_trusted_subpath(root, missing) == root.resolve() / "backlog" / "archive" / "milestones"


def test_assert_trusted_subpath_allows_root_reached_through_symlink(tmp_path):
    """The macOS /tmp -> /private/tmp case: only the root is resolved, once, up front."""
    real_root = tmp_path / "real"
    (real_root / "backlog" / "docs").mkdir(parents=True)
    link_root = tmp_path / "linked"
    _symlink(link_root, real_root)

    resolved = assert_trusted_subpath(link_root, link_root / "backlog" / "docs")

    assert resolved == (real_root / "backlog" / "docs").resolve()


def test_assert_trusted_subpath_rejects_component_symlinked_to_project_sibling(tmp_path):
    """``backlog/docs -> backlog/decisions`` never leaves the root, and must still be refused.

    Containment alone accepts it: the resolved target is inside the project. But
    it silently redirects every document write into the decisions directory,
    where documents overwrite decision files. Trusting a component requires it to
    be the real directory it names, not merely a pointer to somewhere allowed.
    """
    root = tmp_path / "proj"
    decisions = root / "backlog" / "decisions"
    decisions.mkdir(parents=True)
    docs = root / "backlog" / "docs"
    _symlink(docs, decisions)

    with pytest.raises(PathContainmentError, match="symlink"):
        assert_trusted_subpath(root, docs)


def test_assert_trusted_subpath_rejects_intermediate_symlink_to_project_sibling(tmp_path):
    """The redirect can sit at any depth, not just on the final component."""
    root = tmp_path / "proj"
    real = root / "real"
    (real / "docs").mkdir(parents=True)
    backlog = root / "backlog"
    _symlink(backlog, real)

    with pytest.raises(PathContainmentError):
        assert_trusted_subpath(root, backlog / "docs")


def test_assert_trusted_subpath_rejects_component_symlinked_outside_project(tmp_path):
    root = tmp_path / "proj"
    (root / "backlog").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    docs = root / "backlog" / "docs"
    _symlink(docs, outside)

    with pytest.raises(PathContainmentError, match="outside allowed base"):
        assert_trusted_subpath(root, docs)


def test_assert_trusted_subpath_rejects_candidate_outside_root(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()

    with pytest.raises(PathContainmentError):
        assert_trusted_subpath(root, tmp_path / "elsewhere" / "docs")


def test_config_write_rejects_a_symlinked_backlog_directory(tmp_path):
    """A symlink planted inside the project must not let a config write escape it.

    Anchoring containment on the config file's own parent is vacuous: the parent
    is the symlink, so "is the file inside its own parent" is always true.
    """
    from backlog_py.core.init import init_project
    from backlog_py.storage.config import set_config_value

    project = init_project(tmp_path / "proj", no_git=True).project
    outside = tmp_path / "outside"
    outside.mkdir()

    config_name = project.config_path.name
    shutil.copy(project.config_path, outside / config_name)
    shutil.rmtree(project.backlog_dir)
    try:
        project.backlog_dir.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    before = (outside / config_name).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="outside allowed base"):
        set_config_value(project, "defaultPort", "9999")

    assert (outside / config_name).read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "subdir,service_attr",
    [("docs", "docs_dir"), ("decisions", "decisions_dir"), ("drafts", "drafts_dir")],
)
def test_service_directory_symlink_cannot_redirect_writes(tmp_path, subdir, service_attr):
    """A repo shipping backlog/<subdir> as a symlink must not become the trusted base.

    assert_path_within_base resolves the base, so an attacker-planted directory
    symlink would otherwise make every candidate under it "contained".
    """
    from backlog_py.core.init import init_project

    project = init_project(tmp_path / "proj", no_git=True).project
    outside = tmp_path / "outside"
    outside.mkdir()
    target = project.backlog_dir / subdir
    if target.exists():
        shutil.rmtree(target)
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    if subdir == "docs":
        from backlog_py.core.documents import DocumentService as Service
    elif subdir == "decisions":
        from backlog_py.core.decisions import DecisionService as Service
    else:
        from backlog_py.core.drafts import DraftService as Service

    with pytest.raises(ValueError):
        service = Service(project)
        if subdir == "docs":
            service.create_document("escaped.md", title="Escaped", content="x")
        elif subdir == "decisions":
            service.create_decision(title="Escaped")
        else:
            service.create_draft(title="Escaped")

    assert list(outside.iterdir()) == [], "a write escaped the project through a directory symlink"


def test_docs_symlinked_to_decisions_cannot_cross_contaminate(tmp_path):
    """The reported P1: the redirect target is inside the project, so containment passed.

    ``backlog/docs -> backlog/decisions`` never escapes the root, yet it routes
    every document write into the decisions directory where the two managed
    directories collide and documents overwrite decision files.
    """
    from backlog_py.core.documents import DocumentService
    from backlog_py.core.init import init_project

    project = init_project(tmp_path / "proj", no_git=True).project
    decisions = project.backlog_dir / "decisions"
    decisions.mkdir(parents=True, exist_ok=True)
    (decisions / "decision-1 - Keep.md").write_text("original decision", encoding="utf-8")

    docs = project.backlog_dir / "docs"
    if docs.exists():
        shutil.rmtree(docs)
    _symlink(docs, decisions)

    # Use a name that does not collide, so the only thing that can stop the
    # write is the containment guard rather than an "already exists" check.
    with pytest.raises(ValueError, match="symlink"):
        DocumentService(project).create_document("cross.md", title="Cross", content="x")

    assert [p.name for p in decisions.iterdir()] == ["decision-1 - Keep.md"]


def test_milestone_directory_symlink_cannot_redirect_writes(tmp_path):
    from backlog_py.core.init import init_project
    from backlog_py.core.milestones import MilestoneService

    project = init_project(tmp_path / "proj", no_git=True).project
    outside = tmp_path / "outside"
    outside.mkdir()
    target = project.backlog_dir / "milestones"
    if target.exists():
        shutil.rmtree(target)
    try:
        target.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(ValueError):
        MilestoneService(project).add_milestone(name="Escaped")

    assert list(outside.iterdir()) == [], "a milestone write escaped the project"


def test_task_reads_do_not_follow_a_symlink_out_of_the_project(tmp_path):
    """A planted symlink must not have its contents surfaced as a task.

    Writes are containment-checked, reads were not: `backlog/tasks/leak.md`
    pointing anywhere readable was parsed and surfaced on the board, in
    `task list`, in search, and through the MCP read tools.
    """
    from loguru import logger as loguru_logger

    from backlog_py.core.init import init_project
    from backlog_py.core.repository import ReadOnlyRepository

    project = init_project(tmp_path / "proj", no_git=True).project
    secret = tmp_path / "secret.md"
    secret.write_text(
        "---\nid: TASK-99\ntitle: Exfiltrated\nstatus: To Do\ncreated_date: '2026-01-01'\n---\n\n"
        "## Description\n\ntop secret\n",
        encoding="utf-8",
    )
    tasks_dir = project.backlog_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    try:
        (tasks_dir / "leak.md").symlink_to(secret)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    messages: list[str] = []
    sink = loguru_logger.add(lambda m: messages.append(str(m)), level="WARNING")
    try:
        tasks = ReadOnlyRepository(project, refresh_remote_refs=False).list_tasks()
    finally:
        loguru_logger.remove(sink)

    assert [task.id for task in tasks] == [], "a symlinked file outside the project was surfaced as a task"
    assert any("leak.md" in message for message in messages), "the skip was silent"


def test_task_reads_still_work_for_ordinary_files(tmp_path):
    """The containment check must not make a normal project unreadable."""
    from backlog_py.core.init import init_project
    from backlog_py.core.repository import MutableRepository, ReadOnlyRepository

    project = init_project(tmp_path / "proj", no_git=True).project
    MutableRepository(project, refresh_remote_refs=False).create_task(title="Ordinary")

    tasks = ReadOnlyRepository(project, refresh_remote_refs=False).list_tasks()
    assert [task.title for task in tasks] == ["Ordinary"]
