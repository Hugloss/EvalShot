"""Conservative information-arrival projection for immutable Harbor ATIF traces.

Uses only structured tool-call arguments, linked observations, and a frozen
verifier-owned expected path. Never reads agent messages or reasoning. A tool
return is evidence availability, not evidence of the agent's cognition.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.tool_routing import (
    DISCOVERY_CLASSES,
    NATIVE_READ,
    SUBJECT_REPOSITORY_INTELLIGENCE,
    classify_call,
)

INFORMATION_SCHEMA = "agentscookbook.harbor-information-timing.v1"
PATH_KEYS = frozenset({"path", "file", "file_path", "owner_path", "target_path", "next_read"})
MAX_CONTENT_BYTES = 262144
MAX_TARGETS = 128


def _repository_path(value: object) -> str | None:
    """Require a repository-relative path; do not fuzzy-match or use basenames."""
    if not isinstance(value, str):
        return None
    path = value.strip().replace("\\", "/")
    if (
        not path
        or len(path) > 500
        or path.startswith("/")
        or ":" in path
        or "\n" in path
        or "\r" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        return None
    return path


def _decode(value: object) -> object | None:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_CONTENT_BYTES:
        return None
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _paths(value: object) -> set[str]:
    """Extract only named path fields, including JSON-encoded MCP text blocks."""
    found: set[str] = set()

    def visit(item: object, depth: int = 0) -> None:
        if depth >= 16 or len(found) >= MAX_TARGETS:
            return
        if isinstance(item, str):
            decoded = _decode(item)
            if decoded is not None:
                visit(decoded, depth + 1)
        elif isinstance(item, list):
            for child in item[:MAX_TARGETS]:
                visit(child, depth + 1)
        elif isinstance(item, dict):
            for key, child in list(item.items())[:MAX_TARGETS]:
                if str(key).lower() in PATH_KEYS:
                    path = _repository_path(child)
                    if path is not None:
                        found.add(path)
                else:
                    visit(child, depth + 1)

    visit(value)
    return found


def _opaque_mcp_text(value: object, *, depth: int = 0) -> bool:
    """An unparseable text block cannot support a negative path-evidence claim."""
    if depth >= 16:
        return True
    if isinstance(value, list):
        return any(_opaque_mcp_text(item, depth=depth + 1) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("type") == "text" and isinstance(value.get("text"), str):
        if _decode(value["text"]) is None:
            return True
    return any(
        _opaque_mcp_text(child, depth=depth + 1)
        for child in value.values()
        if isinstance(child, (list, dict))
    )


def unavailable_information(reason: str) -> dict[str, Any]:
    return {
        "schema": INFORMATION_SCHEMA,
        "qualified": False,
        "reason": reason,
        "target_alignment": "UNKNOWN",
        "arrival_timing": "UNKNOWN",
        "native_read_followthrough": "UNKNOWN",
        "first_subject_result_ordinal": None,
        "first_subject_result_step": None,
        "first_native_discovery_step": None,
        "first_oracle_target_ordinal": None,
        "first_alternate_target_ordinal": None,
        "first_native_discovery_ordinal": None,
        "first_oracle_read_after_result_ordinal": None,
        "first_alternate_read_after_result_ordinal": None,
        "agent_message_consumed": False,
        "reasoning_content_consumed": False,
        "causal_influence_claimed": False,
    }


def project_information_timing(
    trajectory: Path,
    *,
    expected_path: object,
) -> dict[str, Any]:
    """Grade returned *path candidates* against verifier evidence, not prose.

    An incomplete/ambiguous call-to-observation link produces UNKNOWN rather
    than an unsupported assertion that information was absent.
    """
    oracle = _repository_path(expected_path)
    if oracle is None:
        return unavailable_information("no-verifier-owned-path-oracle")
    try:
        atif = json.loads(trajectory.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unavailable_information("atif-unavailable")
    if (
        not isinstance(atif, dict)
        or not isinstance(atif.get("schema_version"), str)
        or not atif["schema_version"].startswith("ATIF-v")
        or not isinstance(atif.get("steps"), list)
        or not atif["steps"]
    ):
        return unavailable_information("atif-invalid-or-empty")

    steps = atif["steps"]
    linked: dict[str, object] = {}
    duplicated_links = False
    for step_index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            return unavailable_information("malformed-step")
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        rows = observation.get("results")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            call_id = row.get("source_call_id")
            if isinstance(call_id, str) and call_id:
                if call_id in linked:
                    duplicated_links = True
                linked[call_id] = (row.get("content"), step_index)
    if duplicated_links:
        return unavailable_information("duplicate-observation-link")

    calls: list[tuple[int, str, object, object, int, int | None]] = []
    call_ids: set[str] = set()
    for step_index, step in enumerate(steps, start=1):
        if "tool_calls" not in step:
            continue
        tool_calls = step["tool_calls"]
        if not isinstance(tool_calls, list):
            return unavailable_information("malformed-tool-call-list")
        for call in tool_calls:
            if not isinstance(call, dict) or not isinstance(call.get("function_name"), str):
                return unavailable_information("malformed-tool-call")
            name = call["function_name"]
            if not name.strip():
                return unavailable_information("missing-tool-name")
            call_id = call.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                if call_id in call_ids:
                    return unavailable_information("duplicate-tool-call-id")
                call_ids.add(call_id)
            arguments = call.get("arguments")
            arguments = arguments if isinstance(arguments, dict) else {}
            linked_result = (
                linked.get(call_id) if isinstance(call_id, str) else None
            )
            calls.append(
                (
                    len(calls) + 1,
                    classify_call(name, arguments, subject="hashmarks"),
                    arguments,
                    linked_result[0] if linked_result is not None else None,
                    step_index,
                    linked_result[1] if linked_result is not None else None,
                )
            )

    first_native_call = next(
        (
            (ordinal, step_index)
            for ordinal, category, _, _, step_index, _ in calls
            if category in DISCOVERY_CLASSES
        ),
        None,
    )
    first_native = first_native_call[0] if first_native_call else None
    first_native_step = first_native_call[1] if first_native_call else None
    subject_calls = [
        (ordinal, response, call_step, result_step)
        for ordinal, category, _, response, call_step, result_step in calls
        if category == SUBJECT_REPOSITORY_INTELLIGENCE
    ]
    if not subject_calls:
        return {
            **unavailable_information("subject-never-invoked"),
            "qualified": True,
            "target_alignment": "NO_SUBJECT_RESULT",
            "arrival_timing": "NO_SUBJECT_RESULT",
            "native_read_followthrough": "NO_SUBJECT_RESULT",
            "first_native_discovery_ordinal": first_native,
            "first_native_discovery_step": first_native_step,
        }
    if any(
        response is None or result_step is None
        for _, response, _, result_step in subject_calls
    ):
        return unavailable_information("missing-subject-observation-link")
    if any(
        result_step < call_step
        for _, _, call_step, result_step in subject_calls
        if result_step is not None
    ):
        return unavailable_information("observation-precedes-call")
    decoded = [
        (ordinal, result_step, _decode(response))
        for ordinal, response, _, result_step in subject_calls
    ]
    if any(
        content is None or _opaque_mcp_text(content)
        for _, _, content in decoded
    ):
        return unavailable_information("unstructured-subject-observation")

    exposed: list[tuple[int, int, set[str]]] = [
        (ordinal, result_step, _paths(content))
        for ordinal, result_step, content in decoded
        if content is not None and result_step is not None
    ]
    exposed.sort(key=lambda event: (event[1], event[0]))
    oracle_ordinals = [i for i, _, paths in exposed if oracle in paths]
    alternate_ordinals = [i for i, _, paths in exposed if paths - {oracle}]
    first_result, first_result_step, _ = exposed[0]
    first_oracle = min(oracle_ordinals) if oracle_ordinals else None
    first_alternate = min(alternate_ordinals) if alternate_ordinals else None
    if first_oracle is not None and first_alternate is not None:
        alignment = "MIXED_TARGETS"
    elif first_oracle is not None:
        alignment = "ORACLE_TARGET_ONLY"
    elif first_alternate is not None:
        alignment = "ALTERNATE_TARGETS_ONLY"
    else:
        alignment = "NO_STRUCTURED_PATH_TARGETS"
    if first_native_step == first_result_step:
        # ATIF does not order an observation versus another call in one step.
        return unavailable_information("native-discovery-observation-same-step")
    arrival = (
        "NO_NATIVE_DISCOVERY"
        if first_native_step is None
        else "BEFORE_NATIVE_DISCOVERY"
        if first_result_step < first_native_step
        else "AFTER_NATIVE_DISCOVERY"
    )

    oracle_read: int | None = None
    alternate_read: int | None = None
    ambiguous_same_step_read = False
    opaque_native_read = False
    for ordinal, category, arguments, _, call_step, _ in calls:
        if category != NATIVE_READ:
            continue
        read_paths = _paths(arguments)
        if not read_paths:
            opaque_native_read = True
        earlier_oracle = any(
            result_step < call_step and oracle in paths
            for _, result_step, paths in exposed
        )
        earlier_alternates = {
            path
            for _, result_step, paths in exposed
            if result_step < call_step
            for path in paths
            if path != oracle
        }
        concurrent_paths = {
            path
            for _, result_step, paths in exposed
            if result_step == call_step
            for path in paths
        }
        if read_paths & concurrent_paths:
            ambiguous_same_step_read = True
        if oracle_read is None and earlier_oracle and oracle in read_paths:
            oracle_read = ordinal
        if alternate_read is None and read_paths & earlier_alternates:
            alternate_read = ordinal
    followthrough = (
        "BOTH_PATHS_READ"
        if oracle_read is not None and alternate_read is not None
        else "UNKNOWN_SAME_STEP"
        if ambiguous_same_step_read
        else "ORACLE_PATH_READ"
        if oracle_read is not None
        else "ALTERNATE_PATH_READ"
        if alternate_read is not None
        else "UNKNOWN_NATIVE_READ_ARGUMENTS"
        if opaque_native_read
        else "NO_MATCHING_NATIVE_READ"
    )
    return {
        "schema": INFORMATION_SCHEMA,
        "qualified": True,
        "reason": None,
        "target_alignment": alignment,
        "arrival_timing": arrival,
        "native_read_followthrough": followthrough,
        "subject_result_calls": len(exposed),
        "first_subject_result_ordinal": first_result,
        "first_subject_result_step": first_result_step,
        "first_native_discovery_step": first_native_step,
        "first_oracle_target_ordinal": first_oracle,
        "first_alternate_target_ordinal": first_alternate,
        "first_native_discovery_ordinal": first_native,
        "first_oracle_read_after_result_ordinal": oracle_read,
        "first_alternate_read_after_result_ordinal": alternate_read,
        "agent_message_consumed": False,
        "reasoning_content_consumed": False,
        "causal_influence_claimed": False,
    }
