"""E239/E240: independent campaign seal, exact trial alignment, qualification.

This verifies source/assignment/reviewer authority carried by external
signatures, and keeps causal claims withheld. Signatures must be produced
outside agent workspaces; this module deliberately has no 'self-approve' or
reviewer-signing CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .host_input_attestation import (
    canonical, load_host_key, _load_receipt, verify_host_attestations,
)
from .intervention_audit import audit_intervention_bundles
from .mechanism_attribution import load_harbor_bundle_projection
from .trusted_treatments import load_manifest, verify_manifest

SCHEMA = "agentscookbook.independent-campaign-seal.v1"
REPORT_SCHEMA = "agentscookbook.independent-campaign-qualification.v1"
DOMAIN = b"agentscookbook:independent-campaign-seal:v1\x00"


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def verify_seal(manifest: Mapping[str, Any], seal: object, *, key: bytes) -> dict[str, Any]:
    """A separate external custodian vouches for a frozen plan.

    Signed booleans prove claims made by that custodian, not chronology or
    true independence; explicit evidence may still be withheld.
    """
    frozen = verify_manifest(dict(manifest))
    if not isinstance(seal, dict) or set(seal) != {
        "schema", "campaign_id", "manifest_sha256", "design_sha256",
        "authority_id", "review_corpus_sha256",
        "independent_oracle_review_completed", "pre_work_frozen",
        "external_anchor_sha256", "mac_sha256",
    } or seal["schema"] != SCHEMA:
        raise ValueError("invalid-independent-campaign-seal")
    if (seal["campaign_id"] != frozen["design"]["campaign_id"]
            or seal["manifest_sha256"] != frozen["manifest_sha256"]
            or seal["design_sha256"] != frozen["design_sha256"]
            or not isinstance(seal["authority_id"], str)
            or not 0 < len(seal["authority_id"]) <= 128
            or not all(_hex64(seal[k]) for k in (
                "review_corpus_sha256", "external_anchor_sha256", "mac_sha256"
            ))
            or type(seal["independent_oracle_review_completed"]) is not bool
            or type(seal["pre_work_frozen"]) is not bool):
        raise ValueError("stale-or-malformed-independent-campaign-seal")
    fields = {name: value for name, value in seal.items() if name != "mac_sha256"}
    correct = hmac.new(key, DOMAIN + canonical(fields), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(correct, seal["mac_sha256"]):
        raise ValueError("unauthenticated-campaign-seal")
    return dict(fields)


def validate_trial_alignment(
    manifest: Mapping[str, Any], bundles_root: Path, attestations_root: Path,
) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    expected = {cell["trial_id"]: cell for cell in frozen["assignments"]}
    observed: set[str] = set()
    problems: Counter[str] = Counter()
    if any(not root.is_dir() or root.is_symlink() for root in (
        bundles_root, attestations_root,
    )):
        problems["unavailable-or-unsafe-results-root"] += 1
    else:
        for entry in sorted(bundles_root.iterdir()):
            if entry.name.startswith("."):
                continue
            if not entry.is_dir() or entry.is_symlink():
                problems["non-directory-or-symlink-run-entry"] += 1
                continue
            cell = expected.get(entry.name)
            if cell is None:
                problems["foreign-or-unplanned-trial"] += 1
                continue
            if entry.name in observed:
                problems["duplicate-trial"] += 1
                continue
            observed.add(entry.name)
            try:
                projected = load_harbor_bundle_projection(entry)
                receipt = projected["receipt"]
                execution = receipt.get("execution")
                if (receipt.get("task_id") != cell["task"]
                        or receipt.get("harness") != cell["harness"]
                        or receipt.get("replicate_id") != cell["replicate"]
                        or receipt.get("subject") != "hashmarks"
                        or receipt.get("model") != frozen["design"]["model"]
                        or not isinstance(execution, dict)
                        or execution.get("campaign_id") != frozen["design"]["campaign_id"]):
                    raise ValueError("receipt-identities-mismatch")
                treatment = execution.get("mcp_treatment")
                if (not isinstance(treatment, dict) or treatment.get("full_contract") is not True
                        or treatment.get("source_contract_identity") != frozen["source_contract_identity"]):
                    raise ValueError("frozen-source-contract-mismatch")
                attached = _load_receipt(attestations_root / (entry.name + ".json"))
                if (not isinstance(attached, dict)
                        or attached.get("trial_id") != cell["trial_id"]
                        or attached.get("campaign_id") != frozen["design"]["campaign_id"]
                        or not isinstance(attached.get("deliveries"), list)
                        or not attached["deliveries"]):
                    raise ValueError("foreign-or-unbound-host-observation")
                if any(
                    not isinstance(row, dict)
                    or row.get("host_build_sha256") != frozen["host_build_sha256"]
                    or not isinstance(row.get("intervention"), dict)
                    or row["intervention"].get("arm") != cell["arm"]
                    or row["intervention"].get("design_sha256") != frozen["design_sha256"]
                    for row in attached["deliveries"]
                ):
                    raise ValueError("host-observation-or-assignment-mismatch")
            except (OSError, ValueError, TypeError, KeyError):
                problems["receipt-or-host-assignment-mismatch"] += 1
        missing = len(set(expected) - observed)
        if missing:
            problems["missing-planned-trial"] = missing
        unexpected = [
            f for f in attestations_root.iterdir()
            if f.name not in {name + ".json" for name in expected}
            and not f.name.startswith(".")
        ]
        if unexpected:
            problems["foreign-attestation-entry"] += len(unexpected)
    return {
        "complete": not problems and len(observed) == len(expected),
        "expected_trials": len(expected),
        "observed_planned_trials": len(observed),
        "issues": dict(sorted(problems.items())),
    }


def qualify_campaign(manifest: Mapping[str, Any], bundles_root: Path,
                     attestations_root: Path, host_key: bytes,
                     seal: object | None, *, independent_key: bytes | None,
                     require_provider_submission: bool = False) -> dict[str, Any]:
    frozen = verify_manifest(dict(manifest))
    audit = audit_intervention_bundles(
        frozen["design"], bundles_root, attestations_root, host_key,
    )
    alignment = validate_trial_alignment(
        frozen, bundles_root, attestations_root,
    )
    issues: Counter[str] = Counter()
    if audit["coverage_state"] != "COMPLETE":
        issues["unqualified-host-attested-intervention-coverage"] += 1
    if audit.get("comparison_publication_state") != "PUBLISHED_COMPLETE_DESCRIPTIVE":
        issues["matched-effect-publication-unqualified"] += 1
    if not alignment["complete"]:
        issues["incomplete-or-misaligned-frozen-plan"] += 1
    verified_submission_trials = 0
    effective_submission_requirement = (
        require_provider_submission or frozen["design"]["study"] == "timing"
    )
    if effective_submission_requirement:
        # Independent read-back, never from ATIF agent prose, unsigned file
        # metadata or source-provided claim of provider dispatch.
        for cell in frozen["assignments"]:
            trial_id = cell["trial_id"]
            try:
                verified = verify_host_attestations(
                    bundles_root / trial_id / "trajectory.json",
                    attestations_root / (trial_id + ".json"), host_key,
                    campaign_id=frozen["design"]["campaign_id"],
                    trial_id=trial_id,
                )
                submitted = (
                    verified["attestation_state"] == "VERIFIED"
                    and verified["delivery_state"] == "PROVEN"
                    and verified["provider_submission_state"] == "SUBMITTED"
                    and verified["verified_provider_submission_packets"]
                    == verified["returned_packets"]
                    and type(verified["returned_packets"]) is int
                    and verified["returned_packets"] > 0
                )
            except (OSError, ValueError, KeyError, TypeError):
                submitted = False
            if submitted:
                verified_submission_trials += 1
            else:
                issues["missing-or-unverified-provider-submission"] += 1
    seal_valid = False
    reviewed = False
    alleged_pre_work = False
    if seal is None or independent_key is None:
        issues["independent-authority-seal-unavailable"] += 1
    else:
        try:
            fields = verify_seal(frozen, seal, key=independent_key)
            seal_valid = True
            reviewed = fields["independent_oracle_review_completed"]
            alleged_pre_work = fields["pre_work_frozen"]
            if not reviewed:
                issues["independent-review-not-approved"] += 1
            if not alleged_pre_work:
                issues["external-pre-work-freeze-not-attested"] += 1
        except (ValueError, TypeError):
            issues["independent-authority-seal-rejected"] += 1
    if seal_valid and independent_key == host_key:
        issues["host-and-independent-authority-keys-must-differ"] += 1
        reviewed = False
    qualification = not issues
    # Host-intervention comparisons are only eligible for publication after
    # independent plan/alignment/seal qualification, never on audit alone.
    publication_admitted = (
        qualification
        and audit.get("comparison_publication_state") == "PUBLISHED_COMPLETE_DESCRIPTIVE"
    )
    return {
        "schema": REPORT_SCHEMA,
        "campaign_id": frozen["design"]["campaign_id"],
        "manifest_sha256": frozen["manifest_sha256"],
        "host_intervention_coverage": audit,
        "frozen_trial_alignment": alignment,
        "require_provider_submission": effective_submission_requirement,
        "provider_submission_policy": (
            "MANDATORY_TIMING" if frozen["design"]["study"] == "timing"
            else "EXPLICIT" if require_provider_submission else "OPTIONAL"
        ),
        "verified_provider_submission_trials": verified_submission_trials,
        "expected_provider_submission_trials": (
            len(frozen["assignments"]) if effective_submission_requirement else None
        ),
        "provider_processing_proven": False,
        "independent_seal_authenticated": seal_valid,
        "independent_review_custodian_claim_authenticated": reviewed,
        "external_pre_work_claim_authenticated": alleged_pre_work,
        "descriptive_campaign_admitted": qualification,
        "descriptive_comparison_publication_admitted": publication_admitted,
        "qualification_blockers": dict(sorted(issues.items())),
        "real_randomization_proven": False,
        "trusted_pre_work_timestamp_proven": False,
        "model_attention_proven": False,
        "causal_effect_proven": False,
        "interpretation": (
            "Admitted means independently key-authenticated custodial "
            "assertions and complete verified model-input/campaign evidence. "
            "It does not prove a timestamp predating the trial, independent "
            "reviewer work, assignment randomness, model attention or causality. "
            "Source review and timestamp authority still require external proof."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--bundles-root", type=Path, required=True)
    parser.add_argument("--attestations-root", type=Path, required=True)
    parser.add_argument("--host-key-file", type=Path, required=True)
    parser.add_argument("--seal", type=Path)
    parser.add_argument("--independent-key-file", type=Path)
    parser.add_argument("--require-admitted", action="store_true")
    parser.add_argument("--require-provider-submission", action="store_true")
    args = parser.parse_args()
    try:
        manifest = load_manifest(args.manifest)
        host_key = load_host_key(args.host_key_file)
        seal = _load_receipt(args.seal) if args.seal is not None else None
        independent_key = (
            load_host_key(args.independent_key_file)
            if args.independent_key_file is not None else None
        )
        report = qualify_campaign(manifest, args.bundles_root,
                                  args.attestations_root, host_key,
                                  seal, independent_key=independent_key,
                                  require_provider_submission=args.require_provider_submission)
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "descriptive_campaign_admitted": False,
                          "error": str(exc)}, sort_keys=True, indent=2))
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 2 if args.require_admitted and not report["descriptive_campaign_admitted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
