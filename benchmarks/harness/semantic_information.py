"""Oracle-aligned semantic claim exposure from immutable Harbor ATIF artifacts.

This post-run projection compares *structured tool-return claims* with the
same case atoms used by the frozen behavioral verifier. It never calls an
oracle, evaluates reasoning/message text, or treats returned evidence as
proof the agent attended to or used it. Unobservable facts remain unknown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from benchmarks.harness.information_timing import _decode, _opaque_mcp_text
from benchmarks.tool_routing import (
    DISCOVERY_CLASSES,
    SUBJECT_REPOSITORY_INTELLIGENCE,
    classify_call,
    matches_subject_operation,
)

SCHEMA = "agentscookbook.harbor-semantic-information.v1"
ORACLE_SCHEMA = "agents-cookbook-lexigram-oracle.v1"
MAX_ATOMS = 64
MAX_CLAIM_DEPTH = 8
MAX_OBSERVATION_BYTES = 262144
WRAPPERS = ("structuredContent", "result", "data", "evidence", "content")


def unavailable_semantic(reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "qualified": False,
        "reason": reason,
        "claim_alignment": "UNKNOWN",
        "arrival_timing": "UNKNOWN",
        "final_answer_overlap": "UNKNOWN",
        "aligned_fields": [],
        "divergent_fields": [],
        "conflicted_fields": [],
        "aligned_repeated_in_final": [],
        "divergent_repeated_in_final": [],
        "first_subject_observation_step": None,
        "first_aligned_observation_step": None,
        "first_divergent_observation_step": None,
        "first_conflicted_observation_step": None,
        "aligned_claim_arrival": "UNKNOWN",
        "divergent_claim_arrival": "UNKNOWN",
        "claim_arrival_order": "UNKNOWN",
        "first_native_discovery_step": None,
        "message_content_consumed": False,
        "reasoning_content_consumed": False,
        "agent_attention_proven": False,
        "causal_influence_claimed": False,
    }


def _identity(value: object) -> str:
    """Keep bool distinct from int and preserve oracle's list ordering."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _claims(payload: object, fields: set[str]) -> dict[str, list[object]] | None:
    """Select exact named fields from explicit response envelopes only.

    Arbitrary narrative text or similarly-named fields nested in unrelated
    objects must never be mistaken for oracle-aligned evidence.
    """
    result: dict[str, list[object]] = {}

    def visit(value: object, depth: int = 0) -> bool:
        if depth > MAX_CLAIM_DEPTH:
            return False
        if isinstance(value, str):
            decoded = _decode(value)
            return decoded is not None and visit(decoded, depth + 1)
        if isinstance(value, list):
            return all(visit(part, depth + 1) for part in value[:MAX_ATOMS]) and len(value) <= MAX_ATOMS
        if not isinstance(value, dict):
            return False
        if (
            value.get("isError") is True
            or ("error" in value and value["error"] not in (None, ""))
            or (
                "status" in value
                and (
                    not isinstance(value["status"], str)
                    or value["status"] in {"error", "failed", "denied", "unavailable"}
                )
            )
        ):
            return False
        if value.get("type") == "text":
            text = value.get("text")
            return isinstance(text, str) and visit(text, depth + 1)
        for key in fields.intersection(value):
            atom = value[key]
            try:
                if len(_identity(atom).encode("utf-8")) > MAX_OBSERVATION_BYTES:
                    return False
            except (TypeError, ValueError):
                return False
            result.setdefault(key, []).append(atom)
        for wrapper in WRAPPERS:
            if wrapper in value and not visit(value[wrapper], depth + 1):
                return False
        return True

    return result if visit(payload) else None


def _oracle_contract(answer: object) -> tuple[dict[str, object], dict[str, object]] | None:
    if not isinstance(answer, Mapping):
        return None
    expected = answer.get("expected")
    observed = answer.get("observed")
    oracle = answer.get("oracle")
    if (
        not isinstance(expected, dict)
        or not expected
        or len(expected) > MAX_ATOMS
        or not isinstance(observed, dict)
        or not isinstance(oracle, dict)
        or oracle.get("schema") != ORACLE_SCHEMA
        or answer.get("error") is not None
    ):
        return None
    rubric = oracle.get("rubric")
    if not isinstance(rubric, dict):
        return None
    correct = rubric.get("correct_fields")
    missing = rubric.get("missing_fields")
    incorrect = rubric.get("incorrect_fields")
    if not all(
        isinstance(rows, list)
        and all(isinstance(k, str) for k in rows)
        and len(rows) == len(set(rows))
        for rows in (correct, missing, incorrect)
    ):
        return None
    keys = set(expected)
    if not all(isinstance(key, str) and key for key in keys):
        return None
    if (
        (set(correct) | set(missing) | set(incorrect)) != keys
        or set(correct) & set(missing)
        or set(correct) & set(incorrect)
        or set(missing) & set(incorrect)
    ):
        return None
    try:
        graded_correct = {
            key for key in keys
            if key in observed and _identity(observed[key]) == _identity(expected[key])
        }
        graded_missing = keys - set(observed)
        graded_incorrect = keys - graded_correct - graded_missing
    except (TypeError, ValueError):
        return None
    if (
        graded_correct != set(correct)
        or graded_missing != set(missing)
        or graded_incorrect != set(incorrect)
    ):
        return None
    return expected, observed


