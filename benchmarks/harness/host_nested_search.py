"""Externally authenticated, bounded nested-call observations for Harbor pairs.

ATIF's tool order cannot prove what a generic router or shell executed.
Only a privileged host instrumenting that boundary may assert complete nested
calls, and its separately custodied key must be supplied at verification.
A host assertion is not independent proof of full execution or causal savings.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Mapping

from benchmarks.tool_routing import (
    NATIVE_SEARCH,
    SHELL,
    TOOL_ROUTER,
    classify_call,
)
from .host_input_attestation import canonical, load_host_key
from .mechanism_attribution import (
    _pair_key,
    load_harbor_bundle_projection,
    project_atif,
)

SCHEMA = "agentscookbook.host-nested-search-observation.v1"
REPORT_SCHEMA = "agentscookbook.host-nested-search-pair.v1"
DOMAIN = b"agentscookbook:host-nested-search:v1\x00"
MAX_TRACE_BYTES = 64 * 1024 * 1024
MAX_RECEIPT_BYTES = 2_097_152
MAX_CALLS = 10_000
MAX_DEPTH = 8
OPAQUE = frozenset((SHELL, TOOL_ROUTER))


def _hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _calls(path: Path) -> tuple[str, list[str], int, set[str]]:
    """Return immutable ATIF byte identity, opaque parents and visible searches."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TRACE_BYTES:
        raise ValueError("unsafe-or-oversized-trajectory")
    raw = path.read_bytes()
    projected = project_atif(path)
    if projected.get("tool_order_complete") is not True:
        raise ValueError("atif-tool-order-unqualified")
    try:
        atif = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid-atif") from exc
    seen: set[str] = set()
    opaque: list[str] = []
    for step in atif["steps"]:
        if not isinstance(step, dict):
            raise ValueError("malformed-atif-step")
        for call in step.get("tool_calls", []):
            name, call_id = call.get("function_name"), call.get("tool_call_id")
            if (
                not isinstance(name, str) or not name.strip()
                or not isinstance(call_id, str) or not 0 < len(call_id) <= 512
                or call_id in seen or len(seen) >= MAX_CALLS
                or "nested_calls" in call
            ):
                raise ValueError("unbound-or-duplicate-atif-call-id")
            seen.add(call_id)
            arguments = call.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                raise ValueError("malformed-atif-call-arguments")
            if classify_call(name, arguments or {}, subject="hashmarks") in OPAQUE:
                opaque.append(_digest(call_id.encode("utf-8")))
    count = projected.get("native_search_calls")
    if type(count) is not int or count < 0:
        raise ValueError("native-search-count-unavailable")
    return _digest(raw), opaque, count, {
        _digest(call_id.encode("utf-8")) for call_id in seen
    }


def _nested_calls(
    rows: object, *, seen: set[str], depth: int = 0,
) -> tuple[int, int]:
    """Validate one host-observed child tree. No inferred shell commands."""
    if (
        depth > MAX_DEPTH or not isinstance(rows, list)
        or len(rows) > MAX_CALLS
    ):
        raise ValueError("invalid-nested-call-tree")
    native_search = 0
    visited = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid-nested-call")
        if not {"call_id_sha256", "tool", "input"} <= set(row):
            raise ValueError("missing-nested-call-fields")
        if set(row) - {"call_id_sha256", "tool", "input", "nested_calls"}:
            raise ValueError("unrecognized-nested-call-fields")
        call_id = row["call_id_sha256"]
        tool = row["tool"]
        inputs = row["input"]
        if (
            not _hex64(call_id) or call_id in seen
            or not isinstance(tool, str) or not 0 < len(tool) <= 256
            or not isinstance(inputs, dict)
        ):
            raise ValueError("invalid-or-duplicate-nested-call")
        seen.add(call_id)
        category = classify_call(tool, inputs, subject="hashmarks")
        child = row.get("nested_calls")
        if (category in OPAQUE) != ("nested_calls" in row):
            raise ValueError("unresolved-or-forged-nested-router-boundary")
        if category == NATIVE_SEARCH:
            native_search += 1
        visited += 1
        if child is not None:
            count, nodes = _nested_calls(child, seen=seen, depth=depth + 1)
            native_search += count
            visited += nodes
        if len(seen) > MAX_CALLS:
            raise ValueError("nested-call-budget-exceeded")
    return native_search, visited


