"""E274: transparent bounded stdio forwarding to pinned real Hashmarks MCP.

OpenCode talks to this local stdio wrapper; the wrapper launches the
actual SHA-pinned Hashmarks binary, forwards JSON-RPC control messages,
and intervenes on exactly one successfully matched tools/call response.

No separate network listener, invented MCP reply, or automatic fallback.
Stderr is not forwarded to model-visible stdout. Local bridge execution
does not independently attest native provider consumption.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

if __package__:
    from .native_mcp_bridge_contract import (
        BridgeState, MAX_FRAME, validate_bridge_contract,
    )
else:
    # The same executable is mounted with its contract module under /run.
    # This is an explicit invocation mode, never a transport fallback.
    from native_mcp_bridge_contract import (
        BridgeState, MAX_FRAME, validate_bridge_contract,
    )


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1_048_576), b""):
            digest.update(part)
    return digest.hexdigest()


def _read_frame(stream) -> bytes | None:
    frame = stream.readline(MAX_FRAME + 2)
    if not frame:
        return None
    if len(frame) > MAX_FRAME + 1 or not frame.endswith(b"\n"):
        raise ValueError("native-mcp-unbounded-or-unterminated-stdio-frame")
    return frame.rstrip(b"\r\n")


def run_stdio(*, hashmarks_executable: Path, hashmarks_sha256: str,
              workspace: Path, contract_path: Path,
              stdin=None, stdout=None) -> int:
    input_stream = stdin if stdin is not None else sys.stdin.buffer
    output_stream = stdout if stdout is not None else sys.stdout.buffer
    if (not hashmarks_executable.is_absolute()
            or hashmarks_executable.is_symlink()
            or not hashmarks_executable.is_file()
            or sha_file(hashmarks_executable) != hashmarks_sha256
            or not workspace.is_absolute() or workspace.is_symlink()
            or not workspace.is_dir()
            or contract_path.is_symlink()
            or not contract_path.is_file()
            or contract_path.stat().st_size > MAX_FRAME):
        raise ValueError("native-mcp-bridge-executable-or-source-not-pinned")
    contract = validate_bridge_contract(
        json.loads(contract_path.read_bytes()),
        hashmarks_sha256=hashmarks_sha256,
    )
    state = BridgeState(contract)
    failure: list[str] = []
    process = subprocess.Popen(
        [str(hashmarks_executable), "--workspace", str(workspace), "mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=workspace,
        # Runtime is already in the bwrap process/network namespace and
        # has only the closed XDG environment. Never inherit API secrets.
        env={
            key: os.environ[key]
            for key in (
                "HOME", "PATH", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                "XDG_DATA_HOME", "XDG_STATE_HOME",
            )
            if key in os.environ
        }, bufsize=0,
    )
    assert process.stdin is not None and process.stdout is not None
    guard = threading.Lock()

    def forward_parent_input() -> None:
        try:
            while True:
                frame = _read_frame(input_stream)
                if frame is None:
                    break
                with guard:
                    forwarded = state.receive_request(frame)
                process.stdin.write(forwarded + b"\n")
                process.stdin.flush()
        except (OSError, ValueError, TypeError):
            failure.append("unqualified-native-mcp-request")
            process.kill()
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    pump = threading.Thread(target=forward_parent_input, daemon=True)
    pump.start()
    try:
        while True:
            frame = _read_frame(process.stdout)
            if frame is None:
                break
            with guard:
                response = state.receive_response(frame)
            output_stream.write(response + b"\n")
            output_stream.flush()
        status = process.wait()
        # A valid MCP session should complete one actual Hashmarks response.
        # Some hosts close stdin after running the agent. A session with no
        # observed subject response is not evidence and is rejected.
        if failure or status != 0 or state.subject_responses != 1:
            return 2
        return 0
    except (OSError, ValueError, TypeError, BrokenPipeError):
        process.kill()
        process.wait()
        return 2
    finally:
        pump.join(timeout=1)
        if process.poll() is None:
            process.kill()
            process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hashmarks", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run_stdio(
            hashmarks_executable=args.hashmarks,
            hashmarks_sha256=args.sha256,
            workspace=args.workspace, contract_path=args.contract,
        )
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
