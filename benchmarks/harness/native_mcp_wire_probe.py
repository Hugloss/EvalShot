"""E279/E280: direct, bounded native Hashmarks MCP wire qualification.

Runs a pinned Hashmarks executable directly (no presentation rewrite) and
observes initialize -> tools/list -> one tools/call. Report carries schema,
byte digests, tool and server identity, plus precise compatibility blockers,
but never original code or secrets. Does not claim independent provenance,
OpenCode tool visibility, a model call, or causal uplift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import canonical
from .native_mcp_bridge_contract import (
    MAX_FRAME, _valid_tool_schema, _validate_call_input,
)
from .native_mcp_stdio_bridge import sha_file

SCHEMA = "agentscookbook.native-mcp-direct-wire-probe.v1"


def _read_json(path: Path, max_bytes: int) -> object:
    if (path.is_symlink() or not path.is_file()
            or not 0 < path.stat().st_size <= max_bytes):
        raise ValueError("unsafe-native-mcp-probe-input-file")
    return json.loads(path.read_bytes())


def _one_response(process: subprocess.Popen, *, expected_id: str,
                  deadline: float, pending: bytearray) -> tuple[dict[str, Any], bytes]:
    assert process.stdout is not None
    fd = process.stdout.fileno()
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            newline = pending.find(b"\n")
            if newline >= 0:
                frame = bytes(pending[:newline]).rstrip(b"\r")
                del pending[:newline + 1]
                try:
                    result = json.loads(frame)
                except (ValueError, UnicodeError) as exc:
                    raise ValueError("native-mcp-direct-probe-invalid-json") from exc
                if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
                    raise ValueError("native-mcp-direct-probe-invalid-jsonrpc")
                if result.get("id") != expected_id:
                    raise ValueError("native-mcp-direct-probe-unexpected-response-id")
                return result, frame
            if len(pending) > MAX_FRAME:
                raise ValueError("native-mcp-direct-probe-unbounded-response")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError("native-mcp-direct-probe-timeout")
            data = os.read(fd, 65536)
            if not data:
                raise ValueError("native-mcp-direct-probe-incomplete-response")
            pending.extend(data)


def _send(process: subprocess.Popen, method: str, request_id: str,
          params: Mapping[str, Any]) -> None:
    assert process.stdin is not None
    req = {
        "jsonrpc": "2.0", "id": request_id,
        "method": method, "params": dict(params),
    }
    data = canonical(req)
    if len(data) > MAX_FRAME:
        raise ValueError("native-mcp-direct-probe-unbounded-request")
    process.stdin.write(data + b"\n")
    process.stdin.flush()


def probe_native_mcp(
    *, binary: Path, expected_sha256: str, workspace: Path,
    tool_name: str, tool_args: Mapping[str, Any],
    expected_original_packet: object | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    if (not binary.is_absolute() or binary.is_symlink()
            or not binary.is_file()
            or sha_file(binary) != expected_sha256
            or not workspace.is_absolute() or workspace.is_symlink()
            or not workspace.is_dir() or not (workspace / ".git").exists()
            or type(tool_name) is not str or not 0 < len(tool_name) <= 128
            or not isinstance(tool_args, Mapping)
            or len(canonical(tool_args)) > 16_384
            or type(timeout) is not int or not 1 <= timeout <= 120):
        raise ValueError("native-mcp-direct-probe-admission-failed")
    deadline = time.monotonic() + timeout
    env = {"HOME": "/nonexistent", "PATH": os.defpath}
    # Readonly per-child source effects are not certified by this process
    # boundary; network and host filesystem are NOT sandboxed here.
    process = subprocess.Popen(
        [str(binary), "--workspace", str(workspace), "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, cwd=workspace, env=env, bufsize=0,
    )
    pending = bytearray()
    frames: list[bytes] = []
    try:
        _send(process, "initialize", "init",
              {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "agentscookbook-native-probe",
                              "version": "1"}})
        init, raw = _one_response(
            process, expected_id="init", deadline=deadline, pending=pending,
        )
        frames.append(raw)
        info = init.get("result", {}).get("serverInfo") if isinstance(init.get("result"), dict) else None
        if (not isinstance(info, dict) or info.get("name") != "Hashmarks"
                or type(info.get("version")) is not str or not info["version"]):
            raise ValueError("native-mcp-direct-probe-unqualified-server")
        assert process.stdin is not None
        process.stdin.write(canonical({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        }) + b"\n")
        process.stdin.flush()
        _send(process, "tools/list", "catalog", {})
        catalog, raw = _one_response(
            process, expected_id="catalog", deadline=deadline, pending=pending,
        )
        frames.append(raw)
        tools = catalog.get("result", {}).get("tools") if isinstance(catalog.get("result"), dict) else None
        if (not isinstance(tools, list) or not tools or len(tools) > 128
                or any(not _valid_tool_schema(t) for t in tools)
                or len({t["name"] for t in tools}) != len(tools)):
            raise ValueError("native-mcp-direct-probe-invalid-catalog")
        selected = [t for t in tools if t["name"] == tool_name]
        if len(selected) != 1:
            raise ValueError("native-mcp-direct-probe-tool-not-listed")
        _validate_call_input(selected[0], tool_args)
        _send(process, "tools/call", "subject", {
            "name": tool_name, "arguments": dict(tool_args),
        })
        answer, raw = _one_response(
            process, expected_id="subject", deadline=deadline, pending=pending,
        )
        frames.append(raw)
        if "error" in answer:
            raise ValueError("native-mcp-direct-probe-tool-failed")
        result = answer.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        text_content = (
            content[0].get("text")
            if isinstance(content, list) and len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get("type") == "text"
            else None
        )
        try:
            parsed_text = json.loads(text_content) if isinstance(text_content, str) else None
        except (ValueError, UnicodeError):
            parsed_text = None
        # Pin source equality even when SDK presents the same original
        # structured object alongside a wrapper in its text content.
        source_equal_structured = (
            expected_original_packet is not None
            and structured is not None
            and canonical(structured) == canonical(expected_original_packet)
        )
        source_equal_text = (
            expected_original_packet is not None
            and parsed_text is not None
            and canonical(parsed_text) == canonical(expected_original_packet)
        )
        bridge_compatible = (
            isinstance(result, dict)
            and set(result) <= {"content", "structuredContent", "isError"}
            and result.get("isError") is not True
            and isinstance(text_content, str)
            and isinstance(parsed_text, (dict, list))
            and (structured is None or canonical(structured) == canonical(parsed_text))
            and (expected_original_packet is None or source_equal_text)
        )
        return {
            "schema": SCHEMA,
            "hashmarks_binary_sha256": expected_sha256,
            "server_name": "Hashmarks",
            "server_version": info["version"],
            "tool_name": tool_name,
            "catalog_sha256": hashlib.sha256(canonical(tools)).hexdigest(),
            "selected_tool_schema_sha256": hashlib.sha256(
                canonical(selected[0]["inputSchema"])
            ).hexdigest(),
            "original_response_sha256": hashlib.sha256(raw).hexdigest(),
            "observed_protocol_sha256": hashlib.sha256(b"\n".join(frames)).hexdigest(),
            "original_structured_content_observed": structured is not None,
            "original_text_json_object_or_array_observed": isinstance(parsed_text, (dict, list)),
            "original_text_matches_frozen_packet": source_equal_text,
            "original_structured_matches_frozen_packet": source_equal_structured,
            "bridge_response_shape_compatible": bridge_compatible,
            "real_installed_mcp_process_observed_locally": True,
            "model_calls_performed": 0,
            "native_opencode_sdk_proven": False,
            "original_response_host_attested": False,
            "provider_attention_proven": False,
            "causal_uplift_proven": False,
        }
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--arguments", type=Path, required=True)
    parser.add_argument("--original-source", type=Path)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--require-bridge-compatible", action="store_true")
    args = parser.parse_args()
    try:
        result = probe_native_mcp(
            binary=args.binary, expected_sha256=args.sha256,
            workspace=args.workspace, tool_name=args.tool,
            tool_args=_read_json(args.arguments, 16_384),
            expected_original_packet=(
                _read_json(args.original_source, 262_144)
                if args.original_source else None
            ), timeout=args.timeout,
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print(json.dumps({
            "schema": SCHEMA, "bridge_response_shape_compatible": False,
            "reason": str(exc), "causal_uplift_proven": False,
        }, sort_keys=True, indent=2))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 2 if (
        args.require_bridge_compatible
        and (
            not result["bridge_response_shape_compatible"]
            or args.original_source is None
            or not result["original_text_matches_frozen_packet"]
        )
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
