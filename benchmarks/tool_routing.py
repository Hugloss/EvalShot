"""Host-neutral semantic classification for repository tool-routing evidence."""

from __future__ import annotations

import hashlib
import json

from benchmarks.harness.tool_results import result_bytes
import re
from typing import Any

SUBJECT_REPOSITORY_INTELLIGENCE = "subject-repository-intelligence"
NATIVE_SEARCH = "native-search"
NATIVE_READ = "native-read"
SHELL = "shell"
TOOL_ROUTER = "tool-router"
OTHER = "other"

ROUTING_FIRST_CHOICE = "FIRST_CHOICE"
ROUTING_LATE_RESCUE = "LATE_RESCUE"
ROUTING_NEVER_INVOKED = "NEVER_INVOKED"
ROUTING_UNKNOWN = "UNKNOWN"
ROUTING_NOT_CONFIGURED = "NOT_CONFIGURED"

DISCOVERY_CLASSES = frozenset({NATIVE_SEARCH, NATIVE_READ, SHELL})


def routing_artifact_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


_SEARCH_OPERATIONS = frozenset({
    "grep",
    "rg",
    "ripgrep",
    "glob",
    "search",
    "search_code",
    "find",
    "find_files",
    "list_files",
})
_READ_OPERATIONS = frozenset({
    "read",
    "read_file",
    "fetch_file",
    "get_file",
    "open_file",
})
_SHELL_OPERATIONS = frozenset({
    "bash",
    "shell",
    "terminal",
    "run_command",
    "command_execution",
})
_ROUTER_OPERATIONS = frozenset({
    "execute",
    "exec",
    "functions_exec",
})

_SEPARATOR = re.compile(r"(?:::|__|[./])+")


def tool_tokens(name: object) -> tuple[str, ...]:
    if not isinstance(name, str):
        return ()
    normalized = name.strip().lower().replace("-", "_")
    if not normalized:
        return ()
    return tuple(token for token in _SEPARATOR.split(normalized) if token)


def _subject_tokens(subject: object) -> tuple[str, ...]:
    if not isinstance(subject, str):
        return ()
    value = subject.strip().lower().replace("-", "_")
    return (value,) if value and value != "none" else ()


def is_subject_tool(name: object, subject: object) -> bool:
    tokens = tool_tokens(name)
    subject_tokens = _subject_tokens(subject)
    if not tokens or not subject_tokens:
        return False
    selected = subject_tokens[0]
    if selected in tokens:
        return True
    rendered = "_".join(tokens)
    return rendered.startswith(selected + "_")


def _operation_candidates(tokens: tuple[str, ...]) -> tuple[str, ...]:
    if not tokens:
        return ()
    candidates = [tokens[-1]]
    if len(tokens) >= 2:
        candidates.append("_".join(tokens[-2:]))
    return tuple(dict.fromkeys(candidates))


def matches_subject_operation(
    name: object,
    *,
    subject: object,
    operation: str,
) -> bool:
    if not is_subject_tool(name, subject):
        return False
    tokens = tool_tokens(name)
    normalized = operation.strip().lower().replace("-", "_")
    selected = _subject_tokens(subject)[0]
    rendered = "_".join(tokens)
    return (
        normalized in _operation_candidates(tokens)
        or rendered == f"{selected}_{normalized}"
        or rendered.endswith(f"_{selected}_{normalized}")
    )


def classify_tool(name: object, *, subject: object = None) -> str:
    """Map host-specific tool names into one routing-scoring vocabulary."""
    if is_subject_tool(name, subject):
        return SUBJECT_REPOSITORY_INTELLIGENCE

    tokens = tool_tokens(name)
    candidates = _operation_candidates(tokens)
    if (
        "container" in tokens
        and any(candidate == "exec" for candidate in candidates)
    ):
        return SHELL
    if any(candidate in _SEARCH_OPERATIONS for candidate in candidates):
        return NATIVE_SEARCH
    if any(candidate in _READ_OPERATIONS for candidate in candidates):
        return NATIVE_READ
    if any(candidate in _SHELL_OPERATIONS for candidate in candidates):
        return SHELL
    if any(candidate in _ROUTER_OPERATIONS for candidate in candidates):
        return TOOL_ROUTER
    return OTHER


def classify_call(
    name: object,
    inputs: object = None,
    *,
    subject: object = None,
) -> str:
    """Classify a concrete call, including generic API discovery surfaces."""
    base = classify_tool(name, subject=subject)
    if base != OTHER:
        return base
    if not isinstance(inputs, dict):
        return base

    tokens = tool_tokens(name)
    operation = tokens[-1] if tokens else ""
    url = inputs.get("url")
    if operation == "fetch" and isinstance(url, str):
        lowered = url.lower()
        if "/git/trees/" in lowered or "/search/code" in lowered:
            return NATIVE_SEARCH
        if "/contents/" in lowered or "/git/blobs/" in lowered:
            return NATIVE_READ
    return base