def _verify_body(
    body: object, *, trajectory: Path, campaign_id: str, trial_id: str,
    host_identity: str, host_build_sha256: str,
) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) != {
        "schema", "campaign_id", "trial_id", "trajectory_sha256",
        "host_identity", "host_build_sha256", "captures",
    }:
        raise ValueError("invalid-nested-attestation-fields")
    if (
        body["schema"] != SCHEMA
        or body["campaign_id"] != campaign_id
        or body["trial_id"] != trial_id
        or body["host_identity"] != host_identity
        or body["host_build_sha256"] != host_build_sha256
    ):
        raise ValueError("foreign-or-stale-nested-attestation")
    identity, opaque, explicit, all_ids = _calls(trajectory)
    if body["trajectory_sha256"] != identity:
        raise ValueError("nested-attestation-trajectory-drift")
    captures = body["captures"]
    if not isinstance(captures, list) or len(captures) != len(opaque):
        raise ValueError("incomplete-opaque-parent-coverage")
    expected = set(opaque)
    seen: set[str] = set(all_ids)
    captured: set[str] = set()
    hidden_search = 0
    nodes = 0
    for row in captures:
        if not isinstance(row, dict) or set(row) != {
            "parent_call_id_sha256", "complete", "nested_calls",
        }:
            raise ValueError("invalid-nested-parent-observation")
        parent = row["parent_call_id_sha256"]
        if parent not in expected or parent in captured or row["complete"] is not True:
            raise ValueError("unqualified-or-duplicate-opaque-parent")
        captured.add(parent)
        count, n = _nested_calls(row["nested_calls"], seen=seen)
        hidden_search += count
        nodes += n
    if captured != expected:
        raise ValueError("missing-opaque-parent-observations")
    return {
        "verified_opaque_parents": len(captured),
        "host_asserted_nested_calls": nodes,
        "explicit_native_search_calls": explicit,
        "host_observed_nested_native_search_calls": hidden_search,
        "combined_observed_native_search_calls": explicit + hidden_search,
        "trajectory_sha256": identity,
    }


def host_capture_nested_observations(
    trajectory: Path, captures: list[dict[str, Any]], *, campaign_id: str,
    trial_id: str, host_identity: str, host_build_sha256: str, key: bytes,
) -> dict[str, Any]:
    """Privileged-host integration only: seal actually intercepted call trees.

    Do not invoke this function from an agent workspace, an ATIF parser, or a
    model-facing tool; those are not owners of nested-call completeness.
    """
    if not isinstance(key, bytes) or not 32 <= len(key) <= 4096:
        raise ValueError("invalid-host-key")
    if not isinstance(host_identity, str) or not 0 < len(host_identity) <= 128:
        raise ValueError("invalid-host-identity")
    if not _hex64(host_build_sha256):
        raise ValueError("invalid-host-build-sha256")
    identity, _, _, _ = _calls(trajectory)
    body = {
        "schema": SCHEMA,
        "campaign_id": campaign_id,
        "trial_id": trial_id,
        "trajectory_sha256": identity,
        "host_identity": host_identity,
        "host_build_sha256": host_build_sha256,
        "captures": captures,
    }
    _verify_body(
        body, trajectory=trajectory, campaign_id=campaign_id,
        trial_id=trial_id, host_identity=host_identity,
        host_build_sha256=host_build_sha256,
    )
    return {
        **body, "mac_sha256": hmac.new(
            key, DOMAIN + canonical(body), hashlib.sha256
        ).hexdigest(),
    }


