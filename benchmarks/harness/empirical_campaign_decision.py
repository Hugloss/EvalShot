"""E248: evidence-bounded empirical campaign decision, never causal uplift.

Admits only complete externally keyed *descriptive* submissions with frozen
trial alignment, paired control headroom and cross-trial host provenance.
Current native harnesses remain unsupported for native-input claims.
No trusted timestamp, real independent-review labor or causal attribution
is silently inferred from an HMAC or a p-value.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import _load_receipt, load_host_key
from .host_transport_provenance import audit_transport_provenance
from .independent_campaign_qualification import qualify_campaign
from .native_host_readiness import audit_native_readiness
from .trusted_treatments import load_manifest, verify_manifest

SCHEMA = "agentscookbook.empirical-campaign-decision.v1"
TRANSITIONS = {
    "FAIL_TO_FAIL", "FAIL_TO_PASS", "PASS_TO_FAIL", "PASS_TO_PASS",
}


def assess_empirical_campaign(
    manifest: Mapping[str, Any], bundles_root: Path, attestations_root: Path,
    host_key: bytes, seal: object | None, independent_key: bytes | None,
    *, approved_endpoint_sha256: str, expected_host_identity: str,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    admitted = qualify_campaign(
        frozen, bundles_root, attestations_root,
        host_key, seal, independent_key=independent_key,
        require_provider_submission=True,
    )
    provenance = audit_transport_provenance(
        frozen, bundles_root, attestations_root, host_key,
        approved_endpoint_sha256=approved_endpoint_sha256,
        expected_host_identity=expected_host_identity,
    )
    readiness = audit_native_readiness(frozen["design"])
    audit = admitted["host_intervention_coverage"]
    pairs = audit.get("matched_pairs", [])
    expected_pairs = sum(1 for x in frozen["assignments"] if x["arm"] == "control")
    checked = (
        admitted["descriptive_campaign_admitted"] is True
        and provenance["cross_trial_transport_qualified"] is True
        and audit.get("coverage_state") == "COMPLETE"
        and isinstance(pairs, list)
        and len(pairs) == expected_pairs
        and all(isinstance(p, dict) and p.get("outcome_transition") in TRANSITIONS
                for p in pairs)
    )
    changes = Counter(p["outcome_transition"] for p in pairs) if checked else Counter()
    controls_failed = changes["FAIL_TO_FAIL"] + changes["FAIL_TO_PASS"]
    distinct_task_clusters: dict[str, set[str]] = {}
    if checked:
        for row in pairs:
            h, task = row.get("harness"), row.get("task")
            if not isinstance(h, str) or not isinstance(task, str):
                checked = False
                break
            distinct_task_clusters.setdefault(h, set()).add(task)
    blockers: Counter[str] = Counter()
    if not admitted["descriptive_campaign_admitted"]:
        blockers["independent-campaign-not-admitted"] += 1
    if not provenance["cross_trial_transport_qualified"]:
        blockers["cross-trial-transport-provenance-failed"] += 1
    if not checked:
        blockers["complete-graded-pair-population-not-proven"] += 1
    if checked and controls_failed == 0:
        blockers["no-observed-control-failure-headroom"] += 1
    if checked and any(len(tasks) < 8 for tasks in distinct_task_clusters.values()):
        blockers["fewer-than-eight-task-clusters-in-harness"] += 1
    return {
        "schema": SCHEMA,
        "campaign_id": frozen["design"]["campaign_id"],
        "manifest_sha256": frozen["manifest_sha256"],
        "transport": provenance,
        "independent_campaign": {
            "admitted": admitted["descriptive_campaign_admitted"],
            "blockers": admitted["qualification_blockers"],
            "review_assertion_authenticated": admitted["independent_review_custodian_claim_authenticated"],
        },
        "native_host_capability": readiness,
        "graded_pairs": len(pairs) if checked else 0,
        "expected_pairs": expected_pairs,
        "observed_control_failures": controls_failed if checked else None,
        "outcome_transitions": dict(sorted(changes.items())),
        "task_clusters_per_harness": {
            h: len(tasks) for h, tasks in sorted(distinct_task_clusters.items())
        },
        "per_harness_effect": (
            audit.get("per_harness_descriptive_effect")
            if checked else None
        ),
        "submission_bounded_descriptive_population_qualified": not blockers,
        "descriptive_decision_blockers": dict(sorted(blockers.items())),
        # These require a truly integrated harness and independently
        # anchored review/experiment authority, not static instrumentation.
        "native_harness_population_qualified": False,
        "real_human_review_proven": False,
        "trusted_pre_work_timestamp_proven": False,
        "randomized_assignment_proven": False,
        "provider_consumption_proven": False,
        "model_attention_proven": False,
        "causal_hashmarks_improvement_proven": False,
        "interpretation": (
            "Only a submission-bounded matched descriptive population may "
            "qualify. This is neither model-input proof for opaque native "
            "harnesses nor evidence of randomized causal Hashmarks uplift."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundles-root", type=Path, required=True)
    parser.add_argument("--attestations-root", type=Path, required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--independent-key-file", type=Path, required=True)
    parser.add_argument("--seal", type=Path, required=True)
    parser.add_argument("--approved-endpoint-sha256", required=True)
    parser.add_argument("--expected-host-identity", required=True)
    parser.add_argument("--require-descriptive-population", action="store_true")
    args = parser.parse_args()
    try:
        report = assess_empirical_campaign(
            load_manifest(args.manifest), args.bundles_root,
            args.attestations_root, load_host_key(args.host_key_file),
            _load_receipt(args.seal), load_host_key(args.independent_key_file),
            approved_endpoint_sha256=args.approved_endpoint_sha256,
            expected_host_identity=args.expected_host_identity,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({
            "schema": SCHEMA,
            "submission_bounded_descriptive_population_qualified": False,
            "causal_hashmarks_improvement_proven": False,
            "reason": str(exc),
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if (args.require_descriptive_population
                 and not report["submission_bounded_descriptive_population_qualified"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
