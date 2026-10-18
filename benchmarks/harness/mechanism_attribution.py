"""Paired Harbor mechanism attribution from immutable observable evidence.

This module deliberately ignores model reasoning text. It consumes only frozen
trial receipts, verifier answer evidence, ATIF tool calls/observations, and ATIF
token metrics. Pair-level conclusions are descriptive or supported
associations; positive causal attribution is never upgraded to proof.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from benchmarks.harness.bundle import verify_bundle
from benchmarks.harness.information_timing import (
    project_information_timing,
    unavailable_information,
)
from benchmarks.harness.semantic_information import (
    project_semantic_information,
    unavailable_semantic,
)
from benchmarks.harness.evidence_lifecycle import (
    project_evidence_lifecycle,
    unavailable_lifecycle,
)
from benchmarks.harness.evaluation_assurance import build_assurance_summary
from benchmarks.harness.relationship_scope_timing import (
    project_relationship_scope_timing,
    unavailable_relationship_scope,
)
from benchmarks.tool_routing import (
    DISCOVERY_CLASSES,
    NATIVE_READ,
    NATIVE_SEARCH,
    SHELL,
    TOOL_ROUTER,
    ROUTING_UNKNOWN,
    SUBJECT_REPOSITORY_INTELLIGENCE,
    classify_call,
    normalize_calls,
    subject_routing_timing,
)

MECHANISM_REPORT_SCHEMA = "agentscookbook.harbor-mechanism-report.v1"
TRACE_PROJECTION_SCHEMA = "agentscookbook.harbor-atif-projection.v1"
ANSWER_EVIDENCE_SCHEMA = "agentscookbook.harbor-answer-evidence.v1"

TREATMENT_SUCCESS = "OBSERVED_SUCCESSFUL_RESULT"
TREATMENT_NO_USABLE_RESULT = "OBSERVED_NO_USABLE_RESULT"
TREATMENT_NEVER_INVOKED = "NEVER_INVOKED"
TREATMENT_UNKNOWN = "UNKNOWN"

FOLLOWED = "SUBJECT_TARGET_FOLLOWED"
NO_FOLLOWTHROUGH = "NO_FOLLOWTHROUGH_OBSERVED"
FOLLOWTHROUGH_UNKNOWN = "FOLLOWTHROUGH_UNKNOWN"

ATTRIBUTION_SUPPORTED = "SUPPORTED"
ATTRIBUTION_PROVEN = "PROVEN"
ATTRIBUTION_UNPROVEN = "UNPROVEN"
ATTRIBUTION_NOT_ATTRIBUTABLE = "NOT_ATTRIBUTABLE"

_PATH_KEYS = frozenset(
    {
        "path",
        "file",
        "file_path",
        "owner_path",
        "target_path",
        "next_read",
    }
)


class MechanismAttributionError(ValueError):
    pass


def _encoded_size(value: object) -> int:
    return len(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    )


def _observation_results(steps: list[object]) -> dict[str, object]:
    results: dict[str, object] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
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
                results[call_id] = row.get("content")
    return results


def _positive_int(value: object) -> int:
    return (
        int(value)
        if isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        else 0
    )


def _token_metrics(steps: list[object]) -> dict[str, int]:
    prompt = 0
    completion = 0
    cached = 0
    llm_calls = 0
    for step in steps:
        if not isinstance(step, dict) or step.get("source") != "agent":
            continue
        metrics = step.get("metrics")
        if isinstance(metrics, dict):
            prompt += _positive_int(metrics.get("prompt_tokens"))
            completion += _positive_int(metrics.get("completion_tokens"))
            cached += _positive_int(metrics.get("cached_tokens"))
        count = step.get("llm_call_count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            llm_calls += count
    return {
        "input_tokens": prompt,
        "output_tokens": completion,
        "cached_input_tokens": cached,
        "total_tokens": prompt + completion,
        "llm_calls": llm_calls,
    }


def _path_values(value: object, *, key: str | None = None) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for child_key, child in value.items():
            normalized = str(child_key).strip().lower()
            found.update(
                _path_values(
                    child,
                    key=normalized if normalized in _PATH_KEYS else None,
                )
            )
        return found
    if isinstance(value, list):
        for child in value:
            found.update(_path_values(child, key=key))
        return found
    if isinstance(value, str):
        candidate = value.strip()
        if key in _PATH_KEYS:
            normalized_path = candidate.replace("\\", "/")
            if (
                normalized_path
                and "\n" not in normalized_path
                and len(normalized_path) <= 500
            ):
                found.add(normalized_path)
        if candidate.startswith(("{", "[")) and candidate.endswith(("}", "]")):
            try:
                decoded = json.loads(candidate)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, (dict, list)):
                found.update(_path_values(decoded))
    return found


def _result_size(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, str) and not value.strip():
        return 0
    if isinstance(value, (list, dict)) and not value:
        return 0
    return _encoded_size(value)


def _input_strings(value: object) -> tuple[str, ...]:
    out: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            out.append(item.replace("\\", "/"))
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return tuple(out)


def _read_atif(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MechanismAttributionError(
            f"cannot read ATIF trajectory {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise MechanismAttributionError("ATIF trajectory must be one object")
    version = value.get("schema_version")
    steps = value.get("steps")
    if (
        not isinstance(version, str)
        or not version.startswith("ATIF-v")
        or not isinstance(steps, list)
    ):
        raise MechanismAttributionError("trajectory is not a supported ATIF document")
    return value


def project_atif(path: Path, *, subject: str = "hashmarks") -> dict[str, Any]:
    """Project ATIF into host-neutral observable tool/treatment evidence."""
    trajectory = _read_atif(path)
    steps = trajectory["steps"]
    linked_results = _observation_results(steps)
    calls: list[dict[str, object]] = []
    subject_result_targets: list[tuple[int, set[str]]] = []
    order_issues: list[str] = []
    if not steps:
        order_issues.append("empty-trajectory")

    for step in steps:
        if not isinstance(step, dict):
            order_issues.append("malformed-step")
            continue
        if "tool_calls" not in step:
            continue  # ATIF allows a step with no tool calls.
        tool_calls = step["tool_calls"]
        if not isinstance(tool_calls, list):
            order_issues.append("malformed-tool-call-list")
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                order_issues.append("malformed-tool-call")
                continue
            call_id = tool_call.get("tool_call_id")
            name = tool_call.get("function_name")
            if not isinstance(name, str) or not name.strip():
                order_issues.append("missing-tool-name")
                continue
            arguments = tool_call.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            content = (
                linked_results.get(call_id)
                if isinstance(call_id, str)
                else None
            )
            row: dict[str, object] = {
                "tool": name,
                "input": arguments,
                "status": "completed" if content is not None else "unknown",
                "result_bytes": _result_size(content) if content is not None else None,
            }
            calls.append(row)
            if (
                classify_call(name, arguments, subject=subject)
                == SUBJECT_REPOSITORY_INTELLIGENCE
                and content is not None
            ):
                subject_result_targets.append((len(calls), _path_values(content)))

    order_complete = not order_issues
    normalized = normalize_calls(calls, subject=subject)
    native_search_observability = (
        "PARTIAL_OPAQUE_TOOL"
        if any(row.get("tool_class") in (SHELL, TOOL_ROUTER) for row in normalized)
        else "EXPLICIT_TOOL_CALLS_ONLY"
    )
    sequence = tuple(
        str(call.get("tool"))
        for call in normalized
        if isinstance(call.get("tool"), str) and call.get("tool")
    )
    subject_ordinals = tuple(
        int(call["ordinal"])
        for call in normalized
        if call.get("tool_class") == SUBJECT_REPOSITORY_INTELLIGENCE
        and isinstance(call.get("ordinal"), int)
    )
    invocation_observed = bool(subject_ordinals)
    routing = (
        subject_routing_timing(
            sequence,
            subject_ordinals,
            configured=True,
            invocation_observed=invocation_observed,
            order_complete=True,
        )
        if order_complete
        else ROUTING_UNKNOWN
    )
    subject_rows = [
        call
        for call in normalized
        if call.get("tool_class") == SUBJECT_REPOSITORY_INTELLIGENCE
    ]
    subject_tools = [
        str(call["tool"])
        for call in subject_rows
        if isinstance(call.get("tool"), str)
    ]
    subject_call_selectors = [
        {
            "tool": str(call["tool"]),
            **(
                {"surface_name": str(call["input"]["surface_name"])}
                if (
                    isinstance(call.get("input"), dict)
                    and isinstance(call["input"].get("surface_name"), str)
                )
                else {}
            ),
        }
        for call in subject_rows
        if isinstance(call.get("tool"), str)
    ]
    if not order_complete:
        treatment = TREATMENT_UNKNOWN
    elif not subject_rows:
        treatment = TREATMENT_NEVER_INVOKED
    elif any(
        call.get("status") == "completed"
        and isinstance(call.get("result_bytes"), int)
        and int(call["result_bytes"]) > 0
        for call in subject_rows
    ):
        treatment = TREATMENT_SUCCESS
    elif all(
        call.get("status") == "completed"
        and call.get("result_bytes") == 0
        for call in subject_rows
    ):
        treatment = TREATMENT_NO_USABLE_RESULT
    else:
        treatment = TREATMENT_UNKNOWN

    first_subject = min(subject_ordinals) if subject_ordinals else None
    before = 0
    after = 0
    native_search = 0
    native_read = 0
    for call in normalized:
        tool_class = call.get("tool_class")
        ordinal = call.get("ordinal")
        if tool_class == NATIVE_SEARCH:
            native_search += 1
        if tool_class == NATIVE_READ:
            native_read += 1
        if tool_class in DISCOVERY_CLASSES and isinstance(ordinal, int):
            if first_subject is None or ordinal < first_subject:
                before += 1
            else:
                after += 1

    targets = sorted(
        {
            target
            for _ordinal, values in subject_result_targets
            for target in values
        }
    )
    followed = False
    if targets:
        for call in normalized:
            if call.get("tool_class") != NATIVE_READ:
                continue
            ordinal = call.get("ordinal")
            if not isinstance(ordinal, int):
                continue
            eligible = [
                target
                for subject_ordinal, values in subject_result_targets
                if subject_ordinal < ordinal
                for target in values
            ]
            if not eligible:
                continue
            rendered_inputs = "\n".join(_input_strings(call.get("input")))
            if any(target in rendered_inputs for target in eligible):
                followed = True
                break
        followthrough = FOLLOWED if followed else NO_FOLLOWTHROUGH
    else:
        followthrough = FOLLOWTHROUGH_UNKNOWN
    if not order_complete:
        followthrough = FOLLOWTHROUGH_UNKNOWN

    token_metrics = _token_metrics(steps)
    return {
        "schema": TRACE_PROJECTION_SCHEMA,
        "available": True,
        "atif_schema_version": trajectory.get("schema_version"),
        "agent_name": (
            trajectory.get("agent", {}).get("name")
            if isinstance(trajectory.get("agent"), dict)
            else None
        ),
        "tool_calls": len(normalized),
        "native_search_calls": native_search,
        "native_search_observability": native_search_observability,
        "native_read_calls": native_read,
        "native_discovery_calls": sum(
            1 for call in normalized if call.get("tool_class") in DISCOVERY_CLASSES
        ),
        "native_discovery_before_subject": before,
        "native_discovery_after_subject": after,
        "subject_tool_calls": len(subject_rows),
        "subject_tools": subject_tools,
        "subject_call_selectors": subject_call_selectors,
        "subject_first_tool_call_ordinal": first_subject,
        "subject_routing_timing": routing,
        "treatment": treatment,
        "subject_target_count": len(targets),
        "subject_target_followthrough": followthrough,
        **token_metrics,
        "reasoning_content_consumed": False,
        "message_content_consumed": False,
        "tool_order_complete": order_complete,
        "tool_order_issue_count": len(order_issues),
        "tool_order_issue_codes": sorted(set(order_issues)),
    }


def unavailable_trace(reason: str) -> dict[str, Any]:
    return {
        "schema": TRACE_PROJECTION_SCHEMA,
        "available": False,
        "reason": reason,
        "subject_routing_timing": ROUTING_UNKNOWN,
        "treatment": TREATMENT_UNKNOWN,
        "subject_target_followthrough": FOLLOWTHROUGH_UNKNOWN,
        "reasoning_content_consumed": False,
        "message_content_consumed": False,
        "tool_order_complete": False,
        "native_search_observability": "UNKNOWN",
    }


def read_answer_evidence(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema") != ANSWER_EVIDENCE_SCHEMA
        or not isinstance(value.get("match"), bool)
    ):
        return None
    observed = value.get("observed")
    expected = value.get("expected")
    return {
        "match": value["match"],
        "observed": observed if isinstance(observed, dict) else None,
        "expected": expected if isinstance(expected, dict) else None,
        "oracle": value.get("oracle") if isinstance(value.get("oracle"), dict) else None,
        "semantic_case_id": value.get("semantic_case_id"),
        "error": value.get("error"),
        "tracked_clean": (
            value.get("tracked_clean")
            if isinstance(value.get("tracked_clean"), bool)
            else None
        ),
    }


def load_harbor_bundle_projection(directory: Path) -> dict[str, Any]:
    valid, reason = verify_bundle(directory)
    if not valid:
        raise MechanismAttributionError(
            f"invalid Harbor bundle {directory}: {reason}"
        )
    receipt = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("backend") != "harbor":
        raise MechanismAttributionError(
            f"bundle is not a Harbor receipt: {directory}"
        )
    answer = read_answer_evidence(directory / "answer.json")
    trajectory = directory / "trajectory.json"
    if trajectory.is_file():
        try:
            trace = project_atif(trajectory)
        except MechanismAttributionError as exc:
            trace = unavailable_trace(str(exc))
    else:
        trace = unavailable_trace("immutable ATIF trajectory was not captured")
    expected = answer.get("expected") if isinstance(answer, dict) else None
    expected_path = expected.get("path") if isinstance(expected, dict) else None
    information = (
        project_information_timing(trajectory, expected_path=expected_path)
        if trace.get("available") is True and trace.get("tool_order_complete") is True
        else unavailable_information("atif-order-unavailable-or-incomplete")
    )
    if not isinstance(answer, dict) or answer.get("semantic_case_id") != receipt.get("task_id"):
        semantic = unavailable_semantic("semantic-case-identity-mismatch-or-missing")
    elif trace.get("available") is True and trace.get("tool_order_complete") is True:
        semantic = project_semantic_information(trajectory, answer=answer)
    else:
        semantic = unavailable_semantic("atif-order-unavailable-or-incomplete")
    execution = receipt.get("execution")
    ablation = (
        execution.get("ablation")
        if isinstance(execution, dict)
        else None
    )
    if not isinstance(ablation, dict) or not isinstance(ablation.get("component"), str):
        component_semantic = unavailable_semantic("no-frozen-component-contract")
    elif not isinstance(answer, dict) or answer.get("semantic_case_id") != receipt.get("task_id"):
        component_semantic = unavailable_semantic("semantic-case-identity-mismatch-or-missing")
    elif trace.get("available") is not True or trace.get("tool_order_complete") is not True:
        component_semantic = unavailable_semantic("atif-order-unavailable-or-incomplete")
    else:
        component_semantic = project_semantic_information(
            trajectory,
            answer=answer,
            component=ablation["component"],
            selector=ablation.get("selector"),
        )
    relationship_scope = (
        project_relationship_scope_timing(trajectory)
        if trace.get("available") is True and trace.get("tool_order_complete") is True
        else unavailable_relationship_scope("atif-order-unavailable-or-incomplete")
    )
    lifecycle = (
        project_evidence_lifecycle(trajectory)
        if trace.get("available") is True and trace.get("tool_order_complete") is True
        else unavailable_lifecycle("atif-order-unavailable-or-incomplete")
    )
    return {
        "receipt": receipt,
        "trace": trace,
        "answer": answer,
        "delivery_evidence": lifecycle,
        "information": information,
        "semantic_information": semantic,
        "component_semantic_information": component_semantic,
        "relationship_scope_timing": relationship_scope,
    }


def _outcome_transition(bare: Mapping[str, Any], treated: Mapping[str, Any]) -> str:
    left = bare.get("status")
    right = treated.get("status")
    if left not in {"PASS", "FAIL"} or right not in {"PASS", "FAIL"}:
        return "INCOMPLETE"
    return f"{left}_TO_{right}"


def _answer_transition(
    bare: Mapping[str, Any] | None,
    treated: Mapping[str, Any] | None,
) -> str:
    if bare is None or treated is None:
        return "UNKNOWN"
    left = bare.get("match")
    right = treated.get("match")
    if not isinstance(left, bool) or not isinstance(right, bool):
        return "UNKNOWN"
    return {
        (False, True): "WRONG_TO_CORRECT",
        (True, True): "CORRECT_TO_CORRECT",
        (True, False): "CORRECT_TO_WRONG",
        (False, False): "WRONG_TO_WRONG",
    }[(left, right)]


def _same_answer(
    bare: Mapping[str, Any] | None,
    treated: Mapping[str, Any] | None,
) -> bool | None:
    if bare is None or treated is None:
        return None
    left = bare.get("observed")
    right = treated.get("observed")
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None
    return left == right


def _delta(
    bare: Mapping[str, Any],
    treated: Mapping[str, Any],
    name: str,
) -> int | None:
    left = bare.get(name)
    right = treated.get(name)
    if (
        isinstance(left, int)
        and not isinstance(left, bool)
        and isinstance(right, int)
        and not isinstance(right, bool)
    ):
        return right - left
    return None


def _native_search_comparison(
    bare: Mapping[str, Any],
    treated: Mapping[str, Any],
    *,
    tool_order_qualified: bool,
) -> dict[str, Any]:
    """Keep directly counted tools distinct from comparable search savings."""
    observed_delta = _delta(bare, treated, "native_search_calls")
    states = (bare.get("native_search_observability"),
              treated.get("native_search_observability"))
    if not tool_order_qualified:
        reason = "tool-order-incomplete"
    elif (
        bare.get("tool_order_complete") is not True
        or treated.get("tool_order_complete") is not True
    ):
        reason = "tool-order-unestablished"
    elif bare.get("available") is not True or treated.get("available") is not True:
        reason = "trace-unavailable"
    elif "PARTIAL_OPAQUE_TOOL" in states:
        reason = "opaque-tool-boundary"
    elif states != ("EXPLICIT_TOOL_CALLS_ONLY", "EXPLICIT_TOOL_CALLS_ONLY"):
        reason = "observability-unestablished"
    elif observed_delta is None:
        reason = "native-search-count-unavailable"
    else:
        reason = None
    return {
        "qualified": reason is None,
        "reason": reason,
        "state": "COMPARABLE_EXPLICIT_COUNTS" if reason is None else "UNQUALIFIED",
        "observed_delta": observed_delta,
        "qualified_delta": observed_delta if reason is None else None,
        "scope": "explicit-ATIF-native-search-calls-only",
        "bare_observability": states[0],
        "treated_observability": states[1],
        "negative_evidence_admissible": False,
        "causal_savings_proven": False,
    }


def _discovery_change(delta: int | None, treated_count: object) -> str:
    if delta is None or not isinstance(treated_count, int):
        return "UNKNOWN"
    if delta < 0 and treated_count == 0:
        return "AVOIDED"
    if delta < 0:
        return "REDUCED"
    if delta == 0:
        return "SAME"
    return "INCREASED"


def _mechanism_tags(
    *,
    outcome: str,
    answer_transition: str,
    treated_trace: Mapping[str, Any],
    discovery_change: str,
    tool_delta: int | None,
    token_delta: int | None,
) -> list[str]:
    tags: list[str] = []
    routing = treated_trace.get("subject_routing_timing")
    treatment = treated_trace.get("treatment")
    followthrough = treated_trace.get("subject_target_followthrough")
    if treatment == TREATMENT_NEVER_INVOKED:
        tags.append("SUBJECT_NEVER_INVOKED")
    if routing == "FIRST_CHOICE":
        tags.append("FIRST_CHOICE_LOCALIZATION")
    elif routing == "LATE_RESCUE":
        tags.append("LATE_RESCUE")
    if discovery_change == "AVOIDED":
        tags.append("NATIVE_DISCOVERY_AVOIDED")
    elif discovery_change == "REDUCED":
        tags.append("NATIVE_DISCOVERY_REDUCED")
    if tool_delta is not None and tool_delta < 0:
        tags.append("FEWER_TOOL_CALLS")
    if token_delta is not None and token_delta < 0:
        tags.append("FEWER_TOKENS")
    if answer_transition == "WRONG_TO_CORRECT":
        tags.append("WRONG_TO_CORRECT_ANSWER")
    elif answer_transition == "CORRECT_TO_CORRECT":
        tags.append("SAME_CORRECTNESS")
    elif answer_transition == "CORRECT_TO_WRONG":
        tags.append("CORRECT_TO_WRONG_ANSWER")
    if followthrough == FOLLOWED:
        tags.append(FOLLOWED)
    if outcome == "PASS_TO_FAIL":
        tags.append("ADVERSE_OUTCOME_ASSOCIATION")
    return tags


def _attribution(
    *,
    outcome: str,
    treated_trace: Mapping[str, Any],
    discovery_change: str,
    answer_transition: str,
) -> tuple[str, str]:
    if treated_trace.get("available") is not True:
        return ATTRIBUTION_UNPROVEN, "trace-unavailable"
    treatment = treated_trace.get("treatment")
    if treatment == TREATMENT_NEVER_INVOKED:
        return (
            ATTRIBUTION_PROVEN,
            "not-attributable-subject-never-invoked",
        )
    if treatment != TREATMENT_SUCCESS:
        return ATTRIBUTION_UNPROVEN, "usable-treatment-not-observed"
    if outcome == "FAIL_TO_PASS":
        return ATTRIBUTION_SUPPORTED, "supported-positive-mechanism-association"
    if outcome == "PASS_TO_FAIL":
        return ATTRIBUTION_SUPPORTED, "supported-adverse-mechanism-association"
    if outcome == "PASS_TO_PASS" and (
        discovery_change in {"AVOIDED", "REDUCED"}
        or answer_transition == "CORRECT_TO_CORRECT"
    ):
        return ATTRIBUTION_SUPPORTED, "supported-efficiency-mechanism-association"
    return ATTRIBUTION_SUPPORTED, "treatment-observed-without-isolated-causal-proof"


def pair_projection(
    bare: Mapping[str, Any],
    treated: Mapping[str, Any],
) -> dict[str, Any]:
    bare_receipt = bare["receipt"]
    treated_receipt = treated["receipt"]
    bare_trace = bare["trace"]
    treated_trace = treated["trace"]
    outcome = _outcome_transition(bare_receipt, treated_receipt)
    answer_transition = _answer_transition(bare.get("answer"), treated.get("answer"))
    discovery_delta = _delta(
        bare_trace,
        treated_trace,
        "native_discovery_calls",
    )
    tool_delta = _delta(bare_trace, treated_trace, "tool_calls")
    token_delta = _delta(bare_trace, treated_trace, "total_tokens")
    discovery_change = _discovery_change(
        discovery_delta,
        treated_trace.get("native_discovery_calls"),
    )
    call_order_qualified = (
        bare_trace.get("tool_order_complete") is not False
        and treated_trace.get("tool_order_complete") is not False
    )
    native_search_comparison = _native_search_comparison(
        bare_trace, treated_trace, tool_order_qualified=call_order_qualified
    )
    search_comparable = native_search_comparison["qualified"] is True
    if not search_comparable:
        discovery_change = "UNKNOWN"
    if not call_order_qualified:
        discovery_delta = None
        tool_delta = None
        token_delta = None
        discovery_change = "UNKNOWN"
        strength, interpretation = ATTRIBUTION_UNPROVEN, "tool-order-incomplete"
    else:
        strength, interpretation = _attribution(
            outcome=outcome,
            treated_trace=treated_trace,
            discovery_change=discovery_change,
            answer_transition=answer_transition,
        )
    tags = _mechanism_tags(
        outcome=outcome,
        answer_transition=answer_transition,
        treated_trace=(
            treated_trace if search_comparable
            else {**treated_trace, "subject_routing_timing": "UNKNOWN"}
        ),
        discovery_change=discovery_change,
        tool_delta=tool_delta if search_comparable else None,
        token_delta=token_delta,
    )
    same_answer = _same_answer(
        bare.get("answer"),
        treated.get("answer"),
    )
    native_read_delta = (
        _delta(bare_trace, treated_trace, "native_read_calls")
        if call_order_qualified
        else None
    )
    if (
        search_comparable and same_answer is True
        and native_read_delta is not None and native_read_delta < 0
    ):
        tags.append("SAME_ANSWER_FEWER_READS")
    information = treated.get("information")
    if not isinstance(information, dict):
        information = unavailable_information("information-projection-unavailable")
    if not call_order_qualified and information.get("qualified") is True:
        information = unavailable_information("atif-order-unavailable-or-incomplete")
    semantic = treated.get("semantic_information")
    if not isinstance(semantic, dict):
        semantic = unavailable_semantic("semantic-projection-unavailable")
    if not call_order_qualified and semantic.get("qualified") is True:
        semantic = unavailable_semantic("atif-order-unavailable-or-incomplete")
    relationship_scope = treated.get("relationship_scope_timing")
    if not isinstance(relationship_scope, dict):
        relationship_scope = unavailable_relationship_scope("scope-projection-unavailable")
    if not call_order_qualified:
        relationship_scope = unavailable_relationship_scope(
            "atif-order-unavailable-or-incomplete"
        )
    delivery = treated.get("delivery_evidence")
    if not isinstance(delivery, dict):
        delivery = unavailable_lifecycle("delivery-projection-unavailable")
    if not call_order_qualified:
        delivery = unavailable_lifecycle("atif-order-unavailable-or-incomplete")
    return {
        "delivery_evidence": delivery,
        "relationship_scope_evidence": relationship_scope,
        "native_search_comparison": native_search_comparison,
        "information_evidence": information,
        "semantic_information_evidence": semantic,
        "task": bare_receipt["task_id"],
        "harness": bare_receipt["harness"],
        "model": bare_receipt["model"],
        "replicate_id": bare_receipt["replicate_id"],
        "outcome_transition": outcome,
        "answer_transition": answer_transition,
        "same_observed_answer": same_answer,
        "treatment": treated_trace.get("treatment"),
        "routing": treated_trace.get("subject_routing_timing"),
        "subject_target_followthrough": treated_trace.get(
            "subject_target_followthrough"
        ),
        "native_discovery_delta": discovery_delta,
        "native_discovery_change": discovery_change,
        "native_search_delta": (
            _delta(bare_trace, treated_trace, "native_search_calls")
            if call_order_qualified
            else None
        ),
        "native_read_delta": native_read_delta,
        "tool_call_delta": tool_delta,
        "token_delta": token_delta,
        "mechanism_tags": tags,
        "attribution_result": (
            ATTRIBUTION_NOT_ATTRIBUTABLE
            if interpretation == "not-attributable-subject-never-invoked"
            else "SUPPORTED_ASSOCIATION"
            if strength == ATTRIBUTION_SUPPORTED
            else ATTRIBUTION_UNPROVEN
        ),
        "attribution_strength": strength,
        "attribution_interpretation": interpretation,
        "positive_causal_proof_claimed": False,
        "tool_order_qualified": call_order_qualified,
        "trace_observability": {
            "bare": bare_trace.get("available") is True,
            "hashmarks": treated_trace.get("available") is True,
        },
    }


def _pair_key(receipt: Mapping[str, Any]) -> tuple[object, ...]:
    execution = receipt.get("execution")
    campaign_id = (
        execution.get("campaign_id")
        if isinstance(execution, dict)
        else None
    )
    return (
        campaign_id,
        receipt.get("task_id"),
        receipt.get("harness"),
        receipt.get("model"),
        receipt.get("replicate_id"),
    )


def build_mechanism_report(results_root: Path) -> dict[str, Any]:
    """Build strict bare/Hashmarks pairs from immutable Harbor result bundles."""
    grouped: dict[tuple[object, ...], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    unavailable_bundles: list[dict[str, str]] = []
    if results_root.is_dir():
        for directory in sorted(results_root.iterdir()):
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                projection = load_harbor_bundle_projection(directory)
            except (MechanismAttributionError, OSError, json.JSONDecodeError) as exc:
                unavailable_bundles.append(
                    {"directory": str(directory), "reason": str(exc)}
                )
                continue
            receipt = projection["receipt"]
            subject = receipt.get("subject")
            if subject in {"none", "hashmarks"}:
                grouped[_pair_key(receipt)][str(subject)].append(projection)

    pairs: list[dict[str, Any]] = []
    unpaired: list[dict[str, Any]] = []
    for key, arms in sorted(grouped.items(), key=lambda item: tuple(str(v) for v in item[0])):
        bare = arms.get("none", [])
        treated = arms.get("hashmarks", [])
        if len(bare) != 1 or len(treated) != 1:
            unpaired.append(
                {
                    "pair_key": [str(value) if value is not None else None for value in key],
                    "bare_receipts": len(bare),
                    "hashmarks_receipts": len(treated),
                }
            )
            continue
        pairs.append(pair_projection(bare[0], treated[0]))

    outcome_counts = Counter(str(pair["outcome_transition"]) for pair in pairs)
    treatment_counts = Counter(str(pair["treatment"]) for pair in pairs)
    routing_counts = Counter(str(pair["routing"]) for pair in pairs)
    strength_counts = Counter(str(pair["attribution_strength"]) for pair in pairs)
    attribution_counts = Counter(str(pair["attribution_result"]) for pair in pairs)
    tag_counts = Counter(
        str(tag)
        for pair in pairs
        for tag in pair["mechanism_tags"]
    )
    information_counts = Counter(
        "|".join((
            str(pair["outcome_transition"]),
            str(pair["information_evidence"]["arrival_timing"]),
            str(pair["information_evidence"]["target_alignment"]),
            str(pair["information_evidence"]["native_read_followthrough"]),
        ))
        for pair in pairs
        if pair["information_evidence"].get("qualified") is True
        and pair["outcome_transition"] != "INCOMPLETE"
    )
    information_exclusions = Counter(
        (
            "incomplete-pair-outcome"
            if pair["outcome_transition"] == "INCOMPLETE"
            else str(pair["information_evidence"].get("reason") or "unknown")
        )
        for pair in pairs
        if pair["information_evidence"].get("qualified") is not True
        or pair["outcome_transition"] == "INCOMPLETE"
    )
    semantic_counts = Counter(
        "|".join((
            str(pair["outcome_transition"]),
            str(pair["semantic_information_evidence"]["arrival_timing"]),
            str(pair["semantic_information_evidence"]["claim_alignment"]),
            str(pair["semantic_information_evidence"]["final_answer_overlap"]),
        ))
        for pair in pairs
        if pair["semantic_information_evidence"].get("qualified") is True
        and pair["outcome_transition"] != "INCOMPLETE"
    )
    semantic_exclusions = Counter(
        (
            "incomplete-pair-outcome"
            if pair["outcome_transition"] == "INCOMPLETE"
            else str(pair["semantic_information_evidence"].get("reason") or "unknown")
        )
        for pair in pairs
        if pair["semantic_information_evidence"].get("qualified") is not True
        or pair["outcome_transition"] == "INCOMPLETE"
    )
    scope_counts = Counter(
        "|".join((
            str(pair["outcome_transition"]),
            str(pair["relationship_scope_evidence"]["arrival_timing"]),
            str(pair["relationship_scope_evidence"]["detail_followthrough"]),
            str(pair["relationship_scope_evidence"]["summary_count_scope"]),
            str(pair["native_search_comparison"]["qualified_delta"])
            if pair["native_search_comparison"]["qualified"] is True
            else "UNQUALIFIED",
        ))
        for pair in pairs
        if pair["relationship_scope_evidence"].get("state") == "SCOPED_SEMANTIC_RETURN"
        and pair["relationship_scope_evidence"].get("qualified") is True
        and pair["outcome_transition"] != "INCOMPLETE"
        and pair["tool_order_qualified"]
    )
    scope_exclusions = Counter(
        (
            "incomplete-pair-outcome"
            if pair["outcome_transition"] == "INCOMPLETE"
            else str(pair["relationship_scope_evidence"].get("reason") or "unknown")
        )
        for pair in pairs
        if pair["relationship_scope_evidence"].get("state") != "SCOPED_SEMANTIC_RETURN"
        or pair["relationship_scope_evidence"].get("qualified") is not True
        or pair["outcome_transition"] == "INCOMPLETE"
        or not pair["tool_order_qualified"]
    )
    return {
        "schema": MECHANISM_REPORT_SCHEMA,
        "pairs": pairs,
        "summary": {
            "paired_observations": len(pairs),
            "evaluation_assurance": build_assurance_summary(pairs),
            "relationship_scope_qualified_pairs": sum(scope_counts.values()),
            "relationship_scope_search_comparable_pairs": sum(
                pair["relationship_scope_evidence"].get("state") == "SCOPED_SEMANTIC_RETURN"
                and pair["relationship_scope_evidence"].get("qualified") is True
                and pair["native_search_comparison"]["qualified"] is True
                and pair["outcome_transition"] != "INCOMPLETE"
                for pair in pairs
            ),
            "relationship_scope_outcome_cross_tab": dict(sorted(scope_counts.items())),
            "relationship_scope_exclusion_reasons": dict(sorted(scope_exclusions.items())),
            "semantic_qualified_pairs": sum(
                pair["semantic_information_evidence"].get("qualified") is True
                and pair["outcome_transition"] != "INCOMPLETE"
                for pair in pairs
            ),
            "semantic_outcome_cross_tab": dict(sorted(semantic_counts.items())),
            "semantic_exclusion_reasons": dict(sorted(semantic_exclusions.items())),
            "information_qualified_pairs": sum(
                pair["information_evidence"].get("qualified") is True
                and pair["outcome_transition"] != "INCOMPLETE"
                for pair in pairs
            ),
            "information_outcome_cross_tab": dict(sorted(information_counts.items())),
            "information_exclusion_reasons": dict(sorted(information_exclusions.items())),
            "unpaired_groups": len(unpaired),
            "unavailable_bundles": len(unavailable_bundles),
            "outcome_transitions": dict(sorted(outcome_counts.items())),
            "treatment": dict(sorted(treatment_counts.items())),
            "routing": dict(sorted(routing_counts.items())),
            "attribution_strength": dict(sorted(strength_counts.items())),
            "attribution_result": dict(sorted(attribution_counts.items())),
            "mechanism_tags": dict(sorted(tag_counts.items())),
            "fail_to_pass_supported": sum(
                pair["outcome_transition"] == "FAIL_TO_PASS"
                and pair["attribution_strength"] == ATTRIBUTION_SUPPORTED
                for pair in pairs
            ),
            "never_invoked_pairs": treatment_counts[TREATMENT_NEVER_INVOKED],
            "never_invoked_credited_pairs": sum(
                pair["treatment"] == TREATMENT_NEVER_INVOKED
                and pair["attribution_interpretation"].startswith("supported-")
                for pair in pairs
            ),
            "positive_causal_proof_claimed": False,
        },
        "unpaired": unpaired,
        "unavailable_bundles": unavailable_bundles,
        "method": {
            "pair_authority": (
                "same campaign + task + harness + model + replicate; "
                "subject none versus hashmarks"
            ),
            "evidence": (
                "immutable result bundle + verifier answer evidence + ATIF "
                "tool calls/linked observations/token metrics"
            ),
            "reasoning_content_consumed": False,
            "message_content_consumed": False,
            "semantic_information_policy": (
                "semantic claims are compared to the same frozen behavioral "
                "oracle atoms after grading; matching a returned atom with a "
                "final answer is co-occurrence, not observed mental adoption; "
                "no oracle or model is run by the post-run evaluator"
            ),
            "information_policy": (
                "structured subject return is availability, not cognition; "
                "matching subsequent native reads is observed follow-through; "
                "incomplete tool order, missing oracle, or ambiguous linked "
                "observations deny information qualification"
            ),
            "delivery_evidence_policy": (
                "ATIF linked tool results prove return availability only. "
                "No independent model-input receipt is available, so delivered, "
                "attended-to and used remain UNKNOWN; an action matching the "
                "return is behavioral alignment, not proof of model cognition"
            ),
            "evaluation_assurance_policy": (
                "cluster-bootstrap intervals are descriptive when eight or more "
                "independent task/harness/model clusters exist; smaller populations "
                "never receive invented intervals or significance claims"
            ),
            "relationship_scope_policy": (
                "scoped semantic evidence must be present in a linked ATIF tool return; "
                "an exact follow-up structural_locality request is tool behavior, "
                "not proof of agent attention or causal benefit; paired native "
                "search deltas are descriptive only; opaque tool boundaries "
                "deny qualified search-savings interpretations"
            ),
            "positive_causal_claim_policy": (
                "pair mechanisms are descriptive or supported associations; "
                "positive causal proof requires a dedicated ablation"
            ),
        },
    }
