"""Observation-only timing of Hashmarks' scoped semantic relationship evidence.

Reads immutable ATIF linked tool returns, never agent prose, cognition, or a
live Hashmarks instance. A returned producer claim is not proven model use.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from benchmarks.harness.information_timing import _decode, _opaque_mcp_text
from benchmarks.tool_routing import (
    DISCOVERY_CLASSES,
    NATIVE_SEARCH,
    SHELL,
    TOOL_ROUTER,
    classify_call,
    matches_subject_operation,
)

SCHEMA = "agentscookbook.harbor-relationship-scope-timing.v1"
OUTGOING = "exact-owner-scip-definition-outgoing"
ASSOCIATED = "explicit-producer-claims-associated-with-owner"
MAX_OBSERVATION_BYTES = 262_144
MAX_STEPS = 20_000
MAX_CALLS = 10_000


def unavailable_relationship_scope(reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "qualified": False,
        "reason": reason,
        "state": "UNKNOWN",
        "arrival_timing": "UNKNOWN",
        "detail_followthrough": "UNKNOWN",
        "summary_count_scope": None,
        "summary_count": None,
        "associated_count_scope": None,
        "associated_count": None,
        "captured_semantic_record_sha256": None,
        "scoped_return_count": None,
        "first_semantic_return_step": None,
        "first_native_discovery_step": None,
        "first_exact_detail_request_step": None,
        "native_search_before_return": None,
        "native_search_after_return": None,
        "native_search_same_step": None,
        "native_search_observability": "UNKNOWN",
        "observed_use_proven": False,
        "agent_attention_proven": False,
        "causal_influence_claimed": False,
    }


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 100_000 else None


def _semantic_record(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    row = value.get("semantic_relationships")
    owner = value.get("ownership")
    if not isinstance(row, Mapping) or not isinstance(owner, Mapping):
        return None
    proof = owner.get("owner")
    if (
        owner.get("status") != "resolved"
        or owner.get("proof_scope_complete") is not True
        or owner.get("authority") != "repository-ownership-only"
        or not isinstance(proof, Mapping)
    ):
        return None
    path, qualname = proof.get("path"), proof.get("qualname")
    if not isinstance(path, str) or not isinstance(qualname, str):
        return None
    subject = f"{path}::{qualname}"
    summary_scope = row.get("observed_relationship_count_scope")
    associated_scope = row.get("associated_observed_relationship_count_scope")
    summary = _count(row.get("observed_relationship_count"))
    associated = _count(row.get("associated_observed_relationship_count"))
    evidence = row.get("evidence")
    if (
        row.get("subject") != subject
        or summary_scope not in (OUTGOING, ASSOCIATED)
        or associated_scope != ASSOCIATED
        or summary is None
        or associated is None
        or not isinstance(evidence, Mapping)
        or _count(evidence.get("observed_relationship_count")) != associated
        or row.get("negative_evidence_admissible") is not False
        or evidence.get("negative_evidence_admissible") is not False
    ):
        return None
    if summary_scope == ASSOCIATED and summary != associated:
        return None
    if summary_scope == OUTGOING and row.get("observation_state") not in (
        "direct-claims-observed",
        "definition-observed-no-direct-claims",
    ):
        return None
    if summary_scope == OUTGOING and (
        (summary == 0) != (row["observation_state"] == "definition-observed-no-direct-claims")
    ):
        return None
    try:
        encoded = json.dumps(
            {"ownership": owner, "semantic_relationships": row},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    return {
        "subject": subject,
        "summary_count_scope": summary_scope,
        "summary_count": summary,
        "associated_count_scope": associated_scope,
        "associated_count": associated,
        "captured_semantic_record_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _unwrap(value: object, depth: int = 0) -> tuple[str, dict[str, Any] | None]:
    """Follow only explicit MCP envelopes, not arbitrary nested objects."""
    if depth > 8:
        return "unreadable", None
    if isinstance(value, str):
        decoded = _decode(value)
        return _unwrap(decoded, depth + 1) if decoded is not None else ("unreadable", None)
    if isinstance(value, list):
        if len(value) > 32:
            return "unreadable", None
        outcomes = [_unwrap(row, depth + 1) for row in value]
        if any(state == "unreadable" for state, _ in outcomes):
            return "unreadable", None
        records = [row for state, row in outcomes if state == "record"]
        if records and any(row != records[0] for row in records):
            return "unreadable", None
        return ("record", records[0]) if records else ("absent", None)
    if not isinstance(value, dict):
        return "unreadable", None
    if (
        value.get("isError") is True
        or value.get("status") in ("error", "failed", "denied", "unavailable")
        or ("error" in value and value["error"] not in (None, ""))
    ):
        return "unreadable", None
    if "semantic_relationships" in value:
        record = _semantic_record(value)
        return ("record", record) if record is not None else ("unreadable", None)
    if value.get("type") == "text":
        return _unwrap(value.get("text"), depth + 1)
    fields = [
        name for name in ("structuredContent", "result", "data", "content")
        if name in value
    ]
    if not fields:
        return "absent", None
    outcomes = [_unwrap(value[name], depth + 1) for name in fields]
    if any(state == "unreadable" for state, _ in outcomes):
        return "unreadable", None
    records = [row for state, row in outcomes if state == "record"]
    if records and any(row != records[0] for row in records):
        return "unreadable", None
    return ("record", records[0]) if records else ("absent", None)


def _steps(atif: object) -> tuple[list[dict[str, Any]], dict[str, tuple[object, int]]] | None:
    if (
        not isinstance(atif, dict)
        or not isinstance(atif.get("schema_version"), str)
        or not atif["schema_version"].startswith("ATIF-v")
        or not isinstance(atif.get("steps"), list)
        or not 0 < len(atif["steps"]) <= MAX_STEPS
    ):
        return None
    calls: list[dict[str, Any]] = []
    linked: dict[str, tuple[object, int]] = {}
    seen: set[str] = set()
    for step_index, step in enumerate(atif["steps"], start=1):
        if not isinstance(step, dict):
            return None
        observation = step.get("observation")
        if observation is not None and not isinstance(observation, dict):
            return None
        if isinstance(observation, dict):
            results = observation.get("results", [])
            if not isinstance(results, list):
                return None
            for result in results:
                if not isinstance(result, dict):
                    return None
                call_id = result.get("source_call_id")
                if (
                    not isinstance(call_id, str)
                    or not call_id
                    or call_id in linked
                    or "content" not in result
                    or len(linked) >= MAX_CALLS
                ):
                    return None
                linked[call_id] = (result["content"], step_index)
        tool_calls = step.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            return None
        for call in tool_calls:
            if not isinstance(call, dict):
                return None
            name, call_id = call.get("function_name"), call.get("tool_call_id")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(call_id, str)
                or not call_id
                or call_id in seen
                or len(seen) >= MAX_CALLS
            ):
                return None
            seen.add(call_id)
            arguments = call.get("arguments")
            arguments = arguments if isinstance(arguments, dict) else {}
            calls.append({
                "name": name,
                "call_id": call_id,
                "step": step_index,
                "arguments": arguments,
                "category": classify_call(name, arguments, subject="hashmarks"),
            })
    if set(linked) - seen:
        return None  # An uninvoked tool cannot supply a valid returned observation.
    if any(
        linked[call["call_id"]][1] < call["step"]
        for call in calls
        if call["call_id"] in linked
    ):
        return None  # Even unrelated tool results may not precede invocation.
    return calls, linked


def project_relationship_scope_timing(trajectory: Path) -> dict[str, Any]:
    """Describe arrival and explicit downstream requests, not model attention."""
    try:
        atif = json.loads(trajectory.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unavailable_relationship_scope("atif-unavailable")
    extracted = _steps(atif)
    if extracted is None:
        return unavailable_relationship_scope("atif-order-or-link-invalid")
    calls, linked = extracted
    selected = [
        call for call in calls
        if matches_subject_operation(
            call["name"], subject="hashmarks", operation="task_evidence"
        )
    ]
    if not selected:
        return {
            **unavailable_relationship_scope("task-evidence-never-invoked"),
            "qualified": True,
            "state": "NEVER_INVOKED",
            "arrival_timing": "NO_SEMANTIC_RETURN",
            "detail_followthrough": "NO_SEMANTIC_RETURN",
        }
    observed: list[tuple[int, dict[str, Any]]] = []
    for call in selected:
        item = linked.get(call["call_id"])
        if item is None:
            return unavailable_relationship_scope("missing-task-evidence-result")
        raw, return_step = item
        if return_step < call["step"]:
            return unavailable_relationship_scope("observation-precedes-call")
        try:
            if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_OBSERVATION_BYTES:
                return unavailable_relationship_scope("subject-result-oversized")
        except (TypeError, ValueError):
            return unavailable_relationship_scope("invalid-subject-result")
        if _opaque_mcp_text(raw):
            return unavailable_relationship_scope("unstructured-subject-result")
        state, record = _unwrap(raw)
        if state == "unreadable":
            return unavailable_relationship_scope("invalid-semantic-observation")
        if record is not None:
            observed.append((return_step, record))
    if not observed:
        return {
            **unavailable_relationship_scope("no-scoped-semantic-return"),
            "qualified": True,
            "state": "NO_SCOPE_RECORD",
            "arrival_timing": "NO_SEMANTIC_RETURN",
            "detail_followthrough": "NO_SEMANTIC_RETURN",
        }
    # The pair report describes one scoped subject. Never choose the first
    # convenient claim when later observed packets disagree about that subject,
    # its count scopes, or the underlying owner/producer evidence.
    if any(record != observed[0][1] for _, record in observed[1:]):
        return unavailable_relationship_scope("conflicting-scoped-semantic-returns")
    observed.sort(key=lambda row: row[0])
    first_step, first = observed[0]
    native_steps = [
        call["step"] for call in calls if call["category"] in DISCOVERY_CLASSES
    ]
    first_native = min(native_steps) if native_steps else None
    opaque_router_steps = [
        call["step"] for call in calls if call["category"] == TOOL_ROUTER
    ]
    opaque_tool_steps = [
        call["step"] for call in calls
        if call["category"] in (TOOL_ROUTER, SHELL)
    ]
    # A generic router can conceal native exploration or a detail request.
    # Positive earlier discovery remains observable, but a first-choice/no-
    # discovery conclusion cannot be inferred across a hidden nested call.
    arrival = (
        "AFTER_NATIVE_DISCOVERY"
        if first_native is not None and first_native < first_step
        else "SAME_STEP_UNORDERED"
        if first_native == first_step
        else "UNKNOWN_OPAQUE_ROUTER"
        if any(step <= first_step for step in opaque_router_steps)
        else "BEFORE_NATIVE_DISCOVERY"
        if first_native is not None
        else "UNKNOWN_OPAQUE_ROUTER"
        if opaque_router_steps
        else "NO_NATIVE_DISCOVERY"
    )
    exact_requests = [
        call["step"] for call in calls
        if matches_subject_operation(
            call["name"], subject="hashmarks", operation="structural_locality"
        )
        and call["arguments"].get("target") == first["subject"]
        and call["arguments"].get("result_mode") == "relationships"
    ]
    after = [step for step in exact_requests if step > first_step]
    followthrough = (
        "EXACT_DETAIL_REQUEST_AFTER_RETURN" if after
        else "SAME_STEP_UNORDERED" if first_step in exact_requests
        else "UNKNOWN_OPAQUE_DETAIL_REQUEST"
        if any(step >= first_step for step in opaque_tool_steps)
        else "NO_EXACT_DETAIL_REQUEST_OBSERVED"
    )
    return {
        **unavailable_relationship_scope(""),
        **first,
        "scoped_return_count": len(observed),
        "qualified": True,
        "reason": None,
        "state": "SCOPED_SEMANTIC_RETURN",
        "arrival_timing": arrival,
        "detail_followthrough": followthrough,
        "first_semantic_return_step": first_step,
        "first_native_discovery_step": first_native,
        "first_exact_detail_request_step": min(after) if after else None,
        "native_search_before_return": sum(
            call["category"] == NATIVE_SEARCH and call["step"] < first_step
            for call in calls
        ),
        "native_search_after_return": sum(
            call["category"] == NATIVE_SEARCH and call["step"] > first_step
            for call in calls
        ),
        "native_search_same_step": sum(
            call["category"] == NATIVE_SEARCH and call["step"] == first_step
            for call in calls
        ),
        "native_search_observability": (
            "PARTIAL_OPAQUE_TOOL"
            if opaque_tool_steps
            else "EXPLICIT_TOOL_CALLS_ONLY"
        ),
    }
