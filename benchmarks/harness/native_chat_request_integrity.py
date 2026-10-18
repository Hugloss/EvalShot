"""E270: exact OpenAI Chat Completions assistant-call -> tool-result ancestry.

This verifies syntactic linkage in the host-observed provider request.
It cannot prove the agent executed an MCP function or that the provider
attended to the result; those remain separate native-session/transport
evidence questions. Legacy synthetic structured-gateway tests are not
rewritten into Chat Completions messages.
"""

from __future__ import annotations

from typing import Mapping

SUBJECT_NATIVE_FUNCTIONS = frozenset((
    "hashmarks_task_evidence", "hashmarks_find",
    "hashmarks.task_evidence", "hashmarks.find",
    "mcp__hashmarks__task_evidence", "mcp__hashmarks__find",
    "tools.hashmarks.task_evidence", "tools.hashmarks.find",
))


def require_assistant_tool_ancestry(request: Mapping[str, object], *,
                                    call_id: str) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list) or not 2 <= len(messages) <= 1024:
        raise ValueError("native-chat-assistant-tool-lineage-missing")
    if type(call_id) is not str or not 0 < len(call_id) <= 512:
        raise ValueError("native-chat-call-id-invalid")
    # OpenAI Chat Completions tool results must follow a model-authored
    # assistant message that declared the same function/call ID. A user
    # message or orphan role=tool cannot manufacture this link.
    if not isinstance(messages[-1], dict) or messages[-1].get("role") != "tool":
        raise ValueError("native-chat-final-tool-result-not-last")
    tools = [(i, m) for i, m in enumerate(messages)
             if isinstance(m, dict) and m.get("role") == "tool"]
    if len(tools) != 1 or tools[0][0] != len(messages) - 1:
        raise ValueError("native-chat-foreign-or-replayed-tool-result")
    observed = tools[0][1]
    if observed.get("tool_call_id") != call_id:
        raise ValueError("native-chat-tool-call-id-drift")
    previous = messages[-2]
    if not isinstance(previous, dict) or previous.get("role") != "assistant":
        raise ValueError("native-chat-assistant-tool-call-absent")
    calls = previous.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("native-chat-missing-or-parallel-assistant-call")
    native = calls[0]
    if not isinstance(native, dict) or set(native) != {"id", "type", "function"}:
        raise ValueError("native-chat-invalid-assistant-tool-call")
    function = native.get("function")
    if (native.get("type") != "function" or native.get("id") != call_id
            or not isinstance(function, dict)
            or type(function.get("name")) is not str
            or function["name"] not in SUBJECT_NATIVE_FUNCTIONS
            or type(function.get("arguments")) is not str):
        raise ValueError("native-chat-assistant-hashmarks-call-not-bound")
    # Previous assistant tool calls without recorded responses are not
    # secretly dropped: no parallel pending calls can be presumed completed.
    if any(isinstance(m, dict) and m.get("role") == "assistant"
           and m.get("tool_calls") for m in messages[:-2]):
        raise ValueError("native-chat-foreign-prior-assistant-tools")
    return function["name"]
