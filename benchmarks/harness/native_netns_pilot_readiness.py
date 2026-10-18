"""E260: independently re-read native netns trial receipts before a paired pilot.

This is an *observation and grading readiness* gate. It does NOT run a
provider, prove an independent timestamp/reviewer, or produce causal uplift.
A real host may supply artifacts; model-free tests only prove rejection
of unsupported or incomplete evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import load_host_key
from .native_session_cohort_audit import audit_native_session_cohort
from .opencode_native_trial import SCHEMA as TRIAL_SCHEMA
from .native_netns_host_attestation import verify_isolation_attestation
from .native_execve_evidence_join import verify_native_execve_join
from .native_netns_probe import kernel_probe_receipt_valid
from .native_mount_probe import inspect_mount_probe
from .native_chat_pair_admission import verify_native_chat_pair_claim
from .native_chat_transport_parity import audit_frozen_chat_treatments
from .trusted_treatments import load_manifest, verify_manifest

SCHEMA = "agentscookbook.netns-paired-pilot-readiness.v4"
HEX = set("0123456789abcdef")


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def evaluate_native_netns_pilot(
    manifest: Mapping[str, Any], *, results_root: Path,
    sources_root: Path, workspace: Path, host_key: bytes,
    host_identity: str, approved_endpoint_sha256: str,
    expected_bwrap_sha256: str, expected_hashmarks_sha256: str,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    if not (_hex64(expected_bwrap_sha256) and _hex64(expected_hashmarks_sha256)):
        raise ValueError("missing-independent-binary-pins")
    cohort = audit_native_session_cohort(
        frozen, results_root=results_root, sources_root=sources_root,
        key=host_key, expected_workspace=workspace,
        host_identity=host_identity,
        approved_endpoint_sha256=approved_endpoint_sha256,
    )
    issues: Counter[str] = Counter()
    # Independent frozen-source observation. A successful *single* arm
    # cannot promote a presentation comparison that the wire cannot carry.
    try:
        pairs = audit_frozen_chat_treatments(frozen, sources_root=sources_root)
        if not pairs["fully_compatible_for_paired_study"]:
            issues["incompatible-frozen-native-chat-pairs"] += 1
    except (OSError, ValueError, TypeError, KeyError):
        pairs = None
        issues["native-chat-frozen-pair-admission-unavailable"] += 1
    if not cohort["native_session_cohort_observationally_consistent"]:
        issues["unqualified-native-session-cohort"] += 1
    checked = 0
    execve_requested = 0
    execve_verified = 0
    for cell in frozen["assignments"]:
        receipt = results_root / cell["trial_id"] / "trial-execution.json"
        if (receipt.is_symlink() or not receipt.is_file()
                or receipt.stat().st_size > 65_536):
            issues["missing-or-unsafe-isolation-execution-receipt"] += 1
            continue
        try:
            evidence = json.loads(receipt.read_bytes())
        except (OSError, ValueError, UnicodeError):
            issues["malformed-isolation-execution-receipt"] += 1
            continue
        if not isinstance(evidence, dict) or not isinstance(evidence.get("gateway"), dict):
            issues["unrecognized-isolation-execution-receipt"] += 1
            continue
        if any((
            evidence.get("schema") != TRIAL_SCHEMA,
            evidence.get("trial_id") != cell["trial_id"],
            evidence.get("manifest_sha256") != frozen["manifest_sha256"],
            evidence.get("native_exit_code") != 0,
            evidence.get("native_trial_evidence_qualified") is not True,
            evidence.get("host_receipt_finalized") is not True,
            evidence.get("native_gateway_route_completed") is not True,
            evidence.get("linux_network_namespace_requested") is not True,
            evidence.get("host_network_egress_intentionally_disabled") is not True,
            evidence.get("kernel_distinct_netns_relay_observed") is not True,
            evidence.get("native_sandbox_binary_sha256") != expected_bwrap_sha256,
            evidence.get("frozen_hashmarks_executable_sha256") != expected_hashmarks_sha256,
            evidence.get("observed_provider_response_relays") != 1,
            evidence["gateway"].get("kernel_netns_peer_check_required") is not True,
            evidence["gateway"].get("distinct_netns_peer_connections", 0) < 1,
            evidence.get("native_process_origin_proven") is not False,
            evidence.get("causal_improvement_proven") is not False,
            evidence["gateway"].get("native_chat_tool_wire_contract_enforced") is not True,
            evidence["gateway"].get("provider_issued_tool_observed") is not True,
            evidence["gateway"].get("provider_issued_tool_continuation_verified") is not True,
            not isinstance(evidence["gateway"].get("provider_issued_response_sha256"), str),
            not (
                isinstance(evidence["gateway"].get("provider_issued_response_sha256"), str)
                and _hex64(evidence["gateway"]["provider_issued_response_sha256"])
            ),
            not verify_native_chat_pair_claim(
                evidence.get("native_chat_pair_preflight"),
                trial_id=cell["trial_id"],
                manifest_sha256=frozen["manifest_sha256"],
            ),
            not kernel_probe_receipt_valid(
                evidence.get("native_kernel_preflight"),
                expected_bwrap_sha256=expected_bwrap_sha256,
            ),
            not inspect_mount_probe(
                evidence.get("native_mount_preflight"),
                expected_bwrap_sha256=expected_bwrap_sha256,
                expected_config_sha256=evidence.get("config_sha256"),
            ),
        )):
            issues["inconsistent-or-self-upgraded-native-isolation-receipt"] += 1
            continue
        pair_report = evidence["native_chat_pair_preflight"]
        if (pair_report["pair_id"] != cell["pair_id"]
                or pair_report["arm"] != cell["arm"]
                or pair_report["diagnostic_only"] is not False
                or pair_report["paired_transport_compatible"] is not True):
            issues["native-chat-diagnostic-or-incompatible-paired-receipt"] += 1
            continue
        if pairs is not None:
            expected_row = next(
                (row for row in pairs["rows"] if row["trial_id"] == cell["trial_id"]),
                None,
            )
            if (expected_row is None
                    or pair_report["representations"][cell["arm"]]
                    != expected_row["model_input_representation"]
                    or expected_row["chat_tool_content_wire_compatible"] is not True):
                issues["native-chat-signed-pair-differs-from-frozen-source"] += 1
                continue
        binding = evidence.get("native_session_binding")
        if (not isinstance(binding, dict)
                or binding.get("native_session_to_gateway_packet_bound") is not True
                or binding.get("signed_host_input_and_transport_verified") is not True
                or binding.get("provider_issued_native_arguments_bound") is not True
                or evidence.get("provider_issued_native_export_arguments_bound") is not True
                or binding.get("provider_issued_response_sha256")
                   != evidence["gateway"]["provider_issued_response_sha256"]):
            issues["incomplete-native-session-transport-binding"] += 1
            continue
        # Execution JSON is agent-adjacent local data, not independent
        # kernel authority. Require a separate host-key signature bound
        # to its exact bytes and the original provider input attestation.
        try:
            raw_execution = receipt.read_bytes()
            original_provider = (
                results_root / cell["trial_id"] / "host-attestation.json"
            ).read_bytes()
            signed = results_root / cell["trial_id"] / "trial-execution.attestation.json"
            if not verify_isolation_attestation(
                signed, report=evidence, execution_bytes=raw_execution,
                provider_attestation_bytes=original_provider,
                host_identity=host_identity, host_key=host_key,
            ):
                raise ValueError("host-isolation-mac-or-source-mismatch")
        except (OSError, ValueError, TypeError):
            issues["host-isolation-attestation-unverified"] += 1
            continue
        if evidence.get("native_host_execve_observation_required") is True:
            execve_requested += 1
        try:
            if verify_native_execve_join(
                evidence, trial_dir=results_root / cell["trial_id"],
                key=host_key, campaign_id=frozen["design"]["campaign_id"],
                trial_id=cell["trial_id"], host_identity=host_identity,
                host_build_sha256=frozen["host_build_sha256"],
            ):
                execve_verified += 1
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            issues["native-execve-host-receipt-join-unverified"] += 1
            continue
        checked += 1
    if execve_requested and execve_requested != len(frozen["assignments"]):
        issues["mixed-native-execve-observation-cohort"] += 1
    if checked != len(frozen["assignments"]):
        issues["incomplete-planned-native-isolation-population"] += 1
    return {
        "schema": SCHEMA,
        "campaign_id": frozen["design"]["campaign_id"],
        "manifest_sha256": frozen["manifest_sha256"],
        "planned_trials": len(frozen["assignments"]),
        "verified_local_isolation_receipts": checked,
        "native_execve_observation_requested_trials": execve_requested,
        "native_execve_host_receipts_independently_verified": execve_verified,
        "native_execve_per_tool_call_ancestry_proven": False,
        "native_execve_no_hidden_searches_proven": False,
        "native_execve_causal_search_savings_proven": False,
        "native_session_cohort": cohort,
        "independently_checked_chat_pairs": pairs,
        "paired_pilot_observations_ready_for_independent_grading": not issues,
        "blockers": dict(sorted(issues.items())),
        "provider_issued_call_observations_host_mac_verified": (
            checked == len(frozen["assignments"]) and not issues
        ),
        "provider_execution_or_attention_independently_proven": False,
        "network_namespace_peer_observations_host_mac_verified": (
            checked == len(frozen["assignments"])
        ),
        "native_kernel_preflight_receipts_host_mac_verified": (
            checked == len(frozen["assignments"]) and not issues
        ),
        "native_mount_preflight_receipts_host_mac_verified": (
            checked == len(frozen["assignments"]) and not issues
        ),
        "point_in_time_mount_probe_is_independent_process_proof": False,
        "point_in_time_kernel_probe_is_exhaustive_egress_proof": False,
        "exclusive_network_egress_independently_audited": False,
        "native_executable_origin_independently_proven": False,
        "independent_oracle_grading_completed": False,
        "pre_work_timestamp_independently_proven": False,
        "randomized_causal_hashmarks_uplift_proven": False,
        "native_pair_diagnostic_only_counts_as_paired_trial": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--sources-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--host-identity", required=True)
    parser.add_argument("--approved-endpoint-sha256", required=True)
    parser.add_argument("--bwrap-sha256", required=True)
    parser.add_argument("--hashmarks-sha256", required=True)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    try:
        report = evaluate_native_netns_pilot(
            load_manifest(args.manifest), results_root=args.results_root,
            sources_root=args.sources_root, workspace=args.workspace,
            host_key=load_host_key(args.host_key_file),
            host_identity=args.host_identity,
            approved_endpoint_sha256=args.approved_endpoint_sha256,
            expected_bwrap_sha256=args.bwrap_sha256,
            expected_hashmarks_sha256=args.hashmarks_sha256,
        )
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({
            "schema": SCHEMA, "paired_pilot_observations_ready_for_independent_grading": False,
            "reason": str(exc), "randomized_causal_hashmarks_uplift_proven": False,
        }, sort_keys=True, indent=2))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if (args.require_ready and
                 not report["paired_pilot_observations_ready_for_independent_grading"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
