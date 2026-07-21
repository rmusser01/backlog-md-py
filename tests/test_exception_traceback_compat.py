"""Exception classes must tolerate ``exc.__traceback__`` assignment.

Click's ``augment_usage_errors`` and ``contextlib._GeneratorContextManager.__exit__``
assign ``exc.__traceback__ = traceback`` in pure Python when an exception
propagates through them. A ``@dataclass(frozen=True)`` exception rejects that
assignment with ``FrozenInstanceError``, turning any parse/orchestration error
raised inside a CLI command into an unrelated crash (see the ``backlog-py board``
traceback ending in ``cannot assign to field '__traceback__'``).
"""

from __future__ import annotations

import contextlib

import pytest

from backlog_py.markdown.task_parser import TaskMarkdownParseError
from backlog_py.orchestration.models import (
    OrchestrationError,
    OrchestrationIdempotencyConflict,
    OrchestrationLeaseConflict,
    OrchestrationPolicyError,
    OrchestrationTransitionError,
    OrchestrationValidationError,
    OrchestrationVersionConflict,
    RunHistoryParseError,
    TaskSplitError,
)


@contextlib.contextmanager
def _passthrough():
    # Re-raising through a generator context manager makes contextlib restore
    # the original traceback via ``exc.__traceback__ = traceback``.
    yield


EXCEPTION_INSTANCES = [
    TaskMarkdownParseError(code="invalid_frontmatter", message="bad yaml"),
    OrchestrationIdempotencyConflict(idempotency_key="key", message="conflict"),
    RunHistoryParseError(code="bad_event", message="bad event", location="runs.jsonl:3"),
    OrchestrationError("failed"),
    OrchestrationPolicyError("policy"),
    OrchestrationValidationError("invalid", details={"code": "example"}),
    OrchestrationVersionConflict("version"),
    OrchestrationLeaseConflict("lease"),
    OrchestrationTransitionError("transition"),
    TaskSplitError("split"),
]


@pytest.mark.parametrize("exc", EXCEPTION_INSTANCES, ids=lambda e: type(e).__name__)
def test_exception_survives_contextmanager_reraise(exc):
    with pytest.raises(type(exc)) as caught:
        with _passthrough():
            raise exc
    assert caught.value is exc


@pytest.mark.parametrize("exc", EXCEPTION_INSTANCES, ids=lambda e: type(e).__name__)
def test_exception_allows_traceback_assignment(exc):
    exc.__traceback__ = None
