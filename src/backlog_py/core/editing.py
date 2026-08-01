"""Shared interactive-editor flow for the CLI and the TUI.

Both surfaces let the user edit a task in ``$EDITOR`` without holding the
project write lock for the whole session: the lock is process-wide and every
other writer gives up after five seconds, so an open editor would otherwise
stall the project. The user edits a copy and the lock is taken only to apply it.

The logic lives here rather than in either surface because it decides whether to
**delete bytes the user typed**. It was previously implemented twice, and the two
copies had to be fixed for the same four defects independently — the second only
because a reviewer happened to read it.

The contract, which every branch below upholds: *the copy is deleted only once
the user's bytes are in the task file, or once it is certain they authored
nothing.* Every other outcome keeps the copy and raises :class:`EditorAbort`
naming its path, so the caller can put that path in front of the user.

This module deliberately imports neither ``click`` nor ``textual``: each surface
maps :class:`EditorAbort` onto its own error convention.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from backlog_py.security.paths import assert_path_within_base


# Below this, an editor that reports "done" without changing anything almost
# certainly did not wait for the user — GUI editors return as soon as they hand
# the file to an already-running instance. This only selects the wording; it is
# never used to decide whether deleting the copy is safe, because a slow machine
# would then silently destroy work.
NON_BLOCKING_EDITOR_SECONDS = 0.5


class EditorAbort(RuntimeError):
    """An edit was not applied, and the user's bytes were preserved on disk."""


def copy_is_untouched(scratch_path: Path, original: bytes | None) -> bool:
    """True only when the copy provably holds nothing the user authored.

    An unreadable copy answers ``False``: not being able to tell is not a
    licence to delete it.
    """
    try:
        if not scratch_path.exists():
            return original is None
        return scratch_path.read_bytes() == original
    except OSError:
        return False


def edit_via_scratch_copy(
    path: Path,
    project_root: Path,
    *,
    editor_label: str,
    run_editor: Callable[[Path], None],
    apply_locked: Callable[[Callable[[], None]], None],
) -> None:
    """Edit ``path`` through a copy, taking the lock only to apply the result.

    ``run_editor`` launches the editor on the copy. ``apply_locked`` runs the
    supplied callable while holding the project write lock. Both are injected so
    this module stays free of the CLI and TUI dependency trees.
    """
    # Validate before anything is copied: a path that escapes the project must be
    # rejected while there is still nothing of the user's to lose.
    safe_path = assert_path_within_base(project_root, path)
    original = safe_path.read_bytes() if safe_path.exists() else None

    # Not a TemporaryDirectory: the copy must outlive this function whenever the
    # user's bytes are not safely applied.
    scratch = Path(tempfile.mkdtemp(prefix="backlog-py-edit-"))
    scratch_path = scratch / safe_path.name
    keep_scratch = False
    try:
        if original is not None:
            scratch_path.write_bytes(original)
        # From here the copy is the file the user types into, so it is kept by
        # default and cleared only where its bytes are provably not their work.
        keep_scratch = True

        started = time.monotonic()
        try:
            run_editor(scratch_path)
        except Exception as exc:
            if copy_is_untouched(scratch_path, original):
                keep_scratch = False
                raise
            raise EditorAbort(
                f"{editor_label} failed ({exc}) after saving, so nothing was applied. "
                f"Your edit is preserved at {scratch_path}."
            ) from exc
        elapsed = time.monotonic() - started

        if not scratch_path.exists():
            # Deleting the copy inside the editor is how you abort an edit; it is
            # not a request to write an empty file over the task.
            keep_scratch = False
            return

        edited = scratch_path.read_bytes()
        if edited == original:
            # Unchanged content is never worth applying, and this is the only
            # case where cleanup can race an editor that is still open on the
            # copy: a GUI editor hands the file to a running instance and returns
            # while the user is still typing, whatever the clock says. So the copy
            # is always kept and always reported; elapsed time only picks wording.
            if elapsed < NON_BLOCKING_EDITOR_SECONDS:
                raise EditorAbort(
                    f"{editor_label} returned immediately, so it is probably not waiting for the "
                    f"editor to close. Nothing was applied, and your copy is preserved at "
                    f"{scratch_path} — save into that file and copy it back yourself. Configure a "
                    "blocking editor (for example 'code --wait') to edit tasks in place."
                )
            raise EditorAbort(
                f"{safe_path.name} came back unchanged, so nothing was applied. Your copy is "
                f"preserved at {scratch_path} in case the editor is still open on it; delete it "
                "once you are done with it."
            )

        def apply_edit() -> None:
            # Re-read under the lock: a writer that landed while the editor was
            # open must not be silently overwritten with stale content.
            current = safe_path.read_bytes() if safe_path.exists() else None
            if current != original:
                raise EditorAbort(
                    f"{safe_path.name} changed while it was open in the editor, so your edit was "
                    f"not applied. It is preserved at {scratch_path}."
                )
            # Deliberately a plain write rather than temp-file + os.replace:
            # os.replace swaps in a new inode, which would break hard links and
            # any editor or watcher holding this file open. The durability that
            # atomicity would buy is already covered — the copy is removed only
            # after this returns, so a torn write leaves the user's bytes intact.
            safe_path.write_bytes(edited)

        try:
            apply_locked(apply_edit)
        except EditorAbort:
            raise
        except Exception as exc:
            # A lock timeout, a full disk, anything at all: the edit did not land,
            # so the copy is the only remaining record of it.
            raise EditorAbort(
                f"{safe_path.name} could not be updated ({exc}), so your edit was not applied. "
                f"It is preserved at {scratch_path}."
            ) from exc
        keep_scratch = False
    finally:
        if not keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
