"""Standalone host-neutral tool-routing trace qualification."""

from __future__ import annotations

from typing import Any

from benchmarks.tool_routing import (
    catalog_admission,
    catalog_tool_names,
    evaluate_routing_calls,
    normalize_calls,
    routing_artifact_sha256,
)

CATALOG_CAPTURE_SCHEMA = "agents-cookbook-tool-routing-catalog-capture.v1"
TRACE_SCHEMA = "agents-cookbook-tool-routing-trace.v1"
SCORE_SCHEMA = "agents-cookbook-tool-routing-trace-score.v1"

_DERIVED_CALL_FIELDS = frozenset({
    "ordinal",
    "tool_class",
    "result_basis",
    "routing_observability",
})
_MAX_CATALOG_TOOLS = 4096
_MAX_TRACE_CALLS = 4096
_MAX_TRACE_NESTING = 16


def build_catalog_capture(
    payload: object,
    *,
    host: str,
    capture_id: str,
) -> dict[str, object]:
    if not isinstance(host, str) or not host.strip():
        raise ValueError("tool-routing catalog capture host must be a nonempty string")
    if not isinstance(capture_id, str) or not capture_id.strip():
        raise ValueError(
            "tool-routing catalog capture capture_id must be a nonempty string"
        )
    catalog_tool_names(payload)
    raw_tools = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(raw_tools, list):
        raise ValueError("tool catalog must contain a tools list")
    capture = {
        "schema": CATALOG_CAPTURE_SCHEMA,
        "host": host.strip(),
        "capture_id": capture_id.strip(),
        "tools": list(raw_tools),
    }
    return validate_catalog_capture(capture)


