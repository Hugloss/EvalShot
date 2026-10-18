"""Derived, source-free diagnostics from verified benchmark agent traces."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any

from benchmarks.hashmarks_task_evidence import retrieval_candidate_matches
from benchmarks.tool_routing import (
    NATIVE_READ,
    NATIVE_SEARCH,
    SHELL,
    TOOL_ROUTER,
    classify_call,
    classify_tool,
    is_subject_tool,
    matches_subject_operation,
    result_bytes,
)

from .bundle import verify_bundle


class TraceDiagnosticError(ValueError):
    pass


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _relative_path(value: Any, workspace: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    root = Path(workspace)
    try:
        return str(path.relative_to(root)) if path.is_absolute() else str(path)
    except ValueError:
        return "<outside-workspace>"


def _tool_failure(status: Any, output: Any) -> str | None:
    if status not in {"error", "failed"}:
        return None
    rendered = str(output).lower()
    if "rejected permission" in rendered or "permission denied" in rendered:
        return "permission-denied"
    if "ripgrep execution failed" in rendered:
        return "search-backend-failed"
    if "file not found" in rendered:
        return "path-not-found"
    return "tool-error"


def _terminal_failure_cause(
    receipt: dict[str, Any], terminal_tool_failure: str | None,
) -> str | None:
    if receipt.get("status") != "INCOMPLETE":
        return None
    terminal = receipt.get("execution", {}).get("agent_terminal", {})
    if not isinstance(terminal, dict) or terminal.get("type") != "turn.failed":
        return None
    reason = str(terminal.get("reason") or "")
    if "ContextOverflowError" in reason or "ContextWindowExceededError" in reason:
        return "context-overflow"
    if "Cannot connect to API" in reason and "isRetryable" in reason:
        return "provider-connection-failed"
    if terminal_tool_failure == "permission-denied":
        return "permission-denied"
    if "terminal assistant message has no final text" in reason:
        return "no-final-text-unexplained"
    return "other-agent-terminal-failure"


def _retrieval_truncation_state(retrieval: Any) -> str:
    """Use only truncation metadata reported by the task-evidence packet."""
    if not isinstance(retrieval, dict):
        return "unknown"
    bounds = retrieval.get("bounds")
    canonical = bounds.get("canonical_truncation") if isinstance(bounds, dict) else None
    if isinstance(bounds, dict) and "canonical_truncation" in bounds:
        if not isinstance(canonical, str) or canonical not in {
            "complete", "truncated", "unknown"
        }:
            return "unknown"
        legacy = retrieval.get("truncated")
        if "truncated" in retrieval:
            if not isinstance(legacy, bool):
                return "unknown"
            if canonical != "unknown" and legacy != (canonical == "truncated"):
                return "unknown"
        return canonical
    if "truncated" in retrieval:
        legacy = retrieval["truncated"]
        if isinstance(legacy, bool):
            return "truncated" if legacy else "complete"
        return "unknown"
    return "not-reported"


def _hashmarks_evidence(output: Any, expected: dict[str, str] | None) -> dict[str, Any]:
    try:
        packet = json.loads(output) if isinstance(output, str) else output
    except ValueError:
        packet = None
    if not isinstance(packet, dict):
        return {"packet_status": "unparseable"}
    retrieval = packet.get("retrieval")
    results = retrieval.get("results") if isinstance(retrieval, dict) else None
    rank = None
    if isinstance(results, list) and expected:
        for index, candidate in enumerate(results, 1):
            if retrieval_candidate_matches(candidate, expected):
                rank = index
                break
    ownership = packet.get("ownership")
    next_read = (
        ownership.get("next_read")
        if isinstance(ownership, dict)
        and isinstance(ownership.get("next_read"), dict)
        else None
    )
    return {
        "packet_status": "parsed",
        "schema": packet.get("schema"),
        "retrieval_count": len(results) if isinstance(results, list) else None,
        "retrieval_truncation_state": _retrieval_truncation_state(retrieval),
        "canonical_omitted_results": (
            retrieval.get("canonical_omitted_results")
            if isinstance(retrieval, dict) else None
        ),
        "supplemental_results": (
            retrieval.get("supplemental_results")
            if isinstance(retrieval, dict) else None
        ),
        "expected_target_rank": rank,
        "expected_target_observability": (
            "observed" if rank is not None else "absent-from-returned-candidates"
            if isinstance(results, list) and expected else "unknown"
        ),
        "ownership_status": (
            ownership.get("status") if isinstance(ownership, dict) else None
        ),
        "next_read_path": (
            next_read.get("path") if isinstance(next_read, dict) else None
        ),
        "next_read_reason": (
            next_read.get("reason") if isinstance(next_read, dict) else None
        ),
        "next_read_authority": (
            next_read.get("authority") if isinstance(next_read, dict) else None
        ),
    }


def _normalize_repo_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    rendered = value.strip().replace("\\", "/")
    while rendered.startswith("./"):
        rendered = rendered[2:]
    return rendered or None


def _repository_candidate_evidence(
    output: Any,
) -> tuple[dict[str, Any], set[str], set[str]]:
    """Extract subject-returned candidate locators without consulting the oracle."""
    try:
        packet = json.loads(output) if isinstance(output, str) else output
    except ValueError:
        packet = None
    if not isinstance(packet, dict):
        return (
            {
                "state": "unparseable",
                "candidate_results": None,
                "candidate_paths_observed": 0,
                "candidate_symbols_observed": 0,
            },
            set(),
            set(),
        )

    retrieval = packet.get("retrieval")
    results = retrieval.get("results") if isinstance(retrieval, dict) else None
    if not isinstance(results, list):
        return (
            {
                "state": "unsupported-candidate-shape",
                "candidate_results": None,
                "candidate_paths_observed": 0,
                "candidate_symbols_observed": 0,
            },
            set(),
            set(),
        )

    paths: set[str] = set()
    symbols: set[str] = set()
    for candidate in results:
        if not isinstance(candidate, dict):
            continue
        path = _normalize_repo_path(candidate.get("path"))
        if path is not None:
            paths.add(path)
        for key in ("symbol", "qualname", "name"):
            value = candidate.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            rendered = value.strip()
            symbols.add(rendered)
            terminal = rendered.rsplit(".", 1)[-1]
            if terminal:
                symbols.add(terminal)

    return (
        {
            "state": (
                "candidate-locators-observed"
                if paths or symbols
                else "no-candidate-locators"
            ),
            "candidate_results": len(results),
            "candidate_paths_observed": len(paths),
            "candidate_symbols_observed": len(symbols),
            "locator_source": "subject-result.retrieval.results",
            "oracle_relative": False,
        },
        paths,
        symbols,
    )


_QUERY_KEYS = frozenset(
    {
        "pattern",
        "query",
        "q",
        "search",
        "search_query",
        "text",
        "symbol",
        "name",
    }
)


def _query_strings(value: Any, *, selected: bool = False) -> list[str]:
    if isinstance(value, str):
        return [value] if selected else []
    if isinstance(value, list):
        return [
            item
            for child in value
            for item in _query_strings(child, selected=selected)
        ]
    if not isinstance(value, dict):
        return []

    output: list[str] = []
    for key, child in value.items():
        key_selected = str(key).strip().lower() in _QUERY_KEYS
        output.extend(
            _query_strings(
                child,
                selected=selected or key_selected,
            )
        )
    return output


def _query_mentions_symbol(query: str, symbol: str) -> bool:
    query = query.strip()
    symbol = symbol.strip()
    if not query or not symbol:
        return False
    if query == symbol:
        return True
    return re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])",
        query,
    ) is not None


def _candidate_followup(
    *,
    inputs: dict[str, Any],
    tool_class: str,
    path_attempted: str | None,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    candidate_paths = evidence["paths"]
    candidate_symbols = evidence["symbols"]
    query_strings = _query_strings(inputs)
    normalized_path = _normalize_repo_path(path_attempted)
    bases: list[str] = []

    if tool_class == NATIVE_READ and normalized_path in candidate_paths:
        bases.append("candidate-path-read")
    if tool_class == NATIVE_SEARCH:
        if normalized_path in candidate_paths:
            bases.append("candidate-path-search-scope")
        if any(
            path in query
            for path in candidate_paths
            for query in query_strings
        ):
            bases.append("candidate-path-query")
        if any(
            _query_mentions_symbol(query, symbol)
            for symbol in candidate_symbols
            for query in query_strings
        ):
            bases.append("candidate-symbol-query")

    return {
        "source_subject_ordinal": evidence["ordinal"],
        "candidate_match": bool(bases),
        "match_basis": sorted(set(bases)),
        "observability": "direct-native-call-inputs",
    }


def _opencode_calls(trace: dict[str, Any], receipt: dict[str, Any]) -> list[dict[str, Any]]:
    subject = receipt.get("condition", {}).get("subject")
    expected = (
        receipt.get("task", {}).get("oracle", {}).get("configuration", {}).get("expected")
    )
    expected = expected if isinstance(expected, dict) else None
    workspace = str(receipt.get("execution", {}).get("workspace_root", ""))
    calls: list[dict[str, Any]] = []
    active_evidence: dict[str, Any] | None = None
    for message in trace.get("messages", []):
        if not isinstance(message, dict):
            continue
        for part in message.get("parts", []):
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            name = part.get("tool")
            state = part.get("state")
            state = state if isinstance(state, dict) else {}
            inputs = state.get("input")
            inputs = inputs if isinstance(inputs, dict) else {}
            status = state.get("status")
            output = state.get("error") or state.get("output")
            subject_call = is_subject_tool(name, subject)
            tool_class = classify_call(
                name,
                inputs,
                subject=subject,
            )
            if subject_call:
                # A new subject call ends attribution to any previous result.
                active_evidence = None

            row: dict[str, Any] = {
                "ordinal": len(calls) + 1,
                "tool": name,
                "tool_class": tool_class,
                "status": status,
                "failure": _tool_failure(status, output),
                "subject_call": subject_call,
                "input_sha256": _sha256(inputs),
                "input_fields": sorted(inputs),
            }
            if tool_class in {NATIVE_READ, NATIVE_SEARCH}:
                row["path_attempted"] = _relative_path(
                    inputs.get("filePath") or inputs.get("path"), workspace
                )
                if active_evidence is not None:
                    row["evidence_followup"] = _candidate_followup(
                        inputs=inputs,
                        tool_class=tool_class,
                        path_attempted=row["path_attempted"],
                        evidence=active_evidence,
                    )
            if subject_call:
                row["result_bytes"] = result_bytes(output)
                if status == "completed":
                    repository_evidence, paths, symbols = (
                        _repository_candidate_evidence(output)
                    )
                    row["repository_evidence"] = repository_evidence
                    if (
                        repository_evidence["state"]
                        == "candidate-locators-observed"
                        and isinstance(row["result_bytes"], int)
                        and not isinstance(row["result_bytes"], bool)
                        and row["result_bytes"] > 0
                    ):
                        active_evidence = {
                            "ordinal": row["ordinal"],
                            "paths": paths,
                            "symbols": symbols,
                        }
                if (
                    status == "completed"
                    and matches_subject_operation(
                        name,
                        subject="hashmarks",
                        operation="task_evidence",
                    )
                ):
                    row["hashmarks_evidence"] = _hashmarks_evidence(output, expected)
            if tool_class == TOOL_ROUTER:
                metadata = state.get("metadata")
                nested = (
                    metadata.get("toolCalls")
                    if isinstance(metadata, dict)
                    else None
                )
                row["routing_observability"] = (
                    "expanded" if isinstance(nested, list) else "opaque"
                )
            else:
                nested = None
            calls.append(row)

            if name == "execute" and isinstance(nested, list):
                for call in nested:
                    if not isinstance(call, dict):
                        continue
                    nested_name = call.get("tool")
                    nested_status = call.get("status")
                    nested_result = call.get("result")
                    nested_subject = is_subject_tool(nested_name, subject)
                    nested_class = classify_tool(
                        nested_name,
                        subject=subject,
                    )
                    nested_row: dict[str, Any] = {
                        "ordinal": len(calls) + 1,
                        "tool": nested_name,
                        "tool_class": nested_class,
                        "status": nested_status,
                        "failure": _tool_failure(
                            nested_status,
                            nested_result,
                        ),
                        "subject_call": nested_subject,
                        "input_sha256": None,
                        "input_fields": [],
                        "observability": "execute-metadata",
                        "result_bytes": None,
                    }
                    if (
                        active_evidence is not None
                        and nested_class in {NATIVE_READ, NATIVE_SEARCH}
                    ):
                        nested_row["evidence_followup"] = {
                            "source_subject_ordinal": active_evidence["ordinal"],
                            "candidate_match": None,
                            "match_basis": [],
                            "observability": "inputs-unavailable",
                        }
                    calls.append(nested_row)
                    if nested_subject:
                        # The nested result is not observable enough to establish
                        # a new candidate authority, so fail closed.
                        active_evidence = None
    return calls

def _last_assistant_text_present(trace: dict[str, Any]) -> bool | None:
    for message in reversed(trace.get("messages", [])):
        if not isinstance(message, dict):
            continue
        info = message.get("info")
        if not isinstance(info, dict) or info.get("role") != "assistant":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            return None
        return any(
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
            and bool(part["text"].strip())
            for part in parts
        )
    return None



def _numeric_summary(values: list[int | float]) -> dict[str, Any]:
    return {
        "observations": len(values),
        "mean": mean(values) if values else None,
        "median": median(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _repository_intelligence_quality(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate generic retrieval-quality diagnostics without ranking products."""

    hashmarks_calls: list[dict[str, Any]] = []
    for row in rows:
        for call in row.get("calls") or []:
            evidence = call.get("hashmarks_evidence")
            if isinstance(evidence, dict):
                hashmarks_calls.append(
                    {
                        "result_bytes": call.get("result_bytes"),
                        "evidence": evidence,
                    }
                )

    if not hashmarks_calls:
        return {
            "state": "not-observed",
            "claim_scope": "descriptive-diagnostic-only",
            "subjects": [],
        }

    packet_statuses = Counter(
        str(call["evidence"].get("packet_status"))
        for call in hashmarks_calls
        if isinstance(call["evidence"].get("packet_status"), str)
    )
    schemas = Counter(
        str(call["evidence"].get("schema"))
        for call in hashmarks_calls
        if isinstance(call["evidence"].get("schema"), str)
    )
    retrieval_counts = [
        int(value)
        for call in hashmarks_calls
        if isinstance(
            (value := call["evidence"].get("retrieval_count")),
            int,
        )
        and not isinstance(value, bool)
        and value >= 0
    ]
    result_sizes = [
        int(value)
        for call in hashmarks_calls
        if isinstance((value := call.get("result_bytes")), int)
        and not isinstance(value, bool)
        and value >= 0
    ]
    target_observability = Counter(
        str(call["evidence"].get("expected_target_observability"))
        for call in hashmarks_calls
        if isinstance(
            call["evidence"].get("expected_target_observability"),
            str,
        )
    )
    target_ranks = [
        int(value)
        for call in hashmarks_calls
        if isinstance(
            (value := call["evidence"].get("expected_target_rank")),
            int,
        )
        and not isinstance(value, bool)
        and value >= 1
    ]
    ownership = Counter(
        str(call["evidence"].get("ownership_status"))
        for call in hashmarks_calls
        if isinstance(call["evidence"].get("ownership_status"), str)
    )
    truncation = Counter(
        call["evidence"].get("retrieval_truncation_state", "unknown")
        for call in hashmarks_calls
    )
    canonical_omitted = [
        int(value)
        for call in hashmarks_calls
        if isinstance(
            (value := call["evidence"].get("canonical_omitted_results")),
            int,
        )
        and not isinstance(value, bool)
        and value >= 0
    ]
    supplemental = [
        int(value)
        for call in hashmarks_calls
        if isinstance(
            (value := call["evidence"].get("supplemental_results")),
            int,
        )
        and not isinstance(value, bool)
        and value >= 0
    ]

    diagnostic_complete = 0
    for call in hashmarks_calls:
        evidence = call["evidence"]
        observable_target = evidence.get("expected_target_observability")
        if (
            evidence.get("packet_status") == "parsed"
            and isinstance(evidence.get("retrieval_count"), int)
            and not isinstance(evidence.get("retrieval_count"), bool)
            and isinstance(evidence.get("ownership_status"), str)
            and isinstance(call.get("result_bytes"), int)
            and not isinstance(call.get("result_bytes"), bool)
            and observable_target
            in {"observed", "absent-from-returned-candidates"}
        ):
            diagnostic_complete += 1

    evaluable_targets = (
        target_observability.get("observed", 0)
        + target_observability.get("absent-from-returned-candidates", 0)
    )
    ownership_observations = sum(ownership.values())
    rank_buckets = {
        "1": sum(rank == 1 for rank in target_ranks),
        "2-5": sum(2 <= rank <= 5 for rank in target_ranks),
        "6-10": sum(6 <= rank <= 10 for rank in target_ranks),
        "11+": sum(rank >= 11 for rank in target_ranks),
    }
    total_calls = len(hashmarks_calls)
    if diagnostic_complete == total_calls:
        sufficiency_state = "complete"
    elif diagnostic_complete:
        sufficiency_state = "partial"
    else:
        sufficiency_state = "insufficient"

    return {
        "state": "observed",
        "claim_scope": "descriptive-diagnostic-only",
        "subjects": [
            {
                "subject_id": "hashmarks",
                "operation": "task_evidence",
                "calls": total_calls,
                "packet_status_counts": dict(sorted(packet_statuses.items())),
                "schema_counts": dict(sorted(schemas.items())),
                "candidate_set_size": _numeric_summary(retrieval_counts),
                "result_payload_bytes": _numeric_summary(result_sizes),
                "retrieval_truncation": {
                    "complete": truncation.get("complete", 0),
                    "truncated": truncation.get("truncated", 0),
                    "unknown": truncation.get("unknown", 0),
                    "not-reported": truncation.get("not-reported", 0),
                },
                "canonical_omission": {
                    "calls_with_omission": sum(value > 0 for value in canonical_omitted),
                    "observed_calls": len(canonical_omitted),
                    "unknown_calls": total_calls - len(canonical_omitted),
                    "omitted_results": _numeric_summary(canonical_omitted),
                },
                "supplemental_results": _numeric_summary(supplemental),
                "oracle_relative_target_observability": {
                    "counts": dict(sorted(target_observability.items())),
                    "evaluable_calls": evaluable_targets,
                    "observed_calls": target_observability.get("observed", 0),
                    "coverage_rate": (
                        target_observability.get("observed", 0)
                        / evaluable_targets
                        if evaluable_targets
                        else None
                    ),
                },
                "oracle_relative_target_rank": {
                    **_numeric_summary(target_ranks),
                    "buckets": rank_buckets,
                },
                "ownership": {
                    "observations": ownership_observations,
                    "status_counts": dict(sorted(ownership.items())),
                    "ambiguous_rate": (
                        ownership.get("ambiguous", 0) / ownership_observations
                        if ownership_observations
                        else None
                    ),
                    "unresolved_rate": (
                        ownership.get("unresolved", 0) / ownership_observations
                        if ownership_observations
                        else None
                    ),
                },
                "evidence_sufficiency": {
                    "state": sufficiency_state,
                    "complete_calls": diagnostic_complete,
                    "total_calls": total_calls,
                    "complete_rate": (
                        diagnostic_complete / total_calls if total_calls else None
                    ),
                    "required_fields": [
                        "parsed-packet",
                        "retrieval-count",
                        "ownership-status",
                        "result-bytes",
                        "oracle-target-observability",
                    ],
                },
                "interpretation": {
                    "target_rank": (
                        "aggregate oracle-relative benchmark observation only; "
                        "not a product acceptance threshold or optimization target"
                    ),
                    "ownership": (
                        "subject-reported packet status; benchmark does not "
                        "reinterpret ownership certainty"
                    ),
                    "candidate_set_and_payload": (
                        "descriptive retrieval-shape and transport-cost evidence"
                    ),
                },
            }
        ],
    }


