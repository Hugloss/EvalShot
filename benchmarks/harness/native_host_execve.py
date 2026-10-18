"""Host-owned process execve observation for optional native OpenCode trials.

Only a privileged host may launch the tracer. Parsed execve lines are a bounded
positive observation of processes, NOT proof that no other native searches
occurred or that an execve belongs to a particular agent tool-call ID. A real
nested-call-completeness receipt needs independently instrumented parent calls.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import canonical
from .opencode_native_gateway import sha256_file

SCHEMA = "agentscookbook.native-host-execve-observation.v1"
DOMAIN = b"agentscookbook:native-host-execve-observation:v1\x00"
MAX_TRACE_BYTES = 16_777_216
MAX_LINES = 40_000
MAX_COMMAND_ARGS = 128
SEARCH_EXECUTABLES = frozenset(("rg", "ag", "grep", "egrep", "fgrep"))
LINE = re.compile(
    r'^(?:(?:\[pid\s+(?P<pid>\d+)\]|(?P<pid2>\d+))\s+)?'
    r'execve\("(?P<executable>[^"\\\r\n]{1,4096})",.*\)\s*=\s*0\s*$'
)


def admit_execve_tracer(tracer: Path, *, sha256: str) -> dict[str, str]:
    """Pin a real host tracer without invoking model, shell, or Git."""
    if (
        sys.platform != "linux"
        or not tracer.is_absolute() or tracer.is_symlink()
        or not tracer.is_file()
        or not isinstance(sha256, str) or len(sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in sha256)
        or tracer.stat().st_mode & stat.S_IWOTH
    ):
        raise ValueError("native-execve-tracer-not-admitted")
    observed = sha256_file(tracer)
    if observed != sha256:
        raise ValueError("native-execve-tracer-binary-drift")
    return {"tracer_sha256": observed, "tracer_path": str(tracer)}


def _trace_file(path: Path, *, run_root: Path) -> None:
    if (
        path != run_root / "host-execve.trace"
        or run_root.is_symlink() or not run_root.is_dir()
        or path.exists() or path.is_symlink()
        or run_root.stat().st_mode & 0o077
    ):
        raise ValueError("native-execve-trace-path-not-private")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)


def summarize_execve_trace(raw: bytes) -> dict[str, Any]:
    """Parse only successful execve observations, never argv or shell text.

    Unknown kernel/strace event forms fail the *measurement*, not the native
    trial, so partial logs never upgrade to complete process observation.
    """
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_TRACE_BYTES:
        raise ValueError("native-execve-trace-unavailable-or-oversized")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ValueError("native-execve-trace-not-utf8") from exc
    if not lines or len(lines) > MAX_LINES:
        raise ValueError("native-execve-trace-line-budget")
    successes = 0
    search_count = 0
    for line in lines:
        matched = LINE.fullmatch(line)
        if matched:
            executable = matched.group("executable")
            # Require the exact executable pathname as observed by the host;
            # only positive native search process launches are counted.
            if not executable.startswith("/"):
                raise ValueError("native-execve-executable-not-absolute")
            successes += 1
            if executable.rsplit("/", 1)[-1] in SEARCH_EXECUTABLES:
                search_count += 1
        elif (
            "execveat(" in line or "<unfinished ...>" in line
            or "<... execve resumed>" in line
        ):
            raise ValueError("native-execve-event-not-reconstructable")
        elif "execve(" in line:
            # Failed syscall lines may appear (e.g. PATH resolution). Ignore
            # only a clearly failed completed syscall, never an ambiguous one.
            if not re.search(r"\)\s*=\s*-1\s+[A-Z0-9_]+\s+", line):
                raise ValueError("native-execve-event-not-reconstructable")
        elif line.strip():
            raise ValueError("native-execve-unrecognized-event")
    if successes == 0:
        raise ValueError("native-execve-no-successful-launch")
    return {
        "successful_execve_observations": successes,
        "recognized_search_process_launches": search_count,
        "recognized_search_executables": sorted(SEARCH_EXECUTABLES),
        "observation_scope": "strace-observed-successful-execve-executable-path",
        "complete_nested_tool_call_coverage_proven": False,
        "no_hidden_searches_proven": False,
        "tool_call_ancestry_proven": False,
        "model_attention_proven": False,
        "causal_search_savings_proven": False,
    }


def _save_create_only(path: Path, value: object) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("native-execve-receipt-already-exists")
    payload = canonical(value) + b"\n"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def run_host_execve_observed(
    command: list[str], *, tracer: Path, tracer_sha256: str,
    workspace: Path, run_root: Path, environment: Mapping[str, str],
    timeout_seconds: int, campaign_id: str, trial_id: str,
    host_identity: str, host_build_sha256: str,
    key: bytes,
) -> tuple[int | None, dict[str, Any]]:
    """Launch actual native argv under host-owned fork-following strace.

    Raw argv/syscall bytes are private to the host and deleted immediately
    after measurement. The signed report contains counts/digests only.
    """
    admission = admit_execve_tracer(tracer, sha256=tracer_sha256)
    if (
        not isinstance(command, list) or not 0 < len(command) <= MAX_COMMAND_ARGS
        or not all(isinstance(x, str) and x for x in command)
        or not Path(command[0]).is_absolute()
        or not workspace.is_absolute() or not workspace.is_dir()
        or not run_root.is_absolute() or not run_root.is_dir()
        or run_root.is_relative_to(workspace)
        or workspace.is_relative_to(run_root)
        or not isinstance(environment, Mapping)
        or not all(isinstance(k, str) and isinstance(v, str)
                   for k, v in environment.items())
        or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600
        or not isinstance(key, bytes) or not 32 <= len(key) <= 4096
        or not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (
            campaign_id, trial_id, host_identity,
        ))
        or not isinstance(host_build_sha256, str)
        or len(host_build_sha256) != 64
        or any(c not in "0123456789abcdef" for c in host_build_sha256)
    ):
        raise ValueError("invalid-host-execve-capture-authority")
    raw_path = run_root / "host-execve.trace"
    receipt_path = run_root / "host-execve.attestation.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ValueError("native-execve-receipt-already-exists")
    _trace_file(raw_path, run_root=run_root)
    exit_code: int | None = None
    summary: dict[str, Any]
    raw_hash: str | None = None
    try:
        args = [
            admission["tracer_path"], "-f", "-qq", "-s", "256",
            "-e", "trace=execve", "-o", str(raw_path),
            "--", *command,
        ]
        try:
            result = subprocess.run(
                args, cwd=workspace, env=dict(environment),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout_seconds, check=False,
            )
            exit_code = result.returncode
        except (OSError, subprocess.SubprocessError):
            exit_code = None
        if sha256_file(tracer) != tracer_sha256:
            raise ValueError("native-execve-tracer-changed-during-execution")
        if raw_path.is_symlink() or not raw_path.is_file():
            raise ValueError("native-execve-trace-path-replaced")
        raw = raw_path.read_bytes()
        raw_hash = hashlib.sha256(raw).hexdigest()
        try:
            summary = summarize_execve_trace(raw)
        except (ValueError, UnicodeError):
            summary = {
                "measurement_qualified": False,
                "reason": "host-execve-trace-not-fully-decodable",
                "complete_nested_tool_call_coverage_proven": False,
                "no_hidden_searches_proven": False,
                "tool_call_ancestry_proven": False,
                "causal_search_savings_proven": False,
            }
        else:
            summary["measurement_qualified"] = exit_code is not None
            if exit_code is None:
                summary["reason"] = "native-launch-unknown"
    finally:
        raw_path.unlink(missing_ok=True)
    report = {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "trial_id": trial_id,
        "host_identity": host_identity,
        "host_build_sha256": host_build_sha256,
        "tracer_sha256": tracer_sha256,
        "trace_sha256": raw_hash,
        "exit_code": exit_code,
        "observation": summary,
    }
    signed = {
        **report,
        "mac_sha256": hmac.new(
            key, DOMAIN + canonical(report), hashlib.sha256
        ).hexdigest(),
    }
    _save_create_only(receipt_path, signed)
    return exit_code, {
        "state": (
            "HOST_EXECVE_OBSERVED"
            if summary.get("measurement_qualified") is True
            else "UNQUALIFIED"
        ),
        "receipt_sha256": sha256_file(receipt_path),
        "observed_search_launches": (
            summary.get("recognized_search_process_launches")
            if summary.get("measurement_qualified") is True
            else None
        ),
        "host_execve_key_holder_authenticated": True,
        "nested_call_completeness_proven": False,
        "causal_search_savings_proven": False,
    }


def verify_host_execve_receipt(
    path: Path, *, key: bytes, campaign_id: str, trial_id: str,
    host_identity: str, host_build_sha256: str,
) -> dict[str, Any]:
    if (
        path.is_symlink() or not path.is_file()
        or path.stat().st_size > 32_768
        or not isinstance(key, bytes) or len(key) < 32
    ):
        raise ValueError("unsafe-native-execve-receipt")
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or set(data) != {
        "schema", "campaign_id", "trial_id", "host_identity",
        "host_build_sha256", "tracer_sha256", "trace_sha256",
        "exit_code", "observation", "mac_sha256",
    }:
        raise ValueError("native-execve-receipt-shape")
    body = {k: v for k, v in data.items() if k != "mac_sha256"}
    if (
        body["schema"] != SCHEMA
        or body["campaign_id"] != campaign_id
        or body["trial_id"] != trial_id
        or body["host_identity"] != host_identity
        or body["host_build_sha256"] != host_build_sha256
        or not isinstance(data["mac_sha256"], str)
    ):
        raise ValueError("native-execve-receipt-identity-drift")
    mac = hmac.new(key, DOMAIN + canonical(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(data["mac_sha256"], mac):
        raise ValueError("native-execve-host-authentication-failed")
    observation = body["observation"]
    if (
        not isinstance(observation, dict)
        or observation.get("no_hidden_searches_proven") is not False
        or observation.get("complete_nested_tool_call_coverage_proven") is not False
        or observation.get("causal_search_savings_proven") is not False
    ):
        raise ValueError("native-execve-evidence-authority-upgrade")
    return data
