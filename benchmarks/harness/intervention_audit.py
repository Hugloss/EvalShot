"""Strict host-attested presentation/freshness intervention audit (E234-E236).

This is an external-host integration boundary, NOT an intervention generator:
Harbor's current trial launcher does not emit independently authenticated
model-input receipts or guarantee controlled treatment assignment. A valid
audit remains descriptive and cannot establish randomization or causal use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from .evaluation_assurance import _bootstrap
from .host_input_attestation import (
    SCHEMA as HOST_SCHEMA, _load_receipt, canonical, load_host_key,
    verify_host_attestations,
)
from .mechanism_attribution import (
    MechanismAttributionError, load_harbor_bundle_projection,
)

SCHEMA = "agentscookbook.host-attested-intervention-design.v1"
REPORT_SCHEMA = "agentscookbook.host-attested-intervention-audit.v1"
MAX_CELLS = 10_000
DIGEST_FIELDS = (
    "catalog_sha256", "prompt_sha256", "oracle_sha256", "workspace_sha256",
)


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _axis(value: object) -> list[str]:
    if (not isinstance(value, list) or not value or len(value) > 256
            or any(not isinstance(v, str) or not 0 < len(v) <= 256 for v in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid-experiment-axis")
    return value


def validate_design(design: object) -> dict[str, Any]:
    if not isinstance(design, dict) or set(design) != {
        "schema", "study", "campaign_id", "model", "harnesses",
        "tasks", "replicates", "arms",
    }:
        raise ValueError("invalid-intervention-design")
    if design["schema"] != SCHEMA or design["study"] not in ("presentation", "freshness", "timing", "text_presentation"):
        raise ValueError("unsupported-intervention-study")
    if any(not isinstance(design[v], str) or not 0 < len(design[v]) <= 256
           for v in ("campaign_id", "model")):
        raise ValueError("invalid-study-identity")
    harnesses, tasks = _axis(design["harnesses"]), _axis(design["tasks"])
    n = design["replicates"]
    if type(n) is not int or not 1 <= n <= 20 or 2 * len(harnesses) * len(tasks) * n > MAX_CELLS:
        raise ValueError("invalid-study-replicates-or-size")
    arms = design["arms"]
    if not isinstance(arms, dict) or set(arms) != {"control", "variant"}:
        raise ValueError("invalid-study-arms")
    if design["study"] == "presentation":
        if arms != {"control": "structured", "variant": "text"}:
            raise ValueError("presentation-arms-must-be-structured-vs-text")
    elif design["study"] == "text_presentation":
        if arms != {
            "control": "compact-json-text",
            "variant": "indented-json-text",
        }:
            raise ValueError("text-presentation-arms-must-be-compact-vs-indented")
    elif design["study"] == "freshness":
        if arms != {"control": "current-generation", "variant": "replaced-generation"}:
            raise ValueError("freshness-arms-must-be-current-vs-replaced")
    elif arms != {"control": "direct-first-request", "variant": "deferred-second-request"}:
        raise ValueError("timing-arms-must-be-direct-vs-deferred")
    return dict(design)


def _facet_compare(a: Mapping[str, Any], b: Mapping[str, Any], study: str) -> str | None:
    if any(a[k] != b[k] for k in DIGEST_FIELDS):
        return "cross-arm-prompt-catalog-oracle-or-workspace-drift"
    if a["current_generation_sha256"] != b["current_generation_sha256"]:
        return "cross-arm-target-generation-drift"
    if study == "timing":
        # Withheld-first-call is a real extra model request. The identical
        # payload must not be misrepresented as a randomized pure timing effect.
        if (
            a["presentation"] != "structured"
            or b["presentation"] != "structured"
            or a["surface_sha256"] != b["surface_sha256"]
            or a["semantic_sha256"] != b["semantic_sha256"]
            or a["generation_sha256"] != b["generation_sha256"]
            or a["generation_sha256"] != a["current_generation_sha256"]
            or a["delivery_request_sequence"] != 1
            or b["delivery_request_sequence"] != 2
            or a["prior_request_sha256"] != "0" * 64
            or b["prior_request_sha256"] == "0" * 64
            or a["prior_transport_sha256"] != "0" * 64
            or b["prior_transport_sha256"] == "0" * 64
        ):
            return "timing-intervention-not-isolated-or-unproven"
        return None
    if a["surface_sha256"] == b["surface_sha256"]:
        return "identical-model-input-surface"
    if study == "text_presentation":
        if (
            a["presentation"] != "compact-json-text"
            or b["presentation"] != "indented-json-text"
            or a["semantic_sha256"] != b["semantic_sha256"]
            or a["generation_sha256"] != b["generation_sha256"]
            or a["generation_sha256"] != a["current_generation_sha256"]
        ):
            return "text-presentation-intervention-not-isolated"
    elif study == "presentation":
        if (a["presentation"] != "structured" or b["presentation"] != "text"
                or a["semantic_sha256"] != b["semantic_sha256"]
                or a["generation_sha256"] != b["generation_sha256"]
                or a["generation_sha256"] != a["current_generation_sha256"]):
            return "presentation-intervention-not-isolated"
    else:
        if (a["presentation"] != b["presentation"]
                or a["generation_sha256"] != a["current_generation_sha256"]
                or b["generation_sha256"] == b["current_generation_sha256"]
                or a["semantic_sha256"] == b["semantic_sha256"]):
            return "freshness-intervention-not-isolated"
    return None


def audit_intervention_observations(
    design: Mapping[str, Any], rows: list[Mapping[str, Any]], *,
    corrupt_bundles: int = 0,
) -> dict[str, Any]:
    """Rows are INTERNAL projections: CLI obtains them from verified bundles
    plus independently MAC-verified host receipts. Never trust user-built rows
    as independently validated evidence.
    """
    plan = validate_design(dict(design))
    expected = {
        (h, t, i, arm)
        for h in plan["harnesses"] for t in plan["tasks"]
        for i in range(1, plan["replicates"] + 1)
        for arm in ("control", "variant")
    }
    registered: dict[tuple[str, str, int, str], Mapping[str, Any]] = {}
    issues: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, Mapping):
            issues["invalid-observation"] += 1
            continue
        key = (row.get("harness"), row.get("task"), row.get("replicate"), row.get("arm"))
        if (type(key[2]) is not int or not all(isinstance(k, str) for k in (key[0], key[1], key[3]))
                or key not in expected):
            issues["foreign-or-unexpected-observation"] += 1
            continue
        if key in registered:
            issues["duplicate-cell"] += 1
            continue
        registered[key] = row
        if row.get("host_attested") is not True:
            issues["host-boundary-not-attested"] += 1
        if row.get("model") != plan["model"] or row.get("status") not in ("PASS", "FAIL"):
            issues["model-or-graded-outcome-mismatch"] += 1
        if (not isinstance(row.get("source_contract_identity"), str)
                or not row["source_contract_identity"]):
            issues["missing-source-contract-identity"] += 1
        meta = row.get("intervention")
        required_facets = {
            "study", "arm", "design_sha256", "presentation",
            "generation_sha256", "current_generation_sha256",
            "semantic_sha256", "catalog_sha256", "prompt_sha256",
            "oracle_sha256", "workspace_sha256", "surface_sha256",
        }
        if plan["study"] == "timing":
            required_facets |= {
                "delivery_request_sequence", "prior_request_sha256",
                "prior_transport_sha256",
            }
        if (not isinstance(meta, Mapping) or set(meta) != required_facets
                or meta.get("study") != plan["study"]
                or meta.get("arm") != key[3]
                or meta.get("presentation") not in (
                     "structured", "text", "compact-json-text", "indented-json-text"
                 )
                or meta.get("design_sha256") != hashlib.sha256(canonical(plan)).hexdigest()
                or any(not _hex64(meta.get(name)) for name in required_facets -
                       {"study", "arm", "presentation", "delivery_request_sequence"})
                or (plan["study"] == "timing"
                    and (type(meta.get("delivery_request_sequence")) is not int
                         or meta["delivery_request_sequence"] not in (1, 2)))):
            issues["host-intervention-design-unbound"] += 1
    missing = expected - set(registered)
    if missing:
        issues["missing-cell"] = len(missing)
    pairs: list[dict[str, Any]] = []
    groups: dict[str, dict[tuple[str, str, str], list[float]]] = defaultdict(lambda: defaultdict(list))
    for h in plan["harnesses"]:
        for t in plan["tasks"]:
            for i in range(1, plan["replicates"] + 1):
                a, b = registered.get((h, t, i, "control")), registered.get((h, t, i, "variant"))
                if a is None or b is None:
                    continue
                if (a.get("host_attested") is not True or b.get("host_attested") is not True
                        or a.get("model") != plan["model"] or b.get("model") != plan["model"]
                        or a.get("status") not in ("PASS", "FAIL")
                        or b.get("status") not in ("PASS", "FAIL")):
                    continue
                if (not isinstance(a.get("source_contract_identity"), str)
                        or not a["source_contract_identity"]
                        or a.get("source_contract_identity") != b.get("source_contract_identity")):
                    issues["cross-arm-source-contract-drift"] += 1
                    continue
                left, right = a.get("intervention"), b.get("intervention")
                if not isinstance(left, Mapping) or not isinstance(right, Mapping):
                    continue
                required = {
                    "study", "arm", "design_sha256", "presentation",
                    "generation_sha256", "current_generation_sha256",
                    "semantic_sha256", "catalog_sha256", "prompt_sha256",
                    "oracle_sha256", "workspace_sha256", "surface_sha256",
                }
                if plan["study"] == "timing":
                    required |= {
                        "delivery_request_sequence", "prior_request_sha256",
                        "prior_transport_sha256",
                    }
                if (set(left) != required or set(right) != required
                        or left.get("study") != plan["study"] or right.get("study") != plan["study"]
                        or left.get("arm") != "control" or right.get("arm") != "variant"
                        or any(x.get("design_sha256") != hashlib.sha256(canonical(plan)).hexdigest()
                               for x in (left, right))
                        or any(not _hex64(x.get(name)) for x in (left, right)
                               for name in required - {"study", "arm", "presentation", "delivery_request_sequence"})
                        or (plan["study"] == "timing"
                            and (type(left.get("delivery_request_sequence")) is not int
                                 or type(right.get("delivery_request_sequence")) is not int))):
                    continue
                reason = _facet_compare(left, right, plan["study"])
                if reason:
                    issues[reason] += 1
                    continue
                effect = int(b["status"] == "PASS") - int(a["status"] == "PASS")
                groups[h][(t, h, plan["model"])].append(float(effect))
                pairs.append({
                    "task": t, "harness": h, "replicate": i,
                    "outcome_transition": a["status"] + "_TO_" + b["status"],
                    "observed_variant_minus_control": effect,
                })
    total_pairs = len(plan["harnesses"]) * len(plan["tasks"]) * plan["replicates"]
    if len(pairs) != total_pairs:
        issues["missing-or-unqualified-matched-pair"] = total_pairs - len(pairs)
    if corrupt_bundles:
        issues["corrupt-or-unverifiable-bundle"] += corrupt_bundles
    complete = not issues and len(registered) == len(expected) and len(pairs) == total_pairs
    # Never turn selective trial survival into a published treatment effect.
    # Keep only completeness diagnostics when any assigned arm is unresolved.
    published_pairs = pairs if complete else []
    published_effects = (
        {h: _bootstrap(groups[h]) for h in plan["harnesses"]}
        if complete else {}
    )
    return {
        "schema": REPORT_SCHEMA,
        "study": plan["study"],
        "campaign_id": plan["campaign_id"],
        "design_sha256": hashlib.sha256(canonical(plan)).hexdigest(),
        "coverage_state": "COMPLETE" if complete else "INCOMPLETE",
        "comparison_publication_state": (
            "PUBLISHED_COMPLETE_DESCRIPTIVE" if complete
            else "WITHHELD_INCOMPLETE_CAMPAIGN"
        ),
        "partial_observed_matched_pairs": len(pairs) if not complete else 0,
        "expected_cells": len(expected),
        "observed_unique_cells": len(registered),
        "qualified_matched_pairs": len(pairs),
        "expected_matched_pairs": total_pairs,
        "issues": dict(sorted(issues.items())),
        "matched_pairs": published_pairs,
        "per_harness_descriptive_effect": published_effects,
        "independent_oracle_review_attested": False,
        "randomization_or_pre_registration_attested": False,
        "causal_effect_qualified": False,
        "model_attention_proven": False,
        "timing_delivery_extra_request_confound": plan["study"] == "timing",
        "interpretation": (
            "The timing variant requires a host-attested tool-free first provider "
            "request, then a second request carrying the same tool evidence. "
            "The extra model invocation and resulting state are confounded "
            "with delay: this is not a pure timing causal experiment. "
            "Only externally keyed host-model-request delivery plus immutable Harbor "
            "grade and exact within-pair facet isolation can enter descriptive "
            "contrasts. Descriptive effects and pair outcomes are withheld "
            "unless every assigned arm is present, valid and matched. "
            "A signed design is not a trusted timestamp, a host MAC "
            "is not model cognition, and complete coverage cannot prove randomization "
            "or causal uplift. Incomplete trials are never silently dropped."
        ),
    }


def audit_intervention_bundles(
    design: Mapping[str, Any], bundles_root: Path, attestations_root: Path, key: bytes,
) -> dict[str, Any]:
    plan = validate_design(dict(design))
    rows: list[dict[str, Any]] = []
    corrupt = 0
    if (not bundles_root.is_dir() or bundles_root.is_symlink()
            or not attestations_root.is_dir() or attestations_root.is_symlink()):
        return audit_intervention_observations(plan, rows, corrupt_bundles=1)
    plan_sha = hashlib.sha256(canonical(plan)).hexdigest()
    for directory in sorted(bundles_root.iterdir()):
        if directory.name.startswith(".") or not directory.is_dir():
            continue
        if directory.is_symlink():
            corrupt += 1
            continue
        try:
            projection = load_harbor_bundle_projection(directory)
            receipt = projection["receipt"]
            execution = receipt.get("execution")
            if (not isinstance(execution, dict)
                    or execution.get("campaign_id") != plan["campaign_id"]
                    or receipt.get("subject") != "hashmarks"):
                raise ValueError("foreign-campaign-or-uncontrolled-subject")
            treatment = execution.get("mcp_treatment")
            if (not isinstance(treatment, dict)
                    or treatment.get("full_contract") is not True
                    or not isinstance(treatment.get("source_contract_identity"), str)
                    or not treatment["source_contract_identity"]):
                raise ValueError("unproven-or-partial-subject-tool-contract")
            path = attestations_root / (directory.name + ".json")
            verified = verify_host_attestations(
                directory / "trajectory.json", path, key,
                campaign_id=plan["campaign_id"], trial_id=directory.name,
            )
            if verified["delivery_state"] != "PROVEN":
                raise ValueError("unverified-host-model-input")
            if plan["study"] == "timing" and verified["provider_submission_state"] != "SUBMITTED":
                raise ValueError("timing-model-request-not-submitted")
            envelope = _load_receipt(path)
            if not isinstance(envelope, dict) or envelope.get("schema") != HOST_SCHEMA:
                raise ValueError("invalid-host-attestation")
            facets = [r.get("intervention") for r in envelope["deliveries"]]
            if not facets or not all(isinstance(x, dict) and x == facets[0] for x in facets):
                raise ValueError("missing-or-mixed-host-intervention")
            facet = facets[0]
            if facet["design_sha256"] != plan_sha:
                raise ValueError("stale-intervention-design")
            rows.append({
                "harness": receipt.get("harness"), "task": receipt.get("task_id"),
                "replicate": receipt.get("replicate_id"), "arm": facet["arm"],
                "model": receipt.get("model"), "status": receipt.get("status"),
                "source_contract_identity": treatment["source_contract_identity"],
                "host_attested": True, "intervention": facet,
            })
        except (MechanismAttributionError, OSError, ValueError, KeyError, TypeError):
            corrupt += 1
    return audit_intervention_observations(plan, rows, corrupt_bundles=corrupt)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, type=Path)
    parser.add_argument("--bundles-root", required=True, type=Path)
    parser.add_argument("--attestations-root", required=True, type=Path)
    parser.add_argument("--host-key-file", required=True, type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        if args.design.is_symlink() or args.design.stat().st_size > 65_536:
            raise ValueError("invalid-design-file")
        design = validate_design(json.loads(args.design.read_text(encoding="utf-8")))
        key = load_host_key(args.host_key_file)
        report = audit_intervention_bundles(
            design, args.bundles_root, args.attestations_root, key,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "coverage_state": "INCOMPLETE",
                          "reason": str(exc), "causal_effect_qualified": False},
                         indent=2, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if args.require_complete and report["coverage_state"] != "COMPLETE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
