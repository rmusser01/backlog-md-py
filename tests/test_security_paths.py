import shutil
from pathlib import Path

import pytest

from backlog_py.security.paths import PathContainmentError, assert_path_within_base


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
