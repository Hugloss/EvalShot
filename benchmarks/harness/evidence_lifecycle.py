"""Fail-closed ATIF evidence lifecycle projection (model-free, observation-only).

A linked tool result is not proof of delivery to a model. ATIF has no
independently attestable model-input boundary. This module deliberately leaves
delivery and cognitive use UNKNOWN, including when assistant prose claims use.
A future host-owned receipt must be bound and independently verified upstream.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from benchmarks.tool_routing import (
    SUBJECT_REPOSITORY_INTELLIGENCE,
    classify_call,
)

SCHEMA = "agentscookbook.evidence-lifecycle.v1"
MAX_STEPS = 20_000
MAX_CALLS = 10_000
MAX_PACKET_BYTES = 262_144


def unavailable_lifecycle(reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "qualified": False,
        "reason": reason,
        "invocation_state": "UNKNOWN",
        "return_state": "UNKNOWN",
        "delivery_state": "UNKNOWN",
        "application_state": "UNKNOWN",
        "model_attention_proven": False,
        "causal_influence_proven": False,
        "invoked_calls": None,
        "returned_packets": None,
        "unreturned_calls": None,
        "packet_refs": [],
        "total_return_bytes": None,
        "presentation_parity": "UNKNOWN",
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _packet(value: object) -> tuple[str, int] | None:
    if isinstance(value, dict) and (
        value.get("isError") is True
        or value.get("status") in ("error", "failed", "denied", "unavailable")
        or ("error" in value and value["error"] not in (None, ""))
    ):
        raise ValueError("error-result-not-evidence")
    if value is None or value == "" or value == [] or value == {}:
        return None
    payload = _canonical(value)
    if len(payload) > MAX_PACKET_BYTES:
        raise ValueError("packet-oversized")
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _decode_envelope(value: object, depth: int = 0) -> object:
    """Decode only explicit MCP envelopes; no fuzzy claim extraction."""
    if depth > 6:
        raise ValueError("envelope-depth")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return value
        return _decode_envelope(parsed, depth + 1)
    if isinstance(value, dict):
        if value.get("isError") is True or value.get("status") in (
            "error", "failed", "denied", "unavailable",
        ):
            raise ValueError("tool-error")
        if value.get("type") == "text" and "text" in value:
            return _decode_envelope(value["text"], depth + 1)
        if "result" in value and len(value) == 1:
            return _decode_envelope(value["result"], depth + 1)
    if isinstance(value, list) and len(value) == 1:
        return _decode_envelope(value[0], depth + 1)
    return value


def _parity(value: object) -> str:
    """Confirm *semantic JSON* parity where structured and text views coexist.

    Opaque/unparseable text cannot prove equivalent presentation. A missing
    second view means parity is not assessed, not a success.
    """
    if not isinstance(value, dict) or not (
        "structuredContent" in value and "content" in value
    ):
        return "NOT_ASSESSED"
    try:
        structured = _decode_envelope(value["structuredContent"])
        text_view = _decode_envelope(value["content"])
        if isinstance(text_view, str):
            return "UNKNOWN"
        return "EQUIVALENT" if _canonical(structured) == _canonical(text_view) else "DIVERGENT"
    except (ValueError, TypeError, OverflowError, RecursionError):
        return "UNKNOWN"


def project_evidence_lifecycle(path: Path) -> dict[str, Any]:
    try:
        atif = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unavailable_lifecycle("atif-unavailable")
    if (
        not isinstance(atif, dict)
        or not isinstance(atif.get("schema_version"), str)
        or not atif["schema_version"].startswith("ATIF-v")
        or not isinstance(atif.get("steps"), list)
        or not 0 < len(atif["steps"]) <= MAX_STEPS
    ):
        return unavailable_lifecycle("atif-invalid")
    calls: dict[str, tuple[int, bool]] = {}
    observations: dict[str, tuple[int, object]] = {}
    for ordinal, step in enumerate(atif["steps"], 1):
        if not isinstance(step, dict):
            return unavailable_lifecycle("malformed-step")
        tool_calls = step.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            return unavailable_lifecycle("malformed-tool-call-list")
        for call in tool_calls:
            if not isinstance(call, dict):
                return unavailable_lifecycle("malformed-tool-call")
            name, call_id = call.get("function_name"), call.get("tool_call_id")
            if (
                not isinstance(name, str) or not name.strip()
                or not isinstance(call_id, str) or not call_id or len(call_id) > 512
                or call_id in calls or len(calls) >= MAX_CALLS
            ):
                return unavailable_lifecycle("invalid-or-duplicate-call")
            arguments = call.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                return unavailable_lifecycle("malformed-call-arguments")
            subject = classify_call(name, arguments or {}, subject="hashmarks") == SUBJECT_REPOSITORY_INTELLIGENCE
            calls[call_id] = (ordinal, subject)
        observation = step.get("observation")
        if observation is not None:
            if not isinstance(observation, dict):
                return unavailable_lifecycle("malformed-observation")
            results = observation.get("results", [])
            if not isinstance(results, list):
                return unavailable_lifecycle("malformed-result-list")
            for result in results:
                if not isinstance(result, dict):
                    return unavailable_lifecycle("malformed-result")
                call_id = result.get("source_call_id")
                if (
                    not isinstance(call_id, str) or not call_id
                    or call_id in observations or len(observations) >= MAX_CALLS
                    or "content" not in result
                ):
                    return unavailable_lifecycle("invalid-or-duplicate-result")
                observations[call_id] = (ordinal, result["content"])
    for call_id, (ordinal, _) in observations.items():
        if call_id not in calls:
            return unavailable_lifecycle("orphan-observation")
        if ordinal < calls[call_id][0]:
            return unavailable_lifecycle("observation-precedes-call")
    selected = [(call_id, at) for call_id, at in calls.items() if at[1]]
    if not selected:
        return {
            **unavailable_lifecycle("subject-never-invoked"),
            "qualified": True, "invocation_state": "NEVER_INVOKED",
            "return_state": "NO_SUBJECT_RETURN",
            "invoked_calls": 0, "returned_packets": 0,
            "unreturned_calls": 0, "total_return_bytes": 0,
            "presentation_parity": "NOT_ASSESSED",
        }
    rows: list[dict[str, Any]] = []
    total = 0
    unreturned = 0
    parity_states: set[str] = set()
    for call_id, (invoked_step, _) in selected:
        observed = observations.get(call_id)
        if observed is None:
            unreturned += 1
            continue
        step, content = observed
        try:
            value = _packet(content)
            parity = _parity(content)
        except (ValueError, TypeError, OverflowError, RecursionError):
            return unavailable_lifecycle("invalid-subject-packet")
        if value is None:
            unreturned += 1
            continue
        digest, count = value
        total += count
        if total > MAX_PACKET_BYTES * MAX_CALLS:
            return unavailable_lifecycle("aggregate-packet-oversized")
        parity_states.add(parity)
        if parity == "DIVERGENT":
            return unavailable_lifecycle("divergent-presentation")
        rows.append({
            "call_id_sha256": hashlib.sha256(call_id.encode("utf-8")).hexdigest(),
            "packet_sha256": digest,
            "invocation_step": invoked_step,
            "return_step": step,
            "result_bytes": count,
        })
    if "UNKNOWN" in parity_states:
        parity_state = "UNKNOWN"
    elif "EQUIVALENT" in parity_states:
        parity_state = "EQUIVALENT"
    else:
        parity_state = "NOT_ASSESSED"
    return {
        **unavailable_lifecycle("no-independent-model-input-receipt"),
        "qualified": True,
        "invocation_state": "INVOKED",
        "return_state": "RETURNED" if rows else "NO_USABLE_RESULT",
        "invoked_calls": len(selected), "returned_packets": len(rows),
        "unreturned_calls": unreturned, "packet_refs": rows,
        "total_return_bytes": total,
        "presentation_parity": parity_state,
    }
