"""E281–E284: independent read-back join for optional host execve receipts.

The host-authenticated syscall summary is a positive lower-bound observation,
NOT an authenticated per-tool-call ancestry graph, complete process inventory,
or proof of zero hidden searches. Do not sign a substitute receipt here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from .native_host_execve import (
    SCHEMA as EXECVE_SCHEMA, SEARCH_EXECUTABLES,
    verify_host_execve_receipt,
)

EVIDENCE_KEYS = frozenset((
    "state", "receipt_sha256", "observed_search_launches",
    "host_execve_key_holder_authenticated",
    "nested_call_completeness_proven", "causal_search_savings_proven",
))
HEX = frozenset("0123456789abcdef")


def _sha64(value: object) -> bool:
    return type(value) is str and len(value) == 64 and set(value) <= HEX


def verify_native_execve_join(
    report: Mapping[str, Any], *, trial_dir: Path, key: bytes,
    campaign_id: str, trial_id: str, host_identity: str,
    host_build_sha256: str,
) -> bool:
    """Check persisted report -> exact receipt bytes -> host MAC -> trial.

    False means an explicitly unobserved legacy/opt-out trial. True means
    exactly one host-key authenticated positive execve summary was checked.
    """
    if trial_dir.is_symlink() or not trial_dir.is_dir():
        raise ValueError("native-execve-trial-dir-unsafe")
    required = report.get("native_host_execve_observation_required", False)
    evidence = report.get("native_host_execve_evidence")
    receipt_path = trial_dir / "host-execve.attestation.json"
    if type(required) is not bool:
        raise ValueError("native-execve-requirement-not-boolean")
    if not required:
        if evidence is not None or receipt_path.exists() or receipt_path.is_symlink():
            raise ValueError("native-execve-unrequested-evidence-present")
        return False
    if (not isinstance(evidence, dict)
            or set(evidence) != EVIDENCE_KEYS
            or evidence.get("state") != "HOST_EXECVE_OBSERVED"
            or evidence.get("host_execve_key_holder_authenticated") is not True
            or evidence.get("nested_call_completeness_proven") is not False
            or evidence.get("causal_search_savings_proven") is not False
            or not _sha64(evidence.get("receipt_sha256"))
            or type(evidence.get("observed_search_launches")) is not int
            or evidence["observed_search_launches"] < 0):
        raise ValueError("native-execve-execution-report-not-qualified")
    if (receipt_path.is_symlink() or not receipt_path.is_file()
            or not 0 < receipt_path.stat().st_size <= 32768):
        raise ValueError("native-execve-receipt-missing-or-unsafe")

    # The host-key verifier owns its own read; subsequently compare exact
    # persisted bytes to the execution report's hash. Re-read to deny races.
    first = receipt_path.read_bytes()
    signed = verify_host_execve_receipt(
        receipt_path, key=key, campaign_id=campaign_id, trial_id=trial_id,
        host_identity=host_identity, host_build_sha256=host_build_sha256,
    )
    if (receipt_path.is_symlink()
            or receipt_path.read_bytes() != first
            or hashlib.sha256(first).hexdigest() != evidence["receipt_sha256"]
            or signed.get("schema") != EXECVE_SCHEMA
            or not _sha64(signed.get("tracer_sha256"))
            or not _sha64(signed.get("trace_sha256"))
            or type(signed.get("exit_code")) is not int
            or signed["exit_code"] != report.get("native_exit_code")):
        raise ValueError("native-execve-raw-identity-or-exit-drift")
    observation = signed["observation"]
    count = observation.get("recognized_search_process_launches")
    launches = observation.get("successful_execve_observations")
    if (observation.get("measurement_qualified") is not True
            or type(count) is not int or type(launches) is not int
            or not 0 <= count <= launches
            or launches == 0
            or evidence["observed_search_launches"] != count
            or observation.get("recognized_search_executables")
            != sorted(SEARCH_EXECUTABLES)
            or observation.get("observation_scope")
            != "strace-observed-successful-execve-executable-path"
            or observation.get("tool_call_ancestry_proven") is not False
            or observation.get("model_attention_proven") is not False
            or observation.get("no_hidden_searches_proven") is not False
            or observation.get("complete_nested_tool_call_coverage_proven") is not False
            or observation.get("causal_search_savings_proven") is not False):
        raise ValueError("native-execve-observation-misrepresented")
    return True