def _claim_arrival(step: int | None, native_step: int | None) -> str:
    """Time the actual claim, not the first possibly unrelated tool return."""
    if step is None:
        return "NOT_OBSERVED"
    if native_step is None:
        return "NO_NATIVE_DISCOVERY"
    if step == native_step:
        return "UNKNOWN_SAME_STEP"
    return "BEFORE_NATIVE_DISCOVERY" if step < native_step else "AFTER_NATIVE_DISCOVERY"


def _claim_order(aligned: int | None, divergent: int | None) -> str:
    if aligned is None and divergent is None:
        return "NO_UNCONFLICTED_CLAIMS"
    if aligned is None:
        return "DIVERGENT_ONLY"
    if divergent is None:
        return "ALIGNED_ONLY"
    if aligned == divergent:
        return "UNKNOWN_SAME_STEP"
    return "ALIGNED_FIRST" if aligned < divergent else "DIVERGENT_FIRST"


def project_semantic_information(
    trajectory: Path,
    *,
    answer: object,
    component: str | None = None,
    selector: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Project structured semantic atoms; optionally isolate one frozen operation.

    A component filter narrows tool *observations*, not the verifier's oracle.
    A selector further requires the exact configured argument/value.
    """
    if component is not None and (not component or not isinstance(component, str)):
        return unavailable_semantic("invalid-component-filter")
    if selector is not None and (
        component is None
        or not isinstance(selector, Mapping)
        or set(selector) != {"argument", "value"}
        or selector.get("argument") != "surface_name"
        or not isinstance(selector.get("value"), str)
        or not selector["value"]
    ):
        return unavailable_semantic("invalid-component-selector")
    contract = _oracle_contract(answer)
    if contract is None:
        return unavailable_semantic("frozen-semantic-oracle-unavailable")
    expected, observed = contract
    try:
        atif = json.loads(trajectory.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unavailable_semantic("atif-unavailable")
    if (
        not isinstance(atif, dict)
        or not isinstance(atif.get("schema_version"), str)
        or not atif["schema_version"].startswith("ATIF-v")
        or not isinstance(atif.get("steps"), list)
        or not atif["steps"]
    ):
        return unavailable_semantic("atif-invalid-or-empty")
    steps = atif["steps"]
    linked: dict[str, tuple[object, int]] = {}
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            return unavailable_semantic("malformed-step")
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
                    return unavailable_semantic("duplicate-observation-link")
                linked[call_id] = (row.get("content"), index)

    seen: set[str] = set()
    native_steps: list[int] = []
    subject_observations: list[tuple[int, dict[str, list[object]]]] = []
    count = 0
    for index, step in enumerate(steps, 1):
        if "tool_calls" not in step:
            continue
        calls = step["tool_calls"]
        if not isinstance(calls, list):
            return unavailable_semantic("malformed-tool-call-list")
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("function_name"), str) or not call["function_name"].strip():
                return unavailable_semantic("malformed-tool-call")
            name = call["function_name"]
            call_id = call.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                if call_id in seen:
                    return unavailable_semantic("duplicate-tool-call-id")
                seen.add(call_id)
            arguments = call.get("arguments")
            tool_class = classify_call(
                name, arguments if isinstance(arguments, dict) else {}, subject="hashmarks"
            )
            if tool_class in DISCOVERY_CLASSES:
                native_steps.append(index)
            if tool_class != SUBJECT_REPOSITORY_INTELLIGENCE:
                continue
            if component is not None and not matches_subject_operation(
                name,
                subject="hashmarks",
                operation=component,
            ):
                continue
            if selector is not None and (
                not isinstance(arguments, Mapping)
                or arguments.get(selector["argument"]) != selector["value"]
            ):
                continue
            count += 1
            if not isinstance(call_id, str) or call_id not in linked:
                return unavailable_semantic("missing-subject-observation-link")
            payload, observed_step = linked[call_id]
            if observed_step < index:
                return unavailable_semantic("observation-precedes-call")
            if payload is None or _opaque_mcp_text(payload):
                return unavailable_semantic("unstructured-subject-observation")
            try:
                if len(_identity(payload).encode("utf-8")) > MAX_OBSERVATION_BYTES:
                    return unavailable_semantic("oversized-subject-observation")
            except (TypeError, ValueError):
                return unavailable_semantic("unstructured-subject-observation")
            claims = _claims(payload, set(expected))
            if claims is None:
                return unavailable_semantic("unstructured-subject-observation")
            subject_observations.append((observed_step, claims))

    native_step = min(native_steps) if native_steps else None
    if not count:
        return {
            **unavailable_semantic("subject-never-invoked"),
            "qualified": True,
            "claim_alignment": "NO_SUBJECT_RESULT",
            "arrival_timing": "NO_SUBJECT_RESULT",
            "final_answer_overlap": "NO_SUBJECT_RESULT",
            "first_native_discovery_step": native_step,
        }

    atom_steps: dict[str, list[tuple[int, object]]] = {}
    for step_index, claims in subject_observations:
        for field, values in claims.items():
            for value in values:
                atom_steps.setdefault(field, []).append((step_index, value))
    aligned: list[str] = []
    divergent: list[str] = []
    conflicted: list[str] = []
    aligned_steps: list[int] = []
    divergent_steps: list[int] = []
    conflicted_steps: list[int] = []
    final_good: list[str] = []
    final_bad: list[str] = []
    for field, candidates in sorted(atom_steps.items()):
        values = {_identity(value) for _, value in candidates}
        if len(values) > 1:
            conflicted.append(field)
            conflicted_steps.append(min(step for step, _ in candidates))
            continue
        value = candidates[0][1]
        if _identity(value) == _identity(expected[field]):
            aligned.append(field)
            aligned_steps.append(min(step for step, _ in candidates))
            if field in observed and _identity(observed[field]) == _identity(value):
                final_good.append(field)
        else:
            divergent.append(field)
            divergent_steps.append(min(step for step, _ in candidates))
            if field in observed and _identity(observed[field]) == _identity(value):
                final_bad.append(field)
    alignment = (
        "MIXED_OR_CONFLICTING"
        if conflicted or (aligned and divergent)
        else "ALIGNED_ONLY"
        if aligned
        else "DIVERGENT_ONLY"
        if divergent
        else "NO_COMPARABLE_CLAIMS"
    )
    first_step = min(step for step, _ in subject_observations)
    if native_step is not None and first_step == native_step:
        timing = "UNKNOWN_SAME_STEP"
    else:
        timing = (
            "NO_NATIVE_DISCOVERY" if native_step is None
            else "BEFORE_NATIVE_DISCOVERY" if first_step < native_step
            else "AFTER_NATIVE_DISCOVERY"
        )
    aligned_step = min(aligned_steps) if aligned_steps else None
    divergent_step = min(divergent_steps) if divergent_steps else None
    overlap = (
        "BOTH_ALIGNED_AND_DIVERGENT_REPEATED" if final_good and final_bad
        else "ALIGNED_REPEATED" if final_good
        else "DIVERGENT_REPEATED" if final_bad
        else "NO_REPEATED_STRUCTURED_ATOM"
    )
    return {
        "schema": SCHEMA,
        "qualified": True,
        "reason": None,
        "claim_alignment": alignment,
        "arrival_timing": timing,
        "final_answer_overlap": overlap,
        "oracle_atom_count": len(expected),
        "comparable_atom_count": len(atom_steps),
        "aligned_fields": aligned,
        "divergent_fields": divergent,
        "conflicted_fields": conflicted,
        "aligned_repeated_in_final": final_good,
        "divergent_repeated_in_final": final_bad,
        "first_subject_observation_step": first_step,
        "first_aligned_observation_step": aligned_step,
        "first_divergent_observation_step": divergent_step,
        "first_conflicted_observation_step": (
            min(conflicted_steps) if conflicted_steps else None
        ),
        "aligned_claim_arrival": _claim_arrival(aligned_step, native_step),
        "divergent_claim_arrival": _claim_arrival(divergent_step, native_step),
        "claim_arrival_order": _claim_order(aligned_step, divergent_step),
        "first_native_discovery_step": native_step,
        "message_content_consumed": False,
        "reasoning_content_consumed": False,
        "agent_attention_proven": False,
        "causal_influence_claimed": False,
    }
