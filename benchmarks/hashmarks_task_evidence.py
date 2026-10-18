"""Single consumer compatibility owner for Hashmarks task-evidence packets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TASK_EVIDENCE_SCHEMAS = frozenset(
    {
        "hashmarks.task-evidence.v2",
        "hashmarks.task-evidence.v3",
    }
)


def is_task_evidence_packet(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema") in TASK_EVIDENCE_SCHEMAS
    )


def retrieval_candidate_symbol(candidate: object) -> str | None:
    if not isinstance(candidate, Mapping):
        return None
    value = (
        candidate.get("symbol")
        or candidate.get("qualname")
        or candidate.get("name")
    )
    if not value:
        return None
    return str(value).rsplit(".", 1)[-1]


def retrieval_candidate_matches(
    candidate: object,
    expected: Mapping[str, str],
) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    return (
        candidate.get("path") == expected.get("path")
        and retrieval_candidate_symbol(candidate) == expected.get("symbol")
    )


def task_evidence_schema(value: Any) -> str | None:
    if not is_task_evidence_packet(value):
        return None
    schema = value.get("schema")
    return str(schema) if schema is not None else None
