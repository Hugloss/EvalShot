"""E276: real installed-Hashmarks MCP stdio contract smoke, with no model.

Runs the exact SHA-pinned external Hashmarks executable via the same
native MCP bridge used in the confined OpenCode launcher. Uses real
JSON-RPC initialize, list and tools/call exchanges (not a synthetic
successful subject packet). External frozen content and an explicit
tool-arguments file are required. The test is *not* OS confinement,
provider-request evidence, a real model call, independent attestation,
or a causal/graded benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import canonical
from .native_chat_transport_parity import require_chat_tool_content
from .native_mcp_bridge_contract import (
    SUPPORTED_TOOLS, freeze_bridge_contract, write_bridge_contract,
)
from .native_mcp_stdio_bridge import sha_file
from .native_mcp_wire_probe import _one_response, _send
from .trusted_treatments import load_manifest, select_treatment, verify_manifest

SCHEMA = "agentscookbook.native-hashmarks-stdio-live-smoke.v1"
MAX_RESPONSE = 1_048_576


def _json(path: Path, max_bytes: int) -> Any:
    if (path.is_symlink() or not path.is_file()
            or not 0 < path.stat().st_size <= max_bytes):
        raise ValueError("invalid-frozen-native-mcp-live-source")
    return json.loads(path.read_bytes())


def smoke_real_native_mcp(
    manifest: Mapping[str, Any], *, trial_id: str,
    workspace: Path, binary: Path, sha256: str,
    source: Mapping[str, Any], replaced: Mapping[str, Any] | None,
    tool_name: str, tool_args: Mapping[str, Any],
    timeout: int = 30,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    cells = [c for c in frozen["assignments"] if c["trial_id"] == trial_id]
    if (len(cells) != 1 or cells[0]["harness"] not in ("opencode", "opencode-native")
            or not workspace.is_absolute() or workspace.is_symlink()
            or not workspace.is_dir() or not (workspace / ".git").exists()
            or not binary.is_absolute() or binary.is_symlink()
            or not binary.is_file() or sha_file(binary) != sha256
            or tool_name not in SUPPORTED_TOOLS
            or not isinstance(tool_args, Mapping)
            or len(canonical(tool_args)) > 16_384
            or type(timeout) is not int or not 1 <= timeout <= 120):
        raise ValueError("unadmitted-native-mcp-live-smoke")
    cell = cells[0]
    selected = select_treatment(
        frozen, trial_id=trial_id, harness=cell["harness"],
        task=cell["task"], replicate=cell["replicate"],
        arm=cell["arm"], current=source, replaced=replaced,
    )
    require_chat_tool_content(
        selected, actual_tool_content=selected["selected_content"],
    )
    contract = freeze_bridge_contract(
        selected, original_packet=source["content"], hashmarks_sha256=sha256,
    )
    reqs = [
        {"jsonrpc": "2.0", "id": "native-init", "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "agentscookbook-native-probe",
                                   "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": "native-list", "method": "tools/list",
         "params": {}},
        {"jsonrpc": "2.0", "id": "native-subject", "method": "tools/call",
         "params": {"name": tool_name, "arguments": dict(tool_args)}},
    ]
    # E288: a catalog request is not catalog proof. Use the existing bounded
    # native MCP wire transport and wait for each real response before sending
    # the subject call to the canonical bridge. No second bridge is created.
    with tempfile.TemporaryDirectory(prefix="agentscookbook-native-mcp-") as private:
        directory = Path(private)
        contract_path = directory / "bridge.json"
        write_bridge_contract(contract_path, contract)
        home = directory / "home"
        home.mkdir(mode=0o700)
        for name in ("config", "cache", "data", "state"):
            (directory / name).mkdir(mode=0o700)
        env = {
            "PATH": os.defpath, "HOME": str(home),
            "XDG_CONFIG_HOME": str(directory / "config"),
            "XDG_CACHE_HOME": str(directory / "cache"),
            "XDG_DATA_HOME": str(directory / "data"),
            "XDG_STATE_HOME": str(directory / "state"),
        }
        executable = Path(__file__).with_name("native_mcp_stdio_bridge.py")
        deadline = time.monotonic() + timeout
        process = None
        try:
            process = subprocess.Popen(
                [sys.executable, str(executable),
                 "--hashmarks", str(binary), "--sha256", sha256,
                 "--workspace", str(workspace),
                 "--contract", str(contract_path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=env, cwd=workspace, bufsize=0,
            )
            pending = bytearray()
            frames = []
            _send(process, "initialize", "native-init", reqs[0]["params"])
            initialized, raw = _one_response(
                process, expected_id="native-init",
                deadline=deadline, pending=pending,
            )
            frames.append(raw)
            info = initialized.get("result", {}).get("serverInfo") if (
                isinstance(initialized.get("result"), dict)
            ) else None
            if (not isinstance(info, dict)
                    or info.get("name") != "Hashmarks"
                    or type(info.get("version")) is not str
                    or not info["version"]):
                raise ValueError("native-mcp-live-initialization-unverified")
            assert process.stdin is not None
            process.stdin.write(canonical(reqs[1]) + b"\n")
            process.stdin.flush()
            _send(process, "tools/list", "native-list", reqs[2]["params"])
            listed, raw = _one_response(
                process, expected_id="native-list",
                deadline=deadline, pending=pending,
            )
            frames.append(raw)
            catalog = listed.get("result", {}).get("tools") if (
                isinstance(listed.get("result"), dict)
            ) else None
            if (not isinstance(catalog, list)
                    or tool_name not in {
                        tool.get("name") for tool in catalog
                        if isinstance(tool, dict)
                    }):
                raise ValueError("native-mcp-live-subject-not-listed")
            _send(process, "tools/call", "native-subject", reqs[3]["params"])
            _, raw = _one_response(
                process, expected_id="native-subject",
                deadline=deadline, pending=pending,
            )
            frames.append(raw)
            process.stdin.close()
            process.stdin = None
            remaining = max(0.001, deadline - time.monotonic())
            out_tail, _ = process.communicate(timeout=remaining)
            if process.returncode != 0 or pending.strip() or out_tail.strip():
                raise ValueError("native-mcp-live-extra-or-unqualified-response")
            output = b"\n".join(frames) + b"\n"
            if not 0 < len(output) <= MAX_RESPONSE:
                raise ValueError("native-mcp-live-stdio-contract-unqualified")
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("native-mcp-live-executable-unavailable") from exc
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
    try:
        rows = [json.loads(line) for line in output.splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise ValueError("native-mcp-live-stdio-output-invalid") from exc
    # A dictionary comprehension would silently discard duplicate IDs,
    # unexpected successes and malformed rows, overstating local MCP proof.
    # Only three exact, owned replies may qualify this smoke observation.
    expected_ids = {"native-init", "native-list", "native-subject"}
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("jsonrpc") != "2.0":
            raise ValueError("native-mcp-live-malformed-response-row")
        if "id" not in row:
            method = row.get("method")
            if (type(method) is not str
                    or not method.startswith("notifications/")
                    or "result" in row or "error" in row):
                raise ValueError("native-mcp-live-unrequested-server-message")
            continue
        ident = row["id"]
        if (type(ident) is not str or ident not in expected_ids
                or ident in indexed
                or ("result" in row) == ("error" in row)
                or "method" in row):
            raise ValueError("native-mcp-live-duplicate-or-foreign-response")
        indexed[ident] = row
    if set(indexed) != expected_ids:
        raise ValueError("native-mcp-live-incomplete-correlation")
    if (not isinstance(indexed.get("native-init", {}).get("result"), dict)
            or not isinstance(indexed.get("native-list", {}).get("result"), dict)
            or not isinstance(indexed["native-list"]["result"].get("tools"), list)
            or tool_name not in {
                entry.get("name") for entry in indexed["native-list"]["result"]["tools"]
                if isinstance(entry, dict)
            }
            or not isinstance(indexed.get("native-subject", {}).get("result"), dict)
            or indexed["native-subject"]["result"].get("content") != [
                {"type": "text", "text": selected["selected_content"]}
            ]):
        raise ValueError("native-mcp-live-listed-tool-or-subject-packet-drift")
    return {
        "schema": SCHEMA, "trial_id": trial_id,
        "manifest_sha256": frozen["manifest_sha256"],
        "hashmarks_sha256": sha256,
        "mcp_tool": tool_name,
        "mcp_stdio_source_and_treatment_qualified": True,
        "actual_external_hashmarks_subprocess_called": True,
        "observed_stdio_sha256": hashlib.sha256(output).hexdigest(),
        "model_requests_performed": 0,
        "sandbox_isolation_proven": False,
        "provider_evidence_delivery_proven": False,
        "original_response_independently_attested": False,
        "causal_hashmarks_uplift_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--hashmarks-binary", type=Path, required=True)
    parser.add_argument("--hashmarks-sha256", required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--replaced", type=Path)
    parser.add_argument("--tool-name", required=True)
    parser.add_argument("--tool-args-file", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    try:
        result = smoke_real_native_mcp(
            load_manifest(args.manifest), trial_id=args.trial_id,
            workspace=args.workspace, binary=args.hashmarks_binary,
            sha256=args.hashmarks_sha256, source=_json(args.current, 262_144),
            replaced=_json(args.replaced, 262_144) if args.replaced else None,
            tool_name=args.tool_name,
            tool_args=_json(args.tool_args_file, 16_384),
            timeout=args.timeout,
        )
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
        print(json.dumps({
            "schema": SCHEMA,
            "mcp_stdio_source_and_treatment_qualified": False,
            "reason": str(exc), "causal_hashmarks_uplift_proven": False,
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
