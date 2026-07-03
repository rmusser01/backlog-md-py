from __future__ import annotations


class NotFoundError(KeyError):
    """Raised when a requested entity (task, draft, decision, document, ...) does not exist.

    Subclasses ``KeyError`` so existing ``except KeyError`` / ``pytest.raises(KeyError)``
    callers keep working, while error-mapping layers can catch this specific type
    instead of a bare ``KeyError`` (which would otherwise mask accidental
    ``dict[missing]`` bugs inside handlers).
    """