def validate_catalog_capture(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("tool-routing catalog capture must be a JSON object")
    if payload.get("schema") != CATALOG_CAPTURE_SCHEMA:
        raise ValueError(
            f"tool-routing catalog capture schema must be {CATALOG_CAPTURE_SCHEMA}"
        )
    host = payload.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("tool-routing catalog capture host must be a nonempty string")
    capture_id = payload.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id.strip():
        raise ValueError(
            "tool-routing catalog capture capture_id must be a nonempty string"
        )
    names = catalog_tool_names(payload)
    if len(names) > _MAX_CATALOG_TOOLS:
        raise ValueError(
            f"tool-routing catalog capture exceeds {_MAX_CATALOG_TOOLS} tools"
        )
    return payload


def _validate_call(
    call: object,
    *,
    path: str,
    depth: int,
) -> int:
    if depth > _MAX_TRACE_NESTING:
        raise ValueError(
            f"{path} exceeds maximum nested tool depth {_MAX_TRACE_NESTING}"
        )
    if not isinstance(call, dict):
        raise ValueError(f"{path} must be an object")
    tool = call.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError(f"{path}.tool must be a nonempty string")
    forbidden = sorted(_DERIVED_CALL_FIELDS.intersection(call))
    if forbidden:
        raise ValueError(
            f"{path} contains derived routing fields owned by the scorer: {forbidden}"
        )
    status = call.get("status")
    if status is not None and (
        not isinstance(status, str) or not status.strip()
    ):
        raise ValueError(f"{path}.status must be a nonempty string when present")
    if "input" in call and "inputs" in call:
        raise ValueError(f"{path} must not contain both input and inputs")
    if "output" in call and "result" in call:
        raise ValueError(f"{path} must not contain both output and result")
    declared = call.get("result_bytes")
    if declared is not None and (
        not isinstance(declared, int) or isinstance(declared, bool) or declared < 0
    ):
        raise ValueError(f"{path}.result_bytes must be a nonnegative integer")
    nested = call.get("nested_calls")
    count = 1
    if nested is not None:
        if not isinstance(nested, list):
            raise ValueError(f"{path}.nested_calls must be a list")
        for index, child in enumerate(nested):
            count += _validate_call(
                child,
                path=f"{path}.nested_calls[{index}]",
                depth=depth + 1,
            )
            if count > _MAX_TRACE_CALLS:
                raise ValueError(
                    f"tool-routing trace exceeds {_MAX_TRACE_CALLS} calls"
                )
    return count


def validate_trace(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("tool-routing trace must be a JSON object")
    if payload.get("schema") != TRACE_SCHEMA:
        raise ValueError(f"tool-routing trace schema must be {TRACE_SCHEMA}")
    host = payload.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValueError("tool-routing trace host must be a nonempty string")
    capture_id = payload.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id.strip():
        raise ValueError("tool-routing trace capture_id must be a nonempty string")
    catalog_sha256 = payload.get("catalog_sha256")
    if not isinstance(catalog_sha256, str) or not (
        catalog_sha256.startswith("sha256:")
        and len(catalog_sha256) == len("sha256:") + 64
    ):
        raise ValueError(
            "tool-routing trace catalog_sha256 must be sha256:<64 lowercase hex>"
        )
    digest = catalog_sha256.removeprefix("sha256:")
    if digest != digest.lower():
        raise ValueError(
            "tool-routing trace catalog_sha256 must use lowercase hexadecimal digits"
        )
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ValueError(
            "tool-routing trace catalog_sha256 must contain hexadecimal digits"
        ) from exc
    calls = payload.get("calls")
    if not isinstance(calls, list):
        raise ValueError("tool-routing trace calls must be a list")
    total = 0
    for index, call in enumerate(calls):
        total += _validate_call(
            call,
            path=f"calls[{index}]",
            depth=0,
        )
        if total > _MAX_TRACE_CALLS:
            raise ValueError(
                f"tool-routing trace exceeds {_MAX_TRACE_CALLS} calls"
            )
    return payload


def score_trace(
    *,
    catalog_payload: object,
    trace_payload: object,
    subject: str,
    required_tool: str,
) -> dict[str, Any]:
    if not isinstance(subject, str) or not subject:
        raise ValueError("tool-routing subject must be a nonempty string")
    if not isinstance(required_tool, str) or not required_tool:
        raise ValueError("tool-routing required tool must be a nonempty string")
    catalog = validate_catalog_capture(catalog_payload)
    trace = validate_trace(trace_payload)
    if trace["host"] != catalog["host"]:
        raise ValueError("tool-routing catalog and trace host differ")
    if trace["capture_id"] != catalog["capture_id"]:
        raise ValueError("tool-routing catalog and trace capture_id differ")
    catalog_sha256 = routing_artifact_sha256(catalog_payload)
    if trace["catalog_sha256"] != catalog_sha256:
        raise ValueError(
            "tool-routing trace catalog_sha256 does not match the supplied catalog"
        )
    catalog_names = catalog_tool_names(catalog_payload)
    admission = catalog_admission(
        catalog_names,
        subject=subject,
        required_tool=required_tool,
    )
    raw_calls = trace["calls"]
    assert isinstance(raw_calls, list)
    calls = normalize_calls(raw_calls, subject=subject)
    evaluation = evaluate_routing_calls(
        calls,
        subject=subject,
        required_tool=required_tool,
    )

    if admission["status"] == "READY":
        outcome = evaluation["outcome"]
    else:
        outcome = "ENVIRONMENT_BLOCKED"

    return {
        "schema": SCORE_SCHEMA,
        "authority": {
            "diagnostic_only": True,
            "heldout_comparable": False,
            "model_execution_owner": "external",
            "routing_classification_owner": "agents-cookbook",
        },
        "host": trace["host"],
        "capture_id": trace["capture_id"],
        "subject": subject,
        "required_tool": required_tool,
        "outcome": outcome,
        "catalog_admission": admission,
        "catalog_sha256": catalog_sha256,
        "trace_sha256": routing_artifact_sha256(trace_payload),
        "call_count": len(calls),
        "calls": calls,
        "routing_evaluation": evaluation,
    }


def exit_code(score: dict[str, Any]) -> int:
    outcome = score.get("outcome")
    if outcome == "PASS":
        return 0
    if outcome == "FAIL":
        return 1
    if outcome == "ENVIRONMENT_BLOCKED":
        return 2
    if outcome == "UNKNOWN":
        return 3
    raise ValueError(f"unsupported tool-routing outcome: {outcome!r}")