def _native_call_signature(call: dict[str, Any]) -> tuple[str, str, str] | None:
    tool_class = call.get("tool_class")
    tool = call.get("tool")
    input_sha256 = call.get("input_sha256")
    if (
        tool_class not in {NATIVE_SEARCH, NATIVE_READ}
        or not isinstance(tool, str)
        or not tool
        or not isinstance(input_sha256, str)
        or not input_sha256
    ):
        return None
    return tool_class, tool, input_sha256


def _subject_next_read_followups(
    calls: list[dict[str, Any]],
) -> dict[str, Any]:
    outcomes: Counter[str] = Counter()
    emitted = 0
    for index, call in enumerate(calls):
        evidence = call.get("hashmarks_evidence")
        if not isinstance(evidence, dict):
            continue
        advised = evidence.get("next_read_path")
        if not isinstance(advised, str) or not advised:
            continue
        emitted += 1
        outcome = "no-native-navigation"
        for later in calls[index + 1 :]:
            if later.get("subject_call") is True:
                break
            tool_class = later.get("tool_class")
            if tool_class == NATIVE_SEARCH:
                outcome = "search-first"
                break
            if tool_class == NATIVE_READ:
                attempted = later.get("path_attempted")
                outcome = (
                    "advised-read-first"
                    if isinstance(attempted, str) and attempted == advised
                    else "different-read-first"
                )
                break
        outcomes[outcome] += 1
    followed = outcomes.get("advised-read-first", 0)
    return {
        "emitted_calls": emitted,
        "followup_counts": dict(sorted(outcomes.items())),
        "advised_read_first_rate": followed / emitted if emitted else None,
        "claim_scope": "descriptive-followup-only",
    }