def first_discovery_index(calls: list[dict[str, Any]]) -> int:
    return next(
        (
            index
            for index, call in enumerate(calls)
            if call.get("tool_class") in DISCOVERY_CLASSES
        ),
        len(calls),
    )


def subject_routing_timing(
    tool_sequence: list[str] | tuple[str, ...],
    subject_ordinals: list[int] | tuple[int, ...],
    *,
    configured: bool,
    invocation_observed: bool | None,
    order_complete: bool,
) -> str:
    """Classify when a configured repository-intelligence subject entered the run.

    Subject ordinals are observed by the host adapter. Tool classification remains
    host-neutral here. Positive evidence is asymmetric: an observed native
    discovery before the first subject call proves LATE_RESCUE, and ordinal 1
    proves FIRST_CHOICE. Partial order without either proof remains UNKNOWN.
    """
    if not configured:
        return ROUTING_NOT_CONFIGURED
    if invocation_observed is False:
        return ROUTING_NEVER_INVOKED
    if invocation_observed is not True:
        return ROUTING_UNKNOWN

    valid_ordinals = [
        value
        for value in subject_ordinals
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= len(tool_sequence)
    ]
    if not valid_ordinals:
        return ROUTING_UNKNOWN

    first_subject = min(valid_ordinals)
    prior = tool_sequence[: first_subject - 1]
    if any(
        classify_tool(name.removeprefix("nested:")) in DISCOVERY_CLASSES
        for name in prior
    ):
        return ROUTING_LATE_RESCUE
    if first_subject == 1 or order_complete:
        return ROUTING_FIRST_CHOICE
    return ROUTING_UNKNOWN


def catalog_tool_names(payload: object) -> list[str]:
    """Extract tool names from a simple list or a captured host catalog object."""
    raw = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise ValueError("tool catalog must be a list or an object with a tools list")

    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            name = item["name"]
        else:
            raise ValueError("every tool catalog entry must be a name or object with name")
        name = name.strip()
        if not name:
            raise ValueError("tool catalog names must not be empty")
        names.append(name)
    if not names:
        raise ValueError("tool catalog must contain at least one tool")
    return names


def catalog_discovery_classes(
    name: object,
    *,
    subject: str,
) -> frozenset[str]:
    base = classify_tool(name, subject=subject)
    if base in DISCOVERY_CLASSES:
        return frozenset({base})

    tokens = tool_tokens(name)
    if tokens and tokens[-1] == "fetch" and "github" in tokens:
        return frozenset({NATIVE_READ, NATIVE_SEARCH})
    return frozenset()


def catalog_admission(
    tool_names: list[str] | tuple[str, ...],
    *,
    subject: str,
    required_tool: str,
) -> dict[str, object]:
    """Classify whether one host catalog can measure subject-first routing."""
    operation = required_tool.removeprefix(subject + "_")
    required_visible = any(
        matches_subject_operation(
            name,
            subject=subject,
            operation=operation,
        )
        for name in tool_names
    )
    native_classes = sorted({
        tool_class
        for name in tool_names
        for tool_class in catalog_discovery_classes(
            name,
            subject=subject,
        )
    })
    reasons: list[str] = []
    if not required_visible:
        reasons.append("required-subject-tool-missing")
    if not native_classes:
        reasons.append("native-discovery-tools-missing")
    return {
        "schema": "agents-cookbook-tool-routing-catalog.v1",
        "status": "READY" if not reasons else "ENVIRONMENT_BLOCKED",
        "subject": subject,
        "required_tool": required_tool,
        "required_tool_visible": required_visible,
        "native_discovery_classes": native_classes,
        "reason_codes": reasons,
    }


def normalized_call(
    call: dict[str, object],
    *,
    subject: str,
) -> dict[str, object]:
    """Project one host call into canonical routing evidence."""
    tool = call.get("tool")
    inputs = call.get("inputs")
    if not isinstance(inputs, dict):
        inputs = call.get("input")
    if not isinstance(inputs, dict):
        inputs = {}

    captured_result = (
        call["output"]
        if "output" in call
        else call.get("result")
    )
    measured = result_bytes(captured_result)
    basis = "captured-output" if measured is not None else "unobserved"
    if measured is None:
        declared = call.get("result_bytes")
        if isinstance(declared, int) and declared >= 0:
            measured = declared
            basis = "declared-size"

    tool_class = classify_call(tool, inputs, subject=subject)
    observability = None
    if tool_class == TOOL_ROUTER:
        nested = call.get("nested_calls")
        observability = "expanded" if isinstance(nested, list) else "opaque"

    return {
        "tool": tool,
        "tool_class": tool_class,
        "status": call.get("status"),
        "input": inputs,
        "result_bytes": measured,
        "result_basis": basis,
        "routing_observability": observability,
    }