def write_host_nested_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    """Create-only, owner-only host receipt; never overwrite another run."""
    if path.is_symlink() or path.exists():
        raise ValueError("nested-receipt-exists-or-symlink")
    payload = canonical(dict(receipt)) + b"\n"
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ValueError("nested-receipt-oversized")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def verify_nested_observations(
    trajectory: Path, receipt: Path, key: bytes, *, campaign_id: str,
    trial_id: str, host_identity: str, host_build_sha256: str,
) -> dict[str, Any]:
    """Read the external receipt and authenticate it against exact ATIF bytes."""
    if not isinstance(key, bytes) or not 32 <= len(key) <= 4096:
        raise ValueError("invalid-host-key")
    if not isinstance(host_identity, str) or not 0 < len(host_identity) <= 128:
        raise ValueError("invalid-expected-host-identity")
    if not _hex64(host_build_sha256):
        raise ValueError("invalid-expected-host-build-sha256")
    if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > MAX_RECEIPT_BYTES:
        raise ValueError("unsafe-or-oversized-nested-receipt")
    try:
        record = json.loads(receipt.read_bytes())
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid-nested-receipt-json") from exc
    if not isinstance(record, dict) or set(record) != {
        "schema", "campaign_id", "trial_id", "trajectory_sha256",
        "host_identity", "host_build_sha256", "captures", "mac_sha256",
    }:
        raise ValueError("malformed-nested-receipt")
    signed = record["mac_sha256"]
    if not _hex64(signed):
        raise ValueError("malformed-nested-mac")
    body = {name: value for name, value in record.items() if name != "mac_sha256"}
    calculated = hmac.new(key, DOMAIN + canonical(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signed, calculated):
        raise ValueError("nested-host-authentication-failed")
    evidence = _verify_body(
        body, trajectory=trajectory, campaign_id=campaign_id,
        trial_id=trial_id, host_identity=host_identity,
        host_build_sha256=host_build_sha256,
    )
    return {
        "qualified": True,
        "receipt_sha256": _digest(receipt.read_bytes()),
        **evidence,
        "host_key_authenticated": True,
        "host_asserted_complete": True,
        "independently_proven_complete": False,
        "model_attention_proven": False,
    }


def audit_attested_nested_pair(
    bare_bundle: Path, treated_bundle: Path, bare_receipt: Path,
    treated_receipt: Path, *, key: bytes, host_identity: str,
    host_build_sha256: str,
) -> dict[str, Any]:
    """Separate read-only comparison: never retrofit unauthenticated ATIF."""
    bare = load_harbor_bundle_projection(bare_bundle)
    treated = load_harbor_bundle_projection(treated_bundle)
    left, right = bare["receipt"], treated["receipt"]
    if (
        left.get("subject") != "none" or right.get("subject") != "hashmarks"
        or _pair_key(left) != _pair_key(right)
        or not _pair_key(left)[0]
        or left.get("status") not in ("PASS", "FAIL")
        or right.get("status") not in ("PASS", "FAIL")
        or not isinstance(left.get("trial_id"), str)
        or not isinstance(right.get("trial_id"), str)
    ):
        raise ValueError("unmatched-or-unqualified-harbor-pair")
    campaign_id = _pair_key(left)[0]
    bare_proof = verify_nested_observations(
        bare_bundle / "trajectory.json", bare_receipt, key,
        campaign_id=campaign_id, trial_id=left["trial_id"],
        host_identity=host_identity, host_build_sha256=host_build_sha256,
    )
    treated_proof = verify_nested_observations(
        treated_bundle / "trajectory.json", treated_receipt, key,
        campaign_id=campaign_id, trial_id=right["trial_id"],
        host_identity=host_identity, host_build_sha256=host_build_sha256,
    )
    delta = (
        treated_proof["combined_observed_native_search_calls"]
        - bare_proof["combined_observed_native_search_calls"]
    )
    return {
        "schema": REPORT_SCHEMA,
        "qualified": True,
        "qualification": "HOST_MAC_ASSERTED_COMPLETE_NESTED_COUNTS",
        "campaign_id": campaign_id,
        "task": left["task_id"],
        "harness": left["harness"],
        "model": left["model"],
        "replicate_id": left["replicate_id"],
        "bare": bare_proof,
        "hashmarks": treated_proof,
        "augmented_observed_native_search_delta": delta,
        "native_search_savings_proven": False,
        "independently_proven_nested_completeness": False,
        "host_assertion_not_independent_execution_proof": True,
        "causal_influence_proven": False,
        "modified_canonical_harbor_report": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bare-bundle", required=True, type=Path)
    parser.add_argument("--hashmarks-bundle", required=True, type=Path)
    parser.add_argument("--bare-attestation", required=True, type=Path)
    parser.add_argument("--hashmarks-attestation", required=True, type=Path)
    parser.add_argument("--host-key-file", required=True, type=Path)
    parser.add_argument("--expected-host-identity", required=True)
    parser.add_argument("--expected-host-build-sha256", required=True)
    parser.add_argument("--require-qualified", action="store_true")
    args = parser.parse_args()
    try:
        report = audit_attested_nested_pair(
            args.bare_bundle, args.hashmarks_bundle,
            args.bare_attestation, args.hashmarks_attestation,
            key=load_host_key(args.host_key_file),
            host_identity=args.expected_host_identity,
            host_build_sha256=args.expected_host_build_sha256,
        )
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
        print(json.dumps({
            "schema": REPORT_SCHEMA, "qualified": False, "reason": str(exc),
        }, sort_keys=True, indent=2))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