def _trial_search_efficiency(
    calls: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Describe observed repository navigation without inferring command semantics."""
    if calls is None:
        return None

    subject_calls = [call for call in calls if call.get("subject_call") is True]
    subject_ordinals = [
        int(call["ordinal"])
        for call in subject_calls
        if isinstance(call.get("ordinal"), int)
        and not isinstance(call.get("ordinal"), bool)
    ]
    first_subject = min(subject_ordinals) if subject_ordinals else None
    last_subject = max(subject_ordinals) if subject_ordinals else None
    subject_results_observed = sum(
        call.get("status") == "completed"
        and isinstance(call.get("result_bytes"), int)
        and not isinstance(call.get("result_bytes"), bool)
        for call in subject_calls
    )

    native_calls = [
        call
        for call in calls
        if call.get("tool_class") in {NATIVE_SEARCH, NATIVE_READ}
    ]
    search_calls = [
        call for call in native_calls if call.get("tool_class") == NATIVE_SEARCH
    ]
    read_calls = [
        call for call in native_calls if call.get("tool_class") == NATIVE_READ
    ]
    shell_calls = [call for call in calls if call.get("tool_class") == SHELL]

    def before(call: dict[str, Any]) -> bool:
        return (
            first_subject is not None
            and isinstance(call.get("ordinal"), int)
            and call["ordinal"] < first_subject
        )

    def after(call: dict[str, Any]) -> bool:
        return (
            last_subject is not None
            and isinstance(call.get("ordinal"), int)
            and call["ordinal"] > last_subject
        )

    seen: set[tuple[str, str, str]] = set()
    exact_repeats = 0
    post_subject_exact_repeats = 0
    for call in native_calls:
        signature = _native_call_signature(call)
        if signature is None:
            continue
        repeated = signature in seen
        if repeated:
            exact_repeats += 1
            if after(call):
                post_subject_exact_repeats += 1
        seen.add(signature)

    post_subject_search = sum(after(call) for call in search_calls)
    post_subject_read = sum(after(call) for call in read_calls)
    post_subject_native = post_subject_search + post_subject_read

    return {
        "subject_calls": len(subject_calls),
        "subject_result_observed_calls": subject_results_observed,
        "subject_next_read_guidance": _subject_next_read_followups(calls),
        "first_subject_ordinal": first_subject,
        "last_subject_ordinal": last_subject,
        "native_search_calls": len(search_calls),
        "native_read_calls": len(read_calls),
        "native_navigation_calls": len(native_calls),
        "shell_calls": len(shell_calls),
        "pre_subject_native_search_calls": sum(before(call) for call in search_calls),
        "pre_subject_native_read_calls": sum(before(call) for call in read_calls),
        "pre_subject_native_navigation_calls": sum(
            before(call) for call in native_calls
        ),
        "post_subject_native_search_calls": post_subject_search,
        "post_subject_native_read_calls": post_subject_read,
        "post_subject_native_navigation_calls": post_subject_native,
        "post_subject_shell_calls": sum(after(call) for call in shell_calls),
        "no_native_search_after_subject": (
            post_subject_search == 0 if last_subject is not None else None
        ),
        "no_native_navigation_after_subject": (
            post_subject_native == 0 if last_subject is not None else None
        ),
        "exact_repeat_native_navigation_calls": exact_repeats,
        "post_subject_exact_repeat_native_navigation_calls": (
            post_subject_exact_repeats
        ),
        "exact_repeat_signature_observability": (
            "direct-call-input-hash-only"
        ),
    }


def _trace_pair_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("task_id"),
        row.get("agent_id"),
        row.get("trial_index"),
        row.get("replicate_id"),
        row.get("context_group"),
        row.get("context_variant"),
    )


def _delta_summary(values: list[int | float]) -> dict[str, Any]:
    return {
        **_numeric_summary(values),
        "reduced": sum(value < 0 for value in values),
        "same": sum(value == 0 for value in values),
        "increased": sum(value > 0 for value in values),
    }


def _trial_evidence_to_action(
    calls: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Classify observed action on subject-returned candidates without oracle use."""
    if calls is None:
        return None

    subject_ordinals = sorted(
        int(call["ordinal"])
        for call in calls
        if call.get("subject_call") is True
        and isinstance(call.get("ordinal"), int)
        and not isinstance(call.get("ordinal"), bool)
    )
    evidence_calls = [
        call
        for call in calls
        if call.get("subject_call") is True
        and isinstance(call.get("repository_evidence"), dict)
        and call["repository_evidence"].get("state")
        == "candidate-locators-observed"
        and isinstance(call.get("result_bytes"), int)
        and not isinstance(call.get("result_bytes"), bool)
        and call["result_bytes"] > 0
    ]

    segments: list[dict[str, Any]] = []
    for source in evidence_calls:
        source_ordinal = int(source["ordinal"])
        next_subject = next(
            (
                ordinal
                for ordinal in subject_ordinals
                if ordinal > source_ordinal
            ),
            None,
        )
        segment_calls = [
            call
            for call in calls
            if isinstance(call.get("ordinal"), int)
            and call["ordinal"] > source_ordinal
            and (next_subject is None or call["ordinal"] < next_subject)
        ]
        native_calls = [
            call
            for call in segment_calls
            if call.get("tool_class") in {NATIVE_SEARCH, NATIVE_READ}
        ]
        followed = [
            call
            for call in native_calls
            if call.get("evidence_followup", {}).get("source_subject_ordinal")
            == source_ordinal
            and call.get("evidence_followup", {}).get("candidate_match") is True
        ]
        unresolved_native = [
            call
            for call in native_calls
            if call.get("evidence_followup", {}).get("source_subject_ordinal")
            == source_ordinal
            and call.get("evidence_followup", {}).get("candidate_match") is None
        ]
        unmatched_native = [
            call
            for call in native_calls
            if call.get("evidence_followup", {}).get("source_subject_ordinal")
            == source_ordinal
            and call.get("evidence_followup", {}).get("candidate_match") is False
        ]
        opaque_router_calls = [
            call
            for call in segment_calls
            if call.get("tool_class") == TOOL_ROUTER
            and call.get("routing_observability") == "opaque"
        ]
        shell_calls = [
            call
            for call in segment_calls
            if call.get("tool_class") == SHELL
        ]

        basis_counts = Counter(
            basis
            for call in followed
            for basis in call.get("evidence_followup", {}).get("match_basis", [])
            if isinstance(basis, str)
        )
        followed_ordinals = [
            int(call["ordinal"])
            for call in followed
            if isinstance(call.get("ordinal"), int)
        ]
        if followed:
            state = "candidate-followed"
        elif unresolved_native or opaque_router_calls:
            state = "followup-unresolved"
        elif unmatched_native:
            state = "other-native-navigation"
        elif shell_calls:
            state = "shell-only-followup-unknown"
        else:
            state = "no-observed-native-followup"

        segments.append(
            {
                "source_subject_ordinal": source_ordinal,
                "next_subject_ordinal": next_subject,
                "candidate_results": source["repository_evidence"].get(
                    "candidate_results"
                ),
                "candidate_paths_observed": source["repository_evidence"].get(
                    "candidate_paths_observed"
                ),
                "candidate_symbols_observed": source["repository_evidence"].get(
                    "candidate_symbols_observed"
                ),
                "state": state,
                "candidate_followup_calls": len(followed),
                "candidate_followup_basis_counts": dict(sorted(basis_counts.items())),
                "other_native_navigation_calls": len(unmatched_native),
                "unresolved_native_navigation_calls": len(unresolved_native),
                "opaque_router_calls": len(opaque_router_calls),
                "shell_followup_calls_unknown_semantics": len(shell_calls),
                "first_candidate_followup_ordinal_delta": (
                    min(followed_ordinals) - source_ordinal
                    if followed_ordinals
                    else None
                ),
            }
        )

    return {
        "candidate_evidence_segments": len(segments),
        "segments": segments,
        "state_counts": dict(
            sorted(Counter(segment["state"] for segment in segments).items())
        ),
        "candidate_followed_segments": sum(
            segment["state"] == "candidate-followed"
            for segment in segments
        ),
        "oracle_relative": False,
        "correctness_joined": False,
        "observability": "direct-subject-result-and-native-inputs-only",
    }


def _repository_intelligence_evidence_to_action(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        subject_id = row.get("subject_id")
        evidence = row.get("evidence_to_action")
        if (
            not isinstance(subject_id, str)
            or not subject_id
            or subject_id == "none"
            or not isinstance(evidence, dict)
            or int(evidence.get("candidate_evidence_segments", 0) or 0) <= 0
        ):
            continue
        by_subject.setdefault(subject_id, []).append(row)

    if not by_subject:
        return {
            "state": "not-observed",
            "claim_scope": "descriptive-behavioral-only",
            "oracle_relative": False,
            "correctness_joined": False,
            "subjects": [],
        }

    subjects: list[dict[str, Any]] = []
    for subject_id, subject_rows in sorted(by_subject.items()):
        segments = [
            segment
            for row in subject_rows
            for segment in row["evidence_to_action"].get("segments", [])
            if isinstance(segment, dict)
        ]
        state_counts = Counter(
            str(segment["state"])
            for segment in segments
            if isinstance(segment.get("state"), str)
        )
        basis_counts = Counter(
            str(basis)
            for segment in segments
            for basis, count in (
                segment.get("candidate_followup_basis_counts", {}) or {}
            ).items()
            for _ in range(int(count))
        )
        ordinal_deltas = [
            int(value)
            for segment in segments
            if isinstance(
                (value := segment.get("first_candidate_followup_ordinal_delta")),
                int,
            )
            and not isinstance(value, bool)
            and value >= 1
        ]
        followed = state_counts.get("candidate-followed", 0)
        subjects.append(
            {
                "subject_id": subject_id,
                "evidence_observed_trials": len(subject_rows),
                "candidate_evidence_segments": len(segments),
                "action_state_counts": dict(sorted(state_counts.items())),
                "candidate_followed_segments": followed,
                "candidate_followup_rate": (
                    followed / len(segments) if segments else None
                ),
                "candidate_followup_basis_counts": dict(sorted(basis_counts.items())),
                "first_candidate_followup_ordinal_delta": _numeric_summary(
                    ordinal_deltas
                ),
                "other_native_navigation_calls": sum(
                    int(segment.get("other_native_navigation_calls", 0) or 0)
                    for segment in segments
                ),
                "unresolved_native_navigation_calls": sum(
                    int(
                        segment.get(
                            "unresolved_native_navigation_calls",
                            0,
                        )
                        or 0
                    )
                    for segment in segments
                ),
                "shell_followup_calls_unknown_semantics": sum(
                    int(
                        segment.get(
                            "shell_followup_calls_unknown_semantics",
                            0,
                        )
                        or 0
                    )
                    for segment in segments
                ),
                "interpretation": {
                    "candidate_followed": (
                        "a directly observed native read/search matched a locator "
                        "returned by the immediately preceding subject result"
                    ),
                    "other_native_navigation": (
                        "native repository navigation was observed, but no exact "
                        "returned candidate locator match was established"
                    ),
                    "no_observed_native_followup": (
                        "not equivalent to ignored evidence; the agent may answer "
                        "from the subject result or use unclassified/opaque tools"
                    ),
                    "correctness": (
                        "not joined; following or not following subject evidence "
                        "does not change semantic task correctness"
                    ),
                    "oracle": (
                        "candidate-action matching uses subject-returned locators "
                        "only and does not consult the frozen oracle"
                    ),
                },
            }
        )

    return {
        "state": "observed",
        "claim_scope": "descriptive-behavioral-only",
        "oracle_relative": False,
        "correctness_joined": False,
        "subjects": subjects,
    }


def _repository_intelligence_search_efficiency(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare observed navigation behavior without treating fewer calls as correctness."""

    baseline = {
        _trace_pair_key(row): row
        for row in rows
        if row.get("subject_id") == "none"
        and isinstance(row.get("search_efficiency"), dict)
    }
    by_subject: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        subject_id = row.get("subject_id")
        metrics = row.get("search_efficiency")
        if (
            not isinstance(subject_id, str)
            or not subject_id
            or subject_id == "none"
            or row.get("subject_invocation_observed") is not True
            or not isinstance(metrics, dict)
            or int(metrics.get("subject_result_observed_calls", 0) or 0) <= 0
        ):
            continue
        by_subject.setdefault(subject_id, []).append(row)

    if not by_subject:
        return {
            "state": "not-observed",
            "claim_scope": "descriptive-behavioral-only",
            "correctness_joined": False,
            "subjects": [],
        }

    subjects = []
    for subject_id, assisted_rows in sorted(by_subject.items()):
        paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for assisted in assisted_rows:
            bare = baseline.get(_trace_pair_key(assisted))
            if bare is not None:
                paired.append((bare, assisted))

        post_search = [
            int(row["search_efficiency"]["post_subject_native_search_calls"])
            for row in assisted_rows
        ]
        post_read = [
            int(row["search_efficiency"]["post_subject_native_read_calls"])
            for row in assisted_rows
        ]
        post_navigation = [
            int(row["search_efficiency"]["post_subject_native_navigation_calls"])
            for row in assisted_rows
        ]
        post_repeats = [
            int(
                row["search_efficiency"][
                    "post_subject_exact_repeat_native_navigation_calls"
                ]
            )
            for row in assisted_rows
        ]
        pre_navigation = [
            int(row["search_efficiency"]["pre_subject_native_navigation_calls"])
            for row in assisted_rows
        ]
        post_shell = [
            int(row["search_efficiency"]["post_subject_shell_calls"])
            for row in assisted_rows
        ]

        def paired_delta(metric: str) -> list[int]:
            return [
                int(assisted["search_efficiency"][metric])
                - int(bare["search_efficiency"][metric])
                for bare, assisted in paired
            ]

        no_search = sum(
            row["search_efficiency"]["no_native_search_after_subject"] is True
            for row in assisted_rows
        )
        no_navigation = sum(
            row["search_efficiency"]["no_native_navigation_after_subject"] is True
            for row in assisted_rows
        )
        next_read_followups: Counter[str] = Counter()
        next_read_emitted = 0
        for row in assisted_rows:
            guidance = row["search_efficiency"].get("subject_next_read_guidance")
            if not isinstance(guidance, dict):
                continue
            next_read_emitted += int(guidance.get("emitted_calls", 0) or 0)
            counts = guidance.get("followup_counts")
            if isinstance(counts, dict):
                next_read_followups.update(
                    {
                        str(key): int(value)
                        for key, value in counts.items()
                        if isinstance(value, int) and not isinstance(value, bool)
                    }
                )
        subjects.append(
            {
                "subject_id": subject_id,
                "evidence_observed_trials": len(assisted_rows),
                "post_subject": {
                    "native_search_calls": _numeric_summary(post_search),
                    "native_read_calls": _numeric_summary(post_read),
                    "native_navigation_calls": _numeric_summary(post_navigation),
                    "exact_repeat_native_navigation_calls": _numeric_summary(
                        post_repeats
                    ),
                    "shell_calls_unknown_semantics": _numeric_summary(post_shell),
                    "no_native_search_trials": no_search,
                    "no_native_search_rate": (
                        no_search / len(assisted_rows) if assisted_rows else None
                    ),
                    "no_native_navigation_trials": no_navigation,
                    "no_native_navigation_rate": (
                        no_navigation / len(assisted_rows)
                        if assisted_rows
                        else None
                    ),
                    "next_read_guidance": {
                        "emitted_calls": next_read_emitted,
                        "followup_counts": dict(sorted(next_read_followups.items())),
                        "advised_read_first_rate": (
                            next_read_followups.get("advised-read-first", 0)
                            / next_read_emitted
                            if next_read_emitted
                            else None
                        ),
                        "claim_scope": "descriptive-followup-only",
                    },
                },
                "pre_subject": {
                    "native_navigation_calls": _numeric_summary(pre_navigation),
                },
                "paired_vs_bare": {
                    "eligible_assisted_trials": len(assisted_rows),
                    "comparable_pairs": len(paired),
                    "unpaired_trials": len(assisted_rows) - len(paired),
                    "delta_policy": "assisted-minus-bare",
                    "native_search_calls_delta": _delta_summary(
                        paired_delta("native_search_calls")
                    ),
                    "native_read_calls_delta": _delta_summary(
                        paired_delta("native_read_calls")
                    ),
                    "native_navigation_calls_delta": _delta_summary(
                        paired_delta("native_navigation_calls")
                    ),
                    "exact_repeat_native_navigation_calls_delta": _delta_summary(
                        paired_delta("exact_repeat_native_navigation_calls")
                    ),
                },
                "interpretation": {
                    "correctness": (
                        "not joined; fewer observed calls are not scored as better "
                        "and do not change semantic task correctness"
                    ),
                    "native_search": (
                        "counts only directly classified native search/read calls; "
                        "shell command semantics are not inferred"
                    ),
                    "exact_repeat": (
                        "same observed tool name plus exact input hash; structural "
                        "repetition is not proof that a call was unnecessary"
                    ),
                    "paired_delta": (
                        "descriptive assisted-minus-bare comparison for matched "
                        "task/agent/replicate/context pairs"
                    ),
                    "attribution": (
                        "requires observed subject invocation plus at least one "
                        "completed subject result with observable result bytes"
                    ),
                },
            }
        )

    return {
        "state": "observed",
        "claim_scope": "descriptive-behavioral-only",
        "correctness_joined": False,
        "subjects": subjects,
    }

def build_trace_diagnostics(results_root: Path) -> dict[str, Any]:
    """Inspect every verified result bundle; never turn an absent trace into no calls."""
    if not results_root.is_dir():
        raise TraceDiagnosticError(f"results directory is missing: {results_root}")
    rows: list[dict[str, Any]] = []
    for directory in sorted(results_root.iterdir()):
        if directory.name.startswith("."):
            continue
        valid, reason = verify_bundle(directory)
        if not valid:
            raise TraceDiagnosticError(f"invalid bundle {directory}: {reason}")
        receipt = json.loads((directory / "result.json").read_text(encoding="utf-8"))
        evidence = receipt["execution"]["artifacts"].get("agent_trace")
        if not isinstance(evidence, dict):
            trace_state = "missing"
            calls = None
        else:
            trace_path = directory / evidence["path"]
            try:
                trace = json.loads(trace_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                trace = None
            if isinstance(trace, dict) and isinstance(trace.get("messages"), list):
                trace_state = "opencode-export"
                calls = _opencode_calls(trace, receipt)
                final_text_present = _last_assistant_text_present(trace)
            else:
                trace_state = "unsupported-format"
                calls = None
                final_text_present = None
        if not isinstance(evidence, dict):
            final_text_present = None
        condition = receipt.get("condition", {})
        execution = receipt.get("execution", {})
        subject_definition = condition.get("subject_definition", {})
        agent_definition = condition.get("agent_definition", {})
        context = condition.get("context")
        context = context if isinstance(context, dict) else {}
        subject = (
            subject_definition.get("id")
            if isinstance(subject_definition, dict)
            and isinstance(subject_definition.get("id"), str)
            else condition.get("subject")
        )
        agent_id = (
            agent_definition.get("id")
            if isinstance(agent_definition, dict)
            and isinstance(agent_definition.get("id"), str)
            else condition.get("agent")
        )
        replicate_id = execution.get("replicate_id", execution.get("seed"))
        terminal_tool_failure = calls[-1]["failure"] if calls else None
        row = {
            "trial_id": receipt["trial_id"],
            "definition_id": receipt["definition_id"],
            "task_id": receipt.get("task", {}).get("id"),
            "condition_id": condition.get("id"),
            "agent_id": agent_id,
            "subject_id": subject,
            "trial_index": execution.get("trial_index"),
            "replicate_id": replicate_id,
            "context_group": context.get("group"),
            "context_variant": context.get("variant"),
            "status": receipt.get("status"),
            "reason": receipt.get("reason"),
            "trace_state": trace_state,
            "last_assistant_text_present": final_text_present,
            "terminal_tool_failure": terminal_tool_failure,
            "terminal_failure_cause": _terminal_failure_cause(
                receipt, terminal_tool_failure
            ),
            "trace_path": (
                f"results/{directory.name}/{evidence['path']}"
                if isinstance(evidence, dict) else None
            ),
            "trace_sha256": evidence.get("sha256") if isinstance(evidence, dict) else None,
            "session_export_attempts": receipt.get("measurements", {}).get("agent", {}).get("session_export_attempts"),
            "subject_invocation_observed": receipt.get("measurements", {}).get("agent", {}).get("subject_tool_invoked"),
            "search_efficiency": _trial_search_efficiency(calls),
            "evidence_to_action": _trial_evidence_to_action(calls),
            "calls": calls,
        }
        rows.append(row)
    subject_calls = Counter()
    tool_failures = Counter()
    target_absences = Counter()
    for row in rows:
        for call in row["calls"] or []:
            if call["subject_call"]:
                subject_calls[str(row["subject_id"])] += 1
            if call["failure"]:
                tool_failures[call["failure"]] += 1
            if (
                call.get("hashmarks_evidence", {}).get("expected_target_observability")
                == "absent-from-returned-candidates"
            ):
                target_absences[str(row["task_id"])] += 1
    return {
        "schema": "agents-cookbook-trace-diagnostics.v6",
        "authority": {"derived_only": True, "source": "verified-result-bundles"},
        "trials": rows,
        "summary": {
            "trials": len(rows),
            "trace_states": dict(sorted(Counter(row["trace_state"] for row in rows).items())),
            "tool_classes": dict(sorted(Counter(
                call["tool_class"]
                for row in rows
                for call in (row["calls"] or [])
            ).items())),
            "subject_calls": dict(sorted(subject_calls.items())),
            "tool_failures": dict(sorted(tool_failures.items())),
            "terminal_failure_causes": dict(sorted(Counter(
                row["terminal_failure_cause"]
                for row in rows
                if row["terminal_failure_cause"] is not None
            ).items())),
            "hashmarks_expected_target_absent_from_returned_candidates": dict(sorted(target_absences.items())),
            "repository_intelligence_quality": _repository_intelligence_quality(rows),
            "repository_intelligence_search_efficiency": (
                _repository_intelligence_search_efficiency(rows)
            ),
            "repository_intelligence_evidence_to_action": (
                _repository_intelligence_evidence_to_action(rows)
            ),
        },
    }