def normalize_calls(
    calls: list[dict[str, object]],
    *,
    subject: str,
) -> list[dict[str, object]]:
    """Flatten observable router children and assign one authoritative order."""
    normalized: list[dict[str, object]] = []

    def append_call(call: dict[str, object]) -> None:
        row = normalized_call(call, subject=subject)
        row["ordinal"] = len(normalized) + 1
        normalized.append(row)
        nested = call.get("nested_calls")
        if isinstance(nested, list):
            for child in nested:
                if not isinstance(child, dict):
                    raise ValueError("nested_calls entries must be objects")
                append_call(child)

    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("tool trace calls must be objects")
        append_call(call)
    return normalized


def required_call_result(
    calls: list[dict[str, object]] | None,
    *,
    subject: str,
    required_tool: str,
) -> tuple[bool | None, bool | None]:
    if calls is None:
        return None, None
    matching = [
        call
        for call in calls
        if matches_subject_operation(
            call.get("tool"),
            subject=subject,
            operation=required_tool.removeprefix(subject + "_"),
        )
    ]
    succeeded = any(
        call.get("status") == "completed"
        and isinstance(call.get("result_bytes"), int)
        and call["result_bytes"] > 0
        for call in matching
    )
    if succeeded:
        return True, True
    if matching and any(
        call.get("status") == "completed" and call.get("result_bytes") is None
        for call in matching
    ):
        return True, None
    return bool(matching), False


def required_success_index(
    calls: list[dict[str, object]],
    *,
    subject: str,
    required_tool: str,
) -> int | None:
    for index, call in enumerate(calls):
        if not matches_subject_operation(
            call.get("tool"),
            subject=subject,
            operation=required_tool.removeprefix(subject + "_"),
        ):
            continue
        if (
            call.get("status") == "completed"
            and isinstance(call.get("result_bytes"), int)
            and call["result_bytes"] > 0
        ):
            return index
    return None


def required_before_native_discovery(
    calls: list[dict[str, object]] | None,
    *,
    subject: str,
    required_tool: str,
) -> bool | None:
    if calls is None:
        return None
    projected = []
    for call in calls:
        row = dict(call)
        if not isinstance(row.get("tool_class"), str):
            row["tool_class"] = classify_call(
                row.get("tool"),
                row.get("inputs") or row.get("input"),
                subject=subject,
            )
        if (
            row.get("tool_class") == TOOL_ROUTER
            and row.get("routing_observability") not in {"expanded", "opaque"}
        ):
            row["routing_observability"] = (
                "expanded" if isinstance(row.get("nested_calls"), list) else "opaque"
            )
        projected.append(row)

    discovery_index = first_discovery_index(projected)
    required_index = required_success_index(
        projected,
        subject=subject,
        required_tool=required_tool,
    )
    opaque_router_indexes = [
        index
        for index, call in enumerate(projected)
        if call.get("tool_class") == TOOL_ROUTER
        and call.get("routing_observability") != "expanded"
    ]

    prior = projected[:discovery_index]
    _, succeeded = required_call_result(
        prior,
        subject=subject,
        required_tool=required_tool,
    )
    if succeeded is True and required_index is not None:
        if any(index < required_index for index in opaque_router_indexes):
            return None
        return True
    if any(index < discovery_index for index in opaque_router_indexes):
        return None
    return succeeded


def first_native_discovery(
    calls: list[dict[str, object]] | None,
) -> tuple[str | None, object | None]:
    if calls is None:
        return None, None
    index = first_discovery_index(calls)
    if index == len(calls):
        return None, None
    call = calls[index]
    tool_class = call.get("tool_class")
    return (
        tool_class if isinstance(tool_class, str) else None,
        call.get("tool"),
    )


def evaluate_routing_calls(
    calls: list[dict[str, object]],
    *,
    subject: str,
    required_tool: str,
) -> dict[str, object]:
    """Evaluate one already-normalized ordered call stream."""
    attempted, succeeded = required_call_result(
        calls,
        subject=subject,
        required_tool=required_tool,
    )
    before = required_before_native_discovery(
        calls,
        subject=subject,
        required_tool=required_tool,
    )
    first_class, first_tool = first_native_discovery(calls)
    if before is True:
        outcome = "PASS"
    elif before is False:
        outcome = "FAIL"
    else:
        outcome = "UNKNOWN"
    return {
        "outcome": outcome,
        "required_call_attempted": attempted,
        "required_call_succeeded": succeeded,
        "required_before_native_discovery": before,
        "first_native_discovery_class": first_class,
        "first_native_discovery_tool": first_tool,
    }
