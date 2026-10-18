"""E255/E256: independent read-back of saved native sessions and host receipts.

The reference source payload is supplied outside the evaluated workspace.
This validator never signs, executes a provider, constructs missing artifacts,
or upgrades a local session export into externally proven OS process identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import (
    _load_receipt, canonical, load_host_key, verify_host_attestations,
)
from .intervention_audit import _facet_compare
from .mechanism_attribution import project_atif
from .opencode_native_session import inspect_native_export, MAX_EXPORT_BYTES
from .trusted_treatments import load_manifest, select_treatment, verify_manifest

SCHEMA = "agentscookbook.native-session-cohort-audit.v2"


def _json_file(path: Path, limit: int) -> object:
    if (path.is_symlink() or not path.is_file()
            or path.stat().st_size > limit):
        raise ValueError("unavailable-or-unsafe-native-artifact")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("unreadable-native-artifact") from exc


def audit_native_session_cohort(
    manifest: Mapping[str, Any], *, results_root: Path, sources_root: Path,
    key: bytes, expected_workspace: Path,
    host_identity: str, approved_endpoint_sha256: str,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    expected = {cell["trial_id"]: cell for cell in frozen["assignments"]}
    issues: Counter[str] = Counter()
    admitted: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    if (not isinstance(host_identity, str) or not 0 < len(host_identity) <= 128
            or not isinstance(approved_endpoint_sha256, str)
            or len(approved_endpoint_sha256) != 64
            or set(approved_endpoint_sha256) - set("0123456789abcdef")):
        raise ValueError("invalid-independent-native-host-or-endpoint")
    if (not results_root.is_dir() or results_root.is_symlink()
            or not sources_root.is_dir() or sources_root.is_symlink()
            or expected_workspace.is_symlink() or not expected_workspace.is_dir()):
        issues["unavailable-or-unsafe-native-cohort-roots"] += 1
    else:
        present = set()
        for folder in sorted(results_root.iterdir()):
            if folder.name.startswith("."):
                continue
            if folder.is_symlink() or not folder.is_dir() or folder.name not in expected:
                issues["foreign-or-unsafe-trial-directory"] += 1
            else:
                present.add(folder.name)
        if set(expected) - present:
            issues["missing-frozen-native-trial-directory"] += len(set(expected) - present)
        for trial_id, cell in expected.items():
            if trial_id not in present:
                continue
            path = results_root / trial_id
            try:
                raw = path / "native-session.json"
                trace = path / "trajectory.json"
                receipt = path / "host-attestation.json"
                source_path = sources_root / (trial_id + ".json")
                source_info = _json_file(source_path, 524_288)
                if (not isinstance(source_info, dict)
                        or set(source_info) != {"current", "replaced"}):
                    raise ValueError("native-source-selection-contract")
                current, replaced = source_info["current"], source_info["replaced"]
                if not isinstance(current, dict) or (
                    replaced is not None and not isinstance(replaced, dict)
                ):
                    raise ValueError("invalid-native-source-authority")
                selected = select_treatment(
                    frozen, trial_id=trial_id, harness=cell["harness"],
                    task=cell["task"], replicate=cell["replicate"],
                    arm=cell["arm"], current=current, replaced=replaced,
                )
                attested = verify_host_attestations(
                    trace, receipt, key,
                    campaign_id=frozen["design"]["campaign_id"], trial_id=trial_id,
                )
                if (attested["attestation_state"] != "VERIFIED"
                        or attested["delivery_state"] != "PROVEN"
                        or attested["provider_submission_state"] != "SUBMITTED"
                        or attested["verified_model_input_packets"] != 1):
                    raise ValueError("native-host-packet-transport-not-attested")
                envelope = _load_receipt(receipt)
                if (not isinstance(envelope, dict)
                        or not isinstance(envelope.get("deliveries"), list)
                        or len(envelope["deliveries"]) != 1):
                    raise ValueError("native-host-single-submission-contract")
                record = envelope["deliveries"][0]
                meta, transport = record.get("intervention"), record.get("transport")
                if (not isinstance(meta, dict) or not isinstance(transport, dict)
                        or record.get("host_identity") != host_identity
                        or record.get("host_build_sha256") != frozen["host_build_sha256"]
                        or record.get("model_request_sequence") != 1
                        or transport.get("endpoint_sha256") != approved_endpoint_sha256):
                    raise ValueError("native-host-identity-or-transport-drift")
                if any(meta.get(field) != selected[field] for field in (
                    "arm", "study", "presentation", "generation_sha256",
                    "current_generation_sha256", "semantic_sha256",
                    "surface_sha256", "design_sha256",
                )):
                    raise ValueError("native-selected-treatment-mismatch")
                if (raw.is_symlink() or not raw.is_file()
                        or not 0 < raw.stat().st_size <= MAX_EXPORT_BYTES):
                    raise ValueError("native-session-unavailable")
                native_bytes = raw.read_bytes()
                native = json.loads(native_bytes)
                info = native.get("info") if isinstance(native, dict) else None
                if not isinstance(info, dict):
                    raise ValueError("native-session-header-unavailable")
                inspected = inspect_native_export(
                    native_bytes, session_id=info.get("id"), title=info.get("title"),
                    workspace=expected_workspace,
                    expected_provider="agentscookbook-captured",
                    expected_model=frozen["design"]["model"],
                    expected_packet=current["content"],
                    expected_call_id_sha256=record["call_id_sha256"],
                    expected_packet_sha256=record["packet_sha256"],
                )
                atif = _json_file(trace, MAX_EXPORT_BYTES)
                if canonical(atif) != canonical(inspected["atif"]):
                    raise ValueError("native-atif-is-not-projected-from-export")
                trace_evidence = project_atif(trace)
                if (trace_evidence.get("available") is not True
                        or trace_evidence.get("tool_order_complete") is not True
                        or trace_evidence.get("tool_calls") != inspected["native_tool_parts_observed"]
                        or trace_evidence.get("subject_tool_calls") != 1):
                    raise ValueError("native-trace-order-or-subject-count-not-qualified")
                admitted[(cell["harness"], cell["task"],
                          cell["replicate"], cell["arm"])] = {
                    "trial_id": trial_id, "facets": meta,
                    "native_export_sha256": hashlib.sha256(native_bytes).hexdigest(),
                    "native_tool_calls": trace_evidence["tool_calls"],
                    "native_search_calls": trace_evidence["native_search_calls"],
                    "native_read_calls": trace_evidence["native_read_calls"],
                    "subject_routing_timing": trace_evidence["subject_routing_timing"],
                }
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                issues["native-trial-session-or-provider-mismatch"] += 1
    pairs: dict[tuple[str, str, int], dict[str, Any]] = defaultdict(dict)
    for (harness, task, replicate, arm), item in admitted.items():
        pairs[(harness, task, replicate)][arm] = item
    for cell in frozen["assignments"]:
        if cell["arm"] != "control":
            continue
        key_pair = (cell["harness"], cell["task"], cell["replicate"])
        sides = pairs.get(key_pair, {})
        if set(sides) != {"control", "variant"}:
            issues["missing-native-evidence-pair"] += 1
            continue
        reason = _facet_compare(
            sides["control"]["facets"], sides["variant"]["facets"],
            frozen["design"]["study"],
        )
        if reason:
            issues["cross-arm-native-" + reason] += 1
    return {
        "schema": SCHEMA,
        "campaign_id": frozen["design"]["campaign_id"],
        "manifest_sha256": frozen["manifest_sha256"],
        "expected_native_trial_exports": len(expected),
        "verified_native_trial_exports": len(admitted),
        "expected_native_pairs": sum(c["arm"] == "control" for c in frozen["assignments"]),
        "verified_native_pairs": sum(set(sides) == {"control", "variant"} for sides in pairs.values()),
        "native_session_cohort_observationally_consistent": (
            not issues and len(admitted) == len(expected)
        ),
        "issues": dict(sorted(issues.items())),
        "native_tool_order_evidence": {
            "observed_tool_calls": sum(row["native_tool_calls"] for row in admitted.values()),
            "observed_native_search_calls": sum(row["native_search_calls"] for row in admitted.values()),
            "observed_native_read_calls": sum(row["native_read_calls"] for row in admitted.values()),
            "routing_by_arm": {
                arm: dict(sorted(Counter(row["subject_routing_timing"] for key, row in admitted.items() if key[3] == arm).items()))
                for arm in ("control", "variant")
            },
            "export_tool_part_projection_qualified": not issues and len(admitted) == len(expected),
            "host_wide_capture_exhaustiveness_proven": False,
            "native_model_attention_proven": False,
        },
        "native_process_origin_proven": False,
        "exclusive_network_egress_proven": False,
        "independent_oracle_review_proven": False,
        "provider_cognition_proven": False,
        "randomized_causal_uplift_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--sources-root", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--host-key-file", required=True, type=Path)
    parser.add_argument("--host-identity", required=True)
    parser.add_argument("--approved-endpoint-sha256", required=True)
    parser.add_argument("--require-qualified", action="store_true")
    args = parser.parse_args()
    try:
        result = audit_native_session_cohort(
            load_manifest(args.manifest), results_root=args.results_root,
            sources_root=args.sources_root, key=load_host_key(args.host_key_file),
            expected_workspace=args.workspace,
            host_identity=args.host_identity,
            approved_endpoint_sha256=args.approved_endpoint_sha256,
        )
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({
            "schema": SCHEMA, "native_session_cohort_observationally_consistent": False,
            "reason": str(exc), "randomized_causal_uplift_proven": False,
        }, sort_keys=True, indent=2))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 2 if args.require_qualified and not result["native_session_cohort_observationally_consistent"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
