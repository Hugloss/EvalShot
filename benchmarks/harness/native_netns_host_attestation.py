"""E260: host-key authentication for observed Linux netns pilot receipts.

This signature binds a local kernel peer observation to the exact host
execution JSON and independently signed provider packet attestation.
Only the privileged host writes it, and the verifier requires separately
custodied host-key bytes. A matching MAC is a host assertion, not
independent OS attestation or proof of provider attention.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import canonical
from .native_netns_probe import kernel_probe_receipt_valid
from .native_mount_probe import inspect_mount_probe
from .native_chat_pair_admission import verify_native_chat_pair_claim

SCHEMA = "agentscookbook.native-netns-host-isolation-attestation.v4"
DOMAIN = b"agentscookbook.netns-host-execution-hmac.v1\0"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _auth(key: bytes, body: Mapping[str, Any]) -> str:
    if not isinstance(key, bytes) or len(key) < 16:
        raise ValueError("invalid-privileged-netns-host-key")
    return hmac.new(key, DOMAIN + canonical(body), hashlib.sha256).hexdigest()


def sign_isolation_report(
    report: Mapping[str, Any], *, execution_bytes: bytes,
    provider_attestation_bytes: bytes, host_identity: str, host_key: bytes,
) -> dict[str, Any]:
    if (not isinstance(report, Mapping)
            or report.get("native_trial_evidence_qualified") is not True
            or report.get("host_receipt_finalized") is not True
            or report.get("kernel_distinct_netns_relay_observed") is not True
            or report.get("linux_network_namespace_requested") is not True
            or not isinstance(report.get("gateway"), Mapping)
            or report["gateway"].get("native_chat_tool_wire_contract_enforced") is not True
            or report["gateway"].get("provider_issued_tool_observed") is not True
            or report["gateway"].get("provider_issued_tool_continuation_verified") is not True
            or not isinstance(report["gateway"].get("provider_issued_response_sha256"), str)
            or len(report["gateway"]["provider_issued_response_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in
                   report["gateway"]["provider_issued_response_sha256"])
            or not verify_native_chat_pair_claim(
                report.get("native_chat_pair_preflight"),
                trial_id=report.get("trial_id"),
                manifest_sha256=report.get("manifest_sha256"),
            )
            or not kernel_probe_receipt_valid(
                report.get("native_kernel_preflight"),
                expected_bwrap_sha256=report.get("native_sandbox_binary_sha256"),
            )
            or not inspect_mount_probe(
                report.get("native_mount_preflight"),
                expected_bwrap_sha256=report.get("native_sandbox_binary_sha256"),
                expected_config_sha256=report.get("config_sha256"),
            )
            or not isinstance(report.get("gateway"), Mapping)
            or report["gateway"].get("kernel_netns_peer_check_required") is not True
            or type(report["gateway"].get("distinct_netns_peer_connections")) is not int
            or report["gateway"]["distinct_netns_peer_connections"] < 1
            or not isinstance(host_identity, str)
            or not 0 < len(host_identity) <= 128):
        raise ValueError("unqualified-netns-host-observation-cannot-be-signed")
    if (not isinstance(execution_bytes, bytes)
            or not isinstance(provider_attestation_bytes, bytes)
            or not 0 < len(execution_bytes) <= 65_536
            or not 0 < len(provider_attestation_bytes) <= 1_048_576
            or json.loads(execution_bytes) != dict(report)):
        raise ValueError("host-isolation-source-bytes-do-not-match-report")
    body = {
        "schema": SCHEMA,
        "trial_id": report["trial_id"],
        "manifest_sha256": report["manifest_sha256"],
        "host_identity": host_identity,
        "execution_sha256": sha(execution_bytes),
        "provider_attestation_sha256": sha(provider_attestation_bytes),
        "bwrap_sha256": report["native_sandbox_binary_sha256"],
        "hashmarks_sha256": report["frozen_hashmarks_executable_sha256"],
        "kernel_distinct_netns_relay_observed": True,
        "distinct_netns_peer_connections": report["gateway"]["distinct_netns_peer_connections"],
    }
    return {**body, "mac_sha256": _auth(host_key, body)}


def write_isolation_attestation(path: Path, attestation: Mapping[str, Any]) -> None:
    if path.is_symlink() or path.exists():
        raise ValueError("duplicate-or-symlinked-host-isolation-attestation")
    data = canonical(attestation) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def verify_isolation_attestation(
    path: Path, *, report: Mapping[str, Any],
    execution_bytes: bytes, provider_attestation_bytes: bytes,
    host_identity: str, host_key: bytes,
) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        return False
    try:
        saved = json.loads(path.read_bytes())
        if not isinstance(saved, dict):
            return False
        expected = sign_isolation_report(
            report, execution_bytes=execution_bytes,
            provider_attestation_bytes=provider_attestation_bytes,
            host_identity=host_identity, host_key=host_key,
        )
        return (
            set(saved) == set(expected)
            and isinstance(saved.get("mac_sha256"), str)
            and hmac.compare_digest(saved["mac_sha256"], expected["mac_sha256"])
            and {k: v for k, v in saved.items() if k != "mac_sha256"}
            == {k: v for k, v in expected.items() if k != "mac_sha256"}
        )
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return False
