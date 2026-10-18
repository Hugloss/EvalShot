"""E273-E276: bind a native tool result to the *actual provider response*.

The prior gateway validated only the assistant/tool messages authored in a
subsequent request. A client could therefore invent its own assistant tool
call. This module extracts exactly one complete Hashmarks call from the
bounded provider response returned by the trusted upstream transport and
compares the later request's call ID, function name and argument bytes.

This remains host-observed transport evidence, not independent provider
cognition, MCP execution or causal effect.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .native_chat_request_integrity import (
    SUBJECT_NATIVE_FUNCTIONS, require_assistant_tool_ancestry,
)
from .trusted_http_provider import validate_sse_response

SCHEMA = "agentscookbook.native-provider-issued-call.v1"
MAX_ARGUMENTS = 262_144
MAX_EVENTS = 2048


def _bounded_call(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("type") != "function":
        raise ValueError("provider-issued-tool-call-type-unsupported")
    tool_id = value.get("id")
    function = value.get("function")
    if not isinstance(function, dict):
        raise ValueError("provider-issued-tool-call-function-missing")
    name, arguments = function.get("name"), function.get("arguments")
    if (type(tool_id) is not str or not 0 < len(tool_id) <= 512
            or type(name) is not str or name not in SUBJECT_NATIVE_FUNCTIONS
            or type(arguments) is not str
            or len(arguments.encode("utf-8")) > MAX_ARGUMENTS):
        raise ValueError("provider-issued-hashmarks-tool-call-not-qualified")
    try:
        parsed = json.loads(arguments)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("provider-issued-tool-arguments-invalid-json") from None
    if not isinstance(parsed, dict):
        raise ValueError("provider-issued-tool-arguments-not-object")
    from .host_input_attestation import canonical
    return {
        "call_id": tool_id, "function_name": name,
        "arguments_sha256": hashlib.sha256(arguments.encode("utf-8")).hexdigest(),
        # Canonical semantic digest permits SDKs to store the same decoded
        # arguments as a dict without requiring identical JSON whitespace.
        # Raw arguments digest remains authoritative for provider continuation.
        "arguments_semantic_sha256": hashlib.sha256(canonical(parsed)).hexdigest(),
    }


def _from_json(body: bytes) -> dict[str, str] | None:
    try:
        response = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("provider-pre-evidence-response-invalid-json") from None
    if not isinstance(response, dict) or not isinstance(response.get("choices"), list) or len(response["choices"]) != 1:
        raise ValueError("provider-pre-evidence-choice-ambiguous")
    choice = response["choices"][0]
    if not isinstance(choice, dict):
        raise ValueError("provider-pre-evidence-choice-not-object")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError("provider-pre-evidence-assistant-message-missing")
    calls = message.get("tool_calls")
    if not calls:
        # A syntactically complete JSON body is not necessarily a
        # completed provider turn; unknown/absent finish reasons must
        # not authorize another pre-evidence request.
        if choice.get("finish_reason") != "stop":
            raise ValueError("provider-pre-evidence-finish-not-qualified")
        return None
    if choice.get("finish_reason") != "tool_calls" or not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("provider-issued-tools-incomplete-or-parallel")
    return _bounded_call(calls[0])


def _from_sse(body: bytes) -> dict[str, str] | None:
    if not validate_sse_response(body):
        raise ValueError("provider-pre-evidence-sse-not-complete")
    rows = []
    for event in body.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
        if not event.strip():
            continue
        values = [line[5:].lstrip(" ") for line in event.split("\n")
                  if line.startswith("data:")]
        if len(values) != 1:
            raise ValueError("provider-sse-event-ambiguous")
        rows.append(values[0])
    if len(rows) > MAX_EVENTS + 1:
        raise ValueError("provider-pre-evidence-sse-too-many-events")
    tool = {"id": "", "type": None, "function": {"name": "", "arguments": ""}}
    saw_tool = False
    finish = None
    for line in rows[:-1]:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            raise ValueError("provider-sse-event-not-json") from None
        if not isinstance(event, dict) or not isinstance(event.get("choices"), list):
            raise ValueError("provider-sse-invalid-choice-container")
        if not event["choices"]:
            continue
        if len(event["choices"]) != 1:
            raise ValueError("provider-sse-multiple-choices")
        choice = event["choices"][0]
        if not isinstance(choice, dict) or choice.get("index", 0) != 0:
            raise ValueError("provider-sse-choice-index-unsupported")
        if choice.get("finish_reason") is not None:
            if finish is not None:
                raise ValueError("provider-sse-multiple-finish-events")
            finish = choice["finish_reason"]
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            raise ValueError("provider-sse-invalid-delta")
        calls = delta.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) > 1:
            raise ValueError("provider-sse-parallel-tool-deltas")
        for part in calls:
            if not isinstance(part, dict) or part.get("index") != 0:
                raise ValueError("provider-sse-tool-index-unsupported")
            saw_tool = True
            for key in ("id", "type"):
                value = part.get(key)
                if value is not None:
                    if type(value) is not str or (key == "type" and value != "function"):
                        raise ValueError("provider-sse-invalid-tool-field")
                    if key == "type":
                        if tool[key] is not None and tool[key] != value:
                            raise ValueError("provider-sse-conflicting-tool-type")
                        tool[key] = value
                    else:
                        if tool[key]:
                            raise ValueError("provider-sse-repeated-tool-id")
                        tool[key] = value
            fragment = part.get("function", {})
            if not isinstance(fragment, dict) or set(fragment) - {"name", "arguments"}:
                raise ValueError("provider-sse-unrecognized-function-field")
            for key in ("name", "arguments"):
                val = fragment.get(key)
                if val is not None:
                    if type(val) is not str:
                        raise ValueError("provider-sse-nontext-tool-fragment")
                    tool["function"][key] += val
                    if len(tool["function"][key].encode("utf-8")) > MAX_ARGUMENTS:
                        raise ValueError("provider-sse-oversized-tool-fragments")
    if not saw_tool:
        if finish != "stop":
            raise ValueError("provider-sse-no-tool-finish-not-qualified")
        return None
    if finish != "tool_calls":
        raise ValueError("provider-sse-tool-finish-incomplete")
    return _bounded_call(tool)


def extract_provider_issued_tool(body: bytes, *, streamed: bool) -> dict[str, str] | None:
    if type(body) is not bytes or not 0 < len(body) <= 2_097_152 or type(streamed) is not bool:
        raise ValueError("invalid-pre-evidence-provider-response")
    observed = _from_sse(body) if streamed else _from_json(body)
    if observed is not None:
        return {**observed, "schema": SCHEMA,
                "provider_response_sha256": hashlib.sha256(body).hexdigest()}
    return None


def require_issued_tool_continuation(
    request: Mapping[str, Any], *, call_id: str,
    provider_issued: Mapping[str, str] | None,
) -> None:
    name = require_assistant_tool_ancestry(request, call_id=call_id)
    if (provider_issued is None or provider_issued.get("schema") != SCHEMA
            or provider_issued.get("call_id") != call_id
            or provider_issued.get("function_name") != name):
        raise ValueError("native-tool-call-not-issued-by-observed-provider")
    call = request["messages"][-2]["tool_calls"][0]
    args = call["function"]["arguments"]
    if (type(args) is not str
            or hashlib.sha256(args.encode("utf-8")).hexdigest()
            != provider_issued["arguments_sha256"]):
        raise ValueError("native-tool-arguments-not-issued-by-provider")
