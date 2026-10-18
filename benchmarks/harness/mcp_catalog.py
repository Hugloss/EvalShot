"""Model-free MCP stdio tool-catalog admission proof."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.adapters.registry import build_subject
from benchmarks.harness.identity import canonical_json
from benchmarks.harness.model import McpExposure, TrialContext
from benchmarks.harness.runtime_authority import transport_runtime_authority
from benchmarks.harness.suite import SuiteDefinition
from benchmarks.harness.workspace import isolated_environment
from scripts.agent_economics.bounded_process import (
    ProcessLimits,
    StopStdinExchange,
    run_bounded,
)


MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CATALOG_TIMEOUT_SECONDS = 10
MCP_CATALOG_MAX_OUTPUT_BYTES = 1_000_000


def _message_bytes(message: dict[str, Any]) -> bytes:
    return (
        json.dumps(message, sort_keys=True, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


def _initialize_request_bytes() -> bytes:
    return _message_bytes(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "agents-cookbook-benchmark-admission",
                    "version": "1",
                },
            },
        }
    )


def _catalog_request_bytes() -> bytes:
    return b"".join(
        _message_bytes(message)
        for message in (
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    )


def _response_by_id(raw: bytes) -> dict[int, dict[str, Any]]:
    responses: dict[int, dict[str, Any]] = {}
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("MCP catalog stdout is not valid UTF-8") from exc
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"MCP catalog stdout line {number} is not JSON-RPC"
            ) from exc
        if not isinstance(value, dict):
            raise ValueError(
                f"MCP catalog stdout line {number} is not a JSON object"
            )
        response_id = value.get("id")
        if type(response_id) is not int or response_id not in (1, 2):
            continue
        if response_id in responses:
            raise ValueError(f"MCP catalog emitted duplicate response id {response_id}")
        responses[int(response_id)] = value
    return responses


def _complete_responses(raw: bytes) -> dict[int, dict[str, Any]] | None:
    last_newline = raw.rfind(b"\n")
    if last_newline < 0:
        return None
    return _response_by_id(raw[: last_newline + 1])


def _initialize_response_ready(raw: bytes) -> bool:
    try:
        responses = _complete_responses(raw)
    except ValueError as exc:
        raise StopStdinExchange() from exc
    if responses is None or 1 not in responses:
        return False
    try:
        initialize = _result(responses[1], label="initialize")
        negotiated = initialize.get("protocolVersion")
        if not isinstance(negotiated, str) or not negotiated:
            raise ValueError("MCP initialize response has no protocolVersion")
    except ValueError as exc:
        raise StopStdinExchange() from exc
    return True


def _catalog_responses_ready(raw: bytes) -> bool:
    """Close stdin after a complete catalog response, including malformed output."""
    try:
        responses = _complete_responses(raw)
    except ValueError:
        return True
    return responses is not None and 2 in responses


def _timeout_stage(raw: bytes) -> str:
    try:
        responses = _complete_responses(raw)
    except ValueError:
        return "valid MCP output"
    return "tools/list" if responses is not None and 1 in responses else "initialize"


def _result(response: dict[str, Any], *, label: str) -> dict[str, Any]:
    error = response.get("error")
    if error is not None:
        raise ValueError(f"MCP {label} failed: {error!r}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"MCP {label} response has no object result")
    return result


def probe_mcp_tool_catalog(
    *,
    context: TrialContext,
    exposure: McpExposure,
    subject_id: str,
    required_tool: str,
) -> dict[str, Any]:
    """Prove the admitted stdio MCP server exposes the frozen required operation."""
    if exposure.name != subject_id:
        raise ValueError(
            f"MCP exposure subject differs from contract: {exposure.name} != {subject_id}"
        )
    transport = exposure.semantic_identity.get("transport")
    if transport != "stdio":
        raise ValueError(
            f"MCP catalog admission supports stdio only, got {transport!r}"
        )
    prefix = subject_id + "_"
    if not required_tool.startswith(prefix):
        raise ValueError(f"required tool must use the {prefix} prefix")
    required_operation = required_tool[len(prefix):]
    if not required_operation:
        raise ValueError("required MCP operation is empty")

    result = run_bounded(
        repository_root=context.workspace,
        argv=(exposure.command, *exposure.args),
        cwd=exposure.cwd,
        environment={**context.environment, **exposure.environment},
        limits=ProcessLimits(
            timeout_seconds=MCP_CATALOG_TIMEOUT_SECONDS,
            max_stdout_bytes=MCP_CATALOG_MAX_OUTPUT_BYTES,
            max_stderr_bytes=MCP_CATALOG_MAX_OUTPUT_BYTES,
        ),
        inherit_environment=False,
        stdin_stages=(
            (_initialize_request_bytes(), _initialize_response_ready),
            (_catalog_request_bytes(), _catalog_responses_ready),
        ),
    )
    if result.executable_missing:
        raise ValueError("MCP catalog executable is missing")
    if result.timed_out:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {stderr[-1000:]}" if stderr else ""
        stage = _timeout_stage(result.stdout)
        raise ValueError(
            f"MCP catalog probe timed out waiting for {stage} response{detail}"
        )
    if result.stdout_truncated or result.stderr_truncated:
        raise ValueError("MCP catalog probe output exceeded admission bounds")
    if result.return_code not in {0, None}:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {stderr[-1000:]}" if stderr else ""
        raise ValueError(
            f"MCP catalog process exited {result.return_code}{detail}"
        )

    responses = _response_by_id(result.stdout)
    if 1 not in responses:
        raise ValueError("MCP catalog emitted no initialize response")
    initialize = _result(responses[1], label="initialize")
    negotiated = initialize.get("protocolVersion")
    if not isinstance(negotiated, str) or not negotiated:
        raise ValueError("MCP initialize response has no protocolVersion")

    if 2 not in responses:
        raise ValueError("MCP catalog emitted no tools/list response")
    tools_result = _result(responses[2], label="tools/list")
    if tools_result.get("nextCursor") not in {None, ""}:
        raise ValueError(
            "MCP tools/list is paginated; complete catalog proof is unavailable"
        )
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        raise ValueError("MCP tools/list result has no tools list")

    names: list[str] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ValueError(f"MCP tool {index} is not an object")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"MCP tool {index} has no nonempty name")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("MCP tools/list contains duplicate tool names")
    ordered = sorted(names)
    visible = required_operation in set(ordered)
    if not visible:
        raise ValueError(
            f"MCP required operation is not exposed: {required_operation}"
        )

    catalog_identity = {
        "subject_id": subject_id,
        "required_tool": required_tool,
        "required_operation": required_operation,
        "protocol_version": negotiated,
        "tool_names": ordered,
    }
    return {
        **catalog_identity,
        "required_tool_visible": True,
        "tool_count": len(ordered),
        "catalog_sha256": hashlib.sha256(
            canonical_json(catalog_identity)
        ).hexdigest(),
    }


def probe_subject_catalog_contracts(
    *,
    suite: SuiteDefinition,
    contracts: list[dict[str, str]],
    source: Mapping[str, str],
    root: Path,
) -> list[dict[str, Any]]:
    """Probe each selected subject once in an isolated admission workspace."""
    proofs: list[dict[str, Any]] = []
    root.mkdir(parents=True, exist_ok=True)
    for contract in contracts:
        subject_id = contract["subject_id"]
        required_tool = contract["required_tool"]
        subject_root = root / subject_id
        workspace = subject_root / "workspace"
        control = subject_root / "control"
        workspace.mkdir(parents=True)
        control.mkdir(parents=True)
        environment = isolated_environment(control, source=source)
        transport_runtime_authority(source, environment)
        context = TrialContext(
            workspace=workspace,
            control_root=control,
            environment=environment,
        )
        subject = build_subject(suite.subjects[subject_id])
        exposure = subject.mcp_exposure(context)
        if exposure is None:
            raise ValueError(f"subject {subject_id} has no MCP exposure")
        try:
            proof = probe_mcp_tool_catalog(
                context=context,
                exposure=exposure,
                subject_id=subject_id,
                required_tool=required_tool,
            )
        except (OSError, ValueError) as exc:
            raise ValueError(f"{subject_id}: {exc}") from exc
        proofs.append(proof)
    return proofs
