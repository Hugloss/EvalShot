"""Host-neutral MCP tool result evidence."""

from __future__ import annotations

import json
from typing import Any


_FAILURE_STATUSES = frozenset(
    {
        "cancelled",
        "canceled",
        "error",
        "failed",
        "failure",
        "rejected",
    }
)


def result_bytes(value: object) -> int | None:
    """Return exact serialized result size when a captured result is observable."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, (dict, list)):
        return len(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        )
    if isinstance(value, bytes):
        return len(value)
    return None


def tool_result_evidence(
    *,
    operation: str,
    status: object,
    result_present: bool,
    result: object,
    error: object = None,
    basis: str,
) -> dict[str, Any]:
    """Project one subject-tool call into result evidence without retaining payload."""
    normalized_status = (
        status.strip().lower()
        if isinstance(status, str) and status.strip()
        else None
    )
    measured = result_bytes(result) if result_present else None
    error_present = not (
        error is None
        or error is False
        or error == ""
    )
    explicit_failure = (
        normalized_status in _FAILURE_STATUSES
        or error_present
    )
    if explicit_failure:
        outcome = "failed"
    elif measured is not None and measured > 0:
        outcome = "successful-result-observed"
    elif measured == 0:
        outcome = "empty-result"
    else:
        outcome = "result-unobserved"

    return {
        "operation": operation,
        "status": normalized_status,
        "result_bytes": measured,
        "error_present": error_present,
        "result_basis": basis,
        "outcome": outcome,
    }
