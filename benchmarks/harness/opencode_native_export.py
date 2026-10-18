"""E253: bounded native OpenCode session discovery and export.

The session is selected using a new exact title, directory and start epoch.
An arbitrary 'latest session' or an agent-authored log is never accepted.
The native command runs with the identical isolated environment after
the trial, before any evidence is promoted. The resulting export remains
host-local observational data, not independently authenticated agent bytes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Callable

from .opencode_native_session import MAX_EXPORT_BYTES

SCHEMA = "agentscookbook.native-session-export-acquisition.v1"
MAX_INDEX_BYTES = 262_144


def native_export_preflight() -> str:
    """Resolve the Linux file-size-limit owner before launching the agent.

    Never use subprocess preexec_fn: the gateway is threaded and forking a
    multi-threaded Python process with a pre-exec Python callback can deadlock.
    """
    binary = shutil.which("prlimit", path=os.defpath)
    if not binary or not Path(binary).is_file():
        raise ValueError("native-export-prlimit-unavailable")
    return str(Path(binary).resolve())


def _run_bounded_native(
    argv: list[str], *, workspace: Path, environment: Mapping[str, str],
    max_bytes: int, timeout: int = 20,
) -> bytes:
    """Native stdout is redirected to a bounded host-owned temporary file.

    Avoid unbounded PIPE capture and never propagate provider secrets or
    arbitrary child stdout/stderr in an exception.
    """
    if (max_bytes <= 0 or max_bytes > MAX_EXPORT_BYTES
            or timeout <= 0 or timeout > 60):
        raise ValueError("invalid-native-export-limits")
    limit_owner = native_export_preflight()
    try:
        with tempfile.TemporaryFile() as capture:
            run = subprocess.run(
                [limit_owner, "--fsize=" + str(max_bytes) + ":" + str(max_bytes),
                 "--", *argv],
                cwd=workspace, env=dict(environment),
                stdout=capture, stderr=subprocess.DEVNULL,
                timeout=timeout, check=False,
            )
            if run.returncode != 0:
                raise ValueError("native-session-command-failed")
            capture.seek(0, os.SEEK_END)
            count = capture.tell()
            if not 0 < count <= max_bytes:
                raise ValueError("native-session-export-empty-or-oversized")
            capture.seek(0)
            return capture.read(max_bytes + 1)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("native-session-command-unavailable-or-timed-out") from None


def select_native_session(index: bytes, *, exact_title: str,
                          workspace: Path, started_at_ms: int) -> str:
    if (not isinstance(index, bytes) or not 0 < len(index) <= MAX_INDEX_BYTES
            or not isinstance(exact_title, str) or not exact_title
            or type(started_at_ms) is not int or started_at_ms < 1):
        raise ValueError("invalid-native-session-selection-authority")
    try:
        listing = json.loads(index)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid-native-session-index") from exc
    if not isinstance(listing, list) or len(listing) > 20:
        raise ValueError("invalid-native-session-population")
    matches = [
        item for item in listing
        if isinstance(item, dict)
        and item.get("title") == exact_title
        and item.get("directory") == str(workspace.resolve())
        and type(item.get("updated")) is int
        and item["updated"] >= started_at_ms
        and isinstance(item.get("id"), str)
        and 0 < len(item["id"]) <= 256
    ]
    if len(matches) != 1:
        raise ValueError("native-session-exact-selection-ambiguous-or-missing")
    return matches[0]["id"]


def acquire_native_export(
    executable: Path, *, workspace: Path,
    environment: Mapping[str, str], exact_title: str, started_at_ms: int,
    isolate_command: Callable[[list[str]], list[str]] | None = None,
) -> tuple[str, bytes]:
    listing_command = [
        str(executable), "session", "list", "--format", "json",
        "--max-count", "20",
    ]
    if isolate_command is not None:
        listing_command = isolate_command(listing_command)
    source = _run_bounded_native(
        listing_command,
        workspace=workspace, environment=environment,
        max_bytes=MAX_INDEX_BYTES,
    )
    session_id = select_native_session(
        source, exact_title=exact_title, workspace=workspace,
        started_at_ms=started_at_ms,
    )
    export_command = [str(executable), "export", session_id]
    if isolate_command is not None:
        export_command = isolate_command(export_command)
    exported = _run_bounded_native(
        export_command,
        workspace=workspace, environment=environment,
        max_bytes=MAX_EXPORT_BYTES,
    )
    return session_id, exported
